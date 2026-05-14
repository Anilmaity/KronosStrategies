"""
S07 — Inducement + Key Level
-----------------------------
Concept: before price reaches a major swing high/low (key level), it forms
a SMALLER swing nearby (inducement). Retail enters at the inducement; their
stops fund the move from the actual key level. Wait for inducement sweep,
then enter at key level.
"""

from __future__ import annotations
import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig
from strategy.liquidity_engine import detect_liquidity_pools

NAME = "INDUCEMENT"
CONFIG = StrategyConfig(
    name=NAME,
    description="Wait for inducement sweep, enter at HTF key level",
    cooldown_s=240,
)


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    if w5m is None or len(w5m) < 30 or len(w1m) < 5:
        return None

    # Find 5m swing highs/lows as key levels (larger lookback = stronger)
    key_pools = detect_liquidity_pools(w5m.tail(60), lookback=4, n_pools=2)
    if not key_pools:
        return None

    # Find 1m inducement = small swing within 3pt of a key level, opposite side
    indu_pools = detect_liquidity_pools(w1m.tail(40), lookback=2, n_pools=5)
    if not indu_pools:
        return None

    current_px = float(w1m.iloc[-1]["close"])
    candle = w1m.iloc[-1]

    for key in key_pools:
        if key.type == "BSL":
            # Key BSL above. Inducement = small SSL below key by 1-3pt
            indu = [p for p in indu_pools if p.type == "SSL"
                    and 1 < (key.level - p.level) < 3]
            if not indu:
                continue
            indu_level = max(p.level for p in indu)
            # Inducement just got swept (last 5 candles)
            recent_low = float(w1m.tail(5)["low"].min())
            swept = recent_low < indu_level and current_px > indu_level
            if not swept:
                continue
            # Price now approaching key BSL — enter SELL just before key
            if key.level - current_px > 1 and current_px > indu_level:
                sl = round(key.level + 0.3, 2)
                tp = round(current_px - 2 * (sl - current_px), 2)
                return Signal("SELL", round(current_px, 2), sl, tp, "INDU_KEY_BSL")
        else:
            # Key SSL below. Inducement = small BSL above key by 1-3pt
            indu = [p for p in indu_pools if p.type == "BSL"
                    and 1 < (p.level - key.level) < 3]
            if not indu:
                continue
            indu_level = min(p.level for p in indu)
            recent_high = float(w1m.tail(5)["high"].max())
            swept = recent_high > indu_level and current_px < indu_level
            if not swept:
                continue
            if current_px - key.level > 1 and current_px < indu_level:
                sl = round(key.level - 0.3, 2)
                tp = round(current_px + 2 * (current_px - sl), 2)
                return Signal("BUY", round(current_px, 2), sl, tp, "INDU_KEY_SSL")

    return None
