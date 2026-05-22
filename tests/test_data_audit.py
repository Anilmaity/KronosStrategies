"""
test_data_audit.py
------------------
TDD tests for strategies.research.data_audit.audit_ticks.

All tests inject synthetic tick DataFrames — NO real cache, NO DB, NO network.
"""
from __future__ import annotations

import pandas as pd
import pytest

from strategies.research.data_audit import audit_ticks


def _ticks(times: list[str], prices: list[float] | None = None) -> pd.DataFrame:
    """Build a minimal synthetic tick DataFrame with tz-aware UTC timestamps."""
    if prices is None:
        prices = [2000.0] * len(times)
    return pd.DataFrame({
        "time": pd.to_datetime(times, utc=True),
        "price": prices,
    })


# ---------------------------------------------------------------------------
# 1. Required keys are always present
# ---------------------------------------------------------------------------

def test_required_keys_present():
    """The returned dict must contain all 5 required keys."""
    df = _ticks(["2026-01-05 08:00:00"])
    result = audit_ticks("XAU_USD", "2026-01-05", "2026-01-05 23:59:59", ticks=df)
    required = {"rows", "first_tick", "last_tick", "duplicate_count", "gaps_minutes"}
    assert required.issubset(result.keys()), (
        f"Missing keys: {required - result.keys()}"
    )


# ---------------------------------------------------------------------------
# 2. Empty window (no ticks in range)
# ---------------------------------------------------------------------------

def test_empty_window():
    """Window selects nothing → rows=0, first/last=None, gaps/dups=0."""
    df = _ticks(["2026-01-05 08:00:00"])
    result = audit_ticks(
        "XAU_USD",
        "2026-01-10 00:00:00",
        "2026-01-10 23:59:59",
        ticks=df,
    )
    assert result["rows"] == 0
    assert result["first_tick"] is None
    assert result["last_tick"] is None
    assert result["gaps_minutes"] == 0.0
    assert result["duplicate_count"] == 0


# ---------------------------------------------------------------------------
# 3. Clean dense series inside a session block → gaps_minutes ≈ 0
# ---------------------------------------------------------------------------

def test_clean_london_session_no_gaps():
    """Dense ticks every 10s inside London 07:00–11:00 → zero qualifying gaps."""
    times = pd.date_range(
        "2026-01-05 07:00:00", periods=1440, freq="10s", tz="UTC"
    ).strftime("%Y-%m-%d %H:%M:%S").tolist()
    df = _ticks(times)
    result = audit_ticks("XAU_USD", "2026-01-05 00:00:00", "2026-01-05 23:59:59", ticks=df)
    assert result["gaps_minutes"] == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# 4. Injected 5-minute gap inside London block → gaps_minutes ≈ 5.0
# ---------------------------------------------------------------------------

def test_five_minute_gap_inside_london():
    """A 5-min gap wholly inside London session on a weekday counts in full."""
    # 07:00 → 08:00 (every 10s), then jump to 08:05, then continue to 09:00
    before = pd.date_range("2026-01-05 07:00:00", "2026-01-05 08:00:00",
                           freq="10s", tz="UTC")
    after  = pd.date_range("2026-01-05 08:05:00", "2026-01-05 09:00:00",
                           freq="10s", tz="UTC")
    times = list(before.strftime("%Y-%m-%d %H:%M:%S")) + \
            list(after.strftime("%Y-%m-%d %H:%M:%S"))
    df = _ticks(times)
    result = audit_ticks(
        "XAU_USD", "2026-01-05 00:00:00", "2026-01-05 23:59:59", ticks=df
    )
    # Gap = 08:00 → 08:05 = 5 minutes; both endpoints in London 07–11 block
    assert result["gaps_minutes"] == pytest.approx(5.0, abs=0.02)


# ---------------------------------------------------------------------------
# 5. Gap straddling two session blocks / overnight → NOT counted
# ---------------------------------------------------------------------------

def test_gap_straddling_sessions_not_counted():
    """A gap that bridges the end of London (11:00) and start of NY (12:00)
    has its endpoints in different session blocks → must NOT be counted."""
    # One tick just before London close, one just after NY open — straddling gap
    times = [
        "2026-01-05 10:55:00",  # London block (07–11)
        "2026-01-05 12:05:00",  # NY block (12–16) — different block
    ]
    df = _ticks(times)
    result = audit_ticks(
        "XAU_USD", "2026-01-05 00:00:00", "2026-01-05 23:59:59", ticks=df
    )
    assert result["gaps_minutes"] == pytest.approx(0.0, abs=1e-6)


def test_overnight_gap_not_counted():
    """A gap spanning midnight (one tick Mon night, one Tue morning) is NOT counted."""
    times = [
        "2026-01-05 15:55:00",  # NY block Mon
        "2026-01-06 07:05:00",  # London block Tue — different calendar date
    ]
    df = _ticks(times)
    result = audit_ticks(
        "XAU_USD", "2026-01-05 00:00:00", "2026-01-06 23:59:59", ticks=df
    )
    assert result["gaps_minutes"] == pytest.approx(0.0, abs=1e-6)


def test_gap_between_ny_sessions_different_days_not_counted():
    """A gap from one NY session day to the next is NOT counted (different date)."""
    times = [
        "2026-01-05 15:50:00",  # NY Mon
        "2026-01-06 12:10:00",  # NY Tue — different date
    ]
    df = _ticks(times)
    result = audit_ticks(
        "XAU_USD", "2026-01-05 00:00:00", "2026-01-06 23:59:59", ticks=df
    )
    assert result["gaps_minutes"] == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# 6. Exact-timestamp duplicates
# ---------------------------------------------------------------------------

def test_duplicate_count():
    """Three ticks with identical timestamps: duplicate_count = 2."""
    times = [
        "2026-01-05 08:00:00",
        "2026-01-05 08:00:00",  # dup 1
        "2026-01-05 08:00:00",  # dup 2
        "2026-01-05 08:01:00",
    ]
    df = _ticks(times)
    result = audit_ticks(
        "XAU_USD", "2026-01-05 00:00:00", "2026-01-05 23:59:59", ticks=df
    )
    # 4 rows - 2 unique timestamps → 2 duplicates
    assert result["duplicate_count"] == 2
    assert result["rows"] == 4


# ---------------------------------------------------------------------------
# 7. Gap on a weekend day → NOT counted
# ---------------------------------------------------------------------------

def test_gap_on_weekend_not_counted():
    """Saturday ticks in the London window are not session hours → not counted."""
    # 2026-01-03 is a Saturday
    times = [
        "2026-01-03 08:00:00",
        "2026-01-03 08:10:00",  # 10-min gap — weekend, not counted
    ]
    df = _ticks(times)
    result = audit_ticks(
        "XAU_USD", "2026-01-03 00:00:00", "2026-01-03 23:59:59", ticks=df
    )
    assert result["gaps_minutes"] == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# 8. rows, first_tick, last_tick sanity
# ---------------------------------------------------------------------------

def test_rows_and_boundaries():
    """rows equals len of window; first_tick/last_tick are correct."""
    times = [
        "2026-01-05 07:00:00",
        "2026-01-05 08:00:00",
        "2026-01-05 09:00:00",
    ]
    df = _ticks(times)
    result = audit_ticks(
        "XAU_USD", "2026-01-05 00:00:00", "2026-01-05 23:59:59", ticks=df
    )
    assert result["rows"] == 3
    assert result["first_tick"] == pd.Timestamp("2026-01-05 07:00:00", tz="UTC")
    assert result["last_tick"]  == pd.Timestamp("2026-01-05 09:00:00", tz="UTC")


# ---------------------------------------------------------------------------
# 9. gaps_by_month extra key is present
# ---------------------------------------------------------------------------

def test_gaps_by_month_key_present():
    """Extra key gaps_by_month must be present in the returned dict."""
    df = _ticks(["2026-01-05 08:00:00"])
    result = audit_ticks(
        "XAU_USD", "2026-01-05", "2026-01-05 23:59:59", ticks=df
    )
    assert "gaps_by_month" in result, "gaps_by_month key missing from result"


def test_gap_also_captured_in_function_signature_without_symbol():
    """audit_ticks can be called with start,end positional when symbol omitted?
    Actually spec requires symbol — just test call with all positional args."""
    df = _ticks(["2026-01-05 08:00:00"])
    result = audit_ticks("XAU_USD", "2026-01-05 00:00:00", "2026-01-06 00:00:00", ticks=df)
    assert result["rows"] == 1
