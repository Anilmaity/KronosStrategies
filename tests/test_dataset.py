"""Tests for the IS/OOS split helpers — the leak-critical boundary."""
import datetime
import os

import pandas as pd

from strategies.research import dataset as ds


def _bars(times):
    n = len(times)
    return pd.DataFrame({
        "time": pd.to_datetime(times),
        "open": [1.0] * n, "high": [1.0] * n, "low": [1.0] * n,
        "close": [1.0] * n, "volume": [1.0] * n, "spread": [0.30] * n,
    })


def test_slice_is_excludes_oos_window():
    bars = _bars([
        "2025-01-01 08:00:00",     # IS
        "2025-06-15 13:00:00",     # IS
        ds.IS_END,                 # IS boundary (inclusive)
        ds.OOS_START,              # OOS — must be excluded
        "2026-04-01 09:00:00",     # OOS — must be excluded
    ])
    out = ds.slice_is(bars)
    assert len(out) == 3
    assert out["time"].max() <= ds.IS_END
    assert (out["time"] >= ds.IS_START).all()


def test_slice_oos_excludes_is_window():
    bars = _bars([
        "2025-12-31 08:00:00",     # IS — excluded
        ds.IS_END,                 # IS — excluded
        ds.OOS_START,              # OOS boundary (inclusive)
        "2026-04-01 09:00:00",     # OOS
        ds.OOS_END,                # OOS boundary (inclusive)
    ])
    out = ds.slice_oos(bars)
    assert len(out) == 3
    assert (out["time"] >= ds.OOS_START).all()
    assert (out["time"] <= ds.OOS_END).all()


def test_is_and_oos_are_disjoint_and_ordered():
    assert ds.IS_START < ds.IS_END < ds.OOS_START < ds.OOS_END
    # no overlap
    assert ds.IS_END < ds.OOS_START


def test_day_files_in_range_filters_by_label_date(tmp_path):
    sym_dir = tmp_path / "XAU_USD"
    sym_dir.mkdir(parents=True)
    # D_M_YYYY.json
    for name in ["1_1_2025.json", "15_6_2025.json", "20_2_2026.json", "1_5_2026.json"]:
        (sym_dir / name).write_text("[]")
    got = ds.day_files_in_range(
        "XAU_USD", datetime.date(2025, 1, 1), datetime.date(2026, 2, 19),
        cache_dir=str(tmp_path),
    )
    names = sorted(os.path.basename(p) for p in got)
    assert names == ["15_6_2025.json", "1_1_2025.json"]  # 20_2_2026 and 1_5_2026 excluded
