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

opt15 Task 9 -- SOFT M15 structure veto + 1.5xATR gap cap (validated 2026-07-23)
-------------------------------------------------------------------------------
Two DEFAULT-ON, env-escapable filters, both faithful to the validation harness
`ClaudeTradingRD/m3_scalper/s93_struct_validate.py` (RD fvg_ict swing method):

  1. SOFT M15 swing-structure veto -- compute M15 swing structure from w15m
     (swing = extreme over +/-_STRUCT_LOOKBACK bars, confirmed _STRUCT_LOOKBACK
     bars later; last two CONFIRMED swing highs AND lows within a
     _STRUCT_WINDOW-bar window -> HH&HL=+1, LH&LL=-1, else 0; mapped to the M5
     decision bar by closed-bar time). Veto an FVG entry ONLY when structure
     OPPOSES the side (struct == -side): -1 vetoes bull FVGs, +1 vetoes bear
     FVGs, 0 (ranging / insufficient depth) never vetoes. This is the harness's
     struct_mode='soft' branch verbatim.
  2. Gap cap -- reject FVGs wider than S93_GAP_CAP_ATR x ATR (the 2026-07 52pt
     news gap was ~17x ATR). Harness parity: max_gap_atr branch.

Validation evidence (2026-07-23, s93_struct_validate.py, f0.3 tp1.5R w12 KZ):
  test n=388 PF 1.31 vs base 1.26; 0.80pt stress PF 1.20 vs 1.16; flat
  parameter plateau (SOFT beat STRICT and H4-bias arms). Fewer, cleaner trades.

Env escapes (both default to the validated shipping config):
  S93_SOFT_VETO   = on|off      (default on)
  S93_GAP_CAP_ATR = float       (default 1.5; 0 or negative disables the cap)

w15m depth: the veto needs >= _MIN_M15 (_STRUCT_WINDOW + _STRUCT_LOOKBACK) M15
bars; below that it fails OPEN (struct 0, no veto). MIN_BARS_15M exports that
floor so research_runner asserts RESEARCH_WIN_15M >= it at startup (Task 7).
The s93 compose service runs WIN_15M=100 (default) >= 63, DAYS_15M=5 -- ample.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from backtest_strategies._shared_ta import (
    PendingRetrace,
    atr_last,
    check_touch,
    ensure_utc_ts,
    fvg_at,
)
from backtest_strategies.base import Signal, StrategyConfig

NAME = "KRONOS_S93_FVG_SCALP"
CONFIG = StrategyConfig(
    name=NAME,
    description="FVG continuation scalp (M5): displacement FVG >=0.3xATR in "
                "killzones {7-9,12-14} UTC, retrace entry at the proximal "
                "edge, SL beyond distal edge, TP 1.5R, 120-min backstop. "
                "Validated 2026-07-06 (train PF 1.30 / test PF 1.24; "
                "survives 0.80pt stress). SOFT M15 veto + 1.5xATR gap cap "
                "2026-07-23 (test PF 1.31 vs 1.26).",
    cooldown_s=300,            # one M5 bar; pending-state machine is stricter
    session_start_hour=None,   # killzone hours gated inside get_signal
    session_end_hour=None,
    max_concurrent_positions=1,
)

# -- Knobs (validated 2026-07-06) ---------------------------------------------
_MIN_FVG_ATR = 0.3
_RETRACE_W   = 12         # bars the FVG stays tradeable (60 min)
_TP_R        = 1.5
_BUF_ATR     = 0.2
_ATR_N       = 14
_HOURS       = (7, 8, 9, 12, 13, 14)   # London + NY killzones
_MAX_HOLD_MIN = 120
_MIN_M5      = _ATR_N + 4

# -- SOFT M15 structure veto (opt15 Task 9, validated 2026-07-23) -------------
# Harness constants (s93_struct_validate.m15_structure_on_m5): swing lookback 3,
# structure window 60. Cross-checked byte-for-byte before coding -- see the
# module docstring. _MIN_M15 = window + confirm tail is the smallest w15m the
# veto can compute over; below it the veto fails OPEN (struct 0).
_STRUCT_LOOKBACK = 3
_STRUCT_WINDOW   = 60
_MIN_M15         = _STRUCT_WINDOW + _STRUCT_LOOKBACK   # 63

# Default shipping config for the two new filters (both env-escapable).
_GAP_CAP_ATR_DEFAULT = 1.5

# Runner MIN_BARS contract (opt15 Task 7): the smallest M5 window get_signal
# tolerates. research_runner asserts RESEARCH_WIN_5M >= this at startup so an
# undersized window fails LOUD instead of silently no-trading (CHALLENGE_XAU
# defect class). Derived from _MIN_M5 -- never a second hardcoded literal.
MIN_BARS_5M = _MIN_M5
# The SOFT veto needs this many M15 bars to compute the validated structure;
# research_runner asserts RESEARCH_WIN_5M/15M against these at startup.
MIN_BARS_15M = _MIN_M15

# -- Pending-setup state (persists across runner ticks in-process) ------------
_pending: PendingRetrace | None = None
_last_fvg_bar = None      # dedup: one setup per FVG bar


def reset_state() -> None:
    """Clear the armed setup + dedup memory (used by tests)."""
    global _pending, _last_fvg_bar
    _pending = None
    _last_fvg_bar = None


def _veto_enabled() -> bool:
    """SOFT M15 structure veto on/off (env S93_SOFT_VETO, default ON).

    Read at call time so tests can toggle it via monkeypatch.setenv without a
    module reload, and so an operator can disable it on the live box."""
    return os.getenv("S93_SOFT_VETO", "on").strip().lower() not in (
        "off", "0", "false", "no")


def _gap_cap_atr() -> float:
    """FVG gap cap as a multiple of ATR (env S93_GAP_CAP_ATR, default 1.5).

    Returns 0.0 (cap disabled) for a value of 0 or negative. An unparseable
    value falls back to _GAP_CAP_ATR_DEFAULT (1.5, cap enabled) -- it does
    NOT disable the cap, so a typo'd env value fails safe rather than open."""
    try:
        v = float(os.getenv("S93_GAP_CAP_ATR", str(_GAP_CAP_ATR_DEFAULT)))
    except (TypeError, ValueError):
        return _GAP_CAP_ATR_DEFAULT
    return v if v > 0 else 0.0


def _m15_struct_series(m15: pd.DataFrame) -> np.ndarray:
    """Per-M15-bar +1/-1/0 swing structure, causal -- byte-faithful port of the
    validation harness `m15_structure_on_m5` (RD fvg_ict swing method).

    swing high/low = extreme over +/-_STRUCT_LOOKBACK bars, confirmed
    _STRUCT_LOOKBACK bars later; structure at bar t = the last two CONFIRMED
    swing highs AND lows within the trailing _STRUCT_WINDOW bars: HH&HL -> +1,
    LH&LL -> -1, else 0. The ptr-based accumulation and the hi_idx[-4:] window
    filter mirror the harness exactly (do not "simplify" -- fidelity is the
    validated behavior).
    """
    h = m15["high"].to_numpy(float)
    l = m15["low"].to_numpy(float)
    n = len(m15)
    lb = _STRUCT_LOOKBACK
    win = _STRUCT_WINDOW
    sw_hi = np.zeros(n, bool)
    sw_lo = np.zeros(n, bool)
    for i in range(lb, n - lb):
        wh = h[i - lb:i + lb + 1]
        wl = l[i - lb:i + lb + 1]
        if h[i] == wh.max():
            sw_hi[i] = True
        if l[i] == wl.min():
            sw_lo[i] = True

    struct = np.zeros(n, int)
    hi_idx: list[int] = []
    lo_idx: list[int] = []
    ptr_h = ptr_l = 0
    for t in range(n):
        conf = t - lb                    # newest swing index confirmed by bar t
        while ptr_h <= conf:
            if conf >= 0 and ptr_h >= 0 and ptr_h <= conf and sw_hi[ptr_h]:
                hi_idx.append(ptr_h)
            ptr_h += 1
        while ptr_l <= conf:
            if conf >= 0 and ptr_l >= 0 and ptr_l <= conf and sw_lo[ptr_l]:
                lo_idx.append(ptr_l)
            ptr_l += 1
        hs = [i for i in hi_idx[-4:] if i >= t - win]
        ls = [i for i in lo_idx[-4:] if i >= t - win]
        if len(hs) >= 2 and len(ls) >= 2:
            if h[hs[-1]] > h[hs[-2]] and l[ls[-1]] > l[ls[-2]]:
                struct[t] = 1
            elif h[hs[-1]] < h[hs[-2]] and l[ls[-1]] < l[ls[-2]]:
                struct[t] = -1
    return struct


def _m15_structure_at(m15, m5_time) -> int:
    """M15 swing-structure (+1/-1/0) for the M5 decision bar.

    Mapped by closed-bar time exactly as the harness maps struct to m5: the last
    M15 bar CLOSED at/before the M5 bar OPENS (searchsorted 'right' - 1).
    Fail-open (0 -> no veto) when w15m is absent or shorter than _MIN_M15.
    """
    if m15 is None or len(m15) < _MIN_M15:
        return 0
    struct = _m15_struct_series(m15)
    # UTC epoch-ns on both sides so tz-aware / tz-naive frames compare cleanly.
    close_ns = ((pd.to_datetime(m15["time"], utc=True)
                 + pd.Timedelta(minutes=15)).astype("int64").to_numpy())
    m5_ns = pd.to_datetime(m5_time, utc=True).value
    pos = int(np.searchsorted(close_ns, m5_ns, side="right")) - 1
    if pos < 0:
        return 0
    return int(struct[pos])


def _detect_fvg(w5m: pd.DataFrame, w15m, now_utc: datetime) -> None:
    """Arm a pending setup when the LAST closed M5 bar leaves a large-enough
    FVG. One setup per bar; a newer FVG replaces an unfilled older one.

    opt15 Task 9: after detecting the FVG side, apply the 1.5xATR gap cap and
    the SOFT M15 structure veto (both default ON) -- a rejected FVG does NOT arm
    and does NOT stamp _last_fvg_bar, so a later tick re-evaluates the same bar
    (matching the harness, which simply skips bar k on a cap/veto hit)."""
    global _pending, _last_fvg_bar
    h = w5m["high"].to_numpy(float)
    l = w5m["low"].to_numpy(float)
    c = w5m["close"].to_numpy(float)
    k = len(c) - 1
    bar_time = w5m["time"].iloc[k]
    if bar_time == _last_fvg_bar:
        return
    a = atr_last(h, l, c, _ATR_N)
    if not (a > 0):
        return

    kind, gap, prox, dist = fvg_at(h, l, k)
    side = 0
    if kind == "bull" and gap >= _MIN_FVG_ATR * a:
        side = 1
        sl = round(dist - _BUF_ATR * a, 2)
    elif kind == "bear" and gap >= _MIN_FVG_ATR * a:
        side = -1
        sl = round(dist + _BUF_ATR * a, 2)
    if side == 0:
        return

    # Gap cap (harness: `if max_gap_atr and gap > max_gap_atr * a[k]: continue`).
    cap = _gap_cap_atr()
    if cap > 0 and gap > cap * a:
        return

    # SOFT M15 structure veto -- skip ONLY when structure OPPOSES the side
    # (harness struct_mode='soft': `if struct[k] == -side: continue`). Ranging
    # (0) and aligned structure both pass; the map uses the M5 bar's OWN time.
    if _veto_enabled() and _m15_structure_at(w15m, bar_time) == -side:
        return

    risk = abs(sl - prox)
    if risk <= 0:
        return
    _last_fvg_bar = bar_time
    # the retrace must come AFTER the displacement bar closes -- its own extreme
    # IS the proximal edge (optimizer parity: scan starts at k+1)
    _pending = PendingRetrace(
        side=side,
        prox=float(prox),
        sl=sl,
        tp=round(prox + side * _TP_R * risk, 2),
        armed_after=ensure_utc_ts(bar_time) + timedelta(minutes=5),
        expires_at=now_utc + timedelta(minutes=5 * _RETRACE_W),
        reason="S93_FVG_SCALP_LONG" if side > 0 else "S93_FVG_SCALP_SHORT",
        max_hold_min=_MAX_HOLD_MIN,
    )


def get_signal(w1m, w5m: pd.DataFrame, w15m, now_utc: datetime) -> Signal | None:
    global _pending
    if w5m is None or len(w5m) < _MIN_M5:
        return None
    if now_utc.hour not in _HOURS:
        _pending = None                # setups do not survive out of killzones
        return None
    if _pending is not None and now_utc >= _pending.expires_at:
        _pending = None

    _detect_fvg(w5m, w15m, now_utc)
    if _pending is None:
        return None

    # Probe the freshest CLOSED 1m bar (fill ~1 min after the touch); fall back
    # to the last closed M5 bar for 1m-less offline replays. A phantom-guard
    # cancel and a fill both clear the pending setup (check_touch clear flag).
    r = w1m.iloc[-1] if (w1m is not None and len(w1m) > 0) else w5m.iloc[-1]
    clear, sig = check_touch(_pending, r["time"], float(r["high"]),
                             float(r["low"]))
    if clear:
        _pending = None
    return sig
