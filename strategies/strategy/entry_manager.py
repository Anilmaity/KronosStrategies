"""
entry_manager.py
----------------
Creates Position, Order, and Trigger records in the Kronos DB (PostgreSQL)
via SQLAlchemy when an ICT entry signal fires.
"""

from __future__ import annotations

import uuid
import logging
from decimal import Decimal

from shared.models import (
    Session,
    Position, Order, Trigger,
    UserStrategy, Strategy, CurrencyPair,
    StrategySignal,
)
from shared.metaapi_client import place_market_order
from strategy.ict_engine import EntrySignal

log = logging.getLogger(__name__)
SYMBOL = "XAU_USD"


# Variation tag → DB Strategy.name. Runners pass `variation="VAR2"` etc; we use
# this to select the correct Strategy row (each variation has its own
# entry_quantity and its own deployed UserStrategy).
_VARIATION_STRATEGY_NAME = {
    # VAR1 (Liquidity Scalper) retired 2026-05-18 — unprofitable in 16mo backtest.
    # VAR2 (Liquidity Sweep) retired 2026-05-19 — UserStrategy removed from DB.
    "VAR3":           "Micro Scalper VAR3",
    # ICT_S2_FVG retired 2026-05-19 — UserStrategy removed from DB.
    "ICT_S4_BREAKER": "ICT Breaker Block (M15)",
    # ICT_S6_DAILY_CRT retired 2026-05-18 — 71 trades over 16mo, sample too small.
    # Research-strategy variations (backtest_strategies/sNN_*.py). Name = NAME
    # constant in the source module; description tracks the file.
    "OB_MIT":      "Research OB_MIT",
    "BB":          "Research BB",
    "FVG_MID":     "Research FVG_MID",
    "M90_FADE":    "Research M90_FADE",
    "M90_BIAS":    "Research M90_BIAS",
    "OB_MIT_BIAS": "Research OB_MIT_BIAS",
    # Concept-strategy variations (concept_strategies/cNN_*.py).
    "C03_FVG_FILL": "Concept C03_FVG_FILL",
}


# ──────────────────────────────────────────────────────────────────────────────
# Context lookup
# ──────────────────────────────────────────────────────────────────────────────

def _get_context(symbol: str = SYMBOL, variation: str | None = None) -> dict | None:
    """
    Return the active trading context for the given symbol + variation.
    Finds: UserStrategy (deployed + active) → Strategy → CurrencyPair.
    Returns dict with user_strategy_id, user_broker_id, currency_pair_id, quantity.
    """
    sess = Session()
    try:
        cp = sess.query(CurrencyPair).filter_by(symbol=symbol).first()
        if not cp:
            log.warning("[CTX] CurrencyPair '%s' not found in DB", symbol)
            return None

        q = sess.query(Strategy).filter_by(currencypair_id=cp.id, is_active=True)
        if variation:
            name = _VARIATION_STRATEGY_NAME.get(variation)
            if not name:
                log.warning("[CTX] Unknown variation tag '%s'", variation)
                return None
            q = q.filter_by(name=name)
        strategy = q.first()
        if not strategy:
            log.warning("[CTX] No active Strategy for symbol='%s' variation='%s'",
                        symbol, variation)
            return None

        us = (
            sess.query(UserStrategy)
            .filter_by(strategy_id=strategy.id, is_active=True, deployed=True)
            .first()
        )
        if not us:
            log.warning("[CTX] No deployed UserStrategy for strategy '%s'", strategy.name)
            return None

        qty = float(strategy.entry_quantity) * int(us.multiplyer)

        return {
            "strategy_id": strategy.id,
            "user_strategy_id": us.id,
            "user_broker_id": us.user_broker_id,
            "currency_pair_id": cp.id,
            "quantity": qty,
        }
    finally:
        sess.close()


def _log_signal_fired(strategy_id, symbol, signal: EntrySignal) -> uuid.UUID | None:
    """Write a StrategySignal(status='FIRED') row and return its id.
    Failures here must not abort entry — logged and swallowed.
    """
    sess = Session()
    try:
        row = StrategySignal(
            id=uuid.uuid4(),
            strategy_id=strategy_id,
            symbol=symbol,
            side=signal.side,
            entry_price=Decimal(str(signal.entry_price)),
            stop_loss=Decimal(str(signal.stop_loss)) if signal.stop_loss is not None else None,
            take_profit=Decimal(str(signal.take_profit)) if signal.take_profit is not None else None,
            reason=signal.reason,
            status="FIRED",
        )
        sess.add(row)
        sess.commit()
        return row.id
    except Exception:
        sess.rollback()
        log.exception("[SIGNAL] Failed to log FIRED signal — continuing")
        return None
    finally:
        sess.close()


def _update_signal_status(signal_log_id, status, *, rejection_reason=None, position_id=None):
    """Update an existing StrategySignal row's status and related fields."""
    if signal_log_id is None:
        return
    sess = Session()
    try:
        row = sess.query(StrategySignal).filter_by(id=signal_log_id).first()
        if row is None:
            return
        row.status = status
        if rejection_reason is not None:
            row.rejection_reason = rejection_reason[:500]
        if position_id is not None:
            row.position_id = position_id
        sess.commit()
    except Exception:
        sess.rollback()
        log.exception("[SIGNAL] Failed to update signal status — continuing")
    finally:
        sess.close()


def _has_open_position(user_strategy_id: uuid.UUID) -> bool:
    """Return True if there is already an open (quantity > 0) position."""
    sess = Session()
    try:
        return (
            sess.query(Position)
            .filter(
                Position.user_strategy_id == user_strategy_id,
                Position.quantity > 0,
            )
            .first()
        ) is not None
    finally:
        sess.close()


# ──────────────────────────────────────────────────────────────────────────────
# Entry placement
# ──────────────────────────────────────────────────────────────────────────────

def place_entry(signal: EntrySignal, symbol: str = SYMBOL, variation: str | None = None) -> bool:
    """
    Persist an entry into the DB:
      1. Position record
      2. ENTRY Order
      3. STOPLOSS Trigger
      4. TARGET Trigger

    `variation` selects which Strategy row (and therefore which UserStrategy /
    entry_quantity) the trade belongs to. Required when multiple strategies
    share a symbol — otherwise the first active strategy for the symbol wins.

    Returns True on success, False if skipped or failed.
    """
    ctx = _get_context(symbol, variation=variation)
    if not ctx:
        # No strategy_id known -> can't log; the misconfiguration is the bug.
        return False

    # Persist the signal as FIRED before anything else can fail.
    signal_log_id = _log_signal_fired(ctx["strategy_id"], symbol, signal)

    if _has_open_position(ctx["user_strategy_id"]):
        _update_signal_status(signal_log_id, "REJECTED",
                              rejection_reason="open_position_cap")
        log.info("[ENTRY] Position already open — skipping new entry")
        return False

    qty = ctx["quantity"]

    # Fire MetaAPI market order FIRST. If broker rejects, we don't pollute the
    # DB with phantom positions. On success we record the broker positionId on
    # the Order so position_monitor / reconciliation can correlate.
    broker_position_id = place_market_order(
        side=signal.side,
        symbol=symbol,
        volume=qty,
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit,
        entry_price=signal.entry_price,
    )
    if not broker_position_id:
        _update_signal_status(signal_log_id, "REJECTED",
                              rejection_reason="metaapi_rejection")
        log.warning("[ENTRY] MetaAPI rejected order — skipping DB write")
        return False

    sess = Session()
    try:
        # ── 1. Position ───────────────────────────────────────────────────────
        is_long = signal.side == "BUY"
        position = Position(
            id=uuid.uuid4(),
            symbol=symbol,
            avg_buy_price=Decimal(str(signal.entry_price)) if is_long else Decimal("0"),
            avg_sell_price=Decimal("0") if is_long else Decimal(str(signal.entry_price)),
            total_buy_quantity=Decimal(str(qty)) if is_long else Decimal("0"),
            quantity=Decimal(str(qty)),
            ltp=Decimal(str(signal.entry_price)),
            profit_loss=Decimal("0"),
            profit_loss_percentage=Decimal("0"),
            realized_profit_loss=Decimal("0"),
            user_strategy_id=ctx["user_strategy_id"],
            currencypair_id=ctx["currency_pair_id"],
        )
        sess.add(position)
        sess.flush()  # populate position.id before FK references

        # ── 2. Entry Order ────────────────────────────────────────────────────
        entry_order = Order(
            id=uuid.uuid4(),
            symbol=symbol,
            price=Decimal(str(signal.entry_price)),
            condition="ENTRY",
            side=signal.side,
            quantity=Decimal(str(qty)),
            amount=Decimal(str(round(signal.entry_price * qty, 2))),
            order_type="MARKET",
            status="EXECUTED",
            reason=signal.reason,
            broker_order_id=str(broker_position_id),
            position_id=position.id,
            user_broker_id=ctx["user_broker_id"],
        )
        sess.add(entry_order)

        # ── 3. SL Trigger ─────────────────────────────────────────────────────
        # LONG: SL fires when price <= sl  (greater_than=False)
        # SHORT: SL fires when price >= sl (greater_than=True)
        close_side = "SELL" if is_long else "BUY"
        sl_trigger = Trigger(
            id=uuid.uuid4(),
            symbol=symbol,
            trigger_price=Decimal(str(signal.stop_loss)),
            order_type="MARKET",
            side=close_side,
            greater_than=not is_long,
            quantity=Decimal(str(qty)),
            trigger_type="STOPLOSS",
            status="PENDING",
            position_id=position.id,
        )
        sess.add(sl_trigger)

        # ── 4. TP Trigger ─────────────────────────────────────────────────────
        # LONG: TP fires when price >= tp (greater_than=True)
        # SHORT: TP fires when price <= tp (greater_than=False)
        tp_trigger = Trigger(
            id=uuid.uuid4(),
            symbol=symbol,
            trigger_price=Decimal(str(signal.take_profit)),
            order_type="MARKET",
            side=close_side,
            greater_than=is_long,
            quantity=Decimal(str(qty)),
            trigger_type="TARGET",
            status="PENDING",
            position_id=position.id,
        )
        sess.add(tp_trigger)

        sess.commit()

        _update_signal_status(signal_log_id, "PLACED", position_id=position.id)

        log.info(
            "[ENTRY] %s %s qty=%.2f @ %.2f | SL=%.2f TP=%.2f | %s | pos_id=%s",
            signal.side, symbol, qty,
            signal.entry_price, signal.stop_loss, signal.take_profit,
            signal.reason, position.id,
        )
        return True

    except Exception as e:
        sess.rollback()
        log.exception("[ENTRY] Failed to persist entry signal")
        _update_signal_status(signal_log_id, "REJECTED",
                              rejection_reason=f"db_error: {type(e).__name__}: {e}")
        return False
    finally:
        sess.close()
