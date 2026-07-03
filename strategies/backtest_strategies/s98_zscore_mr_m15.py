"""
Kronos S98 -- M15 Z-Score Mean Reversion (PAPER slot)
-----------------------------------------------------
Replaces S97 snap-fade in the Strategy Manager scalper slot
(spec docs/superpowers/specs/2026-07-03-s98-zscore-mr-design.md).

Design (zero discretion), evaluated on the last CLOSED M15 bar:
  z         : (close - SMA50) / std50 on M15 closes.
  entry     : ON THE CROSS only -- previous closed bar |z| < 2.0 AND current
              z >= +2.0 -> SELL (fade the stretch), z <= -2.0 -> BUY.
              No refire while the series stays stretched.
  ADF gate  : adfuller on the last 50 (close - SMA50) residuals; entry only
              if p < 0.05. Non-stationary residuals = trending tape = no fade.
  exits     : TP = SMA50 (z = 0), hard SL at z = +/-3.5, max_hold_min = 240.
  session   : 03:00-09:00 UTC only (CONFIG *and* in-function, like S97).

Exactly 3 parameters: _LOOKBACK, _ENTRY_Z, _STOP_Z. Everything else is a
fixed convention. Every signal has a hard stop; no averaging, no martingale.

Manager gating policy (quiet_mr): 03:00-09:00 UTC, vol_regime in
{LOW, NORMAL}, trend_regime != TRENDING -- bias-agnostic (MR trades both
directions), applied by the manager on top of the gates in here.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
from statsmodels.tsa.stattools import adfuller

from backtest_strategies.base import Signal, StrategyConfig

NAME = "KRONOS_S98_ZSCORE_MR"
CONFIG = StrategyConfig(
    name=NAME,
    description="M15 z-score mean reversion: enter on |z| crossing 2.0 "
                "(SMA50/std50), TP at the mean, hard SL at z=3.5, ADF "
                "stationarity gate (p<0.05), 240-min time exit. "
                "03:00-09:00 UTC. PAPER-only slot.",
    cooldown_s=900,            # one M15 bar; crossing logic is the real dedup
    session_start_hour=3,
    session_end_hour=9,
    max_concurrent_positions=1,
)

# -- The 3 parameters ---------------------------------------------------------
_LOOKBACK = 50     # rolling window for SMA / std / ADF residuals
_ENTRY_Z  = 2.0    # entry threshold (crossing)
_STOP_Z   = 3.5    # hard stop distance in z units

# -- Fixed conventions (not parameters) ---------------------------------------
_ADF_P        = 0.05
_MAX_HOLD_MIN = 240
_SESSION_START_H = 3   # UTC, inclusive
_SESSION_END_H   = 9   # UTC, exclusive
_BAR = pd.Timedelta(minutes=15)


def _closed_only(w15m: pd.DataFrame, now_utc: datetime) -> pd.DataFrame:
    """Drop the last row if its bar is still forming at now_utc.

    Both the live runner and the sim engine already pass closed bars; this
    is defense in depth so a forming bar can never leak into z or ADF.
    """
    if len(w15m) == 0:
        return w15m
    last = pd.Timestamp(w15m["time"].iloc[-1])
    if last.tzinfo is None:
        last = last.tz_localize("UTC")
    if last + _BAR > pd.Timestamp(now_utc):
        return w15m.iloc[:-1]
    return w15m


def get_signal(w1m, w5m, w15m: pd.DataFrame | None,
               now_utc: datetime) -> Signal | None:
    # Session gate (defense in depth -- CONFIG hours gate the runner too).
    if not (_SESSION_START_H <= now_utc.hour < _SESSION_END_H):
        return None

    if w15m is None or len(w15m) < _LOOKBACK + 2:
        return None
    df = _closed_only(w15m, now_utc)
    if len(df) < _LOOKBACK + 2:
        return None

    c = df["close"].astype(float)
    sma = c.rolling(_LOOKBACK).mean()
    sd = c.rolling(_LOOKBACK).std(ddof=1)
    sd_now = float(sd.iloc[-1])
    if pd.isna(sd_now) or sd_now <= 0:
        return None

    z = (c - sma) / sd
    z_now, z_prev = float(z.iloc[-1]), float(z.iloc[-2])
    if pd.isna(z_now) or pd.isna(z_prev):
        return None

    # Entry on the cross, not the state.
    if abs(z_prev) >= _ENTRY_Z or abs(z_now) < _ENTRY_Z:
        return None
    side = "SELL" if z_now >= _ENTRY_Z else "BUY"

    # ADF stationarity gate on the mean-reversion residuals.
    resid = (c - sma).dropna().iloc[-_LOOKBACK:]
    if len(resid) < _LOOKBACK:
        return None
    try:
        p_value = adfuller(resid.to_numpy(), autolag="AIC")[1]
    except Exception:
        return None
    if not (p_value < _ADF_P):
        return None

    entry = float(c.iloc[-1])
    mean = float(sma.iloc[-1])

    if side == "SELL":
        sl = round(mean + _STOP_Z * sd_now, 2)
        tp = round(mean, 2)
        if not (sl > entry > tp):
            return None
        return Signal(side="SELL", entry_price=entry, stop_loss=sl,
                      take_profit=tp, reason="S98_ZMR_SELL",
                      max_hold_min=_MAX_HOLD_MIN)

    sl = round(mean - _STOP_Z * sd_now, 2)
    tp = round(mean, 2)
    if not (sl < entry < tp):
        return None
    return Signal(side="BUY", entry_price=entry, stop_loss=sl,
                  take_profit=tp, reason="S98_ZMR_BUY",
                  max_hold_min=_MAX_HOLD_MIN)
