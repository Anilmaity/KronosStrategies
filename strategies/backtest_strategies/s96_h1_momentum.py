"""
Kronos S96 -- pure M5 EMA9/21 crossover momentum (chandelier trailing)
----------------------------------------------------------------------
Momentum child strategy for the Strategy Manager roster. Logic rewritten
in place 2026-07-03 (spec: docs/superpowers/specs/
2026-07-03-s96-m5-ema-cross-design.md); the previous H1 Donchian(24)
continuation lives in git history. NAME keeps the historical
"KRONOS_S96_H1_MOMENTUM" identifier -- it is the strategy's DB identity
(entry_manager variation map, deploy_manager roster, ManagedStrategy
momentum slot, compose service env) and must not change.

Design (zero discretion), evaluated on the last closed M5 bar of w5m
(same window convention as s97_snap_scalper_m5):

  entry : EMA9/EMA21 crossover EVENT on M5 closes -- fast crosses above
          slow on the last bar -> BUY; fast crosses below slow -> SELL.
          Pure crossover: both directions, no HTF filter, no session
          window. Chop protection is the Strategy Manager's TRENDING +
          H4-bias gating policy, enforced by the manager, not here.
  stop  : hard initial stop = entry -/+ 1.5 x ATR(14, M5); trailing=True
          so position_monitor ratchets a chandelier stop off the
          high/low-water mark (trail distance = |entry - stop|). The TP
          emitted is only a far broker backstop, never a realistic cap.
  time  : max_hold_min = 480 (8 hours) backstop.

The 2026-07-02 backtest verdict (H1 Donchian, test PF 1.445) does NOT
apply to this logic. live_eligible was revoked with the rewrite
(db/revoke_s96_live_eligibility.py); re-validate through the backtest
harness before arming live.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig
from backtest_strategies._kronos_indicators import ema, atr

NAME = "KRONOS_S96_H1_MOMENTUM"  # historical DB identity -- do not change
CONFIG = StrategyConfig(
    name=NAME,
    description="Pure M5 EMA9/21 crossover: cross event on the last closed "
                "M5 bar, either direction, 1.5xATR(14,M5) chandelier "
                "trailing stop, 480-min time backstop.",
    cooldown_s=300,            # one M5 bar; a cross is an event, this is a backstop
    session_start_hour=None,   # runs around the clock -- the manager gates it
    session_end_hour=None,
    max_concurrent_positions=1,
)

# ── Knobs ─────────────────────────────────────────────────────────────────────
_EMA_FAST = 9
_EMA_SLOW = 21
_ATR_N    = 14
_K_ATR    = 1.5    # initial-stop distance == chandelier trail multiple
_FAR_ATR  = 30.0   # broker placeholder TP distance (never realistically hit)
_MAX_HOLD_MIN = 480

# Minimum M5 bars: EMA21 + ATR14 stabilisation with headroom.
_MIN_M5 = 120


def get_signal(w1m, w5m: pd.DataFrame, w15m, now_utc: datetime) -> Signal | None:
    if w5m is None or len(w5m) < _MIN_M5:
        return None

    c = w5m["close"].astype(float).reset_index(drop=True)
    ef = ema(c, _EMA_FAST)
    es = ema(c, _EMA_SLOW)
    a = atr(w5m.reset_index(drop=True), _ATR_N)

    i = len(c) - 1
    A = float(a.iloc[i])
    if not (A > 0):
        return None
    if (pd.isna(ef.iloc[i]) or pd.isna(es.iloc[i])
            or pd.isna(ef.iloc[i - 1]) or pd.isna(es.iloc[i - 1])):
        return None

    f_now, s_now = float(ef.iloc[i]), float(es.iloc[i])
    f_prev, s_prev = float(ef.iloc[i - 1]), float(es.iloc[i - 1])
    close = float(c.iloc[i])

    cross_up = f_prev <= s_prev and f_now > s_now
    cross_dn = f_prev >= s_prev and f_now < s_now

    if cross_up:
        return Signal(
            side="BUY",
            entry_price=close,
            stop_loss=round(close - _K_ATR * A, 2),
            take_profit=round(close + _FAR_ATR * A, 2),  # broker backstop only
            reason="S96_M5_CROSS_LONG",
            max_hold_min=_MAX_HOLD_MIN,
            trailing=True,
        )
    if cross_dn:
        return Signal(
            side="SELL",
            entry_price=close,
            stop_loss=round(close + _K_ATR * A, 2),
            take_profit=round(close - _FAR_ATR * A, 2),  # broker backstop only
            reason="S96_M5_CROSS_SHORT",
            max_hold_min=_MAX_HOLD_MIN,
            trailing=True,
        )
    return None
