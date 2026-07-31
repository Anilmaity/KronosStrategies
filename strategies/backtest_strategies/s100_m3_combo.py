"""
Kronos S100 -- M3 combo scalper (spec v3, user campaign 2026-07-23)
-------------------------------------------------------------------
Three entry models on M3 bars (resampled live from the M1 feed), all gated
by EMA20/200 direction and session hours, one position at a time:

  FVG  : displacement gap (0.3..1.5 x ATR14) in EMA direction; retrace
         entry at the proximal edge within 12 M3 bars (1m touch).
  OB   : close breaks the prior 3-bar extreme with body >= 0.5 x ATR in EMA
         direction; entry at the EDGE of the last opposite candle's zone
         (zone <= 1.5 x ATR), same retrace machine.
  RSI  : RSI(3) crossing INTO 85/15 in EMA direction (momentum, inverted
         from classic mean-reversion) -- immediate market signal,
         SL 1.0 x ATR.

  stops : zone far side -/+ 0.2 x ATR (FVG/OB), 1.0 x ATR (RSI).
  target: max(2.5R, 1.5pt).  hold: 72 min backstop.
  hours : 1-8 and 13-15 UTC (9-12 dead zone measured -26/-16pts, the
          13-14 NY window carries ~1/3 of annual profit).

Validated 2026-07-23 on 3y OANDA M1 (ClaudeTradingRD/m3_scalper, engine
s93_ob_validate.run_combo): 2026 PF 1.68 (+2,591pts flat) 90% pos weeks,
stress-PF 1.51 | 2025 PF 1.36 stress 1.09 | 2024 PF 1.05 | 2023H2 PF 0.74
(-273) -- the one losing period; regime-dependent, deploy paper-first.
TP ladder 1.0R->3.0R rises monotonically to a 2.0-3.0R plateau; 2.5R chosen.
The daily -15pt kill-switch of the spec maps to the Strategy Manager's
existing $150/day brake at 0.10 lots -- NOT re-implemented here.
"""
from __future__ import annotations

import logging
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

log = logging.getLogger(__name__)

NAME = "KRONOS_S100_M3_COMBO"
CONFIG = StrategyConfig(
    name=NAME,
    description="M3 combo scalper (spec v3): FVG retrace + OB edge retest + "
                "RSI3-momentum, EMA20/200 direction gate, hours 1-8/13-15 UTC, "
                "TP max(2.5R, 1.5pt), 72-min backstop. Validated 2026-07-23 "
                "(3y: 2026 PF 1.68 / 2025 1.36 / 2024 1.05 / 2023H2 0.74).",
    cooldown_s=180,            # one M3 bar
    session_start_hour=None,   # hours gated inside get_signal
    session_end_hour=None,
    max_concurrent_positions=1,
)

# ── Knobs (spec v3 -- see module docstring; do not tune without the 3y rerun) ─
_HOURS        = (1, 2, 3, 4, 5, 6, 7, 8, 13, 14, 15)
_ATR_N        = 14
_MIN_FVG_ATR  = 0.3
_MAX_GAP_ATR  = 1.5
_BUF_ATR      = 0.2
_TP_R         = 2.5
_TP_FLOOR     = 1.5
_OB_DISP      = 0.5
_OB_BREAK     = 3
_OB_LOOKBACK  = 10
_RSI_N        = 3
_RSI_OB       = 85.0
_RSI_OS       = 15.0
_RSI_SL_ATR   = 1.0
_EMA_FAST     = 20
_EMA_SLOW     = 200
_RETRACE_W    = 12          # M3 bars the pending setup stays tradeable (36 min)
_MAX_HOLD_MIN = 72          # 24 M3 bars, engine parity
_MIN_M3       = _EMA_SLOW + _ATR_N   # closed M3 bars needed

# ER trend-persistence gate constants (opt15 Task 15, DEFAULT OFF)
# ----------------------------------------------------------------
# Optional entry filter keyed on the Kaufman efficiency ratio (trend
# persistence). S100's one known weakness is regime dependence -- its spec's only
# losing period was 2023H2 (-273 pts, choppy/ranging tape); the gate lets an
# operator suppress new entries when the market is not trending. The ratio +
# thresholds are ported BYTE-FOR-BYTE from regime_engine (_efficiency_ratio /
# _classify_trend: 24 CLOSED H1 bars, 30 CLOSED M15 bars, both > 0.35 -> TRENDING,
# both < 0.20 -> RANGING, else MIXED). Parity against the regime engine on a
# shared fixture is proven in tests/test_s100_er_gate.py.
_ER_H1_BARS   = 24
_ER_M15_BARS  = 30
_ER_TRENDING  = 0.35
_ER_RANGING   = 0.20
_ER_MODES     = ("off", "ranging", "strict")


def _er_gate_mode() -> str:
    """Read S100_ER_GATE at call time (Task 9 pattern: the env is authoritative,
    read fresh each tick, fail toward OFF). Unknown/blank -> 'off'.

    Env S100_ER_GATE = off (default) | ranging | strict:
      off      no gate -- byte-identical to the pre-Task-15 module.
      ranging  block a NEW entry when trend_regime == RANGING.
      strict   block a NEW entry when trend_regime != TRENDING (RANGING or MIXED).
    """
    mode = os.getenv("S100_ER_GATE", "off").strip().lower()
    return mode if mode in _ER_MODES else "off"


# Runner MIN_BARS contract (opt15 Task 7 + Task 15): the requirement is on the
# w1m LENGTH (this module resamples M1 internally), matching get_signal's own
# guard `len(w1m) < 3 * _MIN_M3`. research_runner asserts RESEARCH_WIN_1M >=
# MIN_BARS_1M at startup so an undersized window fails LOUD instead of silently
# no-trading (CHALLENGE_XAU defect class).
#
# The BASE floor is the scalper's own get_signal guard (3 * _MIN_M3 = 642). When
# the ER gate is ENABLED (S100_ER_GATE != off) a valid 24-bar H1 efficiency ratio
# needs 25 CLOSED H1 bars, so the floor RISES to _ER_MIN_BARS_1M. Extending
# MIN_BARS_1M is precisely what makes the Task-7 runner assert REFUSE TO START an
# armed s100 service whose window is too small: the operator's one arm-time step
# is to widen RESEARCH_WIN_1M, and forgetting it fails LOUD, never silently. Read
# from the env at import -- the runner imports this module once at container start
# with the compose env already set and asserts against MIN_BARS_1M once. Both
# floors derive from the constants above -- never a second hardcoded literal.
_MIN_BARS_1M_BASE = 3 * _MIN_M3               # 642: EMA200 warm-up on ~214 M3 bars
_ER_MIN_BARS_1M   = (_ER_H1_BARS + 2) * 60    # 1560: 25 closed H1 bars for a
#                                               24-diff ratio + 1h headroom to
#                                               absorb the dropped incomplete tail


def _required_min_bars_1m(mode: str | None = None) -> int:
    """The w1m-length floor for the current gate mode: the base scalper floor when
    the ER gate is off, raised to cover a valid 24-bar H1 ratio when it is armed.
    Defaults to the live S100_ER_GATE env; pass ``mode`` to query a hypothetical."""
    mode = _er_gate_mode() if mode is None else mode
    if mode == "off":
        return _MIN_BARS_1M_BASE
    return max(_MIN_BARS_1M_BASE, _ER_MIN_BARS_1M)


# Frozen at import from S100_ER_GATE (see the contract above). Default off -> 642.
MIN_BARS_1M = _required_min_bars_1m()

# ── Pending-setup state (persists across runner ticks in-process) ─────────────
_pending: PendingRetrace | None = None
_last_bar = None            # dedup: one detection pass per closed M3 bar
_er_warned = False          # one-shot WARN: gate on but window too short for ER


def reset_state() -> None:
    """Clear the armed setup + dedup memory (used by tests)."""
    global _pending, _last_bar, _er_warned
    _pending = None
    _last_bar = None
    _er_warned = False


def _resample_m3(w1m: pd.DataFrame) -> pd.DataFrame:
    """M1 -> M3, CLOSED buckets only.

    The newest w1m row is the newest closed M1 bar; its M3 bucket is complete
    only when that bar is the bucket's last minute ((minute + 1) % 3 == 0).
    Incomplete tail buckets are dropped so detection never sees a forming bar.
    """
    df = (w1m.set_index("time")
          .resample("3min")
          .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
          .dropna())
    if len(df) == 0:
        return df.reset_index()
    last_m1 = w1m["time"].iloc[-1]
    last_bucket = df.index[-1]
    if (pd.Timestamp(last_m1) - pd.Timestamp(last_bucket)) < pd.Timedelta(minutes=2):
        df = df.iloc[:-1]
    return df.reset_index()


def _rsi(c: np.ndarray, n: int) -> np.ndarray:
    s = pd.Series(c)
    delta = s.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    ag = gain.ewm(alpha=1.0 / n, adjust=False).mean()
    al = loss.ewm(alpha=1.0 / n, adjust=False).mean().replace(0.0, 1e-12)
    return (100.0 - 100.0 / (1.0 + ag / al)).to_numpy()


def _tp(entry: float, sl: float, side: int) -> float:
    risk = abs(entry - sl)
    return round(entry + side * max(_TP_R * risk, _TP_FLOOR), 2)


def _detect(m3: pd.DataFrame, now_utc: datetime) -> Signal | None:
    """One detection pass on the newest closed M3 bar. May arm a pending
    retrace setup (FVG/OB) or return an immediate signal (RSI momentum)."""
    global _pending, _last_bar

    k = len(m3) - 1
    bar_time = m3["time"].iloc[k]
    if bar_time == _last_bar:
        return None
    _last_bar = bar_time

    h = m3["high"].to_numpy(float)
    l = m3["low"].to_numpy(float)
    c = m3["close"].to_numpy(float)
    o = m3["open"].to_numpy(float)
    a = atr_last(h, l, c, _ATR_N)
    if not (a > 0):
        return None

    close_s = pd.Series(c)
    ema_f = close_s.ewm(span=_EMA_FAST, adjust=False).mean().iloc[-1]
    ema_s = close_s.ewm(span=_EMA_SLOW, adjust=False).mean().iloc[-1]
    d = 1 if ema_f > ema_s else -1

    armed_after = ensure_utc_ts(bar_time)

    def _arm(kind: str, prox: float, sl: float) -> None:
        global _pending
        _pending = PendingRetrace(
            side=d,
            prox=round(float(prox), 2),
            sl=round(float(sl), 2),
            tp=_tp(float(prox), float(sl), d),
            armed_after=armed_after + timedelta(minutes=3),
            expires_at=now_utc + timedelta(minutes=3 * _RETRACE_W),
            reason=f"S100_{kind.upper()}_{'LONG' if d > 0 else 'SHORT'}",
            max_hold_min=_MAX_HOLD_MIN,
            kind=kind,
        )

    # 1) FVG in EMA direction (a newer FVG replaces an unfilled older setup)
    kind_fvg, gap, prox_fvg, dist_fvg = fvg_at(h, l, k)
    if d > 0 and kind_fvg == "bull":
        if _MIN_FVG_ATR * a <= gap <= _MAX_GAP_ATR * a:
            _arm("fvg", prox_fvg, dist_fvg - _BUF_ATR * a)
            return None
    elif d < 0 and kind_fvg == "bear":
        if _MIN_FVG_ATR * a <= gap <= _MAX_GAP_ATR * a:
            _arm("fvg", prox_fvg, dist_fvg + _BUF_ATR * a)
            return None

    # 2) OB: 3-bar break with displacement body, entry at zone EDGE
    body = c[k] - o[k]
    if d > 0 and c[k] > h[k - _OB_BREAK:k].max() and body >= _OB_DISP * a:
        for j in range(k - 1, max(k - 1 - _OB_LOOKBACK, -1), -1):
            if c[j] < o[j]:
                zone_hi, zone_lo = o[j], c[j]
                if (zone_hi - zone_lo) <= _MAX_GAP_ATR * a:
                    _arm("ob", zone_hi, zone_lo - _BUF_ATR * a)
                break
        if _pending is not None and _pending["kind"] == "ob":
            return None
    elif d < 0 and c[k] < l[k - _OB_BREAK:k].min() and -body >= _OB_DISP * a:
        for j in range(k - 1, max(k - 1 - _OB_LOOKBACK, -1), -1):
            if c[j] > o[j]:
                zone_lo, zone_hi = o[j], c[j]
                if (zone_hi - zone_lo) <= _MAX_GAP_ATR * a:
                    _arm("ob", zone_lo, zone_hi + _BUF_ATR * a)
                break
        if _pending is not None and _pending["kind"] == "ob":
            return None

    # 3) RSI(3) momentum cross INTO the extreme, immediate market signal
    rsi = _rsi(c, _RSI_N)
    if k >= _RSI_N + 1:
        if d > 0 and rsi[k - 1] < _RSI_OB <= rsi[k]:
            entry = float(c[k])
            sl = round(entry - _RSI_SL_ATR * a, 2)
            return Signal(side="BUY", entry_price=round(entry, 2),
                          stop_loss=sl, take_profit=_tp(entry, sl, 1),
                          reason="S100_RSI3_MOMO_LONG",
                          max_hold_min=_MAX_HOLD_MIN)
        if d < 0 and rsi[k - 1] > _RSI_OS >= rsi[k]:
            entry = float(c[k])
            sl = round(entry + _RSI_SL_ATR * a, 2)
            return Signal(side="SELL", entry_price=round(entry, 2),
                          stop_loss=sl, take_profit=_tp(entry, sl, -1),
                          reason="S100_RSI3_MOMO_SHORT",
                          max_hold_min=_MAX_HOLD_MIN)
    return None


# ── ER trend-persistence gate compute (opt15 Task 15) ────────────────────────
# When armed, MIN_BARS_1M is extended so the runner guarantees a wide-enough w1m
# (see the MIN_BARS contract above); these helpers then classify the regime from
# that window. If w1m is nonetheless too short to form a valid 24-bar H1 ratio
# (a transient data hiccup, never normal operation once armed), the gate FAILS
# TOWARD OFF -- admitting the entry -- because a data gap must never BLOCK trading
# (Global Constraint 1). The gate ships DEFAULT OFF; arming is an operator
# decision informed by docs/research/2026-07-30-s100-er-gate.md.
def _efficiency_ratio(closes: pd.Series, n_bars: int) -> float:
    """Kaufman efficiency ratio |net|/path over the last ``n_bars`` moves.

    Ported BYTE-FOR-BYTE from regime_engine._efficiency_ratio (opt15 Task 15;
    parity asserted in tests/test_s100_er_gate.py). Uses the last n_bars+1
    closes (n_bars diffs). NaN when history is short or the path length is zero.
    """
    c = closes.astype(float).dropna().reset_index(drop=True)
    if len(c) < n_bars + 1:
        return float("nan")
    window = c.iloc[-(n_bars + 1):]
    net = abs(float(window.iloc[-1]) - float(window.iloc[0]))
    path = float(window.diff().abs().sum())
    if path <= 0:
        return float("nan")
    return net / path


def _classify_trend(er_h1: float, er_m15: float) -> str:
    """TRENDING/RANGING/MIXED from the two efficiency ratios. Ported BYTE-FOR-
    BYTE from regime_engine._classify_trend (opt15 Task 15)."""
    if pd.isna(er_h1) or pd.isna(er_m15):
        return "MIXED"
    if er_h1 > _ER_TRENDING and er_m15 > _ER_TRENDING:
        return "TRENDING"
    if er_h1 < _ER_RANGING and er_m15 < _ER_RANGING:
        return "RANGING"
    return "MIXED"


def _resample_closed(w1m: pd.DataFrame, rule: str, bucket_min: int) -> pd.DataFrame:
    """M1 -> ``rule`` OHLC, CLOSED buckets only.

    Same left-labelled default resample + incomplete-tail drop as _resample_m3
    (a bucket is complete only once w1m reaches its last minute). Used only by
    the ER gate so the ratio sees the SAME closed higher-TF bars the regime
    engine would fetch from OANDA.
    """
    df = (w1m.set_index("time")
          .resample(rule)
          .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
          .dropna())
    if len(df) == 0:
        return df.reset_index()
    last_m1 = w1m["time"].iloc[-1]
    last_bucket = df.index[-1]
    if (pd.Timestamp(last_m1) - pd.Timestamp(last_bucket)) < pd.Timedelta(
            minutes=bucket_min - 1):
        df = df.iloc[:-1]
    return df.reset_index()


def _er_trend_regime(w1m: pd.DataFrame) -> str | None:
    """Classify the current trend regime from w1m alone, exactly as the regime
    engine would from its H1/M15 frames. Returns 'TRENDING'|'RANGING'|'MIXED',
    or None when history is too short to compute a valid ratio -- the caller
    then FAILS TOWARD OFF (never MIXED, which would make `strict` block every
    entry on a short window).
    """
    m15 = _resample_closed(w1m, "15min", 15)
    h1 = _resample_closed(w1m, "1h", 60)
    er_h1 = _efficiency_ratio(h1["close"], _ER_H1_BARS) if len(h1) else float("nan")
    er_m15 = _efficiency_ratio(m15["close"], _ER_M15_BARS) if len(m15) else float("nan")
    if pd.isna(er_h1) or pd.isna(er_m15):
        return None
    return _classify_trend(er_h1, er_m15)


def _apply_er_gate(sig: Signal | None, w1m: pd.DataFrame) -> Signal | None:
    """DEFAULT-OFF trend-persistence gate on a would-be entry.

    off        -> return ``sig`` untouched (byte-identical to pre-Task-15).
    ranging    -> None when trend_regime == RANGING, else ``sig``.
    strict     -> None when trend_regime != TRENDING, else ``sig``.
    Short window (cannot classify) -> ``sig`` (fail toward OFF) + one-time WARN.
    """
    global _er_warned
    if sig is None:
        return None
    mode = _er_gate_mode()
    if mode == "off":
        return sig
    regime = _er_trend_regime(w1m)
    if regime is None:
        if not _er_warned:
            log.warning(
                "S100_ER_GATE=%s but w1m too short for a 24-bar H1 ratio "
                "(need WIN_1M>=%d); failing toward OFF (admitting entries) "
                "until the runner window is widened", mode, _ER_MIN_BARS_1M)
            _er_warned = True
        return sig
    if mode == "ranging" and regime == "RANGING":
        return None
    if mode == "strict" and regime != "TRENDING":
        return None
    return sig


def get_signal(w1m, w5m, w15m, now_utc: datetime) -> Signal | None:
    global _pending
    if w1m is None or len(w1m) < 3 * _MIN_M3:
        return None
    if now_utc.hour not in _HOURS:
        _pending = None             # setups do not survive out of hours
        return None
    if _pending is not None and now_utc >= _pending.expires_at:
        _pending = None

    m3 = _resample_m3(w1m)
    if len(m3) < _MIN_M3:
        return None

    sig = _detect(m3, now_utc)
    if sig is not None:
        return _apply_er_gate(sig, w1m)     # DEFAULT OFF; no-op unless armed
    if _pending is None:
        return None

    # Retrace fill on the freshest 1m bar; the pending carries its own kind in
    # the reason label. A phantom-guard cancel and a fill both clear it.
    r = w1m.iloc[-1]
    clear, sig = check_touch(_pending, r["time"], float(r["high"]),
                             float(r["low"]))
    if clear:
        _pending = None
    return _apply_er_gate(sig, w1m)         # DEFAULT OFF; no-op unless armed
