"""
S03 — Order Block Mitigation (5m OB, 1m entry)
-----------------------------------------------
Concept: 5m OB = last opposing candle before a displacement move. When
price returns to mitigate (touch) the OB, it tends to reverse from there.
"""

from __future__ import annotations
import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig
from strategy.ict_engine import detect_order_blocks

NAME = "OB_MIT"
CONFIG = StrategyConfig(
    name=NAME,
    description="5m Order Block mitigation — enter on 1m price returning to OB midpoint",
    cooldown_s=300,
)


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    if w5m is None or len(w5m) < 20 or len(w1m) < 5:
        return None

    # Detect 5m OBs in recent history
    obs = detect_order_blocks(w5m.tail(60), displacement_mult=1.8)
    if not obs:
        return None

    current_px = float(w1m.iloc[-1]["close"])

    # Find a recent OB that price is currently mitigating
    for ob in reversed(obs[-10:]):
        if ob.zone_low <= current_px <= ob.zone_high:
            mid = (ob.zone_low + ob.zone_high) / 2
            risk_unit = abs(ob.zone_high - ob.zone_low)
            if risk_unit < 0.5 or risk_unit > 5:
                continue

            if ob.type == "bullish":
                sl = round(ob.zone_low - 0.3, 2)
                tp = round(current_px + 2 * (current_px - sl), 2)
                return Signal("BUY", round(current_px, 2), sl, tp, "OB_BULL")
            else:
                sl = round(ob.zone_high + 0.3, 2)
                tp = round(current_px - 2 * (sl - current_px), 2)
                return Signal("SELL", round(current_px, 2), sl, tp, "OB_BEAR")

    return None
