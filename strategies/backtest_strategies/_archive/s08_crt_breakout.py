"""
S08 — CRT (Asia Range) Breakout
--------------------------------
Concept: Asia session (22:00 prev day - 06:00 UTC) builds a range. London
sweeps one side first (manipulation) then breaks the other (real move).
Enter on the post-sweep break with a retest.
"""

from __future__ import annotations
import pandas as pd
from datetime import datetime, timedelta

from backtest_strategies.base import Signal, StrategyConfig

NAME = "CRT_RANGE"
CONFIG = StrategyConfig(
    name=NAME,
    description="Asia range break — London/NY trades the post-sweep direction",
    cooldown_s=600,
    session_start_hour=7,
    session_end_hour=12,
)


def _asia_range(w15m: pd.DataFrame, now_utc: datetime) -> tuple[float, float] | None:
    """Return (asia_high, asia_low) for the most recent Asia session (22-06 UTC).

    Uses NAIVE datetimes for comparison since TSDB candle times are naive UTC.
    """
    today = now_utc.date()
    asia_start = datetime(today.year, today.month, today.day, 0, 0) - timedelta(hours=2)
    asia_end   = datetime(today.year, today.month, today.day, 6, 0)

    mask = (w15m["time"] >= asia_start) & (w15m["time"] < asia_end)
    asia = w15m.loc[mask]
    if len(asia) < 4:
        return None
    return float(asia["high"].max()), float(asia["low"].min())


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    if w15m is None or len(w15m) < 10 or len(w1m) < 5:
        return None

    rng = _asia_range(w15m, now_utc)
    if rng is None:
        return None
    asia_high, asia_low = rng
    range_size = asia_high - asia_low
    if range_size < 1.5 or range_size > 15:
        return None

    current_px = float(w1m.iloc[-1]["close"])
    candle = w1m.iloc[-1]

    # Did either side get swept already in the London window?
    swept_high = float(w1m.tail(60)["high"].max()) > asia_high + 0.2
    swept_low  = float(w1m.tail(60)["low"].min())  < asia_low  - 0.2

    # Simpler rule: range breakout with retest. Enter when 1m candle closes
    # outside Asia range in trend direction; SL just inside range mid-point.
    body_dir = "BUY" if float(candle["close"]) > float(candle["open"]) else "SELL"
    mid = (asia_high + asia_low) / 2

    if current_px > asia_high + 0.2 and body_dir == "BUY":
        sl = round(mid, 2)
        risk = current_px - sl
        if 0.5 < risk < 4:
            tp = round(current_px + 2 * risk, 2)
            return Signal("BUY", round(current_px, 2), sl, tp, "CRT_UP")
    if current_px < asia_low - 0.2 and body_dir == "SELL":
        sl = round(mid, 2)
        risk = sl - current_px
        if 0.5 < risk < 4:
            tp = round(current_px - 2 * risk, 2)
            return Signal("SELL", round(current_px, 2), sl, tp, "CRT_DN")

    return None
