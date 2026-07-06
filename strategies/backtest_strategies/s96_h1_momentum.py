"""
Kronos S96 -- H1 Donchian(24) continuation momentum (static high-WR exits)
--------------------------------------------------------------------------
Momentum child strategy for the Strategy Manager roster. Rewritten AGAIN
2026-07-06: the interim pure M5 EMA9/21 crossover proved to have negative
edge at EVERY exit geometry and filter combination tested (288 configs incl.
H4-bias + efficiency-ratio gates -- best train PF 0.94, all net-negative;
see backtest/optimize_manager_strategies.py). This returns to the H1
Donchian continuation family (the design that validated at test PF 1.445 on
2026-07-02) with the WR-optimized static exits:

  entry : last CLOSED H1 bar closes beyond the prior 24-bar Donchian
          extreme, in the direction of the H1 EMA20/50 bias.
  stop  : static, entry -/+ 3.0 x ATR(14, H1).
  tp    : static, entry +/- 0.4R = 1.2 x ATR(14, H1)  (high-WR geometry).
  time  : none -- the broker SL/TP resolve the position (sim parity).

Validation (18mo M5-era data, cost 0.45pt, train 2025 / test 2026H1):
  train WR 78.8% PF 1.50, test WR 79.8% PF 1.36; robust at 0.80pt stress
  (train PF 1.45 / test PF 1.33) and across don20/don48 neighbors.

H1 bars are resampled from the 15m window (same pattern as
kronos_challenge_xau resamples H4): needs >= (EMA50 + Donchian24 + 2) = 76
CLOSED H1 bars => >= ~310 fifteen-min bars; compose pins RESEARCH_WIN_15M
well past that (otherwise get_signal stays silent -- the classic gotcha).

NAME keeps the historical "KRONOS_S96_H1_MOMENTUM" identifier -- it is the
strategy's DB identity (entry_manager variation map, deploy_manager roster,
ManagedStrategy momentum slot, compose service env) and must not change.
live_eligible is revoked at deploy time via db/revoke_s96_live_eligibility.py;
arm PAPER first and compare fills against this backtest before going live.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig
from backtest_strategies._kronos_indicators import ema, atr

NAME = "KRONOS_S96_H1_MOMENTUM"  # historical DB identity -- do not change
CONFIG = StrategyConfig(
    name=NAME,
    description="H1 Donchian(24) continuation, EMA20/50 bias, static SL "
                "3xATR(14,H1), static TP 0.4R (1.2xATR). High-WR geometry "
                "validated 2026-07-06 (train WR 78.8%/PF 1.50, test WR "
                "79.8%/PF 1.36).",
    cooldown_s=3600,           # one H1 bar: at most one entry per closed bar
    session_start_hour=None,   # runs around the clock -- the manager gates it
    session_end_hour=None,
    max_concurrent_positions=1,
)

# ── Knobs (validated 2026-07-06) ──────────────────────────────────────────────
_N        = 24     # Donchian lookback (prior bars, excluding current)
_EMA_FAST = 20
_EMA_SLOW = 50
_ATR_N    = 14
_K_ATR    = 3.0    # static initial-stop distance
_TP_R     = 0.4    # TP as fraction of the SL distance => 1.2 x ATR

# Minimum closed H1 bars: EMA_SLOW stabilisation + Donchian lookback.
_MIN_H1 = _EMA_SLOW + _N + 2


def _resample_h1(w15m: pd.DataFrame) -> pd.DataFrame:
    """Resample a 15m window to H1 OHLC (UTC, top-of-hour buckets), dropping
    the last still-forming bucket so every returned bar is CLOSED."""
    t = pd.to_datetime(w15m["time"], utc=True)
    df = pd.DataFrame(
        {
            "open":  w15m["open"].astype(float).to_numpy(),
            "high":  w15m["high"].astype(float).to_numpy(),
            "low":   w15m["low"].astype(float).to_numpy(),
            "close": w15m["close"].astype(float).to_numpy(),
        },
        index=t,
    )
    h1 = (
        df.resample("1h", label="left", closed="left")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )
    # The final bucket is (almost always) still forming -> not a closed bar.
    return h1.iloc[:-1] if len(h1) else h1


def get_signal(w1m, w5m, w15m: pd.DataFrame, now_utc: datetime) -> Signal | None:
    if w15m is None or len(w15m) < _MIN_H1 * 4:  # 4 fifteen-min bars per H1
        return None

    h1 = _resample_h1(w15m)
    if len(h1) < _MIN_H1:
        return None

    c = h1["close"].reset_index(drop=True)
    h = h1["high"].reset_index(drop=True)
    lo = h1["low"].reset_index(drop=True)

    ef = ema(c, _EMA_FAST)
    es = ema(c, _EMA_SLOW)
    a = atr(h1.reset_index(drop=True), _ATR_N)

    i = len(c) - 1
    A = float(a.iloc[i])
    if not (A > 0):
        return None
    if pd.isna(ef.iloc[i]) or pd.isna(es.iloc[i]):
        return None

    donch_hi = float(h.iloc[i - _N:i].max())
    donch_lo = float(lo.iloc[i - _N:i].min())
    close = float(c.iloc[i])
    up = float(ef.iloc[i]) > float(es.iloc[i])

    risk = _K_ATR * A
    if close > donch_hi and up:
        return Signal(
            side="BUY",
            entry_price=close,
            stop_loss=round(close - risk, 2),
            take_profit=round(close + _TP_R * risk, 2),
            reason="S96_H1_DON_LONG",
        )
    if close < donch_lo and not up:
        return Signal(
            side="SELL",
            entry_price=close,
            stop_loss=round(close + risk, 2),
            take_profit=round(close - _TP_R * risk, 2),
            reason="S96_H1_DON_SHORT",
        )
    return None
