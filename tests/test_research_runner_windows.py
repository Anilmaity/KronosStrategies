# tests/test_research_runner_windows.py
"""research_runner window/bar-freshness regression tests (2026-07-06 fix).

The OANDA feed (tsdb_reader._fetch_oanda_ohlc) returns only COMPLETE candles,
so the runner must NOT drop the last row of any frame: doing so hid the newest
closed bar until the next bar closed, making every live entry one full bar
stale (SESSION_BREAKOUT fills landed 5-15 min after the OR boundary touch).
"""
from __future__ import annotations

import os
import sys

import pandas as pd

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies")
)
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

import research_runner as rr  # noqa: E402


def _frame(freq: str, n: int) -> pd.DataFrame:
    times = pd.date_range("2026-06-05 00:00", periods=n, freq=freq)
    return pd.DataFrame({
        "time": times,
        "open": 2000.0, "high": 2001.0, "low": 1999.0, "close": 2000.5,
        "volume": 1.0,
    })


def test_build_windows_keeps_newest_closed_bar():
    c1m, c5m, c15m = _frame("1min", 120), _frame("5min", 200), _frame("15min", 150)
    w1m, w5m, w15m = rr._build_windows(c1m, c5m, c15m)
    # The newest CLOSED bar must survive into every strategy window.
    assert w1m["time"].iloc[-1] == c1m["time"].iloc[-1]
    assert w5m["time"].iloc[-1] == c5m["time"].iloc[-1]
    assert w15m["time"].iloc[-1] == c15m["time"].iloc[-1]
    # And the windows are tail-limited to the configured widths.
    assert len(w1m) == min(rr.WIN_1M, len(c1m))
    assert len(w5m) == min(rr.WIN_5M, len(c5m))
    assert len(w15m) == min(rr.WIN_15M, len(c15m))


def test_build_windows_tolerates_empty_higher_tfs():
    empty = pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    w1m, w5m, w15m = rr._build_windows(_frame("1min", 60), empty, empty)
    assert len(w1m) == min(rr.WIN_1M, 60)
    assert w5m.empty and w15m.empty


def test_is_new_1m_keys_off_newest_closed_bar():
    rr._last_1m_time = None
    c = _frame("1min", 10)
    assert rr._is_new_1m(c) is True          # first sighting
    assert rr._is_new_1m(c) is False         # unchanged newest bar -> no retrigger
    nxt = _frame("1min", 11)                 # one more closed bar arrives
    assert rr._is_new_1m(nxt) is True        # detected immediately, not one bar late
    assert rr._last_1m_time == nxt["time"].iloc[-1]


def test_is_new_1m_handles_empty_frame():
    rr._last_1m_time = None
    empty = pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    assert rr._is_new_1m(empty) is False
