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
)
from strategy.ict_engine import EntrySignal

log = logging.getLogger(__name__)
SYMBOL = "XAU_USD"


# ──────────────────────────────────────────────────────────────────────────────
# Context lookup
# ──────────────────────────────────────────────────────────────────────────────

def _get_context(symbol: str = SYMBOL) -> dict | None:
    """
    Return the active trading context for the given symbol.
    Finds: UserStrategy (deployed + active) → Strategy → CurrencyPair.
    Returns dict with user_strategy_id, user_broker_id, currency_pair_id, quantity.
    """
    sess = Session()
    try:
        cp = sess.query(CurrencyPair).filter_by(symbol=symbol).first()
        if not cp:
            log.warning("[CTX] CurrencyPair '%s' not found in DB", symbol)
            return None

        strategy = (
            sess.query(Strategy)
            .filter_by(currencypair_id=cp.id, is_active=True)
            .first()
        )
        if not strategy:
            log.warning("[CTX] No active Strategy for symbol '%s'", symbol)
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
            "user_strategy_id": us.id,
            "user_broker_id": us.user_broker_id,
            "currency_pair_id": cp.id,
            "quantity": qty,
        }
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

def place_entry(signal: EntrySignal, symbol: str = SYMBOL) -> bool:
    """
    Persist an entry into the DB:
      1. Position record
      2. ENTRY Order
      3. STOPLOSS Trigger
      4. TARGET Trigger

    Returns True on success, False if skipped or failed.
    """
    ctx = _get_context(symbol)
    if not ctx:
        return False

    if _has_open_position(ctx["user_strategy_id"]):
        log.info("[ENTRY] Position already open — skipping new entry")
        return False

    qty = ctx["quantity"]
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
            currency_pair_id=ctx["currency_pair_id"],
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

        log.info(
            "[ENTRY] %s %s qty=%.2f @ %.2f | SL=%.2f TP=%.2f | %s | pos_id=%s",
            signal.side, symbol, qty,
            signal.entry_price, signal.stop_loss, signal.take_profit,
            signal.reason, position.id,
        )
        return True

    except Exception:
        sess.rollback()
        log.exception("[ENTRY] Failed to persist entry signal")
        return False
    finally:
        sess.close()
