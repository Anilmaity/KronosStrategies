"""
Kronos S97 -- M5 Snap-Fade Scalper with HTF-structure bias (PAPER slot)
-----------------------------------------------------------------------
Scalper child strategy for the Strategy Manager roster
(docs/superpowers/specs/2026-07-02-strategy-manager-design.md §4).

**PAPER-ONLY in v1** (doctrine: edge unproven at honest costs; the maker-fill
re-audit is pending). Deployed under research_runner with DRY_RUN=true --
full pipeline, DB rows, no broker orders.

Design (zero discretion), evaluated on the last CLOSED M5 bar:
  overshoot : the last 2 M5 bars' net move |close[t] - close[t-2]| exceeds
              1.2 x the rolling 60-bar std of 2-bar moves (std computed on
              moves strictly BEFORE the current one -- causal).
  direction : fade the overshoot back toward the mean (spike up -> SELL,
              spike down -> BUY), but ONLY when the fade agrees with the
              M15-derived HTF structure bias (ict_engine.get_htf_bias on
              4h/1h frames resampled from the 15m window). Neutral -> flat.
  exits     : TP = 0.5 x overshoot, hard SL = 0.9 x overshoot beyond entry,
              both floored at 1.0 pt. max_hold_min = 30.
  session   : 03:00-09:00 UTC only (Asia tail / pre-London) -- enforced by
              CONFIG *and* inside get_signal (defense in depth).

Every signal has a hard stop; no averaging, no martingale.

Manager gating policy (spec §4): 03:00-09:00 UTC, vol_regime in {LOW, NORMAL},
d1_bias != neutral -- the manager's job, on top of the gates in here.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig
from strategy.ict_engine import get_htf_bias

NAME = "KRONOS_S97_SNAP_SCALPER"
CONFIG = StrategyConfig(
    name=NAME,
    description="M5 snap-fade: fade a 2-bar overshoot (>1.2x rolling 60-bar "
                "std) toward the mean, only with the M15-derived HTF structure "
                "bias. TP 0.5x / hard SL 0.9x overshoot (floor 1.0 pt), "
                "30-min time exit. 03:00-09:00 UTC. PAPER-only slot.",
    cooldown_s=600,
    session_start_hour=3,
    session_end_hour=9,
    max_concurrent_positions=1,
)

# ── Knobs ─────────────────────────────────────────────────────────────────────
_STD_N        = 60     # rolling std window over 2-bar moves
_OV_MULT      = 1.2    # overshoot threshold in stds
_TP_FRAC      = 0.5    # TP distance as a fraction of the overshoot
_SL_FRAC      = 0.9    # SL distance as a fraction of the overshoot
_FLOOR_PTS    = 1.0    # minimum TP/SL distance
_MAX_HOLD_MIN = 30
_SESSION_START_H = 3   # UTC, inclusive
_SESSION_END_H   = 9   # UTC, exclusive

# HTF frames need enough closed bars for swing detection (lookback 3 needs
# >= 2 swing highs + 2 swing lows; ~9 bars minimum, more in practice).
_MIN_HTF_BARS = 12


def _resample(w15m: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample the 15m window to `rule` OHLC (UTC buckets), dropping the last
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
    out = (
        df.resample(rule, label="left", closed="left")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )
    return out.iloc[:-1] if len(out) else out


def _htf_bias(w15m: pd.DataFrame) -> str:
    """M15-derived HTF structure bias: ict_engine.get_htf_bias on 4h/1h frames
    resampled from the 15m window. Returns 'long' | 'short' | 'neutral'."""
    if w15m is None or len(w15m) < _MIN_HTF_BARS * 16:
        return "neutral"
    h4 = _resample(w15m, "4h")
    h1 = _resample(w15m, "1h")
    if len(h4) < _MIN_HTF_BARS or len(h1) < _MIN_HTF_BARS:
        return "neutral"
    return get_htf_bias(h4.reset_index(drop=True), h1.reset_index(drop=True))


def get_signal(w1m, w5m: pd.DataFrame, w15m, now_utc: datetime) -> Signal | None:
    # Session gate (defense in depth -- CONFIG hours gate the runner too).
    if not (_SESSION_START_H <= now_utc.hour < _SESSION_END_H):
        return None

    if w5m is None or len(w5m) < _STD_N + 4:
        return None

    c = w5m["close"].astype(float)
    d2 = c.diff(2)                               # 2-bar moves
    # Rolling std of the 2-bar moves strictly BEFORE the current one.
    sigma = d2.shift(1).rolling(_STD_N, min_periods=_STD_N).std().iloc[-1]
    if pd.isna(sigma) or not (float(sigma) > 0):
        return None

    ov = float(d2.iloc[-1])                      # signed overshoot
    if abs(ov) <= _OV_MULT * float(sigma):
        return None                              # no snap -- nothing to fade

    bias = _htf_bias(w15m)
    if bias == "neutral":
        return None

    entry = float(c.iloc[-1])
    tp_d = max(_TP_FRAC * abs(ov), _FLOOR_PTS)
    sl_d = max(_SL_FRAC * abs(ov), _FLOOR_PTS)

    if ov > 0:
        # Spike up -> fade SHORT, only if HTF structure is short.
        if bias != "short":
            return None
        return Signal(
            side="SELL",
            entry_price=entry,
            stop_loss=round(entry + sl_d, 2),    # hard stop, always
            take_profit=round(entry - tp_d, 2),
            reason="S97_SNAP_FADE_SHORT",
            max_hold_min=_MAX_HOLD_MIN,
        )
    # Spike down -> fade LONG, only if HTF structure is long.
    if bias != "long":
        return None
    return Signal(
        side="BUY",
        entry_price=entry,
        stop_loss=round(entry - sl_d, 2),        # hard stop, always
        take_profit=round(entry + tp_d, 2),
        reason="S97_SNAP_FADE_LONG",
        max_hold_min=_MAX_HOLD_MIN,
    )
