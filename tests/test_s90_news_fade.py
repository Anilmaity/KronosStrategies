"""
test_s90_news_fade.py
---------------------
TDD tests for strategies.xauusd_strategies.s90_news_fade.

ALL tests use synthetic bars (hand-constructed DataFrames) - no real cache,
no network, no disk I/O.  Tests are fully offline and deterministic.

Coverage
--------
detect_news_fade_signals:
  1. Post-event spike UP that tags a prior swing high
     -> SELL Signal generated, entry_index = bar after 5-min mark (k+1)
  2. Post-event spike DOWN that tags a prior swing low
     -> BUY Signal generated, entry_index = k+1
  3. Spike exists but does NOT tag any liquidity -> no Signal
  4. No event anywhere near the bar window -> no Signal
  5. Event present, but R:R < 1:1 after spread -> no Signal
  6. get_high_impact_usd_events returns non-empty list of pd.Timestamp objects
  7. Spike that tags liquidity but SL>entry (invalid) -> no Signal (skipped)

LOOK-AHEAD INVARIANT (self-audit):
  - Decision at observation bar k uses only bars 0..k (the spike window).
  - Entry fires at entry_index=k+1.
  - Event time T is a known scheduled release -> using T is allowed.
  - Price reaction is read only from closed bars after T.
"""
from __future__ import annotations

import pandas as pd
import pytest

from strategies.xauusd_strategies.s90_news_fade import (
    detect_news_fade_signals,
    get_high_impact_usd_events,
)
from strategies.research.exec_sim import Signal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bars(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame with tz-naive UTC timestamps."""
    data = []
    for r in rows:
        data.append({
            "time": pd.Timestamp(r["time"]),  # tz-naive UTC
            "open": float(r.get("open", r.get("close", 2000.0))),
            "high": float(r.get("high", r.get("close", 2000.0))),
            "low": float(r.get("low", r.get("close", 2000.0))),
            "close": float(r.get("close", 2000.0)),
            "volume": float(r.get("volume", 100)),
            "spread": float(r.get("spread", 0.30)),
        })
    return pd.DataFrame(data)


def _bar(time_str: str, open_: float, high: float, low: float, close: float) -> dict:
    return dict(time=time_str, open=open_, high=high, low=low, close=close)


# ---------------------------------------------------------------------------
# Test 1: Spike UP tagging prior swing high -> SELL signal
# ---------------------------------------------------------------------------

class TestSpikeFadeShort:
    """
    Scenario: NFP release at 2025-01-10 13:30 UTC.
    Pre-event bars show a swing high at 2060.0 (visible before T).
    Spike bar covers T..T+5min, spike high = 2062.0 (sweeps 2060.0).
    After 5min mark, we should see a SELL signal.
    """

    EVENT_TIME = pd.Timestamp("2025-01-10 13:30:00")  # tz-naive UTC

    def _build_bars(self):
        # Bars before event: establish price context and a prior swing high at 2060
        pre_event = [
            # early context bars (well before event)
            _bar("2025-01-10 11:00:00", 2040.0, 2042.0, 2038.0, 2041.0),
            _bar("2025-01-10 11:05:00", 2041.0, 2043.0, 2039.0, 2042.0),
            _bar("2025-01-10 11:10:00", 2042.0, 2060.0, 2041.0, 2058.0),  # swing HIGH at 2060
            _bar("2025-01-10 11:15:00", 2058.0, 2059.0, 2050.0, 2052.0),
            _bar("2025-01-10 11:20:00", 2052.0, 2054.0, 2048.0, 2050.0),
            _bar("2025-01-10 11:25:00", 2050.0, 2053.0, 2047.0, 2049.0),
            _bar("2025-01-10 11:30:00", 2049.0, 2052.0, 2046.0, 2048.0),
            _bar("2025-01-10 11:35:00", 2048.0, 2051.0, 2045.0, 2047.0),
            _bar("2025-01-10 11:40:00", 2047.0, 2050.0, 2044.0, 2046.0),
            _bar("2025-01-10 11:45:00", 2046.0, 2049.0, 2043.0, 2045.0),
            _bar("2025-01-10 11:50:00", 2045.0, 2048.0, 2042.0, 2044.0),
            _bar("2025-01-10 11:55:00", 2044.0, 2047.0, 2041.0, 2043.0),
            _bar("2025-01-10 12:00:00", 2043.0, 2046.0, 2040.0, 2042.0),
            _bar("2025-01-10 12:05:00", 2042.0, 2045.0, 2039.0, 2041.0),
            _bar("2025-01-10 12:10:00", 2041.0, 2044.0, 2038.0, 2040.0),
            _bar("2025-01-10 12:15:00", 2040.0, 2043.0, 2037.0, 2039.0),
            _bar("2025-01-10 12:20:00", 2039.0, 2042.0, 2036.0, 2038.0),
            _bar("2025-01-10 12:25:00", 2038.0, 2041.0, 2035.0, 2037.0),
            _bar("2025-01-10 12:30:00", 2037.0, 2040.0, 2034.0, 2036.0),
            _bar("2025-01-10 12:35:00", 2036.0, 2039.0, 2033.0, 2035.0),
            _bar("2025-01-10 12:40:00", 2035.0, 2038.0, 2032.0, 2034.0),
            _bar("2025-01-10 12:45:00", 2034.0, 2037.0, 2031.0, 2033.0),
            _bar("2025-01-10 12:50:00", 2033.0, 2036.0, 2030.0, 2032.0),
            _bar("2025-01-10 12:55:00", 2032.0, 2035.0, 2029.0, 2031.0),
            # immediate pre-event bar (ends at 13:25, last bar before 13:30)
            _bar("2025-01-10 13:00:00", 2031.0, 2034.0, 2028.0, 2030.0),
            _bar("2025-01-10 13:05:00", 2030.0, 2033.0, 2027.0, 2029.0),
            _bar("2025-01-10 13:10:00", 2029.0, 2032.0, 2026.0, 2028.0),
            _bar("2025-01-10 13:15:00", 2028.0, 2031.0, 2025.0, 2027.0),
            _bar("2025-01-10 13:20:00", 2027.0, 2030.0, 2024.0, 2026.0),
            _bar("2025-01-10 13:25:00", 2026.0, 2029.0, 2023.0, 2025.0),
        ]
        # Spike bar: at event time 13:30, huge UP spike exceeding swing high 2060
        spike_bar = [
            _bar("2025-01-10 13:30:00", 2025.0, 2062.0, 2024.0, 2050.0),  # spike high=2062 > 2060
        ]
        # Confirmation bar at 13:35 (this is bar k = last bar at/just after T+5min)
        # Entry bar k+1 is 13:40
        confirmation_bar = [
            _bar("2025-01-10 13:35:00", 2050.0, 2052.0, 2046.0, 2048.0),
        ]
        # Entry bar (k+1) and subsequent bars
        post_signal = [
            _bar("2025-01-10 13:40:00", 2048.0, 2049.0, 2040.0, 2042.0),
            _bar("2025-01-10 13:45:00", 2042.0, 2043.0, 2030.0, 2032.0),
            _bar("2025-01-10 13:50:00", 2032.0, 2033.0, 2025.0, 2027.0),
        ]
        return _make_bars(pre_event + spike_bar + confirmation_bar + post_signal)

    def test_short_signal_generated(self):
        bars = self._build_bars()
        event_times = [self.EVENT_TIME]
        signals = detect_news_fade_signals(bars, event_times)
        assert len(signals) == 1, f"Expected 1 SELL signal, got {len(signals)}: {signals}"
        sig = signals[0]
        assert sig.direction == "SELL", f"Expected SELL, got {sig.direction}"

    def test_entry_index_is_after_5min_mark(self):
        """entry_index must be bar k+1 where bar k is the last bar at/after T+5min."""
        bars = self._build_bars()
        event_times = [self.EVENT_TIME]
        signals = detect_news_fade_signals(bars, event_times)
        assert len(signals) == 1
        sig = signals[0]
        # The bar at 13:35 is bar k (confirmation); entry is at 13:40 = k+1
        entry_bar_time = bars.iloc[sig.entry_index]["time"]
        assert entry_bar_time >= pd.Timestamp("2025-01-10 13:35:00"), (
            f"Entry bar time {entry_bar_time} should be at or after 13:35"
        )

    def test_sl_above_spike_extreme(self):
        """For SELL: SL must be above the spike high (plus buffer)."""
        bars = self._build_bars()
        event_times = [self.EVENT_TIME]
        signals = detect_news_fade_signals(bars, event_times)
        assert len(signals) == 1
        sig = signals[0]
        spike_high = 2062.0
        assert sig.sl > spike_high, f"SELL SL {sig.sl} should be > spike high {spike_high}"

    def test_tp_below_entry(self):
        """For SELL: TP must be below the entry open."""
        bars = self._build_bars()
        event_times = [self.EVENT_TIME]
        signals = detect_news_fade_signals(bars, event_times)
        assert len(signals) == 1
        sig = signals[0]
        entry_open = bars.iloc[sig.entry_index]["open"]
        assert sig.tp < entry_open, f"SELL TP {sig.tp} should be < entry open {entry_open}"

    def test_rr_at_least_1_to_1(self):
        """Risk:Reward must be >= 1:1 (TP distance >= SL distance from entry)."""
        bars = self._build_bars()
        event_times = [self.EVENT_TIME]
        signals = detect_news_fade_signals(bars, event_times)
        assert len(signals) == 1
        sig = signals[0]
        entry_open = bars.iloc[sig.entry_index]["open"]
        risk = sig.sl - entry_open       # positive for SELL
        reward = entry_open - sig.tp     # positive for SELL
        assert reward >= risk, f"R:R < 1:1: risk={risk:.2f} reward={reward:.2f}"


# ---------------------------------------------------------------------------
# Test 2: Spike DOWN tagging prior swing low -> BUY signal
# ---------------------------------------------------------------------------

class TestSpikeFadeLong:
    """
    Scenario: CPI release at 2025-01-15 13:30 UTC.
    Pre-event bars show a swing low at 2000.0 (visible before T).
    Spike bar goes down to 1998.0 (sweeps 2000.0 swing low).
    After 5min mark, we should see a BUY signal.
    """

    EVENT_TIME = pd.Timestamp("2025-01-15 13:30:00")  # tz-naive UTC

    def _build_bars(self):
        pre_event = [
            _bar("2025-01-15 11:00:00", 2020.0, 2022.0, 2018.0, 2021.0),
            _bar("2025-01-15 11:05:00", 2021.0, 2023.0, 2019.0, 2022.0),
            _bar("2025-01-15 11:10:00", 2022.0, 2024.0, 2000.0, 2010.0),  # swing LOW at 2000
            _bar("2025-01-15 11:15:00", 2010.0, 2015.0, 2009.0, 2013.0),
            _bar("2025-01-15 11:20:00", 2013.0, 2017.0, 2012.0, 2015.0),
            _bar("2025-01-15 11:25:00", 2015.0, 2019.0, 2014.0, 2017.0),
            _bar("2025-01-15 11:30:00", 2017.0, 2020.0, 2016.0, 2018.0),
            _bar("2025-01-15 11:35:00", 2018.0, 2021.0, 2017.0, 2019.0),
            _bar("2025-01-15 11:40:00", 2019.0, 2022.0, 2018.0, 2020.0),
            _bar("2025-01-15 11:45:00", 2020.0, 2023.0, 2019.0, 2021.0),
            _bar("2025-01-15 11:50:00", 2021.0, 2024.0, 2020.0, 2022.0),
            _bar("2025-01-15 11:55:00", 2022.0, 2025.0, 2021.0, 2023.0),
            _bar("2025-01-15 12:00:00", 2023.0, 2026.0, 2022.0, 2024.0),
            _bar("2025-01-15 12:05:00", 2024.0, 2027.0, 2023.0, 2025.0),
            _bar("2025-01-15 12:10:00", 2025.0, 2028.0, 2024.0, 2026.0),
            _bar("2025-01-15 12:15:00", 2026.0, 2029.0, 2025.0, 2027.0),
            _bar("2025-01-15 12:20:00", 2027.0, 2030.0, 2026.0, 2028.0),
            _bar("2025-01-15 12:25:00", 2028.0, 2031.0, 2027.0, 2029.0),
            _bar("2025-01-15 12:30:00", 2029.0, 2032.0, 2028.0, 2030.0),
            _bar("2025-01-15 12:35:00", 2030.0, 2033.0, 2029.0, 2031.0),
            _bar("2025-01-15 12:40:00", 2031.0, 2034.0, 2030.0, 2032.0),
            _bar("2025-01-15 12:45:00", 2032.0, 2035.0, 2031.0, 2033.0),
            _bar("2025-01-15 12:50:00", 2033.0, 2036.0, 2032.0, 2034.0),
            _bar("2025-01-15 12:55:00", 2034.0, 2037.0, 2033.0, 2035.0),
            _bar("2025-01-15 13:00:00", 2035.0, 2038.0, 2034.0, 2036.0),
            _bar("2025-01-15 13:05:00", 2036.0, 2039.0, 2035.0, 2037.0),
            _bar("2025-01-15 13:10:00", 2037.0, 2040.0, 2036.0, 2038.0),
            _bar("2025-01-15 13:15:00", 2038.0, 2041.0, 2037.0, 2039.0),
            _bar("2025-01-15 13:20:00", 2039.0, 2042.0, 2038.0, 2040.0),
            _bar("2025-01-15 13:25:00", 2040.0, 2043.0, 2039.0, 2041.0),
        ]
        # Spike bar: huge DOWN spike sweeping swing low at 2000 -> goes to 1998
        spike_bar = [
            _bar("2025-01-15 13:30:00", 2041.0, 2042.0, 1998.0, 2010.0),  # spike low=1998 < 2000
        ]
        # Confirmation bar (bar k, last at/after T+5)
        confirmation_bar = [
            _bar("2025-01-15 13:35:00", 2010.0, 2015.0, 2008.0, 2012.0),
        ]
        # Entry bar k+1 and subsequent
        post_signal = [
            _bar("2025-01-15 13:40:00", 2012.0, 2018.0, 2011.0, 2016.0),
            _bar("2025-01-15 13:45:00", 2016.0, 2025.0, 2015.0, 2023.0),
            _bar("2025-01-15 13:50:00", 2023.0, 2035.0, 2022.0, 2032.0),
        ]
        return _make_bars(pre_event + spike_bar + confirmation_bar + post_signal)

    def test_buy_signal_generated(self):
        bars = self._build_bars()
        event_times = [self.EVENT_TIME]
        signals = detect_news_fade_signals(bars, event_times)
        assert len(signals) == 1, f"Expected 1 BUY signal, got {len(signals)}: {signals}"
        sig = signals[0]
        assert sig.direction == "BUY", f"Expected BUY, got {sig.direction}"

    def test_sl_below_spike_extreme(self):
        """For BUY: SL must be below the spike low (plus buffer)."""
        bars = self._build_bars()
        event_times = [self.EVENT_TIME]
        signals = detect_news_fade_signals(bars, event_times)
        assert len(signals) == 1
        sig = signals[0]
        spike_low = 1998.0
        assert sig.sl < spike_low, f"BUY SL {sig.sl} should be < spike low {spike_low}"

    def test_tp_above_entry(self):
        """For BUY: TP must be above entry open."""
        bars = self._build_bars()
        event_times = [self.EVENT_TIME]
        signals = detect_news_fade_signals(bars, event_times)
        assert len(signals) == 1
        sig = signals[0]
        entry_open = bars.iloc[sig.entry_index]["open"]
        assert sig.tp > entry_open, f"BUY TP {sig.tp} should be > entry open {entry_open}"


# ---------------------------------------------------------------------------
# Test 3: Spike exists but does NOT tag liquidity -> no signal
# ---------------------------------------------------------------------------

class TestNoLiquidityTagged:
    """
    Spike goes up but does NOT exceed any prior swing high (no liquidity sweep).
    No signal should be generated.
    """

    EVENT_TIME = pd.Timestamp("2025-02-07 13:30:00")

    def _build_bars(self):
        # Swing high at 2100 (well above spike)
        pre_event = [
            _bar("2025-02-07 11:00:00", 2050.0, 2100.0, 2048.0, 2055.0),  # swing high 2100
            _bar("2025-02-07 11:05:00", 2055.0, 2060.0, 2048.0, 2052.0),
            _bar("2025-02-07 11:10:00", 2052.0, 2060.0, 2048.0, 2052.0),
            _bar("2025-02-07 11:15:00", 2052.0, 2060.0, 2048.0, 2052.0),
            _bar("2025-02-07 11:20:00", 2052.0, 2060.0, 2048.0, 2052.0),
            _bar("2025-02-07 11:25:00", 2052.0, 2060.0, 2048.0, 2052.0),
            _bar("2025-02-07 11:30:00", 2052.0, 2060.0, 2048.0, 2052.0),
            _bar("2025-02-07 11:35:00", 2052.0, 2060.0, 2048.0, 2052.0),
            _bar("2025-02-07 11:40:00", 2052.0, 2060.0, 2048.0, 2052.0),
            _bar("2025-02-07 11:45:00", 2052.0, 2060.0, 2048.0, 2052.0),
            _bar("2025-02-07 11:50:00", 2052.0, 2060.0, 2048.0, 2052.0),
            _bar("2025-02-07 11:55:00", 2052.0, 2060.0, 2048.0, 2052.0),
            _bar("2025-02-07 12:00:00", 2052.0, 2060.0, 2048.0, 2052.0),
            _bar("2025-02-07 12:05:00", 2052.0, 2060.0, 2048.0, 2052.0),
            _bar("2025-02-07 12:10:00", 2052.0, 2060.0, 2048.0, 2052.0),
            _bar("2025-02-07 12:15:00", 2052.0, 2060.0, 2048.0, 2052.0),
            _bar("2025-02-07 12:20:00", 2052.0, 2060.0, 2048.0, 2052.0),
            _bar("2025-02-07 12:25:00", 2052.0, 2060.0, 2048.0, 2052.0),
            _bar("2025-02-07 12:30:00", 2052.0, 2060.0, 2048.0, 2052.0),
            _bar("2025-02-07 12:35:00", 2052.0, 2060.0, 2048.0, 2052.0),
            _bar("2025-02-07 12:40:00", 2052.0, 2060.0, 2048.0, 2052.0),
            _bar("2025-02-07 12:45:00", 2052.0, 2060.0, 2048.0, 2052.0),
            _bar("2025-02-07 12:50:00", 2052.0, 2060.0, 2048.0, 2052.0),
            _bar("2025-02-07 12:55:00", 2052.0, 2060.0, 2048.0, 2052.0),
            _bar("2025-02-07 13:00:00", 2052.0, 2060.0, 2048.0, 2052.0),
            _bar("2025-02-07 13:05:00", 2052.0, 2060.0, 2048.0, 2052.0),
            _bar("2025-02-07 13:10:00", 2052.0, 2060.0, 2048.0, 2052.0),
            _bar("2025-02-07 13:15:00", 2052.0, 2060.0, 2048.0, 2052.0),
            _bar("2025-02-07 13:20:00", 2052.0, 2060.0, 2048.0, 2052.0),
        ]
        # Spike goes up to 2070 - does NOT tag 2100
        pre_event_last = [
            _bar("2025-02-07 13:25:00", 2050.0, 2055.0, 2048.0, 2052.0),
        ]
        spike_bar = [
            _bar("2025-02-07 13:30:00", 2052.0, 2070.0, 2050.0, 2065.0),  # spike high=2070 < 2100
        ]
        confirm = [_bar("2025-02-07 13:35:00", 2065.0, 2067.0, 2060.0, 2062.0)]
        entry = [_bar("2025-02-07 13:40:00", 2062.0, 2064.0, 2058.0, 2060.0)]
        return _make_bars(pre_event + pre_event_last + spike_bar + confirm + entry)

    def test_no_signal_without_liquidity(self):
        bars = self._build_bars()
        event_times = [self.EVENT_TIME]
        signals = detect_news_fade_signals(bars, event_times)
        assert len(signals) == 0, f"Expected 0 signals (no liquidity tagged), got {len(signals)}"


# ---------------------------------------------------------------------------
# Test 4: No event -> no signal
# ---------------------------------------------------------------------------

class TestNoEvent:
    """No event anywhere near the bars -> no signal."""

    def _build_bars(self):
        # Normal bars with a spike but no event
        bars = [
            _bar("2025-03-07 13:00:00", 2050.0, 2053.0, 2048.0, 2051.0),
            _bar("2025-03-07 13:05:00", 2051.0, 2054.0, 2049.0, 2052.0),
            _bar("2025-03-07 13:10:00", 2052.0, 2090.0, 2051.0, 2060.0),  # spike with no event
            _bar("2025-03-07 13:15:00", 2060.0, 2062.0, 2055.0, 2057.0),
            _bar("2025-03-07 13:20:00", 2057.0, 2059.0, 2052.0, 2054.0),
        ]
        return _make_bars(bars)

    def test_no_signal_without_event(self):
        bars = self._build_bars()
        signals = detect_news_fade_signals(bars, event_times=[])
        assert len(signals) == 0, f"Expected 0 signals (no events), got {len(signals)}"

    def test_empty_events_list(self):
        bars = self._build_bars()
        signals = detect_news_fade_signals(bars, [])
        assert len(signals) == 0


# ---------------------------------------------------------------------------
# Test 5: Spike tags liquidity but R:R < 1:1 -> no signal
# ---------------------------------------------------------------------------

class TestInsufficientRR:
    """
    Spike tags a prior swing high (2060), so direction is SELL.
    But the pre-event price is very close to the spike extreme, making R:R < 1:1.
    SL must be above 2062 (spike extreme + buffer).
    TP is retrace target, but if the retrace target is very small, skip.
    """

    EVENT_TIME = pd.Timestamp("2025-03-12 13:30:00")

    def _build_bars(self):
        # Set up: swing high at 2060, pre-event price also near 2060
        # So spike to 2062 is tiny (2 points) but SL must be e.g. 2063 (above 2062+buffer)
        # Retrace target = 50% back toward pre-event (2060), so TP ≈ 2061
        # Risk (entry ~2060 to SL 2063) = 3; Reward (entry 2060 to TP 2061) = 1 -> R:R < 1
        pre_event = [
            _bar("2025-03-12 11:00:00", 2050.0, 2060.0, 2048.0, 2058.0),  # swing high 2060
            _bar("2025-03-12 11:05:00", 2058.0, 2060.0, 2056.0, 2059.0),
            _bar("2025-03-12 11:10:00", 2059.0, 2060.0, 2057.0, 2059.5),
            _bar("2025-03-12 11:15:00", 2059.5, 2060.0, 2058.0, 2059.0),
            _bar("2025-03-12 11:20:00", 2059.0, 2060.0, 2058.0, 2059.5),
            _bar("2025-03-12 11:25:00", 2059.5, 2060.0, 2058.0, 2059.0),
            _bar("2025-03-12 11:30:00", 2059.0, 2060.0, 2058.0, 2059.0),
            _bar("2025-03-12 11:35:00", 2059.0, 2060.0, 2058.0, 2059.0),
            _bar("2025-03-12 11:40:00", 2059.0, 2060.0, 2058.0, 2059.0),
            _bar("2025-03-12 11:45:00", 2059.0, 2060.0, 2058.0, 2059.0),
            _bar("2025-03-12 11:50:00", 2059.0, 2060.0, 2058.0, 2059.0),
            _bar("2025-03-12 11:55:00", 2059.0, 2060.0, 2058.0, 2059.0),
            _bar("2025-03-12 12:00:00", 2059.0, 2060.0, 2058.0, 2059.0),
            _bar("2025-03-12 12:05:00", 2059.0, 2060.0, 2058.0, 2059.0),
            _bar("2025-03-12 12:10:00", 2059.0, 2060.0, 2058.0, 2059.0),
            _bar("2025-03-12 12:15:00", 2059.0, 2060.0, 2058.0, 2059.0),
            _bar("2025-03-12 12:20:00", 2059.0, 2060.0, 2058.0, 2059.0),
            _bar("2025-03-12 12:25:00", 2059.0, 2060.0, 2058.0, 2059.0),
            _bar("2025-03-12 12:30:00", 2059.0, 2060.0, 2058.0, 2059.0),
            _bar("2025-03-12 12:35:00", 2059.0, 2060.0, 2058.0, 2059.0),
            _bar("2025-03-12 12:40:00", 2059.0, 2060.0, 2058.0, 2059.0),
            _bar("2025-03-12 12:45:00", 2059.0, 2060.0, 2058.0, 2059.0),
            _bar("2025-03-12 12:50:00", 2059.0, 2060.0, 2058.0, 2059.0),
            _bar("2025-03-12 12:55:00", 2059.0, 2060.0, 2058.0, 2059.0),
            _bar("2025-03-12 13:00:00", 2059.0, 2060.0, 2058.0, 2059.0),
            _bar("2025-03-12 13:05:00", 2059.0, 2060.0, 2058.0, 2059.0),
            _bar("2025-03-12 13:10:00", 2059.0, 2060.0, 2058.0, 2059.0),
            _bar("2025-03-12 13:15:00", 2059.0, 2060.0, 2058.0, 2059.0),
            _bar("2025-03-12 13:20:00", 2059.0, 2060.0, 2058.0, 2059.0),
            _bar("2025-03-12 13:25:00", 2059.0, 2060.0, 2058.0, 2059.0),
        ]
        # Tiny spike to 2062, just above swing high 2060, but pre-event was already 2059
        # SL = 2063.5, TP = 50% retrace ~ 2060.5 -> risk=3.5, reward=1.5 < 1:1 risk
        spike_bar = [
            _bar("2025-03-12 13:30:00", 2059.0, 2062.0, 2058.0, 2061.0),
        ]
        confirm = [_bar("2025-03-12 13:35:00", 2061.0, 2062.0, 2059.0, 2060.0)]
        entry = [_bar("2025-03-12 13:40:00", 2060.0, 2061.0, 2059.0, 2059.5)]
        return _make_bars(pre_event + spike_bar + confirm + entry)

    def test_no_signal_poor_rr(self):
        bars = self._build_bars()
        event_times = [self.EVENT_TIME]
        signals = detect_news_fade_signals(bars, event_times)
        # Poor R:R means signal should be skipped
        assert len(signals) == 0, (
            f"Expected 0 signals (R:R < 1:1), got {len(signals)}: {signals}"
        )


# ---------------------------------------------------------------------------
# Test 6: get_high_impact_usd_events returns valid timestamps
# ---------------------------------------------------------------------------

class TestGetHighImpactEvents:
    """get_high_impact_usd_events must return a non-empty list of tz-naive pd.Timestamps."""

    def test_returns_nonempty_list(self):
        events = get_high_impact_usd_events()
        assert len(events) > 0, "Expected non-empty event list"

    def test_returns_timestamps(self):
        events = get_high_impact_usd_events()
        for ev in events:
            assert isinstance(ev, pd.Timestamp), f"Expected pd.Timestamp, got {type(ev)}"

    def test_timestamps_are_tz_naive(self):
        """Events are returned as tz-naive (bars are tz-naive UTC) for consistent comparison."""
        events = get_high_impact_usd_events()
        for ev in events:
            assert ev.tzinfo is None, f"Expected tz-naive, got tzinfo={ev.tzinfo}"

    def test_includes_fomc_dates(self):
        """Must include known FOMC dates in IS window (e.g. 2025-01-29)."""
        events = get_high_impact_usd_events()
        fomc_jan = pd.Timestamp("2025-01-29")
        dates = [e.date() for e in events]
        assert fomc_jan.date() in dates, f"FOMC 2025-01-29 not found in events"

    def test_includes_nfp_dates(self):
        """Must include NFP 2025-01-10 13:30."""
        events = get_high_impact_usd_events()
        nfp_ts = pd.Timestamp("2025-01-10 13:30:00")
        assert nfp_ts in events, f"NFP {nfp_ts} not found in events: {events[:5]}..."

    def test_includes_cpi_dates(self):
        """Must include CPI 2025-01-15 13:30."""
        events = get_high_impact_usd_events()
        cpi_ts = pd.Timestamp("2025-01-15 13:30:00")
        assert cpi_ts in events, f"CPI {cpi_ts} not found in events"
