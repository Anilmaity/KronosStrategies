"""
test_trigger_logic.py
---------------------
Unit tests for position_manager/trigger_logic.py — the pure decision core of
position_monitor._check_triggers (TP/SL/TIME_EXIT/TRAIL). Pure logic — no DB,
no broker, no network.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

_PM_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "position_manager")
)
if _PM_DIR not in sys.path:
    sys.path.insert(0, _PM_DIR)

from trigger_logic import (  # noqa: E402
    TriggerDecision,
    evaluate_trigger,
    is_time_exit,
    ratchet_trail,
    trigger_fired,
)


def _trig(**kw) -> SimpleNamespace:
    base = dict(
        trigger_type="STOPLOSS",
        order_type="LIMIT",
        greater_than=False,
        trigger_price=2000.0,
        trail_points=0.0,
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ── is_time_exit predicate ───────────────────────────────────────────────────

def test_is_time_exit_requires_custom_and_time_exit():
    assert is_time_exit("CUSTOM", "TIME_EXIT") is True
    assert is_time_exit("CUSTOM", "LIMIT") is False
    assert is_time_exit("CUSTOM", None) is False
    assert is_time_exit("STOPLOSS", "TIME_EXIT") is False


# ── TIME_EXIT ────────────────────────────────────────────────────────────────

def test_time_exit_not_expired_does_not_fire():
    t = _trig(trigger_type="CUSTOM", order_type="TIME_EXIT",
              greater_than=False, trigger_price=1_800_000_000.0)
    d = evaluate_trigger(t, price=2000.0, now_epoch=1_799_999_999.0)
    assert d == TriggerDecision(fired=False)


def test_time_exit_expired_fires_with_active_close():
    t = _trig(trigger_type="CUSTOM", order_type="TIME_EXIT",
              greater_than=False, trigger_price=1_800_000_000.0)
    d = evaluate_trigger(t, price=2000.0, now_epoch=1_800_000_000.0)
    assert d.fired and d.label == "TIME_EXIT" and d.need_active_close
    assert d.new_trail_stop is None


def test_time_exit_epoch_never_compared_to_price():
    """The guarded bug: an epoch ~1.8e9 in trigger_price with greater_than=False
    would fire instantly on price<=epoch if evaluated as a price trigger."""
    t = _trig(trigger_type="CUSTOM", order_type="TIME_EXIT",
              greater_than=False, trigger_price=1_800_000_000.0)
    d = evaluate_trigger(t, price=2000.0, now_epoch=0.0)
    assert d.fired is False


# ── plain price triggers (SL/TP have a broker-side backstop) ────────────────

def test_target_fires_on_price_at_or_above():
    t = _trig(trigger_type="TARGET", greater_than=True, trigger_price=2100.0)
    assert evaluate_trigger(t, 2100.0, 0.0) == TriggerDecision(fired=True, label="TARGET")
    assert evaluate_trigger(t, 2099.99, 0.0).fired is False


def test_stoploss_fires_on_price_at_or_below():
    t = _trig(trigger_type="STOPLOSS", greater_than=False, trigger_price=1990.0)
    d = evaluate_trigger(t, 1990.0, 0.0)
    assert d.fired and d.label == "STOPLOSS" and not d.need_active_close
    assert evaluate_trigger(t, 1990.01, 0.0).fired is False


def test_custom_without_time_exit_order_type_is_a_price_trigger():
    t = _trig(trigger_type="CUSTOM", order_type="LIMIT",
              greater_than=True, trigger_price=2050.0)
    d = evaluate_trigger(t, 2051.0, 0.0)
    assert d.fired and d.label == "CUSTOM"


# ── chandelier trail ─────────────────────────────────────────────────────────

def test_trail_long_ratchets_up_without_firing():
    t = _trig(trigger_type="TRAILING_STOPLOSS_POINTS", greater_than=False,
              trigger_price=1970.0, trail_points=30.0)
    d = evaluate_trigger(t, price=2100.0, now_epoch=0.0)
    assert d.fired is False and d.label is None
    assert d.new_trail_stop == 2070.0  # 2100 - 30, persisted even w/o fire


def test_trail_long_fires_on_pullback_to_ratcheted_stop():
    t = _trig(trigger_type="TRAILING_STOPLOSS_POINTS", greater_than=False,
              trigger_price=2070.0, trail_points=30.0)
    d = evaluate_trigger(t, price=2070.0, now_epoch=0.0)
    assert d.fired and d.label == "TRAIL" and d.need_active_close
    assert d.new_trail_stop is None  # stop unchanged at fire


def test_trail_long_never_loosens():
    t = _trig(trigger_type="TRAILING_STOPLOSS_POINTS", greater_than=False,
              trigger_price=2070.0, trail_points=30.0)
    d = evaluate_trigger(t, price=2080.0, now_epoch=0.0)  # 2080-30 < 2070
    assert d.new_trail_stop is None and d.fired is False


def test_trail_short_ratchets_down_and_fires_symmetrically():
    t = _trig(trigger_type="TRAILING_STOPLOSS_POINTS", greater_than=True,
              trigger_price=2030.0, trail_points=30.0)
    d = evaluate_trigger(t, price=1950.0, now_epoch=0.0)
    assert d.fired is False and d.new_trail_stop == 1980.0
    t2 = _trig(trigger_type="TRAILING_STOPLOSS_POINTS", greater_than=True,
               trigger_price=1980.0, trail_points=30.0)
    d2 = evaluate_trigger(t2, price=1980.0, now_epoch=0.0)
    assert d2.fired and d2.label == "TRAIL" and d2.need_active_close


def test_trail_stop_rounds_to_two_decimals_and_fire_uses_rounded_level():
    # price - dist = 2069.995 → persisted stop rounds to 2.0 dp (Numeric(25,2))
    # and the fire check runs against the ROUNDED level, exactly like the old
    # inline code (assignment before _trigger_fired).
    t = _trig(trigger_type="TRAILING_STOPLOSS_POINTS", greater_than=False,
              trigger_price=2000.0, trail_points=30.005)
    d = evaluate_trigger(t, price=2100.0, now_epoch=0.0)
    assert d.new_trail_stop == round(2100.0 - 30.005, 2) == 2069.99
    assert d.fired is False


def test_trail_fire_check_uses_persisted_rounded_stop():
    # ratchet moves stop to round(2070.004,2)=2070.0; price 2070.0 fires on it
    t = _trig(trigger_type="TRAILING_STOPLOSS_POINTS", greater_than=False,
              trigger_price=2000.0, trail_points=29.996)
    d = evaluate_trigger(t, price=2100.0, now_epoch=0.0)
    assert d.new_trail_stop == 2070.0
    d2 = evaluate_trigger(
        _trig(trigger_type="TRAILING_STOPLOSS_POINTS", greater_than=False,
              trigger_price=2070.0, trail_points=29.996),
        price=2070.0, now_epoch=0.0,
    )
    assert d2.fired is True


# ── primitives kept in sync with position_monitor's wrappers ────────────────

def test_trigger_fired_directionality():
    assert trigger_fired(True, 2100.0, 2100.0) is True
    assert trigger_fired(True, 2100.0, 2099.0) is False
    assert trigger_fired(False, 1990.0, 1990.0) is True
    assert trigger_fired(False, 1990.0, 1991.0) is False


def test_ratchet_matches_position_monitor_wrapper():
    import position_monitor as pm
    assert pm._ratchet_trail(False, 30.0, 2100.0, 2050.0) == ratchet_trail(False, 30.0, 2100.0, 2050.0) == 2070.0
    assert pm._ratchet_trail(True, 30.0, 2000.0, 2050.0) == ratchet_trail(True, 30.0, 2000.0, 2050.0) == 2030.0
