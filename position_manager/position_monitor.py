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
from shared.metaapi_client import close_position_by_id, get_position_realized_pnl

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


def _realized_pnl(position: Position, close_price: float, qty: float) -> float:
    """Calculate realized P&L for closing `qty` units of `position`."""
    avg_buy = float(position.avg_buy_price)
    avg_sell = float(position.avg_sell_price)

    if avg_buy > 0:          # LONG position
        return (close_price - avg_buy) * qty
    elif avg_sell > 0:       # SHORT position
        return (avg_sell - close_price) * qty
    return 0.0


def _resolve_realized_close(model_realized: float, model_close_price: float,
                            broker_pid: str | None,
                            lookup=get_position_realized_pnl) -> tuple[float, float, str]:
    """Prefer the broker's SETTLED realized PnL + close price over the OANDA-mid
    model figure position_monitor computes from its own trigger price.

    The model figure ignores fill slippage, spread, commission and swap, so a
    trade modelled as +$56 can be ~$0 or negative in the real account. When the
    broker's history-deals lookup returns a settled (closed) deal we record that;
    otherwise we keep the model so a close is never lost waiting on the broker.
    Returns (realized_pnl, close_price, source) where source is 'broker'|'model'.
    """
    if broker_pid and broker_pid != "dry-run":
        deal = lookup(broker_pid)
        if deal and deal.get("closed"):
            cp = deal.get("close_price")
            return (deal["realized_pnl"],
                    cp if cp is not None else model_close_price,
                    "broker")
    return model_realized, model_close_price, "model"


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
                if is_time_exit:
                    if time.time() < float(trigger.trigger_price):
                        continue
                    label = "TIME_EXIT"
                    log.info("[TRIGGER] TIME_EXIT fired | held past max-hold | pos=%s", pos.id)
                    # Unlike SL/TP (attached at the broker, auto-closed there),
                    # a time-exit has NO broker-side equivalent. Actively close
                    # the MetaAPI position; otherwise the DB flattens but the real
                    # position rides on to its attached SL/TP.
                    entry_order = (
                        sess.query(Order)
                        .filter_by(position_id=pos.id, condition="ENTRY")
                        .first()
                    )
                    broker_pid = entry_order.broker_order_id if entry_order else None
                    closed_ok = close_position_by_id(broker_pid) if broker_pid else False
                    if not closed_ok:
                        log.warning(
                            "[TIME_EXIT] broker close not confirmed for pos=%s "
                            "(broker_pid=%s) — recording DB close anyway; broker "
                            "SL/TP remain attached as a backstop", pos.id, broker_pid)
                else:
                    if not _trigger_fired(trigger, current_price):
                        continue
                    label = trigger.trigger_type
                    log.info(
                        "[TRIGGER] %s fired | price=%.2f trigger_price=%.2f | pos=%s",
                        label, current_price, float(trigger.trigger_price), pos.id,
                    )

                # Mark this trigger as triggered
                trigger.status = "TRIGGERED"

                close_qty = float(trigger.quantity)

                # Reconcile against BROKER TRUTH. The OANDA-mid trigger price is a
                # model close; the broker's settled deal (real fill/close price +
                # commission + swap) is what actually hit the account. Prefer it,
                # so the dashboard stops showing profit the account never earned.
                model_realized = _realized_pnl(pos, current_price, close_qty)
                entry_order = (
                    sess.query(Order)
                    .filter_by(position_id=pos.id, condition="ENTRY")
                    .first()
                )
                broker_pid = entry_order.broker_order_id if entry_order else None
                realized, close_price, source = _resolve_realized_close(
                    model_realized, current_price, broker_pid)

                # Close order (recorded at the resolved close price)
                user_broker_id = _get_user_broker_id(sess, pos)
                close_order = Order(
                    id=uuid.uuid4(),
                    symbol=SYMBOL,
                    price=Decimal(str(close_price)),
                    condition=label,
                    side=trigger.side,
                    quantity=trigger.quantity,
                    amount=Decimal(str(round(close_price * close_qty, 2))),
                    order_type="MARKET",
                    status="EXECUTED",
                    reason=label,
                    position_id=pos.id,
                    user_broker_id=user_broker_id,
                )
                sess.add(close_order)

                # Update position
                pos.realized_profit_loss = Decimal(
                    str(round(float(pos.realized_profit_loss) + realized, 2))
                )
                pos.ltp = Decimal(str(close_price))
                pos.quantity = Decimal("0")
                pos.profit_loss = Decimal("0")

                # Cancel all other pending triggers for this position
                for other in pending:
                    if other.id != trigger.id and other.status == "PENDING":
                        other.status = "CANCELLED"

                log.info(
                    "[CLOSE] pos=%s | reason=%s | close_price=%.2f | realized_pnl=%.2f (%s)",
                    pos.id, trigger.trigger_type, close_price, realized, source,
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
