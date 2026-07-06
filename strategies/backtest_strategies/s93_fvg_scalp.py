"""
Kronos S93 -- FVG continuation scalp (M5, killzones)
----------------------------------------------------
SCALPING-category child strategy (2026-07-06): the first scalp to pass
held-out validation after five failed campaigns (S97 snap-fade, S98 z-score
MR, M5 z-fade sweep, M1 liquidity-sweep sweep, burst/squeeze/volume sweep).

Setup (zero discretion), bullish case (bearish is the mirror):
  event : a displacement M5 bar leaves a bullish FVG (low[k] > high[k-2])
          at least _MIN_FVG_ATR x ATR(14, M5) wide, during a killzone hour
          {7,8,9,12,13,14} UTC. No bias filter -- both directions.
  entry : the first retrace DOWN to the proximal edge (low[k]) within
          _RETRACE_W bars, detected on the newest CLOSED 1m bar; a probe
          that has already pierced the stop cancels the setup instead.
  stop  : beyond the distal edge (high[k-2]) - _BUF_ATR x ATR.
  tp    : _TP_R x risk.          time: 120-min backstop.

Validation 2026-07-06 (optimize_manager_strategies --strategy scalp2; 18mo
M5, train 2025 / test 2026H1):
  @0.45pt: train n=981 WR 51.8% PF 1.30 (+$1052 @0.02) | test n=487 WR 48.5%
  PF 1.24 (+$938) | ~3.9 trades/day, holds <= 2h
  @0.80pt stress: train PF 1.09 (+$365) / test PF 1.15 (+$597) -- POSITIVE
  on both halves at double cost; w24 plateau neighbor also survives.
NOTE ~50% WR with 1.5R winners by design -- scalping category, short holds.
Correlation note: S99 (reversal) also enters on FVG retraces; the engine's
no-add-to-loser guard and the manager's max-concurrent cap bound the overlap.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig

NAME = "KRONOS_S93_FVG_SCALP"
CONFIG = StrategyConfig(
    name=NAME,
    description="FVG continuation scalp (M5): displacement FVG >=0.3xATR in "
                "killzones {7-9,12-14} UTC, retrace entry at the proximal "
                "edge, SL beyond distal edge, TP 1.5R, 120-min backstop. "
                "Validated 2026-07-06 (train PF 1.30 / test PF 1.24; "
                "survives 0.80pt stress).",
    cooldown_s=300,            # one M5 bar; pending-state machine is stricter
    session_start_hour=None,   # killzone hours gated inside get_signal
    session_end_hour=None,
    max_concurrent_positions=1,
)

# ── Knobs (validated 2026-07-06) ──────────────────────────────────────────────
_MIN_FVG_ATR = 0.3
_RETRACE_W   = 12         # bars the FVG stays tradeable (60 min)
_TP_R        = 1.5
_BUF_ATR     = 0.2
_ATR_N       = 14
_HOURS       = (7, 8, 9, 12, 13, 14)   # London + NY killzones
_MAX_HOLD_MIN = 120
_MIN_M5      = _ATR_N + 4

# ── Pending-setup state (persists across runner ticks in-process) ─────────────
_pending: dict | None = None
_last_fvg_bar = None      # dedup: one setup per FVG bar


def reset_state() -> None:
    """Clear the armed setup + dedup memory (used by tests)."""
    global _pending, _last_fvg_bar
    _pending = None
    _last_fvg_bar = None


def _atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int) -> float:
    prev_c = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
    return float(pd.Series(tr).rolling(n).mean().iloc[-1])


def _detect_fvg(w5m: pd.DataFrame, now_utc: datetime) -> None:
    """Arm a pending setup when the LAST closed M5 bar leaves a large-enough
    FVG. One setup per bar; a newer FVG replaces an unfilled older one."""
    global _pending, _last_fvg_bar
    h = w5m["high"].to_numpy(float)
    l = w5m["low"].to_numpy(float)
    c = w5m["close"].to_numpy(float)
    k = len(c) - 1
    bar_time = w5m["time"].iloc[k]
    if bar_time == _last_fvg_bar:
        return
    a = _atr(h, l, c, _ATR_N)
    if not (a > 0):
        return

    side = 0
    if l[k] > h[k - 2] and (l[k] - h[k - 2]) >= _MIN_FVG_ATR * a:
        side = 1
        prox, dist = l[k], h[k - 2]
        sl = round(dist - _BUF_ATR * a, 2)
    elif h[k] < l[k - 2] and (l[k - 2] - h[k]) >= _MIN_FVG_ATR * a:
        side = -1
        prox, dist = h[k], l[k - 2]
        sl = round(dist + _BUF_ATR * a, 2)
    if side == 0:
        return
    risk = abs(sl - prox)
    if risk <= 0:
        return
    _last_fvg_bar = bar_time
    armed_after = pd.Timestamp(bar_time)
    if armed_after.tzinfo is None:
        armed_after = armed_after.tz_localize("UTC")
    _pending = {
        "side": side,
        "prox": float(prox),
        "sl": sl,
        "tp": round(prox + side * _TP_R * risk, 2),
        # the retrace must come AFTER the displacement bar closes — its own
        # extreme IS the proximal edge (optimizer parity: scan starts at k+1)
        "armed_after": armed_after + timedelta(minutes=5),
        "expires_at": now_utc + timedelta(minutes=5 * _RETRACE_W),
    }


def _touch(probe_time, probe_hi: float, probe_lo: float) -> Signal | None:
    """Fire when a post-FVG probe bar retraces to the proximal edge; cancel
    when that bar has already pierced the stop (phantom guard)."""
    global _pending
    p = _pending
    if p is None:
        return None
    t = pd.Timestamp(probe_time)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    if t < p["armed_after"]:
        return None
    if p["side"] > 0:
        if probe_lo <= p["sl"]:
            _pending = None
            return None
        if probe_lo <= p["prox"]:
            _pending = None
            return Signal(side="BUY", entry_price=p["prox"],
                          stop_loss=p["sl"], take_profit=p["tp"],
                          reason="S93_FVG_SCALP_LONG",
                          max_hold_min=_MAX_HOLD_MIN)
    else:
        if probe_hi >= p["sl"]:
            _pending = None
            return None
        if probe_hi >= p["prox"]:
            _pending = None
            return Signal(side="SELL", entry_price=p["prox"],
                          stop_loss=p["sl"], take_profit=p["tp"],
                          reason="S93_FVG_SCALP_SHORT",
                          max_hold_min=_MAX_HOLD_MIN)
    return None


def get_signal(w1m, w5m: pd.DataFrame, w15m, now_utc: datetime) -> Signal | None:
    global _pending
    if w5m is None or len(w5m) < _MIN_M5:
        return None
    if now_utc.hour not in _HOURS:
        _pending = None                # setups do not survive out of killzones
        return None
    if _pending is not None and now_utc >= _pending["expires_at"]:
        _pending = None

    _detect_fvg(w5m, now_utc)
    if _pending is None:
        return None

    if w1m is not None and len(w1m) > 0:
        r = w1m.iloc[-1]
        return _touch(r["time"], float(r["high"]), float(r["low"]))
    r = w5m.iloc[-1]
    return _touch(r["time"], float(r["high"]), float(r["low"]))
