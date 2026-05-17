"""
C11 - Pre-Event Drift (Pre-NY Compression Long Bias) - XAUUSD
-------------------------------------------------------------
Adaptation of Pre-FOMC Drift without an economic calendar.

Detects the PRICE-ACTION SIGNATURE of pre-event positioning: in the 60
minutes before 13:30 UTC (the canonical US data-release / NY-open vol
expansion hour) liquidity often withdraws and range compresses. We take
a long bias on that compression, targeting the typical NY-open expansion.

Rule:
  - Trigger window: 12:30-13:30 UTC (the last hour before the 13:30 event).
  - Compute last 60min range (high-low of last 60 1m bars).
  - Compare against the 20-day median 60min range proxy: mean of the
    rolling 60min high-low across w15m (using last ~100 bars * 15m = 25h
    is short; use a tail-window of the 5m series as a proxy of recent
    typical 60min range).
  - If compressed (range < 0.4 * proxy), go long.
  - SL = entry - 0.5% (XAU dollar terms ~ $10-17 on a $2000-3500 price).
  - TP = entry + 1.0% (1:2 RR aiming at NY-open expansion).
"""

from __future__ import annotations
import pandas as pd
import numpy as np

from backtest_strategies.base import Signal, StrategyConfig

NAME = "C11_PRE_DRIFT"
CONFIG = StrategyConfig(
    name=NAME,
    description="Pre-NY (12:30-13:30 UTC) compression long bias on XAUUSD",
    cooldown_s=3600,
    session_start_hour=12,
    session_end_hour=14,
)

_WINDOW_START_H = 12   # 12:30 UTC
_WINDOW_START_M = 30
_WINDOW_END_H = 13     # 13:30 UTC
_WINDOW_END_M = 30


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    try:
        if w1m is None or len(w1m) < 70:
            return None
        if w5m is None or len(w5m) < 40:
            return None

        # Confine to the 12:30-13:30 UTC trigger window
        minute_of_day = now_utc.hour * 60 + now_utc.minute
        start_mod = _WINDOW_START_H * 60 + _WINDOW_START_M
        end_mod = _WINDOW_END_H * 60 + _WINDOW_END_M
        if not (start_mod <= minute_of_day < end_mod):
            return None

        # Last 60 1m bars => last 60 minutes
        last_60 = w1m.tail(60)
        if len(last_60) < 50:
            return None
        cur_range = float(last_60["high"].max() - last_60["low"].min())
        if cur_range <= 0:
            return None

        # Build a "typical 60min range" proxy from w5m: rolling 12-bar
        # (12 * 5m = 60min) range, take median over the available history.
        df5 = w5m.copy()
        roll_high = df5["high"].rolling(12).max()
        roll_low = df5["low"].rolling(12).min()
        roll_range = (roll_high - roll_low).dropna()
        if len(roll_range) < 20:
            return None
        typical = float(roll_range.median())
        if typical <= 0:
            return None

        # Compression check
        if cur_range >= 0.6 * typical:
            return None

        # Minimum meaningful compression range floor on XAU dollars
        if cur_range < 0.5:
            return None

        last = w1m.iloc[-1]
        entry = float(last["close"])
        if entry <= 0:
            return None

        # 0.5% SL, 1.0% TP (long-bias)
        sl = round(entry * (1.0 - 0.005), 2)
        tp = round(entry * (1.0 + 0.010), 2)
        entry = round(entry, 2)

        if not (sl < entry < tp):
            return None

        return Signal("BUY", entry, sl, tp, "C11_PRE_NY_COMPRESSION")
    except Exception:
        return None
