"""
C19 — Z-Score Mean Reversion (single-asset XAUUSD adaptation)
-------------------------------------------------------------
Pairs trading is unavailable with a single instrument. Adapt as single-asset
z-score mean reversion on 5m: z = (close - SMA60) / std60. Fade |z| > 2.0
with stop at z = 3.5 and target at z = 1.0 (T1, used here).
"""

from __future__ import annotations
import pandas as pd
import numpy as np

from backtest_strategies.base import Signal, StrategyConfig

NAME = "C19_ZSCORE_MR"
CONFIG = StrategyConfig(
    name=NAME,
    description="Single-asset 5m z-score mean reversion; fade |z|>2.0 toward z=1.0.",
    cooldown_s=1200,
    session_start_hour=7,
    session_end_hour=19,
)

_LOOKBACK = 60
_Z_ENTRY = 2.0
_Z_STOP = 3.5
_Z_TARGET = 1.0


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    try:
        if w5m is None or len(w5m) < _LOOKBACK + 2:
            return None
        closes = w5m["close"].astype(float)
        window = closes.tail(_LOOKBACK)
        mean = float(window.mean())
        std = float(window.std(ddof=0))
        if not np.isfinite(std) or std <= 0:
            return None

        last_close = float(closes.iloc[-1])
        z = (last_close - mean) / std

        # Convert z-thresholds to price levels
        # price = mean + z * std
        # Fade above: SELL when z > +entry; stop at +stop, target at +target
        if z > _Z_ENTRY and z < _Z_STOP:
            entry = round(last_close, 2)
            sl = round(mean + _Z_STOP * std, 2)
            tp = round(mean + _Z_TARGET * std, 2)
            if sl > entry > tp and (sl - entry) >= 1.0 and (entry - tp) >= 1.5:
                return Signal("SELL", entry, sl, tp, f"Z_FADE_HI_{z:.2f}")

        # Fade below: BUY when z < -entry; stop at -stop, target at -target
        if z < -_Z_ENTRY and z > -_Z_STOP:
            entry = round(last_close, 2)
            sl = round(mean - _Z_STOP * std, 2)
            tp = round(mean - _Z_TARGET * std, 2)
            if sl < entry < tp and (entry - sl) >= 1.0 and (tp - entry) >= 1.5:
                return Signal("BUY", entry, sl, tp, f"Z_FADE_LO_{z:.2f}")

        return None
    except Exception:
        return None
