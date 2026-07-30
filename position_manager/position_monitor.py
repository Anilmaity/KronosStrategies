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
from shared.metaapi_client import close_position_by_id, client_for_broker
from trigger_logic import (
    USD_PER_POINT_PER_LOT,
    evaluate_trigger,
    ratchet_trail,
    trigger_fired,
)

# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────
SYMBOL        = "XAU_USD"
POLL_INTERVAL = 1   # seconds

# opt15(task3): throttle DB write amplification. pos.ltp / pos.profit_loss and
# the CurrencyPair.ltp mirror are pure functions of the live price -- when the
# price has not moved the value written is byte-identical, so skipping the write
# is a genuine no-op that no consumer (dashboards, kill-switch) can observe. We
# still refresh at least once every MONITOR_PNL_WRITE_SEC as a heartbeat (so a
# freshly opened position picks up a live ltp within the window). Trigger
# *firing* is never throttled -- it is evaluated against the live price on every
# tick below, independent of this gate.
MONITOR_PNL_WRITE_SEC = float(os.getenv("MONITOR_PNL_WRITE_SEC", "5"))
_last_write_price: float | None = None   # price at the last ltp/pnl write
_last_write_ts: float = 0.0              # unix ts of the last ltp/pnl write


def _reset_write_throttle() -> None:
    """Reset the price-mirror write throttle (used by tests)."""
    global _last_write_price, _last_write_ts
    _last_write_price = None
    _last_write_ts = 0.0


def _should_write_price(now_ts: float, current_price: float) -> bool:
    """True when the ltp/pnl price mirror should be persisted this tick.

    Writes when the price moved since the last written value, or when the
    MONITOR_PNL_WRITE_SEC heartbeat interval has elapsed. An unchanged price
    within the window is skipped -- the value would be identical.
    """
    return (
        _last_write_price is None
        or current_price != _last_write_price
        or (now_ts - _last_write_ts) >= MONITOR_PNL_WRITE_SEC
    )


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("position_monitor")


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

# Thin wrappers kept for back-compat (tests and any external callers); the
# actual logic lives in trigger_logic.py so it is unit-testable without a DB.

def _trigger_fired(trigger: Trigger, price: float) -> bool:
    """Return True if `price` crosses the trigger's threshold in the correct direction."""
    return trigger_fired(trigger.greater_than, float(trigger.trigger_price), price)


def _ratchet_trail(greater_than: bool, dist: float, price: float, cur_stop: float) -> float:
    """Chandelier ratchet — see trigger_logic.ratchet_trail."""
    return ratchet_trail(greater_than, dist, price, cur_stop)


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


# ── Active broker close with retry (TIME_EXIT/TRAIL/CUSTOM) ──────────────────
# The Jul-14 TIME_EXITs 504ed at the broker (env client resolved the wrong
# region) and the monitor flattened the DB anyway — the real positions rode on
# to their attached stops (−52pt each). Closes now go through the per-broker
# client (the entry manager's working path) and an unconfirmed close leaves
# the trigger PENDING for retry; only after _CLOSE_MAX_ATTEMPTS failures do we
# fall back to flatten-and-warn (broker SL/TP backstop remains attached).

_CLOSE_MAX_ATTEMPTS = int(os.getenv("BROKER_CLOSE_MAX_ATTEMPTS", "5"))
_CLOSE_RETRY_SEC    = float(os.getenv("BROKER_CLOSE_RETRY_SEC", "10"))
_close_attempts: dict = {}   # position id -> failed attempt count
_close_next_try: dict = {}   # position id -> unix ts of next allowed attempt


def _attempt_broker_close(sess, pos: Position, label: str,
                          now_ts: float) -> tuple[bool, bool]:
    """Try to close `pos` at the broker. Returns (finalize, attempted).

    finalize=True  → book the DB close (broker close confirmed, nothing to
                     close, or attempts exhausted).
    finalize=False → leave the trigger PENDING; the caller must NOT flatten.
    attempted=False → skipped inside the retry backoff window.
    """
    pid = pos.id
    if now_ts < _close_next_try.get(pid, 0.0):
        return False, False

    entry_order = (
        sess.query(Order)
        .filter_by(position_id=pos.id, condition="ENTRY")
        .first()
    )
    broker_pid = entry_order.broker_order_id if entry_order else None
    if not broker_pid:
        log.warning("[%s] no broker position id for pos=%s — DB close only",
                    label, pid)
        _close_attempts.pop(pid, None)
        _close_next_try.pop(pid, None)
        return True, True

    client = client_for_broker(sess, _get_user_broker_id(sess, pos))
    closed_ok = (client.close_position_by_id(broker_pid) if client
                 else close_position_by_id(broker_pid))
    if closed_ok:
        _close_attempts.pop(pid, None)
        _close_next_try.pop(pid, None)
        return True, True

    n = _close_attempts.get(pid, 0) + 1
    _close_attempts[pid] = n
    if n >= _CLOSE_MAX_ATTEMPTS:
        log.error(
            "[%s] broker close FAILED %d/%d times for pos=%s (broker_pid=%s) — "
            "giving up and recording DB close; broker SL/TP remain attached as "
            "a backstop. RECONCILE MANUALLY.",
            label, n, _CLOSE_MAX_ATTEMPTS, pid, broker_pid)
        _close_attempts.pop(pid, None)
        _close_next_try.pop(pid, None)
        return True, True

    _close_next_try[pid] = now_ts + _CLOSE_RETRY_SEC
    log.warning(
        "[%s] broker close not confirmed for pos=%s (broker_pid=%s) — attempt "
        "%d/%d, retrying in %.0fs; trigger stays PENDING",
        label, pid, broker_pid, n, _CLOSE_MAX_ATTEMPTS, _CLOSE_RETRY_SEC)
    return False, True


# ──────────────────────────────────────────────────────────────────────────────
# Core monitor tick
# ──────────────────────────────────────────────────────────────────────────────

def _check_triggers(current_price: float) -> None:
    """Evaluate all pending triggers against current_price and close positions when hit.

    One Session per tick (opt15 task3): the CurrencyPair.ltp mirror, the
    per-position ltp / unrealized-P&L refresh (throttled), the PENDING-trigger
    read (ONE batched query for all open positions), and every close share a
    single session and a single commit. The broker-close path is unchanged.
    Trigger *decisions* are evaluated against the live price on every tick,
    independent of the price-mirror throttle.
    """
    global _last_write_price, _last_write_ts

    now_ts = time.time()
    write_price = _should_write_price(now_ts, current_price)

    sess = Session()
    try:
        # (3) CurrencyPair LTP mirror -- throttled to the same cadence as the
        # per-position P&L write, folded into this tick's single session.
        if write_price:
            cp = sess.query(CurrencyPair).filter_by(symbol=SYMBOL).first()
            if cp:
                cp.ltp = str(current_price)

        open_positions = (
            sess.query(Position)
            .filter(Position.symbol == SYMBOL, Position.quantity > 0)
            .all()
        )

        # (1) Kill the N+1: one PENDING-trigger read for ALL open positions,
        # grouped by position in Python (was one SELECT per position per tick).
        pos_ids = [pos.id for pos in open_positions]
        triggers_by_pos: dict = {}
        if pos_ids:
            for trig in (
                sess.query(Trigger)
                .filter(Trigger.position_id.in_(pos_ids),
                        Trigger.status == "PENDING")
                .all()
            ):
                triggers_by_pos.setdefault(trig.position_id, []).append(trig)

        for pos in open_positions:
            # (2) Refresh live LTP and unrealized P&L only when the price moved
            # or the heartbeat elapsed. quantity is in LOTS; the contract factor
            # converts $-move × lots into USD (see trigger_logic module docstring
            # for the units convention).
            if write_price:
                qty = float(pos.quantity) * USD_PER_POINT_PER_LOT
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

            # Evaluate pending triggers (batch-fetched above, grouped per pos).
            pending = triggers_by_pos.get(pos.id, [])

            for trigger in pending:
                # All TP/SL/TIME_EXIT/TRAIL semantics live in trigger_logic
                # (pure, unit-tested); this loop only applies the decision.
                decision = evaluate_trigger(trigger, current_price, time.time())

                if decision.new_trail_stop is not None:
                    # Persist the ratcheted chandelier stop even when it does
                    # not fire this tick — the broker holds only the initial
                    # hard stop; the ratcheted level exists solely here.
                    trigger.trigger_price = Decimal(str(decision.new_trail_stop))

                if not decision.fired:
                    continue

                label = decision.label
                if label == "TIME_EXIT":
                    log.info("[TRIGGER] TIME_EXIT fired | held past max-hold | pos=%s", pos.id)
                elif label == "TRAIL":
                    log.info(
                        "[TRIGGER] TRAIL fired | price=%.2f stop=%.2f | pos=%s",
                        current_price, float(trigger.trigger_price), pos.id,
                    )
                else:
                    log.info(
                        "[TRIGGER] %s fired | price=%.2f trigger_price=%.2f | pos=%s",
                        label, current_price, float(trigger.trigger_price), pos.id,
                    )

                if decision.need_active_close:
                    # Unlike SL/TP (attached at the broker, auto-closed there),
                    # actively close the MetaAPI position via the per-broker
                    # client. An unconfirmed close leaves this trigger PENDING
                    # and the position open in the DB — retried with backoff
                    # (see _attempt_broker_close) instead of flattening while
                    # the real position rides on at the broker.
                    finalize, _ = _attempt_broker_close(
                        sess, pos, label, time.time())
                    if not finalize:
                        break  # keep trigger PENDING; retry on a later tick

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
        # Record the write only after a successful commit: a rolled-back tick
        # must not convince the next tick that the mirror is already current.
        if write_price:
            _last_write_price = current_price
            _last_write_ts = now_ts

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
                    # CurrencyPair.ltp is now updated inside _check_triggers'
                    # single per-tick session (opt15 task3).
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
