"""
S02 — Algo Candle FVG retest
------------------------------
Concept: an Algo Candle is one that BOTH sweeps liquidity AND leaves an FVG.
This signals algorithmic order placement. Enter on retest of the FVG midpoint.
"""

from __future__ import annotations
import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig
from strategy.liquidity_engine import detect_liquidity_pools, detect_sweep

NAME = "AC"
CONFIG = StrategyConfig(
    name=NAME,
    description="Algo Candle: sweep + FVG, enter on FVG midpoint retest",
    cooldown_s=180,
)


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    if len(w1m) < 30:
        return None

    # Look at the last ~10 candles for an Algo Candle pattern
    pools = detect_liquidity_pools(w1m.iloc[:-10], lookback=3, n_pools=3)
    if not pools:
        return None

    for i in range(-10, -3):
        ac = w1m.iloc[i]
        s = detect_sweep(ac, pools)
        if s is None:
            continue
        # FVG check: AC[i+2].low > AC[i].high  (bullish FVG) — gap left after AC
        c0 = w1m.iloc[i]
        c2 = w1m.iloc[i+2] if (i+2) < 0 else None
        if c2 is None:
            continue
        if s.direction == "BUY":
            fvg_low  = float(c0["high"])
            fvg_high = float(c2["low"])
            if fvg_high - fvg_low < 0.3:
                continue
            mid = (fvg_low + fvg_high) / 2
            current_px = float(w1m.iloc[-1]["close"])
            # wait for price to come back into FVG midpoint zone
            if not (fvg_low <= current_px <= fvg_high):
                continue
            sl = round(float(ac["low"]) - 0.3, 2)
            tp = round(current_px + 2 * (current_px - sl), 2)  # 1:2 RR
            return Signal("BUY", round(current_px, 2), sl, tp, "AC_FVG")
        else:
            fvg_low  = float(c2["high"])
            fvg_high = float(c0["low"])
            if fvg_high - fvg_low < 0.3:
                continue
            current_px = float(w1m.iloc[-1]["close"])
            if not (fvg_low <= current_px <= fvg_high):
                continue
            sl = round(float(ac["high"]) + 0.3, 2)
            tp = round(current_px - 2 * (sl - current_px), 2)
            return Signal("SELL", round(current_px, 2), sl, tp, "AC_FVG")

    return None
