"""
test_bar_builder.py
-------------------
TDD tests for strategies.research.bar_builder.build_bars.

All tests inject synthetic tick DataFrames — NO real cache, NO DB, NO network.
The tick stream is a single mid/last price — columns: time (tz-aware UTC), price (float).
"""
from __future__ import annotations

import pandas as pd
import pytest

from strategies.research.bar_builder import build_bars, VALID_TFS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ticks(times: list[str], prices: list[float]) -> pd.DataFrame:
    """Build a minimal synthetic tick DataFrame with tz-aware UTC timestamps."""
    return pd.DataFrame({
        "time":  pd.to_datetime(times, utc=True),
        "price": prices,
    })


# ---------------------------------------------------------------------------
# 1. Known minute of ticks → exact OHLC and volume
# ---------------------------------------------------------------------------

def test_single_minute_ohlcv():
    """Five ticks in one minute → correct open/high/low/close/volume to the cent."""
    times = [
        "2026-01-05 09:00:01",
        "2026-01-05 09:00:15",
        "2026-01-05 09:00:30",
        "2026-01-05 09:00:45",
        "2026-01-05 09:00:59",
    ]
    prices = [2700.10, 2700.55, 2699.80, 2700.90, 2700.30]
    ticks = _ticks(times, prices)

    bars = build_bars(ticks, "1m")

    assert len(bars) == 1, f"Expected 1 bar, got {len(bars)}"
    row = bars.iloc[0]
    assert row["open"]   == pytest.approx(2700.10, abs=1e-5)
    assert row["high"]   == pytest.approx(2700.90, abs=1e-5)
    assert row["low"]    == pytest.approx(2699.80, abs=1e-5)
    assert row["close"]  == pytest.approx(2700.30, abs=1e-5)
    assert row["volume"] == 5


# ---------------------------------------------------------------------------
# 2. Spread column equals assumed_spread on every row
# ---------------------------------------------------------------------------

def test_spread_default_value():
    """Default assumed_spread=0.30 → every bar's spread column == 0.30."""
    times = [
        "2026-01-05 09:00:00",
        "2026-01-05 09:01:00",
        "2026-01-05 09:02:00",
    ]
    prices = [2700.00, 2700.10, 2700.20]
    ticks = _ticks(times, prices)

    bars = build_bars(ticks, "1m")

    assert "spread" in bars.columns
    # Use scalar comparison: all values must equal 0.30 within float tolerance
    assert ((bars["spread"] - 0.30).abs() < 1e-9).all(), (
        f"Expected all spread==0.30, got: {bars['spread'].tolist()}"
    )


def test_spread_custom_value():
    """Custom assumed_spread=0.25 → every bar's spread column == 0.25."""
    times = [
        "2026-01-05 09:00:00",
        "2026-01-05 09:01:00",
        "2026-01-05 09:02:00",
    ]
    prices = [2700.00, 2700.10, 2700.20]
    ticks = _ticks(times, prices)

    bars = build_bars(ticks, "1m", assumed_spread=0.25)

    assert "spread" in bars.columns
    # Use scalar comparison: all values must equal 0.25 within float tolerance
    assert ((bars["spread"] - 0.25).abs() < 1e-9).all(), (
        f"Expected all spread==0.25, got: {bars['spread'].tolist()}"
    )


# ---------------------------------------------------------------------------
# 3. Dedup: exact (time, price) duplicates
# ---------------------------------------------------------------------------

def test_dedup_true_removes_exact_duplicates():
    """With dedup=True, exact (time, price) duplicates are removed before aggregation."""
    # 3 unique ticks + 2 duplicates of the first
    times = [
        "2026-01-05 09:00:10",
        "2026-01-05 09:00:10",  # dup
        "2026-01-05 09:00:10",  # dup
        "2026-01-05 09:00:30",
        "2026-01-05 09:00:50",
    ]
    prices = [2700.00, 2700.00, 2700.00, 2700.10, 2700.20]
    ticks = _ticks(times, prices)

    bars_dedup = build_bars(ticks, "1m", dedup=True)
    bars_raw   = build_bars(ticks, "1m", dedup=False)

    # dedup=True: 3 unique (time,price) rows
    assert bars_dedup.iloc[0]["volume"] == 3, (
        f"dedup=True: expected volume=3, got {bars_dedup.iloc[0]['volume']}"
    )
    # dedup=False: all 5 rows counted
    assert bars_raw.iloc[0]["volume"] == 5, (
        f"dedup=False: expected volume=5, got {bars_raw.iloc[0]['volume']}"
    )


def test_dedup_same_time_different_price_not_dropped():
    """Two ticks with same time but DIFFERENT price are NOT deduplicated."""
    times  = ["2026-01-05 09:00:10", "2026-01-05 09:00:10"]
    prices = [2700.00, 2700.50]   # different prices → not exact dups
    ticks  = _ticks(times, prices)

    bars = build_bars(ticks, "1m", dedup=True)

    assert bars.iloc[0]["volume"] == 2, (
        f"Expected volume=2 (different prices kept), got {bars.iloc[0]['volume']}"
    )


# ---------------------------------------------------------------------------
# 4. Multiple timeframes → expected number of bars
# ---------------------------------------------------------------------------

def test_ten_minutes_yields_ten_1m_bars():
    """10 minutes of data (1 tick per minute on the minute) → exactly 10 1m bars."""
    times  = [f"2026-01-05 09:{m:02d}:00" for m in range(10)]
    prices = [2700.00 + m * 0.01 for m in range(10)]
    ticks  = _ticks(times, prices)

    bars = build_bars(ticks, "1m")

    assert len(bars) == 10, f"Expected 10 bars, got {len(bars)}"


def test_ten_minutes_yields_two_5m_bars():
    """10 minutes of data → exactly 2 5m bars."""
    times  = [f"2026-01-05 09:{m:02d}:00" for m in range(10)]
    prices = [2700.00 + m * 0.01 for m in range(10)]
    ticks  = _ticks(times, prices)

    bars = build_bars(ticks, "5m")

    assert len(bars) == 2, f"Expected 2 bars, got {len(bars)}"


def test_sixty_minutes_yields_one_1h_bar():
    """60 minutes of ticks → exactly 1 1h bar."""
    times  = [f"2026-01-05 09:{m:02d}:00" for m in range(60)]
    prices = [2700.00 + m * 0.01 for m in range(60)]
    ticks  = _ticks(times, prices)

    bars = build_bars(ticks, "1h")

    assert len(bars) == 1, f"Expected 1 1h bar, got {len(bars)}"


def test_15m_timeframe():
    """30 minutes of ticks → exactly 2 15m bars."""
    times  = [f"2026-01-05 09:{m:02d}:00" for m in range(30)]
    prices = [2700.00 + m * 0.01 for m in range(30)]
    ticks  = _ticks(times, prices)

    bars = build_bars(ticks, "15m")

    assert len(bars) == 2, f"Expected 2 bars, got {len(bars)}"


# ---------------------------------------------------------------------------
# 5. Empty input → empty DataFrame with exactly 7 required columns
# ---------------------------------------------------------------------------

def test_empty_input_returns_empty_df():
    """Empty ticks DataFrame → empty result with the 7 exact columns."""
    ticks = pd.DataFrame({"time": pd.Series(dtype="datetime64[ns, UTC]"),
                          "price": pd.Series(dtype=float)})

    bars = build_bars(ticks, "1m")

    assert bars.empty, "Expected an empty DataFrame"
    expected_cols = {"time", "open", "high", "low", "close", "volume", "spread"}
    assert set(bars.columns) == expected_cols, (
        f"Column mismatch: got {set(bars.columns)}, expected {expected_cols}"
    )


# ---------------------------------------------------------------------------
# 6. Unknown tf → ValueError
# ---------------------------------------------------------------------------

def test_unknown_tf_raises_value_error():
    """An unsupported timeframe string must raise ValueError."""
    times  = ["2026-01-05 09:00:00"]
    prices = [2700.00]
    ticks  = _ticks(times, prices)

    with pytest.raises(ValueError, match="Unknown timeframe"):
        build_bars(ticks, "3m")


def test_all_valid_tfs_accepted():
    """All VALID_TFS strings must be accepted without error."""
    times  = [f"2026-01-05 09:{m:02d}:00" for m in range(60)]
    prices = [2700.00 + m * 0.01 for m in range(60)]
    ticks  = _ticks(times, prices)

    for tf in VALID_TFS:
        bars = build_bars(ticks, tf)
        assert not bars.empty or bars.columns.tolist() != [], (
            f"tf={tf} returned unexpected result"
        )


# ---------------------------------------------------------------------------
# 7. Output time dtype is tz-naive datetime64[ns]
# ---------------------------------------------------------------------------

def test_output_time_is_tz_naive():
    """The output 'time' column must be tz-naive datetime64[ns] (UTC wall-clock)."""
    times  = ["2026-01-05 09:00:00", "2026-01-05 09:00:30"]
    prices = [2700.00, 2700.10]
    ticks  = _ticks(times, prices)

    bars = build_bars(ticks, "1m")

    assert bars["time"].dtype == "datetime64[ns]", (
        f"Expected datetime64[ns], got {bars['time'].dtype}"
    )
    # Must have no timezone info
    assert bars["time"].dt.tz is None, (
        f"Expected tz-naive, but tz={bars['time'].dt.tz}"
    )


# ---------------------------------------------------------------------------
# 8. Output columns order and types sanity
# ---------------------------------------------------------------------------

def test_output_columns_present():
    """All 7 required columns are present in the output."""
    times  = ["2026-01-05 09:00:00", "2026-01-05 09:00:30"]
    prices = [2700.00, 2700.10]
    ticks  = _ticks(times, prices)

    bars = build_bars(ticks, "1m")

    for col in ("time", "open", "high", "low", "close", "volume", "spread"):
        assert col in bars.columns, f"Missing column: {col}"


# ---------------------------------------------------------------------------
# 9. OHLC correctness across bar boundary
# ---------------------------------------------------------------------------

def test_two_minute_bars_ohlcv():
    """Two separate minutes → each bar has correct OHLC independently."""
    times = [
        "2026-01-05 09:00:05",
        "2026-01-05 09:00:30",
        "2026-01-05 09:01:10",
        "2026-01-05 09:01:50",
    ]
    prices = [
        2700.00, 2701.00,   # bar 0: open=2700.00, high=2701.00, low=2700.00, close=2701.00
        2701.50, 2699.50,   # bar 1: open=2701.50, high=2701.50, low=2699.50, close=2699.50
    ]
    ticks = _ticks(times, prices)

    bars = build_bars(ticks, "1m")

    assert len(bars) == 2
    b0, b1 = bars.iloc[0], bars.iloc[1]

    assert b0["open"]  == pytest.approx(2700.00, abs=1e-5)
    assert b0["high"]  == pytest.approx(2701.00, abs=1e-5)
    assert b0["low"]   == pytest.approx(2700.00, abs=1e-5)
    assert b0["close"] == pytest.approx(2701.00, abs=1e-5)
    assert b0["volume"] == 2

    assert b1["open"]  == pytest.approx(2701.50, abs=1e-5)
    assert b1["high"]  == pytest.approx(2701.50, abs=1e-5)
    assert b1["low"]   == pytest.approx(2699.50, abs=1e-5)
    assert b1["close"] == pytest.approx(2699.50, abs=1e-5)
    assert b1["volume"] == 2


# ---------------------------------------------------------------------------
# 10. Sparse bars: gaps between ticks → no phantom bars in between
# ---------------------------------------------------------------------------

def test_no_phantom_bars_in_gaps():
    """Ticks 30 minutes apart → only 2 bars for 1m, no empty bars in between."""
    times  = ["2026-01-05 09:00:00", "2026-01-05 09:30:00"]
    prices = [2700.00, 2701.00]
    ticks  = _ticks(times, prices)

    bars = build_bars(ticks, "1m")

    # Only 2 bars should exist; empty bars in the gap must be dropped
    assert len(bars) == 2, f"Expected 2 bars (no phantom bars), got {len(bars)}"
