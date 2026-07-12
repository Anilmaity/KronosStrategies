"""
trigger_logic.py
----------------
Pure trigger-evaluation logic for position_monitor — no DB, no broker, no
wall-clock. position_monitor calls evaluate_trigger() once per pending trigger
per tick and applies the returned decision (persist ratcheted stop, mark
TRIGGERED, close at broker, book P&L). Keeping the decision pure makes the
TP/SL/TIME_EXIT/TRAIL semantics unit-testable without a database.

Units note: 1 lot of XAUUSD moves $100 per $1 of price ("contract factor").
Position.quantity is stored in LOTS; the monitor's *unrealized* profit_loss is
stored in USD (lots × USD_PER_POINT_PER_LOT × points), while *realized*
profit_loss is booked from the raw trigger quantity (points × lots). Do not
"harmonize" the two without checking every consumer (kill-switch, dashboards).
"""
from __future__ import annotations

from dataclasses import dataclass

# 1 XAUUSD lot = 100 oz → $1 of price move = $100 P&L per lot.
USD_PER_POINT_PER_LOT = 100.0


def is_time_exit(trigger_type: str, order_type: str | None) -> bool:
    """True for TIME_EXIT triggers: tagged CUSTOM + order_type="TIME_EXIT".

    These carry an absolute UNIX expiry epoch in trigger_price (NOT a price)
    and MUST be evaluated on wall-clock, never via price comparison — an epoch
    ~1.7e9 would otherwise fire a price<=epoch trigger instantly. This is the
    single shared definition of the convention entry_manager.place_entry writes.
    """
    return trigger_type == "CUSTOM" and (order_type or "") == "TIME_EXIT"


def trigger_fired(greater_than: bool, trigger_price: float, price: float) -> bool:
    """True if `price` crosses the threshold in the trigger's direction."""
    return (greater_than and price >= trigger_price) or (
        not greater_than and price <= trigger_price
    )


def ratchet_trail(greater_than: bool, dist: float, price: float, cur_stop: float) -> float:
    """Chandelier ratchet for a TRAILING_STOPLOSS_POINTS trigger.

    `dist` is the fixed trail distance (price points). Returns the new stop,
    which only ever tightens toward price (never loosens):
      * LONG  (greater_than=False, stop below price): stop = max(cur, price-dist)
      * SHORT (greater_than=True,  stop above price): stop = min(cur, price+dist)
    """
    if greater_than:                       # SHORT — stop sits above price
        return min(cur_stop, price + dist)
    return max(cur_stop, price - dist)     # LONG  — stop sits below price


@dataclass(frozen=True)
class TriggerDecision:
    """Outcome of evaluating one pending trigger against price/now.

    fired             — the trigger fires this tick (close the position).
    label             — close reason: "TIME_EXIT" | "TRAIL" | the trigger_type.
    need_active_close — no broker-side equivalent exists (TIME_EXIT, ratcheted
                        TRAIL level); the MetaAPI position must be closed
                        actively, otherwise only the DB flattens.
    new_trail_stop    — ratcheted chandelier stop rounded to 2dp, to persist on
                        the trigger even when it does not fire; None = no change.
    """
    fired: bool
    label: str | None = None
    need_active_close: bool = False
    new_trail_stop: float | None = None


def evaluate_trigger(trigger, price: float, now_epoch: float) -> TriggerDecision:
    """Evaluate one pending trigger. Pure — no I/O, no clock, no ORM needs.

    `trigger` is duck-typed (ORM Trigger or any object) with attributes:
    trigger_type, order_type, greater_than, trigger_price, trail_points.
    """
    ttype = trigger.trigger_type

    if is_time_exit(ttype, getattr(trigger, "order_type", None)):
        # Wall-clock exit: trigger_price holds an absolute UNIX epoch.
        if now_epoch < float(trigger.trigger_price):
            return TriggerDecision(fired=False)
        return TriggerDecision(fired=True, label="TIME_EXIT", need_active_close=True)

    if ttype == "TRAILING_STOPLOSS_POINTS":
        # Ratchet the stop toward price each tick, persist it (rounded to 2dp,
        # matching the Numeric(25,2) column), then fire on the usual price
        # cross of the *persisted* level. The ratcheted level has no broker-
        # side equivalent (the broker holds only the initial hard stop), so a
        # fire here must actively close the position.
        cur_stop = float(trigger.trigger_price)
        raw_stop = ratchet_trail(
            trigger.greater_than, float(trigger.trail_points), price, cur_stop
        )
        new_trail_stop = round(raw_stop, 2) if raw_stop != cur_stop else None
        effective_stop = new_trail_stop if new_trail_stop is not None else cur_stop
        fired = trigger_fired(trigger.greater_than, effective_stop, price)
        return TriggerDecision(
            fired=fired,
            label="TRAIL" if fired else None,
            need_active_close=fired,
            new_trail_stop=new_trail_stop,
        )

    # Plain price triggers (TARGET / STOPLOSS / ...): SL/TP are also attached
    # at the broker as a backstop, so no active close is needed on fire.
    fired = trigger_fired(trigger.greater_than, float(trigger.trigger_price), price)
    return TriggerDecision(fired=fired, label=ttype if fired else None)
