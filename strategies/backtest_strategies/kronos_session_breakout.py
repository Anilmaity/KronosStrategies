# strategies/backtest_strategies/kronos_session_breakout.py
"""
Kronos SESSION_BREAKOUT — M5 bias-filtered opening-range breakout (XAUUSD)
--------------------------------------------------------------------------
Port of strat_orb_biased (research: s5_intraday_research2.py). See
SESSION_BREAKOUT_STRATEGY_SPEC.md for the canonical spec. TAKER-compatible.

Design (zero discretion):
  bias  : EMA(240) level + 48-bar slope on M5 -> +1 up / -1 down / 0 flat
  window: session-open hours [1,7,12,13,14] UTC, first 30 min = opening range
  entry : break of OR boundary in the bias direction (long@hi / short@lo),
          detected on the newest CLOSED 1m bar (fires ~1 min after the touch,
          approximating the spec's stop-at-boundary fill); newest CLOSED M5
          bar kept as fallback probe for 1m-less offline replays
  stop  : entry -/+ 2.0 * OR width (static; wider than the opposite extreme)
  tp    : entry +/- 0.8 * OR width (static)
  exit  : static broker SL/TP + 3h (36-bar) max-hold time-close

Exit geometry re-optimized 2026-07-06 for the 70-80%-WR mandate
(backtest/optimize_manager_strategies.py, 18mo M5, cost 0.45pt, train 2025 /
test 2026H1): tp=0.8xOR sl=2.0xOR -> train WR 68.6% PF 1.20, test WR 72.3%
PF 1.57; still profitable both halves at 0.80pt stress. The original
tp=1.5xOR sl=1.0xOR (WR ~48%) lives in git history.
"""
from __future__ import annotations

import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig
from backtest_strategies._kronos_indicators import ema

NAME = "SESSION_BREAKOUT"
CONFIG = StrategyConfig(
    name=NAME,
    description="M5 opening-range breakout, EMA240 bias, sessions [1,7,12,13,14] UTC, "
                "static SL 2.0xOR / TP 0.8xOR (high-WR geometry, 2026-07-06). "
                "Port of strat_orb_biased.",
    cooldown_s=1800,               # >= OR length; per-session guard does the fine-grained work
    session_start_hour=None,       # FIVE discrete hours -> gate inside get_signal
    session_end_hour=None,
    max_concurrent_positions=1,
)

SESSION_HOURS = (1, 7, 12, 13, 14)
_OR_MIN, _TP_MULT, _SL_MULT, _HOLD_BARS, _N_LONG, _SLOPE_LK = 30, 0.8, 2.0, 36, 240, 48
_MAX_HOLD_MIN = 180.0                     # 36 M5 bars = 3h time-stop
USD_PER_POINT_PER_0_1_LOT = 10.0

# One-entry-per-session guard: (utc_date, session_hour) keys that already fired.
# Persists across research_runner ticks in the running process.
_fired_sessions: set = set()


def reset_state() -> None:
    """Clear the per-session dedup memory (used by tests and the s95 delegate)."""
    _fired_sessions.clear()


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


def _closed_bars(frame) -> pd.DataFrame:
    """UTC-normalise and sort. Do NOT drop rows: the OANDA feed itself excludes
    the still-forming candle (`complete` filter in tsdb_reader) and
    research_runner passes it through unchanged (its own extra drop was removed
    2026-07-06 — it was hiding the newest closed bar for a full bar interval)."""
    if frame is None or len(frame) == 0:
        return pd.DataFrame()
    df = frame.copy()
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.sort_values("time").reset_index(drop=True)


# Backwards-compatible alias (older tests/callers import _closed_m5).
_closed_m5 = _closed_bars


def _or_break_probe(bars_m5, probe_t, probe_hi, probe_lo):
    """Session/OR gates + boundary-touch test for ONE closed probe bar (either
    the newest 1m bar or the newest M5 bar). Returns (side, rng_hi, rng_lo, key)
    or None. Bias is deliberately NOT checked here so the caller only pays for
    the O(n) EMA when a break candidate actually exists, and so a bias-blocked
    break does not consume the session (matches the original semantics).

    The spec's 36-bar hold-window check is subsumed by the current-hour gate:
    probe and OR share the session hour, so the probe can never be more than
    6 M5 bars past the OR."""
    sh = int(probe_t.hour)
    if sh not in SESSION_HOURS:                      # spec §8.1 current-bar-hour gate
        return None
    if int(probe_t.minute) < _OR_MIN:                # OR must be complete
        return None
    day = probe_t.date()
    key = (day, sh)
    if key in _fired_sessions:                       # one entry per (date, session hour)
        return None
    orr = opening_range(bars_m5, day, sh, _OR_MIN)
    if orr is None:
        return None
    rng_hi, rng_lo, _ = orr
    if probe_hi >= rng_hi:
        return "BUY", rng_hi, rng_lo, key
    if probe_lo <= rng_lo:
        return "SELL", rng_hi, rng_lo, key
    return None


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    bars = _closed_bars(w5m)
    n = len(bars)
    if n < _N_LONG + _SLOPE_LK + 2:
        return None

    # Probe order matters: the newest CLOSED 1m bar first, so a boundary touch
    # fires ~1 min after it happens instead of waiting for the M5 break bar to
    # close (live fills were 5-15 min behind the touch — diagnosed 2026-07-06;
    # the backtest fills AT the boundary, spec §3.3/§8.3). The M5 probe stays as
    # a fallback for offline replays that pass no 1m frame, and re-catches a
    # touch the 1m probe missed (e.g. right after a runner restart).
    probes: list[tuple] = []
    m1 = _closed_bars(w1m)
    if len(m1):
        r1 = m1.iloc[-1]
        probes.append((m1["time"].iloc[-1], float(r1["high"]), float(r1["low"])))
    r5 = bars.iloc[-1]
    probes.append((bars["time"].iloc[-1], float(r5["high"]), float(r5["low"])))

    for probe_t, hi, lo in probes:
        cand = _or_break_probe(bars, probe_t, hi, lo)
        if cand is None:
            continue
        side, rng_hi, rng_lo, key = cand
        rng = rng_hi - rng_lo
        b = bias_series(bars["close"])[n - 1]
        if side == "BUY" and b == 1:
            _fired_sessions.add(key)
            return Signal(side="BUY", entry_price=rng_hi,
                          stop_loss=round(rng_hi - _SL_MULT * rng, 2),
                          take_profit=round(rng_hi + _TP_MULT * rng, 2),
                          reason="SESSION_BREAKOUT_LONG", max_hold_min=_MAX_HOLD_MIN)
        if side == "SELL" and b == -1:
            _fired_sessions.add(key)
            return Signal(side="SELL", entry_price=rng_lo,
                          stop_loss=round(rng_lo + _SL_MULT * rng, 2),
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
