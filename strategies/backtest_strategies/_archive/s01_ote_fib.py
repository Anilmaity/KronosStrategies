"""
S01 — OTE (Optimal Trade Entry) Fibonacci retracement
------------------------------------------------------
Concept: after a liquidity sweep, price retraces 62-79% of the manipulation
swing before continuing in the swept direction. Enter at 70.5% (the
highest-probability OTE level). TP at -27% extension.
"""

from __future__ import annotations
import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig
from strategy.liquidity_engine import detect_liquidity_pools, detect_sweep

NAME = "OTE"
CONFIG = StrategyConfig(
    name=NAME,
    description="Liquidity sweep + 70.5% Fib OTE entry, target -27% extension",
    cooldown_s=180,
)


def get_signal(w1m: pd.DataFrame, w5m: pd.DataFrame, w15m: pd.DataFrame, now_utc) -> Signal | None:
    if len(w1m) < 30:
        return None

    # 1. Find a recent sweep on the 1m chart (look at the last 5 candles)
    pools = detect_liquidity_pools(w1m.iloc[:-5], lookback=3, n_pools=3)
    if not pools:
        return None

    sweep = None
    sweep_idx = -1
    for i in range(-5, -1):
        s = detect_sweep(w1m.iloc[i], pools)
        if s is not None:
            sweep = s
            sweep_idx = i
            break
    if sweep is None:
        return None

    # 2. Define manipulation swing
    sweep_candle = w1m.iloc[sweep_idx]
    current     = w1m.iloc[-1]
    current_px  = float(current["close"])

    # Widen lookback to 10 candles for the manipulation swing origin
    if sweep.direction == "BUY":
        swing_low  = float(sweep_candle["low"])
        swing_high = float(w1m.iloc[max(0, sweep_idx-10):sweep_idx]["high"].max())
        if swing_high <= swing_low + 0.5:
            return None
        rng = swing_high - swing_low
        # Widen OTE band to 55-80% (more accommodating)
        ote_low  = swing_low + rng * 0.55
        ote_high = swing_low + rng * 0.80
        if not (ote_low <= current_px <= ote_high):
            return None
        sl = round(swing_low - 0.3, 2)
        tp = round(swing_high + rng * 0.27, 2)
        side = "BUY"
    else:
        swing_high = float(sweep_candle["high"])
        swing_low  = float(w1m.iloc[max(0, sweep_idx-10):sweep_idx]["low"].min())
        if swing_high <= swing_low + 0.5:
            return None
        rng = swing_high - swing_low
        ote_low  = swing_high - rng * 0.80
        ote_high = swing_high - rng * 0.55
        if not (ote_low <= current_px <= ote_high):
            return None
        sl = round(swing_high + 0.3, 2)
        tp = round(swing_low - rng * 0.27, 2)
        side = "SELL"

    # R:R sanity
    risk = abs(current_px - sl)
    reward = abs(tp - current_px)
    if risk == 0 or reward / risk < 1.5:
        return None

    return Signal(side=side, entry_price=round(current_px, 2),
                  stop_loss=sl, take_profit=tp, reason="OTE")
