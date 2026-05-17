"""
C13 - Volatility Expansion Breakout (NFP-style, no calendar) - XAUUSD
---------------------------------------------------------------------
Adaptation of NFP Volatility Expansion Breakout without a calendar.

Setup:
  - Pre-13:30-UTC compression: 30-min range immediately before 13:30 UTC
    is < 0.4x the typical 30-min range (proxy from rolling 5m bars).
  - Define a "reference range" as the 5m bar that contains/follows 13:30
    UTC (i.e. the 13:30-13:35 UTC bar). Use its high/low as the breakout
    box.
  - Trigger window 13:35-14:00 UTC: a subsequent 5m close above the
    reference high (long) or below the reference low (short).
  - SL = opposite extreme of reference candle.
  - TP = 1x reference range from breakout direction.
"""

from __future__ import annotations
import pandas as pd
import numpy as np

from backtest_strategies.base import Signal, StrategyConfig

NAME = "C13_VOL_BREAKOUT"
CONFIG = StrategyConfig(
    name=NAME,
    description="Pre-NY compression + 13:30 reference-candle breakout",
    cooldown_s=3600,
    session_start_hour=13,
    session_end_hour=15,
)


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    try:
        if w5m is None or len(w5m) < 30:
            return None
        if w1m is None or len(w1m) < 30:
            return None

        # Only trigger 13:35 - 14:00 UTC
        minute_of_day = now_utc.hour * 60 + now_utc.minute
        if not (13 * 60 + 35 <= minute_of_day <= 14 * 60 + 30):
            return None

        # Reference candle = 13:30-13:35 UTC bar; find it in w5m
        df5 = w5m.copy()
        df5["time"] = pd.to_datetime(df5["time"])
        today = pd.Timestamp(now_utc).tz_localize(None).normalize()
        ref_start = today + pd.Timedelta(hours=13, minutes=30)
        ref_end = today + pd.Timedelta(hours=13, minutes=35)

        ref_mask = (df5["time"] >= ref_start) & (df5["time"] < ref_end)
        ref = df5[ref_mask]
        if ref.empty:
            return None
        ref_bar = ref.iloc[-1]
        ref_high = float(ref_bar["high"])
        ref_low = float(ref_bar["low"])
        ref_range = ref_high - ref_low
        if ref_range <= 0 or ref_range < 0.4:
            return None

        # Pre-13:30 compression: 30 minutes before -> 13:00-13:30 UTC
        pre_start = today + pd.Timedelta(hours=13, minutes=0)
        pre_mask = (df5["time"] >= pre_start) & (df5["time"] < ref_start)
        pre = df5[pre_mask]
        if len(pre) < 4:
            return None
        pre_range = float(pre["high"].max() - pre["low"].min())
        if pre_range <= 0:
            return None

        # Typical 30-min range = median rolling 6-bar (5m * 6 = 30min)
        roll_h = df5["high"].rolling(6).max()
        roll_l = df5["low"].rolling(6).min()
        typical = (roll_h - roll_l).dropna()
        if len(typical) < 20:
            return None
        typ = float(typical.median())
        if typ <= 0:
            return None
        if pre_range >= 0.6 * typ:
            return None  # not compressed enough

        # Breakout: the most recent CLOSED 5m bar must close beyond ref range
        # (and not be the ref bar itself)
        last5 = df5.iloc[-1]
        if pd.Timestamp(last5["time"]) <= pd.Timestamp(ref_bar["time"]):
            return None
        last_close = float(last5["close"])

        if last_close > ref_high:
            # Long breakout
            entry = float(w1m.iloc[-1]["close"])
            sl = round(ref_low, 2)
            tp = round(ref_high + ref_range, 2)
            entry_r = round(entry, 2)
            if not (sl < entry_r < tp):
                return None
            return Signal("BUY", entry_r, sl, tp, "C13_VOL_BREAK_UP")

        if last_close < ref_low:
            entry = float(w1m.iloc[-1]["close"])
            sl = round(ref_high, 2)
            tp = round(ref_low - ref_range, 2)
            entry_r = round(entry, 2)
            if not (tp < entry_r < sl):
                return None
            return Signal("SELL", entry_r, sl, tp, "C13_VOL_BREAK_DN")

        return None
    except Exception:
        return None
