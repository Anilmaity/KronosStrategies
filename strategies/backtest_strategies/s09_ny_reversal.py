"""
S09 — NY Session Reversal
--------------------------
Concept: When London makes the HOD/LOD by 13:00 UTC and price has already
run, NY often reverses. Trade the first 1m rejection at the London extreme
between 13:00–15:00 UTC.
"""

from __future__ import annotations
import pandas as pd
from datetime import datetime

from backtest_strategies.base import Signal, StrategyConfig
from strategy.liquidity_engine import is_rejection_wick

NAME = "NY_REV"
CONFIG = StrategyConfig(
    name=NAME,
    description="NY reversal at London HOD/LOD",
    cooldown_s=600,
    session_start_hour=13,
    session_end_hour=15,
)


def _london_extreme(w1m: pd.DataFrame, now_utc: datetime) -> tuple[float, float] | None:
    """Naive UTC datetimes — must match TSDB candle.time format."""
    today = now_utc.date()
    london_start = datetime(today.year, today.month, today.day, 7, 0)
    london_end   = datetime(today.year, today.month, today.day, 13, 0)
    mask = (w1m["time"] >= london_start) & (w1m["time"] < london_end)
    london = w1m.loc[mask]
    if len(london) < 60:
        return None
    return float(london["high"].max()), float(london["low"].min())


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    if len(w1m) < 60:
        return None

    ext = _london_extreme(w1m, now_utc)
    if ext is None:
        return None
    hod, lod = ext

    # Look at last 5 candles for a sweep + rejection at HOD/LOD
    last5 = w1m.tail(5)
    candle = w1m.iloc[-1]
    close = float(candle["close"])

    swept_hod = (last5["high"] > hod).any()
    swept_lod = (last5["low"] < lod).any()

    # Sweep of HOD then close back below → SELL
    if swept_hod and close < hod - 0.2:
        sl = round(float(last5["high"].max()) + 0.3, 2)
        risk = sl - close
        if 0.5 < risk < 5:
            tp = round(close - 2 * risk, 2)
            return Signal("SELL", round(close, 2), sl, tp, "NY_REV_HOD")

    if swept_lod and close > lod + 0.2:
        sl = round(float(last5["low"].min()) - 0.3, 2)
        risk = close - sl
        if 0.5 < risk < 5:
            tp = round(close + 2 * risk, 2)
            return Signal("BUY", round(close, 2), sl, tp, "NY_REV_LOD")

    return None
