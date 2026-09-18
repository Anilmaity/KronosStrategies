"""s99v -- S99 variant mechanics for the r2_s99_s100 study (pre-registered in PROTOCOL.md §5).

Nothing in `backtest_strategies/s99_mss_fvg.py` is edited. An arm patches the ORIGINAL module's
`get_signal` with `get_signal_v` below (via `Cfg.patch={"get_signal": s99v.get_signal_v}`) and
sets the knobs of THIS module through the harness's dotted delegate patch
(`"lab.research_s5.r2_s99_s100.s99v:MIN_GAP_ATR": 0.5`), which the harness restores after
the arm. `_detect_mss_v` is a line-for-line copy of the original `_detect_mss` with the knobs
inserted; with every knob at its neutral value the trade list must be identical to the
original (identity control, checked in `run_all.py` before any variant number is read).

State (`_pending`, `_last_mss_bar`) lives in the ORIGINAL module object, so the single shared
pending slot and `reset_state()` behave exactly as live.

Knobs (neutral value first):
  MIN_GAP_ATR  0.0   -- arm only if the FVG gap >= MIN_GAP_ATR * ATR(14, M5)
  BUF_ATR      None  -- stop buffer in ATR; None -> the original module's _BUF_ATR (0.2)
  BIAS         "off" -- "with": BUY only when the HTF bias is BULL / SELL only when BEAR;
                        "against": the complement (control)
  BIAS_DEF     "ema21_15m" -- house `htf_bias(w15m, 21)` (s14) | "ema50_h1": H1 close vs EMA50
                        of H1 closes resampled from w15m (needs win_15m >= 400)
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

import backtest_strategies.s99_mss_fvg as s99
from backtest_strategies._shared_ta import PendingRetrace, atr_last, check_touch, ensure_utc_ts, fvg_at
from backtest_strategies.base import htf_bias

MIN_GAP_ATR: float = 0.0
BUF_ATR: float | None = None
BIAS: str = "off"
BIAS_DEF: str = "ema21_15m"


def _bias_ema50_h1(w15m: pd.DataFrame) -> str | None:
    """'BULL'/'BEAR'/None: newest H1 close vs EMA50 of H1 closes, H1 buckets built from the M15
    frame (floor of the bar time to the hour; the newest bucket may be partial -- it carries the
    latest price, no look-ahead). None until 51 buckets exist."""
    if w15m is None or len(w15m) < 4 * 51:
        return None
    t = pd.to_datetime(w15m["time"]).dt.floor("h")
    closes = w15m["close"].astype(float).groupby(t.to_numpy()).last()
    if len(closes) < 51:
        return None
    ema = closes.ewm(span=50, adjust=False).mean().iloc[-1]
    last = float(closes.iloc[-1])
    if last > ema:
        return "BULL"
    if last < ema:
        return "BEAR"
    return None


def _bias(w15m) -> str | None:
    if BIAS_DEF == "ema21_15m":
        return htf_bias(w15m, period=21)
    if BIAS_DEF == "ema50_h1":
        return _bias_ema50_h1(w15m)
    raise ValueError(BIAS_DEF)


def _detect_mss_v(w5m: pd.DataFrame, now_utc) -> None:
    """Copy of s99._detect_mss with MIN_GAP_ATR / BUF_ATR inserted. Writes s99's globals."""
    h = w5m["high"].to_numpy(float)
    l = w5m["low"].to_numpy(float)
    c = w5m["close"].to_numpy(float)
    k = len(c) - 1
    bar_time = w5m["time"].iloc[k]
    if bar_time == s99._last_mss_bar:
        return
    swing_hi, swing_lo = s99._last_confirmed_swings(h[:-1], l[:-1])
    if np.isnan(swing_hi) or np.isnan(swing_lo):
        return
    a = atr_last(h, l, c, s99._ATR_N)
    if not (a > 0):
        return
    roll_hi = h[max(0, k - s99._SWEEP_N):k].max()
    roll_lo = l[max(0, k - s99._SWEEP_N):k].min()

    kind, gap, prox, dist = fvg_at(h, l, k)
    buf = s99._BUF_ATR if BUF_ATR is None else BUF_ATR
    side = 0
    if roll_hi > swing_hi and c[k] < swing_lo and kind == "bear":
        side = -1
        sl = round(dist + buf * a, 2)
    elif roll_lo < swing_lo and c[k] > swing_hi and kind == "bull":
        side = 1
        sl = round(dist - buf * a, 2)
    if side == 0:
        return
    # --- variant knob A: minimum FVG size in ATR units (neutral 0.0 = original behaviour) ---
    if MIN_GAP_ATR > 0 and gap < MIN_GAP_ATR * a:
        return
    risk = abs(sl - prox)
    if risk <= 0:
        return
    s99._last_mss_bar = bar_time
    s99._pending = PendingRetrace(
        side=side,
        prox=float(prox),
        sl=sl,
        tp=round(prox + side * s99._TP_R * risk, 2),
        armed_after=ensure_utc_ts(bar_time) + timedelta(minutes=5),
        expires_at=now_utc + timedelta(minutes=5 * s99._RETRACE_W),
        reason="S99_MSS_FVG_SHORT" if side < 0 else "S99_MSS_FVG_LONG",
        max_hold_min=s99._MAX_HOLD_MIN,
    )


def get_signal_v(w1m, w5m: pd.DataFrame, w15m, now_utc):
    """Copy of s99.get_signal with the variant detector and the optional bias gate. The bias
    gate is applied to the FILL (the signal), after the pending is consumed -- the same place
    the harness `sides` gate and live `S94_SIDES` act, so a gated-out fill frees the slot."""
    if w5m is None or len(w5m) < s99._MIN_M5:
        return None
    if now_utc.hour not in s99._HOURS:
        s99._pending = None
        return None
    if s99._pending is not None and now_utc >= s99._pending.expires_at:
        s99._pending = None

    _detect_mss_v(w5m, now_utc)
    if s99._pending is None:
        return None

    r = w1m.iloc[-1] if (w1m is not None and len(w1m) > 0) else w5m.iloc[-1]
    clear, sig = check_touch(s99._pending, r["time"], float(r["high"]), float(r["low"]))
    if clear:
        s99._pending = None
    if sig is None or BIAS == "off":
        return sig
    b = _bias(w15m)
    if b is None:
        return None
    aligned = (sig.side == "BUY" and b == "BULL") or (sig.side == "SELL" and b == "BEAR")
    if BIAS == "with":
        return sig if aligned else None
    if BIAS == "against":
        return None if aligned else sig
    raise ValueError(BIAS)
