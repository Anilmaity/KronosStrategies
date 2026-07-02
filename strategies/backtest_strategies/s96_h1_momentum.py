"""
Kronos S96 -- H1 Donchian(24) Momentum Continuation (chandelier trailing)
-------------------------------------------------------------------------
Momentum child strategy for the Strategy Manager roster
(docs/superpowers/specs/2026-07-02-strategy-manager-design.md §4).

Design (zero discretion), evaluated on the last CLOSED H1 bar (resampled
from the 15m runner window, dropping the still-forming bucket -- strictly
causal, same pattern as kronos_challenge_xau):

  bias  : EMA20 > EMA50 on H1 (long-only) / EMA20 < EMA50 (short-only)
  entry : H1 close through the prior-24-bar Donchian high (long) / low
          (short), in the bias direction -- close-through continuation.
  stop  : hard initial stop = entry -/+ 1.5 x ATR(14, H1); trailing=True so
          position_monitor ratchets a chandelier stop off the high/low-water
          mark (trail distance = |entry - stop|). The TP emitted is only a
          far broker backstop, never a realistic cap.
  time  : max_hold_min = 2880 (2 days) backstop.

The live research_runner must pull enough 15m history to form >= 76 closed
H1 bars (set RESEARCH_WIN_15M / RESEARCH_DAYS_15M accordingly).

Manager gating policy (spec §4): run when trend_regime=TRENDING and
h4_bias != neutral -- enforced by the manager, not here.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig
from backtest_strategies._kronos_indicators import ema, atr

NAME = "KRONOS_S96_H1_MOMENTUM"
CONFIG = StrategyConfig(
    name=NAME,
    description="H1 Donchian(24) close-through continuation with EMA20/50 "
                "agreement, 1.5xATR(14,H1) chandelier trailing stop, "
                "2880-min time backstop.",
    cooldown_s=3600,           # one H1 bar: at most one entry per closed bar
    session_start_hour=None,   # momentum runs around the clock
    session_end_hour=None,
    max_concurrent_positions=1,
)

# ── Knobs ─────────────────────────────────────────────────────────────────────
_DONCH    = 24     # Donchian lookback (prior bars, excluding current)
_EMA_FAST = 20
_EMA_SLOW = 50
_ATR_N    = 14
_K_ATR    = 1.5    # initial-stop distance == chandelier trail multiple
_FAR_ATR  = 30.0   # broker placeholder TP distance (never realistically hit)
_MAX_HOLD_MIN = 2880

# Minimum closed H1 bars: EMA_SLOW stabilisation + Donchian lookback.
_MIN_H1 = _EMA_SLOW + _DONCH + 2


def _resample_h1(w15m: pd.DataFrame) -> pd.DataFrame:
    """Resample a 15m window to H1 OHLC (UTC buckets), dropping the last
    still-forming bucket so every returned bar is CLOSED."""
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

    donch_hi = float(h.iloc[i - _DONCH:i].max())
    donch_lo = float(lo.iloc[i - _DONCH:i].min())
    close = float(c.iloc[i])
    up = float(ef.iloc[i]) > float(es.iloc[i])
    down = float(ef.iloc[i]) < float(es.iloc[i])

    if close > donch_hi and up:
        return Signal(
            side="BUY",
            entry_price=close,
            stop_loss=round(close - _K_ATR * A, 2),
            take_profit=round(close + _FAR_ATR * A, 2),  # broker backstop only
            reason="S96_H1_MOMO_LONG",
            max_hold_min=_MAX_HOLD_MIN,
            trailing=True,
        )
    if close < donch_lo and down:
        return Signal(
            side="SELL",
            entry_price=close,
            stop_loss=round(close + _K_ATR * A, 2),
            take_profit=round(close - _FAR_ATR * A, 2),  # broker backstop only
            reason="S96_H1_MOMO_SHORT",
            max_hold_min=_MAX_HOLD_MIN,
            trailing=True,
        )
    return None
