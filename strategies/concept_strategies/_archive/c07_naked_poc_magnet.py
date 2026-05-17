"""
C07 — Naked POC Magnet (XAUUSD adaptation)
------------------------------------------
Concept: a POC from a recent session that price has not revisited acts
as a magnet. After a liquidity sweep on the opposite side, price often
gravitates toward the untouched POC.
"""

from __future__ import annotations
import pandas as pd
import numpy as np
from datetime import timedelta

from backtest_strategies.base import Signal, StrategyConfig

NAME = "C07_NAKED_POC"
CONFIG = StrategyConfig(
    name=NAME,
    description="After a liquidity sweep, trade toward an untouched prior-session POC.",
    cooldown_s=900,
    session_start_hour=13,
    session_end_hour=19,
)


def _session_poc(bars: pd.DataFrame, bucket: float = 1.0):
    if bars is None or len(bars) < 4:
        return None
    lo = float(bars["low"].min())
    hi = float(bars["high"].max())
    if hi - lo < bucket:
        return None
    edges = np.arange(np.floor(lo), np.ceil(hi) + bucket, bucket)
    if len(edges) < 3:
        return None
    centers = (edges[:-1] + edges[1:]) / 2.0
    vols = np.zeros(len(centers), dtype=float)
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
    return float(centers[int(np.argmax(vols))])


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    try:
        if w5m is None or len(w5m) < 40 or w15m is None or len(w15m) < 40:
            return None

        today = now_utc.date()

        # Build naked POC inventory: prior 1..5 sessions (using w15m)
        npocs = []  # list of (price, age)
        # Touch-tracking: a prior POC is "naked" if no subsequent session's range covered it
        prior_pocs = []
        for age in range(1, 6):
            d = (now_utc - timedelta(days=age)).date()
            sess = w15m[pd.to_datetime(w15m["time"]).dt.date == d]
            if len(sess) < 8:
                continue
            poc = _session_poc(sess, bucket=1.0)
            if poc is None:
                continue
            prior_pocs.append((d, poc, age))

        if not prior_pocs:
            return None

        # Check whether any subsequent session (between source and today) touched the POC
        for src_date, poc, age in prior_pocs:
            touched = False
            for back in range(0, age):
                td = (now_utc - timedelta(days=back)).date()
                if td <= src_date:
                    continue
                sess = w15m[pd.to_datetime(w15m["time"]).dt.date == td]
                if len(sess) == 0:
                    continue
                if (sess["low"].min() <= poc <= sess["high"].max()):
                    touched = True
                    break
            if not touched:
                npocs.append((poc, age))

        if not npocs:
            return None

        # Daily ATR proxy: average daily range over last 14 days from w15m
        ranges = []
        for back in range(1, 15):
            d = (now_utc - timedelta(days=back)).date()
            sess = w15m[pd.to_datetime(w15m["time"]).dt.date == d]
            if len(sess) > 0:
                ranges.append(float(sess["high"].max() - sess["low"].min()))
        if len(ranges) < 5:
            return None
        atr14d = float(np.mean(ranges))
        if atr14d <= 0:
            return None

        # 5m ATR(5)
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
        last_high = float(last["high"])
        last_low = float(last["low"])
        prev_high = float(prev["high"])
        prev_low = float(prev["low"])
        prev_close = float(prev["close"])

        # Today's session reference levels (for sweep target ID)
        dev = w5m[pd.to_datetime(w5m["time"]).dt.date == today]
        if len(dev) < 3:
            return None
        today_high_pre = float(dev.iloc[:-1]["high"].max())
        today_low_pre = float(dev.iloc[:-1]["low"].min())

        # Identify sweep: prev bar pierced today_high or today_low by >= 0.25*ATR5 and closed back
        sweep_high = (prev_high > today_high_pre + 0.15 * atr5) and (prev_close < today_high_pre)
        sweep_low = (prev_low < today_low_pre - 0.15 * atr5) and (prev_close > today_low_pre)

        if not (sweep_high or sweep_low):
            return None

        # Absorption: sweep bar volume >= 1.2x median
        if float(prev["volume"]) < 1.2 * vol_med20:
            return None

        # Pick closest naked POC on the correct side
        if sweep_high:
            # Magnet must be below current price (short toward nPOC)
            candidates = [(p, a) for p, a in npocs if p < last_close - 0.5]
            if not candidates:
                return None
            poc_target, age = min(candidates, key=lambda t: abs(last_close - t[0]))
            if abs(last_close - poc_target) > 2.5 * atr14d:
                return None
            # Trigger: this 5m bar closes downward
            if last_close >= prev_close:
                return None
            entry = round(last_close, 2)
            sl = round(prev_high + 0.10, 2)
            tp = round(poc_target, 2)
            if sl > entry > tp and (sl - entry) >= 0.5 and (entry - tp) >= 1.0:
                return Signal("SELL", entry, sl, tp, f"NPOC_SWEEP_HI_a{age}")

        if sweep_low:
            candidates = [(p, a) for p, a in npocs if p > last_close + 0.5]
            if not candidates:
                return None
            poc_target, age = min(candidates, key=lambda t: abs(last_close - t[0]))
            if abs(last_close - poc_target) > 2.5 * atr14d:
                return None
            if last_close <= prev_close:
                return None
            entry = round(last_close, 2)
            sl = round(prev_low - 0.10, 2)
            tp = round(poc_target, 2)
            if sl < entry < tp and (entry - sl) >= 0.5 and (tp - entry) >= 1.0:
                return Signal("BUY", entry, sl, tp, f"NPOC_SWEEP_LO_a{age}")

        return None
    except Exception:
        return None
