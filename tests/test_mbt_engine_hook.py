"""Task 7 (Manager Backtest plan): run_sim progress_cb hook.

Golden parity: progress_cb=None must stay byte-identical; a callback gets a
monotone [0,1] series and its exceptions propagate (the cancellation path).
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies"))
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from backtest.manager_sim_engine import (  # noqa: E402
    SimConfig, load_frames, run_sim,
)

START = pd.Timestamp("2026-04-06", tz="UTC")
END = pd.Timestamp("2026-04-08 21:00", tz="UTC")
_SHALLOW = {"1d": 40, "4h": 60, "1h": 80, "15m": 50, "5m": 20, "1m": 20}


def _write_cache(cache_dir):
    """Drift + fast sine tape (mirrors test_manager_sim's synthetic cache)."""
    start = START - pd.Timedelta(days=30)
    idx = pd.date_range(start, END, freq="1min", tz="UTC")
    idx = idx[(idx.dayofweek < 5) & (idx.hour < 21)]
    t = np.arange(len(idx), dtype=float)
    px = 3300 + 0.005 * t + 3.0 * np.sin(2 * np.pi * t / 7.0)
    df1 = pd.DataFrame({"time": idx, "open": px, "high": px,
                        "low": px, "close": px, "volume": 10.0})
    df1.to_parquet(cache_dir / "is_XAU_USD_1m.parquet", index=False)
    g = df1.set_index("time")
    for tf, rule in [("5m", "5min"), ("15m", "15min"), ("1h", "1h"),
                     ("4h", "4h"), ("1d", "1D")]:
        r = g.resample(rule).agg({"open": "first", "high": "max",
                                  "low": "min", "close": "last",
                                  "volume": "sum"}).dropna().reset_index()
        r.to_parquet(cache_dir / f"is_XAU_USD_{tf}.parquet", index=False)


def _cfg():
    return SimConfig(start=START.to_pydatetime(), end=END.to_pydatetime(),
                     gated=False, slice_rows=_SHALLOW)


def test_progress_series_and_golden_parity(tmp_path):
    _write_cache(tmp_path)
    frames = load_frames(tmp_path, START, END)

    baseline = run_sim(frames, _cfg())

    seen: list[float] = []
    hooked = run_sim(frames, _cfg(), progress_cb=seen.append)

    assert seen, "callback was never invoked"
    assert seen == sorted(seen), "progress must be nondecreasing"
    assert seen[0] == 0.0 and seen[-1] == 1.0
    assert all(0.0 <= f <= 1.0 for f in seen)
    assert hooked.trades == baseline.trades
    assert hooked.kill_trips == baseline.kill_trips
    assert hooked.paused_pct == baseline.paused_pct


def test_callback_exception_propagates(tmp_path):
    _write_cache(tmp_path)
    frames = load_frames(tmp_path, START, END)

    class Abort(RuntimeError):
        pass

    def boom(_frac):
        raise Abort("cancelled")

    with pytest.raises(Abort):
        run_sim(frames, _cfg(), progress_cb=boom)
