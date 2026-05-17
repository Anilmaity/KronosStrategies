"""
C06 — Prior-Day VAH/VAL Fade (XAUUSD adaptation)
------------------------------------------------
Concept: in balance regimes, an excursion outside prior-day value that
fails to accept new value tends to revert to prior-day POC and/or the
opposite VA boundary. Adapted from ES/NQ to XAUUSD using session-based
intraday profile computed from 5m/15m bars.
"""

from __future__ import annotations
import pandas as pd
import numpy as np
from datetime import timedelta

from backtest_strategies.base import Signal, StrategyConfig

NAME = "C06_VAH_VAL_FADE"
CONFIG = StrategyConfig(
    name=NAME,
    description="Fade probes outside prior-day VAH/VAL on volume rejection back inside VA; target prior POC.",
    cooldown_s=900,
    session_start_hour=13,
    session_end_hour=19,
)


def _volume_profile(bars: pd.DataFrame, bucket: float = 1.0, va_pct: float = 0.70):
    """Return (poc, val, vah) computed from price/volume buckets."""
    if bars is None or len(bars) < 4:
        return None
    lo = float(bars["low"].min())
    hi = float(bars["high"].max())
    if not np.isfinite(lo) or not np.isfinite(hi) or hi - lo < bucket:
        return None
    # bucket midpoints
    edges = np.arange(np.floor(lo), np.ceil(hi) + bucket, bucket)
    if len(edges) < 3:
        return None
    centers = (edges[:-1] + edges[1:]) / 2.0
    vols = np.zeros(len(centers), dtype=float)
    # assign each bar's volume uniformly across its high-low range buckets
    for _, b in bars.iterrows():
        b_lo, b_hi, b_v = float(b["low"]), float(b["high"]), float(b["volume"])
        if b_hi <= b_lo:
            continue
        mask = (centers >= b_lo) & (centers <= b_hi)
        n = mask.sum()
        if n > 0 and b_v > 0:
            vols[mask] += b_v / n
    if vols.sum() <= 0:
        return None
    poc_idx = int(np.argmax(vols))
    total = vols.sum()
    target = total * va_pct
    # expand from POC outward
    lo_i = hi_i = poc_idx
    acc = vols[poc_idx]
    while acc < target and (lo_i > 0 or hi_i < len(vols) - 1):
        up = vols[hi_i + 1] if hi_i + 1 < len(vols) else -1
        dn = vols[lo_i - 1] if lo_i - 1 >= 0 else -1
        if up >= dn and hi_i + 1 < len(vols):
            hi_i += 1
            acc += vols[hi_i]
        elif lo_i - 1 >= 0:
            lo_i -= 1
            acc += vols[lo_i]
        else:
            break
    return float(centers[poc_idx]), float(centers[lo_i]), float(centers[hi_i])


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    try:
        if w5m is None or len(w5m) < 40 or w15m is None or len(w15m) < 30:
            return None

        today = now_utc.date()
        yday = (now_utc - timedelta(days=1)).date()

        # Prior-day reference: use w15m bars dated yesterday
        prior = w15m[pd.to_datetime(w15m["time"]).dt.date == yday]
        if len(prior) < 8:
            return None
        prof = _volume_profile(prior, bucket=1.0, va_pct=0.70)
        if prof is None:
            return None
        pd_poc, pd_val, pd_vah = prof
        va_width = pd_vah - pd_val
        if va_width < 1.0:
            return None

        # Developing session bars
        dev = w5m[pd.to_datetime(w5m["time"]).dt.date == today]
        if len(dev) < 6:
            return None

        # ATR(5) on 5m
        recent = w5m.tail(60).copy()
        atr5 = float((recent["high"] - recent["low"]).rolling(5).mean().iloc[-1])
        if not np.isfinite(atr5) or atr5 <= 0:
            return None

        # Volume guard
        vol_med20 = float(recent["volume"].rolling(20).median().iloc[-1])
        last = w5m.iloc[-1]
        bar_vol = float(last["volume"])
        if vol_med20 <= 0 or bar_vol < 1.05 * vol_med20:
            return None

        last_close = float(last["close"])
        last_high = float(last["high"])
        last_low = float(last["low"])
        prev = w5m.iloc[-2]
        prev_high = float(prev["high"])
        prev_low = float(prev["low"])

        # Check probe-and-return: prior bar broke outside VA, current closes back inside
        # Short setup: probed above VAH, closed back inside
        if prev_high > pd_vah and last_close < pd_vah and last_close < float(prev["close"]):
            entry = round(last_close, 2)
            sl = round(prev_high + 1.0 * atr5 + 0.10, 2)
            tp = round(pd_poc, 2)
            if sl > entry > tp and (sl - entry) >= 0.5 and (entry - tp) >= 1.0:
                return Signal("SELL", entry, sl, tp, "VAH_FADE")

        # Long setup: probed below VAL, closed back inside
        if prev_low < pd_val and last_close > pd_val and last_close > float(prev["close"]):
            entry = round(last_close, 2)
            sl = round(prev_low - 1.0 * atr5 - 0.10, 2)
            tp = round(pd_poc, 2)
            if sl < entry < tp and (entry - sl) >= 0.5 and (tp - entry) >= 1.0:
                return Signal("BUY", entry, sl, tp, "VAL_FADE")

        return None
    except Exception:
        return None
