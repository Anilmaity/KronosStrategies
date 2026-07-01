# strategies/backtest_strategies/kronos_session_breakout.py
"""
Kronos SESSION_BREAKOUT — M5 bias-filtered opening-range breakout (XAUUSD)
--------------------------------------------------------------------------
Port of strat_orb_biased (research: s5_intraday_research2.py). See
SESSION_BREAKOUT_STRATEGY_SPEC.md for the canonical spec. TAKER-compatible.

Design (zero discretion), evaluated on the last CLOSED M5 bar:
  bias  : EMA(240) level + 48-bar slope -> +1 up / -1 down / 0 flat
  window: session-open hours [1,7,12,13,14] UTC, first 30 min = opening range
  entry : break of OR boundary in the bias direction (long@hi / short@lo)
  stop  : opposite OR side (static)   tp: entry +/- 1.5 * OR width (static)
  exit  : static broker SL/TP + 3h (36-bar) max-hold time-close
"""
from __future__ import annotations

import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig
from backtest_strategies._kronos_indicators import ema

NAME = "SESSION_BREAKOUT"
CONFIG = StrategyConfig(
    name=NAME,
    description="M5 opening-range breakout, EMA240 bias, sessions [1,7,12,13,14] UTC, "
                "static OR-width stop + 1.5x-OR target. Port of strat_orb_biased.",
    cooldown_s=1800,               # >= OR length; per-session guard does the fine-grained work
    session_start_hour=None,       # FIVE discrete hours -> gate inside get_signal
    session_end_hour=None,
    max_concurrent_positions=1,
)

SESSION_HOURS = (1, 7, 12, 13, 14)
_OR_MIN, _TP_MULT, _HOLD_BARS, _N_LONG, _SLOPE_LK = 30, 1.5, 36, 240, 48
_MAX_HOLD_MIN = 180.0                     # 36 M5 bars = 3h time-stop
USD_PER_POINT_PER_0_1_LOT = 10.0

# One-entry-per-session guard: (utc_date, session_hour) keys that already fired.
# Persists across research_runner ticks in the running process.
_fired_sessions: set = set()


def bias_series(closes: pd.Series, n_long: int = _N_LONG, slope_lk: int = _SLOPE_LK) -> list[int]:
    """Per-bar trend proxy: +1 (price>EMA & EMA rising), -1 (price<EMA & EMA falling),
    else 0. Undefined (0) until i >= n_long + slope_lk."""
    e = ema(closes, n_long)
    n = len(closes)
    out = [0] * n
    for i in range(n):
        if i < n_long + slope_lk:
            continue
        ci = float(closes.iloc[i]); ei = float(e.iloc[i]); ep = float(e.iloc[i - slope_lk])
        if ci > ei and ei > ep:
            out[i] = 1
        elif ci < ei and ei < ep:
            out[i] = -1
    return out


def opening_range(bars: pd.DataFrame, day, sh: int, or_min: int = _OR_MIN):
    """(rng_hi, rng_lo, last_or_index) for session-hour `sh` on `day`, or None when
    < 2 OR bars or a non-positive range. OR bars = same day, hour==sh, minute<or_min."""
    t = bars["time"]
    idx = [k for k in range(len(bars))
           if t.iloc[k].date() == day and t.iloc[k].hour == sh and t.iloc[k].minute < or_min]
    if len(idx) < 2:
        return None
    rng_hi = float(bars["high"].iloc[idx].max())
    rng_lo = float(bars["low"].iloc[idx].min())
    if rng_hi - rng_lo <= 0:
        return None
    return rng_hi, rng_lo, idx[-1]
