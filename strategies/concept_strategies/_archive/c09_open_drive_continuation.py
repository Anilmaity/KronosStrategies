"""
C09 — Open-Drive Trend Day Continuation (XAUUSD adaptation)
-----------------------------------------------------------
Concept: when session opens with initiative drive away from prior value
and holds, pullbacks to developing VWAP are continuation entries in the
drive direction.
"""

from __future__ import annotations
import pandas as pd
import numpy as np
from datetime import timedelta, time

from backtest_strategies.base import Signal, StrategyConfig

NAME = "C09_OPEN_DRIVE"
CONFIG = StrategyConfig(
    name=NAME,
    description="Trend-day continuation: VWAP pullback in the open-drive direction.",
    cooldown_s=900,
    session_start_hour=13,
    session_end_hour=19,
)


def _session_vwap(dev: pd.DataFrame):
    tp = (dev["high"] + dev["low"] + dev["close"]) / 3.0
    vol = dev["volume"].astype(float).clip(lower=0)
    cv = vol.cumsum()
    if cv.iloc[-1] <= 0:
        return None
    cpv = (tp * vol).cumsum()
    return float((cpv / cv.replace(0, np.nan)).iloc[-1])


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    try:
        if w5m is None or len(w5m) < 40 or w15m is None or len(w15m) < 30:
            return None

        today = now_utc.date()
        yday = (now_utc - timedelta(days=1)).date()

        dev = w5m[pd.to_datetime(w5m["time"]).dt.date == today].copy()
        if len(dev) < 8:
            return None

        # Need to be past the open-drive confirmation window (first 30min = 6 bars)
        if len(dev) < 7:
            return None

        # Prior-day VA (rough): use 25th/75th percentile of prior-day 15m closes as proxy
        prior = w15m[pd.to_datetime(w15m["time"]).dt.date == yday]
        if len(prior) < 8:
            return None
        pd_val = float(prior["close"].quantile(0.15))
        pd_vah = float(prior["close"].quantile(0.85))
        pd_high = float(prior["high"].max())
        pd_low = float(prior["low"].min())

        # IB proxy from prior 20-day avg of (first-6-bars high-low) — approximate via daily ATR
        ranges = []
        for back in range(1, 21):
            d = (now_utc - timedelta(days=back)).date()
            sess = w15m[pd.to_datetime(w15m["time"]).dt.date == d]
            if len(sess) >= 4:
                first_hour = sess.iloc[:4]
                ranges.append(float(first_hour["high"].max() - first_hour["low"].min()))
        if len(ranges) < 5:
            return None
        avg_ib = float(np.mean(ranges))
        if avg_ib <= 0:
            return None

        # Open-drive: within first 3 bars (15min) price moved >= 0.75*avg_ib away from pd VA
        first3 = dev.iloc[:3]
        f3_high = float(first3["high"].max())
        f3_low = float(first3["low"].min())
        open_px = float(first3.iloc[0]["open"])

        drive_up = (f3_high - pd_vah) >= 0.5 * avg_ib
        drive_dn = (pd_val - f3_low) >= 0.5 * avg_ib

        if not (drive_up or drive_dn):
            return None

        # Hold: at bar index 6 (30min), price must remain outside pd VA in drive direction
        b6_close = float(dev.iloc[min(5, len(dev) - 1)]["close"])
        if drive_up and b6_close < pd_vah:
            return None
        if drive_dn and b6_close > pd_val:
            return None

        # Volume expansion in first 5 bars (drive bars)
        first5 = dev.iloc[:5]
        if len(first5) < 5:
            return None
        v = first5["volume"].astype(float).values
        expanding = sum(1 for i in range(1, 5) if v[i] >= v[i - 1])
        if expanding < 2:
            return None

        vwap = _session_vwap(dev)
        if vwap is None:
            return None

        recent = w5m.tail(60).copy()
        atr5 = float((recent["high"] - recent["low"]).rolling(5).mean().iloc[-1])
        if not np.isfinite(atr5) or atr5 <= 0:
            return None
        vol_med20 = float(recent["volume"].rolling(20).median().iloc[-1])
        if vol_med20 <= 0:
            return None

        last = w5m.iloc[-1]
        prev = w5m.iloc[-2]
        last_close = float(last["close"])
        last_open = float(last["open"])
        last_high = float(last["high"])
        last_low = float(last["low"])
        bar_vol = float(last["volume"])

        # Pullback to VWAP (low-volume signature)
        touched_vwap_recent = (float(prev["low"]) <= vwap <= float(prev["high"])) or (last_low <= vwap <= last_high)
        if not touched_vwap_recent:
            return None
        # Pullback bar volume low (relaxed)
        if float(prev["volume"]) > 1.4 * vol_med20:
            return None

        if drive_up and last_close > last_open and last_close > vwap:
            # Invalidation guard: not back inside pd VA
            if last_close < pd_vah:
                return None
            entry = round(last_close, 2)
            sl = round(min(last_low, float(prev["low"])) - 0.10, 2)
            risk = entry - sl
            if risk < 0.5 or risk > 5.0:
                return None
            tp = round(entry + max(2.0 * risk, pd_high - entry), 2)
            if sl < entry < tp:
                return Signal("BUY", entry, sl, tp, "OPEN_DRIVE_UP")

        if drive_dn and last_close < last_open and last_close < vwap:
            if last_close > pd_val:
                return None
            entry = round(last_close, 2)
            sl = round(max(last_high, float(prev["high"])) + 0.10, 2)
            risk = sl - entry
            if risk < 0.5 or risk > 5.0:
                return None
            tp = round(entry - max(2.0 * risk, entry - pd_low), 2)
            if sl > entry > tp:
                return Signal("SELL", entry, sl, tp, "OPEN_DRIVE_DN")

        return None
    except Exception:
        return None
