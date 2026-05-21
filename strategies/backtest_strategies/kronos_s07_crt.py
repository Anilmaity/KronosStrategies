"""
Kronos S07 -- Candle Range Theory Sweep Fade (M15, two-way fade)
----------------------------------------------------------------
Ported from TradingSkills `backtest/dev/stratset_session.py::s2_crt`
+ `backtest/dev/htfgate_s07.py` with the v15e production gates layered on top.

ENTRY (CRT-style sweep-and-reverse, two-way fade):
  * Working TF: M15.
  * Reference = the PRIOR bar (t-1) -> range high = bar[t-1].high,
    range low = bar[t-1].low, mid = (high + low) / 2.
  * Range size gate: (high - low) >= 0.5 * ATR(14). Drops too-tight ranges.
  * Bull sweep -> SHORT: bar[t].high > range_high AND bar[t].close < mid
    AND bar[t].close > range_low. (Poke above, close back in lower half.)
  * Bear sweep -> LONG : bar[t].low < range_low AND bar[t].close > mid
    AND bar[t].close < range_high.
  * HTF-bias gate (htfgate_s07 build()): drop a fade only when BOTH HTFs
    oppose it (full-stack veto) --
        drop a SHORT when d1_bias == +1 AND h4_bias == +1
        drop a LONG  when d1_bias == -1 AND h4_bias == -1
    Zero new knobs.

EXIT (ATR(14) at signal bar, source: `_exits(d, atr, ks=1.5, kt=1.3)`):
  * Stop loss  = entry +/- 1.5 * ATR
  * Take profit = entry +/- 1.3 * ATR

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
    atr, htf_bias_from_window,
)
from shared.macro_gate import tnx_gate_open
from shared.event_gate import event_window_open

log = logging.getLogger(__name__)

NAME = "KRONOS_S07_CRT"
CONFIG = StrategyConfig(
    name=NAME,
    description="Kronos S07 CRT prior-bar sweep fade (two-way) -- 0.5*ATR "
                "range gate, close in back half; both-HTF-oppose veto; "
                "v15e TNX + E3 gates.",
    cooldown_s=900,
    session_start_hour=7,
    session_end_hour=21,
)

# Source-strategy knobs
_ATR_N = 14
_SL_ATR = 1.5
_TP_ATR = 1.3
_RANGE_ATR_MIN = 0.5     # range-size floor


def get_signal(w1m, w5m, w15m, now_utc: datetime) -> Signal | None:
    if w15m is None or len(w15m) < _ATR_N + 5:
        return None

    if not tnx_gate_open(now_utc):
        return None
    if not event_window_open(now_utc, window_hours=2.0):
        return None

    d = w15m.copy()
    c = d["close"].astype(float).reset_index(drop=True)
    h = d["high"].astype(float).reset_index(drop=True)
    lo = d["low"].astype(float).reset_index(drop=True)
    a = atr(d, _ATR_N).reset_index(drop=True)

    n = len(c)
    if n < 3:
        return None

    # Prior bar = the range (bar t-1); signal bar = the current closed bar.
    i_sig = n - 1
    i_prev = n - 2

    if not (pd.notna(h.iloc[i_prev]) and pd.notna(lo.iloc[i_prev])):
        return None
    range_h = float(h.iloc[i_prev])
    range_l = float(lo.iloc[i_prev])
    rng = range_h - range_l
    if rng <= 1e-9:
        return None
    mid = (range_h + range_l) / 2.0

    # ATR known at signal bar (causal)
    if not pd.notna(a.iloc[i_sig]):
        return None
    atr_val = float(a.iloc[i_sig])
    if atr_val <= 0:
        return None
    atr_val = max(atr_val, 0.1)

    # Range-size gate
    if rng < _RANGE_ATR_MIN * atr_val:
        return None

    bar_h = float(h.iloc[i_sig])
    bar_lo = float(lo.iloc[i_sig])
    bar_c = float(c.iloc[i_sig])

    # Bull sweep -> short: poke above prior high, close in LOWER half.
    sweep_up = (bar_h > range_h) and (bar_c < mid) and (bar_c > range_l)
    # Bear sweep -> long: poke below prior low, close in UPPER half.
    sweep_dn = (bar_lo < range_l) and (bar_c > mid) and (bar_c < range_h)
    if not (sweep_up or sweep_dn):
        return None
    side = "SELL" if sweep_up else "BUY"

    # HTF-bias gate -- drop only when BOTH HTFs oppose (full bullish/bearish stack).
    d1_bias = htf_bias_from_window(w15m, "1D")
    h4_bias = htf_bias_from_window(w15m, "4h")
    if side == "SELL" and d1_bias == 1 and h4_bias == 1:
        return None
    if side == "BUY" and d1_bias == -1 and h4_bias == -1:
        return None

    entry_px = round(bar_c, 2)
    if side == "BUY":
        sl = round(bar_c - _SL_ATR * atr_val, 2)
        tp = round(bar_c + _TP_ATR * atr_val, 2)
        if not (sl < entry_px < tp):
            return None
    else:
        sl = round(bar_c + _SL_ATR * atr_val, 2)
        tp = round(bar_c - _TP_ATR * atr_val, 2)
        if not (tp < entry_px < sl):
            return None
    return Signal(
        side=side,
        entry_price=entry_px,
        stop_loss=sl,
        take_profit=tp,
        reason=f"KRONOS_S07_CRT_{side}",
    )
