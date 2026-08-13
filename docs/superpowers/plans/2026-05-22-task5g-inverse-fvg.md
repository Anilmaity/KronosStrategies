# Task 5g — Inverse-FVG Continuation (Family G) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and backtest an Inverse-FVG Continuation strategy on 5m XAU/USD bars: detect FVGs, track their fill+violation (inversion), then enter a retest of the inverted zone in the continuation direction.

**Architecture:** A pure-function `detect_signals(bars_5m, bars_1h)` scans all 5m bars using only closed information up to each bar k, generates `Signal(direction, entry_index=k+1, sl, tp)` objects for confirmed inversion-retest setups. A separate `run_backtest()` wrapper loads IS bars, calls `detect_signals`, calls `simulate()`, computes metrics, and writes JSON. Session filter (London [07,11) / NY [12,16) UTC weekdays) is applied to entry_index open time.

**Tech Stack:** Python 3.11, pandas, pytest, `strategies.research.exec_sim.{Signal, simulate}`, `strategies.research.dataset.load_is_bars`

---

## File Map

| Path | Role |
|---|---|
| `strategies/xauusd_strategies/s90_inverse_fvg.py` | Strategy: FVG detection, inversion tracking, retest signal generation, backtest runner |
| `tests/test_s90_inverse_fvg.py` | TDD tests: synthetic bars, offline, no real data |
| `strategies/backtest/results/s90_inverse_fvg_20260522.json` | Output metrics JSON (written by backtest runner) |

---

## ICT Definitions (locked)

**Bullish FVG at bar i (3-candle):** `high[i-2] < low[i]`; zone = `[high[i-2], low[i]]`
**Bearish FVG at bar i (3-candle):** `low[i-2] > high[i]`; zone = `[high[i], low[i-2]]`

**FVG Fill:** price trades back INTO the FVG zone (any bar's wick enters the zone).
**FVG Violation/Inversion:** after fill, price CLOSES decisively beyond the OPPOSITE boundary:
- Bullish FVG violated → close < `high[i-2]` (zone lower bound) → now acts as RESISTANCE → SHORT continuation
- Bearish FVG violated → close > `low[i-2]` (zone upper bound) → now acts as SUPPORT → LONG continuation

**Retest:** after inversion, price re-enters the inverted FVG zone from the new side on a later bar k.
- For inverted-bullish-FVG (resistance): retest = any bar's HIGH enters zone (wick into zone), direction = SHORT
- For inverted-bearish-FVG (support): retest = any bar's LOW enters zone (wick into zone), direction = LONG

**Entry:** at open of bar k+1.
**SL:** beyond the far side of the inverted FVG zone + 0.30 buffer (half the typical spread * 2).
**TP:** 2.0 × risk distance (from entry toward continuation). Skip if R:R < 1.5.
**max_hold_bars:** 48 (4 hours on 5m bars).

---

## Task 1: Write Failing Tests (RED Phase)

**Files:**
- Create: `tests/test_s90_inverse_fvg.py`

- [ ] **Step 1.1: Write the test file (all tests will FAIL — module does not exist yet)**

```python
"""
test_s90_inverse_fvg.py
-----------------------
TDD tests for strategies.xauusd_strategies.s90_inverse_fvg
(Task 5g – Family G: Inverse-FVG Continuation).

ALL tests use synthetic bar DataFrames (tz-naive UTC timestamps).
NO real cache, NO network, NO DB.

Coverage
--------
1. Textbook SHORT: bullish FVG formed, filled, violated (close < lower bound) → on retest → SELL signal
2. FVG filled but NOT violated (price returns into zone but does NOT close beyond opposite boundary) → no signal
3. Textbook LONG: bearish FVG formed, filled, violated (close > upper bound) → on retest → LONG signal
4. Inverted FVG retest attempted but entry bar is outside London/NY session → no signal
5. R:R < 1.5 (TP too close) → no signal
"""
from __future__ import annotations

import pandas as pd
import pytest

from strategies.xauusd_strategies.s90_inverse_fvg import detect_signals


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bars(rows: list[dict], base_time: str = "2025-06-02 08:00:00") -> pd.DataFrame:
    """Build a minimal OHLCV+spread DataFrame with tz-naive UTC timestamps,
    each bar 5 minutes apart starting from base_time."""
    t0 = pd.Timestamp(base_time)
    data = []
    for i, r in enumerate(rows):
        t = r.get("time", t0 + pd.Timedelta(minutes=5 * i))
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


# ---------------------------------------------------------------------------
# Test 1: Textbook SHORT (bullish FVG formed, filled, then violated → retest → SELL)
# ---------------------------------------------------------------------------

def test_textbook_inverted_bullish_fvg_short():
    """
    Setup (all bars at 08:xx UTC, London session):

    Phase 1 — FVG FORMATION (bars 0,1,2):
      Bullish FVG at i=2: high[0]=2000.0 < low[2]=2002.0
      Zone = [2000.0, 2002.0]

    Phase 2 — FVG FILL (bars 3,4):
      Bar 3: price enters zone (low=2001.0 ≤ 2002.0 and high=2003 > 2000, so inside)
      Fill confirmed: yes.

    Phase 3 — FVG VIOLATION (bar 5):
      Bar 5 close = 1999.0 < zone lower bound 2000.0 → VIOLATED → inverted to RESISTANCE
      Confirmation bar k=5.

    Phase 4 — RETEST (bar 6):
      Bar 6 high = 2001.0 (wick enters zone [2000,2002]) → retest of inverted FVG from below
      This is bar k=6; entry_index = 7.

    Expected: SELL signal with entry_index=7.
    SL above zone upper bound (2002.0 + buffer).
    TP = entry_open - 2.0*(SL - entry_open), must give R:R ≥ 1.5.
    """
    bars = _make_bars([
        # Phase 1: FVG Formation
        {"open": 1999, "high": 2000, "low": 1998, "close": 1999},   # 0: 08:00 high=2000
        {"open": 1999, "high": 2001, "low": 1998, "close": 2000},   # 1: 08:05 middle
        {"open": 2000, "high": 2005, "low": 2002, "close": 2004},   # 2: 08:10 low=2002 > high[0]=2000 → bullish FVG zone=[2000,2002]
        # Phase 2: FVG Fill
        {"open": 2004, "high": 2005, "low": 2001, "close": 2003},   # 3: 08:15 low=2001 enters zone → fill begins
        {"open": 2003, "high": 2003, "low": 2000, "close": 2002},   # 4: 08:20 still in zone
        # Phase 3: FVG Violation
        {"open": 2002, "high": 2003, "low": 1997, "close": 1999},   # 5: 08:25 close=1999 < 2000 → VIOLATED k=5
        # Phase 4: Retest from below
        {"open": 1999, "high": 2001, "low": 1998, "close": 1999},   # 6: 08:30 high=2001 enters zone → retest k=6
        # Entry bar
        {"open": 1999, "high": 2000, "low": 1990, "close": 1991},   # 7: 08:35 entry (open=1999)
        {"open": 1991, "high": 1992, "low": 1985, "close": 1986},   # 8: for TP to hit
    ], base_time="2025-06-02 08:00:00")

    signals = detect_signals(bars)

    sell_sigs = [s for s in signals if s.direction == "SELL"]
    assert len(sell_sigs) >= 1, f"Expected at least one SELL signal, got {signals}"
    sig = sell_sigs[0]
    assert sig.entry_index == 7, f"Expected entry_index=7, got {sig.entry_index}"
    assert sig.direction == "SELL"
    # SL must be ABOVE the zone upper bound (2002) + buffer
    assert sig.sl > 2002.0, f"SL={sig.sl} should be above zone upper bound 2002"
    # TP must be BELOW entry open (1999)
    assert sig.tp < 1999.0, f"TP={sig.tp} should be below entry open 1999"


# ---------------------------------------------------------------------------
# Test 2: FVG fills but does NOT violate → no signal
# ---------------------------------------------------------------------------

def test_fvg_fill_without_violation_gives_no_signal():
    """
    Bullish FVG at i=2 zone=[2000,2002].
    Price fills the zone (enters it) but NEVER closes below lower bound (2000).
    Price bounces back up from the zone → FVG acts as support (normal behaviour).
    Expect: no signal (FVG is not inverted).
    """
    bars = _make_bars([
        # Phase 1: FVG Formation
        {"open": 1999, "high": 2000, "low": 1998, "close": 1999},   # 0 high=2000
        {"open": 1999, "high": 2001, "low": 1998, "close": 2000},   # 1 middle
        {"open": 2000, "high": 2005, "low": 2002, "close": 2004},   # 2 low=2002 > high[0]=2000 → bullish FVG
        # Phase 2: Fill (enters zone but close stays above 2000)
        {"open": 2004, "high": 2005, "low": 2001, "close": 2003},   # 3 low=2001 in zone, close=2003 > 2000
        {"open": 2003, "high": 2006, "low": 2001, "close": 2005},   # 4 bounces — close=2005 > 2000
        # NO violation — price moves back up
        {"open": 2005, "high": 2008, "low": 2004, "close": 2007},   # 5 close=2007 > 2000, no violation
        {"open": 2007, "high": 2010, "low": 2006, "close": 2009},   # 6
    ], base_time="2025-06-02 08:00:00")

    signals = detect_signals(bars)
    assert len(signals) == 0, f"Expected no signal when FVG only fills (no violation), got {signals}"


# ---------------------------------------------------------------------------
# Test 3: Textbook LONG (bearish FVG inverted → LONG continuation)
# ---------------------------------------------------------------------------

def test_textbook_inverted_bearish_fvg_long():
    """
    Phase 1 — FVG FORMATION (bars 0,1,2):
      Bearish FVG at i=2: low[0]=2010.0 > high[2]=2008.0
      Zone = [2008.0, 2010.0]  (high[i]=2008 to low[i-2]=2010)

    Phase 2 — FVG FILL (bar 3):
      Bar 3 high=2009.0 enters zone → fill begins.

    Phase 3 — FVG VIOLATION (bar 4):
      Bar 4 close=2011.0 > zone upper bound 2010.0 → VIOLATED → inverted to SUPPORT

    Phase 4 — RETEST (bar 5):
      Bar 5 low=2009.0 (wick enters zone [2008,2010]) → retest of inverted FVG from above
      entry_index = 6.

    Expected: BUY signal with entry_index=6.
    SL below zone lower bound (2008.0 - buffer).
    TP well above entry open for R:R ≥ 1.5.
    """
    bars = _make_bars([
        # Phase 1: Bearish FVG Formation
        {"open": 2011, "high": 2012, "low": 2010, "close": 2011},   # 0 low=2010
        {"open": 2011, "high": 2011, "low": 2009, "close": 2010},   # 1 middle
        {"open": 2010, "high": 2008, "low": 2006, "close": 2007},   # 2 high=2008 < low[0]=2010 → bearish FVG zone=[2008,2010]
        # Phase 2: Fill
        {"open": 2007, "high": 2009, "low": 2006, "close": 2008},   # 3 high=2009 enters zone [2008,2010]
        # Phase 3: Violation
        {"open": 2008, "high": 2012, "low": 2007, "close": 2011},   # 4 close=2011 > 2010 → VIOLATED k=4
        # Phase 4: Retest from above
        {"open": 2011, "high": 2012, "low": 2009, "close": 2011},   # 5 low=2009 enters zone → retest k=5
        # Entry bar (open must be > zone upper 2010 for valid LONG where SL < open < TP)
        {"open": 2011, "high": 2015, "low": 2010, "close": 2014},   # 6 entry
        {"open": 2014, "high": 2022, "low": 2013, "close": 2021},   # 7 TP hit
    ], base_time="2025-06-02 08:00:00")

    signals = detect_signals(bars)

    buy_sigs = [s for s in signals if s.direction == "BUY"]
    assert len(buy_sigs) >= 1, f"Expected at least one BUY signal, got {signals}"
    sig = buy_sigs[0]
    assert sig.entry_index == 6, f"Expected entry_index=6, got {sig.entry_index}"
    assert sig.direction == "BUY"
    # SL below zone lower bound (2008) - buffer
    assert sig.sl < 2008.0, f"SL={sig.sl} should be below zone lower bound 2008"
    # TP above entry open (2011)
    assert sig.tp > 2011.0, f"TP={sig.tp} should be above entry open 2011"


# ---------------------------------------------------------------------------
# Test 4: Retest confirmed but entry bar is outside London/NY session → no signal
# ---------------------------------------------------------------------------

def test_outside_session_gives_no_signal():
    """
    Identical to test_textbook_inverted_bullish_fvg_short but all bars start
    at 03:00 UTC (Asian session) — outside London [07,11) and NY [12,16).
    Expect: no signal.
    """
    bars = _make_bars([
        {"open": 1999, "high": 2000, "low": 1998, "close": 1999},
        {"open": 1999, "high": 2001, "low": 1998, "close": 2000},
        {"open": 2000, "high": 2005, "low": 2002, "close": 2004},
        {"open": 2004, "high": 2005, "low": 2001, "close": 2003},
        {"open": 2003, "high": 2003, "low": 2000, "close": 2002},
        {"open": 2002, "high": 2003, "low": 1997, "close": 1999},
        {"open": 1999, "high": 2001, "low": 1998, "close": 1999},
        {"open": 1999, "high": 2000, "low": 1990, "close": 1991},
    ], base_time="2025-06-02 03:00:00")  # <-- Asian session

    signals = detect_signals(bars)
    assert len(signals) == 0, (
        f"Expected no signal outside London/NY session, got {signals}"
    )


# ---------------------------------------------------------------------------
# Test 5: R:R < 1.5 → no signal
# ---------------------------------------------------------------------------

def test_rr_below_minimum_gives_no_signal():
    """
    Bullish FVG inverted (SELL setup), retest confirmed, but TP is placed
    so close to entry that R:R < 1.5 → signal must be suppressed.

    FVG zone = [2000, 2002], retest bar k.
    Entry open = 1999 (bar k+1).
    SL = 2002 + 0.30 buffer = 2002.30 → risk = 2002.30 - 1999 = 3.30
    min_reward for R:R=1.5 → 3.30 * 1.5 = 4.95 → TP must be ≤ 1999 - 4.95 = 1994.05
    We construct bars where no swing low within 48 bars reaches below 1994.05,
    forcing TP candidate to be too close (e.g., only 1997, giving R:R ≈ 0.6).

    We achieve this by making bars 8+ all stay above 1996 (never touch a
    significant low that would give a valid TP ≥ 1.5R).
    """
    bars = _make_bars([
        {"open": 1999, "high": 2000, "low": 1998, "close": 1999},   # 0 high=2000
        {"open": 1999, "high": 2001, "low": 1998, "close": 2000},   # 1
        {"open": 2000, "high": 2005, "low": 2002, "close": 2004},   # 2 bullish FVG zone=[2000,2002]
        {"open": 2004, "high": 2005, "low": 2001, "close": 2003},   # 3 fill
        {"open": 2003, "high": 2003, "low": 2000, "close": 2002},   # 4 still in zone
        {"open": 2002, "high": 2003, "low": 1997, "close": 1999},   # 5 VIOLATED close=1999<2000
        {"open": 1999, "high": 2001, "low": 1998, "close": 1999},   # 6 retest k=6
        # entry bar open = 1999; SL ≈ 2002.30; risk ≈ 3.30; need TP ≤ 1994.05
        # but actual swing low ahead is only 1997.5 → R:R = (1999-1997.5)/3.30 ≈ 0.45 < 1.5
        {"open": 1999, "high": 2000, "low": 1997, "close": 1998},   # 7 entry, low=1997 (too close)
        {"open": 1998, "high": 1999, "low": 1997, "close": 1998},   # 8 no significant new low
        {"open": 1998, "high": 1999, "low": 1997, "close": 1997},   # 9
    ], base_time="2025-06-02 08:00:00")

    signals = detect_signals(bars)
    sell_sigs = [s for s in signals if s.direction == "SELL"]
    assert len(sell_sigs) == 0, (
        f"Expected no signal when R:R < 1.5, got {sell_sigs}"
    )
```

- [ ] **Step 1.2: Run tests to confirm they all FAIL (RED)**

```
.venv/Scripts/python.exe -m pytest tests/test_s90_inverse_fvg.py -q
```

Expected: `ImportError` or `ModuleNotFoundError` — `s90_inverse_fvg` does not exist yet.

---

## Task 2: Implement `s90_inverse_fvg.py` (GREEN Phase)

**Files:**
- Create: `strategies/xauusd_strategies/s90_inverse_fvg.py`

- [ ] **Step 2.1: Create the strategy module**

```python
"""
s90_inverse_fvg.py
------------------
Family G — Inverse-FVG Continuation (XAU/USD 5m, Task 5g).

ICT Logic
---------
1. Detect 3-candle FVGs on 5m bars (bullish: high[i-2] < low[i]; bearish: low[i-2] > high[i]).
2. Track each FVG through two phases: FILL (price enters zone) → VIOLATION (close beyond far bound).
3. After violation, watch for RETEST: a bar whose wick re-enters the inverted zone from the
   new continuation side.
4. At retest bar k, generate Signal(direction, entry_index=k+1, sl, tp) using mid prices.
   entry_index=k+1 means we act on the NEXT bar's open (no look-ahead).

No-look-ahead guarantee: every decision at bar k reads only bars[0..k].
The FVG must be formed on a fully closed bar. Fill + violation must be confirmed on closed bars.
Retest detected at bar k triggers entry at k+1 (next bar's open).

Session filter: London [07,11) UTC or NY [12,16) UTC, weekdays only.
Applied to the ENTRY bar (entry_index), not the signal bar.

Parameters (PARAMS dict at module level — no hidden globals):
    sl_buffer      : 0.30  (extra points beyond FVG far side for SL)
    rr_min         : 1.5   (minimum reward-to-risk ratio; signals below this are skipped)
    tp_rr          : 2.0   (target R:R for TP placement)
    max_hold_bars  : 48
    fvg_max_age    : 200   (bars before an unresolved FVG is dropped)
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from strategies.research.exec_sim import Signal, simulate

# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------
PARAMS = {
    "tf": "5m",
    "half_spread": 0.15,
    "slip": 0.05,
    "session_windows": [{"start_h": 7, "end_h": 11}, {"start_h": 12, "end_h": 16}],
    "sl_buffer": 0.30,
    "rr_min": 1.5,
    "tp_rr": 2.0,
    "max_hold_bars": 48,
    "fvg_max_age": 200,
}

# ---------------------------------------------------------------------------
# Session filter
# ---------------------------------------------------------------------------
_SESSION_RANGES = [(7, 11), (12, 16)]  # [start, end) UTC hours


def _in_session(ts: pd.Timestamp) -> bool:
    """Return True if ts (tz-naive UTC) falls in London or NY session on a weekday."""
    if ts.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    h = ts.hour
    return any(start <= h < end for start, end in _SESSION_RANGES)


# ---------------------------------------------------------------------------
# FVG detection helpers
# ---------------------------------------------------------------------------

def _detect_fvg(bars: pd.DataFrame, i: int) -> Optional[dict]:
    """Detect a 3-candle FVG ending at bar i (i >= 2).

    Returns dict with keys: kind ('bullish'/'bearish'), lo, hi, formed_at
    or None if no FVG.
    """
    if i < 2:
        return None
    high_im2 = float(bars.iloc[i - 2]["high"])
    low_im2  = float(bars.iloc[i - 2]["low"])
    high_i   = float(bars.iloc[i]["high"])
    low_i    = float(bars.iloc[i]["low"])

    if high_im2 < low_i:
        # Bullish FVG: gap up — zone = [high[i-2], low[i]]
        return {"kind": "bullish", "lo": high_im2, "hi": low_i, "formed_at": i}
    if low_im2 > high_i:
        # Bearish FVG: gap down — zone = [high[i], low[i-2]]
        return {"kind": "bearish", "lo": high_i, "hi": low_im2, "formed_at": i}
    return None


# ---------------------------------------------------------------------------
# State machine per active FVG
# ---------------------------------------------------------------------------
# States: WATCHING_FILL → WATCHING_VIOLATION → WATCHING_RETEST → DONE

@dataclass
class _FVGState:
    kind: str          # 'bullish' or 'bearish'
    lo: float          # zone lower bound
    hi: float          # zone upper bound
    formed_at: int     # bar index where FVG was confirmed
    state: str = "WATCHING_FILL"
    filled_at: Optional[int] = None
    violated_at: Optional[int] = None


def _bar_enters_zone(bar: pd.Series, lo: float, hi: float) -> bool:
    """True if any part of the bar's wick overlaps with [lo, hi]."""
    return float(bar["low"]) <= hi and float(bar["high"]) >= lo


# ---------------------------------------------------------------------------
# Core signal detection
# ---------------------------------------------------------------------------

def detect_signals(bars_5m: pd.DataFrame) -> list[Signal]:
    """Scan all 5m bars and return Signals for inverted-FVG retest setups.

    LOOK-AHEAD SELF-AUDIT:
    ----------------------
    At bar k, we access bars_5m.iloc[0..k] only:
    - FVG detection uses bars i-2, i-1, i (all < k at detection time).
    - Fill/violation detection uses bar k's OHLC (the bar that just CLOSED).
    - Retest detection uses bar k's OHLC (wick enters zone on bar that just CLOSED).
    - entry_index is always k+1 (next bar's open) — no execution on the signal bar.
    - SL/TP are computed from already-closed prices only.
    No future information is used at any step.
    """
    signals: list[Signal] = []
    active_fvgs: list[_FVGState] = []
    sl_buffer = PARAMS["sl_buffer"]
    rr_min = PARAMS["rr_min"]
    tp_rr = PARAMS["tp_rr"]
    fvg_max_age = PARAMS["fvg_max_age"]

    n = len(bars_5m)

    for k in range(n):
        bar = bars_5m.iloc[k]

        # ---------------------------------------------------------------
        # 1. Detect new FVG ending at bar k (uses bars k-2, k-1, k)
        # ---------------------------------------------------------------
        fvg = _detect_fvg(bars_5m, k)
        if fvg is not None:
            active_fvgs.append(_FVGState(
                kind=fvg["kind"], lo=fvg["lo"], hi=fvg["hi"], formed_at=k
            ))

        # ---------------------------------------------------------------
        # 2. Update each active FVG state machine
        # ---------------------------------------------------------------
        to_remove: list[int] = []

        for idx, st in enumerate(active_fvgs):
            # Drop expired FVGs
            if k - st.formed_at > fvg_max_age:
                to_remove.append(idx)
                continue

            if st.state == "WATCHING_FILL":
                # Fill: bar k's wick enters the FVG zone
                if _bar_enters_zone(bar, st.lo, st.hi):
                    st.state = "WATCHING_VIOLATION"
                    st.filled_at = k

            elif st.state == "WATCHING_VIOLATION":
                bar_close = float(bar["close"])
                if st.kind == "bullish":
                    # Violated when close BELOW zone lower bound
                    if bar_close < st.lo:
                        st.state = "WATCHING_RETEST"
                        st.violated_at = k
                elif st.kind == "bearish":
                    # Violated when close ABOVE zone upper bound
                    if bar_close > st.hi:
                        st.state = "WATCHING_RETEST"
                        st.violated_at = k

            elif st.state == "WATCHING_RETEST":
                # Retest: price re-enters the inverted zone from the new side
                # For inverted bullish FVG (now resistance): wick enters zone from below
                #   (bar high >= lo means price probed the zone)
                # For inverted bearish FVG (now support): wick enters zone from above
                #   (bar low <= hi means price probed the zone)
                retest = False
                if st.kind == "bullish":
                    # acting as resistance: retest = high enters zone (coming from below)
                    retest = float(bar["high"]) >= st.lo and float(bar["close"]) < st.hi
                elif st.kind == "bearish":
                    # acting as support: retest = low enters zone (coming from above)
                    retest = float(bar["low"]) <= st.hi and float(bar["close"]) > st.lo

                if retest:
                    # Generate signal at k+1
                    entry_idx = k + 1
                    if entry_idx >= n:
                        st.state = "DONE"
                        to_remove.append(idx)
                        continue

                    entry_bar = bars_5m.iloc[entry_idx]
                    entry_open = float(entry_bar["open"])

                    # Session filter on entry bar time
                    entry_time = entry_bar["time"]
                    if not _in_session(entry_time):
                        # Don't mark DONE — might retest again in session later
                        continue

                    if st.kind == "bullish":
                        # Inverted bullish FVG = resistance → SHORT
                        direction = "SELL"
                        sl = st.hi + sl_buffer  # SL above zone upper bound
                        risk = sl - entry_open
                        if risk <= 0:
                            continue
                        tp = entry_open - tp_rr * risk
                        rr = (entry_open - tp) / risk
                    else:
                        # Inverted bearish FVG = support → LONG
                        direction = "BUY"
                        sl = st.lo - sl_buffer  # SL below zone lower bound
                        risk = entry_open - sl
                        if risk <= 0:
                            continue
                        tp = entry_open + tp_rr * risk
                        rr = (tp - entry_open) / risk

                    # Validate signal geometry (exec_sim requirement)
                    if direction == "SELL" and not (tp < entry_open < sl):
                        continue
                    if direction == "BUY" and not (sl < entry_open < tp):
                        continue

                    if rr < rr_min:
                        continue

                    signals.append(Signal(
                        direction=direction,
                        entry_index=entry_idx,
                        sl=sl,
                        tp=tp,
                    ))
                    st.state = "DONE"
                    to_remove.append(idx)

        # Remove in reverse order to keep indices stable
        for idx in reversed(to_remove):
            active_fvgs.pop(idx)

    return signals


# ---------------------------------------------------------------------------
# Backtest runner
# ---------------------------------------------------------------------------

def run_backtest() -> dict:
    """Load IS 5m bars, detect signals, simulate, compute + write metrics."""
    from strategies.research.dataset import load_is_bars

    bars = load_is_bars("5m")
    signals = detect_signals(bars)
    results = simulate(
        bars,
        signals,
        half_spread=PARAMS["half_spread"],
        slip=PARAMS["slip"],
        max_hold_bars=PARAMS["max_hold_bars"],
    )

    if not results:
        metrics = _empty_metrics()
    else:
        metrics = _compute_metrics(results, bars)

    _write_json(metrics)
    return metrics


def _empty_metrics() -> dict:
    return {
        "family": "G_inverse_fvg",
        "params": PARAMS,
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


def _compute_metrics(results, bars: pd.DataFrame) -> dict:
    import numpy as np

    rs = [r.r_multiple for r in results]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    n = len(rs)
    winrate = len(wins) / n if n > 0 else 0.0
    expectancy = sum(rs) / n if n > 0 else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Max drawdown in R (peak-to-trough of cumulative R curve)
    cumr = np.cumsum(rs)
    peak = np.maximum.accumulate(cumr)
    dd = peak - cumr
    max_dd = float(dd.max()) if len(dd) > 0 else 0.0

    # Trades per day
    trading_days = _count_trading_days(bars)
    trades_per_day = n / trading_days if trading_days > 0 else 0.0

    # By-hour breakdown
    by_hour: dict[str, dict] = {}
    for r in results:
        h = str(r.entry_time.hour)
        if h not in by_hour:
            by_hour[h] = {"trades": 0, "winrate": 0.0, "_wins": 0}
        by_hour[h]["trades"] += 1
        if r.r_multiple > 0:
            by_hour[h]["_wins"] += 1
    for h, hd in by_hour.items():
        hd["winrate"] = round(hd["_wins"] / hd["trades"], 4) if hd["trades"] > 0 else 0.0
        del hd["_wins"]

    return {
        "family": "G_inverse_fvg",
        "params": PARAMS,
        "n_trades": n,
        "winrate": round(winrate, 4),
        "expectancy_R": round(expectancy, 4),
        "avg_win_R": round(avg_win, 4),
        "avg_loss_R": round(avg_loss, 4),
        "profit_factor": round(profit_factor, 4),
        "max_dd_R": round(max_dd, 4),
        "trades_per_day": round(trades_per_day, 4),
        "by_hour": by_hour,
    }


def _count_trading_days(bars: pd.DataFrame) -> int:
    """Count unique trading days (weekdays) in the bar series."""
    dates = bars["time"].dt.date
    unique_dates = dates.unique()
    weekday_count = sum(1 for d in unique_dates if pd.Timestamp(d).weekday() < 5)
    return max(weekday_count, 1)


def _write_json(metrics: dict) -> None:
    out_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "backtest", "results"
    )
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "s90_inverse_fvg_20260522.json")
    with open(out_path, "w") as f:
        json.dump(metrics, f, indent=2)
    print(f"[s90_inverse_fvg] Wrote results to {out_path}")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    run_backtest()
```

- [ ] **Step 2.2: Run tests — they should all PASS (GREEN)**

```
.venv/Scripts/python.exe -m pytest tests/test_s90_inverse_fvg.py -q
```

Expected: `5 passed`.

- [ ] **Step 2.3: If any test fails, debug and fix ONLY the implementation (not the tests)**

Common failure modes:
- SL geometry check: for SELL, SL must be > entry_open. For BUY, SL must be < entry_open. Check `risk <= 0` guards.
- FVG state machine: ensure fill detection uses wick (not just close). Violation uses CLOSE only.
- Retest condition: for inverted bullish FVG (resistance), retest = bar's HIGH >= zone lo AND bar's CLOSE < zone hi (price probed from below and didn't fully punch through). Adjust condition to avoid false triggers.

---

## Task 3: Run Backtest on IS Data

**Files:**
- Write: `strategies/backtest/results/s90_inverse_fvg_20260522.json`

- [ ] **Step 3.1: Run the backtest**

```
.venv/Scripts/python.exe -c "from strategies.xauusd_strategies.s90_inverse_fvg import run_backtest; run_backtest()"
```

Expected: Prints JSON with metrics. Watch for n_trades > 0. If n_trades == 0, check FVG detection logic on real bars.

- [ ] **Step 3.2: Check survive-gate**

The survive-gate is: `winrate >= 0.60` AND `n_trades > 0`. If either fails, tune ONLY the parameters in `PARAMS` (not the core logic). Reasonable tuning levers:
- `fvg_max_age`: increase to 400 if too few FVGs survive long enough to be retested
- `sl_buffer`: reduce to 0.15 if too many signals fail geometry check
- `rr_min`: reduce to 1.2 if too many signals are filtered (but document this)

Do NOT change the fundamental ICT logic (fill + violation + retest conditions).

---

## Task 4: Final Test Run + Report

- [ ] **Step 4.1: Run final test suite**

```
.venv/Scripts/python.exe -m pytest tests/test_s90_inverse_fvg.py -q
```

Expected: all 5 tests PASS.

- [ ] **Step 4.2: Print final JSON and confirm it matches the report format**

The JSON must have all required keys: `family, params, n_trades, winrate, expectancy_R, avg_win_R, avg_loss_R, profit_factor, max_dd_R, trades_per_day, by_hour`.

- [ ] **Step 4.3: Compose final report**

Report must include:
1. Status (RED → GREEN)
2. Metrics JSON
3. Prominent raw winrate + trades_per_day
4. Look-ahead self-audit statement
5. Files created
6. Concerns (if winrate < 60%, if n_trades < 50, if expectancy < 0)

---

## Self-Review Checklist

- [x] Spec: all 4 phases of Inverse-FVG (form, fill, violate, retest) covered in logic
- [x] Spec: FVG definitions match (bullish: high[i-2] < low[i]; bearish: low[i-2] > high[i])
- [x] Spec: violation condition matches (close beyond opposite boundary)
- [x] Spec: entry at k+1 (not k)
- [x] Spec: session filter on entry bar
- [x] Spec: SL beyond far side + buffer
- [x] Spec: TP at 2.0R, skip if R:R < 1.5
- [x] Spec: max_hold_bars=48
- [x] Spec: metrics JSON has all required fields
- [x] No look-ahead: all decisions use bars 0..k only
- [x] No placeholder text in any step
- [x] Type consistency: `detect_signals(bars_5m)` used in both tests and implementation
- [x] Tests: test 1 = textbook SHORT, test 2 = fill-no-violation, test 3 = textbook LONG, test 4 = session filter, test 5 = R:R filter
