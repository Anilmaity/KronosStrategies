"""
C20 — XAU short-term vs long-term momentum divergence (DXY substitute)
----------------------------------------------------------------------
No DXY feed available. Substitute the gold/DXY divergence with an internal
multi-horizon divergence: when 5m XAU has been trending one way for 6 bars
but 15m has reversed, fade the 5m extension back toward the 15m direction
("lagging instrument catches up" mechanic preserved on a single asset).
"""

from __future__ import annotations
import pandas as pd
import numpy as np

from backtest_strategies.base import Signal, StrategyConfig

NAME = "C20_XAU_MTF_DIVERGENCE"
CONFIG = StrategyConfig(
    name=NAME,
    description="Fade 6-bar 5m extension when 15m has reversed (MTF momentum divergence).",
    cooldown_s=1500,
    session_start_hour=7,
    session_end_hour=17,
)

_LOOKBACK_5M = 6
_LOOKBACK_15M = 4
_MIN_SAME_DIR_5M = 5      # of 6 bars
_ATR_PERIOD = 14
_STOP_ATR_MULT = 1.5


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    try:
        if w5m is None or len(w5m) < 30 or w15m is None or len(w15m) < (_LOOKBACK_15M + 5):
            return None

        c5 = w5m["close"].astype(float).reset_index(drop=True)
        c15 = w15m["close"].astype(float).reset_index(drop=True)

        # 5m direction over last _LOOKBACK_5M bars
        last5 = c5.tail(_LOOKBACK_5M + 1).values
        if len(last5) < _LOOKBACK_5M + 1:
            return None
        diffs5 = np.diff(last5)
        ups5 = int((diffs5 > 0).sum())
        dns5 = int((diffs5 < 0).sum())
        move5 = float(last5[-1] - last5[0])

        # 15m direction over last _LOOKBACK_15M bars
        last15 = c15.tail(_LOOKBACK_15M + 1).values
        if len(last15) < _LOOKBACK_15M + 1:
            return None
        move15 = float(last15[-1] - last15[0])

        # ATR(14) 5m for stop
        atr5 = float((w5m["high"] - w5m["low"]).tail(_ATR_PERIOD).mean())
        if not np.isfinite(atr5) or atr5 <= 0:
            return None

        last_close = float(c5.iloc[-1])

        # 5m extended UP but 15m DOWN -> fade SHORT
        if ups5 >= _MIN_SAME_DIR_5M and move5 > 0 and move15 < -0.2 * atr5:
            entry = round(last_close, 2)
            sl = round(entry + _STOP_ATR_MULT * atr5, 2)
            tp = round(entry - 1.5 * atr5, 2)
            if sl > entry > tp and (sl - entry) >= 1.0 and (entry - tp) >= 1.5:
                return Signal("SELL", entry, sl, tp, "MTF_DIV_FADE_UP")

        # 5m extended DOWN but 15m UP -> fade LONG
        if dns5 >= _MIN_SAME_DIR_5M and move5 < 0 and move15 > 0.2 * atr5:
            entry = round(last_close, 2)
            sl = round(entry - _STOP_ATR_MULT * atr5, 2)
            tp = round(entry + 1.5 * atr5, 2)
            if sl < entry < tp and (entry - sl) >= 1.0 and (tp - entry) >= 1.5:
                return Signal("BUY", entry, sl, tp, "MTF_DIV_FADE_DN")

        return None
    except Exception:
        return None
