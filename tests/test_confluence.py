"""
test_confluence.py
------------------
TDD tests for strategies.research.confluence (Task 6).

All tests use synthetic in-memory bar DataFrames (tz-naive UTC timestamps).
NO real data, NO network, NO disk I/O.

Coverage
--------
1. htf_bias: True (aligned) and False (misaligned / insufficient data)
2. session_killzone: True inside windows, False outside, False on weekends
3. liquidity_sweep: BUY and SELL positive/negative cases
4. fvg_present: bullish FVG satisfies BUY not SELL; negative cases
5. ob_present: bullish OB satisfies BUY not SELL; negative cases
6. volume_anomaly: spike above mult * mean vs. normal volume
7. dxy_inverse_correlation_holding: raises ConfluenceUnavailable; NOT in SEARCHABLE
8. evaluate / all_met / count_met correctness
9. No-look-ahead invariant: appending a future bar after idx does not change any result
"""
from __future__ import annotations

import pandas as pd
import pytest

from strategies.research.confluence import (
    ConfluenceUnavailable,
    ConfluenceContext,
    htf_bias,
    session_killzone,
    liquidity_sweep,
    fvg_present,
    ob_present,
    volume_anomaly,
    dxy_inverse_correlation_holding,
    DETECTORS,
    SEARCHABLE,
    evaluate,
    all_met,
    count_met,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(s: str) -> pd.Timestamp:
    """Return a tz-naive UTC Timestamp."""
    return pd.Timestamp(s)


def _make_bars(
    rows: list[dict],
    base_time: str = "2026-01-13 08:00:00",
    interval_minutes: int = 5,
) -> pd.DataFrame:
    """Build a minimal OHLCV + spread DataFrame with tz-naive UTC timestamps."""
    t0 = pd.Timestamp(base_time)
    data = []
    for i, r in enumerate(rows):
        t = r.get("time", t0 + pd.Timedelta(minutes=interval_minutes * i))
        data.append({
            "time":   pd.Timestamp(t),
            "open":   float(r["open"]),
            "high":   float(r["high"]),
            "low":    float(r["low"]),
            "close":  float(r["close"]),
            "volume": float(r.get("volume", 100)),
            "spread": float(r.get("spread", 0.30)),
        })
    return pd.DataFrame(data)


def _make_htf_bars(
    rows: list[dict],
    base_time: str = "2026-01-12 00:00:00",
    interval_hours: int = 4,
) -> pd.DataFrame:
    """Build HTF (e.g. 4H) bars with tz-naive UTC timestamps."""
    t0 = pd.Timestamp(base_time)
    data = []
    for i, r in enumerate(rows):
        t = r.get("time", t0 + pd.Timedelta(hours=interval_hours * i))
        data.append({
            "time":   pd.Timestamp(t),
            "open":   float(r["open"]),
            "high":   float(r["high"]),
            "low":    float(r["low"]),
            "close":  float(r["close"]),
            "volume": float(r.get("volume", 1000)),
            "spread": float(r.get("spread", 0.30)),
        })
    return pd.DataFrame(data)


# ---------------------------------------------------------------------------
# 1. htf_bias
# ---------------------------------------------------------------------------

class TestHtfBias:
    """htf_bias(ctx) -> bool: True iff HTF trend aligns with ctx.direction.
    Uses close[last] vs close[last - lookback] to determine bias direction.
    If htf_bars is None or fewer than lookback+1 bars closed → False.
    Only HTF bars whose time is BEFORE bars.time[idx] are considered.
    """

    def test_buy_aligned_bullish_htf(self):
        """HTF close rising → bullish; BUY direction → True."""
        # HTF bars closing at 04:00, 08:00, 12:00 (before entry bar at 13:00)
        htf = _make_htf_bars([
            {"open": 2600, "high": 2610, "low": 2595, "close": 2600},  # 2026-01-12 00:00
            {"open": 2600, "high": 2615, "low": 2598, "close": 2608},  # 04:00
            {"open": 2608, "high": 2620, "low": 2605, "close": 2618},  # 08:00
        ], base_time="2026-01-12 00:00:00")
        # Entry bar is at 2026-01-13 08:05 — all HTF bars above are before that
        entry_bars = _make_bars([
            {"open": 2618, "high": 2622, "low": 2615, "close": 2620},
        ], base_time="2026-01-13 08:05:00")
        ctx = ConfluenceContext(bars=entry_bars, idx=0, direction="BUY", htf_bars=htf, lookback=2)
        assert htf_bias(ctx) is True

    def test_sell_aligned_bearish_htf(self):
        """HTF close falling → bearish; SELL direction → True."""
        htf = _make_htf_bars([
            {"open": 2620, "high": 2625, "low": 2615, "close": 2620},
            {"open": 2620, "high": 2618, "low": 2608, "close": 2612},
            {"open": 2612, "high": 2610, "low": 2600, "close": 2602},
        ], base_time="2026-01-12 00:00:00")
        entry_bars = _make_bars([
            {"open": 2602, "high": 2605, "low": 2598, "close": 2600},
        ], base_time="2026-01-13 08:05:00")
        ctx = ConfluenceContext(bars=entry_bars, idx=0, direction="SELL", htf_bars=htf, lookback=2)
        assert htf_bias(ctx) is True

    def test_buy_misaligned_bearish_htf(self):
        """HTF falling → bearish; BUY direction → False."""
        htf = _make_htf_bars([
            {"open": 2620, "high": 2625, "low": 2615, "close": 2620},
            {"open": 2620, "high": 2618, "low": 2608, "close": 2612},
            {"open": 2612, "high": 2610, "low": 2600, "close": 2602},
        ], base_time="2026-01-12 00:00:00")
        entry_bars = _make_bars([
            {"open": 2602, "high": 2605, "low": 2598, "close": 2600},
        ], base_time="2026-01-13 08:05:00")
        ctx = ConfluenceContext(bars=entry_bars, idx=0, direction="BUY", htf_bars=htf, lookback=2)
        assert htf_bias(ctx) is False

    def test_none_htf_bars_returns_false(self):
        """htf_bars=None → False (insufficient data)."""
        entry_bars = _make_bars([
            {"open": 2618, "high": 2622, "low": 2615, "close": 2620},
        ])
        ctx = ConfluenceContext(bars=entry_bars, idx=0, direction="BUY", htf_bars=None)
        assert htf_bias(ctx) is False

    def test_insufficient_htf_bars_returns_false(self):
        """Fewer than lookback+1 closed HTF bars → False."""
        # Only 1 bar, lookback=2 requires 3
        htf = _make_htf_bars([
            {"open": 2600, "high": 2620, "low": 2595, "close": 2618},
        ], base_time="2026-01-12 00:00:00")
        entry_bars = _make_bars([
            {"open": 2618, "high": 2622, "low": 2615, "close": 2620},
        ], base_time="2026-01-13 08:05:00")
        ctx = ConfluenceContext(bars=entry_bars, idx=0, direction="BUY", htf_bars=htf, lookback=2)
        assert htf_bias(ctx) is False

    def test_future_htf_bars_excluded(self):
        """HTF bars whose time >= bars.time[idx] must NOT be used.

        Only bars before the entry bar time are visible.
        If removing future bars leaves insufficient history → False.
        """
        # Entry bar is at 2026-01-12 08:05; HTF bars at 08:00 and 12:00.
        # The bar at 12:00 is AFTER the entry, so must be excluded.
        htf = _make_htf_bars([
            {"open": 2600, "high": 2610, "low": 2595, "close": 2600},  # 00:00 — visible
            # This bar at 12:00 is after the entry bar at 08:05 — NOT visible
            {"open": 2600, "high": 2620, "low": 2598, "close": 2618},  # 04:00 — visible
        ], base_time="2026-01-12 00:00:00")
        # Add a future bar that would make it look bullish if included
        future_htf = pd.concat([htf, pd.DataFrame([{
            "time":   _ts("2026-01-12 12:00:00"),
            "open":   2618.0, "high": 2640.0, "low": 2615.0, "close": 2638.0,
            "volume": 1000.0, "spread": 0.30,
        }])], ignore_index=True)
        # Entry bar at 08:05 — the 12:00 HTF bar is after entry, so excluded
        entry_bars = _make_bars([
            {"open": 2602, "high": 2605, "low": 2598, "close": 2600},
        ], base_time="2026-01-12 08:05:00")
        ctx = ConfluenceContext(bars=entry_bars, idx=0, direction="BUY",
                                htf_bars=future_htf, lookback=2)
        # With only 2 visible bars and lookback=2, result is False (insufficient)
        assert htf_bias(ctx) is False


# ---------------------------------------------------------------------------
# 2. session_killzone
# ---------------------------------------------------------------------------

class TestSessionKillzone:
    """session_killzone(ctx) -> bool.
    True iff bars.time[idx] hour (with fraction) falls in any killzone window,
    and it is a weekday (Mon-Fri).
    Default windows: ((8.0, 10.0), (13.5, 15.5)) UTC.
    """

    def _ctx(self, ts: pd.Timestamp, direction: str = "BUY",
              windows=((8.0, 10.0), (13.5, 15.5))) -> ConfluenceContext:
        bars = _make_bars([{"open": 2600, "high": 2610, "low": 2595, "close": 2605}],
                          base_time=str(ts))
        return ConfluenceContext(bars=bars, idx=0, direction=direction,
                                 killzone_windows=windows)

    def test_london_open_boundary(self):
        """08:00 UTC (London open) is inside [8.0, 10.0)."""
        assert session_killzone(self._ctx(_ts("2026-01-13 08:00:00"))) is True

    def test_london_inside(self):
        assert session_killzone(self._ctx(_ts("2026-01-13 09:00:00"))) is True

    def test_london_end_boundary_exclusive(self):
        """10:00 is exclusive — not inside [8.0, 10.0)."""
        assert session_killzone(self._ctx(_ts("2026-01-13 10:00:00"))) is False

    def test_ny_open_start(self):
        """13:30 = 13.5 UTC (NY open) is inside [13.5, 15.5)."""
        assert session_killzone(self._ctx(_ts("2026-01-13 13:30:00"))) is True

    def test_ny_inside(self):
        assert session_killzone(self._ctx(_ts("2026-01-13 14:00:00"))) is True

    def test_ny_end_boundary_exclusive(self):
        """15:30 = 15.5 is exclusive."""
        assert session_killzone(self._ctx(_ts("2026-01-13 15:30:00"))) is False

    def test_outside_all_windows(self):
        assert session_killzone(self._ctx(_ts("2026-01-13 12:00:00"))) is False

    def test_weekend_excluded(self):
        """Saturday 2026-01-10 08:30 → False (weekend)."""
        assert session_killzone(self._ctx(_ts("2026-01-10 08:30:00"))) is False

    def test_sunday_excluded(self):
        """Sunday 2026-01-11 09:00 → False (weekend)."""
        assert session_killzone(self._ctx(_ts("2026-01-11 09:00:00"))) is False

    def test_fractional_minute_inside(self):
        """13:45 = 13.75 is inside [13.5, 15.5)."""
        assert session_killzone(self._ctx(_ts("2026-01-13 13:45:00"))) is True


# ---------------------------------------------------------------------------
# 3. liquidity_sweep
# ---------------------------------------------------------------------------

class TestLiquiditySweep:
    """liquidity_sweep(ctx) -> bool.
    BUY: within [idx-sweep_lookback, idx], a bar sweeps below a prior swing low
         (low < prior swing low) and closes back above it.
    SELL: within lookback, a bar sweeps above a prior swing high and closes back below it.
    """

    def test_buy_sweep_positive(self):
        """BUY: bar sweeps below prior swing low and closes above it → True."""
        # Prior swing low is 2590 (bar 0).
        # Sweep bar (bar 2): low=2585 < 2590, close=2595 > 2590.
        bars = _make_bars([
            {"time": "2026-01-13 08:00", "open": 2595, "high": 2600, "low": 2590, "close": 2598},
            {"time": "2026-01-13 08:05", "open": 2598, "high": 2602, "low": 2592, "close": 2600},
            # Sweep bar
            {"time": "2026-01-13 08:10", "open": 2600, "high": 2605, "low": 2585, "close": 2595},
        ])
        ctx = ConfluenceContext(bars=bars, idx=2, direction="BUY", sweep_lookback=20)
        assert liquidity_sweep(ctx) is True

    def test_buy_sweep_negative_no_close_above(self):
        """BUY: bar sweeps below prior low but closes BELOW it → False (no reversal close)."""
        bars = _make_bars([
            {"time": "2026-01-13 08:00", "open": 2595, "high": 2600, "low": 2590, "close": 2598},
            {"time": "2026-01-13 08:05", "open": 2598, "high": 2602, "low": 2592, "close": 2600},
            # Sweeps below 2590 but closes below it too → no reversal
            {"time": "2026-01-13 08:10", "open": 2600, "high": 2601, "low": 2585, "close": 2587},
        ])
        ctx = ConfluenceContext(bars=bars, idx=2, direction="BUY", sweep_lookback=20)
        assert liquidity_sweep(ctx) is False

    def test_sell_sweep_positive(self):
        """SELL: bar sweeps above prior swing high and closes below it → True."""
        # Prior swing high: 2640 (bar 0).
        # Sweep bar (bar 2): high=2645 > 2640, close=2635 < 2640.
        bars = _make_bars([
            {"time": "2026-01-13 08:00", "open": 2630, "high": 2640, "low": 2628, "close": 2635},
            {"time": "2026-01-13 08:05", "open": 2635, "high": 2638, "low": 2630, "close": 2633},
            # Sweep bar
            {"time": "2026-01-13 08:10", "open": 2633, "high": 2645, "low": 2628, "close": 2635},
        ])
        ctx = ConfluenceContext(bars=bars, idx=2, direction="SELL", sweep_lookback=20)
        assert liquidity_sweep(ctx) is True

    def test_sell_sweep_negative_no_close_below(self):
        """SELL: sweeps above prior high but closes above it → False."""
        bars = _make_bars([
            {"time": "2026-01-13 08:00", "open": 2630, "high": 2640, "low": 2628, "close": 2635},
            {"time": "2026-01-13 08:05", "open": 2635, "high": 2638, "low": 2630, "close": 2633},
            # Closes above prior swing high — no reversal
            {"time": "2026-01-13 08:10", "open": 2633, "high": 2645, "low": 2628, "close": 2643},
        ])
        ctx = ConfluenceContext(bars=bars, idx=2, direction="SELL", sweep_lookback=20)
        assert liquidity_sweep(ctx) is False

    def test_no_sweep_at_all(self):
        """No bar in lookback range exceeds prior extremes → False."""
        bars = _make_bars([
            {"time": "2026-01-13 08:00", "open": 2595, "high": 2600, "low": 2590, "close": 2598},
            {"time": "2026-01-13 08:05", "open": 2598, "high": 2602, "low": 2592, "close": 2600},
            # No sweep below swing low 2590
            {"time": "2026-01-13 08:10", "open": 2600, "high": 2605, "low": 2592, "close": 2603},
        ])
        ctx = ConfluenceContext(bars=bars, idx=2, direction="BUY", sweep_lookback=20)
        assert liquidity_sweep(ctx) is False


# ---------------------------------------------------------------------------
# 4. fvg_present
# ---------------------------------------------------------------------------

class TestFvgPresent:
    """fvg_present(ctx) -> bool.
    Bullish FVG at bar i: high[i-2] < low[i]. BUY needs bullish; SELL needs bearish.
    Bearish FVG at bar i: low[i-2] > high[i].
    Searches within [idx-fvg_lookback, idx].
    """

    def test_bullish_fvg_satisfies_buy(self):
        """Bullish FVG (high[i-2] < low[i]) → True for BUY."""
        bars = _make_bars([
            {"time": "2026-01-13 08:00", "open": 2590, "high": 2595, "low": 2588, "close": 2593},
            {"time": "2026-01-13 08:05", "open": 2593, "high": 2598, "low": 2591, "close": 2596},
            # FVG bar: low[2]=2605, high[0]=2595 → 2595 < 2605 → bullish gap
            {"time": "2026-01-13 08:10", "open": 2605, "high": 2620, "low": 2605, "close": 2618},
        ])
        ctx = ConfluenceContext(bars=bars, idx=2, direction="BUY", fvg_lookback=10)
        assert fvg_present(ctx) is True

    def test_bullish_fvg_does_not_satisfy_sell(self):
        """Bullish FVG should NOT satisfy SELL direction → False."""
        bars = _make_bars([
            {"time": "2026-01-13 08:00", "open": 2590, "high": 2595, "low": 2588, "close": 2593},
            {"time": "2026-01-13 08:05", "open": 2593, "high": 2598, "low": 2591, "close": 2596},
            {"time": "2026-01-13 08:10", "open": 2605, "high": 2620, "low": 2605, "close": 2618},
        ])
        ctx = ConfluenceContext(bars=bars, idx=2, direction="SELL", fvg_lookback=10)
        assert fvg_present(ctx) is False

    def test_bearish_fvg_satisfies_sell(self):
        """Bearish FVG (low[i-2] > high[i]) → True for SELL."""
        bars = _make_bars([
            {"time": "2026-01-13 08:00", "open": 2645, "high": 2650, "low": 2640, "close": 2642},
            {"time": "2026-01-13 08:05", "open": 2642, "high": 2644, "low": 2635, "close": 2637},
            # FVG bar: low[0]=2640, high[2]=2630 → 2640 > 2630 → bearish gap
            {"time": "2026-01-13 08:10", "open": 2632, "high": 2630, "low": 2615, "close": 2618},
        ])
        ctx = ConfluenceContext(bars=bars, idx=2, direction="SELL", fvg_lookback=10)
        assert fvg_present(ctx) is True

    def test_bearish_fvg_does_not_satisfy_buy(self):
        """Bearish FVG should NOT satisfy BUY direction → False."""
        bars = _make_bars([
            {"time": "2026-01-13 08:00", "open": 2645, "high": 2650, "low": 2640, "close": 2642},
            {"time": "2026-01-13 08:05", "open": 2642, "high": 2644, "low": 2635, "close": 2637},
            {"time": "2026-01-13 08:10", "open": 2632, "high": 2630, "low": 2615, "close": 2618},
        ])
        ctx = ConfluenceContext(bars=bars, idx=2, direction="BUY", fvg_lookback=10)
        assert fvg_present(ctx) is False

    def test_no_fvg_present(self):
        """No gap between candles → False."""
        bars = _make_bars([
            {"time": "2026-01-13 08:00", "open": 2600, "high": 2605, "low": 2598, "close": 2603},
            {"time": "2026-01-13 08:05", "open": 2603, "high": 2607, "low": 2601, "close": 2605},
            # Overlapping ranges — no FVG
            {"time": "2026-01-13 08:10", "open": 2605, "high": 2610, "low": 2603, "close": 2608},
        ])
        ctx = ConfluenceContext(bars=bars, idx=2, direction="BUY", fvg_lookback=10)
        assert fvg_present(ctx) is False

    def test_fvg_outside_lookback_not_counted(self):
        """FVG exists but is outside fvg_lookback range → False.
        idx=5, fvg_lookback=2 → only check bars 3..5. FVG is at bars 0-2.
        """
        rows = []
        # bars 0-2: FVG present
        rows.append({"time": "2026-01-13 08:00", "open": 2590, "high": 2595, "low": 2588, "close": 2593})
        rows.append({"time": "2026-01-13 08:05", "open": 2593, "high": 2598, "low": 2591, "close": 2596})
        rows.append({"time": "2026-01-13 08:10", "open": 2605, "high": 2620, "low": 2605, "close": 2618})
        # bars 3-5: no FVG (tight price action)
        rows.append({"time": "2026-01-13 08:15", "open": 2618, "high": 2622, "low": 2616, "close": 2620})
        rows.append({"time": "2026-01-13 08:20", "open": 2620, "high": 2623, "low": 2618, "close": 2621})
        rows.append({"time": "2026-01-13 08:25", "open": 2621, "high": 2624, "low": 2619, "close": 2622})
        bars = _make_bars(rows)
        ctx = ConfluenceContext(bars=bars, idx=5, direction="BUY", fvg_lookback=2)
        assert fvg_present(ctx) is False


# ---------------------------------------------------------------------------
# 5. ob_present
# ---------------------------------------------------------------------------

class TestObPresent:
    """ob_present(ctx) -> bool.
    Bullish OB (BUY): last down-candle (close < open) before a sustained up-move
    within the lookback window.
    Bearish OB (SELL): last up-candle (close > open) before a sustained down-move
    within the lookback window.

    Simplified rule (documented in confluence.py):
      BUY:  search bars[idx-lookback..idx] for the last bearish candle (close<open)
            that is immediately followed by a bullish candle (close>open) — this
            is a minimal bullish OB signal. The current bar (idx) is within
            lookback of such a pattern.
      SELL: search bars[idx-lookback..idx] for the last bullish candle (close>open)
            immediately followed by a bearish candle (close<open).
    """

    def test_bullish_ob_satisfies_buy(self):
        """Last bearish->bullish transition within lookback → True for BUY."""
        bars = _make_bars([
            {"time": "2026-01-13 08:00", "open": 2610, "high": 2612, "low": 2598, "close": 2600},
            # OB bar: bearish (close 2600 < open 2610)
            {"time": "2026-01-13 08:05", "open": 2600, "high": 2625, "low": 2598, "close": 2620},
            # Bullish impulse after OB
            {"time": "2026-01-13 08:10", "open": 2620, "high": 2630, "low": 2618, "close": 2628},
        ])
        ctx = ConfluenceContext(bars=bars, idx=2, direction="BUY", lookback=20)
        assert ob_present(ctx) is True

    def test_bullish_ob_does_not_satisfy_sell(self):
        """Bullish OB pattern should not satisfy SELL → False."""
        bars = _make_bars([
            {"time": "2026-01-13 08:00", "open": 2610, "high": 2612, "low": 2598, "close": 2600},
            {"time": "2026-01-13 08:05", "open": 2600, "high": 2625, "low": 2598, "close": 2620},
            {"time": "2026-01-13 08:10", "open": 2620, "high": 2630, "low": 2618, "close": 2628},
        ])
        ctx = ConfluenceContext(bars=bars, idx=2, direction="SELL", lookback=20)
        assert ob_present(ctx) is False

    def test_bearish_ob_satisfies_sell(self):
        """Last bullish->bearish transition within lookback → True for SELL."""
        bars = _make_bars([
            {"time": "2026-01-13 08:00", "open": 2600, "high": 2640, "low": 2598, "close": 2638},
            # OB bar: bullish (close 2638 > open 2600)
            {"time": "2026-01-13 08:05", "open": 2638, "high": 2640, "low": 2600, "close": 2605},
            # Bearish impulse after OB
            {"time": "2026-01-13 08:10", "open": 2605, "high": 2607, "low": 2590, "close": 2592},
        ])
        ctx = ConfluenceContext(bars=bars, idx=2, direction="SELL", lookback=20)
        assert ob_present(ctx) is True

    def test_bearish_ob_does_not_satisfy_buy(self):
        """Bearish OB pattern should not satisfy BUY → False."""
        bars = _make_bars([
            {"time": "2026-01-13 08:00", "open": 2600, "high": 2640, "low": 2598, "close": 2638},
            {"time": "2026-01-13 08:05", "open": 2638, "high": 2640, "low": 2600, "close": 2605},
            {"time": "2026-01-13 08:10", "open": 2605, "high": 2607, "low": 2590, "close": 2592},
        ])
        ctx = ConfluenceContext(bars=bars, idx=2, direction="BUY", lookback=20)
        assert ob_present(ctx) is False

    def test_no_ob_present(self):
        """All doji / same-direction candles → no OB pattern → False."""
        bars = _make_bars([
            # All bullish candles — no bearish->bullish transition for BUY OB
            {"time": "2026-01-13 08:00", "open": 2600, "high": 2605, "low": 2598, "close": 2604},
            {"time": "2026-01-13 08:05", "open": 2604, "high": 2609, "low": 2602, "close": 2608},
            {"time": "2026-01-13 08:10", "open": 2608, "high": 2613, "low": 2606, "close": 2612},
        ])
        ctx = ConfluenceContext(bars=bars, idx=2, direction="BUY", lookback=20)
        assert ob_present(ctx) is False


# ---------------------------------------------------------------------------
# 6. volume_anomaly
# ---------------------------------------------------------------------------

class TestVolumeAnomaly:
    """volume_anomaly(ctx) -> bool.
    True iff bars.volume[idx] > vol_mult * mean(volume[idx-lookback:idx]).
    """

    def test_volume_spike_above_mult(self):
        """Current bar volume (500) >> mean of prior 5 bars (100) * 1.5 → True."""
        rows = []
        for _ in range(5):
            rows.append({"open": 2600, "high": 2605, "low": 2598, "close": 2603, "volume": 100})
        rows.append({"open": 2603, "high": 2620, "low": 2600, "close": 2618, "volume": 500})
        bars = _make_bars(rows)
        ctx = ConfluenceContext(bars=bars, idx=5, direction="BUY", lookback=5, vol_mult=1.5)
        assert volume_anomaly(ctx) is True

    def test_volume_normal_not_spike(self):
        """Current bar volume (110) not > mean(100) * 1.5=150 → False."""
        rows = []
        for _ in range(5):
            rows.append({"open": 2600, "high": 2605, "low": 2598, "close": 2603, "volume": 100})
        rows.append({"open": 2603, "high": 2608, "low": 2601, "close": 2606, "volume": 110})
        bars = _make_bars(rows)
        ctx = ConfluenceContext(bars=bars, idx=5, direction="BUY", lookback=5, vol_mult=1.5)
        assert volume_anomaly(ctx) is False

    def test_insufficient_history_returns_false(self):
        """If idx < lookback (no prior bars), return False."""
        bars = _make_bars([
            {"open": 2600, "high": 2620, "low": 2598, "close": 2618, "volume": 999},
        ])
        ctx = ConfluenceContext(bars=bars, idx=0, direction="BUY", lookback=20, vol_mult=1.5)
        assert volume_anomaly(ctx) is False

    def test_exact_at_mult_boundary_not_spike(self):
        """volume == vol_mult * mean is NOT strictly greater → False."""
        rows = []
        for _ in range(4):
            rows.append({"open": 2600, "high": 2605, "low": 2598, "close": 2603, "volume": 100})
        # mean = 100, vol_mult=1.5, threshold=150; volume exactly 150 → not > 150
        rows.append({"open": 2603, "high": 2608, "low": 2601, "close": 2606, "volume": 150})
        bars = _make_bars(rows)
        ctx = ConfluenceContext(bars=bars, idx=4, direction="BUY", lookback=4, vol_mult=1.5)
        assert volume_anomaly(ctx) is False


# ---------------------------------------------------------------------------
# 7. dxy_inverse_correlation_holding
# ---------------------------------------------------------------------------

class TestDxyInverseCorrelation:
    """dxy_inverse_correlation_holding must raise ConfluenceUnavailable.
    It must NOT appear in SEARCHABLE.
    """

    def test_raises_confluence_unavailable(self):
        bars = _make_bars([{"open": 2600, "high": 2605, "low": 2598, "close": 2603}])
        ctx = ConfluenceContext(bars=bars, idx=0, direction="BUY")
        with pytest.raises(ConfluenceUnavailable):
            dxy_inverse_correlation_holding(ctx)

    def test_not_in_searchable(self):
        assert "dxy_inverse_correlation_holding" not in SEARCHABLE

    def test_in_detectors(self):
        """dxy_inverse_correlation_holding IS in DETECTORS (for documentation)."""
        assert "dxy_inverse_correlation_holding" in DETECTORS

    def test_searchable_has_six_entries(self):
        """SEARCHABLE must have exactly 6 entries (all detectors except dxy)."""
        assert len(SEARCHABLE) == 6

    def test_all_six_searchable_names_valid(self):
        """Every name in SEARCHABLE must be a key in DETECTORS."""
        for name in SEARCHABLE:
            assert name in DETECTORS


# ---------------------------------------------------------------------------
# 8. evaluate / all_met / count_met
# ---------------------------------------------------------------------------

class TestAggregators:
    """Test the three aggregator functions with a mix of True/False detectors."""

    def _ctx_with_volume_spike(self) -> ConfluenceContext:
        """Context that makes volume_anomaly=True and session_killzone=True.
        Other detectors will return False (no htf_bars, no sweep, no FVG, no OB).
        """
        # 5 quiet bars then 1 spike bar; time in London killzone (08:05)
        rows = []
        for _ in range(5):
            rows.append({"open": 2600, "high": 2605, "low": 2598, "close": 2603, "volume": 100})
        rows.append({"open": 2603, "high": 2620, "low": 2600, "close": 2618, "volume": 500})
        bars = _make_bars(rows, base_time="2026-01-13 08:00:00")
        return ConfluenceContext(
            bars=bars,
            idx=5,
            direction="BUY",
            htf_bars=None,
            killzone_windows=((8.0, 10.0), (13.5, 15.5)),
            lookback=5,
            vol_mult=1.5,
            fvg_lookback=10,
            sweep_lookback=20,
        )

    def test_evaluate_returns_dict_of_all_names(self):
        ctx = self._ctx_with_volume_spike()
        names = ["volume_anomaly", "session_killzone"]
        result = evaluate(names, ctx)
        assert set(result.keys()) == set(names)
        assert result["volume_anomaly"] is True
        assert result["session_killzone"] is True

    def test_evaluate_raises_for_dxy(self):
        ctx = self._ctx_with_volume_spike()
        with pytest.raises(ConfluenceUnavailable):
            evaluate(["volume_anomaly", "dxy_inverse_correlation_holding"], ctx)

    def test_all_met_true_when_all_true(self):
        ctx = self._ctx_with_volume_spike()
        assert all_met(["volume_anomaly", "session_killzone"], ctx) is True

    def test_all_met_false_when_one_false(self):
        ctx = self._ctx_with_volume_spike()
        # htf_bias requires htf_bars which is None → False
        assert all_met(["volume_anomaly", "htf_bias"], ctx) is False

    def test_all_met_empty_list_returns_true(self):
        """Vacuously true when no confluences required."""
        ctx = self._ctx_with_volume_spike()
        assert all_met([], ctx) is True

    def test_count_met_counts_true_only(self):
        ctx = self._ctx_with_volume_spike()
        # volume_anomaly=True, session_killzone=True, htf_bias=False
        assert count_met(["volume_anomaly", "session_killzone", "htf_bias"], ctx) == 2

    def test_count_met_zero_when_none_met(self):
        ctx = self._ctx_with_volume_spike()
        assert count_met(["htf_bias"], ctx) == 0

    def test_count_met_empty_list_returns_zero(self):
        ctx = self._ctx_with_volume_spike()
        assert count_met([], ctx) == 0


# ---------------------------------------------------------------------------
# 9. No-look-ahead invariant
# ---------------------------------------------------------------------------

class TestNoLookAhead:
    """Appending future bars after idx must NOT change any detector's result at idx."""

    def _base_bars(self) -> pd.DataFrame:
        """5 bars with volume spike at idx=4 (volume=500) and bullish FVG at 2-4."""
        rows = [
            # bars 0-1 for FVG setup
            {"time": "2026-01-13 08:00", "open": 2590, "high": 2595, "low": 2588, "close": 2593, "volume": 100},
            {"time": "2026-01-13 08:05", "open": 2593, "high": 2598, "low": 2591, "close": 2596, "volume": 100},
            # FVG bar (idx 2): high[0]=2595 < low[2]=2605
            {"time": "2026-01-13 08:10", "open": 2605, "high": 2620, "low": 2605, "close": 2618, "volume": 100},
            {"time": "2026-01-13 08:15", "open": 2618, "high": 2622, "low": 2616, "close": 2620, "volume": 100},
            # idx=4: volume spike
            {"time": "2026-01-13 08:20", "open": 2620, "high": 2640, "low": 2618, "close": 2638, "volume": 500},
        ]
        return _make_bars(rows)

    def _append_future_bar(self, bars: pd.DataFrame) -> pd.DataFrame:
        """Append a bar after idx=4 that has different price action."""
        future = pd.DataFrame([{
            "time":   _ts("2026-01-13 08:25"),
            "open":   2638.0, "high": 2660.0, "low": 2636.0, "close": 2658.0,
            "volume": 999.0, "spread": 0.30,
        }])
        return pd.concat([bars, future], ignore_index=True)

    def test_volume_anomaly_no_lookahead(self):
        bars = self._base_bars()
        ctx = ConfluenceContext(bars=bars, idx=4, direction="BUY", lookback=4, vol_mult=1.5)
        result_before = volume_anomaly(ctx)

        bars_extended = self._append_future_bar(bars)
        ctx_ext = ConfluenceContext(bars=bars_extended, idx=4, direction="BUY", lookback=4, vol_mult=1.5)
        result_after = volume_anomaly(ctx_ext)

        assert result_before == result_after

    def test_fvg_present_no_lookahead(self):
        bars = self._base_bars()
        ctx = ConfluenceContext(bars=bars, idx=2, direction="BUY", fvg_lookback=10)
        result_before = fvg_present(ctx)

        bars_extended = self._append_future_bar(bars)
        ctx_ext = ConfluenceContext(bars=bars_extended, idx=2, direction="BUY", fvg_lookback=10)
        result_after = fvg_present(ctx_ext)

        assert result_before == result_after

    def test_session_killzone_no_lookahead(self):
        bars = self._base_bars()
        ctx = ConfluenceContext(bars=bars, idx=4, direction="BUY")
        result_before = session_killzone(ctx)

        bars_extended = self._append_future_bar(bars)
        ctx_ext = ConfluenceContext(bars=bars_extended, idx=4, direction="BUY")
        result_after = session_killzone(ctx_ext)

        assert result_before == result_after

    def test_liquidity_sweep_no_lookahead(self):
        bars = self._base_bars()
        ctx = ConfluenceContext(bars=bars, idx=4, direction="BUY", sweep_lookback=20)
        result_before = liquidity_sweep(ctx)

        bars_extended = self._append_future_bar(bars)
        ctx_ext = ConfluenceContext(bars=bars_extended, idx=4, direction="BUY", sweep_lookback=20)
        result_after = liquidity_sweep(ctx_ext)

        assert result_before == result_after

    def test_ob_present_no_lookahead(self):
        bars = self._base_bars()
        ctx = ConfluenceContext(bars=bars, idx=4, direction="BUY", lookback=20)
        result_before = ob_present(ctx)

        bars_extended = self._append_future_bar(bars)
        ctx_ext = ConfluenceContext(bars=bars_extended, idx=4, direction="BUY", lookback=20)
        result_after = ob_present(ctx_ext)

        assert result_before == result_after
