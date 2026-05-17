"""
C08 — VWAP Mean Reversion in Balance Days (XAUUSD adaptation)
-------------------------------------------------------------
Concept: on rotational sessions, excursions to +/-1..2 SD VWAP bands
revert to session VWAP. Only valid when intraday range stays compressed
(real-time balance proxy).
"""

from __future__ import annotations
import pandas as pd
import numpy as np

from backtest_strategies.base import Signal, StrategyConfig

NAME = "C08_VWAP_MR"
CONFIG = StrategyConfig(
    name=NAME,
    description="Fade +/-2 SD VWAP band touches in balance regime; target VWAP.",
    cooldown_s=900,
    session_start_hour=13,
    session_end_hour=19,
)


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    try:
        if w5m is None or len(w5m) < 40:
            return None

        today = now_utc.date()
        dev = w5m[pd.to_datetime(w5m["time"]).dt.date == today].copy()
        if len(dev) < 12:
            return None

        # Session VWAP
        tp = (dev["high"] + dev["low"] + dev["close"]) / 3.0
        vol = dev["volume"].astype(float).clip(lower=0)
        cum_vol = vol.cumsum()
        if cum_vol.iloc[-1] <= 0:
            return None
        cum_pv = (tp * vol).cumsum()
        vwap_series = cum_pv / cum_vol.replace(0, np.nan)
        vwap = float(vwap_series.iloc[-1])

        # Volume-weighted std of (tp - vwap) over session
        diffs = tp - vwap_series
        var_num = (diffs * diffs * vol).cumsum().iloc[-1]
        var = var_num / cum_vol.iloc[-1]
        sd = float(np.sqrt(max(var, 0.0)))
        if sd < 0.3:
            return None

        upper2 = vwap + 1.8 * sd
        lower2 = vwap - 1.8 * sd
        upper1 = vwap + 1.0 * sd
        lower1 = vwap - 1.0 * sd

        # Balance regime proxy: today's range vs ATR(20) on 5m
        recent = w5m.tail(60).copy()
        atr20_5m = float((recent["high"] - recent["low"]).rolling(20).mean().iloc[-1])
        if not np.isfinite(atr20_5m) or atr20_5m <= 0:
            return None
        today_range = float(dev["high"].max() - dev["low"].min())
        # Use ATR20*8 as a coarse "session range" baseline (~ avg session expansion)
        if today_range > 8.0 * atr20_5m:
            return None  # trending, skip

        # ATR(5) on 5m
        atr5 = float((recent["high"] - recent["low"]).rolling(5).mean().iloc[-1])
        if not np.isfinite(atr5) or atr5 <= 0:
            return None

        vol_med20 = float(recent["volume"].rolling(20).median().iloc[-1])
        if vol_med20 <= 0:
            return None

        last = w5m.iloc[-1]
        prev = w5m.iloc[-2]
        last_open = float(last["open"])
        last_close = float(last["close"])
        last_high = float(last["high"])
        last_low = float(last["low"])
        bar_range = max(last_high - last_low, 1e-6)
        bar_vol = float(last["volume"])

        if bar_vol < 1.0 * vol_med20:
            return None

        # SHORT: tagged upper band, closed back below upper1 with upper tail >= 30% range
        tagged_up = float(prev["high"]) >= upper2 or last_high >= upper2
        if tagged_up and last_close < upper1 and last_close < last_open:
            upper_tail = last_high - max(last_close, last_open)
            if upper_tail >= 0.3 * bar_range:
                entry = round(last_close, 2)
                sl = round(last_high + 1.5 * atr5, 2)
                tp_px = round(vwap, 2)
                if sl > entry > tp_px and (sl - entry) >= 0.5 and (entry - tp_px) >= 1.0:
                    return Signal("SELL", entry, sl, tp_px, "VWAP_MR_HI")

        # LONG: tagged lower band, closed back above lower1 with lower tail >= 30% range
        tagged_dn = float(prev["low"]) <= lower2 or last_low <= lower2
        if tagged_dn and last_close > lower1 and last_close > last_open:
            lower_tail = min(last_close, last_open) - last_low
            if lower_tail >= 0.3 * bar_range:
                entry = round(last_close, 2)
                sl = round(last_low - 1.5 * atr5, 2)
                tp_px = round(vwap, 2)
                if sl < entry < tp_px and (entry - sl) >= 0.5 and (tp_px - entry) >= 1.0:
                    return Signal("BUY", entry, sl, tp_px, "VWAP_MR_LO")

        return None
    except Exception:
        return None
