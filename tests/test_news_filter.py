"""
test_news_filter.py
-------------------
TDD tests for strategies.research.news_filter.

All timestamps are tz-aware UTC.  No network, no scraping — only synthetic
event lists and the small fixture CSV under tests/fixtures/.

Coverage
--------
is_news_window:
  - ts exactly at event           -> True
  - 14 min before event           -> True   (inside default lookback=15)
  - 15 min before (boundary)      -> True   (inclusive boundary)
  - 16 min before                 -> False  (just outside)
  - 15 min after (boundary)       -> True   (inclusive boundary)
  - 16 min after                  -> False  (just outside)
  - far away from all events      -> False
  - empty events list             -> always False
  - multiple events; ts near 2nd  -> True
  - custom lookback / lookahead   -> respected correctly
  - tz-naive ts                   -> ValueError

load_forexfactory_csv:
  - returns only High-impact USD rows as tz-aware UTC pd.Timestamp
  - excludes Low-impact USD row
  - excludes non-USD High-impact row (EUR)
  - excludes non-USD Medium-impact non-high row (GBP)
  - returned timestamps are tz-aware UTC
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest

from strategies.research.news_filter import is_news_window, load_forexfactory_csv

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "forexfactory_sample.csv"


def _utc(ts_str: str) -> pd.Timestamp:
    """Build a tz-aware UTC pd.Timestamp from an ISO-8601 string."""
    return pd.Timestamp(ts_str, tz="UTC")


def _events(*ts_strings: str) -> list[pd.Timestamp]:
    return [_utc(s) for s in ts_strings]


# ---------------------------------------------------------------------------
# Anchor event used across multiple tests
# ---------------------------------------------------------------------------
_EVENT_TIME = "2026-02-01 13:30:00"  # e.g. NFP release


# ===========================================================================
# is_news_window — boundary and basic cases
# ===========================================================================

class TestIsNewsWindowBoundaries:
    """Verify inclusive-boundary semantics for default lookback=15, lookahead=15."""

    _events = _events(_EVENT_TIME)

    def test_exactly_at_event(self):
        assert is_news_window(_utc(_EVENT_TIME), self._events) is True

    def test_14_min_before(self):
        ts = _utc(_EVENT_TIME) - pd.Timedelta(minutes=14)
        assert is_news_window(ts, self._events) is True

    def test_15_min_before_boundary_inclusive(self):
        ts = _utc(_EVENT_TIME) - pd.Timedelta(minutes=15)
        assert is_news_window(ts, self._events) is True

    def test_16_min_before_outside(self):
        ts = _utc(_EVENT_TIME) - pd.Timedelta(minutes=16)
        assert is_news_window(ts, self._events) is False

    def test_15_min_after_boundary_inclusive(self):
        ts = _utc(_EVENT_TIME) + pd.Timedelta(minutes=15)
        assert is_news_window(ts, self._events) is True

    def test_16_min_after_outside(self):
        ts = _utc(_EVENT_TIME) + pd.Timedelta(minutes=16)
        assert is_news_window(ts, self._events) is False

    def test_far_away(self):
        ts = _utc(_EVENT_TIME) + pd.Timedelta(hours=4)
        assert is_news_window(ts, self._events) is False


# ===========================================================================
# is_news_window — empty events
# ===========================================================================

class TestIsNewsWindowEmptyEvents:
    def test_empty_list_always_false(self):
        ts = _utc("2026-02-01 13:30:00")
        assert is_news_window(ts, []) is False

    def test_empty_dataframe_always_false(self):
        """Also accept a DataFrame with a 'time' column (as documented)."""
        df = pd.DataFrame({"time": pd.to_datetime([], utc=True)})
        ts = _utc("2026-02-01 13:30:00")
        assert is_news_window(ts, df) is False


# ===========================================================================
# is_news_window — multiple events
# ===========================================================================

class TestIsNewsWindowMultipleEvents:
    """ts is not near the first event, but IS near the second."""

    _events = _events("2026-02-01 08:00:00", "2026-02-01 13:30:00")

    def test_near_second_event_is_true(self):
        ts = _utc("2026-02-01 13:20:00")   # 10 min before second event
        assert is_news_window(ts, self._events) is True

    def test_between_both_events_is_false(self):
        ts = _utc("2026-02-01 11:00:00")   # >2h after first, >2.5h before second
        assert is_news_window(ts, self._events) is False


# ===========================================================================
# is_news_window — custom lookback / lookahead
# ===========================================================================

class TestIsNewsWindowCustomWindow:
    _events = _events(_EVENT_TIME)

    def test_custom_lookback_30_includes_25_min_before(self):
        ts = _utc(_EVENT_TIME) - pd.Timedelta(minutes=25)
        assert is_news_window(ts, self._events, lookback_min=30, lookahead_min=15) is True

    def test_custom_lookback_10_excludes_15_min_before(self):
        ts = _utc(_EVENT_TIME) - pd.Timedelta(minutes=15)
        assert is_news_window(ts, self._events, lookback_min=10, lookahead_min=15) is False

    def test_custom_lookahead_5_excludes_10_min_after(self):
        ts = _utc(_EVENT_TIME) + pd.Timedelta(minutes=10)
        assert is_news_window(ts, self._events, lookback_min=15, lookahead_min=5) is False

    def test_custom_lookahead_5_includes_5_min_after(self):
        ts = _utc(_EVENT_TIME) + pd.Timedelta(minutes=5)
        assert is_news_window(ts, self._events, lookback_min=15, lookahead_min=5) is True

    def test_zero_window_only_exact_match(self):
        """lookback=0, lookahead=0 → only exact match returns True."""
        exact = _utc(_EVENT_TIME)
        one_sec_off = _utc(_EVENT_TIME) + pd.Timedelta(seconds=1)
        assert is_news_window(exact, self._events, lookback_min=0, lookahead_min=0) is True
        assert is_news_window(one_sec_off, self._events, lookback_min=0, lookahead_min=0) is False


# ===========================================================================
# is_news_window — tz-naive ts raises ValueError
# ===========================================================================

class TestIsNewsWindowTzNaive:
    _events = _events(_EVENT_TIME)

    def test_tz_naive_raises_value_error(self):
        naive_ts = pd.Timestamp("2026-02-01 13:30:00")  # no tz
        assert naive_ts.tzinfo is None, "Sanity: should be tz-naive"
        with pytest.raises(ValueError, match="tz-aware"):
            is_news_window(naive_ts, self._events)

    def test_tz_aware_does_not_raise(self):
        aware_ts = _utc("2026-02-01 13:30:00")
        # Should not raise
        is_news_window(aware_ts, self._events)


# ===========================================================================
# load_forexfactory_csv
# ===========================================================================

class TestLoadForexFactoryCsv:
    """Verify that load_forexfactory_csv filters correctly on impact and currency."""

    def test_fixture_file_exists(self):
        assert _FIXTURE_PATH.exists(), f"Fixture not found: {_FIXTURE_PATH}"

    def test_returns_only_high_impact_usd(self):
        """The sample has 2 High USD rows; the rest must be excluded."""
        events = load_forexfactory_csv(str(_FIXTURE_PATH))
        assert len(events) == 2, (
            f"Expected 2 High-impact USD events, got {len(events)}: {events}"
        )

    def test_returned_timestamps_are_tz_aware_utc(self):
        events = load_forexfactory_csv(str(_FIXTURE_PATH))
        for ev in events:
            assert isinstance(ev, pd.Timestamp), f"Not a Timestamp: {type(ev)}"
            assert ev.tzinfo is not None, f"tz-naive Timestamp returned: {ev}"
            # Should be UTC
            assert str(ev.tzinfo) in ("UTC", "pytz.UTC", "tzutc()") or \
                   ev.tz == "UTC" or \
                   "UTC" in str(ev.tzinfo), f"Expected UTC, got tzinfo={ev.tzinfo}"

    def test_nfp_row_present(self):
        events = load_forexfactory_csv(str(_FIXTURE_PATH))
        nfp_ts = _utc("2026-01-10 13:30:00")
        assert nfp_ts in events, f"NFP timestamp not found in {events}"

    def test_cpi_row_present(self):
        events = load_forexfactory_csv(str(_FIXTURE_PATH))
        cpi_ts = _utc("2026-01-14 13:30:00")
        assert cpi_ts in events, f"CPI timestamp not found in {events}"

    def test_low_impact_usd_excluded(self):
        events = load_forexfactory_csv(str(_FIXTURE_PATH))
        low_ts = _utc("2026-01-15 10:00:00")
        assert low_ts not in events, "Low-impact USD event should be excluded"

    def test_eur_high_impact_excluded(self):
        events = load_forexfactory_csv(str(_FIXTURE_PATH))
        eur_ts = _utc("2026-01-16 12:45:00")
        assert eur_ts not in events, "EUR High-impact event should be excluded"

    def test_gbp_medium_excluded(self):
        events = load_forexfactory_csv(str(_FIXTURE_PATH))
        gbp_ts = _utc("2026-01-21 14:00:00")
        assert gbp_ts not in events, "GBP Medium-impact event should be excluded"

    def test_custom_impact_filter(self):
        """When impacts=('Low',) is requested, we get only the Low USD row."""
        events = load_forexfactory_csv(str(_FIXTURE_PATH), impacts=("Low",))
        assert len(events) == 1
        assert _utc("2026-01-15 10:00:00") in events

    def test_custom_currency_filter(self):
        """When currencies=('EUR',) is requested, we get only the EUR High row."""
        events = load_forexfactory_csv(
            str(_FIXTURE_PATH), impacts=("High",), currencies=("EUR",)
        )
        assert len(events) == 1
        assert _utc("2026-01-16 12:45:00") in events

    def test_dataframe_with_time_column_accepted_by_is_news_window(self):
        """load_forexfactory_csv result can be wrapped in a DataFrame and passed
        to is_news_window via the DataFrame path (time column)."""
        events = load_forexfactory_csv(str(_FIXTURE_PATH))
        df = pd.DataFrame({"time": events})
        ts = _utc("2026-01-10 13:20:00")   # 10 min before NFP
        assert is_news_window(ts, df) is True
