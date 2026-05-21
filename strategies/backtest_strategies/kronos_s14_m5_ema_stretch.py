"""
Kronos S14 -- M5 EMA20 Stretch + Falling-Knife Veto (M5, long-only)
-------------------------------------------------------------------
Ported from TradingSkills `backtest/dev/stratset_m5.py::s5_ema_stretch`
+ `backtest/dev/htfgate_s14.py` with the v15e production gates layered on top.

ENTRY (M5 mean-reversion dip-buy, long-only):
  * Working TF: M5 (last completed M5 bar). This strategy reads `w5m`, NOT
    `w15m` -- it is the only Kronos strategy in this batch that lives on M5.
  * Uptrend filter: EMA(100) > EMA(200) read on the prior closed bar.
  * Trigger: ATR-normalised stretch below EMA(20) at <= -1.5 ATR, read on
    the prior closed bar:
        stretch = (close - EMA20) / ATR(14)        (shift(1)-style)
        long when stretch <= -1.5
  * Falling-knife veto (htfgate_s14):
        below200_atr = (EMA200 - close) / ATR(14)  (shift(1)-style)
        drop the LONG when below200_atr >= KNIFE_ATR (3.5).
    Cuts the deep-drawdown dips while keeping ~98% of trades; lifts PF
    1.22 -> 1.30, drops max-DD 102 -> 86 in the source backtest.

EXIT (ATR(14) at signal bar -- source: `_apply(...stop_mult=2.5, tgt_mult=2.0)`):
  * Stop loss  = entry - 2.5 * ATR
  * Take profit = entry + 2.0 * ATR

v15e production gates applied INSIDE get_signal:
  1. TNX |z_20d| >= 0.5.
  2. Event-window E3 (+/-2h major events).
"""
from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig
from backtest_strategies._kronos_indicators import (
    atr, ema,
)
from shared.macro_gate import tnx_gate_open
from shared.event_gate import event_window_open

log = logging.getLogger(__name__)

NAME = "KRONOS_S14_M5_STRETCH"
CONFIG = StrategyConfig(
    name=NAME,
    description="Kronos S14 M5 EMA20 stretch <= -1.5 ATR dip-buy in "
                "EMA100/200 uptrend; falling-knife veto (>=3.5 ATR below "
                "EMA200); v15e TNX + E3 gates.",
    cooldown_s=300,           # 5 min -- M5 working TF
    session_start_hour=7,     # liquid hours 07:00 - 21:00 UTC (matches SESSION)
    session_end_hour=21,
)

# Source-strategy knobs (untouched from stratset_m5.s5_ema_stretch + htfgate_s14)
_ATR_N = 14
_EMA_FAST = 100
_EMA_SLOW = 200
_EMA_ANCHOR = 20
_STRETCH_THR = -1.5      # ATR multiples below EMA20
_KNIFE_ATR = 3.5         # falling-knife veto (htfgate_s14.KNIFE_ATR)
_SL_ATR = 2.5            # stop_mult
_TP_ATR = 2.0            # tgt_mult


def get_signal(w1m, w5m, w15m, now_utc: datetime) -> Signal | None:
    # M5 strategy -- reads w5m, not w15m. Need enough history for EMA200.
    if w5m is None or len(w5m) < _EMA_SLOW + 5:
        return None

    if not tnx_gate_open(now_utc):
        return None
    if not event_window_open(now_utc, window_hours=2.0):
        return None

    d = w5m.copy()
    c = d["close"].astype(float).reset_index(drop=True)
    a = atr(d, _ATR_N).reset_index(drop=True)
    e_fast = ema(c, _EMA_FAST)
    e_slow = ema(c, _EMA_SLOW)
    e_anchor = ema(c, _EMA_ANCHOR)

    n = len(c)
    if n < 3:
        return None
    i_sig = n - 1
    i_prev = n - 2

    # Uptrend filter (shift(1)-style: read EMAs on prior closed bar)
    if not (pd.notna(e_fast.iloc[i_prev]) and pd.notna(e_slow.iloc[i_prev])):
        return None
    if not (e_fast.iloc[i_prev] > e_slow.iloc[i_prev]):
        return None

    # ATR shifted by one bar (known at signal bar)
    if not pd.notna(a.iloc[i_prev]):
        return None
    atr_prev = float(a.iloc[i_prev])
    if atr_prev <= 0:
        return None
    atr_prev = max(atr_prev, 0.1)

    # Stretch trigger (shift(1)-style)
    if not pd.notna(e_anchor.iloc[i_prev]):
        return None
    stretch_prev = (float(c.iloc[i_prev]) - float(e_anchor.iloc[i_prev])) / atr_prev
    if not (stretch_prev <= _STRETCH_THR):
        return None

    # Falling-knife veto: drop LONGs that sit too deep below EMA200.
    if not pd.notna(e_slow.iloc[i_prev]):
        return None
    below200_atr_prev = (float(e_slow.iloc[i_prev]) - float(c.iloc[i_prev])) / atr_prev
    if below200_atr_prev >= _KNIFE_ATR:
        return None

    # ATR for sizing: source uses _apply -> shift(1) on ATR for stop/target.
    atr_val = atr_prev

    entry_px = float(c.iloc[i_sig])
    sl = round(entry_px - _SL_ATR * atr_val, 2)
    tp = round(entry_px + _TP_ATR * atr_val, 2)
    entry_px_r = round(entry_px, 2)
    if not (sl < entry_px_r < tp):
        return None
    return Signal(
        side="BUY",
        entry_price=entry_px_r,
        stop_loss=sl,
        take_profit=tp,
        reason="KRONOS_S14_M5_STRETCH",
    )
