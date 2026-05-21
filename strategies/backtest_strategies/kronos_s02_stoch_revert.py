"""
Kronos S02 -- Stoch(14) Oversold Reversion (M15)
------------------------------------------------
Ported from TradingSkills `backtest/dev/stratset_meanrev.py::s2_stoch_revert`
+ `backtest/dev/htfgate_s02.py` with the v15d production gates layered on top.

ENTRY (long-only mean reversion dip-buy):
  * Working TF: M15 (last completed M15 bar).
  * Trend filter: EMA(100) > EMA(200) read one bar before signal (uptrend).
  * Trigger: Stoch(14) on the prior bar < 15  (price pinned at the low of
    its 14-bar range).
  * HTF gate (from htfgate_s02): drop the long when DAILY bias == -1.
  * Liquidity gate (from htfgate_s02): drop the long unless the dip sits
    within 2.0 ATR of a prior-day liquidity zone (PDH / PDL / PD POC).

EXIT (ATR-scaled at fill):
  * Stop loss = entry - 3.0 * ATR(14)
  * Take profit = entry + 2.5 * ATR(14)
  * ATR is read one bar before the signal bar (causal).

v15e production gates applied INSIDE get_signal:
  1. TNX |z_20d| >= 0.5  via `shared.macro_gate.tnx_gate_open`.
  2. Event-window E3 -- skip a signal within +/-2h of any major US macro
     event (FOMC/CPI/NFP/PCE/SPEECH/ECB; importance >= 2) via
     `shared.event_gate.event_window_open`.

Source ceiling (per EXP_V15E_FINAL_REPORT.md, the new deployable winner):
    T_baseline | ANY weekday | drop=noSH | TNXz20_ge0.50 | E3
    -> 78.3% OOS win-day @ 5.02 tpd, DD 94 (halves the v15d DD of 222).
"""
from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig
from backtest_strategies._kronos_indicators import (
    atr, ema, stoch,
    htf_bias_from_window,
    prior_day_zones, zone_dist_atr_at,
)
from shared.macro_gate import tnx_gate_open
from shared.event_gate import event_window_open

log = logging.getLogger(__name__)

NAME = "KRONOS_S02_STOCH"
CONFIG = StrategyConfig(
    name=NAME,
    description="Kronos S02 Stoch(14)<15 oversold dip-buy in EMA100/200 uptrend "
                "+ D1 bias != -1 + within 2 ATR of prior-day liquidity zone "
                "(v3 port, v15d weekday + TNX gates).",
    cooldown_s=900,           # 15 min -- M15 working TF
    session_start_hour=7,     # liquid hours 07:00 - 21:00 UTC (matches source)
    session_end_hour=21,
)

# Source-strategy knobs (untouched from TradingSkills v3 + htfgate_s02)
_STOCH_N = 14
_STOCH_THR = 15
_ATR_N = 14
_SL_ATR = 3.0
_TP_ATR = 2.5
_ZONE_ATR = 2.0      # htfgate_s02.ZONE_ATR
_EMA_FAST = 100
_EMA_SLOW = 200


def get_signal(w1m, w5m, w15m, now_utc: datetime) -> Signal | None:
    # Need enough M15 history for EMA200 + ATR14 + Stoch14 + prior-day zones.
    if w15m is None or len(w15m) < _EMA_SLOW + 5:
        return None

    # ------------------------------------------------------------------ v15e
    # 1. TNX |z_20d| >= 0.5 (fails open on data error).
    if not tnx_gate_open(now_utc):
        return None
    # 2. Event-window E3 -- drop +/-2h around any major US macro event.
    if not event_window_open(now_utc, window_hours=2.0):
        return None
    # ------------------------------------------------------------------

    d = w15m.copy()
    c = d["close"].astype(float)
    a = atr(d, _ATR_N)
    st = stoch(d, _STOCH_N)
    e_fast = ema(c, _EMA_FAST)
    e_slow = ema(c, _EMA_SLOW)

    # Signal triggers on the LAST completed bar -- but the strategy code
    # reads everything "shifted by one" so a fresh signal at bar t uses
    # bar t-1 indicator values. We replicate that exactly: read bar t-1.
    if len(d) < 3:
        return None
    i_sig = -1                                # the bar that has just closed
    i_prev = -2                               # one bar earlier (causal shift)

    # Uptrend filter (shift(1) equivalent -- read EMA at the bar before
    # the candle we are about to trade on).
    if not (pd.notna(e_fast.iloc[i_prev]) and pd.notna(e_slow.iloc[i_prev])):
        return None
    if not (e_fast.iloc[i_prev] > e_slow.iloc[i_prev]):
        return None

    # Stoch trigger (shift(1) equivalent).
    if not pd.notna(st.iloc[i_prev]):
        return None
    if not (st.iloc[i_prev] < _STOCH_THR):
        return None

    # HTF D1 bias gate -- drop the long when D1 bias == -1.
    d1_bias = htf_bias_from_window(w15m, "1D")
    if d1_bias == -1:
        return None

    # ATR for sizing (shift(1) -- known at signal bar)
    atr_val = float(a.iloc[i_prev]) if pd.notna(a.iloc[i_prev]) else None
    if atr_val is None or atr_val <= 0:
        return None
    atr_val = max(atr_val, 0.1)

    # Liquidity-zone gate -- need zone_dist_atr <= 2.0
    entry_px = float(c.iloc[i_sig])
    pdh, pdl, pdc, pd_poc = prior_day_zones(w15m, now_utc)
    zdist = zone_dist_atr_at(entry_px, [pdh, pdl, pd_poc], atr_val)
    if not (zdist <= _ZONE_ATR):
        return None

    # Build the BUY signal.
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
        reason="KRONOS_S02_STOCH_REVERT",
    )
