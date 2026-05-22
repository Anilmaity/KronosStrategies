"""
test_s90_liqsweep_fvg.py
------------------------
TDD tests for strategies.xauusd_strategies.s90_liqsweep_fvg
(Task 5a – Family A: Liquidity sweep + FVG fade).

ALL tests use synthetic bar DataFrames (tz-naive UTC timestamps).
NO real cache, NO network, NO DB.

Coverage
--------
1. Textbook SHORT setup (BSL sweep + bearish FVG + bearish 4H bias) -> one SELL
   Signal returned with entry_index strictly AFTER the confirmation bar.
2. Textbook LONG setup (SSL sweep + bullish FVG + bullish 4H bias) -> one BUY
   Signal returned.
3. Sweep detected but NO aligned bearish FVG within lookback -> no signal.
4. Sweep detected but HTF 4H bias is bullish (misaligned for short) -> no signal.
5. No sweep at all -> no signal.
6. Sweep confirmed but R:R < 1:1 (TP too close) -> no signal.
7. Entry-bar hour outside London/NY session -> no signal.
"""
from __future__ import annotations

import pandas as pd
import pytest

from strategies.xauusd_strategies.s90_liqsweep_fvg import detect_signals, _htf_bias

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bars(rows: list[dict], base_time: str = "2025-06-02 08:00:00") -> pd.DataFrame:
    """Build a minimal OHLCV + spread DataFrame with tz-naive UTC timestamps,
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


def _make_htf_bars(rows: list[dict], base_time: str = "2025-06-01 00:00:00") -> pd.DataFrame:
    """Build 4H bars with tz-naive UTC timestamps, each 4 hours apart."""
    t0 = pd.Timestamp(base_time)
    data = []
    for i, r in enumerate(rows):
        t = r.get("time", t0 + pd.Timedelta(hours=4 * i))
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
# Test 1: Textbook SHORT (BSL sweep + bearish FVG + bearish 4H bias)
# ---------------------------------------------------------------------------

def test_textbook_short_setup_returns_sell_signal():
    """
    Session high = 2010.0 (established in bars 0-3).
    Bearish FVG at i=3: low[1]=2010 > high[3]=2009 (2-point bearish gap).
    FVG zone = [2009, 2010] — contains the swept level 2010.
    Bar k=4: BSL sweep — high=2012 > session_high=2010, close=2007 < 2010.
    4H bias is bearish (lower-high lower-low structure).
    Session low = 2000 (set in bar 0) → TP=2000, RR ≈ 1.27 ≥ 1.0.
    Entry at bar 5 (k+1=5), within London session (08:xx UTC).
    Expect: one SELL signal with entry_index = 5.
    """
    # 4H bars: two swing groups — second has lower-high & lower-low → bearish
    htf_bars = _make_htf_bars([
        {"open": 2020, "high": 2030, "low": 2010, "close": 2025},  # swing group 1 bar A
        {"open": 2025, "high": 2028, "low": 2005, "close": 2008},  # swing group 1 bar B
        {"open": 2008, "high": 2022, "low": 2003, "close": 2020},  # swing group 2 bar A
        {"open": 2020, "high": 2021, "low": 2000, "close": 2004},  # swing group 2 bar B (lower-high, lower-low)
    ], base_time="2025-06-01 00:00:00")

    # 5m bars: London session 08:00+ UTC
    # bar 0: session_low=2000 established here (low=2000)
    # bar 1: session_high=2010 (high=2010); low[1]=2010 (for bearish FVG at i=3)
    # bar 2: middle bar
    # bar 3: high[3]=2009 < low[1]=2010 → bearish FVG zone [2009, 2010]
    # bar 4: BSL sweep — high=2012 > 2010, close=2007 < 2010
    # bar 5: entry bar (k+1); entry open = 2007
    # SL = 2012 + 0.50 = 2012.5; TP = session_low = 2000
    # Risk = 2012.5-2007 = 5.5; Reward = 2007-2000 = 7.0; RR = 1.27
    bars_5m = _make_bars([
        {"open": 2005, "high": 2008, "low": 2000, "close": 2007},   # 0: 08:00, session_low=2000
        {"open": 2007, "high": 2010, "low": 2010, "close": 2010},   # 1: 08:05, session_high=2010, low[1]=2010
        {"open": 2010, "high": 2010, "low": 2009, "close": 2009},   # 2: 08:10, middle
        {"open": 2009, "high": 2009, "low": 2008, "close": 2008},   # 3: 08:15, high[3]=2009 < low[1]=2010 → FVG
        {"open": 2008, "high": 2012, "low": 2007, "close": 2007},   # 4: 08:20, BSL sweep
        {"open": 2007, "high": 2008, "low": 2004, "close": 2005},   # 5: 08:25, entry bar
        {"open": 2005, "high": 2006, "low": 2003, "close": 2004},   # 6: 08:30
        {"open": 2004, "high": 2005, "low": 2000, "close": 2001},   # 7: 08:35, hits TP
    ], base_time="2025-06-02 08:00:00")

    signals = detect_signals(bars_5m, htf_bars)

    assert len(signals) >= 1, f"Expected at least 1 signal, got {len(signals)}"
    sell_sigs = [s for s in signals if s.direction == "SELL"]
    assert len(sell_sigs) >= 1, f"Expected SELL signal, got {signals}"
    sig = sell_sigs[0]
    # entry_index must be STRICTLY after the sweep bar (bar 4)
    assert sig.entry_index > 4, (
        f"entry_index={sig.entry_index} must be > 4 (strictly after sweep bar)"
    )
    assert sig.entry_index == 5, f"Expected entry at bar 5, got {sig.entry_index}"
    assert sig.direction == "SELL"
    # SL must be above the swept high (2012)
    assert sig.sl > 2012, f"SL={sig.sl} should be above sweep high 2012"
    # TP must be below entry open (2007)
    assert sig.tp < 2007, f"TP={sig.tp} should be below entry open"


# ---------------------------------------------------------------------------
# Test 2: Textbook LONG (SSL sweep + bullish FVG + bullish 4H bias)
# ---------------------------------------------------------------------------

def test_textbook_long_setup_returns_buy_signal():
    """
    Session high = 2005 (bar 0). Session low = 1989 (bar 1, low=1989).
    Bullish FVG at i=3: high[1]=1991 < low[3]=1993 (2-point bullish gap).
    FVG zone = [1991, 1993] — straddles session_low=1989 proximity (dist=2 < 5).
    Bar k=4: SSL sweep — low=1987 < session_low=1989, close=1992 > 1989.
    4H bias is bullish (higher-high higher-low structure across two groups).
    Entry at bar 5 (k+1=5), within London session (08:xx UTC).
    SL = 1987 - 0.50 = 1986.5; TP = session_high = 2005.
    Risk = 1992-1986.5 = 5.5; Reward = 2005-1992 = 13.0; RR = 2.36.
    Expect: one BUY signal with entry_index = 5.
    """
    # 4H bars: two swing groups — second has higher-high & higher-low → bullish
    htf_bars = _make_htf_bars([
        {"open": 1980, "high": 1990, "low": 1975, "close": 1985},  # swing group 1 bar A
        {"open": 1985, "high": 1995, "low": 1982, "close": 1992},  # swing group 1 bar B
        {"open": 1992, "high": 2000, "low": 1988, "close": 1998},  # swing group 2 bar A (higher-high)
        {"open": 1998, "high": 2005, "low": 1993, "close": 2002},  # swing group 2 bar B (higher-low)
    ], base_time="2025-06-01 00:00:00")

    # 5m bars: London session 08:00+ UTC
    # bar 0: session_high=2005 (high=2005)
    # bar 1: high[1]=1991, low=1989 → session_low=1989
    # bar 2: middle
    # bar 3: low[3]=1993 > high[1]=1991 → bullish FVG zone [1991, 1993]
    #         FVG zone is near session_low=1989 (dist=|1989-1991|=2 ≤ 5 ✓)
    # bar 4: SSL sweep — low=1987 < session_low=1989, close=1992 > 1989
    # bar 5: entry bar; entry open=1992
    # SL = 1987 - 0.50 = 1986.5; TP = session_high = 2005
    bars_5m = _make_bars([
        {"open": 2000, "high": 2005, "low": 1993, "close": 1995},   # 0: 08:00, session_high=2005
        {"open": 1990, "high": 1991, "low": 1989, "close": 1990},   # 1: 08:05, session_low=1989, high[1]=1991
        {"open": 1991, "high": 1992, "low": 1990, "close": 1991},   # 2: 08:10, middle
        {"open": 1992, "high": 1996, "low": 1993, "close": 1994},   # 3: 08:15, low[3]=1993>high[1]=1991 → bullish FVG
        {"open": 1994, "high": 1995, "low": 1987, "close": 1992},   # 4: 08:20, SSL sweep
        {"open": 1992, "high": 1994, "low": 1991, "close": 1993},   # 5: 08:25, entry bar
        {"open": 1993, "high": 2005, "low": 1992, "close": 2004},   # 6: 08:30, hits TP
    ], base_time="2025-06-02 08:00:00")

    signals = detect_signals(bars_5m, htf_bars)

    buy_sigs = [s for s in signals if s.direction == "BUY"]
    assert len(buy_sigs) >= 1, f"Expected BUY signal, got {signals}"
    sig = buy_sigs[0]
    assert sig.entry_index > 4, (
        f"entry_index={sig.entry_index} must be > 4 (strictly after sweep bar)"
    )
    assert sig.entry_index == 5, f"Expected entry at bar 5, got {sig.entry_index}"
    assert sig.direction == "BUY"
    # SL must be below the swept low (1987)
    assert sig.sl < 1987, f"SL={sig.sl} should be below sweep low 1987"
    # TP must be above entry open (1992)
    assert sig.tp > 1992, f"TP={sig.tp} should be above entry open"


# ---------------------------------------------------------------------------
# Test 3: Sweep detected but no aligned bearish FVG within lookback
# ---------------------------------------------------------------------------

def test_no_fvg_within_lookback_gives_no_signal():
    """
    BSL sweep at bar 4, bearish 4H bias, but no bearish FVG in the last
    FVG_LOOKBACK bars near the swept level -> no signal.
    All bars have overlapping OHLC ranges — no gap exists.
    """
    # 4H bars: bearish structure
    htf_bars = _make_htf_bars([
        {"open": 2020, "high": 2030, "low": 2010, "close": 2025},
        {"open": 2025, "high": 2028, "low": 2005, "close": 2008},
        {"open": 2008, "high": 2022, "low": 2003, "close": 2020},
        {"open": 2020, "high": 2021, "low": 2000, "close": 2004},
    ], base_time="2025-06-01 00:00:00")

    # 5m bars: sweep at bar 4, but NO bearish FVG (all bars overlap in price)
    # For a bearish FVG: low[i-2] > high[i]. Here every bar overlaps with neighbors.
    bars_5m = _make_bars([
        {"open": 2005, "high": 2010, "low": 2003, "close": 2007},   # 0
        {"open": 2007, "high": 2010, "low": 2006, "close": 2009},   # 1 session high=2010
        {"open": 2009, "high": 2010, "low": 2008, "close": 2009},   # 2 overlapping: low[2]=2008
        {"open": 2009, "high": 2010, "low": 2008, "close": 2009},   # 3 overlapping: high[3]=2010 >= low[1]=2006
        {"open": 2009, "high": 2012, "low": 2008, "close": 2008},   # 4 sweep: high>2010, close<2010
        {"open": 2008, "high": 2009, "low": 2003, "close": 2007},   # 5 entry bar (if signal generated)
    ], base_time="2025-06-02 08:00:00")

    signals = detect_signals(bars_5m, htf_bars)
    assert len(signals) == 0, f"Expected no signal without FVG, got {signals}"


# ---------------------------------------------------------------------------
# Test 4: Sweep + FVG present but HTF bias misaligned
# ---------------------------------------------------------------------------

def test_htf_bias_misaligned_gives_no_signal():
    """
    BSL sweep + bearish FVG (same bar layout as textbook short),
    but 4H bias is BULLISH -> no SELL signal emitted.
    """
    # 4H bars: BULLISH structure (higher highs, higher lows in both groups)
    htf_bars_bullish = _make_htf_bars([
        {"open": 1980, "high": 1990, "low": 1975, "close": 1985},
        {"open": 1985, "high": 1995, "low": 1982, "close": 1992},
        {"open": 1992, "high": 2000, "low": 1988, "close": 1998},
        {"open": 1998, "high": 2005, "low": 1993, "close": 2002},
    ], base_time="2025-06-01 00:00:00")

    # 5m bars: same sweep + FVG as textbook short (bearish FVG at i=3, sweep at i=4)
    bars_5m = _make_bars([
        {"open": 2005, "high": 2008, "low": 2000, "close": 2007},   # 0: session_low=2000
        {"open": 2007, "high": 2010, "low": 2010, "close": 2010},   # 1: session_high=2010, low[1]=2010
        {"open": 2010, "high": 2010, "low": 2009, "close": 2009},   # 2: middle
        {"open": 2009, "high": 2009, "low": 2008, "close": 2008},   # 3: high[3]=2009 < low[1]=2010 → FVG
        {"open": 2008, "high": 2012, "low": 2007, "close": 2007},   # 4: BSL sweep
        {"open": 2007, "high": 2008, "low": 2004, "close": 2005},   # 5: entry bar
    ], base_time="2025-06-02 08:00:00")

    signals = detect_signals(bars_5m, htf_bars_bullish)
    sell_sigs = [s for s in signals if s.direction == "SELL"]
    assert len(sell_sigs) == 0, (
        f"Expected no SELL signal when 4H bias is bullish, got {sell_sigs}"
    )


# ---------------------------------------------------------------------------
# Test 5: No sweep at all -> no signal
# ---------------------------------------------------------------------------

def test_no_sweep_gives_no_signal():
    """
    Price moves around session high but never wicks ABOVE it -> no BSL sweep.
    """
    htf_bars = _make_htf_bars([
        {"open": 2020, "high": 2030, "low": 2010, "close": 2025},
        {"open": 2025, "high": 2028, "low": 2005, "close": 2008},
        {"open": 2008, "high": 2022, "low": 2003, "close": 2020},
        {"open": 2020, "high": 2021, "low": 2000, "close": 2004},
    ], base_time="2025-06-01 00:00:00")

    # Session high = 2010; all bars stay at or below
    bars_5m = _make_bars([
        {"open": 2005, "high": 2008, "low": 2003, "close": 2007},
        {"open": 2007, "high": 2010, "low": 2006, "close": 2009},  # session high=2010
        {"open": 2009, "high": 2009, "low": 2007, "close": 2008},  # no sweep
        {"open": 2008, "high": 2009, "low": 2006, "close": 2007},
        {"open": 2007, "high": 2008, "low": 2005, "close": 2006},
    ], base_time="2025-06-02 08:00:00")

    signals = detect_signals(bars_5m, htf_bars)
    assert len(signals) == 0, f"Expected no signal, got {signals}"


# ---------------------------------------------------------------------------
# Test 6: R:R < 1:1 (TP too close) -> no signal
# ---------------------------------------------------------------------------

def test_rr_below_minimum_gives_no_signal():
    """
    Sweep + FVG + bias all aligned, but session low (TP target) is too close
    to entry so R:R < MIN_RR (1.0) and the trade is skipped.

    Setup uses same bar structure as textbook short but session_low=2006
    (barely below entry open=2007):
      SL = 2012 + 0.5 = 2012.5; Risk = 2012.5 - 2007 = 5.5
      TP = 2006; Reward = 2007 - 2006 = 1.0; R:R = 0.18 < 1.0 → skip.
    """
    htf_bars = _make_htf_bars([
        {"open": 2020, "high": 2030, "low": 2010, "close": 2025},
        {"open": 2025, "high": 2028, "low": 2005, "close": 2008},
        {"open": 2008, "high": 2022, "low": 2003, "close": 2020},
        {"open": 2020, "high": 2021, "low": 2000, "close": 2004},
    ], base_time="2025-06-01 00:00:00")

    # Session low is very close to entry; R:R < 1.0
    bars_5m = _make_bars([
        {"open": 2005, "high": 2008, "low": 2006, "close": 2007},   # 0: session_low=2006 (too close)
        {"open": 2007, "high": 2010, "low": 2010, "close": 2010},   # 1: session_high=2010, low[1]=2010
        {"open": 2010, "high": 2010, "low": 2009, "close": 2009},   # 2: middle
        {"open": 2009, "high": 2009, "low": 2008, "close": 2008},   # 3: high[3]=2009 < low[1]=2010 → FVG
        {"open": 2008, "high": 2012, "low": 2007, "close": 2007},   # 4: BSL sweep
        {"open": 2007, "high": 2008, "low": 2006, "close": 2007},   # 5: entry bar (if emitted)
    ], base_time="2025-06-02 08:00:00")

    signals = detect_signals(bars_5m, htf_bars)
    assert len(signals) == 0, (
        f"Expected no signal when R:R is below minimum, got {signals}"
    )


# ---------------------------------------------------------------------------
# Test 7: Entry bar outside London/NY session -> no signal
# ---------------------------------------------------------------------------

def test_outside_session_gives_no_signal():
    """
    All bar times are 03:00 UTC (Asian session) — outside London [07,11)
    and NY [12,16).  Even with a perfect sweep+FVG+bias, no signal.
    Same bar layout as textbook short but shifted to 03:00 UTC.
    """
    htf_bars = _make_htf_bars([
        {"open": 2020, "high": 2030, "low": 2010, "close": 2025},
        {"open": 2025, "high": 2028, "low": 2005, "close": 2008},
        {"open": 2008, "high": 2022, "low": 2003, "close": 2020},
        {"open": 2020, "high": 2021, "low": 2000, "close": 2004},
    ], base_time="2025-06-01 00:00:00")

    # Identical sweep+FVG to textbook short but in Asian session (03:00 UTC)
    bars_5m = _make_bars([
        {"open": 2005, "high": 2008, "low": 2000, "close": 2007},
        {"open": 2007, "high": 2010, "low": 2010, "close": 2010},
        {"open": 2010, "high": 2010, "low": 2009, "close": 2009},
        {"open": 2009, "high": 2009, "low": 2008, "close": 2008},
        {"open": 2008, "high": 2012, "low": 2007, "close": 2007},
        {"open": 2007, "high": 2008, "low": 2004, "close": 2005},
    ], base_time="2025-06-02 03:00:00")  # <-- Asian session, not London/NY

    signals = detect_signals(bars_5m, htf_bars)
    assert len(signals) == 0, (
        f"Expected no signal outside London/NY session, got {signals}"
    )


# ---------------------------------------------------------------------------
# Test 8: _htf_bias helper unit tests
# ---------------------------------------------------------------------------

def test_htf_bias_bearish_lower_high_lower_low():
    """4H bars with lower-high AND lower-low -> 'bearish'."""
    htf_bars = _make_htf_bars([
        {"open": 2020, "high": 2030, "low": 2010, "close": 2025},
        {"open": 2025, "high": 2028, "low": 2005, "close": 2008},  # lower high (2028<2030), lower low (2005<2010)
    ])
    assert _htf_bias(htf_bars, pd.Timestamp("2025-06-01 08:00:00")) == "bearish"


def test_htf_bias_bullish_higher_high_higher_low():
    """4H bars with higher-high AND higher-low -> 'bullish'."""
    htf_bars = _make_htf_bars([
        {"open": 1980, "high": 1990, "low": 1975, "close": 1985},
        {"open": 1985, "high": 1995, "low": 1982, "close": 1992},  # higher high (1995>1990), higher low (1982>1975)
    ])
    assert _htf_bias(htf_bars, pd.Timestamp("2025-06-01 08:00:00")) == "bullish"


def test_htf_bias_neutral_mixed():
    """Mixed signals (higher high but lower low) -> 'neutral'."""
    htf_bars = _make_htf_bars([
        {"open": 2000, "high": 2010, "low": 1990, "close": 2005},
        {"open": 2005, "high": 2015, "low": 1985, "close": 2008},  # higher high, lower low
    ])
    assert _htf_bias(htf_bars, pd.Timestamp("2025-06-01 08:00:00")) == "neutral"
