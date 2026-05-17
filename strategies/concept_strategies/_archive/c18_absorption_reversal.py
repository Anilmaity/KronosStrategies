"""
C18 — Absorption Reversal proxy (XAUUSD adaptation)
---------------------------------------------------
No footprint data is available. We proxy absorption with a price-action
signature at a key level (prior-day high/low or session VWAP): a 5m bar
prints high volume (> 1.5x median), small body (body/range < 0.3), and
a wick rejection in the trade direction. Entry on the next bar open.
"""

from __future__ import annotations
import pandas as pd
import numpy as np

from backtest_strategies.base import Signal, StrategyConfig

NAME = "C18_ABSORPTION_REVERSAL"
CONFIG = StrategyConfig(
    name=NAME,
    description="Absorption proxy: small-body high-vol wick rejection at prior-day H/L or session VWAP.",
    cooldown_s=900,
    session_start_hour=7,
    session_end_hour=19,
)

_LEVEL_BUFFER = 3.0     # USD around key level
_VOL_MULT = 1.2
_BODY_RATIO_MAX = 0.40
_WICK_RATIO_MIN = 0.35  # rejection wick must be >= this fraction of range


def _session_vwap(bars: pd.DataFrame, session_date) -> float | None:
    s = bars[pd.to_datetime(bars["time"]).dt.date == session_date]
    if len(s) < 3:
        return None
    tp = (s["high"].astype(float) + s["low"].astype(float) + s["close"].astype(float)) / 3.0
    v = s["volume"].astype(float)
    tot_v = float(v.sum())
    if tot_v <= 0:
        return None
    return float((tp * v).sum() / tot_v)


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    try:
        if w5m is None or len(w5m) < 30 or w15m is None or len(w15m) < 20:
            return None

        today = now_utc.date()

        # Prior-day high/low from 15m bars not dated today
        prior = w15m[pd.to_datetime(w15m["time"]).dt.date != today]
        if len(prior) < 8:
            return None
        # use the most recent prior calendar date present
        prior_dates = pd.to_datetime(prior["time"]).dt.date
        last_prior_date = max(prior_dates.unique())
        prior = prior[prior_dates == last_prior_date]
        if len(prior) < 4:
            return None
        pd_high = float(prior["high"].max())
        pd_low = float(prior["low"].min())

        vwap = _session_vwap(w5m, today)

        # Volume + body shape on most recent closed 5m bar
        vol_med20 = float(w5m["volume"].tail(20).median())
        last = w5m.iloc[-1]
        bar_vol = float(last["volume"])
        o = float(last["open"]); h = float(last["high"]); l = float(last["low"]); c = float(last["close"])
        rng = h - l
        if rng <= 0 or vol_med20 <= 0 or bar_vol < _VOL_MULT * vol_med20:
            return None
        body = abs(c - o)
        if body / rng > _BODY_RATIO_MAX:
            return None

        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l

        # ATR(14) on 5m for stop sizing
        atr5 = float((w5m["high"] - w5m["low"]).tail(14).mean())
        if not np.isfinite(atr5) or atr5 <= 0:
            return None

        levels_up = [pd_high]
        levels_dn = [pd_low]
        if vwap is not None:
            levels_up.append(vwap)
            levels_dn.append(vwap)

        # Short setup: bar tagged a resistance level with upper-wick rejection
        if upper_wick / rng >= _WICK_RATIO_MIN:
            for lvl in levels_up:
                if (h >= lvl - _LEVEL_BUFFER) and (c < lvl) and (h - c) >= 0.3 * rng:
                    entry = round(c, 2)
                    sl = round(h + 0.5 * atr5, 2)
                    tp = round(c - 1.5 * atr5, 2)
                    if sl > entry > tp and (sl - entry) >= 0.5 and (entry - tp) >= 1.5:
                        return Signal("SELL", entry, sl, tp, "ABSORB_REJ_HI")

        # Long setup: bar tagged a support level with lower-wick rejection
        if lower_wick / rng >= _WICK_RATIO_MIN:
            for lvl in levels_dn:
                if (l <= lvl + _LEVEL_BUFFER) and (c > lvl) and (c - l) >= 0.3 * rng:
                    entry = round(c, 2)
                    sl = round(l - 0.5 * atr5, 2)
                    tp = round(c + 1.5 * atr5, 2)
                    if sl < entry < tp and (entry - sl) >= 0.5 and (tp - entry) >= 1.5:
                        return Signal("BUY", entry, sl, tp, "ABSORB_REJ_LO")

        return None
    except Exception:
        return None
