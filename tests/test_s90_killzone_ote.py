"""
test_s90_killzone_ote.py
------------------------
TDD tests for Killzone OTE strategy (Task 5b).
All tests use synthetic tz-naive UTC bars — NO real data, NO disk I/O.
"""
from __future__ import annotations
import pandas as pd
import pytest
from strategies.xauusd_strategies.s90_killzone_ote import (
    is_killzone,
    get_htf_bias,
    get_swing_leg,
    get_ote_zone,
    find_confluence,
    generate_signals,
)


def _ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s)  # tz-naive UTC


class TestIsKillzone:
    def test_london_start_boundary(self):
        assert is_killzone(_ts("2026-01-13 08:00:00")) is True

    def test_london_inside(self):
        assert is_killzone(_ts("2026-01-13 09:00:00")) is True

    def test_london_end_boundary_exclusive(self):
        # 10:00 is exclusive (window is [08:00, 10:00))
        assert is_killzone(_ts("2026-01-13 10:00:00")) is False

    def test_ny_start_boundary(self):
        assert is_killzone(_ts("2026-01-13 13:30:00")) is True

    def test_ny_inside(self):
        assert is_killzone(_ts("2026-01-13 14:30:00")) is True

    def test_ny_end_boundary_exclusive(self):
        # 15:30 is exclusive
        assert is_killzone(_ts("2026-01-13 15:30:00")) is False

    def test_outside_both_windows(self):
        assert is_killzone(_ts("2026-01-13 12:00:00")) is False

    def test_weekend_is_excluded(self):
        # Saturday 2026-01-10
        assert is_killzone(_ts("2026-01-10 09:00:00")) is False


# ---------------------------------------------------------------------------
# Task 2: HTF Bias Detection
# ---------------------------------------------------------------------------

class TestHtfBias:
    """get_htf_bias(h1_bars, as_of_time) returns 'bullish', 'bearish', or 'neutral'.
    Only 1H bars with time <= as_of_time are considered (closed bars).
    """

    def _h1(self, rows):
        """Build a 1H bar DataFrame with tz-naive UTC timestamps."""
        data = []
        for r in rows:
            data.append({
                "time":   pd.Timestamp(r["time"]),
                "open":   float(r["open"]),
                "high":   float(r["high"]),
                "low":    float(r["low"]),
                "close":  float(r["close"]),
                "volume": 100.0,
                "spread": 0.30,
            })
        return pd.DataFrame(data)

    def test_bullish_bos(self):
        """Last bar closes above prior swing high → bullish BOS → bullish bias.

        as_of = 08:05 → floor = 08:00 → visible = bars with time < 08:00
        → bars at 05:00, 06:00, 07:00 are all visible (fully closed).
        07:00 bar close=2622 > prior max high=2620 → BOS bullish.
        """
        bars = self._h1([
            {"time": "2026-01-13 05:00", "open": 2600, "high": 2620, "low": 2595, "close": 2610},
            {"time": "2026-01-13 06:00", "open": 2610, "high": 2615, "low": 2600, "close": 2605},
            {"time": "2026-01-13 07:00", "open": 2605, "high": 2625, "low": 2600, "close": 2622},
        ])
        as_of = pd.Timestamp("2026-01-13 08:05:00")  # floor=08:00; bars < 08:00 visible
        assert get_htf_bias(bars, as_of) == "bullish"

    def test_bearish_bos(self):
        """Last bar closes below prior swing low → bearish BOS → bearish bias.

        as_of = 08:05 → floor = 08:00 → bars at 05:00, 06:00, 07:00 visible.
        07:00 bar close=2591 < prior min low=2595 → BOS bearish.
        """
        bars = self._h1([
            {"time": "2026-01-13 05:00", "open": 2610, "high": 2620, "low": 2595, "close": 2600},
            {"time": "2026-01-13 06:00", "open": 2600, "high": 2608, "low": 2596, "close": 2602},
            {"time": "2026-01-13 07:00", "open": 2602, "high": 2604, "low": 2590, "close": 2591},
        ])
        as_of = pd.Timestamp("2026-01-13 08:05:00")
        assert get_htf_bias(bars, as_of) == "bearish"

    def test_neutral_when_no_bos(self):
        """No break of structure → neutral."""
        bars = self._h1([
            {"time": "2026-01-13 05:00", "open": 2600, "high": 2620, "low": 2595, "close": 2610},
            {"time": "2026-01-13 06:00", "open": 2610, "high": 2615, "low": 2600, "close": 2606},
            {"time": "2026-01-13 07:00", "open": 2606, "high": 2613, "low": 2601, "close": 2607},
            # inside range, no BOS
        ])
        as_of = pd.Timestamp("2026-01-13 08:05:00")
        assert get_htf_bias(bars, as_of) == "neutral"

    def test_future_bars_excluded(self):
        """Bars starting at or after the current 1H period must not be visible.

        as_of = 07:30 → floor = 07:00 → visible = bars with time < 07:00
        → only bars at 05:00 and 06:00 are visible.
        The bar at 09:00 (would signal BOS) is not seen → neutral.
        """
        bars = self._h1([
            {"time": "2026-01-13 05:00", "open": 2600, "high": 2620, "low": 2595, "close": 2610},
            {"time": "2026-01-13 06:00", "open": 2610, "high": 2615, "low": 2600, "close": 2606},
            # This future bar would signal bullish BOS — must NOT be seen
            {"time": "2026-01-13 09:00", "open": 2606, "high": 2630, "low": 2600, "close": 2628},
        ])
        as_of = pd.Timestamp("2026-01-13 07:30:00")  # floor=07:00; bars < 07:00 visible
        # Only bars at 05:00 and 06:00 are visible; close[06:00]=2606 not > max_high=2620 → neutral
        assert get_htf_bias(bars, as_of) == "neutral"

    def test_insufficient_bars_returns_neutral(self):
        """Fewer than 2 closed 1H bars → neutral (not enough history).

        as_of = 08:05 → floor = 08:00 → only bar at 07:00 visible (1 bar < threshold).
        """
        bars = self._h1([
            {"time": "2026-01-13 07:00", "open": 2600, "high": 2625, "low": 2595, "close": 2622},
        ])
        as_of = pd.Timestamp("2026-01-13 08:05:00")
        assert get_htf_bias(bars, as_of) == "neutral"


# ---------------------------------------------------------------------------
# Task 3: Swing Detection and OTE Zone
# ---------------------------------------------------------------------------

class TestSwingAndOte:
    """get_swing_leg returns (leg_low, leg_high) for the most recent impulse.
    get_ote_zone returns (ote_low, ote_high) from those levels."""

    def _h1(self, rows):
        data = []
        for r in rows:
            data.append({
                "time":   pd.Timestamp(r["time"]),
                "open":   float(r["open"]),
                "high":   float(r["high"]),
                "low":    float(r["low"]),
                "close":  float(r["close"]),
                "volume": 100.0,
                "spread": 0.30,
            })
        return pd.DataFrame(data)

    def test_bullish_swing_leg(self):
        """Bullish: leg_low is the swing low, leg_high is the subsequent swing high.

        as_of = 07:05 → floor = 07:00 → bars with time < 07:00 visible
        → bars at 03:00, 04:00, 05:00, 06:00 are all visible.
        Swing low at idx=1 (04:00, low=2592); max high after = max(2640, 2638) = 2640.
        """
        bars = self._h1([
            {"time": "2026-01-13 03:00", "open": 2620, "high": 2625, "low": 2595, "close": 2600},
            {"time": "2026-01-13 04:00", "open": 2600, "high": 2606, "low": 2592, "close": 2598},
            # swing low bar: lowest low = 2592
            {"time": "2026-01-13 05:00", "open": 2598, "high": 2640, "low": 2597, "close": 2635},
            # swing high bar: highest high = 2640
            {"time": "2026-01-13 06:00", "open": 2635, "high": 2638, "low": 2620, "close": 2625},
        ])
        as_of = pd.Timestamp("2026-01-13 07:05:00")  # floor=07:00; bars < 07:00 visible
        leg_low, leg_high = get_swing_leg(bars, "bullish", as_of)
        assert leg_low  == pytest.approx(2592, abs=0.01)
        assert leg_high == pytest.approx(2640, abs=0.01)

    def test_bearish_swing_leg(self):
        """Bearish: leg_high is the swing high, leg_low is the subsequent swing low.

        as_of = 06:05 → floor = 06:00 → bars with time < 06:00 visible
        → bars at 03:00, 04:00, 05:00 visible.
        """
        bars = self._h1([
            {"time": "2026-01-13 03:00", "open": 2600, "high": 2650, "low": 2598, "close": 2645},
            # swing high bar: highest high = 2650
            {"time": "2026-01-13 04:00", "open": 2645, "high": 2648, "low": 2610, "close": 2615},
            {"time": "2026-01-13 05:00", "open": 2615, "high": 2618, "low": 2580, "close": 2585},
            # swing low bar: lowest low = 2580
        ])
        as_of = pd.Timestamp("2026-01-13 06:05:00")  # floor=06:00; bars < 06:00 visible
        leg_low, leg_high = get_swing_leg(bars, "bearish", as_of)
        assert leg_high == pytest.approx(2650, abs=0.01)
        assert leg_low  == pytest.approx(2580, abs=0.01)

    def test_get_swing_leg_returns_none_on_insufficient_bars(self):
        """Fewer than 3 visible bars → return None (can't identify a swing).

        as_of = 07:05 → floor = 07:00 → bars with time < 07:00 visible
        → only bars at 05:00 and 06:00 (2 bars < 3 minimum).
        """
        bars = self._h1([
            {"time": "2026-01-13 05:00", "open": 2600, "high": 2640, "low": 2592, "close": 2635},
            {"time": "2026-01-13 06:00", "open": 2635, "high": 2638, "low": 2620, "close": 2625},
        ])
        as_of = pd.Timestamp("2026-01-13 07:05:00")  # floor=07:00; bars < 07:00 → 2 bars
        result = get_swing_leg(bars, "bullish", as_of)
        assert result is None

    def test_ote_zone_bullish(self):
        """OTE zone for bullish leg (leg_low=2592, leg_high=2640):
        range = 48. ote_low = 2640 - 0.79*48 = 2601.92. ote_high = 2640 - 0.62*48 = 2610.24."""
        low, high = get_ote_zone(2592.0, 2640.0, "bullish")
        assert low  == pytest.approx(2640 - 0.79 * 48, abs=0.01)
        assert high == pytest.approx(2640 - 0.62 * 48, abs=0.01)

    def test_ote_zone_bearish(self):
        """OTE zone for bearish leg (leg_high=2650, leg_low=2580):
        range = 70. ote_low = 2580 + 0.62*70 = 2623.4. ote_high = 2580 + 0.79*70 = 2635.3."""
        low, high = get_ote_zone(2580.0, 2650.0, "bearish")
        assert low  == pytest.approx(2580 + 0.62 * 70, abs=0.01)
        assert high == pytest.approx(2580 + 0.79 * 70, abs=0.01)


# ---------------------------------------------------------------------------
# Task 4: Confluence Detection (FVG + Order Block)
# ---------------------------------------------------------------------------

class TestConfluence:
    """find_confluence(h1_bars, as_of_time, ote_low, ote_high, bias) → bool."""

    def _h1(self, rows):
        data = []
        for r in rows:
            data.append({
                "time":   pd.Timestamp(r["time"]),
                "open":   float(r["open"]),
                "high":   float(r["high"]),
                "low":    float(r["low"]),
                "close":  float(r["close"]),
                "volume": 100.0,
                "spread": 0.30,
            })
        return pd.DataFrame(data)

    def test_bullish_fvg_overlaps_ote(self):
        """Bullish FVG at bar i: high[i-2] < low[i]; FVG range overlaps OTE zone → True.

        as_of = 08:05 → floor = 08:00 → bars at 05:00, 06:00, 07:00 visible (time < 08:00).
        FVG: high[0]=2595 < low[2]=2605 → FVG=[2595, 2605], OTE=[2598, 2608] → overlap.
        """
        bars = self._h1([
            {"time": "2026-01-13 05:00", "open": 2590, "high": 2595, "low": 2588, "close": 2593},
            {"time": "2026-01-13 06:00", "open": 2593, "high": 2598, "low": 2591, "close": 2596},
            {"time": "2026-01-13 07:00", "open": 2605, "high": 2620, "low": 2605, "close": 2618},
        ])
        as_of = pd.Timestamp("2026-01-13 08:05:00")
        assert find_confluence(bars, as_of, 2598.0, 2608.0, "bullish") is True

    def test_bullish_fvg_does_not_overlap_ote(self):
        """Bullish FVG exists but its range doesn't touch the OTE zone → False.

        FVG=[2595, 2605], OTE=[2610, 2620] → no overlap.
        """
        bars = self._h1([
            {"time": "2026-01-13 05:00", "open": 2590, "high": 2595, "low": 2588, "close": 2593},
            {"time": "2026-01-13 06:00", "open": 2593, "high": 2598, "low": 2591, "close": 2596},
            {"time": "2026-01-13 07:00", "open": 2605, "high": 2620, "low": 2605, "close": 2618},
        ])
        as_of = pd.Timestamp("2026-01-13 08:05:00")
        assert find_confluence(bars, as_of, 2610.0, 2620.0, "bullish") is False

    def test_bearish_fvg_overlaps_ote(self):
        """Bearish FVG at bar i: low[i-2] > high[i]; FVG overlaps OTE zone → True.

        as_of = 08:05 → floor = 08:00 → bars at 05:00, 06:00, 07:00 visible.
        low[0]=2640, high[2]=2630; FVG gap=[2630, 2640], OTE=[2628, 2638] → overlap.
        """
        bars = self._h1([
            {"time": "2026-01-13 05:00", "open": 2645, "high": 2650, "low": 2640, "close": 2642},
            {"time": "2026-01-13 06:00", "open": 2642, "high": 2644, "low": 2635, "close": 2637},
            {"time": "2026-01-13 07:00", "open": 2632, "high": 2630, "low": 2615, "close": 2618},
        ])
        as_of = pd.Timestamp("2026-01-13 08:05:00")
        assert find_confluence(bars, as_of, 2628.0, 2638.0, "bearish") is True

    def test_order_block_overlaps_ote(self):
        """Bearish OB (last bullish candle before impulse) overlaps OTE → True.

        as_of = 07:05 → floor = 07:00 → bars at 04:00, 05:00, 06:00 visible.
        FVG check: low[0]=2638 > high[2]=2610 → FVG gap=[2610,2638], OTE=[2642,2648] → no overlap.
        OB check: last bullish = bar at 04:00, range [open=2640, close=2650] overlaps [2642,2648] → True.
        """
        bars = self._h1([
            # "OB" bar — bullish candle before the impulse
            {"time": "2026-01-13 04:00", "open": 2640, "high": 2652, "low": 2638, "close": 2650},
            # impulse starts here (strong bearish)
            {"time": "2026-01-13 05:00", "open": 2650, "high": 2652, "low": 2600, "close": 2605},
            {"time": "2026-01-13 06:00", "open": 2605, "high": 2610, "low": 2585, "close": 2590},
        ])
        as_of = pd.Timestamp("2026-01-13 07:05:00")  # floor=07:00; bars < 07:00 visible
        assert find_confluence(bars, as_of, 2642.0, 2648.0, "bearish") is True

    def test_no_confluence(self):
        """Neither FVG nor OB overlaps the OTE zone → False.

        as_of = 08:05 → floor = 08:00 → bars at 05:00, 06:00, 07:00 visible.
        """
        bars = self._h1([
            {"time": "2026-01-13 05:00", "open": 2600, "high": 2605, "low": 2598, "close": 2602},
            {"time": "2026-01-13 06:00", "open": 2602, "high": 2607, "low": 2600, "close": 2604},
            {"time": "2026-01-13 07:00", "open": 2604, "high": 2608, "low": 2601, "close": 2606},
        ])
        as_of = pd.Timestamp("2026-01-13 08:05:00")
        assert find_confluence(bars, as_of, 2650.0, 2660.0, "bullish") is False


# ---------------------------------------------------------------------------
# Task 5: Signal Generation (End-to-End OTE Setup)
# ---------------------------------------------------------------------------

class TestSignalGeneration:
    """
    generate_signals(bars_5m, bars_1h) → list[Signal]

    Each Signal must have:
        - entry_index = k+1 (one bar after the trigger bar k)
        - direction matching the 1H bias
        - sl beyond the swing origin (leg_low - SL_BUFFER for BUY)
        - tp at the swing extreme (leg_high for BUY)
    """

    def _make_5m(self, rows):
        data = []
        for r in rows:
            data.append({
                "time":   pd.Timestamp(r["time"]),
                "open":   float(r["open"]),
                "high":   float(r["high"]),
                "low":    float(r["low"]),
                "close":  float(r["close"]),
                "volume": 100.0,
                "spread": 0.30,
            })
        return pd.DataFrame(data)

    def _make_1h(self, rows):
        data = []
        for r in rows:
            data.append({
                "time":   pd.Timestamp(r["time"]),
                "open":   float(r["open"]),
                "high":   float(r["high"]),
                "low":    float(r["low"]),
                "close":  float(r["close"]),
                "volume": 1000.0,
                "spread": 0.30,
            })
        return pd.DataFrame(data)

    def test_textbook_bullish_ote_signal(self):
        """
        Textbook bullish OTE setup.

        1H setup:
            bar0: high=2570, low=2560; bar1: high=2568, low=2558
            bar2: impulse high=2640, low=2580
            bar3: BOS — close=2645 > prior_high=max(2570,2568,2640)=2640 → bullish

        Swing leg: swing low idx=1 (low=2558); max high after idx=1 = max(2640,2648)=2648
            leg_low=2558, leg_high=2648, range=90
            ote_low=2648-0.79*90=2576.9, ote_high=2648-0.62*90=2592.2

        FVG: high[0]=2570 < low[2]=2580 → FVG=[2570,2580] overlaps [2576.9,2592.2] ✓

        5M bar k=0 at 08:05 (London killzone), low=2578 is inside OTE zone [2576.9, 2592.2].
        Signal: direction='BUY', entry_index=1, sl=2558-0.5=2557.5, tp=2648
        """
        bars_1h = self._make_1h([
            {"time": "2026-01-13 04:00", "open": 2558, "high": 2570, "low": 2560, "close": 2562},
            {"time": "2026-01-13 05:00", "open": 2562, "high": 2568, "low": 2558, "close": 2565},
            {"time": "2026-01-13 06:00", "open": 2580, "high": 2640, "low": 2580, "close": 2635},
            {"time": "2026-01-13 07:00", "open": 2635, "high": 2648, "low": 2625, "close": 2645},
        ])

        bars_5m = self._make_5m([
            # bar 0 (k): during London killzone; low=2578 tags OTE zone [2576.9, 2592.2]
            {"time": "2026-01-13 08:05", "open": 2595, "high": 2597, "low": 2578, "close": 2582},
            # bar 1 (k+1): entry bar; open=2583 satisfies sl=2557.5 < 2583 < tp=2648
            {"time": "2026-01-13 08:10", "open": 2583, "high": 2640, "low": 2581, "close": 2638},
            # bar 2: unused in this assertion
            {"time": "2026-01-13 08:15", "open": 2638, "high": 2650, "low": 2630, "close": 2648},
        ])

        signals = generate_signals(bars_5m, bars_1h)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.direction == "BUY"
        assert sig.entry_index == 1       # k+1 where k=0
        assert sig.sl < float(bars_5m.iloc[1]["open"])   # sl below entry open
        assert sig.tp > float(bars_5m.iloc[1]["open"])   # tp above entry open

    def test_no_signal_outside_killzone(self):
        """No signal generated if the 5M trigger bar is outside killzone windows."""
        bars_1h = self._make_1h([
            {"time": "2026-01-13 04:00", "open": 2558, "high": 2570, "low": 2558, "close": 2562},
            {"time": "2026-01-13 05:00", "open": 2562, "high": 2568, "low": 2558, "close": 2565},
            {"time": "2026-01-13 06:00", "open": 2580, "high": 2640, "low": 2580, "close": 2635},
            {"time": "2026-01-13 07:00", "open": 2635, "high": 2648, "low": 2625, "close": 2645},
        ])
        bars_5m = self._make_5m([
            # 12:00 UTC — between London close (10:00) and NY open (13:30): outside killzone
            {"time": "2026-01-13 12:00", "open": 2595, "high": 2597, "low": 2578, "close": 2582},
            {"time": "2026-01-13 12:05", "open": 2583, "high": 2640, "low": 2581, "close": 2638},
        ])
        signals = generate_signals(bars_5m, bars_1h)
        assert len(signals) == 0

    def test_no_signal_when_price_not_in_ote_zone(self):
        """When 5M bar price does not tag the OTE zone, no signal fires."""
        bars_1h = self._make_1h([
            {"time": "2026-01-13 04:00", "open": 2558, "high": 2570, "low": 2560, "close": 2562},
            {"time": "2026-01-13 05:00", "open": 2562, "high": 2572, "low": 2558, "close": 2565},
            # FVG: high[0]=2570 < low[2]=2573
            {"time": "2026-01-13 06:00", "open": 2573, "high": 2580, "low": 2573, "close": 2578},
            {"time": "2026-01-13 07:00", "open": 2578, "high": 2585, "low": 2573, "close": 2582},
        ])
        # swing low idx=1 (low=2558), leg_high=max(2580,2585)=2585
        # range=2585-2558=27; ote_low=2585-0.79*27=2563.7, ote_high=2585-0.62*27=2568.3
        bars_5m = self._make_5m([
            # bar k: during killzone, low=2576 > ote_high=2568.3 → NOT in OTE zone
            {"time": "2026-01-13 08:05", "open": 2578, "high": 2582, "low": 2576, "close": 2580},
            {"time": "2026-01-13 08:10", "open": 2580, "high": 2590, "low": 2578, "close": 2588},
        ])
        signals = generate_signals(bars_5m, bars_1h)
        assert len(signals) == 0

    def test_last_bar_cannot_generate_signal(self):
        """Bar k is the last bar in bars_5m; no k+1 exists → no signal."""
        bars_1h = self._make_1h([
            {"time": "2026-01-13 04:00", "open": 2558, "high": 2570, "low": 2558, "close": 2562},
            {"time": "2026-01-13 05:00", "open": 2562, "high": 2568, "low": 2558, "close": 2565},
            {"time": "2026-01-13 06:00", "open": 2580, "high": 2640, "low": 2580, "close": 2635},
            {"time": "2026-01-13 07:00", "open": 2635, "high": 2648, "low": 2625, "close": 2645},
        ])
        bars_5m = self._make_5m([
            # Single bar during killzone in OTE zone — but it's the last bar, no k+1
            {"time": "2026-01-13 08:05", "open": 2595, "high": 2597, "low": 2578, "close": 2582},
        ])
        signals = generate_signals(bars_5m, bars_1h)
        assert len(signals) == 0
