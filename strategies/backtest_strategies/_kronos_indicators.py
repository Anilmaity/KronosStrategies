"""
_kronos_indicators.py
---------------------
Self-contained indicator + HTF helper code copied (with permission) from
TradingSkills `backtest/indicators.py` and `backtest/dev/htf.py`.

Used by the six Kronos v3-port strategies:
    kronos_s02_stoch_revert, kronos_s05_threebar_pull,
    kronos_s06_session_sweep, kronos_s07_crt,
    kronos_s14_m5_ema_stretch, kronos_sh_momo_short.

Everything here is causal: every value computed on bar t uses only data
fully closed BEFORE bar t. Live runner already drops the still-forming
last 1m bar; these helpers assume the dataframe passed in is a window
of fully-closed bars (working-TF == M15 or M5).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Basic indicators
# ---------------------------------------------------------------------------
def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(s: pd.Series, n: int) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def stoch(df: pd.DataFrame, n: int) -> pd.Series:
    ll = df["low"].rolling(n, min_periods=n).min()
    hh = df["high"].rolling(n, min_periods=n).max()
    return 100 * (df["close"] - ll) / (hh - ll).replace(0, np.nan)


# ---------------------------------------------------------------------------
# HTF bias (D1 / H4) -- copied from TradingSkills backtest/dev/htf.py
# ---------------------------------------------------------------------------
def _tf_bias(htf: pd.DataFrame) -> pd.Series:
    """Bias series for one resampled HTF frame.

    Two-component agreeing classifier: trend (close vs EMA20 + EMA20 slope)
    AND structure (sign of 5-bar leg). Both must agree for +/-1; else 0.
    """
    c = htf["close"]
    e = c.ewm(span=20, adjust=False, min_periods=20).mean()
    slope = e - e.shift(3)
    trend = np.where((c > e) & (slope > 0), 1,
                     np.where((c < e) & (slope < 0), -1, 0))
    leg = c - c.shift(5)
    struct = np.where(leg > 0, 1, np.where(leg < 0, -1, 0))
    bias = np.where((trend == 1) & (struct == 1), 1,
                    np.where((trend == -1) & (struct == -1), -1, 0))
    return pd.Series(bias, index=htf.index)


def htf_bias_from_window(w: pd.DataFrame, rule: str) -> int:
    """Return the {-1, 0, +1} HTF bias for the most recent CLOSED HTF bar
    of `rule` ('1D' or '4h'), built from the intraday window `w`.

    `w` must contain `time` (tz-aware UTC) and `open/high/low/close`.
    The bias is read on the last fully-closed HTF bar -- i.e. on the
    series shifted by one. Returns 0 when the window is too short.
    """
    if w is None or len(w) < 25:
        return 0
    t = pd.to_datetime(w["time"], utc=True)
    o = pd.DataFrame({
        "open":  w["open"].astype(float).to_numpy(),
        "high":  w["high"].astype(float).to_numpy(),
        "low":   w["low"].astype(float).to_numpy(),
        "close": w["close"].astype(float).to_numpy(),
    }, index=t)
    htf = o.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()
    if len(htf) < 25:
        return 0
    bias = _tf_bias(htf).shift(1)   # last CLOSED HTF bar
    if bias.empty:
        return 0
    v = bias.dropna().iloc[-1] if not bias.dropna().empty else 0
    try:
        return int(v)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Prior-day liquidity zones (PDH / PDL / approximate POC)
# ---------------------------------------------------------------------------
def prior_day_zones(w: pd.DataFrame, ref_time, poc_bins: int = 40):
    """Return (pdh, pdl, pdc, pd_poc) -- the prior-day liquidity references --
    relative to `ref_time` (UTC). Returns (nan, nan, nan, nan) if the prior
    day's bars are not present in the window.

    Prior day = max calendar UTC day strictly < ref_time.date().
    """
    if w is None or len(w) == 0:
        return float("nan"), float("nan"), float("nan"), float("nan")
    t = pd.to_datetime(w["time"], utc=True)
    days = t.dt.date.to_numpy()
    ref_day = pd.Timestamp(ref_time).tz_convert("UTC").date() \
        if pd.Timestamp(ref_time).tzinfo else pd.Timestamp(ref_time, tz="UTC").date()
    prior_mask = days < ref_day
    if not prior_mask.any():
        return float("nan"), float("nan"), float("nan"), float("nan")
    prior_days = sorted(set(days[prior_mask]))
    pd_day = prior_days[-1]
    bd = w[days == pd_day]
    if len(bd) == 0:
        return float("nan"), float("nan"), float("nan"), float("nan")
    hi = float(bd["high"].max())
    lo = float(bd["low"].min())
    cl = float(bd["close"].iloc[-1])
    if hi <= lo:
        return hi, lo, cl, cl
    edges = np.linspace(lo, hi, poc_bins + 1)
    idx = np.clip(np.digitize(bd["close"].astype(float).to_numpy(), edges) - 1,
                  0, poc_bins - 1)
    bins = np.bincount(idx, minlength=poc_bins)
    b = int(bins.argmax())
    poc = (edges[b] + edges[b + 1]) / 2
    return hi, lo, cl, float(poc)


def zone_dist_atr_at(close_px: float, zones, atr_val: float) -> float:
    """Minimum |close - zone| / ATR across the supplied zone references.
    Skips NaN zones. Returns +inf if no usable zones / atr is zero."""
    if atr_val is None or not np.isfinite(atr_val) or atr_val <= 0:
        return float("inf")
    dists = []
    for z in zones:
        if z is None or not np.isfinite(z):
            continue
        dists.append(abs(close_px - z))
    if not dists:
        return float("inf")
    return min(dists) / atr_val


# ---------------------------------------------------------------------------
# Session helpers (intraday minute of day)
# ---------------------------------------------------------------------------
def minute_of_day(t) -> int:
    """t may be a naive or tz-aware Timestamp / datetime. Returns hour*60+minute
    in UTC. Assumes the candle frame's `time` is UTC (the Kronos tsdb_reader
    strips tz after converting to UTC -> naive UTC datetime64[ns])."""
    ts = pd.Timestamp(t)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC")
    return int(ts.hour) * 60 + int(ts.minute)
