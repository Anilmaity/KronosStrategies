"""
C14 - Session Overlap Break (London-NY ORB variant) - XAUUSD
------------------------------------------------------------
Adaptation of London-NY Session Overlap Vol Trade.

Setup:
  - Build the 12:00-13:30 UTC range (the pre-NY consolidation around the
    London-NY overlap).
  - Trigger window 13:30-15:30 UTC: a confirming 5m close above range
    high (long) or below range low (short).
  - SL = opposite range boundary.
  - TP = 1x range width from breakout.
  - Range-width sanity floor and ceiling to avoid noise & chop.
"""

from __future__ import annotations
import pandas as pd
import numpy as np

from backtest_strategies.base import Signal, StrategyConfig

NAME = "C14_OVERLAP_BREAK"
CONFIG = StrategyConfig(
    name=NAME,
    description="12:00-13:30 UTC range break with 5m confirmation",
    cooldown_s=3600,
    session_start_hour=13,
    session_end_hour=16,
)


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    try:
        if w5m is None or len(w5m) < 30:
            return None
        if w1m is None or len(w1m) < 5:
            return None

        minute_of_day = now_utc.hour * 60 + now_utc.minute
        if not (13 * 60 + 30 <= minute_of_day <= 15 * 60 + 30):
            return None

        df5 = w5m.copy()
        df5["time"] = pd.to_datetime(df5["time"])
        today = pd.Timestamp(now_utc).tz_localize(None).normalize()

        rng_start = today + pd.Timedelta(hours=12, minutes=0)
        rng_end = today + pd.Timedelta(hours=13, minutes=30)
        rng_mask = (df5["time"] >= rng_start) & (df5["time"] < rng_end)
        rng_bars = df5[rng_mask]
        if len(rng_bars) < 12:
            return None
        r_high = float(rng_bars["high"].max())
        r_low = float(rng_bars["low"].min())
        r_width = r_high - r_low
        if r_width <= 0:
            return None

        # Sanity floor/ceiling on XAU dollars
        if r_width < 1.5 or r_width > 25.0:
            return None

        # Most recent CLOSED 5m bar after 13:30 UTC
        post = df5[df5["time"] >= rng_end]
        if post.empty:
            return None
        last5 = post.iloc[-1]
        last_close = float(last5["close"])

        entry = float(w1m.iloc[-1]["close"])

        if last_close > r_high:
            sl = round(r_low, 2)
            tp = round(r_high + r_width, 2)
            entry_r = round(entry, 2)
            if not (sl < entry_r < tp):
                return None
            return Signal("BUY", entry_r, sl, tp, "C14_OVERLAP_BREAK_UP")

        if last_close < r_low:
            sl = round(r_high, 2)
            tp = round(r_low - r_width, 2)
            entry_r = round(entry, 2)
            if not (tp < entry_r < sl):
                return None
            return Signal("SELL", entry_r, sl, tp, "C14_OVERLAP_BREAK_DN")

        return None
    except Exception:
        return None
