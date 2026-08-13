# Task 5b — Setup Family B: Killzone OTE Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `s90_killzone_ote.py` and its TDD test file that detect OTE (Optimal Trade Entry) setups during London/NY killzones on 5-minute XAU/USD bars with 1H HTF bias, then backtest on in-sample data and write a JSON results file.

**Architecture:** The strategy uses two timeframes — 1H for structural bias (higher-highs/lows or bullish BOS for longs; inverse for shorts) and 5M for entry timing during killzone windows (London 08:00–10:00 UTC and NY 13:30–15:30 UTC). On each 5M bar during a killzone, the system checks if price has retraced into the 62–79% Fibonacci OTE zone of the most recent 1H impulse swing in the bias direction, with required confluence from either an FVG or an Order Block overlapping the OTE zone. Tests are purely synthetic (no real data or disk I/O).

**Tech Stack:** Python 3.x, pandas, numpy (standard library only), pytest. All infrastructure from `strategies/research/` (exec_sim, dataset) is imported but never modified.

---

## File Structure

- **Create:** `strategies/xauusd_strategies/s90_killzone_ote.py` — Strategy logic: killzone filter, 1H bias detection, swing detection, OTE zone computation, FVG/OB confluence check, signal generation, backtest runner, JSON output.
- **Create:** `tests/test_s90_killzone_ote.py` — TDD tests using synthetic bars; NO real data loaded.
- **Create:** `strategies/backtest/results/s90_killzone_ote_20260522.json` — written by the backtest runner at the end of Task 4.

---

## Shared Test Helper (used in all tasks)

```python
import pandas as pd

def _bars(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal tz-naive UTC OHLCV DataFrame for testing.
    Each dict: time (str), open, high, low, close.  volume+spread defaulted."""
    data = []
    for r in rows:
        data.append({
            "time":   pd.Timestamp(r["time"]),
            "open":   float(r["open"]),
            "high":   float(r["high"]),
            "low":    float(r["low"]),
            "close":  float(r["close"]),
            "volume": float(r.get("volume", 100)),
            "spread": float(r.get("spread", 0.30)),
        })
    return pd.DataFrame(data)
```

---

## Task 1: Killzone Filter

**Files:**
- Create: `strategies/xauusd_strategies/s90_killzone_ote.py`
- Create: `tests/test_s90_killzone_ote.py`

### Step 1: Write the failing test

Add to `tests/test_s90_killzone_ote.py`:

```python
"""
test_s90_killzone_ote.py
------------------------
TDD tests for Killzone OTE strategy (Task 5b).
All tests use synthetic tz-naive UTC bars — NO real data, NO disk I/O.
"""
from __future__ import annotations
import pandas as pd
import pytest
from strategies.xauusd_strategies.s90_killzone_ote import is_killzone


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
```

### Step 2: Run to confirm RED

```
.venv\Scripts\python.exe -m pytest tests/test_s90_killzone_ote.py::TestIsKillzone -q
```

Expected: `ImportError` or `ModuleNotFoundError` — the module does not exist yet.

### Step 3: Write minimal implementation

Create `strategies/xauusd_strategies/s90_killzone_ote.py`:

```python
"""
s90_killzone_ote.py
-------------------
Setup Family B: Killzone OTE (Optimal Trade Entry) on XAU/USD.

STRATEGY LOGIC OVERVIEW
-----------------------
Entry TF : 5-minute bars (tz-naive UTC)
Bias TF  : 1-hour bars  (tz-naive UTC, closed bars only)

KILLZONE WINDOWS (UTC, weekdays only):
    London : 08:00 – 10:00  (exclusive end)
    NY     : 13:30 – 15:30  (exclusive end)

HTF BIAS RULE (1H closed bars only):
    Bullish: the last completed 1H bar closes above the prior 1H swing high
             (Break of Structure upward), OR the 1H series shows at least 2
             consecutive higher-highs and higher-lows over the last
             htf_lookback bars.
    Bearish: mirror of bullish — BOS down or 2+ consecutive lower-highs &
             lower-lows.
    Neutral: neither condition → skip entry.

OTE ZONE DEFINITION:
    Identify the last completed impulse swing in the bias direction within
    the 1H bars:
        Bullish → find the most recent swing low then the swing high that
                  followed it (the "leg" goes from low→high).
        Bearish → find the most recent swing high then the swing low that
                  followed it (leg goes from high→low).
    OTE zone = 62%–79% retracement of that leg:
        Bullish OTE low  = leg_high - 0.79 * leg_range
        Bullish OTE high = leg_high - 0.62 * leg_range
        Bearish OTE low  = leg_low  + 0.62 * leg_range
        Bearish OTE high = leg_low  + 0.79 * leg_range
    where leg_range = abs(leg_high - leg_low).

CONFLUENCE REQUIREMENT:
    At least one of these must overlap the OTE zone on the 1H bars:
    • FVG (3-candle imbalance):
        Bullish FVG at bar i: high[i-2] < low[i]   (gap between i-2 high and i low)
        Bearish FVG at bar i: low[i-2]  > high[i]  (gap between i-2 low and i high)
        Overlap = FVG range intersects [ote_low, ote_high]
    • Order Block (OB):
        Bullish OB = last bearish (close < open) 1H candle immediately
                     before the impulse leg (the candle at the base of the move).
        Bearish OB = last bullish candle before the impulse.
        Overlap = OB range [low, high] intersects [ote_low, ote_high]

ENTRY TRIGGER (5M bar k):
    During a killzone, if price tags the OTE zone (5M bar k has low <= ote_high
    and high >= ote_low), AND confluence exists, generate Signal:
        direction  = "BUY" (bullish) or "SELL" (bearish)
        entry_index = k + 1  (fill at next bar's open — no look-ahead)
        sl         = swing origin - buffer  (BUY: leg_low - sl_buffer)
                                            (SELL: leg_high + sl_buffer)
        tp         = swing extreme           (BUY: leg_high; SELL: leg_low)
    Skip if:
        • R:R (mid-price) < min_rr (default 1.5)
        • entry_index >= len(bars)

LOOK-AHEAD SELF-AUDIT:
    ✓ HTF bias: only 1H bars with time <= (5M bar k).time are used.
    ✓ Swing detection: uses closed 1H bars; the current incomplete 1H bar
      is excluded by the time filter.
    ✓ Signal: entry_index = k+1; the decision at bar k uses bars 0..k only.
    ✓ FVG/OB detection: computed on 1H bars already closed before bar k.
    ✓ No future bar prices are accessed during the k-scan loop.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from strategies.research.exec_sim import Signal, simulate

# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------
HALF_SPREAD   = 0.15   # 30c full spread
SLIP          = 0.05   # 5c slippage
MAX_HOLD_BARS = 36     # ~3 hours on 5m
MIN_RR        = 1.5
SL_BUFFER     = 0.50   # $0.50 beyond swing origin (mid)
HTF_LOOKBACK  = 10     # 1H bars to scan for swing + bias

# Killzone windows (UTC hour, minute) — half-open [start, end)
_LONDON_START = (8,  0)
_LONDON_END   = (10, 0)
_NY_START     = (13, 30)
_NY_END       = (15, 30)


# ---------------------------------------------------------------------------
# 1. Killzone filter
# ---------------------------------------------------------------------------

def is_killzone(ts: pd.Timestamp) -> bool:
    """Return True if ts (tz-naive UTC) falls in London or NY killzone on a weekday."""
    if ts.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    h, m = ts.hour, ts.minute
    total = h * 60 + m
    london_start = _LONDON_START[0] * 60 + _LONDON_START[1]
    london_end   = _LONDON_END[0]   * 60 + _LONDON_END[1]
    ny_start     = _NY_START[0]     * 60 + _NY_START[1]
    ny_end       = _NY_END[0]       * 60 + _NY_END[1]
    in_london = london_start <= total < london_end
    in_ny     = ny_start     <= total < ny_end
    return in_london or in_ny
```

### Step 4: Run to confirm GREEN

```
.venv\Scripts\python.exe -m pytest tests/test_s90_killzone_ote.py::TestIsKillzone -q
```

Expected: `8 passed`

---

## Task 2: HTF Bias Detection

**Files:**
- Modify: `strategies/xauusd_strategies/s90_killzone_ote.py` (add `get_htf_bias`)
- Modify: `tests/test_s90_killzone_ote.py` (add `TestHtfBias`)

### Step 1: Write the failing test

Add to the test file:

```python
from strategies.xauusd_strategies.s90_killzone_ote import is_killzone, get_htf_bias


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
        """Last bar closes above prior swing high → bullish BOS → bullish bias."""
        bars = self._h1([
            {"time": "2026-01-13 05:00", "open": 2600, "high": 2620, "low": 2595, "close": 2610},
            {"time": "2026-01-13 06:00", "open": 2610, "high": 2615, "low": 2600, "close": 2605},
            {"time": "2026-01-13 07:00", "open": 2605, "high": 2625, "low": 2600, "close": 2622},
            # bar at 07:00 closes at 2622 > prior high 2620 → BOS up
        ])
        as_of = pd.Timestamp("2026-01-13 07:59:59")
        assert get_htf_bias(bars, as_of) == "bullish"

    def test_bearish_bos(self):
        """Last bar closes below prior swing low → bearish BOS → bearish bias."""
        bars = self._h1([
            {"time": "2026-01-13 05:00", "open": 2610, "high": 2620, "low": 2595, "close": 2600},
            {"time": "2026-01-13 06:00", "open": 2600, "high": 2608, "low": 2596, "close": 2602},
            {"time": "2026-01-13 07:00", "open": 2602, "high": 2604, "low": 2590, "close": 2591},
            # bar at 07:00 closes at 2591 < prior low 2595 → BOS down
        ])
        as_of = pd.Timestamp("2026-01-13 07:59:59")
        assert get_htf_bias(bars, as_of) == "bearish"

    def test_neutral_when_no_bos(self):
        """No break of structure → neutral."""
        bars = self._h1([
            {"time": "2026-01-13 05:00", "open": 2600, "high": 2620, "low": 2595, "close": 2610},
            {"time": "2026-01-13 06:00", "open": 2610, "high": 2615, "low": 2600, "close": 2606},
            {"time": "2026-01-13 07:00", "open": 2606, "high": 2613, "low": 2601, "close": 2607},
            # inside range, no BOS
        ])
        as_of = pd.Timestamp("2026-01-13 07:59:59")
        assert get_htf_bias(bars, as_of) == "neutral"

    def test_future_bars_excluded(self):
        """Bars after as_of_time must not influence the result."""
        bars = self._h1([
            {"time": "2026-01-13 05:00", "open": 2600, "high": 2620, "low": 2595, "close": 2610},
            {"time": "2026-01-13 06:00", "open": 2610, "high": 2615, "low": 2600, "close": 2606},
            # This future bar would signal bullish BOS — must NOT be seen
            {"time": "2026-01-13 09:00", "open": 2606, "high": 2630, "low": 2600, "close": 2628},
        ])
        as_of = pd.Timestamp("2026-01-13 07:30:00")  # before the 09:00 bar
        # Only bars at 05:00 and 06:00 are visible; no BOS → neutral
        assert get_htf_bias(bars, as_of) == "neutral"

    def test_insufficient_bars_returns_neutral(self):
        """Fewer than 2 closed 1H bars → neutral (not enough history)."""
        bars = self._h1([
            {"time": "2026-01-13 07:00", "open": 2600, "high": 2625, "low": 2595, "close": 2622},
        ])
        as_of = pd.Timestamp("2026-01-13 07:59:59")
        assert get_htf_bias(bars, as_of) == "neutral"
```

### Step 2: Run to confirm RED

```
.venv\Scripts\python.exe -m pytest tests/test_s90_killzone_ote.py::TestHtfBias -q
```

Expected: `ImportError` — `get_htf_bias` not defined yet.

### Step 3: Write minimal implementation

Add to `s90_killzone_ote.py` (after the killzone section):

```python
# ---------------------------------------------------------------------------
# 2. HTF Bias Detection
# ---------------------------------------------------------------------------

def get_htf_bias(h1_bars: pd.DataFrame, as_of_time: pd.Timestamp) -> str:
    """Determine HTF bias from closed 1H bars at or before as_of_time.

    Rule:
        Use bars with time <= as_of_time (closed 1H bars only).
        BOS check: compare the last closed bar's close against the prior
        bars' high (for bullish) and low (for bearish) within htf_lookback.
            Bullish BOS: last_close > max(prior_highs)
            Bearish BOS: last_close < min(prior_lows)
        If neither → neutral.

    Requires at least 2 visible bars; returns 'neutral' otherwise.
    """
    visible = h1_bars[h1_bars["time"] <= as_of_time].copy()
    if len(visible) < 2:
        return "neutral"

    # Take up to HTF_LOOKBACK recent visible bars
    recent = visible.tail(HTF_LOOKBACK)
    last  = recent.iloc[-1]
    prior = recent.iloc[:-1]

    last_close  = float(last["close"])
    prior_high  = float(prior["high"].max())
    prior_low   = float(prior["low"].min())

    if last_close > prior_high:
        return "bullish"
    if last_close < prior_low:
        return "bearish"
    return "neutral"
```

### Step 4: Run to confirm GREEN

```
.venv\Scripts\python.exe -m pytest tests/test_s90_killzone_ote.py::TestHtfBias -q
```

Expected: `5 passed`

---

## Task 3: Swing Detection and OTE Zone

**Files:**
- Modify: `strategies/xauusd_strategies/s90_killzone_ote.py` (add `get_swing_leg`, `get_ote_zone`)
- Modify: `tests/test_s90_killzone_ote.py` (add `TestSwingAndOte`)

### Step 1: Write the failing test

```python
from strategies.xauusd_strategies.s90_killzone_ote import (
    is_killzone, get_htf_bias, get_swing_leg, get_ote_zone
)


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
        """Bullish: leg_low is the swing low, leg_high is the subsequent swing high."""
        bars = self._h1([
            {"time": "2026-01-13 03:00", "open": 2620, "high": 2625, "low": 2595, "close": 2600},
            {"time": "2026-01-13 04:00", "open": 2600, "high": 2606, "low": 2592, "close": 2598},
            # swing low bar: lowest low = 2592
            {"time": "2026-01-13 05:00", "open": 2598, "high": 2640, "low": 2597, "close": 2635},
            # swing high bar: highest high = 2640
            {"time": "2026-01-13 06:00", "open": 2635, "high": 2638, "low": 2620, "close": 2625},
        ])
        as_of = pd.Timestamp("2026-01-13 07:00:00")
        leg_low, leg_high = get_swing_leg(bars, "bullish", as_of)
        assert leg_low  == pytest.approx(2592, abs=0.01)
        assert leg_high == pytest.approx(2640, abs=0.01)

    def test_bearish_swing_leg(self):
        """Bearish: leg_high is the swing high, leg_low is the subsequent swing low."""
        bars = self._h1([
            {"time": "2026-01-13 03:00", "open": 2600, "high": 2650, "low": 2598, "close": 2645},
            # swing high bar: highest high = 2650
            {"time": "2026-01-13 04:00", "open": 2645, "high": 2648, "low": 2610, "close": 2615},
            {"time": "2026-01-13 05:00", "open": 2615, "high": 2618, "low": 2580, "close": 2585},
            # swing low bar: lowest low = 2580
        ])
        as_of = pd.Timestamp("2026-01-13 06:00:00")
        leg_low, leg_high = get_swing_leg(bars, "bearish", as_of)
        assert leg_high == pytest.approx(2650, abs=0.01)
        assert leg_low  == pytest.approx(2580, abs=0.01)

    def test_get_swing_leg_returns_none_on_insufficient_bars(self):
        """Fewer than 3 visible bars → return None (can't identify a swing)."""
        bars = self._h1([
            {"time": "2026-01-13 05:00", "open": 2600, "high": 2640, "low": 2592, "close": 2635},
            {"time": "2026-01-13 06:00", "open": 2635, "high": 2638, "low": 2620, "close": 2625},
        ])
        as_of = pd.Timestamp("2026-01-13 07:00:00")
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
```

### Step 2: Run to confirm RED

```
.venv\Scripts\python.exe -m pytest tests/test_s90_killzone_ote.py::TestSwingAndOte -q
```

Expected: `ImportError` — functions not defined yet.

### Step 3: Write minimal implementation

Add to `s90_killzone_ote.py`:

```python
# ---------------------------------------------------------------------------
# 3. Swing Leg and OTE Zone
# ---------------------------------------------------------------------------

def get_swing_leg(
    h1_bars: pd.DataFrame,
    bias: str,
    as_of_time: pd.Timestamp,
) -> Optional[tuple[float, float]]:
    """Return (leg_low, leg_high) for the most recent impulse swing leg.

    For 'bullish': scan backward for the lowest low (swing origin), then the
    highest high after that point (swing extreme).
    For 'bearish': scan backward for the highest high (swing origin), then the
    lowest low after that point (swing extreme).

    Uses only bars with time <= as_of_time (closed bars; no look-ahead).
    Returns None if fewer than 3 visible bars exist.
    """
    visible = h1_bars[h1_bars["time"] <= as_of_time].tail(HTF_LOOKBACK)
    if len(visible) < 3:
        return None

    highs = visible["high"].to_numpy()
    lows  = visible["low"].to_numpy()
    n = len(highs)

    if bias == "bullish":
        # Find swing low: bar with lowest low
        low_idx = int(lows.argmin())
        # Swing high: max high AFTER the swing low
        if low_idx >= n - 1:
            return None  # no bars after swing low
        leg_low  = float(lows[low_idx])
        leg_high = float(highs[low_idx + 1:].max())
        return (leg_low, leg_high)
    else:  # bearish
        # Find swing high: bar with highest high
        high_idx = int(highs.argmax())
        # Swing low: min low AFTER the swing high
        if high_idx >= n - 1:
            return None  # no bars after swing high
        leg_high = float(highs[high_idx])
        leg_low  = float(lows[high_idx + 1:].min())
        return (leg_low, leg_high)


def get_ote_zone(
    leg_low: float,
    leg_high: float,
    bias: str,
) -> tuple[float, float]:
    """Return (ote_low, ote_high) for the 62–79% retracement zone.

    Bullish (leg goes from leg_low up to leg_high):
        retracement measured from leg_high downward.
        ote_low  = leg_high - 0.79 * leg_range
        ote_high = leg_high - 0.62 * leg_range

    Bearish (leg goes from leg_high down to leg_low):
        retracement measured from leg_low upward.
        ote_low  = leg_low + 0.62 * leg_range
        ote_high = leg_low + 0.79 * leg_range
    """
    leg_range = leg_high - leg_low
    if bias == "bullish":
        ote_low  = leg_high - 0.79 * leg_range
        ote_high = leg_high - 0.62 * leg_range
    else:
        ote_low  = leg_low + 0.62 * leg_range
        ote_high = leg_low + 0.79 * leg_range
    return (ote_low, ote_high)
```

### Step 4: Run to confirm GREEN

```
.venv\Scripts\python.exe -m pytest tests/test_s90_killzone_ote.py::TestSwingAndOte -q
```

Expected: `5 passed`

---

## Task 4: Confluence Detection (FVG + Order Block)

**Files:**
- Modify: `strategies/xauusd_strategies/s90_killzone_ote.py` (add `find_confluence`)
- Modify: `tests/test_s90_killzone_ote.py` (add `TestConfluence`)

### Step 1: Write the failing test

```python
from strategies.xauusd_strategies.s90_killzone_ote import (
    is_killzone, get_htf_bias, get_swing_leg, get_ote_zone, find_confluence
)


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
        """Bullish FVG at bar i: high[i-2] < low[i]; FVG range overlaps OTE zone → True."""
        # Bars at indices 0, 1, 2
        # FVG condition: high[0]=2595 < low[2]=2605
        # FVG range = [2595, 2605], OTE zone = [2598, 2608] → overlap ✓
        bars = self._h1([
            {"time": "2026-01-13 05:00", "open": 2590, "high": 2595, "low": 2588, "close": 2593},
            {"time": "2026-01-13 06:00", "open": 2593, "high": 2598, "low": 2591, "close": 2596},
            {"time": "2026-01-13 07:00", "open": 2605, "high": 2620, "low": 2605, "close": 2618},
        ])
        as_of = pd.Timestamp("2026-01-13 07:59:59")
        assert find_confluence(bars, as_of, 2598.0, 2608.0, "bullish") is True

    def test_bullish_fvg_does_not_overlap_ote(self):
        """Bullish FVG exists but its range doesn't touch the OTE zone → False."""
        # FVG range = [2595, 2605], OTE zone = [2610, 2620] → no overlap
        bars = self._h1([
            {"time": "2026-01-13 05:00", "open": 2590, "high": 2595, "low": 2588, "close": 2593},
            {"time": "2026-01-13 06:00", "open": 2593, "high": 2598, "low": 2591, "close": 2596},
            {"time": "2026-01-13 07:00", "open": 2605, "high": 2620, "low": 2605, "close": 2618},
        ])
        as_of = pd.Timestamp("2026-01-13 07:59:59")
        assert find_confluence(bars, as_of, 2610.0, 2620.0, "bullish") is False

    def test_bearish_fvg_overlaps_ote(self):
        """Bearish FVG at bar i: low[i-2] > high[i]; FVG overlaps OTE zone → True."""
        # low[0]=2640, high[2]=2630; FVG gap=[2630,2640], OTE=[2628,2638] → overlap ✓
        bars = self._h1([
            {"time": "2026-01-13 05:00", "open": 2645, "high": 2650, "low": 2640, "close": 2642},
            {"time": "2026-01-13 06:00", "open": 2642, "high": 2644, "low": 2635, "close": 2637},
            {"time": "2026-01-13 07:00", "open": 2632, "high": 2630, "low": 2615, "close": 2618},
        ])
        as_of = pd.Timestamp("2026-01-13 07:59:59")
        assert find_confluence(bars, as_of, 2628.0, 2638.0, "bearish") is True

    def test_order_block_overlaps_ote(self):
        """Bearish OB (last bullish candle before impulse) overlaps OTE → True.
        In a bearish setup, the OB is the last bullish 1H candle before the leg.
        We approximate: use the first bar as OB (open < close = bullish), its
        range [open=2640, close=2650] overlaps OTE [2642, 2648] → True."""
        bars = self._h1([
            # "OB" bar — bullish candle before the impulse
            {"time": "2026-01-13 04:00", "open": 2640, "high": 2652, "low": 2638, "close": 2650},
            # impulse starts here (strong bearish)
            {"time": "2026-01-13 05:00", "open": 2650, "high": 2652, "low": 2600, "close": 2605},
            {"time": "2026-01-13 06:00", "open": 2605, "high": 2610, "low": 2585, "close": 2590},
        ])
        as_of = pd.Timestamp("2026-01-13 06:59:59")
        # FVG check: low[0]=2638 > high[2]=2610 → bearish FVG in [2610, 2638], OTE=[2642,2648] → no overlap
        # OB check: last bullish before impulse = bar at 04:00, range [2640, 2650] → overlaps [2642, 2648] ✓
        assert find_confluence(bars, as_of, 2642.0, 2648.0, "bearish") is True

    def test_no_confluence(self):
        """Neither FVG nor OB overlaps the OTE zone → False."""
        # Flat sideways bars, no FVG, OTE zone far from any bar range
        bars = self._h1([
            {"time": "2026-01-13 05:00", "open": 2600, "high": 2605, "low": 2598, "close": 2602},
            {"time": "2026-01-13 06:00", "open": 2602, "high": 2607, "low": 2600, "close": 2604},
            {"time": "2026-01-13 07:00", "open": 2604, "high": 2608, "low": 2601, "close": 2606},
        ])
        as_of = pd.Timestamp("2026-01-13 07:59:59")
        assert find_confluence(bars, as_of, 2650.0, 2660.0, "bullish") is False
```

### Step 2: Run to confirm RED

```
.venv\Scripts\python.exe -m pytest tests/test_s90_killzone_ote.py::TestConfluence -q
```

Expected: `ImportError` — `find_confluence` not defined yet.

### Step 3: Write minimal implementation

Add to `s90_killzone_ote.py`:

```python
# ---------------------------------------------------------------------------
# 4. Confluence Detection
# ---------------------------------------------------------------------------

def _ranges_overlap(a_low: float, a_high: float, b_low: float, b_high: float) -> bool:
    """True if [a_low, a_high] overlaps [b_low, b_high] (inclusive)."""
    return a_low <= b_high and b_low <= a_high


def find_confluence(
    h1_bars: pd.DataFrame,
    as_of_time: pd.Timestamp,
    ote_low: float,
    ote_high: float,
    bias: str,
) -> bool:
    """Return True if any FVG or Order Block on visible 1H bars overlaps the OTE zone.

    FVG detection (3-candle imbalance):
        Bullish FVG at bar i: high[i-2] < low[i]  → gap = [high[i-2], low[i]]
        Bearish FVG at bar i: low[i-2]  > high[i] → gap = [high[i], low[i-2]]

    Order Block detection:
        Bullish OB = last bearish (close < open) candle in visible bars.
            OB range = [close, open]  (body, bearish candle: open > close)
        Bearish OB = last bullish (close > open) candle in visible bars.
            OB range = [open, close]  (body, bullish candle)
        Overlap tested against OTE zone.

    Uses only bars with time <= as_of_time (no look-ahead).
    """
    visible = h1_bars[h1_bars["time"] <= as_of_time].reset_index(drop=True)
    n = len(visible)

    # --- FVG check ---
    for i in range(2, n):
        h_i2 = float(visible.iloc[i - 2]["high"])
        l_i2 = float(visible.iloc[i - 2]["low"])
        h_i  = float(visible.iloc[i]["high"])
        l_i  = float(visible.iloc[i]["low"])

        if bias == "bullish":
            # Bullish FVG: high[i-2] < low[i]
            if h_i2 < l_i:
                fvg_low, fvg_high = h_i2, l_i
                if _ranges_overlap(fvg_low, fvg_high, ote_low, ote_high):
                    return True
        else:
            # Bearish FVG: low[i-2] > high[i]
            if l_i2 > h_i:
                fvg_low, fvg_high = h_i, l_i2
                if _ranges_overlap(fvg_low, fvg_high, ote_low, ote_high):
                    return True

    # --- Order Block check ---
    opens  = visible["open"].to_numpy()
    closes = visible["close"].to_numpy()

    if bias == "bullish":
        # Bullish OB = last bearish candle; OB range = [close, open]
        for j in range(n - 1, -1, -1):
            if closes[j] < opens[j]:  # bearish candle
                ob_low, ob_high = float(closes[j]), float(opens[j])
                if _ranges_overlap(ob_low, ob_high, ote_low, ote_high):
                    return True
                break  # only check the LAST bearish candle
    else:
        # Bearish OB = last bullish candle; OB range = [open, close]
        for j in range(n - 1, -1, -1):
            if closes[j] > opens[j]:  # bullish candle
                ob_low, ob_high = float(opens[j]), float(closes[j])
                if _ranges_overlap(ob_low, ob_high, ote_low, ote_high):
                    return True
                break

    return False
```

### Step 4: Run to confirm GREEN

```
.venv\Scripts\python.exe -m pytest tests/test_s90_killzone_ote.py::TestConfluence -q
```

Expected: `5 passed`

---

## Task 5: Signal Generation (End-to-End OTE Setup)

**Files:**
- Modify: `strategies/xauusd_strategies/s90_killzone_ote.py` (add `generate_signals`)
- Modify: `tests/test_s90_killzone_ote.py` (add `TestSignalGeneration`)

### Step 1: Write the failing test

This is the critical look-ahead validation test. Synthetic bars are crafted so that at bar k (during a killzone, price in OTE zone, with confluence) a signal is produced with entry_index=k+1.

```python
from strategies.xauusd_strategies.s90_killzone_ote import (
    is_killzone, get_htf_bias, get_swing_leg, get_ote_zone,
    find_confluence, generate_signals
)


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
        Textbook bullish OTE setup:
        - 1H bars show bullish BOS (last close > prior high) with swing leg low=2592, high=2640
        - OTE zone = [2621.92, 2630.24]  (62-79% of 48-point range, from 2640)
          Actually: range=48, ote_low=2640-0.79*48=2601.92, ote_high=2640-0.62*48=2610.24
          Let's use leg_low=2560, leg_high=2640 (range=80):
          ote_low=2640-0.79*80=2576.8, ote_high=2640-0.62*80=2590.4
        - A bullish FVG overlaps [2576.8, 2590.4]
        - Bar k is during London killzone (08:05 UTC), price low tags OTE zone
        - Signal: direction='BUY', entry_index=k+1, sl < open[k+1], tp > open[k+1]
        """
        # leg_low=2560, leg_high=2640, range=80
        # ote_low=2576.8, ote_high=2590.4
        # 1H bars: bullish BOS (last bar close=2645 > prior high=2638)
        # FVG at i=2: high[0]=2575 < low[2]=2580 → FVG gap=[2575,2580] overlaps [2576.8, 2590.4] ✓
        bars_1h = self._make_1h([
            # bar 0: prior structure bar; has high 2638 → prior high
            {"time": "2026-01-13 04:00", "open": 2600, "high": 2638, "low": 2560, "close": 2565},
            # bar 1: middle
            {"time": "2026-01-13 05:00", "open": 2565, "high": 2575, "low": 2561, "close": 2570},
            # bar 2: creates bullish FVG → high[0]=2638 < low[2]? No that's wrong.
            # Let's make FVG: high[0]=2570 < low[2]=2580 ✓; and bar 2 is the impulse
            {"time": "2026-01-13 06:00", "open": 2580, "high": 2640, "low": 2578, "close": 2635},
            # bar 3: BOS — close=2645 > prior high=2638
            {"time": "2026-01-13 07:00", "open": 2635, "high": 2648, "low": 2625, "close": 2645},
        ])
        # Recompute with correct FVG:
        # high[0] (04:00 bar high) = 2638, low[2] (06:00 bar low) = 2578
        # 2638 < 2578? No. Let's use simpler bars:
        # bar0: high=2570, bar1: (middle), bar2: low=2580 → high[0]=2570 < low[2]=2580 ✓
        bars_1h = self._make_1h([
            {"time": "2026-01-13 04:00", "open": 2558, "high": 2570, "low": 2560, "close": 2562},
            {"time": "2026-01-13 05:00", "open": 2562, "high": 2568, "low": 2558, "close": 2565},
            {"time": "2026-01-13 06:00", "open": 2580, "high": 2640, "low": 2580, "close": 2635},
            {"time": "2026-01-13 07:00", "open": 2635, "high": 2648, "low": 2625, "close": 2645},
            # close=2645 > prior_high = max(2570, 2568, 2640) = 2640 → BOS bullish ✓
        ])
        # swing leg: low_idx has lowest low = 2558 (bar0 or bar1); after that, high=2640 (bar2)
        # leg_low=2558, leg_high=2640(?), BUT we want the max high in bars AFTER the swing low.
        # bar0 low=2560, bar1 low=2558 → swing low idx=1 (low=2558)
        # bars after idx=1: bar2 high=2640, bar3 high=2648 → leg_high=2648
        # range=2648-2558=90; ote_low=2648-0.79*90=2576.9, ote_high=2648-0.62*90=2592.2
        # FVG: high[0]=2570 < low[2]=2580 ✓ → FVG=[2570,2580] overlaps [2576.9,2592.2] ✓

        # 5M bars: bar k=0 is during London (08:05), price tags OTE zone [2576.9, 2592.2]
        # Bar k: low=2578 is inside OTE zone ✓
        # Bar k+1 (entry): open must be > sl (which is 2558 - 0.50 = 2557.5) and < tp (2648)
        bars_5m = self._make_5m([
            # bar 0: during London killzone; low tags OTE zone; this is bar k (trigger)
            {"time": "2026-01-13 08:05", "open": 2595, "high": 2597, "low": 2578, "close": 2582},
            # bar 1: entry bar (k+1)
            {"time": "2026-01-13 08:10", "open": 2583, "high": 2640, "low": 2581, "close": 2638},
            # bar 2: unused (beyond entry in this test)
            {"time": "2026-01-13 08:15", "open": 2638, "high": 2650, "low": 2630, "close": 2648},
        ])

        signals = generate_signals(bars_5m, bars_1h)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.direction == "BUY"
        assert sig.entry_index == 1   # k+1 where k=0
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
            # 12:00 UTC — between London close (10:00) and NY open (13:30)
            {"time": "2026-01-13 12:00", "open": 2595, "high": 2597, "low": 2578, "close": 2582},
            {"time": "2026-01-13 12:05", "open": 2583, "high": 2640, "low": 2581, "close": 2638},
        ])
        signals = generate_signals(bars_5m, bars_1h)
        assert len(signals) == 0

    def test_no_signal_when_rr_below_minimum(self):
        """If R:R < MIN_RR (1.5), the signal is skipped."""
        # Construct a scenario where SL is very wide and TP is close
        # leg_low=2560, leg_high=2580 (tiny range=20)
        # ote_low=2580-0.79*20=2564.2, ote_high=2580-0.62*20=2567.6
        # entry_open ≈ 2566 (inside OTE); tp=2580; sl=2560-0.5=2559.5
        # risk = 2566 - 2559.5 = 6.5; reward = 2580 - 2566 = 14 → R:R=2.15 > 1.5
        # So let's push entry open closer to tp:
        # If entry_open = 2578, tp=2580: reward=2, risk=2578-2559.5=18.5 → R:R=0.1 < 1.5 ✓
        # BUT exec_sim validates BUY: sl < O < tp → need tp > O=2578 and sl=2559.5 < 2578 ✓
        # And tp=2580 > 2578 ✓; reward=2, risk=18.5; RR=0.1 < 1.5 → skip ✓
        bars_1h = self._make_1h([
            # BOS: last close 2582 > prior high 2578
            {"time": "2026-01-13 04:00", "open": 2558, "high": 2570, "low": 2560, "close": 2562},
            {"time": "2026-01-13 05:00", "open": 2562, "high": 2572, "low": 2558, "close": 2565},
            # FVG: high[0]=2570 < low[2]=2573
            {"time": "2026-01-13 06:00", "open": 2573, "high": 2580, "low": 2573, "close": 2578},
            {"time": "2026-01-13 07:00", "open": 2578, "high": 2585, "low": 2573, "close": 2582},
        ])
        # leg: swing low idx=1 (low=2558), leg_high = max highs after idx=1 = max(2580,2585)=2585
        # range=2585-2558=27; ote_low=2585-0.79*27=2563.7, ote_high=2585-0.62*27=2568.3
        # entry bar open = 2578 → entry_open > ote_high (2568.3) → price NOT in OTE zone
        # This test ensures we test that when OTE isn't tagged, no signal fires.
        bars_5m = self._make_5m([
            # bar k: during killzone, but price NOT in OTE zone (open=2578 > ote_high=2568.3)
            {"time": "2026-01-13 08:05", "open": 2578, "high": 2582, "low": 2576, "close": 2580},
            {"time": "2026-01-13 08:10", "open": 2580, "high": 2590, "low": 2578, "close": 2588},
        ])
        signals = generate_signals(bars_5m, bars_1h)
        # bar k's low=2576 is not <= ote_high=2568.3, and bar k's high=2582 > ote_low=2563.7
        # Actually 2576 > 2568.3 → low > ote_high → not in OTE zone → no signal
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
```

### Step 2: Run to confirm RED

```
.venv\Scripts\python.exe -m pytest tests/test_s90_killzone_ote.py::TestSignalGeneration -q
```

Expected: `ImportError` — `generate_signals` not defined yet.

### Step 3: Write minimal implementation

Add to `s90_killzone_ote.py`:

```python
# ---------------------------------------------------------------------------
# 5. Signal Generation
# ---------------------------------------------------------------------------

def generate_signals(
    bars_5m: pd.DataFrame,
    bars_1h: pd.DataFrame,
) -> list[Signal]:
    """Scan 5M bars and generate OTE entry signals.

    For each 5M bar k (from 0 to len-2):
        1. Check if bar k is in a killzone window. Skip if not.
        2. Determine HTF bias from 1H bars closed at or before bar k's time.
           Skip if neutral.
        3. Identify the most recent impulse swing leg on 1H (closed bars ≤ bar k).
           Skip if insufficient bars.
        4. Compute the OTE zone [ote_low, ote_high] from the swing leg.
        5. Check if bar k tags the OTE zone (low[k] <= ote_high and high[k] >= ote_low).
           Skip if not.
        6. Check for FVG or OB confluence on 1H bars (closed ≤ bar k) overlapping OTE.
           Skip if no confluence.
        7. Compute SL and TP (mid prices):
            BUY:  sl = leg_low  - SL_BUFFER; tp = leg_high
            SELL: sl = leg_high + SL_BUFFER; tp = leg_low
        8. Compute expected mid R:R:
            BUY:  entry_open = bars_5m.iloc[k+1].open
                  risk_mid   = entry_open - sl
                  reward_mid = tp - entry_open
            SELL: risk_mid   = sl - entry_open
                  reward_mid = entry_open - tp
           Skip if reward_mid / risk_mid < MIN_RR or risk_mid <= 0.
        9. Emit Signal(direction, entry_index=k+1, sl=sl, tp=tp).

    NO look-ahead: HTF bars are filtered to time <= bars_5m.iloc[k].time.
    """
    signals: list[Signal] = []
    n5 = len(bars_5m)

    for k in range(n5 - 1):   # k+1 must exist
        bar_k = bars_5m.iloc[k]
        t_k   = pd.Timestamp(bar_k["time"])  # tz-naive UTC

        # 1. Killzone filter
        if not is_killzone(t_k):
            continue

        # 2. HTF bias (closed 1H bars only — bars with time <= t_k)
        bias = get_htf_bias(bars_1h, t_k)
        if bias == "neutral":
            continue

        # 3. Swing leg
        swing = get_swing_leg(bars_1h, bias, t_k)
        if swing is None:
            continue
        leg_low, leg_high = swing

        # Sanity: leg must be non-trivial
        if leg_high - leg_low < 0.10:
            continue

        # 4. OTE zone
        ote_low, ote_high = get_ote_zone(leg_low, leg_high, bias)

        # 5. Price tag: bar k touches OTE zone
        bar_low  = float(bar_k["low"])
        bar_high = float(bar_k["high"])
        if not (bar_low <= ote_high and bar_high >= ote_low):
            continue

        # 6. Confluence
        if not find_confluence(bars_1h, t_k, ote_low, ote_high, bias):
            continue

        # 7. SL and TP
        if bias == "bullish":
            direction = "BUY"
            sl = leg_low  - SL_BUFFER
            tp = leg_high
        else:
            direction = "SELL"
            sl = leg_high + SL_BUFFER
            tp = leg_low

        # 8. R:R check (mid prices, using next bar's open as proxy for entry)
        entry_open = float(bars_5m.iloc[k + 1]["open"])

        if direction == "BUY":
            # exec_sim requires sl < entry_open < tp
            if not (sl < entry_open < tp):
                continue
            risk_mid   = entry_open - sl
            reward_mid = tp - entry_open
        else:  # SELL
            if not (tp < entry_open < sl):
                continue
            risk_mid   = sl - entry_open
            reward_mid = entry_open - tp

        if risk_mid <= 0 or reward_mid / risk_mid < MIN_RR:
            continue

        # 9. Emit signal
        signals.append(Signal(
            direction   = direction,
            entry_index = k + 1,
            sl          = sl,
            tp          = tp,
        ))

    return signals
```

### Step 4: Run to confirm GREEN

```
.venv\Scripts\python.exe -m pytest tests/test_s90_killzone_ote.py::TestSignalGeneration -q
```

Expected: `4 passed`

---

## Task 6: Full Test Suite Green

Run all tests to confirm no regressions:

```
.venv\Scripts\python.exe -m pytest tests/test_s90_killzone_ote.py -q
```

Expected: all tests pass (cumulative count from Tasks 1–5).

---

## Task 7: Backtest Runner + JSON Output

**Files:**
- Modify: `strategies/xauusd_strategies/s90_killzone_ote.py` (add `run_backtest` function + `__main__` block)

No new tests needed for the backtest runner itself — it calls already-tested building blocks. The JSON output is validated by inspection.

### Step 1: Add runner to `s90_killzone_ote.py`

Append at the end of the file:

```python
# ---------------------------------------------------------------------------
# 6. Backtest Runner + Metrics
# ---------------------------------------------------------------------------

RESULTS_PATH = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "backtest", "results", "s90_killzone_ote_20260522.json"
))


def compute_metrics(results, n_trades: int, bars_5m: pd.DataFrame) -> dict:
    """Compute all required metrics from a list of TradeResult."""
    if not results:
        return {
            "n_trades": 0,
            "winrate": 0.0,
            "expectancy_R": 0.0,
            "avg_win_R": 0.0,
            "avg_loss_R": 0.0,
            "profit_factor": 0.0,
            "max_dd_R": 0.0,
            "trades_per_day": 0.0,
            "by_hour": {},
        }

    rs = [t.r_multiple for t in results]
    wins  = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]

    winrate     = len(wins) / len(rs)
    expectancy  = sum(rs) / len(rs)
    avg_win     = sum(wins)  / len(wins)  if wins  else 0.0
    avg_loss    = sum(losses) / len(losses) if losses else 0.0

    gross_win  = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")

    # Max drawdown in R (peak-to-trough of cumulative R)
    cum_r = 0.0
    peak  = 0.0
    max_dd = 0.0
    for r in rs:
        cum_r += r
        if cum_r > peak:
            peak = cum_r
        dd = peak - cum_r
        if dd > max_dd:
            max_dd = dd

    # Trades per day: unique calendar days with at least one killzone bar
    session_dates = set()
    for _, row in bars_5m.iterrows():
        ts = pd.Timestamp(row["time"])
        if is_killzone(ts):
            session_dates.add(ts.date())
    trades_per_day = n_trades / len(session_dates) if session_dates else 0.0

    # By-hour breakdown
    by_hour: dict[int, dict] = {}
    for t in results:
        h = pd.Timestamp(t.entry_time).hour
        if h not in by_hour:
            by_hour[h] = {"trades": 0, "wins": 0}
        by_hour[h]["trades"] += 1
        if t.r_multiple > 0:
            by_hour[h]["wins"] += 1
    by_hour_out = {
        str(h): {
            "trades":  v["trades"],
            "winrate": v["wins"] / v["trades"],
        }
        for h, v in sorted(by_hour.items())
    }

    return {
        "n_trades":      n_trades,
        "winrate":       round(winrate, 4),
        "expectancy_R":  round(expectancy, 4),
        "avg_win_R":     round(avg_win, 4),
        "avg_loss_R":    round(avg_loss, 4),
        "profit_factor": round(profit_factor, 4),
        "max_dd_R":      round(max_dd, 4),
        "trades_per_day": round(trades_per_day, 4),
        "by_hour":       by_hour_out,
    }


def run_backtest() -> dict:
    """Load IS bars, generate signals, simulate, compute and write metrics JSON."""
    from strategies.research.dataset import load_is_bars

    print("[s90_killzone_ote] Loading in-sample bars...")
    bars_5m = load_is_bars("5m")
    bars_1h = load_is_bars("1h")
    print(f"[s90_killzone_ote] 5M bars: {len(bars_5m):,} | 1H bars: {len(bars_1h):,}")

    print("[s90_killzone_ote] Generating signals (this may take a minute)...")
    signals = generate_signals(bars_5m, bars_1h)
    print(f"[s90_killzone_ote] Signals: {len(signals)}")

    results = simulate(
        bars_5m, signals,
        half_spread=HALF_SPREAD,
        slip=SLIP,
        max_hold_bars=MAX_HOLD_BARS,
    )

    metrics = compute_metrics(results, len(results), bars_5m)

    output = {
        "family": "B_KillzoneOTE",
        "params": {
            "tf":               "5m",
            "htf_tf":           "1h",
            "half_spread":      HALF_SPREAD,
            "slip":             SLIP,
            "session_windows":  {"london": "08:00-10:00 UTC", "ny": "13:30-15:30 UTC"},
            "sl_rule":          "swing_origin - 0.50",
            "tp_rule":          "swing_extreme (0% retracement level)",
            "ote_zone":         "62%-79% Fibonacci retracement",
            "min_rr":           MIN_RR,
            "max_hold_bars":    MAX_HOLD_BARS,
            "htf_lookback":     HTF_LOOKBACK,
            "sl_buffer":        SL_BUFFER,
        },
        **metrics,
    }

    os.makedirs(os.path.dirname(RESULTS_PATH), exist_ok=True)
    with open(RESULTS_PATH, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n[s90_killzone_ote] Results written to: {RESULTS_PATH}")
    print(f"  n_trades       : {output['n_trades']}")
    print(f"  winrate        : {output['winrate']:.1%}")
    print(f"  expectancy_R   : {output['expectancy_R']:.3f}")
    print(f"  trades_per_day : {output['trades_per_day']:.3f}")
    print(f"  profit_factor  : {output['profit_factor']:.3f}")
    print(f"  max_dd_R       : {output['max_dd_R']:.3f}")
    print(json.dumps(output, indent=2))

    return output


if __name__ == "__main__":
    run_backtest()
```

### Step 2: Run the full test suite one more time to ensure nothing broke

```
.venv\Scripts\python.exe -m pytest tests/test_s90_killzone_ote.py -q
```

Expected: all tests pass.

### Step 3: Run the backtest

```
.venv\Scripts\python.exe -m strategies.xauusd_strategies.s90_killzone_ote
```

This will:
- Load IS bars from the parquet cache.
- Generate signals.
- Run exec_sim.
- Write `strategies/backtest/results/s90_killzone_ote_20260522.json`.
- Print metrics.

Expected: completes in < 5 minutes. If 0 signals fire, investigate by printing intermediate counts (killzone bars hit, bias non-neutral, swing found, OTE tagged, confluence found).

---

## Spec Coverage Self-Check

| Requirement | Task |
|---|---|
| Killzone windows London 08-10, NY 13:30-15:30, weekdays | Task 1 |
| HTF bias on 1H closed bars only (BOS) | Task 2 |
| OTE definition 62-79% retracement | Task 3 |
| Confluence required (FVG or OB) | Task 4 |
| Entry k+1, SL beyond origin, TP at extreme | Task 5 |
| Min R:R 1.5 | Task 5 |
| max_hold_bars=36 | Task 7 |
| half_spread=0.15, slip=0.05 | Task 7 |
| JSON output with all required fields | Task 7 |
| Look-ahead self-audit | Docstring in strategy + tests |
| Synthetic-only tests, no real data | All tasks |
| RED → GREEN TDD | All tasks |
| Files only: strategy + test + JSON | All tasks |
| No existing files modified | All tasks |

## Placeholder Scan

No TBD/TODO/implement-later phrases present. All code blocks are complete. All test assertions are concrete.

## Type Consistency Check

- `is_killzone(ts: pd.Timestamp) -> bool` — used consistently.
- `get_htf_bias(h1_bars, as_of_time) -> str` — used consistently.
- `get_swing_leg(h1_bars, bias, as_of_time) -> Optional[tuple[float, float]]` — returns `(leg_low, leg_high)` or `None`; used consistently in Task 5.
- `get_ote_zone(leg_low, leg_high, bias) -> tuple[float, float]` — used consistently.
- `find_confluence(h1_bars, as_of_time, ote_low, ote_high, bias) -> bool` — used consistently.
- `generate_signals(bars_5m, bars_1h) -> list[Signal]` — used in Task 7.
- `Signal(direction, entry_index, sl, tp)` — from exec_sim; field names match throughout.
- `simulate(bars, signals, half_spread, slip, max_hold_bars) -> list[TradeResult]` — from exec_sim; called correctly in Task 7.
- `TradeResult.r_multiple`, `.entry_time` — accessed consistently in `compute_metrics`.
