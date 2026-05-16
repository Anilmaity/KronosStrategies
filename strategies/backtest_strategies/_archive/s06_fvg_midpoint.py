"""
S06 — FVG Midpoint Reversal
----------------------------
Concept: an FVG is a liquidity void. Once price retraces to fill it,
50% fill (the midpoint) often holds as a reaction zone. Trade the reversal
from the FVG midpoint back toward the original move direction.
"""

from __future__ import annotations
import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig
from strategy.ict_engine import detect_fvgs

NAME = "FVG_MID"
CONFIG = StrategyConfig(
    name=NAME,
    description="5m FVG midpoint reversal entry",
    cooldown_s=180,
)


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    if w5m is None or len(w5m) < 20 or len(w1m) < 5:
        return None

    fvgs = detect_fvgs(w5m.tail(40), min_gap=0.5)
    if not fvgs:
        return None

    current_px = float(w1m.iloc[-1]["close"])

    for fvg in reversed(fvgs[-5:]):
        mid = (fvg.zone_low + fvg.zone_high) / 2
        gap = fvg.zone_high - fvg.zone_low
        if not (fvg.zone_low <= current_px <= fvg.zone_high):
            continue
        # only trade FVGs with reasonable size
        if gap < 0.5 or gap > 4.0:
            continue
        # within 30% of midpoint
        if abs(current_px - mid) > gap * 0.3:
            continue

        if fvg.type == "bullish":
            sl = round(fvg.zone_low - 0.3, 2)
            tp = round(current_px + 2 * (current_px - sl), 2)
            return Signal("BUY", round(current_px, 2), sl, tp, "FVG_MID_BULL")
        else:
            sl = round(fvg.zone_high + 0.3, 2)
            tp = round(current_px - 2 * (sl - current_px), 2)
            return Signal("SELL", round(current_px, 2), sl, tp, "FVG_MID_BEAR")

    return None
