# tests/test_s94_sweep_reversal.py
"""S94 liquidity-sweep reversal (M5) — trend-category child (2026-07-07).

All synthetic: a hand-built M5 frame producing swing level -> sweep ->
close-back confirmation (HTF15-contained), then 1m probe bars for the retest.
No DB, no network.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pandas as pd
import pytest

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies")
)
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from backtest_strategies import s94_sweep_reversal as s94  # noqa: E402
from strategy.entry_manager import _VARIATION_STRATEGY_NAME  # noqa: E402

# midnight-aligned so 15m buckets are index//3 and PD levels exist from day 1
_T0 = pd.Timestamp("2026-06-04T00:00:00Z")

# frame geometry (indices):
#   0..299   flat 2000 (highs 2000.5 / lows 1999.5) — spans a full UTC day
#   300      swing-high bar, high 2010
#   301..310 flat below (swing confirms at 310 -> level 2010 active)
#   fast:  312 sweep bar h2012 l2004, 313 confirm bar close 2008  (same 15m
#          bucket: 312//3 == 313//3 == 104 -> HTF15 contained)
#   slow:  311 sweep bar h2012 close 2011, 312 hold bar close 2010.6,
#          313 confirm close 2008 (break bucket 103 != confirm bucket 104 and
#          bucket-103 close is above the level -> NOT HTF15 valid)
_LEVEL, _EXTREME = 2010.0, 2012.0
_STOP = 2012.2            # extreme + 0.1 * pen (pen = 2.0)
_TP = 1996.0              # extreme - 2 * (2012 - 2004) break-bar leg


def _frame(fast=True):
    rows = []

    def add(o, h, l, c):
        rows.append(dict(time=_T0 + pd.Timedelta(minutes=5 * len(rows)),
                         open=float(o), high=float(h), low=float(l),
                         close=float(c), volume=1.0))

    for _ in range(300):
        add(2000, 2000.5, 1999.5, 2000)
    add(2000, 2010, 1999.5, 2005)                 # i=300 swing high
    for _ in range(10):
        add(2004, 2006, 2003, 2004)               # 301..310 (confirm @310)
    if fast:
        add(2004, 2006, 2003, 2004)               # i=311 filler
        add(2005, 2012, 2004, 2006)               # i=312 sweep (bucket 104)
        add(2006, 2011, 2005, 2008)               # i=313 confirm (bucket 104)
    else:
        add(2005, 2012, 2004, 2011)               # i=311 sweep (bucket 103)
        add(2010.8, 2011.5, 2010.2, 2010.6)       # i=312 hold above level
        add(2010.5, 2011, 2005, 2008)             # i=313 confirm (bucket 104)
    return pd.DataFrame(rows)


_CONF_TIME = _T0 + pd.Timedelta(minutes=5 * 313)


def _w1m(minutes_after_conf, hi, lo):
    ts = _CONF_TIME + pd.Timedelta(minutes=minutes_after_conf)
    return pd.DataFrame([dict(time=ts, open=hi - 0.3, high=float(hi),
                              low=float(lo), close=hi - 0.2, volume=1.0)])


def _now(minutes_after_conf=5):
    return (datetime(2026, 6, 4, tzinfo=timezone.utc)
            + pd.Timedelta(minutes=5 * 313 + minutes_after_conf))


@pytest.fixture(autouse=True)
def _reset():
    s94.reset_state()
    yield
    s94.reset_state()


def test_confirmation_arms_but_does_not_fire_on_own_bar():
    # No 1m frame: the M5 probe is the confirmation bar itself -> must NOT
    # fire (armed_after guard), but the setup must be armed with the
    # validated geometry.
    assert s94.get_signal(None, _frame(), None, _now(0)) is None
    assert len(s94._pending) == 1
    p = s94._pending[0]
    assert p["side"] == -1
    assert p["level"] == _LEVEL
    assert p["stop"] == pytest.approx(_STOP)
    assert p["tp"] == pytest.approx(_TP)


def test_retest_fires_short_with_validated_geometry():
    f = _frame()
    assert s94.get_signal(None, f, None, _now(0)) is None          # arm
    sig = s94.get_signal(_w1m(6, hi=2010.5, lo=2009.0), f, None, _now(6))
    assert sig is not None and sig.side == "SELL"
    assert sig.entry_price == _LEVEL
    assert sig.stop_loss == pytest.approx(_STOP)
    assert sig.take_profit == pytest.approx(_TP)
    assert sig.max_hold_min == s94._MAX_HOLD_MIN
    assert not s94._pending                                        # consumed


def test_phantom_guard_cancels_when_probe_crosses_stop():
    f = _frame()
    assert s94.get_signal(None, f, None, _now(0)) is None          # arm
    sig = s94.get_signal(_w1m(6, hi=2012.5, lo=2009.0), f, None, _now(6))
    assert sig is None
    assert not s94._pending                                        # cancelled


def test_setup_expires_after_entry_ttl():
    f = _frame()
    assert s94.get_signal(None, f, None, _now(0)) is None          # arm
    late = _now(5 * s94._ENTRY_TTL + 10)
    sig = s94.get_signal(_w1m(5 * s94._ENTRY_TTL + 10, 2010.5, 2009.0),
                         f, None, late)
    assert sig is None
    assert not s94._pending


def test_slow_cross_bucket_sweep_is_not_htf15_valid():
    # Break and confirmation in different 15m buckets, and the completed
    # prior 15m candle closed ABOVE the level (no wick-rejection) -> invalid.
    assert s94.get_signal(None, _frame(fast=False), None, _now(0)) is None
    assert not s94._pending


def test_variation_registered_for_entry_manager():
    assert _VARIATION_STRATEGY_NAME[s94.NAME] == "S94 Sweep Reversal"
    assert s94.CONFIG.name == s94.NAME
