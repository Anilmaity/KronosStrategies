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
                if not _trigger_fired(trigger, current_price):
                    continue

                log.info(
                    "[TRIGGER] %s fired | price=%.2f trigger_price=%.2f | pos=%s",
                    trigger.trigger_type, current_price,
                    float(trigger.trigger_price), pos.id,
                )

                # Mark this trigger as triggered
                trigger.status = "TRIGGERED"

                # Close order
                user_broker_id = _get_user_broker_id(sess, pos)
                close_qty = float(trigger.quantity)
                close_order = Order(
                    id=uuid.uuid4(),
                    symbol=SYMBOL,
                    price=Decimal(str(current_price)),
                    condition=trigger.trigger_type,
                    side=trigger.side,
                    quantity=trigger.quantity,
                    amount=Decimal(str(round(current_price * close_qty, 2))),
                    order_type="MARKET",
                    status="EXECUTED",
                    reason=trigger.trigger_type,
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
            price = fetch_latest_ltp(SYMBOL)

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

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
