"""
C12 - Post-Event Fade (Vol-Shock Mean Reversion) - XAUUSD
---------------------------------------------------------
Adaptation of Post-CPI Fade without a calendar.

Detects the PRICE-ACTION SIGNATURE of an event: a vol-shock candle.
Around 12:30-14:30 UTC (US data release / NY-open hours), if a single
5m candle's range exceeds 2.5x the 20-bar ATR, treat that as the shock
candle. Wait for a 1m close in the opposite direction (the fade
confirmation), then enter targeting a 50% retracement of the shock
candle body.

SL is placed beyond the shock candle extreme.
"""

from __future__ import annotations
import pandas as pd
import numpy as np

from backtest_strategies.base import Signal, StrategyConfig

NAME = "C12_POST_FADE"
CONFIG = StrategyConfig(
    name=NAME,
    description="Fade a 2.5x-ATR vol-shock 5m candle during NY hours",
    cooldown_s=1800,
    session_start_hour=12,
    session_end_hour=15,
)

_WIN_START_H = 12   # 12:30 UTC
_WIN_START_M = 30
_WIN_END_H = 14     # 14:30 UTC
_WIN_END_M = 30


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    try:
        if w5m is None or len(w5m) < 25:
            return None
        if w1m is None or len(w1m) < 5:
            return None

        minute_of_day = now_utc.hour * 60 + now_utc.minute
        if not (_WIN_START_H * 60 + _WIN_START_M <= minute_of_day
                < _WIN_END_H * 60 + _WIN_END_M):
            return None

        # 20-bar ATR proxy on 5m
        atr = float((w5m["high"] - w5m["low"]).tail(20).mean())
        if atr <= 0:
            return None

        # Look back across the last 3 closed 5m bars for a vol-shock
        # (most recent is w5m.iloc[-1]; allow it to be the trigger as well).
        shock_idx = None
        shock_bar = None
        for k in (-1, -2, -3):
            if abs(k) > len(w5m):
                continue
            bar = w5m.iloc[k]
            rng = float(bar["high"] - bar["low"])
            if rng >= 2.5 * atr and rng > 1.5:
                shock_idx = k
                shock_bar = bar
                break

        if shock_bar is None:
            return None

        shock_open = float(shock_bar["open"])
        shock_close = float(shock_bar["close"])
        shock_high = float(shock_bar["high"])
        shock_low = float(shock_bar["low"])
        shock_body = abs(shock_close - shock_open)
        if shock_body < 0.5:
            return None

        # Bullish shock => fade is SELL; bearish shock => fade is BUY
        bullish_shock = shock_close > shock_open

        # Confirmation: most recent 1m close in opposite direction
        last_1m = w1m.iloc[-1]
        prev_1m = w1m.iloc[-2] if len(w1m) >= 2 else last_1m
        bullish_1m = float(last_1m["close"]) > float(last_1m["open"])

        if bullish_shock and bullish_1m:
            return None
        if (not bullish_shock) and (not bullish_1m):
            return None

        entry = float(last_1m["close"])

        # Target: 50% retrace of shock candle body
        if bullish_shock:
            # SELL
            mid_body = shock_open + 0.5 * (shock_close - shock_open)
            tp = round(mid_body, 2)
            sl = round(shock_high + 0.10 * shock_body, 2)
            entry_r = round(entry, 2)
            if not (tp < entry_r < sl):
                return None
            # Require minimum RR of ~1.0
            if (entry_r - tp) < 0.4 * (sl - entry_r):
                return None
            return Signal("SELL", entry_r, sl, tp, "C12_POST_FADE_SHORT")
        else:
            # BUY
            mid_body = shock_open + 0.5 * (shock_close - shock_open)
            tp = round(mid_body, 2)
            sl = round(shock_low - 0.10 * shock_body, 2)
            entry_r = round(entry, 2)
            if not (sl < entry_r < tp):
                return None
            if (tp - entry_r) < 0.4 * (entry_r - sl):
                return None
            return Signal("BUY", entry_r, sl, tp, "C12_POST_FADE_LONG")
    except Exception:
        return None
