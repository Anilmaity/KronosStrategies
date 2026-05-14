"""
S04 — Breaker Block Retest
---------------------------
Concept: when a bullish OB is broken DOWN through with momentum, the zone
flips polarity to a bearish Breaker. Retest from below = sell setup. Vice
versa for failed bearish OBs.
"""

from __future__ import annotations
import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig
from strategy.ict_engine import detect_order_blocks

NAME = "BB"
CONFIG = StrategyConfig(
    name=NAME,
    description="Breaker Block — failed OB that flipped, traded on retest",
    cooldown_s=300,
)


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    if w5m is None or len(w5m) < 30 or len(w1m) < 5:
        return None

    obs = detect_order_blocks(w5m.tail(80), displacement_mult=1.8)
    if not obs:
        return None

    current_px = float(w1m.iloc[-1]["close"])
    # Use 5m history after OB formation to confirm break
    times = w5m["time"].values

    for ob in reversed(obs[-10:]):
        # Subsequent 5m candles since the OB
        post = w5m[w5m["time"] > ob.time]
        if len(post) < 3:
            continue

        if ob.type == "bullish":
            # Failed bullish OB → price broke DOWN, must dip BELOW the OB,
            # then return UP for a retest.
            broke_down = (post["low"] < ob.zone_low - 0.3).any()
            if not broke_down:
                continue
            # Latest 5m candle must be returning up INTO the zone (current price near upper half)
            in_upper_half = (ob.zone_low + (ob.zone_high - ob.zone_low) * 0.5) <= current_px <= ob.zone_high
            if not in_upper_half:
                continue
            sl = round(ob.zone_high + 0.3, 2)
            tp = round(current_px - 2 * (sl - current_px), 2)
            return Signal("SELL", round(current_px, 2), sl, tp, "BB_BEAR")
        else:
            broke_up = (post["high"] > ob.zone_high + 0.3).any()
            if not broke_up:
                continue
            in_lower_half = ob.zone_low <= current_px <= (ob.zone_low + (ob.zone_high - ob.zone_low) * 0.5)
            if not in_lower_half:
                continue
            sl = round(ob.zone_low - 0.3, 2)
            tp = round(current_px + 2 * (current_px - sl), 2)
            return Signal("BUY", round(current_px, 2), sl, tp, "BB_BULL")

    return None
