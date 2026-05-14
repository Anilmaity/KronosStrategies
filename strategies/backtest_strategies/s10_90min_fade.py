"""
S10 — 90-Minute Cycle Fade
---------------------------
Concept: Every 90 minutes from 00:00 UTC, the algorithm has a potential
inflection point. Price often makes a manipulation sweep at the START of
a new 90-min window, then reverses toward equilibrium. Fade the opening
sweep within the first 15 mins of each cycle.
"""

from __future__ import annotations
import pandas as pd
from datetime import datetime

from backtest_strategies.base import Signal, StrategyConfig

NAME = "M90_FADE"
CONFIG = StrategyConfig(
    name=NAME,
    description="90-min cycle fade — reverse toward window open",
    cooldown_s=300,
    session_start_hour=7,
    session_end_hour=16,
)

# Anchor minutes (UTC): 0, 90, 180, 270, ... = 0:00, 1:30, 3:00, 4:30, 6:00, 7:30, 9:00, 10:30, 12:00, 13:30, 15:00
_ANCHORS_MIN = set(range(0, 24 * 60, 90))


def _current_anchor_dt(now_utc: datetime) -> datetime:
    """Return a NAIVE UTC anchor datetime — must match TSDB candle.time format."""
    minute_of_day = now_utc.hour * 60 + now_utc.minute
    anchor_minute = max(a for a in _ANCHORS_MIN if a <= minute_of_day)
    h, m = divmod(anchor_minute, 60)
    today = now_utc.date()
    return datetime(today.year, today.month, today.day, h, m, 0)


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    if len(w1m) < 5:
        return None

    minute_of_day = now_utc.hour * 60 + now_utc.minute
    mins_into_cycle = minute_of_day - max(a for a in _ANCHORS_MIN if a <= minute_of_day)

    # Trade within first 30 mins of a 90-min cycle (was 15 — too narrow)
    if mins_into_cycle >= 30:
        return None

    anchor_dt = _current_anchor_dt(now_utc)
    cycle_candles = w1m[w1m["time"] >= anchor_dt]
    if len(cycle_candles) < 2:
        return None

    open_px   = float(cycle_candles.iloc[0]["open"])
    extreme_high = float(cycle_candles["high"].max())
    extreme_low  = float(cycle_candles["low"].min())
    current_px   = float(w1m.iloc[-1]["close"])

    # If price has run > 2pt past open AND last candle is rejecting back
    candle = w1m.iloc[-1]
    body = abs(float(candle["close"]) - float(candle["open"]))

    # Lowered run threshold from 2.0 → 1.2pt
    if extreme_high - open_px > 1.2 and current_px < extreme_high - 0.2:
        sl = round(extreme_high + 0.3, 2)
        tp = round(open_px, 2)
        if (current_px - tp) > 0.3 and (sl - current_px) > 0.3:
            return Signal("SELL", round(current_px, 2), sl, tp, "M90_FADE_UP")

    if open_px - extreme_low > 1.2 and current_px > extreme_low + 0.2:
        sl = round(extreme_low - 0.3, 2)
        tp = round(open_px, 2)
        if (tp - current_px) > 0.3 and (current_px - sl) > 0.3:
            return Signal("BUY", round(current_px, 2), sl, tp, "M90_FADE_DN")

    return None
