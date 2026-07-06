"""
Kronos S99 -- ICT Market-Structure-Shift + FVG reversal (M5)
------------------------------------------------------------
REVERSAL-category child strategy (user request 2026-07-06): the first new
family to pass held-out validation since the manager redesign.

Setup (zero discretion), bearish case (bullish is the mirror):
  sweep : within the prior _SWEEP_N M5 bars price traded ABOVE the last
          CONFIRMED fractal swing high (buy-side liquidity taken)
  MSS   : the last closed M5 bar CLOSES below the last confirmed swing low
          (market structure shift / displacement)
  FVG   : the displacement leaves a bearish fair value gap
          (high[k] < low[k-2])
  entry : the first retrace INTO the gap within _RETRACE_W bars, filled at
          the proximal edge; detected on the newest CLOSED 1m bar so the
          fill lands ~1 min after the touch (M5 fallback for 1m-less replays)
  stop  : beyond the distal FVG edge + _BUF_ATR x ATR(14, M5)
  tp    : _TP_R x risk          time: 480-min backstop

Validation 2026-07-06 (backtest/optimize_manager_strategies.py --strategy mss;
18mo M5, cost 0.45pt, train 2025 / test 2026H1):
  train n=1934 WR 52.6% PF 1.29 (+$1586 @0.02) | test n=983 WR 50.2% PF 1.19
  (+$1310) | still positive on BOTH halves at 0.80pt stress | ~7.6 trades/day
  | broad plateau across sweep_n 24-96, retrace 12-24, tp 1.5-2.0R.
NOTE: this is the reversal slot -- ~50% WR with 1.5R winners by design, NOT a
70-80% WR strategy like the trend slots.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig

NAME = "KRONOS_S99_MSS_FVG"
CONFIG = StrategyConfig(
    name=NAME,
    description="ICT MSS+FVG reversal (M5): liquidity sweep -> structure "
                "shift -> FVG retrace entry, SL beyond distal edge, TP 1.5R, "
                "hours 1-15 UTC. Validated 2026-07-06 (train PF 1.29 / test "
                "PF 1.19, ~7.6 trades/day).",
    cooldown_s=300,            # one M5 bar; pending-state machine is stricter
    session_start_hour=None,   # hours gated inside get_signal (1..15 UTC)
    session_end_hour=None,
    max_concurrent_positions=1,
)

# ── Knobs (validated 2026-07-06) ──────────────────────────────────────────────
_SWEEP_N    = 48          # rolling extreme lookback (liquidity memory)
_RETRACE_W  = 24          # bars the FVG stays tradeable after the MSS
_TP_R       = 1.5
_BUF_ATR    = 0.2
_ATR_N      = 14
_SWING_W    = 2           # fractal half-width
_HOURS      = tuple(range(1, 16))     # 01:00..15:59 UTC
_MAX_HOLD_MIN = 480
_MIN_M5     = _SWEEP_N + 3 * _SWING_W + 4

# ── Pending-setup state (persists across runner ticks in-process) ─────────────
# {side, prox, sl, tp, expires_at, mss_bar_time}
_pending: dict | None = None
_last_mss_bar = None      # dedup: one setup per MSS bar


def reset_state() -> None:
    """Clear the armed setup + dedup memory (used by tests)."""
    global _pending, _last_mss_bar
    _pending = None
    _last_mss_bar = None


def _atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int) -> float:
    prev_c = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
    return float(pd.Series(tr).rolling(n).mean().iloc[-1])


def _last_confirmed_swings(h: np.ndarray, l: np.ndarray,
                           w: int = _SWING_W) -> tuple[float, float]:
    """Latest CONFIRMED fractal swing high/low as of the final bar (a swing at
    i confirms at i+w -> only i <= n-1-w qualify; no look-ahead)."""
    n = len(h)
    hi = lo = float("nan")
    for i in range(w, n - w):
        if h[i] == h[i - w:i + w + 1].max() and (h[i] > h[i - w:i]).all():
            hi = float(h[i])
        if l[i] == l[i - w:i + w + 1].min() and (l[i] < l[i - w:i]).all():
            lo = float(l[i])
    return hi, lo


def _detect_mss(w5m: pd.DataFrame, now_utc: datetime) -> None:
    """Arm a pending FVG setup when the LAST closed M5 bar is an MSS with an
    FVG. One setup per MSS bar; a newer MSS replaces an unfilled older one."""
    global _pending, _last_mss_bar
    h = w5m["high"].to_numpy(float)
    l = w5m["low"].to_numpy(float)
    c = w5m["close"].to_numpy(float)
    k = len(c) - 1
    bar_time = w5m["time"].iloc[k]
    if bar_time == _last_mss_bar:
        return
    swing_hi, swing_lo = _last_confirmed_swings(h[:-1], l[:-1])
    if np.isnan(swing_hi) or np.isnan(swing_lo):
        return
    a = _atr(h, l, c, _ATR_N)
    if not (a > 0):
        return
    roll_hi = h[max(0, k - _SWEEP_N):k].max()
    roll_lo = l[max(0, k - _SWEEP_N):k].min()

    side = 0
    if roll_hi > swing_hi and c[k] < swing_lo and h[k] < l[k - 2]:
        side = -1
        prox, dist = h[k], l[k - 2]
        sl = round(dist + _BUF_ATR * a, 2)
    elif roll_lo < swing_lo and c[k] > swing_hi and l[k] > h[k - 2]:
        side = 1
        prox, dist = l[k], h[k - 2]
        sl = round(dist - _BUF_ATR * a, 2)
    if side == 0:
        return
    risk = abs(sl - prox)
    if risk <= 0:
        return
    _last_mss_bar = bar_time
    armed_after = pd.Timestamp(bar_time)
    if armed_after.tzinfo is None:
        armed_after = armed_after.tz_localize("UTC")
    _pending = {
        "side": side,
        "prox": float(prox),
        "sl": sl,
        "tp": round(prox + side * _TP_R * risk, 2),
        # the retrace must come AFTER the displacement bar closes — the MSS
        # bar's own extreme IS the proximal edge, so probing it would fire
        # instantly (optimizer parity: scan starts at k+1)
        "armed_after": armed_after + timedelta(minutes=5),
        "expires_at": now_utc + timedelta(minutes=5 * _RETRACE_W),
    }


def _touch(probe_time, probe_hi: float, probe_lo: float) -> Signal | None:
    """Fire when a post-MSS probe bar retraces into the armed FVG; cancel
    instead when that bar has already blown through the stop (phantom guard)."""
    global _pending
    p = _pending
    if p is None:
        return None
    t = pd.Timestamp(probe_time)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    if t < p["armed_after"]:
        return None
    if p["side"] < 0:
        if probe_hi >= p["sl"]:
            _pending = None            # retrace ran straight through the stop
            return None
        if probe_hi >= p["prox"]:
            _pending = None
            return Signal(side="SELL", entry_price=p["prox"],
                          stop_loss=p["sl"], take_profit=p["tp"],
                          reason="S99_MSS_FVG_SHORT",
                          max_hold_min=_MAX_HOLD_MIN)
    else:
        if probe_lo <= p["sl"]:
            _pending = None
            return None
        if probe_lo <= p["prox"]:
            _pending = None
            return Signal(side="BUY", entry_price=p["prox"],
                          stop_loss=p["sl"], take_profit=p["tp"],
                          reason="S99_MSS_FVG_LONG",
                          max_hold_min=_MAX_HOLD_MIN)
    return None


def get_signal(w1m, w5m: pd.DataFrame, w15m, now_utc: datetime) -> Signal | None:
    global _pending
    if w5m is None or len(w5m) < _MIN_M5:
        return None
    if now_utc.hour not in _HOURS:
        _pending = None                # setups do not survive out of hours
        return None
    if _pending is not None and now_utc >= _pending["expires_at"]:
        _pending = None

    _detect_mss(w5m, now_utc)
    if _pending is None:
        return None

    # Probe the freshest CLOSED 1m bar first (fill ~1 min after the touch);
    # fall back to the last closed M5 bar for 1m-less offline replays.
    if w1m is not None and len(w1m) > 0:
        r = w1m.iloc[-1]
        return _touch(r["time"], float(r["high"]), float(r["low"]))
    r = w5m.iloc[-1]
    return _touch(r["time"], float(r["high"]), float(r["low"]))
