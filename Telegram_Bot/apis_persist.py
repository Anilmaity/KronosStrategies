"""
Dashboard persistence for NeymarGoldTrader signals.

The Kronos web dashboard (algorobos.com) renders trades from the *apis* schema
(`apis_position` / `apis_order`) in the application Postgres — the SAME DB the
other live strategies write to. Each MetaAPI account the bot copy-trades is shown
as its own platform Strategy, so its positions / order history are separate.

`ApisDashboard` writes rows under one (user_strategy_id, user_broker_id,
currencypair_id) target. Fan-out builds one dashboard per account; the
module-level functions remain as a default instance (the primary account) so the
single-account path is unchanged.

Design notes / safety:
  * Connects via DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD (the apis DB used by
    position_manager) — NOT TIGERDATA_URL (the tick-data instance).
  * symbol="XAUUSD" + the XAUUSD currencypair. position_monitor.py only manages
    `symbol == "XAU_USD"` positions, so it will never touch these rows.
  * Every write is best-effort: a DB outage logs and returns, never raising, so
    trading and broker reconciliation are never blocked by persistence problems.
"""
from __future__ import annotations

import logging
import os
import uuid

import psycopg2

log = logging.getLogger("tg-apis")

# Default (primary account) provisioned context. Looked up once and confirmed:
#   Strategy      'Neymar Telegram Copy'  d9bf1604-9ee0-4454-b3c1-b7335ff8915f
#   UserStrategy  'Neymar Telegram Copy'  4a4be335-9606-49cb-9658-4d10cfe064b1
#   CurrencyPair  'XAUUSD'                212ee480-2008-4fe1-a677-9ae7e82d59b0
#   UserBroker                            e673869c-8c56-4521-9a49-ac62f07d7da9
USER_STRATEGY_ID = os.getenv("APIS_USER_STRATEGY_ID", "4a4be335-9606-49cb-9658-4d10cfe064b1")
CURRENCYPAIR_ID  = os.getenv("APIS_CURRENCYPAIR_ID",  "212ee480-2008-4fe1-a677-9ae7e82d59b0")
USER_BROKER_ID   = os.getenv("APIS_USER_BROKER_ID",   "e673869c-8c56-4521-9a49-ac62f07d7da9")
# Strategy (not UserStrategy) id — the FK StrategySignal rows hang off, so the
# Signals tab shows each telegram signal under its strategy name.
STRATEGY_ID      = os.getenv("APIS_STRATEGY_ID",      "d9bf1604-9ee0-4454-b3c1-b7335ff8915f")
SYMBOL = "XAUUSD"

# PnL UNIT CONVENTION — must match the rest of the Kronos dashboard. MetaAPI
# returns profit in ACCOUNT DOLLARS, but the apis schema stores PnL as
# "cash = price_move x lots" (no contract multiplier); the dashboard applies the
# x100 XAUUSD contract size at render. So divide broker dollars by CONTRACT_SIZE
# before storing, else the dashboard shows 100x the real PnL.
CONTRACT_SIZE = float(os.getenv("APIS_PNL_CONTRACT_SIZE", "100"))

_ENABLED = os.getenv("APIS_DASHBOARD_SYNC", "true").lower() != "false"


class ApisDashboard:
    """Writes apis_position / apis_order rows under one strategy/broker target."""

    def __init__(self, user_strategy_id: str, user_broker_id: str,
                 currencypair_id: str, *, enabled: bool = True,
                 symbol: str = SYMBOL, contract_size: float = CONTRACT_SIZE,
                 label: str = "primary", strategy_id: str = STRATEGY_ID):
        self.user_strategy_id = user_strategy_id
        self.user_broker_id = user_broker_id
        self.currencypair_id = currencypair_id
        self.enabled = enabled
        self.symbol = symbol
        self.contract_size = contract_size
        self.label = label
        self.strategy_id = strategy_id

    def _to_cash(self, usd: float | None) -> float | None:
        """Convert a broker account-dollar PnL into the dashboard 'cash' unit."""
        if usd is None:
            return None
        return round(usd / self.contract_size, 4)

    def _connect(self):
        return psycopg2.connect(
            host=os.getenv("DB_HOST"),
            port=int(os.getenv("DB_PORT", "5432")),
            dbname=os.getenv("DB_NAME", "tsdb"),
            user=os.getenv("DB_USER", "tsdbadmin"),
            password=os.getenv("DB_PASSWORD"),
            sslmode=os.getenv("DB_SSLMODE", "require"),
            connect_timeout=10,
        )

    def entries_allowed(self) -> bool | None:
        """Strategy-Manager gate for NEW entries: True only while this
        dashboard's UserStrategy row is deployed AND is_active — the same flags
        the manager loop flips on armed strategies (policy, kill-switch,
        market-closed). Returns None when the DB can't be reached; callers must
        treat None as not-allowed (fail-closed) so a paused strategy can never
        trade through a DB outage. Exits/reconciliation are never gated.
        """
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT deployed, is_active FROM apis_userstrategy WHERE id = %s",
                    (self.user_strategy_id,),
                )
                row = cur.fetchone()
            if row is None:
                log.warning("[%s] entries_allowed: UserStrategy %s not found",
                            self.label, self.user_strategy_id)
                return False
            return bool(row[0]) and bool(row[1])
        except Exception as e:
            log.warning("[%s] entries_allowed: DB check failed (%s)", self.label, e)
            return None

    def open_position(self, side: str, entry: float, volume: float,
                      broker_ticket: str | None = None) -> str | None:
        """Create an apis_position (+ ENTRY order) for a freshly-filled signal.

        Returns the new position id (str UUID) to stash on the signal so later
        live/conclude updates can find it, or None on failure.
        """
        if not self.enabled:
            return None
        is_long = (side or "").lower() == "buy"
        pos_id = str(uuid.uuid4())
        try:
            with self._connect() as conn, conn.cursor() as cur:
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
                        pos_id, self.symbol,
                        entry if is_long else 0,
                        0 if is_long else entry,
                        volume if is_long else 0,
                        volume, entry,
                        self.user_strategy_id, self.currencypair_id,
                    ),
                )
                self._insert_order(cur, pos_id, condition="ENTRY",
                                   side=("BUY" if is_long else "SELL"),
                                   price=entry, quantity=volume, status="EXECUTED",
                                   reason="tg_entry", broker_order_id=broker_ticket)
            log.info("[apis:%s] position opened id=%s %s vol=%s @ %s",
                     self.label, pos_id, self.symbol, volume, entry)
            return pos_id
        except Exception as e:
            log.error("[apis:%s] open_position failed: %s", self.label, e)
            return None

    def update_live(self, position_id: str | None, ltp: float | None,
                    profit_loss: float | None) -> None:
        """Refresh the live unrealized PnL / ltp on an open position (qty > 0)."""
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
                cur.execute(
                    f"UPDATE apis_position SET {', '.join(sets)} "
                    f"WHERE id = %s AND quantity > 0",
                    vals,
                )
        except Exception as e:
            log.error("[apis:%s] update_live id=%s failed: %s", self.label, position_id, e)

    def conclude_position(self, position_id: str | None, realized_pnl: float | None,
                          close_price: float | None = None,
                          side: str | None = None, volume: float | None = None,
                          reason: str = "EXIT") -> None:
        """Flatten a position: quantity -> 0, stamp realized PnL (+ close order).

        Guarded by `quantity > 0` so whichever path concludes first wins and a
        later call can't clobber the more-accurate realized figure.
        """
        if not self.enabled or not position_id:
            return
        rpnl = self._to_cash(realized_pnl) if realized_pnl is not None else 0
        try:
            with self._connect() as conn, conn.cursor() as cur:
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
                    self._insert_order(cur, position_id, condition=cond, side=close_side,
                                       price=close_price, quantity=volume or 0,
                                       status="EXECUTED", reason=reason)
            log.info("[apis:%s] position concluded id=%s realized=%s", self.label, position_id, rpnl)
        except Exception as e:
            log.error("[apis:%s] conclude_position id=%s failed: %s", self.label, position_id, e)

    def find_open_position_id(self) -> str | None:
        """Fallback lookup for this strategy's single open position (no-pyramiding)."""
        if not self.enabled:
            return None
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM apis_position "
                    "WHERE user_strategy_id = %s AND quantity > 0 "
                    "ORDER BY created_at DESC LIMIT 1",
                    (self.user_strategy_id,),
                )
                row = cur.fetchone()
                return str(row[0]) if row else None
        except Exception as e:
            log.error("[apis:%s] find_open_position_id failed: %s", self.label, e)
            return None

    def record_signal(self, side: str, entry: float | None, sl: float | None,
                      take_profit: float | None, *, status: str = "PLACED",
                      reason: str = "", rejection_reason: str = "",
                      signal_at=None, position_id: str | None = None) -> str | None:
        """Best-effort StrategySignal insert so the Signals tab shows this
        telegram signal under its strategy (mirrors the algo runners'
        entry_manager._log_signal_fired). Never raises — trading must not depend
        on it. `signal_at` may be an ISO string / datetime / None (-> NOW())."""
        if not self.enabled or not self.strategy_id:
            return None
        sid = str(uuid.uuid4())
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO apis_strategysignal (
                        id, created_at, modified_at,
                        strategy_id, symbol, side, entry_price,
                        stop_loss, take_profit, reason, status,
                        rejection_reason, signal_at, position_id
                    ) VALUES (
                        %s, NOW(), NOW(),
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, COALESCE(%s::timestamptz, NOW()), %s
                    )
                    """,
                    (
                        sid, self.strategy_id, self.symbol, (side or "").upper(),
                        entry if entry is not None else 0,
                        sl, take_profit, (reason or "")[:500], status,
                        (rejection_reason or "")[:500], signal_at, position_id,
                    ),
                )
            log.info("[apis:%s] signal recorded id=%s %s %s @ %s",
                     self.label, sid, status, (side or "").upper(), entry)
            return sid
        except Exception as e:
            log.error("[apis:%s] record_signal failed: %s", self.label, e)
            return None

    def _insert_order(self, cur, position_id: str, *, condition: str, side: str,
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
                    str(uuid.uuid4()), self.symbol, round(price, 2), condition, side,
                    quantity, round(price * (quantity or 0), 2),
                    status, reason[:200], str(broker_order_id or ""),
                    position_id, self.user_broker_id,
                ),
            )
        except Exception as e:
            log.error("[apis:%s] _insert_order (%s) failed: %s", self.label, condition, e)


# ── Module-level default dashboard (primary) + back-compatible API ────────────
_default_dashboard = ApisDashboard(USER_STRATEGY_ID, USER_BROKER_ID, CURRENCYPAIR_ID,
                                   enabled=_ENABLED, label="primary")


def _to_cash(usd: float | None) -> float | None:
    return _default_dashboard._to_cash(usd)


def open_position(side: str, entry: float, volume: float,
                  broker_ticket: str | None = None) -> str | None:
    return _default_dashboard.open_position(side, entry, volume, broker_ticket)


def update_live(position_id: str | None, ltp: float | None,
                profit_loss: float | None) -> None:
    return _default_dashboard.update_live(position_id, ltp, profit_loss)


def conclude_position(position_id: str | None, realized_pnl: float | None,
                      close_price: float | None = None,
                      side: str | None = None, volume: float | None = None,
                      reason: str = "EXIT") -> None:
    return _default_dashboard.conclude_position(position_id, realized_pnl, close_price,
                                                side, volume, reason)


def find_open_position_id() -> str | None:
    return _default_dashboard.find_open_position_id()


def record_signal(side: str, entry: float | None, sl: float | None,
                  take_profit: float | None, *, status: str = "PLACED",
                  reason: str = "", rejection_reason: str = "",
                  signal_at=None, position_id: str | None = None) -> str | None:
    return _default_dashboard.record_signal(
        side, entry, sl, take_profit, status=status, reason=reason,
        rejection_reason=rejection_reason, signal_at=signal_at, position_id=position_id)
