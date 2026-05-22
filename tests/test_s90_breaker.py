"""
test_s90_breaker.py
-------------------
TDD tests for strategies.xauusd_strategies.s90_breaker (Family F: Breaker Block Reclaim).

ALL tests use synthetic bars (hand-constructed DataFrames with tz-naive UTC
timestamps) — NO real cache, NO DB, NO network.

ICT Breaker logic recap (used in all tests below):
- DOWN impulse at bar I (body<0, close < prior_low) → "bullish OB" = last bullish candle
  in window before I. Zone = [ob.open, ob.close] (open < close for bullish).
  If later: close > ob.high → OB failed upward → BULLISH BREAKER (BUY on retest from above).
- UP impulse at bar I (body>0, close > prior_high) → "bearish OB" = last bearish candle
  in window before I. Zone = [ob.close, ob.open] (close < open for bearish).
  If later: close < ob.low → OB failed downward → BEARISH BREAKER (SELL on retest from below).

Test cases
----------
1. Bullish breaker: DOWN impulse → bullish OB formed → broken upward → LONG on retest.
2. Bearish breaker: UP impulse → bearish OB formed → broken downward → SHORT on retest.
3. No setup: flat bars, no impulse → empty signal list.
4. Stale breaker: breaker formed but max_breaker_age exceeded before retest → no signal.
"""
from __future__ import annotations

import pandas as pd

from strategies.xauusd_strategies.s90_breaker import generate_signals, DEFAULTS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# London session: 5-minute bars starting at 08:00 UTC (inside [07,11) window)
_BASE = pd.Timestamp("2025-06-02 08:00:00")


def _t(bar_index: int) -> pd.Timestamp:
    """Return a tz-naive UTC Timestamp for bar_index (5-min spacing)."""
    return _BASE + pd.Timedelta(minutes=5 * bar_index)


def _bar(ts: pd.Timestamp, open_: float, high: float, low: float, close: float,
         volume: float = 100.0, spread: float = 0.30) -> dict:
    return dict(time=ts, open=open_, high=high, low=low,
                close=close, volume=volume, spread=spread)


def make_bars(rows: list[dict]) -> pd.DataFrame:
    """Build a tz-naive UTC bars DataFrame from a list of bar dicts."""
    data = []
    for r in rows:
        data.append({
            "time":   pd.Timestamp(r["time"]),
            "open":   float(r["open"]),
            "high":   float(r["high"]),
            "low":    float(r["low"]),
            "close":  float(r["close"]),
            "volume": float(r.get("volume", 100.0)),
            "spread": float(r.get("spread", 0.30)),
        })
    return pd.DataFrame(data)


# ===========================================================================
# Test 1: Bullish Breaker → BUY
# ===========================================================================
class TestBullishBreaker:
    """
    Path: DOWN impulse → bullish OB → broken upward → BULLISH BREAKER → BUY on retest.

    Params: swing_lookback=5, ob_search_bars=10, displace_atr_mult=1.2, atr_period=14
    min_i = max(14, 5+10) = 15.  All action happens at bars 15+.

    Bars 0-14: context (15 bars needed to satisfy min_i=15).
      - Establish a downtrend: highs ~ 2004, prior_low ~ 2000.
      - Include at least one bullish candle in bars 5..14 (ob_search_bars=10 before bar15).
        bar13: bullish, open=2001.0, close=2002.5 → OB zone: low=2001.0, high=2002.5

    Bar 15: BIG DOWN impulse.
      - prior_low = min(low of bars 10..14) should be ≈ 2000.
      - close=1994 < prior_low=2000 ✓
      - body = 1994 - 2001 = -7; ATR at bar15 ≈ 1.0-1.5; 7 > 1.2*1.5 ✓
      - ob_search_bars=10: window=bars 5..14; last bullish = bar13
        → bullish OB: side="bullish", low=2001.0, high=2002.5

    Bar 16: UP bar breaks OB upward.
      - close=2003.5 > ob.high=2002.5 ✓ → bullish OB flips to BULLISH BREAKER

    Bars 17-18: move higher, away from zone.

    Bar 19: RETEST — dips back into zone [2001.0, 2002.5].
      - open=2008, low=2001.5 (enters zone), close=2004.0 (bullish rejection: close > open ✓)
      → BUY signal expected, entry_index=20.

    Bar 20: entry bar (open=2004.0).
    """

    def _build_bars(self):
        rows = [
            # Bars 0-14: context
            _bar(_t(0),  2005.0, 2006.0, 2004.0, 2005.5),  # 0 bull
            _bar(_t(1),  2005.5, 2006.0, 2004.5, 2004.8),  # 1 bear
            _bar(_t(2),  2004.8, 2005.5, 2004.0, 2005.2),  # 2 bull
            _bar(_t(3),  2005.2, 2005.8, 2004.5, 2004.7),  # 3 bear
            _bar(_t(4),  2004.7, 2005.2, 2003.8, 2004.2),  # 4 bear
            _bar(_t(5),  2004.2, 2004.8, 2003.5, 2004.0),  # 5 bear
            _bar(_t(6),  2004.0, 2004.5, 2003.0, 2003.5),  # 6 bear
            _bar(_t(7),  2003.5, 2004.0, 2003.0, 2003.8),  # 7 bull
            _bar(_t(8),  2003.8, 2004.2, 2003.2, 2003.5),  # 8 bear
            _bar(_t(9),  2003.5, 2004.0, 2003.0, 2003.8),  # 9 bull
            _bar(_t(10), 2003.8, 2004.2, 2002.5, 2003.0),  # 10 bear  prior_low track starts
            _bar(_t(11), 2003.0, 2003.5, 2001.5, 2002.0),  # 11 bear  prior_low ≈ 2001.5
            _bar(_t(12), 2002.0, 2002.8, 2001.2, 2001.5),  # 12 bear  prior_low ≈ 2001.2
            _bar(_t(13), 2001.5, 2002.8, 2001.0, 2002.5),  # 13 BULL OB candidate
                                                             # open=2001.5, close=2002.5
                                                             # OB zone: low=2001.5, high=2002.5
            _bar(_t(14), 2002.5, 2003.0, 2001.5, 2002.0),  # 14 bear
                                                             # prior_low = min(low[10..14])
                                                             # = min(2002.5,2001.5,2001.2,2001.0,2001.5) = 2001.0
            # Bar 15: BIG DOWN impulse
            # prior_low = min(low of bars 10..14) = 2001.0
            # body = 1994 - 2002 = -8.0  (ATR~1.2 → 8.0 > 1.2*1.2=1.44 ✓)
            # close = 1994.0 < 2001.0 ✓
            # ob_search_bars=10: window = bars 5..14; last bullish = bar13
            # OB: side="bullish", low=ob.open=2001.5, high=ob.close=2002.5
            _bar(_t(15), 2002.0, 2002.5, 1993.5, 1994.0),  # 15 down impulse → bullish OB formed
            # Bar 16: UP bar — close > ob.high=2002.5 → bullish OB flips to BULLISH BREAKER
            _bar(_t(16), 1994.0, 2003.5, 1993.8, 2003.5),  # 16 up bar (close=2003.5 > 2002.5 ✓)
            # Bars 17-18: move higher
            _bar(_t(17), 2003.5, 2007.0, 2003.0, 2006.5),  # 17
            _bar(_t(18), 2006.5, 2009.0, 2006.0, 2008.5),  # 18
            # Bar 19: RETEST — dips back into zone [2001.5, 2002.5]
            # low=2002.0 (enters zone), close=2009.0 > open=2001.5 → bullish rejection ✓
            _bar(_t(19), 2001.5, 2010.0, 2002.0, 2009.0),  # 19 retest (k=19)
            # Bar 20: entry (entry_index=20)
            _bar(_t(20), 2005.0, 2012.0, 2004.5, 2011.0),  # 20 entry bar
        ]
        return make_bars(rows)

    def test_bullish_breaker_produces_buy_signal(self):
        bars = self._build_bars()
        signals = generate_signals(bars, **DEFAULTS)
        buy_signals = [s for s in signals if s.direction == "BUY"]
        assert len(buy_signals) >= 1, \
            f"Expected BUY signal from bullish breaker retest; got signals={signals}"

    def test_bullish_breaker_entry_index_after_retest(self):
        """entry_index must be k+1 = 20 (k=19 is the retest bar)."""
        bars = self._build_bars()
        signals = generate_signals(bars, **DEFAULTS)
        buy_signals = [s for s in signals if s.direction == "BUY"]
        assert len(buy_signals) >= 1, f"No BUY signals: {signals}"
        sig = buy_signals[0]
        assert sig.entry_index == 20, f"Expected entry_index=20 (k+1), got {sig.entry_index}"

    def test_bullish_breaker_sl_below_entry_open(self):
        """SL must be strictly below the entry bar open (below OB zone low - buffer)."""
        bars = self._build_bars()
        signals = generate_signals(bars, **DEFAULTS)
        buy_signals = [s for s in signals if s.direction == "BUY"]
        assert len(buy_signals) >= 1
        sig = buy_signals[0]
        entry_open = float(bars.iloc[sig.entry_index]["open"])
        assert sig.sl < entry_open, f"SL {sig.sl} must be < entry open {entry_open}"

    def test_bullish_breaker_tp_above_entry_open(self):
        """TP must be strictly above the entry bar open."""
        bars = self._build_bars()
        signals = generate_signals(bars, **DEFAULTS)
        buy_signals = [s for s in signals if s.direction == "BUY"]
        assert len(buy_signals) >= 1
        sig = buy_signals[0]
        entry_open = float(bars.iloc[sig.entry_index]["open"])
        assert sig.tp > entry_open, f"TP {sig.tp} must be > entry open {entry_open}"


# ===========================================================================
# Test 2: Bearish Breaker → SELL
# ===========================================================================
class TestBearishBreaker:
    """
    Path: UP impulse → bearish OB → broken downward → BEARISH BREAKER → SELL on retest.

    Bars 0-14: context (uptrend, prior_high ~ 2004).
      - Include at least one bearish candle near bars 5..14.
        bar13: bearish, open=2003.0, close=2001.5 → OB zone: low=2001.5, high=2003.0

    Bar 15: BIG UP impulse.
      - prior_high = max(high of bars 10..14) ≈ 2004.
      - close=2010.0 > prior_high=2004 ✓
      - body = 2010.0 - 2003.0 = 7.0; ATR ≈ 1.2; 7 > 1.2*1.2 ✓
      - ob_search_bars=10: window=bars 5..14; last bearish = bar13
        → bearish OB: side="bearish", low=ob.close=2001.5, high=ob.open=2003.0

    Bar 16: DOWN bar breaks OB downward.
      - close=2001.0 < ob.low=2001.5 ✓ → bearish OB flips to BEARISH BREAKER

    Bars 17-18: move lower.

    Bar 19: RETEST — bounces up into zone [2001.5, 2003.0].
      - open=1997, high=2002.5 (enters zone), close=1996.5 (bearish: close < open ✓)
      → SELL signal expected, entry_index=20.

    Bar 20: entry bar (open=1996.5).
    """

    def _build_bars(self):
        rows = [
            # Bars 0-14: uptrend context
            _bar(_t(0),  1996.0, 1997.5, 1995.5, 1997.0),  # 0 bull
            _bar(_t(1),  1997.0, 1998.0, 1996.5, 1997.5),  # 1 bull
            _bar(_t(2),  1997.5, 1998.5, 1997.0, 1997.8),  # 2 bull
            _bar(_t(3),  1997.8, 1999.0, 1997.5, 1998.5),  # 3 bull
            _bar(_t(4),  1998.5, 1999.5, 1998.0, 1999.0),  # 4 bull
            _bar(_t(5),  1999.0, 2000.0, 1998.5, 1999.5),  # 5 bull
            _bar(_t(6),  1999.5, 2000.5, 1999.0, 2000.0),  # 6 bull
            _bar(_t(7),  2000.0, 2001.0, 1999.5, 2000.5),  # 7 bull
            _bar(_t(8),  2000.5, 2001.5, 2000.0, 2001.0),  # 8 bull
            _bar(_t(9),  2001.0, 2002.0, 2000.5, 2001.5),  # 9 bull
            _bar(_t(10), 2001.5, 2002.5, 2001.0, 2002.0),  # 10 bull  prior_high track
            _bar(_t(11), 2002.0, 2003.0, 2001.5, 2002.5),  # 11 bull  prior_high ≈ 3.0
            _bar(_t(12), 2002.5, 2003.5, 2002.0, 2003.0),  # 12 bull  prior_high ≈ 3.5
            _bar(_t(13), 2003.0, 2003.5, 2001.0, 2001.5),  # 13 BEAR OB candidate
                                                             # open=2003.0, close=2001.5
                                                             # OB zone: low=2001.5, high=2003.0
            _bar(_t(14), 2001.5, 2004.0, 2001.0, 2003.5),  # 14 bull  prior_high ≈ 4.0
                                                             # prior_high = max(high[10..14])
                                                             # = max(2002.5,3.0,3.5,3.5,4.0) = 4.0
            # Bar 15: BIG UP impulse
            # prior_high = max(high of bars 10..14) = 2004.0
            # body = 2010.0 - 2003.0 = 7.0 (ATR~1.2 → 7.0 > 1.2*1.2=1.44 ✓)
            # close=2010.0 > 2004.0 ✓
            # window=bars 5..14; last bearish = bar13 (open=2003.0, close=2001.5)
            # OB: side="bearish", low=2001.5, high=2003.0
            _bar(_t(15), 2003.0, 2010.5, 2002.8, 2010.0),  # 15 up impulse → bearish OB formed
            # Bar 16: DOWN bar — close < ob.low=2001.5 → bearish OB flips to BEARISH BREAKER
            _bar(_t(16), 2010.0, 2010.5, 2001.0, 2001.0),  # 16 down bar (close=2001.0 < 2001.5 ✓)
            # Bars 17-18: move lower (must stay BELOW zone low=2001.5)
            _bar(_t(17), 2001.0, 2001.4, 1997.0, 1997.5),  # 17 — high=2001.4 < zone.low=2001.5
            _bar(_t(18), 1997.5, 1998.0, 1994.0, 1994.5),  # 18 — well below zone
            # Bar 19: RETEST — bounces up into zone [2001.5, 2003.0]
            # open=1994.5, high=2002.5 (enters zone), close=1994.0 (bearish: close < open ✓)
            _bar(_t(19), 1994.5, 2002.5, 1993.5, 1994.0),  # 19 retest (k=19)
            # Bar 20: entry (entry_index=20)
            _bar(_t(20), 1994.0, 1994.5, 1988.0, 1989.0),  # 20 entry bar
        ]
        return make_bars(rows)

    def test_bearish_breaker_produces_sell_signal(self):
        bars = self._build_bars()
        signals = generate_signals(bars, **DEFAULTS)
        sell_signals = [s for s in signals if s.direction == "SELL"]
        assert len(sell_signals) >= 1, \
            f"Expected SELL signal from bearish breaker; got signals={signals}"

    def test_bearish_breaker_entry_index_after_retest(self):
        """entry_index = k+1 = 20 (k=19 is the retest bar)."""
        bars = self._build_bars()
        signals = generate_signals(bars, **DEFAULTS)
        sell_signals = [s for s in signals if s.direction == "SELL"]
        assert len(sell_signals) >= 1
        sig = sell_signals[0]
        assert sig.entry_index == 20, f"Expected entry_index=20, got {sig.entry_index}"

    def test_bearish_breaker_sl_above_entry_open(self):
        """SL must be strictly above the entry bar open (above OB zone high + buffer)."""
        bars = self._build_bars()
        signals = generate_signals(bars, **DEFAULTS)
        sell_signals = [s for s in signals if s.direction == "SELL"]
        assert len(sell_signals) >= 1
        sig = sell_signals[0]
        entry_open = float(bars.iloc[sig.entry_index]["open"])
        assert sig.sl > entry_open, f"SL {sig.sl} must be > entry open {entry_open}"

    def test_bearish_breaker_tp_below_entry_open(self):
        """TP must be strictly below the entry bar open."""
        bars = self._build_bars()
        signals = generate_signals(bars, **DEFAULTS)
        sell_signals = [s for s in signals if s.direction == "SELL"]
        assert len(sell_signals) >= 1
        sig = sell_signals[0]
        entry_open = float(bars.iloc[sig.entry_index]["open"])
        assert sig.tp < entry_open, f"TP {sig.tp} must be < entry open {entry_open}"


# ===========================================================================
# Test 3: No setup → no signals
# ===========================================================================
class TestNoSetup:
    """Perfectly flat sideways bars — no displacement, no OB, no signals."""

    def _build_bars(self):
        rows = [
            # 25 bars of flat sideways with open=close (zero body → no impulse)
            _bar(_t(i), 2000.0, 2000.5, 1999.5, 2000.0)
            for i in range(25)
        ]
        return make_bars(rows)

    def test_no_setup_produces_no_signals(self):
        bars = self._build_bars()
        signals = generate_signals(bars, **DEFAULTS)
        assert signals == [], \
            f"Expected no signals from flat market, got {len(signals)}: {signals}"


# ===========================================================================
# Test 4: Stale breaker is pruned → no signal fires
# ===========================================================================
class TestStaleBreaker:
    """
    Same up-impulse-bearish-OB-breaks-down path as TestBearishBreaker,
    but with 10 padding bars between the break and the retest so the
    breaker age (10) exceeds max_breaker_age=5 → breaker pruned → no signal.
    """

    def _build_bars(self):
        rows = [
            # Bars 0-14: same uptrend context
            _bar(_t(0),  1996.0, 1997.5, 1995.5, 1997.0),
            _bar(_t(1),  1997.0, 1998.0, 1996.5, 1997.5),
            _bar(_t(2),  1997.5, 1998.5, 1997.0, 1997.8),
            _bar(_t(3),  1997.8, 1999.0, 1997.5, 1998.5),
            _bar(_t(4),  1998.5, 1999.5, 1998.0, 1999.0),
            _bar(_t(5),  1999.0, 2000.0, 1998.5, 1999.5),
            _bar(_t(6),  1999.5, 2000.5, 1999.0, 2000.0),
            _bar(_t(7),  2000.0, 2001.0, 1999.5, 2000.5),
            _bar(_t(8),  2000.5, 2001.5, 2000.0, 2001.0),
            _bar(_t(9),  2001.0, 2002.0, 2000.5, 2001.5),
            _bar(_t(10), 2001.5, 2002.5, 2001.0, 2002.0),
            _bar(_t(11), 2002.0, 2003.0, 2001.5, 2002.5),
            _bar(_t(12), 2002.5, 2003.5, 2002.0, 2003.0),
            _bar(_t(13), 2003.0, 2003.5, 2001.0, 2001.5),  # bearish OB candidate
            _bar(_t(14), 2001.5, 2004.0, 2001.0, 2003.5),
            # Bar 15: up impulse → bearish OB formed
            _bar(_t(15), 2003.0, 2010.5, 2002.8, 2010.0),
            # Bar 16: down bar → breaker formed at bar 16 (age starts here)
            _bar(_t(16), 2010.0, 2010.5, 2001.0, 2001.0),
            # Bars 17-26: 10 padding bars — all BELOW zone low=2001.5 (highs < 2001.5)
            _bar(_t(17), 2001.0, 2001.4, 1997.0, 1997.5),  # high=2001.4 < 2001.5
            _bar(_t(18), 1997.5, 1998.0, 1994.0, 1994.5),
            _bar(_t(19), 1994.5, 1995.0, 1993.0, 1993.5),
            _bar(_t(20), 1993.5, 1994.5, 1992.5, 1993.0),
            _bar(_t(21), 1993.0, 1994.0, 1992.0, 1992.5),
            _bar(_t(22), 1992.5, 1993.5, 1991.5, 1992.0),
            _bar(_t(23), 1992.0, 1993.0, 1991.0, 1991.5),
            _bar(_t(24), 1991.5, 1992.5, 1990.5, 1991.0),
            _bar(_t(25), 1991.0, 1992.0, 1990.0, 1990.5),
            _bar(_t(26), 1990.5, 1991.5, 1989.5, 1990.0),
            # Bar 27: retest — bounces into zone [2001.5, 2003.0] (age = 27-16 = 11 > 5)
            _bar(_t(27), 1990.0, 2002.5, 1989.5, 1989.8),  # bearish rejection
            # Bar 28: would be entry bar
            _bar(_t(28), 1989.8, 1990.5, 1985.0, 1986.0),
        ]
        return make_bars(rows)

    def test_stale_breaker_no_signal(self):
        bars = self._build_bars()
        # max_breaker_age=5: at retest bar27, age=27-16=11 > 5 → pruned → no signal
        params = {**DEFAULTS, "max_breaker_age": 5}
        signals = generate_signals(bars, **params)
        sell_signals = [s for s in signals if s.direction == "SELL"]
        assert len(sell_signals) == 0, \
            f"Expected no SELL signal (stale, age=11 > max=5); got {len(sell_signals)}"
