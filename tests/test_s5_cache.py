"""Phase 1 of the 5s-backtest-fidelity spec (2026-08-12): the S5 data cache.

Pure-logic tests only — no network. fetch_s5 takes an injected `getter` so the
paging loop is exercised against synthetic OANDA payloads.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies"))
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from backtest import s5_cache  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

def _candle(ts: str, mid: float, bid: float, ask: float,
            complete: bool = True, vol: int = 7) -> dict:
    """Minimal OANDA S5 candle payload with price=MBA."""
    return {
        "time": ts,
        "complete": complete,
        "volume": vol,
        "mid": {"o": f"{mid:.3f}", "h": f"{mid + 0.2:.3f}",
                "l": f"{mid - 0.2:.3f}", "c": f"{mid + 0.1:.3f}"},
        "bid": {"o": f"{bid:.3f}", "h": f"{bid:.3f}",
                "l": f"{bid:.3f}", "c": f"{bid:.3f}"},
        "ask": {"o": f"{ask:.3f}", "h": f"{ask:.3f}",
                "l": f"{ask:.3f}", "c": f"{ask:.3f}"},
    }


def _frame(times: list[str]) -> pd.DataFrame:
    """S5 frame with the given UTC timestamps and filler prices."""
    n = len(times)
    return pd.DataFrame({
        "time": pd.to_datetime(times, utc=True),
        "o": [4400.0] * n, "h": [4400.5] * n,
        "l": [4399.5] * n, "c": [4400.1] * n,
        "bid_c": [4399.7] * n, "ask_c": [4400.5] * n,
        "volume": [5.0] * n,
    })


# ── parsing ───────────────────────────────────────────────────────────────────

def test_rows_from_candles_extracts_mid_and_both_quote_sides():
    batch = [_candle("2026-07-06T10:00:00.000000000Z", 4400.0, 4399.62, 4400.38)]

    rows = s5_cache.rows_from_candles(batch)

    assert len(rows) == 1
    row = rows[0]
    assert row["time"] == "2026-07-06T10:00:00.000000000Z"
    assert row["o"] == pytest.approx(4400.0)
    assert row["h"] == pytest.approx(4400.2)
    assert row["l"] == pytest.approx(4399.8)
    assert row["c"] == pytest.approx(4400.1)
    # Both quote sides are kept so spread is measured, not assumed.
    assert row["bid_c"] == pytest.approx(4399.62)
    assert row["ask_c"] == pytest.approx(4400.38)
    assert row["volume"] == 7


def test_rows_from_candles_skips_incomplete_candles():
    batch = [
        _candle("2026-07-06T10:00:00.000000000Z", 4400.0, 4399.6, 4400.4),
        _candle("2026-07-06T10:00:05.000000000Z", 4401.0, 4400.6, 4401.4,
                complete=False),
    ]

    rows = s5_cache.rows_from_candles(batch)

    assert [r["time"] for r in rows] == ["2026-07-06T10:00:00.000000000Z"]


def test_frame_from_rows_is_utc_aware_and_float_typed():
    rows = s5_cache.rows_from_candles(
        [_candle("2026-07-06T10:00:00.000000000Z", 4400.0, 4399.6, 4400.4)])

    df = s5_cache.frame_from_rows(rows)

    assert str(df["time"].dt.tz) == "UTC"
    assert df["c"].dtype == float
    assert df["ask_c"].dtype == float


# ── partitioning ──────────────────────────────────────────────────────────────

_BASE = Path("cache_root")   # partition_path is pure — no filesystem needed


def test_partition_path_is_month_keyed():
    p = s5_cache.partition_path(
        "XAU_USD", datetime(2026, 7, 6, 10, 0, tzinfo=timezone.utc),
        base=_BASE)

    assert p.parent == _BASE / "XAU_USD"
    assert p.name == "2026-07.parquet"


def test_partition_path_separates_months():
    july = s5_cache.partition_path(
        "XAU_USD", datetime(2026, 7, 31, 23, 59, 55, tzinfo=timezone.utc),
        base=_BASE)
    august = s5_cache.partition_path(
        "XAU_USD", datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc),
        base=_BASE)

    assert july != august


# ── merging (idempotent re-fetch) ─────────────────────────────────────────────

def test_merge_partition_dedupes_keeping_fresh_row():
    existing = _frame(["2026-07-06T10:00:00Z", "2026-07-06T10:00:05Z"])
    fresh = _frame(["2026-07-06T10:00:05Z", "2026-07-06T10:00:10Z"])
    fresh.loc[0, "c"] = 4444.44          # the fresh copy of the shared bar

    merged = s5_cache.merge_partition(existing, fresh)

    assert len(merged) == 3
    shared = merged[merged["time"] == pd.Timestamp("2026-07-06T10:00:05Z")]
    assert shared["c"].iloc[0] == pytest.approx(4444.44)


def test_merge_partition_returns_sorted_time():
    existing = _frame(["2026-07-06T10:00:10Z"])
    fresh = _frame(["2026-07-06T10:00:00Z"])

    merged = s5_cache.merge_partition(existing, fresh)

    assert merged["time"].is_monotonic_increasing


# ── validation ────────────────────────────────────────────────────────────────

def test_validate_s5_accepts_clean_frame():
    df = _frame(["2026-07-08T10:00:00Z", "2026-07-08T10:00:05Z",
                 "2026-07-08T10:00:10Z"])

    assert s5_cache.validate_s5(df) == []


def test_validate_s5_flags_duplicate_timestamps():
    df = _frame(["2026-07-08T10:00:00Z", "2026-07-08T10:00:00Z"])

    problems = s5_cache.validate_s5(df)

    assert any("duplicate" in p.lower() for p in problems)


def test_validate_s5_flags_non_monotonic_time():
    df = _frame(["2026-07-08T10:00:05Z", "2026-07-08T10:00:00Z"])
    # bypass merge's sort: hand the validator an out-of-order frame
    problems = s5_cache.validate_s5(df, presorted=False)

    assert any("monotonic" in p.lower() for p in problems)


def test_validate_s5_flags_long_market_hours_gap():
    # Wednesday 10:00 -> 10:06 UTC is a 6-minute hole in active trading.
    df = _frame(["2026-07-08T10:00:00Z", "2026-07-08T10:06:00Z"])

    problems = s5_cache.validate_s5(df)

    assert any("gap" in p.lower() for p in problems)


def test_validate_s5_allows_short_thin_quote_gap():
    # 3 minutes is normal thin-quote behaviour at S5, not corruption.
    df = _frame(["2026-07-08T10:00:00Z", "2026-07-08T10:03:00Z"])

    assert s5_cache.validate_s5(df) == []


def test_validate_s5_allows_daily_break():
    # Wednesday 20:59:55 -> 22:00:00 UTC is the daily maintenance break.
    df = _frame(["2026-07-08T20:59:55Z", "2026-07-08T22:00:00Z"])

    assert s5_cache.validate_s5(df) == []


def test_validate_s5_allows_weekend_close():
    # Friday 20:59:55 -> Sunday 22:00:00 UTC is the weekend close.
    df = _frame(["2026-07-10T20:59:55Z", "2026-07-12T22:00:00Z"])

    assert s5_cache.validate_s5(df) == []


# ── paging ────────────────────────────────────────────────────────────────────

def test_fetch_s5_pages_forward_until_caught_up():
    """Two full pages then a short page ends the loop; params advance by 5s."""
    page1 = [_candle(f"2026-07-06T10:{m:02d}:{s:02d}.000000000Z",
                     4400.0, 4399.6, 4400.4)
             for m in range(0, 2) for s in range(0, 60, 5)]      # 24 candles
    page2 = [_candle(f"2026-07-06T10:{m:02d}:{s:02d}.000000000Z",
                     4401.0, 4400.6, 4401.4)
             for m in range(2, 4) for s in range(0, 60, 5)]      # 24 candles
    calls: list[dict] = []

    def fake_getter(url, params):
        calls.append(params)
        return {"candles": [page1, page2][len(calls) - 1]} if len(calls) <= 2 \
            else {"candles": []}

    df = s5_cache.fetch_s5(
        "XAU_USD",
        datetime(2026, 7, 6, 10, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 6, 10, 4, tzinfo=timezone.utc),
        getter=fake_getter, page_size=24,
    )

    assert len(df) == 48
    assert df["time"].is_monotonic_increasing
    assert calls[0]["granularity"] == "S5"
    assert calls[0]["price"] == "MBA"
    # second page starts one S5 step after the last candle of page 1
    assert calls[1]["from"].startswith("2026-07-06T10:01:60") is False
    assert calls[1]["from"] == "2026-07-06T10:02:00Z"


def test_fetch_s5_stops_at_end_timestamp():
    """Candles past `end` are dropped, so a month partition never bleeds over."""
    page = [_candle("2026-07-06T10:00:00.000000000Z", 4400.0, 4399.6, 4400.4),
            _candle("2026-07-06T10:00:05.000000000Z", 4400.5, 4400.1, 4400.9),
            _candle("2026-07-06T10:00:10.000000000Z", 4401.0, 4400.6, 4401.4)]

    def fake_getter(url, params):
        return {"candles": page}

    df = s5_cache.fetch_s5(
        "XAU_USD",
        datetime(2026, 7, 6, 10, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 6, 10, 0, 5, tzinfo=timezone.utc),
        getter=fake_getter, page_size=3,
    )

    assert list(df["time"].astype(str)) == [
        "2026-07-06 10:00:00+00:00", "2026-07-06 10:00:05+00:00"]


def test_fetch_s5_falls_back_to_split_passes_when_mba_rejected():
    """If OANDA rejects the combined price=MBA form, fetch M and BA separately."""
    def fake_getter(url, params):
        if params.get("price") == "MBA":
            raise RuntimeError("OANDA HTTP 400: invalid price component")
        ts = "2026-07-06T10:00:00.000000000Z"
        if params.get("price") == "M":
            return {"candles": [{"time": ts, "complete": True, "volume": 4,
                                 "mid": {"o": "4400.0", "h": "4400.2",
                                         "l": "4399.8", "c": "4400.1"}}]}
        return {"candles": [{"time": ts, "complete": True, "volume": 4,
                             "bid": {"c": "4399.62"},
                             "ask": {"c": "4400.38"}}]}

    df = s5_cache.fetch_s5(
        "XAU_USD",
        datetime(2026, 7, 6, 10, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 6, 10, 0, 5, tzinfo=timezone.utc),
        getter=fake_getter, page_size=1,
    )

    assert len(df) == 1
    assert df["c"].iloc[0] == pytest.approx(4400.1)
    assert df["bid_c"].iloc[0] == pytest.approx(4399.62)
    assert df["ask_c"].iloc[0] == pytest.approx(4400.38)


# ── partition write / read round-trip ─────────────────────────────────────────

def test_update_partition_writes_new_file_and_reports_rows():
    df = _frame(["2026-07-06T10:00:00Z", "2026-07-06T10:00:05Z"])

    with tempfile.TemporaryDirectory() as tmp:
        written = s5_cache.update_partition("XAU_USD", df, base=tmp)
        path = s5_cache.partition_path(
            "XAU_USD", datetime(2026, 7, 6, tzinfo=timezone.utc), base=tmp)

        assert written == 2
        assert path.exists()


def test_update_partition_is_idempotent_on_rewrite():
    df = _frame(["2026-07-06T10:00:00Z", "2026-07-06T10:00:05Z"])

    with tempfile.TemporaryDirectory() as tmp:
        s5_cache.update_partition("XAU_USD", df, base=tmp)
        s5_cache.update_partition("XAU_USD", df, base=tmp)

        out = s5_cache.load_s5(
            "XAU_USD",
            datetime(2026, 7, 6, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 7, 7, 0, 0, tzinfo=timezone.utc),
            base=tmp)

        assert len(out) == 2          # not 4


def test_update_partition_splits_a_month_spanning_frame():
    df = _frame(["2026-07-31T23:59:55Z", "2026-08-01T00:00:00Z"])

    with tempfile.TemporaryDirectory() as tmp:
        s5_cache.update_partition("XAU_USD", df, base=tmp)

        july = s5_cache.partition_path(
            "XAU_USD", datetime(2026, 7, 31, tzinfo=timezone.utc), base=tmp)
        august = s5_cache.partition_path(
            "XAU_USD", datetime(2026, 8, 1, tzinfo=timezone.utc), base=tmp)

        assert july.exists() and august.exists()
        assert len(pd.read_parquet(july)) == 1
        assert len(pd.read_parquet(august)) == 1


def test_load_s5_spans_month_boundary_and_clips_to_window():
    df = _frame(["2026-07-31T23:59:50Z", "2026-07-31T23:59:55Z",
                 "2026-08-01T00:00:00Z", "2026-08-01T00:00:05Z"])

    with tempfile.TemporaryDirectory() as tmp:
        s5_cache.update_partition("XAU_USD", df, base=tmp)

        out = s5_cache.load_s5(
            "XAU_USD",
            datetime(2026, 7, 31, 23, 59, 55, tzinfo=timezone.utc),
            datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc),
            base=tmp)

        assert list(out["time"].astype(str)) == [
            "2026-07-31 23:59:55+00:00", "2026-08-01 00:00:00+00:00"]


def test_load_s5_returns_empty_frame_when_nothing_cached():
    with tempfile.TemporaryDirectory() as tmp:
        out = s5_cache.load_s5(
            "XAU_USD",
            datetime(2026, 7, 6, tzinfo=timezone.utc),
            datetime(2026, 7, 7, tzinfo=timezone.utc),
            base=tmp)

        assert len(out) == 0
        assert list(out.columns) == s5_cache.COLUMNS


def test_cached_months_lists_only_existing_partitions():
    df = _frame(["2026-07-06T10:00:00Z"])

    with tempfile.TemporaryDirectory() as tmp:
        s5_cache.update_partition("XAU_USD", df, base=tmp)

        assert s5_cache.cached_months("XAU_USD", base=tmp) == ["2026-07"]


# ── month chunking (keeps a multi-year backfill memory-bounded) ───────────────

def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def test_month_windows_single_partial_month():
    assert s5_cache.month_windows(_utc(2026, 7, 6, 10), _utc(2026, 7, 20)) == [
        (_utc(2026, 7, 6, 10), _utc(2026, 7, 20))]


def test_month_windows_splits_across_months():
    assert s5_cache.month_windows(_utc(2026, 7, 6), _utc(2026, 8, 12)) == [
        (_utc(2026, 7, 6), _utc(2026, 7, 31, 23, 59, 55)),
        (_utc(2026, 8, 1), _utc(2026, 8, 12))]


def test_month_windows_spans_a_year_boundary():
    assert s5_cache.month_windows(_utc(2025, 12, 30), _utc(2026, 1, 2)) == [
        (_utc(2025, 12, 30), _utc(2025, 12, 31, 23, 59, 55)),
        (_utc(2026, 1, 1), _utc(2026, 1, 2))]


def test_month_windows_empty_when_end_precedes_start():
    assert s5_cache.month_windows(_utc(2026, 8, 1), _utc(2026, 7, 1)) == []


# ── streaming (peak memory) ───────────────────────────────────────────────────
# 2026-08-12 incident: fetching a whole month before writing held ~250 MB of
# Python dicts and thrashed the 2 GB production box into unreachability. The
# fetch must hand pages to a sink and never accumulate the window.

def test_fetch_s5_streaming_never_holds_more_than_one_page():
    """The sink receives each page; peak retained rows stay at page size."""
    pages = [
        [_candle(f"2026-07-06T10:0{i}:{s:02d}.000000000Z", 4400.0 + i,
                 4399.6 + i, 4400.4 + i) for s in range(0, 60, 5)]
        for i in range(4)
    ]
    seen_sizes: list[int] = []
    calls = {"n": 0}

    def fake_getter(url, params):
        i = calls["n"]
        calls["n"] += 1
        return {"candles": pages[i]} if i < len(pages) else {"candles": []}

    def sink(df):
        seen_sizes.append(len(df))

    total = s5_cache.stream_s5(
        "XAU_USD",
        datetime(2026, 7, 6, 10, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 6, 10, 4, tzinfo=timezone.utc),
        sink=sink, getter=fake_getter, page_size=12,
    )

    assert total == 48
    assert seen_sizes == [12, 12, 12, 12]     # one page at a time, never 48
    assert max(seen_sizes) <= 12


def test_stream_s5_clips_pages_to_the_window_end():
    page = [_candle("2026-07-06T10:00:00.000000000Z", 4400.0, 4399.6, 4400.4),
            _candle("2026-07-06T10:00:05.000000000Z", 4400.5, 4400.1, 4400.9),
            _candle("2026-07-06T10:00:10.000000000Z", 4401.0, 4400.6, 4401.4)]
    got: list[pd.DataFrame] = []

    def fake_getter(url, params):
        return {"candles": page}

    total = s5_cache.stream_s5(
        "XAU_USD",
        datetime(2026, 7, 6, 10, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 6, 10, 0, 5, tzinfo=timezone.utc),
        sink=got.append, getter=fake_getter, page_size=3,
    )

    assert total == 2
    assert list(got[0]["time"].astype(str)) == [
        "2026-07-06 10:00:00+00:00", "2026-07-06 10:00:05+00:00"]


def test_stream_s5_appends_each_page_to_its_partition():
    """End-to-end: streaming straight to disk keeps peak memory at one page."""
    pages = [
        [_candle(f"2026-07-06T10:0{i}:{s:02d}.000000000Z", 4400.0 + i,
                 4399.6 + i, 4400.4 + i) for s in range(0, 60, 5)]
        for i in range(3)
    ]
    calls = {"n": 0}

    def fake_getter(url, params):
        i = calls["n"]
        calls["n"] += 1
        return {"candles": pages[i]} if i < len(pages) else {"candles": []}

    with tempfile.TemporaryDirectory() as tmp:
        total = s5_cache.stream_s5(
            "XAU_USD",
            datetime(2026, 7, 6, 10, 0, tzinfo=timezone.utc),
            datetime(2026, 7, 6, 10, 3, tzinfo=timezone.utc),
            sink=s5_cache.partition_sink("XAU_USD", base=tmp),
            getter=fake_getter, page_size=12,
        )

        out = s5_cache.load_s5(
            "XAU_USD",
            datetime(2026, 7, 6, 10, 0, tzinfo=timezone.utc),
            datetime(2026, 7, 6, 10, 3, tzinfo=timezone.utc),
            base=tmp)

        assert total == 36
        assert len(out) == 36
        assert out["time"].is_monotonic_increasing


# ── memory guard ──────────────────────────────────────────────────────────────

def test_memory_is_sufficient_blocks_below_floor():
    """The production box had 227 MB available when the backfill wedged it."""
    assert s5_cache.memory_is_sufficient(available_mb=227, floor_mb=400) is False


def test_memory_is_sufficient_allows_with_headroom():
    assert s5_cache.memory_is_sufficient(available_mb=1200, floor_mb=400) is True


def test_memory_is_sufficient_allows_when_unknown():
    """Unknown availability (e.g. Windows) must not block a local run."""
    assert s5_cache.memory_is_sufficient(available_mb=None, floor_mb=400) is True


# ── resumability ──────────────────────────────────────────────────────────────

def test_window_is_cached_false_when_no_partition():
    with tempfile.TemporaryDirectory() as tmp:
        assert not s5_cache.window_is_cached(
            "XAU_USD", _utc(2026, 7, 1), _utc(2026, 7, 31, 23, 59, 55), base=tmp)


def test_window_is_cached_true_when_data_reaches_the_market_close():
    """A month ending on a Friday has no bars after 21:00 UTC — that is complete,
    not missing, so the closure grace must cover it."""
    df = _frame(["2026-07-31T20:59:55Z"])

    with tempfile.TemporaryDirectory() as tmp:
        s5_cache.update_partition("XAU_USD", df, base=tmp)

        assert s5_cache.window_is_cached(
            "XAU_USD", _utc(2026, 7, 1), _utc(2026, 7, 31, 23, 59, 55), base=tmp)


def test_window_is_cached_false_when_month_only_half_fetched():
    df = _frame(["2026-07-15T10:00:00Z"])

    with tempfile.TemporaryDirectory() as tmp:
        s5_cache.update_partition("XAU_USD", df, base=tmp)

        assert not s5_cache.window_is_cached(
            "XAU_USD", _utc(2026, 7, 1), _utc(2026, 7, 31, 23, 59, 55), base=tmp)


# ── coverage metric ───────────────────────────────────────────────────────────

def test_coverage_pct_reports_fraction_of_expected_slots():
    # 10:00:00, :05, :10 expected; :05 missing -> 2/3 present.
    df = _frame(["2026-07-08T10:00:00Z", "2026-07-08T10:00:10Z"])

    pct = s5_cache.coverage_pct(
        df,
        datetime(2026, 7, 8, 10, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 8, 10, 0, 15, tzinfo=timezone.utc),
    )

    assert pct == pytest.approx(2 / 3, abs=1e-6)
