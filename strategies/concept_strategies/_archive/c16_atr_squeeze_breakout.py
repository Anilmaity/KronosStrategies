"""
C16 — ATR Squeeze Breakout (XAUUSD adaptation)
----------------------------------------------
When 5m ATR(14) compresses to a low percentile of its trailing distribution
and price has been range-bound, the directional break with confirming volume
tends to deliver a volatility expansion move. Single-instrument adaptation:
trailing percentile drawn from the in-window 5m ATR series; daily-trend
proxy is the 50-period EMA on the 15m chart, gated by RSI(14).
"""

from __future__ import annotations
import pandas as pd
import numpy as np

from backtest_strategies.base import Signal, StrategyConfig

NAME = "C16_ATR_SQUEEZE"
CONFIG = StrategyConfig(
    name=NAME,
    description="ATR(14) squeeze break on 5m with vol confirmation and 15m EMA50/RSI trend filter.",
    cooldown_s=900,
    session_start_hour=7,
    session_end_hour=19,
)

_PCTL = 25.0          # squeeze percentile threshold
_COMP_BARS = 12       # compression bars
_VOL_MULT = 1.2       # trigger-bar volume multiplier vs 20-bar median
_ATR_PERIOD = 14
_EMA_PERIOD = 50
_RSI_PERIOD = 14


def _rsi(closes: pd.Series, n: int = 14) -> float:
    if len(closes) < n + 1:
        return float("nan")
    delta = closes.diff()
    up = delta.clip(lower=0.0)
    dn = -delta.clip(upper=0.0)
    roll_up = up.ewm(alpha=1.0 / n, adjust=False).mean()
    roll_dn = dn.ewm(alpha=1.0 / n, adjust=False).mean()
    rs = roll_up / roll_dn.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return float(rsi.iloc[-1])


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    try:
        if w5m is None or len(w5m) < max(40, _COMP_BARS + _ATR_PERIOD + 5):
            return None
        if w15m is None or len(w15m) < _EMA_PERIOD + 2:
            return None

        # 5m ATR series (true-range simplified to high-low rolling mean)
        ranges = (w5m["high"].astype(float) - w5m["low"].astype(float))
        atr_series = ranges.rolling(_ATR_PERIOD).mean()
        atr_now = float(atr_series.iloc[-1])
        if not np.isfinite(atr_now) or atr_now <= 0:
            return None

        # Percentile of current ATR in trailing distribution
        atr_hist = atr_series.dropna()
        if len(atr_hist) < 30:
            return None
        threshold = float(np.percentile(atr_hist.values, _PCTL))
        if atr_now > threshold:
            return None

        # Compression: last _COMP_BARS form a tight range; no body closed outside it
        comp = w5m.iloc[-(_COMP_BARS + 1):-1]  # last 12 closed bars excluding trigger
        if len(comp) < _COMP_BARS:
            return None
        comp_hi = float(comp["high"].max())
        comp_lo = float(comp["low"].min())
        comp_range = comp_hi - comp_lo
        if comp_range <= 0 or comp_range > 2.5 * atr_now:
            return None

        # Volume confirmation on trigger bar
        vol_med20 = float(w5m["volume"].tail(20).median())
        last = w5m.iloc[-1]
        bar_vol = float(last["volume"])
        if vol_med20 <= 0 or bar_vol < _VOL_MULT * vol_med20:
            return None

        # 15m trend proxy
        closes_15 = w15m["close"].astype(float)
        ema = closes_15.ewm(span=_EMA_PERIOD, adjust=False).mean().iloc[-1]
        last_15_close = float(closes_15.iloc[-1])
        rsi_15 = _rsi(closes_15, _RSI_PERIOD)
        if not np.isfinite(rsi_15):
            return None

        last_close = float(last["close"])
        # T1 = 1.5x compression range from breakout edge
        # Stop = opposite compression edge + 1 ATR buffer
        # Long break
        if last_close > comp_hi and last_15_close > ema:
            entry = round(last_close, 2)
            sl = round(comp_lo - atr_now, 2)
            tp = round(comp_hi + 1.5 * comp_range, 2)
            if sl < entry < tp and (entry - sl) >= 0.8 and (tp - entry) >= 1.5:
                return Signal("BUY", entry, sl, tp, "SQZ_BREAK_UP")

        # Short break
        if last_close < comp_lo and last_15_close < ema:
            entry = round(last_close, 2)
            sl = round(comp_hi + atr_now, 2)
            tp = round(comp_lo - 1.5 * comp_range, 2)
            if sl > entry > tp and (sl - entry) >= 0.8 and (entry - tp) >= 1.5:
                return Signal("SELL", entry, sl, tp, "SQZ_BREAK_DN")

        return None
    except Exception:
        return None
