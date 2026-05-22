"""
test_s90_asia_range.py
----------------------
TDD tests for strategies.xauusd_strategies.s90_asia_range.

ALL tests use SYNTHETIC bars (hand-constructed DataFrames with tz-naive UTC
timestamps) — NO real cache, NO DB, NO network.

Strategy: Asia Range Break-and-Retest
  - Asia range  : high & low of 00:00–06:00 UTC 5m bars (finalised at 06:00)
  - London session: entries during 07:00–11:00 UTC, weekdays only
  - Pattern:
      * If London breaks Asia HIGH (close > asia_high) and then price
        returns back down to retest from above and rejects → SHORT entry
        on open of bar k+1 (after retest+rejection confirms at bar k)
      * If London breaks Asia LOW (close < asia_low) and then price
        returns back up to retest from below and reclaims → LONG entry
        on open of bar k+1

  - SL: beyond the sweep extreme (highest high during the break / lowest
        low) + a small buffer (SL_BUFFER_DEFAULT = 0.50)
  - TP: opposite Asia extreme (asia_low for SHORT; asia_high for LONG)
  - Skip if R:R < MIN_RR (1.5 by default)
  - max_hold_bars = 48

Look-ahead guarantee: decision at bar k uses only bars 0..k.
The Asia range is derived from bars whose time < 06:00 UTC, so it is
finalised before any London entry bar is processed.

Coverage
--------
generate_signals:
  1. SHORT setup — London breaks Asia high, retests, rejects → Signal(SELL, k+1, ...)
  2. LONG  setup — London breaks Asia low, retests, reclaims → Signal(BUY, k+1, ...)
  3. No setup — flat bars within range → []
  4. Entry skipped when R:R < MIN_RR — no Signal
  5. Entry on bar outside London window (e.g. 12:00 UTC) → no Signal
  6. Weekend bar inside apparent London hours → no Signal
  7. Multiple days — signals from different days both captured
"""
from __future__ import annotations

import pandas as pd
import pytest

from strategies.xauusd_strategies.s90_asia_range import generate_signals, MIN_RR, SL_BUFFER_DEFAULT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bar(time_str: str, open_: float, high: float, low: float, close: float) -> dict:
    return {
        "time":   pd.Timestamp(time_str),  # tz-naive UTC
        "open":   open_,
        "high":   high,
        "low":    low,
        "close":  close,
        "volume": 1000.0,
        "spread": 0.30,
    }


def _make_bars(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Constants used across tests
# ---------------------------------------------------------------------------

# A representative Tuesday — 2025-01-07 (no holiday concerns)
_DATE = "2025-01-07"

# Asia range for this day: built from 00:00–05:55 UTC bars (12 bars)
# We'll set asia_high = 2660.00, asia_low = 2640.00
_ASIA_HIGH = 2660.00
_ASIA_LOW  = 2640.00


def _asia_bars(date: str = _DATE, n: int = 12) -> list[dict]:
    """12 × 5m bars from 00:00 UTC that create asia_high/low as set above.
    Bar 0  : the session open sets range; high touches _ASIA_HIGH, low touches _ASIA_LOW.
    Bars 1–11: inside range, dull.
    The Asia range is 00:00–05:55 UTC (bar timestamps 00:00 .. 05:55).
    """
    bars = []
    for i in range(n):
        t = f"{date} {i // 12:02d}:{(i % 12) * 5:02d}:00"
        if i == 0:
            bars.append(_bar(t, 2650.0, _ASIA_HIGH, _ASIA_LOW, 2650.0))
        else:
            bars.append(_bar(t, 2650.0, 2655.0, 2645.0, 2650.0))
    return bars


# ---------------------------------------------------------------------------
# Test 1 — SHORT setup: London breaks Asia high → retest → reject → SELL
# ---------------------------------------------------------------------------

class TestShortSetup:
    """
    Asia range: high=2660, low=2640.
    London sequence (starting 07:05 UTC):
      Bar A (07:05): breaks high — close > 2660 (e.g. high=2668, close=2665)
                     sweep_extreme tracks: 2668
      Bar B (07:10): retest — price pulls back to 2660 zone from above
                     high=2662, low=2658, close=2659  ← close ≤ asia_high → retest confirmed
                     (close dropped back below/at asia_high → rejection)
    → SHORT signal at entry_index = index of Bar B + 1.
    SL = sweep_extreme + SL_BUFFER = 2668 + 0.50 = 2668.50
    TP = asia_low = 2640.00
    """

    def _build_bars(self) -> pd.DataFrame:
        rows = _asia_bars()
        # 06:00 bar — gap / flat (still Asia, this bar is excluded from London)
        rows.append(_bar(f"{_DATE} 06:00:00", 2650.0, 2653.0, 2648.0, 2651.0))
        # 06:05 bar — pre-London
        rows.append(_bar(f"{_DATE} 06:05:00", 2651.0, 2654.0, 2649.0, 2651.0))
        # London starts 07:00; but we skip the 07:00 bar itself — entries fire on 07:05+
        rows.append(_bar(f"{_DATE} 07:00:00", 2652.0, 2656.0, 2648.0, 2652.0))
        # Bar A 07:05 — breaks high (close=2665 > 2660)
        rows.append(_bar(f"{_DATE} 07:05:00", 2653.0, 2668.0, 2652.0, 2665.0))
        # Bar B 07:10 — retest: pulls back; close=2659 ≤ 2660 → rejection confirmed
        rows.append(_bar(f"{_DATE} 07:10:00", 2664.0, 2662.0, 2658.0, 2659.0))
        # Bar C 07:15 — ENTRY bar (entry_index points here; signal fires on its open)
        rows.append(_bar(f"{_DATE} 07:15:00", 2660.0, 2663.0, 2655.0, 2658.0))
        return _make_bars(rows)

    def test_generates_one_sell_signal(self):
        bars = self._build_bars()
        signals = generate_signals(bars)
        assert len(signals) == 1, f"Expected 1 signal, got {len(signals)}: {signals}"
        assert signals[0].direction == "SELL"

    def test_entry_index_is_bar_after_retest(self):
        bars = self._build_bars()
        signals = generate_signals(bars)
        # Bar B (retest) is second-to-last; Bar C (entry) is the last bar (index = len-1)
        expected_entry_index = len(bars) - 1
        assert signals[0].entry_index == expected_entry_index, (
            f"Expected entry_index={expected_entry_index}, got {signals[0].entry_index}"
        )

    def test_sl_beyond_sweep_extreme(self):
        bars = self._build_bars()
        signals = generate_signals(bars)
        # SL must be above asia_high (SHORT: SL above entry)
        assert signals[0].sl > _ASIA_HIGH
        # SL should be >= sweep_extreme (2668) + buffer
        assert signals[0].sl >= 2668.0 + SL_BUFFER_DEFAULT - 1e-9

    def test_tp_at_asia_low(self):
        bars = self._build_bars()
        signals = generate_signals(bars)
        assert abs(signals[0].tp - _ASIA_LOW) < 1e-6, (
            f"TP should be asia_low={_ASIA_LOW}, got {signals[0].tp}"
        )

    def test_rr_at_least_min_rr(self):
        """Verify the signal passes the R:R gate (TP-side / SL-side ≥ MIN_RR)."""
        bars = self._build_bars()
        signals = generate_signals(bars)
        # entry ≈ open of entry bar; for the R:R check we use mid prices
        entry_open = float(bars.iloc[signals[0].entry_index]["open"])
        sl = signals[0].sl
        tp = signals[0].tp
        # SELL: risk = sl - entry_open; reward = entry_open - tp
        risk   = sl - entry_open
        reward = entry_open - tp
        assert risk > 0, "SL must be above entry for SELL"
        assert reward / risk >= MIN_RR - 1e-9, (
            f"R:R {reward / risk:.2f} < MIN_RR {MIN_RR}"
        )


# ---------------------------------------------------------------------------
# Test 2 — LONG setup: London breaks Asia low → retest → reclaim → BUY
# ---------------------------------------------------------------------------

class TestLongSetup:
    """
    Asia range: high=2660, low=2640.
    London sequence (starting 07:05 UTC):
      Bar A (07:05): breaks low — close < 2640 (e.g. low=2632, close=2635)
                     sweep_extreme tracks: 2632
      Bar B (07:10): retest — price rallies back to 2640 zone from below
                     low=2638, high=2642, close=2641  ← close ≥ asia_low → reclaim confirmed
    → LONG signal at entry_index = index of Bar B + 1.
    SL = sweep_extreme - SL_BUFFER = 2632 - 0.50 = 2631.50
    TP = asia_high = 2660.00
    """

    def _build_bars(self) -> pd.DataFrame:
        rows = _asia_bars()
        rows.append(_bar(f"{_DATE} 06:00:00", 2650.0, 2653.0, 2648.0, 2651.0))
        rows.append(_bar(f"{_DATE} 06:05:00", 2651.0, 2654.0, 2649.0, 2651.0))
        rows.append(_bar(f"{_DATE} 07:00:00", 2650.0, 2652.0, 2645.0, 2648.0))
        # Bar A 07:05 — breaks low (close=2635 < 2640)
        rows.append(_bar(f"{_DATE} 07:05:00", 2647.0, 2648.0, 2632.0, 2635.0))
        # Bar B 07:10 — reclaim: rallies; close=2641 ≥ 2640 → reclaim confirmed
        rows.append(_bar(f"{_DATE} 07:10:00", 2636.0, 2642.0, 2638.0, 2641.0))
        # Bar C 07:15 — ENTRY bar (entry_index points here; signal fires on its open)
        rows.append(_bar(f"{_DATE} 07:15:00", 2641.0, 2645.0, 2639.0, 2643.0))
        return _make_bars(rows)

    def test_generates_one_buy_signal(self):
        bars = self._build_bars()
        signals = generate_signals(bars)
        assert len(signals) == 1, f"Expected 1 signal, got {len(signals)}: {signals}"
        assert signals[0].direction == "BUY"

    def test_entry_index_is_bar_after_retest(self):
        bars = self._build_bars()
        signals = generate_signals(bars)
        # Bar B (reclaim) is second-to-last; Bar C (entry) is the last bar (index = len-1)
        expected_entry_index = len(bars) - 1
        assert signals[0].entry_index == expected_entry_index, (
            f"Expected entry_index={expected_entry_index}, got {signals[0].entry_index}"
        )

    def test_sl_below_sweep_extreme(self):
        bars = self._build_bars()
        signals = generate_signals(bars)
        # SL must be below asia_low (BUY: SL below entry)
        assert signals[0].sl < _ASIA_LOW
        # SL should be ≤ sweep_extreme (2632) - buffer
        assert signals[0].sl <= 2632.0 - SL_BUFFER_DEFAULT + 1e-9

    def test_tp_at_asia_high(self):
        bars = self._build_bars()
        signals = generate_signals(bars)
        assert abs(signals[0].tp - _ASIA_HIGH) < 1e-6, (
            f"TP should be asia_high={_ASIA_HIGH}, got {signals[0].tp}"
        )

    def test_rr_at_least_min_rr(self):
        bars = self._build_bars()
        signals = generate_signals(bars)
        entry_open = float(bars.iloc[signals[0].entry_index]["open"])
        sl = signals[0].sl
        tp = signals[0].tp
        # BUY: risk = entry_open - sl; reward = tp - entry_open
        risk   = entry_open - sl
        reward = tp - entry_open
        assert risk > 0, "SL must be below entry for BUY"
        assert reward / risk >= MIN_RR - 1e-9, (
            f"R:R {reward / risk:.2f} < MIN_RR {MIN_RR}"
        )


# ---------------------------------------------------------------------------
# Test 3 — No setup: bars always within range → no signals
# ---------------------------------------------------------------------------

class TestNoSetup:
    """
    London bars never close outside the Asia range → no break → no signal.
    """

    def _build_bars(self) -> pd.DataFrame:
        rows = _asia_bars()
        rows.append(_bar(f"{_DATE} 06:00:00", 2650.0, 2653.0, 2648.0, 2651.0))
        rows.append(_bar(f"{_DATE} 07:00:00", 2651.0, 2658.0, 2643.0, 2652.0))
        rows.append(_bar(f"{_DATE} 07:05:00", 2652.0, 2657.0, 2644.0, 2651.0))
        rows.append(_bar(f"{_DATE} 07:10:00", 2651.0, 2655.0, 2645.0, 2650.0))
        return _make_bars(rows)

    def test_no_signals_returned(self):
        bars = self._build_bars()
        signals = generate_signals(bars)
        assert signals == [], f"Expected no signals, got {signals}"


# ---------------------------------------------------------------------------
# Test 4 — R:R gate: too-tight TP → signal skipped
# ---------------------------------------------------------------------------

class TestRRGate:
    """
    SHORT setup as in Test 1, but TP is forced close to entry by making
    the Asia range very narrow (asia_high ≈ asia_low).
    Result: reward / risk < MIN_RR → no signal emitted.

    We construct a day where asia_high=2660, asia_low=2659 (tiny range).
    Bar A breaks high (close=2665, sweep=2668).
    Bar B retests (close=2659).
    Entry open ≈ 2659. SL = 2668.50. TP = 2659.
    risk   = 2668.50 - 2659 = 9.50
    reward = 2659 - 2659 = 0 → R:R = 0 → skipped.
    """

    def _build_bars(self) -> pd.DataFrame:
        narrow_high = 2660.0
        narrow_low  = 2659.5   # only 0.50 range

        # Asia bars: high touches narrow_high, low touches narrow_low
        rows = []
        for i in range(12):
            t = f"{_DATE} {i // 12:02d}:{(i % 12) * 5:02d}:00"
            if i == 0:
                rows.append(_bar(t, 2659.7, narrow_high, narrow_low, 2659.7))
            else:
                rows.append(_bar(t, 2659.7, 2659.9, 2659.6, 2659.7))

        rows.append(_bar(f"{_DATE} 06:00:00", 2659.7, 2659.9, 2659.6, 2659.7))
        rows.append(_bar(f"{_DATE} 07:00:00", 2659.7, 2659.9, 2659.6, 2659.7))
        # Bar A: breaks high
        rows.append(_bar(f"{_DATE} 07:05:00", 2659.7, 2668.0, 2659.5, 2665.0))
        # Bar B: retest — close ≤ narrow_high → rejection
        rows.append(_bar(f"{_DATE} 07:10:00", 2664.0, 2661.0, 2659.0, 2659.5))
        # Bar C: entry bar — needed so i+1 is in range, but R:R check should fail
        rows.append(_bar(f"{_DATE} 07:15:00", 2659.5, 2660.5, 2659.0, 2659.7))
        return _make_bars(rows)

    def test_no_signal_when_rr_below_minimum(self):
        bars = self._build_bars()
        signals = generate_signals(bars)
        assert signals == [], (
            f"Expected no signals (R:R below MIN_RR={MIN_RR}), got {signals}"
        )


# ---------------------------------------------------------------------------
# Test 5 — Outside London window: break+retest at 12:00 UTC → no signal
# ---------------------------------------------------------------------------

class TestOutsideLondonWindow:
    """
    Same SHORT pattern but Bar B is at 12:00 UTC (outside 07:00–11:00).
    No signal should be generated for entries outside the window.
    """

    def _build_bars(self) -> pd.DataFrame:
        rows = _asia_bars()
        rows.append(_bar(f"{_DATE} 06:00:00", 2650.0, 2653.0, 2648.0, 2651.0))
        rows.append(_bar(f"{_DATE} 07:00:00", 2650.0, 2653.0, 2648.0, 2652.0))
        # Bar A 07:05 — breaks high (close > 2660)
        rows.append(_bar(f"{_DATE} 07:05:00", 2653.0, 2668.0, 2652.0, 2665.0))
        # skip time to 12:00 — Bar B at 12:00 UTC (outside window)
        rows.append(_bar(f"{_DATE} 12:00:00", 2664.0, 2662.0, 2658.0, 2659.0))
        return _make_bars(rows)

    def test_no_signal_outside_london_window(self):
        bars = self._build_bars()
        signals = generate_signals(bars)
        # Entry would be at the bar AFTER Bar B, but Bar B is at 12:00 UTC
        # (outside London window 07:00–11:00), so no signal should fire.
        # (The state machine resets / doesn't emit when retest bar is outside window.)
        assert signals == [], (
            f"Expected no signals (outside London window), got {signals}"
        )


# ---------------------------------------------------------------------------
# Test 6 — Weekend bar: Saturday bars inside London hours → no signal
# ---------------------------------------------------------------------------

class TestWeekendFilter:
    """
    Same SHORT pattern but all London bars fall on a Saturday (2025-01-04).
    Strategy must skip weekends.
    """

    _SATURDAY = "2025-01-04"

    def _build_bars(self) -> pd.DataFrame:
        rows = _asia_bars(date=self._SATURDAY)
        rows.append(_bar(f"{self._SATURDAY} 06:00:00", 2650.0, 2653.0, 2648.0, 2651.0))
        rows.append(_bar(f"{self._SATURDAY} 07:00:00", 2650.0, 2653.0, 2648.0, 2652.0))
        rows.append(_bar(f"{self._SATURDAY} 07:05:00", 2653.0, 2668.0, 2652.0, 2665.0))
        rows.append(_bar(f"{self._SATURDAY} 07:10:00", 2664.0, 2662.0, 2658.0, 2659.0))
        return _make_bars(rows)

    def test_no_signal_on_weekend(self):
        bars = self._build_bars()
        signals = generate_signals(bars)
        assert signals == [], (
            f"Expected no signals on weekend (Saturday), got {signals}"
        )


# ---------------------------------------------------------------------------
# Test 7 — Multi-day: SHORT on day 1, LONG on day 2 → 2 signals
# ---------------------------------------------------------------------------

class TestMultiDaySignals:
    """
    Day 1 (2025-01-07, Tuesday): SHORT setup (as in Test 1)
    Day 2 (2025-01-08, Wednesday): LONG setup (as in Test 2)
    → exactly 2 signals, one SELL and one BUY.
    """

    _DAY1 = "2025-01-07"
    _DAY2 = "2025-01-08"

    def _build_bars(self) -> pd.DataFrame:
        rows = []

        # --- Day 1 Asia ---
        rows += _asia_bars(date=self._DAY1)
        rows.append(_bar(f"{self._DAY1} 06:00:00", 2650.0, 2653.0, 2648.0, 2651.0))
        rows.append(_bar(f"{self._DAY1} 07:00:00", 2652.0, 2656.0, 2648.0, 2652.0))
        rows.append(_bar(f"{self._DAY1} 07:05:00", 2653.0, 2668.0, 2652.0, 2665.0))
        rows.append(_bar(f"{self._DAY1} 07:10:00", 2664.0, 2662.0, 2658.0, 2659.0))
        # entry bar for Day 1 (07:15)
        rows.append(_bar(f"{self._DAY1} 07:15:00", 2660.0, 2663.0, 2655.0, 2658.0))

        # --- Day 2 Asia ---
        rows += _asia_bars(date=self._DAY2)
        rows.append(_bar(f"{self._DAY2} 06:00:00", 2650.0, 2653.0, 2648.0, 2651.0))
        rows.append(_bar(f"{self._DAY2} 07:00:00", 2650.0, 2652.0, 2645.0, 2648.0))
        rows.append(_bar(f"{self._DAY2} 07:05:00", 2647.0, 2648.0, 2632.0, 2635.0))
        rows.append(_bar(f"{self._DAY2} 07:10:00", 2636.0, 2642.0, 2638.0, 2641.0))
        # entry bar for Day 2 (07:15)
        rows.append(_bar(f"{self._DAY2} 07:15:00", 2641.0, 2645.0, 2639.0, 2643.0))

        return _make_bars(rows)

    def test_two_signals_generated(self):
        bars = self._build_bars()
        signals = generate_signals(bars)
        assert len(signals) == 2, f"Expected 2 signals, got {len(signals)}: {signals}"

    def test_day1_signal_is_sell(self):
        bars = self._build_bars()
        signals = generate_signals(bars)
        sells = [s for s in signals if s.direction == "SELL"]
        assert len(sells) == 1, f"Expected 1 SELL, got {sells}"

    def test_day2_signal_is_buy(self):
        bars = self._build_bars()
        signals = generate_signals(bars)
        buys = [s for s in signals if s.direction == "BUY"]
        assert len(buys) == 1, f"Expected 1 BUY, got {buys}"
