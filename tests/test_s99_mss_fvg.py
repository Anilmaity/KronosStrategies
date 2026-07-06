# tests/test_s99_mss_fvg.py
"""S99 ICT MSS+FVG reversal (M5) — the reversal-category child (2026-07-06).

All synthetic: a hand-built M5 frame that produces sweep -> MSS -> FVG, then
1m probe bars for the retrace. No DB, no network.
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

from backtest_strategies import s99_mss_fvg as s99  # noqa: E402
from backtest_strategies.base import Signal  # noqa: E402
from strategy.entry_manager import _VARIATION_STRATEGY_NAME  # noqa: E402

_T0 = pd.Timestamp("2026-06-05T02:00:00Z")


def _frame(with_sweep=True):
    """60 flat bars, swing high 2010 (confirmed), swing low 1995 (confirmed),
    a sweep to 2012 (optional), then a displacement bar closing below the
    swing low and leaving a bearish FVG (high 2000.2 < low[66] 2004)."""
    rows = []

    def add(o, h, l, c):
        rows.append(dict(time=_T0 + pd.Timedelta(minutes=5 * len(rows)),
                         open=float(o), high=float(h), low=float(l),
                         close=float(c), volume=1.0))

    for _ in range(60):
        add(2000, 2000.5, 1999.5, 2000)
    add(2000, 2010, 1999.5, 2005)          # i=60 swing high
    add(2005, 2006, 2000, 2004)            # i=61
    add(2004, 2006, 2000, 2004)            # i=62 (swing high confirms)
    add(2004, 2005, 1995, 2000)            # i=63 swing low
    add(2000, 2005, 1998, 2003)            # i=64
    add(2003, 2005, 1998, 2004)            # i=65 (swing low confirms)
    add(2004, 2012 if with_sweep else 2006, 2004, 2006)   # i=66 sweep bar
    add(2006, 2006.5, 1999, 2000)          # i=67
    add(2000, 2000.2, 1992, 1993)          # i=68 MSS + bearish FVG
    return pd.DataFrame(rows)


def _w1m(minute_offset, hi, lo):
    ts = _T0 + pd.Timedelta(minutes=68 * 5 + minute_offset)
    return pd.DataFrame([dict(time=ts, open=hi - 0.3, high=float(hi),
                              low=float(lo), close=hi - 0.2, volume=1.0)])


def _now(minute_offset=0):
    base = datetime(2026, 6, 5, 7, 40, tzinfo=timezone.utc)
    return base + pd.Timedelta(minutes=minute_offset)


@pytest.fixture(autouse=True)
def _reset():
    s99.reset_state()
    yield
    s99.reset_state()


def test_mss_arms_but_does_not_fire_on_own_bar():
    # No 1m frame: the M5 probe is the MSS bar itself -> must NOT fire.
    assert s99.get_signal(None, _frame(), None, _now()) is None
    assert s99._pending is not None and s99._pending["side"] == -1


def test_retrace_into_fvg_fires_short_with_validated_geometry():
    f = _frame()
    assert s99.get_signal(None, f, None, _now()) is None      # arm
    sig = s99.get_signal(_w1m(6, hi=2001.0, lo=1999.0), f, None, _now(6))
    assert sig is not None and isinstance(sig, Signal)
    assert sig.side == "SELL"
    assert sig.reason == "S99_MSS_FVG_SHORT"
    assert sig.entry_price == pytest.approx(2000.2)           # proximal edge
    assert sig.stop_loss > 2004.0                             # beyond distal + buf
    risk = sig.stop_loss - sig.entry_price
    assert (sig.entry_price - sig.take_profit) == pytest.approx(1.5 * risk, abs=0.03)
    assert sig.max_hold_min == 480
    assert s99._pending is None                               # consumed


def test_probe_through_stop_cancels_instead_of_firing():
    f = _frame()
    s99.get_signal(None, f, None, _now())
    sl = s99._pending["sl"]
    assert s99.get_signal(_w1m(6, hi=sl + 0.5, lo=2000.0), f, None, _now(6)) is None
    assert s99._pending is None
    # and the setup does not come back on a later clean touch
    assert s99.get_signal(_w1m(7, hi=2001.0, lo=1999.0), f, None, _now(7)) is None


def test_setup_expires_after_retrace_window():
    f = _frame()
    s99.get_signal(None, f, None, _now())
    late = _now(5 * s99._RETRACE_W + 5)
    assert s99.get_signal(_w1m(6, hi=2001.0, lo=1999.0), f, None, late) is None
    assert s99._pending is None


def test_out_of_hours_clears_and_blocks():
    f = _frame()
    s99.get_signal(None, f, None, _now())
    off = datetime(2026, 6, 5, 16, 5, tzinfo=timezone.utc)    # 16 not in 1..15
    assert s99.get_signal(_w1m(6, hi=2001.0, lo=1999.0), f, None, off) is None
    assert s99._pending is None


def test_no_setup_without_liquidity_sweep():
    assert s99.get_signal(None, _frame(with_sweep=False), None, _now()) is None
    assert s99._pending is None


def test_one_setup_per_mss_bar():
    f = _frame()
    s99.get_signal(None, f, None, _now())
    s99._pending = None                    # simulate a cancelled setup
    s99.get_signal(None, f, None, _now(1))
    assert s99._pending is None, "same MSS bar must not re-arm"


def test_config_contract():
    assert s99.NAME == "KRONOS_S99_MSS_FVG"
    assert s99.NAME in _VARIATION_STRATEGY_NAME
    cfg = s99.CONFIG
    assert cfg.name == s99.NAME
    assert cfg.session_start_hour is None and cfg.session_end_hour is None
    assert cfg.max_concurrent_positions == 1
    assert cfg.cooldown_s >= 300
