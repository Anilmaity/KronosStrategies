"""
test_trail_ratchet.py
---------------------
Unit tests for the chandelier trailing-stop ratchet in position_monitor.
Pure logic — no DB, no broker, no network (create_engine in shared.models is
lazy, so importing the monitor does not open a connection).
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

# position_monitor.py lives in position_manager/ and does `from shared...`,
# so put that dir on the path before importing it.
_PM_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "position_manager")
)
if _PM_DIR not in sys.path:
    sys.path.insert(0, _PM_DIR)

import position_monitor as pm  # noqa: E402


# greater_than=False => LONG (stop sits BELOW price, ratchets UP)
def test_long_ratchets_up_as_price_rises():
    assert pm._ratchet_trail(False, 30.0, 2100.0, 2050.0) == 2070.0


def test_long_never_loosens_when_price_falls():
    # price pulled back to 2080 but stop already at 2070 — must not drop.
    assert pm._ratchet_trail(False, 30.0, 2080.0, 2070.0) == 2070.0


# greater_than=True => SHORT (stop sits ABOVE price, ratchets DOWN)
def test_short_ratchets_down_as_price_falls():
    assert pm._ratchet_trail(True, 30.0, 2000.0, 2050.0) == 2030.0


def test_short_never_loosens_when_price_rises():
    assert pm._ratchet_trail(True, 30.0, 2010.0, 2030.0) == 2030.0


def test_long_full_cycle_ratchet_then_fire():
    """Long entry 2000, trail 30. Price runs to 2100 (stop -> 2070), then
    pulls back to 2070 and the trail fires."""
    stop = 1970.0  # initial = entry(2000) - 30
    for price in (2010, 2050, 2100):  # high-water mark climbs
        stop = pm._ratchet_trail(False, 30.0, price, stop)
    assert stop == 2070.0
    long_trig = SimpleNamespace(trigger_price=stop, greater_than=False)
    assert pm._trigger_fired(long_trig, 2070.0) is True   # price <= stop -> fire
    assert pm._trigger_fired(long_trig, 2071.0) is False  # still above stop


def test_short_full_cycle_ratchet_then_fire():
    stop = 2030.0  # initial = entry(2000) + 30
    for price in (1990, 1950, 1900):  # low-water mark drops
        stop = pm._ratchet_trail(True, 30.0, price, stop)
    assert stop == 1930.0
    short_trig = SimpleNamespace(trigger_price=stop, greater_than=True)
    assert pm._trigger_fired(short_trig, 1930.0) is True   # price >= stop -> fire
    assert pm._trigger_fired(short_trig, 1929.0) is False
