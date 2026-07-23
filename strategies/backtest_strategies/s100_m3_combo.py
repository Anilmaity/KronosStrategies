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

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig

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

# ── Pending-setup state (persists across runner ticks in-process) ─────────────
_pending: dict | None = None
_last_bar = None            # dedup: one detection pass per closed M3 bar


def reset_state() -> None:
    """Clear the armed setup + dedup memory (used by tests)."""
    global _pending, _last_bar
    _pending = None
    _last_bar = None


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


def _atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int) -> float:
    prev_c = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
    return float(pd.Series(tr).rolling(n).mean().iloc[-1])


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
    a = _atr(h, l, c, _ATR_N)
    if not (a > 0):
        return None

    close_s = pd.Series(c)
    ema_f = close_s.ewm(span=_EMA_FAST, adjust=False).mean().iloc[-1]
    ema_s = close_s.ewm(span=_EMA_SLOW, adjust=False).mean().iloc[-1]
    d = 1 if ema_f > ema_s else -1

    armed_after = pd.Timestamp(bar_time)
    if armed_after.tzinfo is None:
        armed_after = armed_after.tz_localize("UTC")

    def _arm(kind: str, prox: float, sl: float) -> None:
        global _pending
        _pending = {
            "kind": kind, "side": d, "prox": round(float(prox), 2),
            "sl": round(float(sl), 2),
            "tp": _tp(float(prox), float(sl), d),
            "armed_after": armed_after + timedelta(minutes=3),
            "expires_at": now_utc + timedelta(minutes=3 * _RETRACE_W),
        }

    # 1) FVG in EMA direction (a newer FVG replaces an unfilled older setup)
    if d > 0 and l[k] > h[k - 2]:
        gap = l[k] - h[k - 2]
        if _MIN_FVG_ATR * a <= gap <= _MAX_GAP_ATR * a:
            _arm("fvg", l[k], h[k - 2] - _BUF_ATR * a)
            return None
    elif d < 0 and h[k] < l[k - 2]:
        gap = l[k - 2] - h[k]
        if _MIN_FVG_ATR * a <= gap <= _MAX_GAP_ATR * a:
            _arm("fvg", h[k], l[k - 2] + _BUF_ATR * a)
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


def _touch(probe_time, probe_hi: float, probe_lo: float) -> Signal | None:
    """Fire when a post-detection probe bar retraces to the pending entry;
    cancel when the probe has already pierced the stop (phantom guard)."""
    global _pending
    p = _pending
    if p is None:
        return None
    t = pd.Timestamp(probe_time)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    if t < p["armed_after"]:
        return None
    kind = p["kind"].upper()
    if p["side"] > 0:
        if probe_lo <= p["sl"]:
            _pending = None
            return None
        if probe_lo <= p["prox"]:
            _pending = None
            return Signal(side="BUY", entry_price=p["prox"],
                          stop_loss=p["sl"], take_profit=p["tp"],
                          reason=f"S100_{kind}_LONG",
                          max_hold_min=_MAX_HOLD_MIN)
    else:
        if probe_hi >= p["sl"]:
            _pending = None
            return None
        if probe_hi >= p["prox"]:
            _pending = None
            return Signal(side="SELL", entry_price=p["prox"],
                          stop_loss=p["sl"], take_profit=p["tp"],
                          reason=f"S100_{kind}_SHORT",
                          max_hold_min=_MAX_HOLD_MIN)
    return None


def get_signal(w1m, w5m, w15m, now_utc: datetime) -> Signal | None:
    global _pending
    if w1m is None or len(w1m) < 3 * _MIN_M3:
        return None
    if now_utc.hour not in _HOURS:
        _pending = None             # setups do not survive out of hours
        return None
    if _pending is not None and now_utc >= _pending["expires_at"]:
        _pending = None

    m3 = _resample_m3(w1m)
    if len(m3) < _MIN_M3:
        return None

    sig = _detect(m3, now_utc)
    if sig is not None:
        return sig
    if _pending is None:
        return None

    r = w1m.iloc[-1]
    return _touch(r["time"], float(r["high"]), float(r["low"]))
