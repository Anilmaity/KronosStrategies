"""
C10 — Initial Balance (IB) Breakout (XAUUSD adaptation)
-------------------------------------------------------
Concept: a clean break of IB High / IB Low on confirming volume, before
the late-session window, signals initiative and targets IB-range
extensions.

IB = first 60 minutes of NY RTH (13:30-14:30 UTC) = 12 x 5m bars.
"""

from __future__ import annotations
import pandas as pd
import numpy as np
from datetime import time

from backtest_strategies.base import Signal, StrategyConfig

NAME = "C10_IB_BREAKOUT"
CONFIG = StrategyConfig(
    name=NAME,
    description="Break of first-60min IB high/low on >=1.5x median volume; target 1x IB range projection.",
    cooldown_s=900,
    session_start_hour=14,
    session_end_hour=18,
)

# NY RTH start 13:30 UTC; cutoff at 17:00 UTC (rough "13:00 local" proxy keeping room before close)
_IB_START = time(13, 30)
_IB_END = time(14, 30)
_CUTOFF = time(17, 0)


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    try:
        if w5m is None or len(w5m) < 40:
            return None

        # Session-window cutoff
        now_t = now_utc.time()
        if now_t < _IB_END or now_t > _CUTOFF:
            return None

        today = now_utc.date()
        dev = w5m[pd.to_datetime(w5m["time"]).dt.date == today].copy()
        if len(dev) < 13:
            return None

        dev["t"] = pd.to_datetime(dev["time"]).dt.time
        ib_bars = dev[(dev["t"] >= _IB_START) & (dev["t"] < _IB_END)]
        if len(ib_bars) < 8:
            return None
        ib_high = float(ib_bars["high"].max())
        ib_low = float(ib_bars["low"].min())
        ib_range = ib_high - ib_low
        if ib_range < 1.0:
            return None

        # Post-IB bars
        post = dev[dev["t"] >= _IB_END]
        if len(post) < 1:
            return None

        recent = w5m.tail(60).copy()
        atr5 = float((recent["high"] - recent["low"]).rolling(5).mean().iloc[-1])
        if not np.isfinite(atr5) or atr5 <= 0:
            return None

        # 20-bar median volume from IB period itself (parameter spec)
        ib_vol_med = float(ib_bars["volume"].rolling(min(20, len(ib_bars))).median().iloc[-1])
        if not np.isfinite(ib_vol_med) or ib_vol_med <= 0:
            ib_vol_med = float(ib_bars["volume"].median())
        if ib_vol_med <= 0:
            return None

        last = w5m.iloc[-1]
        last_close = float(last["close"])
        last_high = float(last["high"])
        last_low = float(last["low"])
        bar_vol = float(last["volume"])

        # Must be a NEW break: last bar's close is the first close beyond IB among post-IB bars,
        # OR confirm no 2-bar snapback (none of last 2 closes back inside).
        post_closes = post["close"].astype(float).values
        if len(post_closes) < 1:
            return None

        # Bull break
        if last_close > ib_high:
            # No 2-bar snapback: at least the last bar is outside; require last <=2 post-bars don't close back inside after break
            # Determine if break is fresh-ish — count how many post bars closed above ib_high
            above_count = int((post_closes > ib_high).sum())
            if above_count < 1:
                return None
            # Volume filter
            if bar_vol < 1.2 * ib_vol_med:
                return None
            if len(post_closes) >= 2:
                inside_after_above = False
                was_above = False
                for c in post_closes[:-1]:
                    if c > ib_high:
                        was_above = True
                    elif was_above and c < ib_high:
                        inside_after_above = True
                        break
                if inside_after_above:
                    return None
            entry = round(last_close, 2)
            sl = round(ib_high - 1.0 * atr5, 2)
            tp = round(ib_high + 1.0 * ib_range, 2)
            risk = entry - sl
            if risk < 0.5 or risk > 6.0:
                return None
            if sl < entry < tp:
                return Signal("BUY", entry, sl, tp, "IB_BREAK_UP")

        # Bear break
        if last_close < ib_low:
            below_count = int((post_closes < ib_low).sum())
            if below_count < 1:
                return None
            if bar_vol < 1.2 * ib_vol_med:
                return None
            if len(post_closes) >= 2:
                inside_after_below = False
                was_below = False
                for c in post_closes[:-1]:
                    if c < ib_low:
                        was_below = True
                    elif was_below and c > ib_low:
                        inside_after_below = True
                        break
                if inside_after_below:
                    return None
            entry = round(last_close, 2)
            sl = round(ib_low + 1.0 * atr5, 2)
            tp = round(ib_low - 1.0 * ib_range, 2)
            risk = sl - entry
            if risk < 0.5 or risk > 6.0:
                return None
            if sl > entry > tp:
                return Signal("SELL", entry, sl, tp, "IB_BREAK_DN")

        return None
    except Exception:
        return None
