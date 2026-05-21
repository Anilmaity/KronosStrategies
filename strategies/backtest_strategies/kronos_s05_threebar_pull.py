"""
Kronos S05 -- 3-Bar Down-Bar Pullback in Uptrend (M15)
------------------------------------------------------
Ported from TradingSkills `backtest/dev/stratset_meanrev.py::s5_threebar_pullback`
+ `backtest/dev/htfgate_s05.py` with the v15d production gates layered on top.

ENTRY (long-only price-pattern dip-buy):
  * Working TF: M15 (last completed M15 bar).
  * Uptrend filter: EMA(100) > EMA(200) one bar before the signal.
  * Trigger: the three bars ending at signal-bar-minus-one each made a
    lower LOW *and* a lower CLOSE than the prior bar (a clean 3-bar pull).
    Reading `three_down.shift(1)` in the source means the signal fires
    when the pattern completed on bars [-4, -3, -2] (relative to the
    candle we trade on); the trade is taken on bar -1's close.
  * Liquidity gate (htfgate_s05): drop the long when BOTH
        (a) close is within 0.50 ATR of the nearest prior-day level
            (PDH / PDL / PD POC), AND
        (b) close is BELOW that nearest level (broken-support condition).

EXIT:
  * Stop loss  = entry - 3.0 * ATR(14)
  * Take profit = entry + 2.0 * ATR(14)

v15e production gates applied INSIDE get_signal:
  1. TNX |z_20d| >= 0.5.
  2. Event-window E3 -- drop +/-2h around any major US macro event
     (FOMC/CPI/NFP/PCE/SPEECH/ECB; importance >= 2).
"""
from __future__ import annotations

import logging
from datetime import datetime

import numpy as np
import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig
from backtest_strategies._kronos_indicators import (
    atr, ema,
    prior_day_zones, zone_dist_atr_at,
)
from shared.macro_gate import tnx_gate_open
from shared.event_gate import event_window_open

log = logging.getLogger(__name__)

NAME = "KRONOS_S05_THREEBAR"
CONFIG = StrategyConfig(
    name=NAME,
    description="Kronos S05 three-bar down-bar pullback in EMA100/200 uptrend, "
                "skip broken-below prior-day levels (v3 port, v15e gates).",
    cooldown_s=900,
    session_start_hour=7,
    session_end_hour=21,
)

# Source-strategy knobs
_ATR_N = 14
_SL_ATR = 3.0
_TP_ATR = 2.0
_ZONE_ATR = 0.50         # htfgate_s05.ZONE_ATR
_EMA_FAST = 100
_EMA_SLOW = 200


def get_signal(w1m, w5m, w15m, now_utc: datetime) -> Signal | None:
    if w15m is None or len(w15m) < _EMA_SLOW + 5:
        return None

    if not tnx_gate_open(now_utc):
        return None
    if not event_window_open(now_utc, window_hours=2.0):
        return None

    d = w15m.copy()
    c = d["close"].astype(float)
    lo = d["low"].astype(float)
    a = atr(d, _ATR_N)
    e_fast = ema(c, _EMA_FAST)
    e_slow = ema(c, _EMA_SLOW)

    # Trade on the bar that has just closed (last completed bar).
    if len(d) < 6:
        return None
    i_sig = -1
    i_prev = -2

    # Uptrend filter, shift(1)-style
    if not (pd.notna(e_fast.iloc[i_prev]) and pd.notna(e_slow.iloc[i_prev])):
        return None
    if not (e_fast.iloc[i_prev] > e_slow.iloc[i_prev]):
        return None

    # Three-bar down-bar pattern. The source does:
    #     down_bar[t] = (low[t] < low[t-1]) & (close[t] < close[t-1])
    #     three_down  = down_bar & down_bar.shift(1) & down_bar.shift(2)
    #     long_sig    = three_down.shift(1)  -> fires the bar AFTER 3 down bars
    # i.e. at signal bar t, the three down-bars are t-1, t-2, t-3, which
    # means we need lows and closes of bars [-2, -3, -4] each falling vs the
    # bar before them.
    def _down(i):
        return (lo.iloc[i] < lo.iloc[i - 1]) and (c.iloc[i] < c.iloc[i - 1])
    try:
        three_down = _down(-2) and _down(-3) and _down(-4)
    except Exception:
        return None
    if not three_down:
        return None

    atr_val = float(a.iloc[i_prev]) if pd.notna(a.iloc[i_prev]) else None
    if atr_val is None or atr_val <= 0:
        return None
    atr_val = max(atr_val, 0.1)

    entry_px = float(c.iloc[i_sig])
    pdh, pdl, pdc, pd_poc = prior_day_zones(w15m, now_utc)

    # Liquidity gate -- drop at-and-broken-below the nearest prior-day level.
    levels = [v for v in (pdh, pdl, pd_poc) if v is not None and np.isfinite(v)]
    if levels:
        nearest = min(levels, key=lambda v: abs(entry_px - v))
        zdist = abs(entry_px - nearest) / atr_val
        at_zone = (zdist <= _ZONE_ATR)
        below_level = entry_px < nearest
        if at_zone and below_level:
            return None  # broken support -- skip

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
        reason="KRONOS_S05_THREEBAR_PULL",
    )
