"""
test_s90_inverse_fvg.py
-----------------------
TDD tests for strategies.xauusd_strategies.s90_inverse_fvg
(Task 5g -- Family G: Inverse-FVG Continuation).

ALL tests use synthetic bar DataFrames (tz-naive UTC timestamps).
NO real cache, NO network, NO DB.

Coverage
--------
1. Textbook SHORT: bullish FVG formed, filled, violated (close < lower bound) -> on retest -> SELL signal
2. FVG filled but NOT violated (price returns into zone but does NOT close beyond opposite boundary) -> no signal
3. Textbook LONG: bearish FVG formed, filled, violated (close > upper bound) -> on retest -> LONG signal
4. Inverted FVG retest attempted but entry bar is outside London/NY session -> no signal
5. Geometry guard: entry open is at or above SL level (risk <= 0) -> no signal

Note on R:R filtering: The TP is always placed at exactly tp_rr * risk (2.0R), so the
computed R:R is always 2.0 >= rr_min (1.5). The rr_min guard is a safety net for edge
cases (e.g., when tp_rr is reconfigured below rr_min). Test 5 instead covers the risk<=0
geometry guard which is the real practical filter protecting signal validity.
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
# Test 1: Textbook SHORT (bullish FVG formed, filled, then violated -> retest -> SELL)
# ---------------------------------------------------------------------------

def test_textbook_inverted_bullish_fvg_short():
    """
    Setup (all bars at 08:xx UTC, London session):

    Phase 1 -- FVG FORMATION (bars 0,1,2):
      Bullish FVG at i=2: high[0]=2000.0 < low[2]=2002.0
      Zone = [2000.0, 2002.0]

    Phase 2 -- FVG FILL (bars 3,4):
      Bar 3: price enters zone (low=2001.0 <= 2002.0 and high=2003 > 2000, so inside)
      Fill confirmed: yes.

    Phase 3 -- FVG VIOLATION (bar 5):
      Bar 5 close = 1999.0 < zone lower bound 2000.0 -> VIOLATED -> inverted to RESISTANCE
      Confirmation bar k=5.

    Phase 4 -- RETEST (bar 6):
      Bar 6 high = 2001.0 (wick enters zone [2000,2002]) -> retest of inverted FVG from below
      This is bar k=6; entry_index = 7.

    Expected: SELL signal with entry_index=7.
    SL above zone upper bound (2002.0 + buffer).
    TP = entry_open - 2.0*(SL - entry_open), must give R:R >= 1.5.
    """
    bars = _make_bars([
        # Phase 1: FVG Formation
        {"open": 1999, "high": 2000, "low": 1998, "close": 1999},   # 0: 08:00 high=2000
        {"open": 1999, "high": 2001, "low": 1998, "close": 2000},   # 1: 08:05 middle
        {"open": 2000, "high": 2005, "low": 2002, "close": 2004},   # 2: 08:10 low=2002 > high[0]=2000 -> bullish FVG zone=[2000,2002]
        # Phase 2: FVG Fill
        {"open": 2004, "high": 2005, "low": 2001, "close": 2003},   # 3: 08:15 low=2001 enters zone -> fill begins
        {"open": 2003, "high": 2003, "low": 2000, "close": 2002},   # 4: 08:20 still in zone
        # Phase 3: FVG Violation
        {"open": 2002, "high": 2003, "low": 1997, "close": 1999},   # 5: 08:25 close=1999 < 2000 -> VIOLATED k=5
        # Phase 4: Retest from below
        {"open": 1999, "high": 2001, "low": 1998, "close": 1999},   # 6: 08:30 high=2001 enters zone -> retest k=6
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
# Test 2: FVG fills but does NOT violate -> no signal
# ---------------------------------------------------------------------------

def test_fvg_fill_without_violation_gives_no_signal():
    """
    Bullish FVG at i=2 zone=[2000,2002].
    Price fills the zone (enters it) but NEVER closes below lower bound (2000).
    Price bounces back up from the zone -> FVG acts as support (normal behaviour).
    Expect: no signal (FVG is not inverted).
    """
    bars = _make_bars([
        # Phase 1: FVG Formation
        {"open": 1999, "high": 2000, "low": 1998, "close": 1999},   # 0 high=2000
        {"open": 1999, "high": 2001, "low": 1998, "close": 2000},   # 1 middle
        {"open": 2000, "high": 2005, "low": 2002, "close": 2004},   # 2 low=2002 > high[0]=2000 -> bullish FVG
        # Phase 2: Fill (enters zone but close stays above 2000)
        {"open": 2004, "high": 2005, "low": 2001, "close": 2003},   # 3 low=2001 in zone, close=2003 > 2000
        {"open": 2003, "high": 2006, "low": 2001, "close": 2005},   # 4 bounces -- close=2005 > 2000
        # NO violation -- price moves back up
        {"open": 2005, "high": 2008, "low": 2004, "close": 2007},   # 5 close=2007 > 2000, no violation
        {"open": 2007, "high": 2010, "low": 2006, "close": 2009},   # 6
    ], base_time="2025-06-02 08:00:00")

    signals = detect_signals(bars)
    assert len(signals) == 0, f"Expected no signal when FVG only fills (no violation), got {signals}"


# ---------------------------------------------------------------------------
# Test 3: Textbook LONG (bearish FVG inverted -> LONG continuation)
# ---------------------------------------------------------------------------

def test_textbook_inverted_bearish_fvg_long():
    """
    Phase 1 -- FVG FORMATION (bars 0,1,2):
      Bearish FVG at i=2: low[0]=2010.0 > high[2]=2008.0
      Zone = [2008.0, 2010.0]  (high[i]=2008 to low[i-2]=2010)

    Phase 2 -- FVG FILL (bar 3):
      Bar 3 high=2009.0 enters zone -> fill begins.

    Phase 3 -- FVG VIOLATION (bar 4):
      Bar 4 close=2011.0 > zone upper bound 2010.0 -> VIOLATED -> inverted to SUPPORT

    Phase 4 -- RETEST (bar 5):
      Bar 5 low=2009.0 (wick enters zone [2008,2010]) -> retest of inverted FVG from above
      entry_index = 6.

    Expected: BUY signal with entry_index=6.
    SL below zone lower bound (2008.0 - buffer).
    TP well above entry open for R:R >= 1.5.
    """
    bars = _make_bars([
        # Phase 1: Bearish FVG Formation
        {"open": 2011, "high": 2012, "low": 2010, "close": 2011},   # 0 low=2010
        {"open": 2011, "high": 2011, "low": 2009, "close": 2010},   # 1 middle
        {"open": 2010, "high": 2008, "low": 2006, "close": 2007},   # 2 high=2008 < low[0]=2010 -> bearish FVG zone=[2008,2010]
        # Phase 2: Fill
        {"open": 2007, "high": 2009, "low": 2006, "close": 2008},   # 3 high=2009 enters zone [2008,2010]
        # Phase 3: Violation
        {"open": 2008, "high": 2012, "low": 2007, "close": 2011},   # 4 close=2011 > 2010 -> VIOLATED k=4
        # Phase 4: Retest from above
        {"open": 2011, "high": 2012, "low": 2009, "close": 2011},   # 5 low=2009 enters zone -> retest k=5
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
# Test 4: Retest confirmed but entry bar is outside London/NY session -> no signal
# ---------------------------------------------------------------------------

def test_outside_session_gives_no_signal():
    """
    Identical setup to test_textbook_inverted_bullish_fvg_short but all bars
    start at 03:00 UTC (Asian session) -- outside London [07,11) and NY [12,16).
    Entry bar (index 7) would be at 03:30 UTC -> filtered out.
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
# Test 5: Geometry guard -- entry open above SL (risk <= 0) -> no signal
# ---------------------------------------------------------------------------

def test_invalid_geometry_gives_no_signal():
    """
    Inverted bullish FVG (SELL setup) where the entry bar opens ABOVE the SL
    level (i.e., entry_open > sl), making risk = sl - entry_open <= 0.
    The implementation guards against this with 'if risk <= 0: continue'.
    Expect: no signal (geometry invalid).

    Setup:
      FVG zone = [2000, 2002]; inverted -> SELL direction.
      SL = zone hi + buffer = 2002 + 0.30 = 2002.30
      Entry bar (k+1) opens at 2003.0 -> entry_open=2003 > sl=2002.30
      -> risk = sl - entry_open = 2002.30 - 2003.0 = -0.70 <= 0 -> skip.

    Note: for a SELL signal we need tp < entry_open < sl.
    If entry_open=2003 > sl=2002.30 the geometry check 'tp < entry_open < sl'
    also fails (2002.30 < 2003 is false for the < sl part means 2003 < 2002.30
    which is false). Either guard fires; the signal must be suppressed.
    """
    bars = _make_bars([
        # Phase 1: FVG Formation
        {"open": 1999, "high": 2000, "low": 1998, "close": 1999},   # 0 high=2000
        {"open": 1999, "high": 2001, "low": 1998, "close": 2000},   # 1 middle
        {"open": 2000, "high": 2005, "low": 2002, "close": 2004},   # 2 bullish FVG zone=[2000,2002]
        # Phase 2: Fill
        {"open": 2004, "high": 2005, "low": 2001, "close": 2003},   # 3 fill
        {"open": 2003, "high": 2003, "low": 2000, "close": 2002},   # 4 in zone
        # Phase 3: Violation
        {"open": 2002, "high": 2003, "low": 1997, "close": 1999},   # 5 VIOLATED
        # Phase 4: Retest
        {"open": 1999, "high": 2001, "low": 1998, "close": 1999},   # 6 retest k=6
        # Entry bar opens ABOVE sl (2002.30): entry_open=2003 > sl -> risk <= 0
        {"open": 2003, "high": 2004, "low": 2002, "close": 2003},   # 7 entry: open=2003 > sl=2002.30
    ], base_time="2025-06-02 08:00:00")

    signals = detect_signals(bars)
    sell_sigs = [s for s in signals if s.direction == "SELL"]
    assert len(sell_sigs) == 0, (
        f"Expected no signal when entry_open > sl (risk <= 0), got {sell_sigs}"
    )
