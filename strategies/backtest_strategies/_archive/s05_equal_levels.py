"""
S05 — Equal Highs/Lows Sweep
-----------------------------
Concept: equal highs (or equal lows) within a small tolerance = retail
double-top/bottom trap. Liquidity stacks above/below these clusters.
After the sweep, enter the reversal.
"""

from __future__ import annotations
import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig
from strategy.liquidity_engine import detect_liquidity_pools, detect_sweep, is_rejection_wick

NAME = "EQ_HL"
CONFIG = StrategyConfig(
    name=NAME,
    description="Equal highs/lows sweep + rejection candle entry",
    cooldown_s=120,
)


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    if len(w1m) < 30:
        return None

    pools = detect_liquidity_pools(w1m, lookback=3, n_pools=4)
    equal_pools = [p for p in pools if p.is_equal]
    if not equal_pools:
        return None

    candle = w1m.iloc[-1]
    sweep = detect_sweep(candle, equal_pools)
    if sweep is None:
        return None

    if not is_rejection_wick(candle, min_wick_ratio=1.5):
        return None

    entry_px = float(candle["close"])
    # Opposite equal pool as TP, else fixed 2pt
    if sweep.direction == "BUY":
        opp = [p.level for p in equal_pools if p.type == "BSL" and p.level > entry_px]
        sl = round(float(candle["low"]) - 0.3, 2)
        tp = round(min(opp), 2) if opp else round(entry_px + 2.0, 2)
    else:
        opp = [p.level for p in equal_pools if p.type == "SSL" and p.level < entry_px]
        sl = round(float(candle["high"]) + 0.3, 2)
        tp = round(max(opp), 2) if opp else round(entry_px - 2.0, 2)

    risk = abs(entry_px - sl)
    reward = abs(tp - entry_px)
    if risk == 0 or reward / risk < 1.0:
        return None

    return Signal(sweep.direction, round(entry_px, 2), sl, tp, "EQ_SWEEP")
