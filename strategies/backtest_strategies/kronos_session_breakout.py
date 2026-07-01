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


def _closed_m5(w5m) -> pd.DataFrame:
    """UTC-normalise and sort. Do NOT drop the last bar: research_runner already
    passes only CLOSED bars (it applies iloc[:-1] to the OANDA feed, which itself
    excludes the still-forming bar). Dropping again would double-drop and evaluate
    a bar ~10 min stale, missing the breakout bar."""
    if w5m is None or len(w5m) == 0:
        return pd.DataFrame()
    df = w5m.copy()
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.sort_values("time").reset_index(drop=True)


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    bars = _closed_m5(w5m)
    n = len(bars)
    if n < _N_LONG + _SLOPE_LK + 2:
        return None
    i = n - 1
    last = bars["time"].iloc[i]
    sh = int(last.hour)
    if sh not in SESSION_HOURS:                      # spec §8.1 current-bar-hour gate
        return None
    if int(last.minute) < _OR_MIN:                   # OR must be complete
        return None
    day = last.date()
    key = (day, sh)
    if key in _fired_sessions:                       # one entry per (date, session hour)
        return None
    orr = opening_range(bars, day, sh, _OR_MIN)
    if orr is None:
        return None
    rng_hi, rng_lo, or_last = orr
    if i - or_last > _HOLD_BARS:                      # past the 3h hold window
        return None
    rng = rng_hi - rng_lo
    b = bias_series(bars["close"])[i]
    hi = float(bars["high"].iloc[i]); lo = float(bars["low"].iloc[i])
    if hi >= rng_hi and b == 1:
        _fired_sessions.add(key)
        return Signal(side="BUY", entry_price=rng_hi, stop_loss=rng_lo,
                      take_profit=round(rng_hi + _TP_MULT * rng, 2),
                      reason="SESSION_BREAKOUT_LONG", max_hold_min=_MAX_HOLD_MIN)
    if lo <= rng_lo and b == -1:
        _fired_sessions.add(key)
        return Signal(side="SELL", entry_price=rng_lo, stop_loss=rng_hi,
                      take_profit=round(rng_lo - _TP_MULT * rng, 2),
                      reason="SESSION_BREAKOUT_SHORT", max_hold_min=_MAX_HOLD_MIN)
    return None


def position_size(equity, or_width_points, *, risk_pct=0.008, risk_floor=40.0,
                  min_lot=0.01, max_lot=0.50, lot_step=0.01):
    """Lots so a full-OR-width stop risks ~max(risk_floor, risk_pct*equity). NOTE:
    the Kronos engine uses a FIXED lot (Strategy.entry_quantity); this is provided for
    parity with the spec and offline sizing checks, not wired into live sizing."""
    risk_dollars = max(risk_floor, risk_pct * equity)
    if or_width_points <= 0:
        return 0.0, 0.0
    raw = risk_dollars / (or_width_points * (USD_PER_POINT_PER_0_1_LOT / 0.1))
    lot = max(min_lot, min(max_lot, round(raw / lot_step) * lot_step))
    actual = or_width_points * (USD_PER_POINT_PER_0_1_LOT / 0.1) * lot
    return round(lot, 2), round(actual, 2)
