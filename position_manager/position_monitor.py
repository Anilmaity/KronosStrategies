"""
position_monitor.py
-------------------
Monitors open positions every second and closes them when TP or SL is hit.

Cycle (every 1 second):
  1. Fetch latest LTP from TimescaleDB
  2. Query all open positions (quantity > 0) from Kronos DB
  3. For each position, evaluate PENDING Triggers
  4. If trigger fires:
       a. Create close Order record
       b. Calculate and record realized P&L
       c. Zero position quantity
       d. Cancel remaining pending triggers for that position

Run:  python position_monitor.py
"""

import sys
import os
import time
import uuid
import logging
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shared.tsdb_reader import fetch_latest_ltp
from shared.models import Session, Position, Trigger, Order, UserStrategy, CurrencyPair
from shared.market_timing import is_market_closed_utc
from shared.metaapi_client import close_position_by_id

# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────
SYMBOL        = "XAU_USD"
POLL_INTERVAL = 1   # seconds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("position_monitor")


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _trigger_fired(trigger: Trigger, price: float) -> bool:
    """Return True if `price` crosses the trigger's threshold in the correct direction."""
    tp = float(trigger.trigger_price)
    return (trigger.greater_than and price >= tp) or (not trigger.greater_than and price <= tp)


def _ratchet_trail(greater_than: bool, dist: float, price: float, cur_stop: float) -> float:
    """Chandelier ratchet for a TRAILING_STOPLOSS_POINTS trigger.

    `dist` is the fixed trail distance (price points). Returns the new stop,
    which only ever tightens toward price (never loosens):
      * LONG  (greater_than=False, stop below price): stop = max(cur, price-dist)
      * SHORT (greater_than=True,  stop above price): stop = min(cur, price+dist)
    """
    if greater_than:                       # SHORT — stop sits above price
        return min(cur_stop, price + dist)
    return max(cur_stop, price - dist)     # LONG  — stop sits below price


def _realized_pnl(position: Position, close_price: float, qty: float) -> float:
    """Calculate realized P&L for closing `qty` units of `position`."""
    avg_buy = float(position.avg_buy_price)
    avg_sell = float(position.avg_sell_price)

    if avg_buy > 0:          # LONG position
        return (close_price - avg_buy) * qty
    elif avg_sell > 0:       # SHORT position
        return (avg_sell - close_price) * qty
    return 0.0


def _get_user_broker_id(sess, position: Position) -> uuid.UUID | None:
    """Look up user_broker_id through Position → UserStrategy."""
    us = sess.query(UserStrategy).filter_by(id=position.user_strategy_id).first()
    return us.user_broker_id if us else None


# ──────────────────────────────────────────────────────────────────────────────
# Core monitor tick
# ──────────────────────────────────────────────────────────────────────────────

def _update_currency_ltp(current_price: float) -> None:
    """Persist the latest price to CurrencyPair.ltp on every tick."""
    sess = Session()
    try:
        cp = sess.query(CurrencyPair).filter_by(symbol=SYMBOL).first()
        if cp:
            cp.ltp = str(current_price)
            sess.commit()
    except Exception:
        sess.rollback()
        log.exception("[LTP UPDATE] Failed to update CurrencyPair ltp")
    finally:
        sess.close()


def _check_triggers(current_price: float) -> None:
    """Evaluate all pending triggers against current_price and close positions when hit."""
    sess = Session()
    try:
        open_positions = (
            sess.query(Position)
            .filter(Position.symbol == SYMBOL, Position.quantity > 0)
            .all()
        )

        for pos in open_positions:
            # Update live LTP and unrealized P&L
            qty = float(pos.quantity) *100
            avg_buy = float(pos.avg_buy_price)
            avg_sell = float(pos.avg_sell_price)

            pos.ltp = Decimal(str(current_price))
            if avg_buy > 0:
                unrealized = (current_price - avg_buy) * qty
            elif avg_sell > 0:
                unrealized = (avg_sell - current_price) * qty
            else:
                unrealized = 0.0
            pos.profit_loss = Decimal(str(round(unrealized, 2)))

            # Evaluate pending triggers
            pending = (
                sess.query(Trigger)
                .filter_by(position_id=pos.id, status="PENDING")
                .all()
            )

            for trigger in pending:
                # TIME_EXIT triggers are tagged CUSTOM + order_type="TIME_EXIT"
                # and carry an absolute UNIX expiry epoch in trigger_price (NOT a
                # price). They MUST be evaluated on wall-clock, never via the
                # price comparison below (an epoch ~1.7e9 would otherwise fire a
                # price<=epoch trigger instantly). See entry_manager.place_entry.
                is_time_exit = (
                    trigger.trigger_type == "CUSTOM"
                    and (trigger.order_type or "") == "TIME_EXIT"
                )
                is_trail = trigger.trigger_type == "TRAILING_STOPLOSS_POINTS"
                # Whether this trigger, once fired, has NO broker-side equivalent
                # and must be actively closed at MetaAPI (true for TIME_EXIT and
                # for the ratcheted TRAIL level; SL/TP are attached at the broker).
                need_active_close = False

                if is_time_exit:
                    # Wall-clock exit: trigger_price holds an absolute UNIX epoch
                    # (NOT a price), so it MUST be compared to time.time(), never
                    # to current_price (an epoch ~1.7e9 would fire a price<=epoch
                    # trigger instantly). See entry_manager.place_entry.
                    if time.time() < float(trigger.trigger_price):
                        continue
                    label = "TIME_EXIT"
                    need_active_close = True
                    log.info("[TRIGGER] TIME_EXIT fired | held past max-hold | pos=%s", pos.id)
                elif is_trail:
                    # Chandelier trailing stop: ratchet the stop toward price each
                    # tick (trail distance lives in trail_points), persist it, then
                    # fire on the usual price-cross. The ratcheted level has no
                    # broker-side equivalent (the broker holds only the initial
                    # hard stop), so a fire here closes the position actively.
                    new_stop = _ratchet_trail(
                        trigger.greater_than,
                        float(trigger.trail_points),
                        current_price,
                        float(trigger.trigger_price),
                    )
                    if new_stop != float(trigger.trigger_price):
                        trigger.trigger_price = Decimal(str(round(new_stop, 2)))
                    if not _trigger_fired(trigger, current_price):
                        continue
                    label = "TRAIL"
                    need_active_close = True
                    log.info(
                        "[TRIGGER] TRAIL fired | price=%.2f stop=%.2f | pos=%s",
                        current_price, float(trigger.trigger_price), pos.id,
                    )
                else:
                    if not _trigger_fired(trigger, current_price):
                        continue
                    label = trigger.trigger_type
                    log.info(
                        "[TRIGGER] %s fired | price=%.2f trigger_price=%.2f | pos=%s",
                        label, current_price, float(trigger.trigger_price), pos.id,
                    )

                if need_active_close:
                    # Unlike SL/TP (attached at the broker, auto-closed there),
                    # actively close the MetaAPI position; otherwise the DB flattens
                    # but the real position rides on to its attached SL/TP.
                    entry_order = (
                        sess.query(Order)
                        .filter_by(position_id=pos.id, condition="ENTRY")
                        .first()
                    )
                    broker_pid = entry_order.broker_order_id if entry_order else None
                    closed_ok = close_position_by_id(broker_pid) if broker_pid else False
                    if not closed_ok:
                        log.warning(
                            "[%s] broker close not confirmed for pos=%s "
                            "(broker_pid=%s) — recording DB close anyway; broker "
                            "SL/TP remain attached as a backstop", label, pos.id, broker_pid)

                # Mark this trigger as triggered
                trigger.status = "TRIGGERED"

                # Close order
                user_broker_id = _get_user_broker_id(sess, pos)
                close_qty = float(trigger.quantity)
                close_order = Order(
                    id=uuid.uuid4(),
                    symbol=SYMBOL,
                    price=Decimal(str(current_price)),
                    condition=label,
                    side=trigger.side,
                    quantity=trigger.quantity,
                    amount=Decimal(str(round(current_price * close_qty, 2))),
                    order_type="MARKET",
                    status="EXECUTED",
                    reason=label,
                    position_id=pos.id,
                    user_broker_id=user_broker_id,
                )
                sess.add(close_order)

                # Update position
                realized = _realized_pnl(pos, current_price, close_qty)
                pos.realized_profit_loss = Decimal(
                    str(round(float(pos.realized_profit_loss) + realized, 2))
                )
                pos.quantity = Decimal("0")
                pos.profit_loss = Decimal("0")

                # Cancel all other pending triggers for this position
                for other in pending:
                    if other.id != trigger.id and other.status == "PENDING":
                        other.status = "CANCELLED"

                log.info(
                    "[CLOSE] pos=%s | reason=%s | close_price=%.2f | realized_pnl=%.2f",
                    pos.id, trigger.trigger_type, current_price, realized,
                )
                break  # one close per position per tick

        sess.commit()

    except Exception:
        sess.rollback()
        log.exception("[MONITOR ERROR] DB error during trigger check")
    finally:
        sess.close()


# ──────────────────────────────────────────────────────────────────────────────
# Main loop
# ──────────────────────────────────────────────────────────────────────────────

def run():
    log.info("=== Position Monitor started | symbol=%s | interval=%ds ===", SYMBOL, POLL_INTERVAL)

    while True:
        try:
            if is_market_closed_utc():
                log.debug("[MONITOR] Market closed — skipping tick")
            else:
                try:
                    price = fetch_latest_ltp(SYMBOL)
                except Exception:
                    log.exception("[MONITOR] fetch_latest_ltp failed")
                    price = None

                if price is not None:
                    _update_currency_ltp(price)
                    _check_triggers(price)
                else:
                    log.debug("[MONITOR] No LTP available — skipping tick")

        except KeyboardInterrupt:
            log.info("=== Position Monitor stopped by user ===")
            break
        except Exception:
            log.exception("[MONITOR FATAL] Unexpected error — continuing")

        try:
            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            log.info("=== Position Monitor stopped by user ===")
            break


if __name__ == "__main__":
    run()
