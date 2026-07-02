"""
test_backfill_merge.py
---------------------
Unit tests for the merge_frames helper in backfill_history_cache.

Mirrors the import style of test_s95_s96_s97.py: adds strategies/ to sys.path
so that `from backtest.backfill_history_cache import merge_frames` resolves.
"""
import os
import sys

import pandas as pd
import pytest

# Strategy modules use absolute imports rooted at `strategies/`, so put that dir
# on the path before importing — same pattern as test_s95_s96_s97.py.
_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies")
)
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from backtest.backfill_history_cache import merge_frames  # noqa: E402


def _df(times, closes):
    return pd.DataFrame({
        "time": pd.to_datetime(times, utc=True),
        "open": closes, "high": closes, "low": closes,
        "close": closes, "volume": [1] * len(times),
    })


def test_merge_appends_and_dedupes_keeping_fresh():
    old = _df(["2026-05-18 10:00", "2026-05-18 10:01"], [100.0, 101.0])
    new = _df(["2026-05-18 10:01", "2026-05-18 10:02"], [999.0, 102.0])
    out = merge_frames(old, new)
    assert list(out["close"]) == [100.0, 999.0, 102.0]
    assert out["time"].is_monotonic_increasing
    assert out["time"].is_unique


def test_merge_empty_fresh_is_noop():
    old = _df(["2026-05-18 10:00"], [100.0])
    out = merge_frames(old, old.iloc[0:0])
    assert len(out) == 1
