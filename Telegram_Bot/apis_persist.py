"""
Dashboard persistence for NeymarGoldTrader signals.

The Kronos web dashboard (algorobos.com) renders trades from the *apis* schema
(`apis_position` / `apis_order`) in the application Postgres — the SAME DB the
other live strategies (position_manager, the research/concept runners) write to
via shared/models.py. This bot historically wrote only its own `tg_signals` /
`tg_orders` tables into a DIFFERENT Timescale instance (the tick-data DB pointed
at by TIGERDATA_URL), so its trades never appeared on the dashboard even though
they executed and were recorded.

This module mirrors each concluded/live signal into an `apis_position` row under
the already-provisioned "Neymar Telegram Copy" UserStrategy, exactly like the
other strategies, so the dashboard shows them.

Design notes / safety:
  * Connects to the apis DB via DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD
    (already present in the container env, used by position_manager) — NOT
    TIGERDATA_URL (that is the tick-data instance).
  * symbol="XAUUSD" + the XAUUSD currencypair. position_monitor.py only manages
    `symbol == "XAU_USD"` positions, so it will NEVER touch / prematurely flatten
    these rows. This bot owns their full lifecycle.
  * Every write is best-effort: a DB outage logs and returns, never raising, so
    trading and broker reconciliation are never blocked by persistence problems
    (same contract as db_persist.py). Order PLACEMENT logic is untouched.
"""
from __future__ import annotations

import logging
import os
import uuid

import psycopg2

log = logging.getLogger("tg-apis")

# Provisioned context (overridable via env). Looked up once and confirmed live:
#   Strategy      'Neymar Telegram Copy'  d9bf1604-9ee0-4454-b3c1-b7335ff8915f
#   UserStrategy  'Neymar Telegram Copy'  4a4be335-9606-49cb-9658-4d10cfe064b1
#   CurrencyPair  'XAUUSD'                212ee480-2008-4fe1-a677-9ae7e82d59b0
#   UserBroker                            e673869c-8c56-4521-9a49-ac62f07d7da9
USER_STRATEGY_ID = os.getenv("APIS_USER_STRATEGY_ID", "4a4be335-9606-49cb-9658-4d10cfe064b1")
CURRENCYPAIR_ID  = os.getenv("APIS_CURRENCYPAIR_ID",  "212ee480-2008-4fe1-a677-9ae7e82d59b0")
USER_BROKER_ID   = os.getenv("APIS_USER_BROKER_ID",   "e673869c-8c56-4521-9a49-ac62f07d7da9")
SYMBOL = "XAUUSD"

# PnL UNIT CONVENTION — must match the rest of the Kronos dashboard.
# MetaAPI returns realized/unrealized profit in ACCOUNT DOLLARS, but the apis
# schema stores PnL as "cash = price_move x lots" (NO contract multiplier): see
# strategies/micro_scalper.py ("realized_profit_loss is cash; convert to points
# ... / qty") and every other strategy's stored values (implied multiplier ~1).
# The dashboard applies the x100 XAUUSD contract size at render. So we divide
# broker dollars by CONTRACT_SIZE before storing — otherwise the dashboard shows
# 100x the real PnL.
CONTRACT_SIZE = float(os.getenv("APIS_PNL_CONTRACT_SIZE", "100"))


def _to_cash(usd: float | None) -> float | None:
    """Convert a broker account-dollar PnL into the dashboard 'cash' unit."""
    if usd is None:
        return None
    return round(usd / CONTRACT_SIZE, 4)

_ENABLED = os.getenv("APIS_DASHBOARD_SYNC", "true").lower() != "false"


def _connect():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("DB_NAME", "tsdb"),
        user=os.getenv("DB_USER", "tsdbadmin"),
        password=os.getenv("DB_PASSWORD"),
        sslmode=os.getenv("DB_SSLMODE", "require"),
        connect_timeout=10,
    )


def open_position(side: str, entry: float, volume: float,
                  broker_ticket: str | None = None) -> str | None:
    """Create an apis_position (+ ENTRY order) for a freshly-filled signal.

    `side` is the bot's lowercase 'buy'/'sell'. Returns the new position id
    (a str UUID) to stash on the signal so later live/conclude updates can find
    it, or None on failure.
    """
    if not _ENABLED:
        return None
    is_long = (side or "").lower() == "buy"
    pos_id = str(uuid.uuid4())
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO apis_position (
                    id, created_at, modified_at,
                    symbol, avg_buy_price, avg_sell_price, total_buy_quantity,
                    quantity, profit_loss, profit_loss_percentage, ltp,
                    realized_profit_loss, user_strategy_id, currencypair_id
                ) VALUES (
                    %s, NOW(), NOW(),
                    %s, %s, %s, %s,
                    %s, 0, 0, %s,
                    0, %s, %s
                )
                """,
                (
                    pos_id, SYMBOL,
                    entry if is_long else 0,
                    0 if is_long else entry,
                    volume if is_long else 0,
                    volume, entry,
                    USER_STRATEGY_ID, CURRENCYPAIR_ID,
                ),
            )
            _insert_order(cur, pos_id, condition="ENTRY",
                          side=("BUY" if is_long else "SELL"),
                          price=entry, quantity=volume, status="EXECUTED",
                          reason="tg_entry", broker_order_id=broker_ticket)
        log.info("[apis] position opened id=%s %s vol=%s @ %s", pos_id, SYMBOL, volume, entry)
        return pos_id
    except Exception as e:
        log.error("[apis] open_position failed: %s", e)
        return None


def update_live(position_id: str | None, ltp: float | None,
                profit_loss: float | None) -> None:
    """Refresh the live unrealized PnL / ltp on an open position (qty > 0)."""
    if not _ENABLED or not position_id:
        return
    sets, vals = ["modified_at = NOW()"], []
    if ltp is not None:
        sets.append("ltp = %s"); vals.append(round(ltp, 2))
    if profit_loss is not None:
        sets.append("profit_loss = %s"); vals.append(_to_cash(profit_loss))
    if len(sets) == 1:
        return
    vals.append(position_id)
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE apis_position SET {', '.join(sets)} "
                f"WHERE id = %s AND quantity > 0",
                vals,
            )
    except Exception as e:
        log.error("[apis] update_live id=%s failed: %s", position_id, e)


def conclude_position(position_id: str | None, realized_pnl: float | None,
                      close_price: float | None = None,
                      side: str | None = None, volume: float | None = None,
                      reason: str = "EXIT") -> None:
    """Flatten a position: quantity -> 0, stamp realized PnL (+ close order).

    Guarded by `quantity > 0` so whichever path concludes first (broker
    reconciliation or a channel TP3/SL reply) wins and a later call can't
    clobber the more-accurate realized figure with a worse one.
    """
    if not _ENABLED or not position_id:
        return
    rpnl = _to_cash(realized_pnl) if realized_pnl is not None else 0
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE apis_position
                   SET quantity = 0,
                       profit_loss = 0,
                       realized_profit_loss = %s,
                       ltp = COALESCE(%s, ltp),
                       modified_at = NOW()
                 WHERE id = %s AND quantity > 0
                """,
                (rpnl, round(close_price, 2) if close_price is not None else None, position_id),
            )
            if cur.rowcount and close_price is not None and side:
                is_long = (side or "").lower() == "buy"
                close_side = "SELL" if is_long else "BUY"
                cond = "TARGET" if rpnl >= 0 else "STOPLOSS"
                _insert_order(cur, position_id, condition=cond, side=close_side,
                              price=close_price, quantity=volume or 0,
                              status="EXECUTED", reason=reason)
        log.info("[apis] position concluded id=%s realized=%s", position_id, rpnl)
    except Exception as e:
        log.error("[apis] conclude_position id=%s failed: %s", position_id, e)


def find_open_position_id() -> str | None:
    """Fallback lookup for the single open Neymar position (no-pyramiding => <=1).

    Used to recover the position id after a restart, when the in-memory link was
    lost but the broker reconciler still needs to conclude the trade.
    """
    if not _ENABLED:
        return None
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM apis_position "
                "WHERE user_strategy_id = %s AND quantity > 0 "
                "ORDER BY created_at DESC LIMIT 1",
                (USER_STRATEGY_ID,),
            )
            row = cur.fetchone()
            return str(row[0]) if row else None
    except Exception as e:
        log.error("[apis] find_open_position_id failed: %s", e)
        return None


def _insert_order(cur, position_id: str, *, condition: str, side: str,
                  price: float, quantity: float, status: str,
                  reason: str = "NONE", broker_order_id: str | None = None) -> None:
    """Best-effort apis_order insert sharing the caller's cursor/transaction."""
    try:
        cur.execute(
            """
            INSERT INTO apis_order (
                id, created_at, modified_at,
                symbol, exchange, price, condition, side, quantity, amount,
                order_type, status, reason, broker_order_id,
                position_id, user_broker_id
            ) VALUES (
                %s, NOW(), NOW(),
                %s, 'OANDA', %s, %s, %s, %s, %s,
                'MARKET', %s, %s, %s,
                %s, %s
            )
            """,
            (
                str(uuid.uuid4()), SYMBOL, round(price, 2), condition, side,
                quantity, round(price * (quantity or 0), 2),
                status, reason[:200], str(broker_order_id or ""),
                position_id, USER_BROKER_ID,
            ),
        )
    except Exception as e:
        log.error("[apis] _insert_order (%s) failed: %s", condition, e)
