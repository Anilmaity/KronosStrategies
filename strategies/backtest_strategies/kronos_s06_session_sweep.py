"""
Kronos S06 -- Session-sweep Reversal (M15, two-way fade)
--------------------------------------------------------
Ported from TradingSkills `backtest/dev/stratset_session.py::s1_session_sweep`
+ `backtest/dev/htfgate_s06.py` with the v15e production gates layered on top.

ENTRY (two-way liquidity-sweep fade):
  * Working TF: M15 (last completed M15 bar).
  * The day is split into three sessions (UTC minute-of-day):
        Asian  : 00:00 - 07:00   (mn < 420)
        London : 07:00 - 13:00   (420 <= mn < 780)
        NY     : 13:00 - 21:00   (mn >= 780)
    At any bar in a later session, the most-recent COMPLETED session's
    high / low are the reference levels. A bar that POKES above the prior
    session high but CLOSES BACK BELOW it -> failed bullish liquidity grab
    -> fade SHORT. Mirror for the low -> fade LONG.
  * No-fade-into-trend overlay (source: `_no_fade_into_trend`):
        EMA(100), slope = EMA(100) - EMA(100).shift(20), slope_k=1.0
        Block a SHORT when close > EMA100 AND slope > 1.0 * ATR
        Block a LONG  when close < EMA100 AND slope < -1.0 * ATR
  * HTF-bias gate (htfgate_s06): take the fade only when NO higher
    timeframe opposes it --
        drop a SHORT when D1 bias == +1 OR H4 bias == +1
        drop a LONG  when D1 bias == -1 OR H4 bias == -1
    Zero new knobs; biases are the {-1,0,+1} classifier on the last CLOSED
    HTF bar (see `_kronos_indicators.htf_bias_from_window`).

EXIT (ATR(14) at signal bar, source: `_exits(d, atr, ks=1.0, kt=1.5, ...)`):
  * Stop loss  = entry +/- 1.0 * ATR
  * Take profit = entry +/- 1.5 * ATR

v15e production gates applied INSIDE get_signal:
  1. TNX |z_20d| >= 0.5  via `shared.macro_gate.tnx_gate_open`.
  2. Event-window E3 -- drop +/-2h of any major US macro event via
     `shared.event_gate.event_window_open`.
"""
from __future__ import annotations

import logging
from datetime import datetime

import numpy as np
import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig
from backtest_strategies._kronos_indicators import (
    atr, ema, minute_of_day,
    htf_bias_from_window,
)
from shared.macro_gate import tnx_gate_open
from shared.event_gate import event_window_open

log = logging.getLogger(__name__)

NAME = "KRONOS_S06_SWEEP"
CONFIG = StrategyConfig(
    name=NAME,
    description="Kronos S06 session-sweep two-way fade -- poke prior session "
                "high/low, close back inside; no-fade-into-trend overlay; "
                "HTF-bias OR gate; v15e TNX + E3 gates.",
    cooldown_s=900,           # 15 min -- M15 working TF
    session_start_hour=7,     # liquid hours 07:00 - 21:00 UTC (matches LIQUID)
    session_end_hour=21,
)

# Source-strategy knobs (untouched from stratset_session.s1_session_sweep)
_ATR_N = 14
_SL_ATR = 1.0           # ks
_TP_ATR = 1.5           # kt
_EMA_TREND = 100
_SLOPE_LB = 20          # bars in slope lookback
_SLOPE_K = 1.0          # _no_fade_into_trend slope_k

# Session boundaries in minute-of-day (UTC) -- mirrors stratset_session
_SESS_ASIA_END   = 420   # 07:00
_SESS_LONDON_END = 780   # 13:00


def _session_id(mn: int) -> int:
    if mn < _SESS_ASIA_END:
        return 0
    if mn < _SESS_LONDON_END:
        return 1
    return 2


def get_signal(w1m, w5m, w15m, now_utc: datetime) -> Signal | None:
    # Need enough history for EMA100 + slope window + ATR.
    if w15m is None or len(w15m) < _EMA_TREND + _SLOPE_LB + 5:
        return None

    # v15e production gates -------------------------------------------------
    if not tnx_gate_open(now_utc):
        return None
    if not event_window_open(now_utc, window_hours=2.0):
        return None
    # -----------------------------------------------------------------------

    d = w15m.copy()
    c = d["close"].astype(float).reset_index(drop=True)
    h = d["high"].astype(float).reset_index(drop=True)
    lo = d["low"].astype(float).reset_index(drop=True)
    a = atr(d, _ATR_N).reset_index(drop=True)
    e100 = ema(c, _EMA_TREND)
    slope = e100 - e100.shift(_SLOPE_LB)

    n = len(c)
    if n < 6:
        return None

    # Determine current session at the signal bar (last completed M15 bar).
    # We need the prior-session H/L within the SAME calendar day.
    t = pd.to_datetime(d["time"], utc=True).reset_index(drop=True)
    sig_mn = minute_of_day(t.iloc[-1])
    sig_sess = _session_id(sig_mn)
    if sig_sess == 0:
        return None   # Asian session -- no prior session today to fade

    # Build prior-session H/L: earlier sessions same UTC day, strictly before
    # the signal bar's session.
    sig_day = t.iloc[-1].date()
    day_mask = t.dt.date == sig_day
    if not day_mask.any():
        return None
    # earlier-session bars from today
    mns_day = t[day_mask].apply(lambda x: minute_of_day(x))
    sess_day = mns_day.map(_session_id)
    earlier = sess_day < sig_sess
    if not earlier.any():
        return None
    day_idx = np.where(day_mask.to_numpy())[0]
    earlier_idx = day_idx[earlier.to_numpy()]
    if len(earlier_idx) == 0:
        return None
    prev_hi = float(h.iloc[earlier_idx].max())
    prev_lo = float(lo.iloc[earlier_idx].min())
    if not (np.isfinite(prev_hi) and np.isfinite(prev_lo)):
        return None

    # Signal bar -- the candle that has just closed (causal).
    i_sig = n - 1
    bar_h = float(h.iloc[i_sig])
    bar_lo = float(lo.iloc[i_sig])
    bar_c = float(c.iloc[i_sig])

    # Sweep detection
    poke_up = (bar_h > prev_hi) and (bar_c < prev_hi)   # high-sweep -> short
    poke_dn = (bar_lo < prev_lo) and (bar_c > prev_lo)  # low-sweep  -> long
    if not (poke_up or poke_dn):
        return None
    side = "SELL" if poke_up else "BUY"

    # ATR known at signal bar
    if not pd.notna(a.iloc[i_sig]):
        return None
    atr_val = float(a.iloc[i_sig])
    if atr_val <= 0:
        return None
    atr_val = max(atr_val, 0.1)

    # No-fade-into-trend overlay (slope_k=1.0)
    if pd.notna(e100.iloc[i_sig]) and pd.notna(slope.iloc[i_sig]):
        e_val = float(e100.iloc[i_sig])
        sl_val = float(slope.iloc[i_sig])
        strong_up = (bar_c > e_val) and (sl_val > _SLOPE_K * atr_val)
        strong_dn = (bar_c < e_val) and (sl_val < -_SLOPE_K * atr_val)
        if side == "SELL" and strong_up:
            return None
        if side == "BUY" and strong_dn:
            return None

    # HTF-bias OR gate (htfgate_s06 build()): drop fade when any HTF opposes.
    d1_bias = htf_bias_from_window(w15m, "1D")
    h4_bias = htf_bias_from_window(w15m, "4h")
    if side == "SELL" and (d1_bias == 1 or h4_bias == 1):
        return None
    if side == "BUY" and (d1_bias == -1 or h4_bias == -1):
        return None

    # Build the fade signal -- ATR-scaled exits.
    entry_px = round(bar_c, 2)
    if side == "BUY":
        sl = round(bar_c - _SL_ATR * atr_val, 2)
        tp = round(bar_c + _TP_ATR * atr_val, 2)
        if not (sl < entry_px < tp):
            return None
    else:  # SELL
        sl = round(bar_c + _SL_ATR * atr_val, 2)
        tp = round(bar_c - _TP_ATR * atr_val, 2)
        if not (tp < entry_px < sl):
            return None
    return Signal(
        side=side,
        entry_price=entry_px,
        stop_loss=sl,
        take_profit=tp,
        reason=f"KRONOS_S06_SWEEP_{side}",
    )
