"""Build 5s bars from the local 1-second tick cache (.history_data).

The cache stores one JSON file per day named D_M_YYYY.json, holding nested
lists of {time, price} at ~1-second resolution. That is finer than OANDA's S5
and matches live's 1-second position_manager loop, so it is the better source
for resolving intrabar order — where it exists (2025-01-01 .. 2026-05-19).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies"))
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from backtest import tick_s5  # noqa: E402


# ── filename parsing (D_M_YYYY, verified against the real cache) ──────────────

def test_parses_day_month_year_filenames():
    assert tick_s5.parse_tick_date("10_2_2026.json") == date(2026, 2, 10)


def test_parses_a_day_above_twelve():
    """31_1_2026 is unambiguous and proves the order is D_M, not M_D."""
    assert tick_s5.parse_tick_date("31_1_2026.json") == date(2026, 1, 31)


def test_returns_none_for_a_non_tick_filename():
    assert tick_s5.parse_tick_date("README.md") is None
    assert tick_s5.parse_tick_date("notes_2026.json") is None


def test_files_in_range_selects_inclusively_and_sorts():
    with tempfile.TemporaryDirectory() as tmp:
        for name in ["1_2_2026.json", "3_2_2026.json", "5_2_2026.json",
                     "10_1_2026.json"]:
            Path(tmp, name).write_text("[]")

        got = tick_s5.tick_files_in_range(tmp, date(2026, 2, 1),
                                          date(2026, 2, 3))

        assert [p.name for p in got] == ["1_2_2026.json", "3_2_2026.json"]


# ── resampling ────────────────────────────────────────────────────────────────

def _ticks(rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame({
        "time": pd.to_datetime([t for t, _ in rows], utc=True),
        "price": [p for _, p in rows],
    })


def test_resample_builds_one_bar_per_5s_bucket():
    ticks = _ticks([
        ("2026-02-10T00:00:05Z", 100.0),
        ("2026-02-10T00:00:06Z", 103.0),
        ("2026-02-10T00:00:07Z",  99.0),
        ("2026-02-10T00:00:08Z", 101.0),
        ("2026-02-10T00:00:10Z", 200.0),
    ])

    out = tick_s5.resample_s5(ticks)

    assert len(out) == 2
    first = out.iloc[0]
    assert first["o"] == pytest.approx(100.0)
    assert first["h"] == pytest.approx(103.0)
    assert first["l"] == pytest.approx(99.0)
    assert first["c"] == pytest.approx(101.0)


def test_resample_labels_buckets_on_their_left_edge():
    ticks = _ticks([("2026-02-10T00:00:07Z", 100.0)])

    out = tick_s5.resample_s5(ticks)

    assert out["time"].iloc[0] == pd.Timestamp("2026-02-10T00:00:05Z")


def test_resample_keeps_utc_tzawareness():
    """run_sim compares these against tz-aware M1 timestamps — naive would raise."""
    ticks = _ticks([("2026-02-10T00:00:05Z", 100.0)])

    out = tick_s5.resample_s5(ticks)

    assert str(out["time"].dt.tz) == "UTC"


def test_resample_drops_empty_buckets_rather_than_forward_filling():
    """A gap must be absent, not a fabricated flat bar — walk_exit would
    otherwise 'observe' a price that never traded."""
    ticks = _ticks([
        ("2026-02-10T00:00:05Z", 100.0),
        ("2026-02-10T00:01:05Z", 110.0),
    ])

    out = tick_s5.resample_s5(ticks)

    assert len(out) == 2


def test_resample_of_nothing_returns_the_right_columns():
    out = tick_s5.resample_s5(_ticks([]))

    assert len(out) == 0
    assert list(out.columns) == ["time", "o", "h", "l", "c", "volume"]


# ── day loading (nested JSON) ─────────────────────────────────────────────────

def test_loads_and_flattens_a_nested_tick_day():
    payload = [
        [{"time": "2026-02-10 00:00:05+00:00", "price": 100.0},
         {"time": "2026-02-10 00:00:06+00:00", "price": 101.0}],
        [{"time": "2026-02-10 00:00:10+00:00", "price": 102.0}],
    ]
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp, "10_2_2026.json")
        p.write_text(json.dumps(payload))

        out = tick_s5.load_tick_day(p)

        assert len(out) == 3
        assert out["price"].iloc[-1] == pytest.approx(102.0)
        assert str(out["time"].dt.tz) == "UTC"


def test_build_s5_streams_days_into_one_frame():
    def day(d: int, base: float):
        return [[{"time": f"2026-02-{d:02d} 00:00:05+00:00", "price": base},
                 {"time": f"2026-02-{d:02d} 00:00:06+00:00", "price": base + 1}]]

    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "10_2_2026.json").write_text(json.dumps(day(10, 100.0)))
        Path(tmp, "11_2_2026.json").write_text(json.dumps(day(11, 200.0)))

        out = tick_s5.build_s5(tmp, date(2026, 2, 10), date(2026, 2, 11))

        assert len(out) == 2
        assert out["time"].is_monotonic_increasing
        assert out["o"].tolist() == pytest.approx([100.0, 200.0])
