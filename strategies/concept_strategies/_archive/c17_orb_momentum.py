"""
C17 — Opening Range Breakout Momentum (XAUUSD adaptation)
---------------------------------------------------------
Define the OR as the first 30 minutes of NY session (13:30-14:00 UTC).
Trigger between 14:00-17:00 UTC: first 5m close that fully clears OR high/low
with bar volume > 1.5x 20-bar average. Stop at OR midpoint, T1 = 1x OR range.
"""

from __future__ import annotations
import pandas as pd
import numpy as np
from datetime import time as dtime

from backtest_strategies.base import Signal, StrategyConfig

NAME = "C17_ORB_MOMENTUM"
CONFIG = StrategyConfig(
    name=NAME,
    description="NY 13:30-14:00 UTC opening-range break with volume confirmation; T1 = 1x OR range.",
    cooldown_s=1800,
    session_start_hour=14,
    session_end_hour=17,
)

_OR_START = dtime(13, 30)
_OR_END = dtime(14, 0)
_VOL_MULT = 1.2


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    try:
        if w5m is None or len(w5m) < 30:
            return None
        # Only fire within entry window
        if not (_OR_END <= now_utc.time() < dtime(17, 0)):
            return None

        today = now_utc.date()
        times = pd.to_datetime(w5m["time"])
        today_mask = times.dt.date == today
        today_bars = w5m[today_mask]
        if len(today_bars) < 4:
            return None

        # Opening range: bars whose start time is within [13:30, 14:00)
        t = pd.to_datetime(today_bars["time"]).dt
        or_mask = (t.time >= _OR_START) & (t.time < _OR_END)
        or_bars = today_bars[or_mask.values]
        if len(or_bars) < 4:  # need ~6 1m bars or roughly 5-6 5m bars; relax
            return None

        or_high = float(or_bars["high"].max())
        or_low = float(or_bars["low"].min())
        or_range = or_high - or_low
        if or_range <= 0:
            return None

        # OR width veto: > 1.5x recent 5m range avg (proxy for 20d avg OR)
        recent_range_avg = float((w5m["high"] - w5m["low"]).tail(40).mean())
        if recent_range_avg > 0 and or_range > 1.5 * (recent_range_avg * 6.0):
            return None

        # Volume confirmation
        vol_avg20 = float(w5m["volume"].tail(20).mean())
        last = w5m.iloc[-1]
        bar_vol = float(last["volume"])
        if vol_avg20 <= 0 or bar_vol < _VOL_MULT * vol_avg20:
            return None

        # Trigger only if last bar is AFTER OR window
        last_t = pd.to_datetime(last["time"]).time()
        if last_t < _OR_END:
            return None

        last_open = float(last["open"])
        last_close = float(last["close"])
        or_mid = (or_high + or_low) / 2.0

        # Must be the first 5m close that fully clears OR
        post_or = today_bars[pd.to_datetime(today_bars["time"]).dt.time >= _OR_END]
        # Exclude the current/last bar from "earlier" check
        earlier = post_or.iloc[:-1]
        already_broken_up = bool((earlier["close"] > or_high).any())
        already_broken_dn = bool((earlier["close"] < or_low).any())

        # Long break
        if last_close > or_high and last_open < last_close and not already_broken_up:
            entry = round(last_close, 2)
            sl = round(or_mid, 2)
            tp = round(or_high + or_range, 2)
            if sl < entry < tp and (entry - sl) >= 0.5 and (tp - entry) >= 1.0:
                return Signal("BUY", entry, sl, tp, "ORB_LONG")

        # Short break
        if last_close < or_low and last_open > last_close and not already_broken_dn:
            entry = round(last_close, 2)
            sl = round(or_mid, 2)
            tp = round(or_low - or_range, 2)
            if sl > entry > tp and (sl - entry) >= 0.5 and (entry - tp) >= 1.0:
                return Signal("SELL", entry, sl, tp, "ORB_SHORT")

        return None
    except Exception:
        return None
