"""
dashboard.py
------------
Best-effort Kronos-dashboard writer for the standalone challenge bot.

The bot runs in a container built from ./strategies, so it cannot import
Telegram_Bot/apis_persist.py — this is a self-contained equivalent that writes the
same `apis_position` / `apis_order` / `apis_strategysignal` rows (raw psycopg2,
same SQL shape) under a DEDICATED dashboard strategy, separate from the integrated
`challenge_xau` bot's rows.

Every method is best-effort: a DB outage logs and returns (never raises), so a
dashboard problem can never block the trading path. When `enabled` is False or any
required id is missing, the dashboard is inert — no connection is attempted.

Config (env, read by `build_dashboard`):
  CHALLENGE_DASHBOARD_SYNC          — "false" to disable (default on)
  CHALLENGE_DASH_STRATEGY_ID        — apis_strategy.id the StrategySignal rows hang off
  CHALLENGE_DASH_USER_STRATEGY_ID   — apis_userstrategy.id the positions hang off
  CHALLENGE_DASH_USER_BROKER_ID     — apis_userbroker.id for the order rows
  CHALLENGE_DASH_CURRENCYPAIR_ID    — XAUUSD currencypair id
  DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD/DB_SSLMODE — the app DB (same as
                                      apis_persist / position_manager use)
"""
from __future__ import annotations

import logging
import os
import uuid

import psycopg2

log = logging.getLogger("challenge-dash")

# XAUUSD: the dashboard stores PnL as "cash = price_move x lots" (no contract
# multiplier; the UI applies x100 at render). MetaAPI returns account dollars, so
# divide by the contract size before storing or the dashboard shows 100x.
CONTRACT_SIZE = float(os.getenv("APIS_PNL_CONTRACT_SIZE", "100"))
SYMBOL = "XAUUSD"


class ChallengeDashboard:
    def __init__(self, *, strategy_id: str, user_strategy_id: str, user_broker_id: str,
                 currencypair_id: str, enabled: bool = True,
                 contract_size: float = CONTRACT_SIZE, symbol: str = SYMBOL,
                 label: str = "challenge-h4"):
        self.strategy_id = strategy_id
        self.user_strategy_id = user_strategy_id
        self.user_broker_id = user_broker_id
        self.currencypair_id = currencypair_id
        # Inert unless explicitly enabled AND every id needed to write is present.
        self.enabled = bool(enabled and strategy_id and user_strategy_id
                            and user_broker_id and currencypair_id)
        self.contract_size = contract_size
        self.symbol = symbol
        self.label = label

    def _to_cash(self, usd: float | None) -> float | None:
        if usd is None:
            return None
        return round(usd / self.contract_size, 4)

    def _connect(self):
        return psycopg2.connect(
            host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", "5432")),
            dbname=os.getenv("DB_NAME", "tsdb"), user=os.getenv("DB_USER", "tsdbadmin"),
            password=os.getenv("DB_PASSWORD"), sslmode=os.getenv("DB_SSLMODE", "require"),
            connect_timeout=10,
        )

    def open_position(self, side: str, entry: float, volume: float,
                      broker_ticket: str | None = None) -> str | None:
        if not self.enabled:
            return None
        is_long = (side or "").lower() == "buy"
        pos_id = str(uuid.uuid4())
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO apis_position (
                        id, created_at, modified_at, symbol,
                        avg_buy_price, avg_sell_price, total_buy_quantity,
                        quantity, profit_loss, profit_loss_percentage, ltp,
                        realized_profit_loss, user_strategy_id, currencypair_id
                    ) VALUES (%s, NOW(), NOW(), %s, %s, %s, %s, %s, 0, 0, %s, 0, %s, %s)
                    """,
                    (pos_id, self.symbol, entry if is_long else 0, 0 if is_long else entry,
                     volume if is_long else 0, volume, entry,
                     self.user_strategy_id, self.currencypair_id),
                )
                self._insert_order(cur, pos_id, condition="ENTRY",
                                   side=("BUY" if is_long else "SELL"), price=entry,
                                   quantity=volume, status="EXECUTED", reason="challenge_entry",
                                   broker_order_id=broker_ticket)
            log.info("[%s] position opened id=%s %s vol=%s @ %s",
                     self.label, pos_id, self.symbol, volume, entry)
            return pos_id
        except Exception as e:
            log.error("[%s] open_position failed: %s", self.label, e)
            return None

    def update_live(self, position_id: str | None, ltp: float | None = None,
                    profit_loss: float | None = None) -> None:
        if not self.enabled or not position_id:
            return
        sets, vals = ["modified_at = NOW()"], []
        if ltp is not None:
            sets.append("ltp = %s"); vals.append(round(ltp, 2))
        if profit_loss is not None:
            sets.append("profit_loss = %s"); vals.append(self._to_cash(profit_loss))
        if len(sets) == 1:
            return
        vals.append(position_id)
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(f"UPDATE apis_position SET {', '.join(sets)} "
                            f"WHERE id = %s AND quantity > 0", vals)
        except Exception as e:
            log.error("[%s] update_live id=%s failed: %s", self.label, position_id, e)

    def conclude_position(self, position_id: str | None, realized_pnl: float | None,
                          close_price: float | None = None, side: str | None = None,
                          volume: float | None = None, reason: str = "EXIT") -> None:
        if not self.enabled or not position_id:
            return
        rpnl = self._to_cash(realized_pnl) if realized_pnl is not None else 0
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE apis_position
                       SET quantity = 0, profit_loss = 0, realized_profit_loss = %s,
                           ltp = COALESCE(%s, ltp), modified_at = NOW()
                     WHERE id = %s AND quantity > 0
                    """,
                    (rpnl, round(close_price, 2) if close_price is not None else None, position_id),
                )
                if cur.rowcount and close_price is not None and side:
                    is_long = (side or "").lower() == "buy"
                    cond = "TARGET" if rpnl >= 0 else "STOPLOSS"
                    self._insert_order(cur, position_id, condition=cond,
                                       side=("SELL" if is_long else "BUY"), price=close_price,
                                       quantity=volume or 0, status="EXECUTED", reason=reason)
            log.info("[%s] position concluded id=%s realized=%s", self.label, position_id, rpnl)
        except Exception as e:
            log.error("[%s] conclude_position id=%s failed: %s", self.label, position_id, e)

    def find_open_position_id(self) -> str | None:
        if not self.enabled:
            return None
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM apis_position WHERE user_strategy_id = %s AND quantity > 0 "
                    "ORDER BY created_at DESC LIMIT 1", (self.user_strategy_id,),
                )
                row = cur.fetchone()
                return str(row[0]) if row else None
        except Exception as e:
            log.error("[%s] find_open_position_id failed: %s", self.label, e)
            return None

    def record_signal(self, side: str, entry: float | None, sl: float | None,
                      take_profit: float | None, *, status: str = "PLACED",
                      reason: str = "", rejection_reason: str = "",
                      signal_at=None, position_id: str | None = None) -> str | None:
        if not self.enabled:
            return None
        sid = str(uuid.uuid4())
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO apis_strategysignal (
                        id, created_at, modified_at, strategy_id, symbol, side,
                        entry_price, stop_loss, take_profit, reason, status,
                        rejection_reason, signal_at, position_id
                    ) VALUES (%s, NOW(), NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s,
                              COALESCE(%s::timestamptz, NOW()), %s)
                    """,
                    (sid, self.strategy_id, self.symbol, (side or "").upper(),
                     entry if entry is not None else 0, sl, take_profit,
                     (reason or "")[:500], status, (rejection_reason or "")[:500],
                     signal_at, position_id),
                )
            log.info("[%s] signal recorded id=%s %s %s @ %s",
                     self.label, sid, status, (side or "").upper(), entry)
            return sid
        except Exception as e:
            log.error("[%s] record_signal failed: %s", self.label, e)
            return None

    def _insert_order(self, cur, position_id: str, *, condition: str, side: str,
                      price: float, quantity: float, status: str, reason: str = "NONE",
                      broker_order_id: str | None = None) -> None:
        try:
            cur.execute(
                """
                INSERT INTO apis_order (
                    id, created_at, modified_at, symbol, exchange, price, condition,
                    side, quantity, amount, order_type, status, reason, broker_order_id,
                    position_id, user_broker_id
                ) VALUES (%s, NOW(), NOW(), %s, 'OANDA', %s, %s, %s, %s, %s,
                          'MARKET', %s, %s, %s, %s, %s)
                """,
                (str(uuid.uuid4()), self.symbol, round(price, 2), condition, side, quantity,
                 round(price * (quantity or 0), 2), status, reason[:200],
                 str(broker_order_id or ""), position_id, self.user_broker_id),
            )
        except Exception as e:
            log.error("[%s] _insert_order (%s) failed: %s", self.label, condition, e)


def build_dashboard():
    """Construct the ChallengeDashboard from env. Returns an inert (disabled)
    instance when CHALLENGE_DASHBOARD_SYNC=false or any required id is missing."""
    enabled = os.getenv("CHALLENGE_DASHBOARD_SYNC", "true").lower() != "false"
    return ChallengeDashboard(
        strategy_id=os.getenv("CHALLENGE_DASH_STRATEGY_ID", "").strip(),
        user_strategy_id=os.getenv("CHALLENGE_DASH_USER_STRATEGY_ID", "").strip(),
        user_broker_id=os.getenv("CHALLENGE_DASH_USER_BROKER_ID", "").strip(),
        currencypair_id=os.getenv("CHALLENGE_DASH_CURRENCYPAIR_ID",
                                  "212ee480-2008-4fe1-a677-9ae7e82d59b0").strip(),
        enabled=enabled,
    )
