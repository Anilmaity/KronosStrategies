"""
btc_research/strategies.py
--------------------------
Candidate BTC_USD strategies, chosen from the data (characterize.py):
  - BTC is momentum/continuation-leaning (fades lose; z<-2 *continues* down).
  - Volatility concentrates in the NY window (13:00-17:00 UTC).

So the family under test is session-timed volatility breakout / continuation,
plus a mean-reversion CONTROL we expect to fail (confirms the redirection).

Each generator returns list[engine.Entry] using only causal info (rolling/shift),
firing on the bar whose CLOSE first crosses the trigger (transition), so the
engine fills at the next bar's open.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from btc_research.engine import Entry
from btc_research.data import add_time_features


def _atr(df: pd.DataFrame, n: int) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def _bars_to_hour(df: pd.DataFrame, end_hour: int) -> np.ndarray:
    """Bars remaining until the first bar whose hour >= end_hour (per day). Used for time-exit."""
    hours = df["time"].dt.hour.values
    n = len(df)
    out = np.full(n, 0, dtype=int)
    # forward count to next bar with hour>=end_hour or day change
    next_close = n - 1
    days = df["time"].dt.normalize().values
    for i in range(n - 1, -1, -1):
        if hours[i] >= end_hour or (i + 1 < n and days[i + 1] != days[i]):
            next_close = i
        out[i] = max(0, next_close - i)
    return out


# ── 1. NY opening-range breakout (continuation) ──────────────────────────────
def orb_ny(tf_df: pd.DataFrame, or_start=12, or_end=13, trade_end=20,
           tp_mult=2.0, sl_frac=1.0, max_hold=28, reason="ORB_NY") -> list[Entry]:
    df = add_time_features(tf_df).reset_index(drop=True)
    df["date_"] = df["time"].dt.normalize()
    ormask = (df["hour"] >= or_start) & (df["hour"] < or_end)
    org = df[ormask].groupby("date_").agg(or_hi=("high", "max"), or_lo=("low", "min"))
    df = df.merge(org, left_on="date_", right_index=True, how="left")
    df["or_rng"] = df["or_hi"] - df["or_lo"]

    in_win = (df["hour"] >= or_end) & (df["hour"] < trade_end) & df["or_hi"].notna() & (df["or_rng"] > 0)
    c, cp = df["close"], df["close"].shift(1)
    cross_up = in_win & (c > df["or_hi"]) & (cp <= df["or_hi"])
    cross_dn = in_win & (c < df["or_lo"]) & (cp >= df["or_lo"])

    entries = []
    for i in np.where(cross_up.values)[0]:
        rng = df["or_rng"].iloc[i]
        entries.append(Entry(int(i), "BUY",
                             stop_loss=df["or_hi"].iloc[i] - sl_frac * rng,
                             take_profit=c.iloc[i] + tp_mult * rng,
                             max_hold_bars=max_hold, reason=reason + "_UP"))
    for i in np.where(cross_dn.values)[0]:
        rng = df["or_rng"].iloc[i]
        entries.append(Entry(int(i), "SELL",
                             stop_loss=df["or_lo"].iloc[i] + sl_frac * rng,
                             take_profit=c.iloc[i] - tp_mult * rng,
                             max_hold_bars=max_hold, reason=reason + "_DN"))
    return entries


# ── 2. Volatility breakout (Donchian + expansion), NY-filtered ───────────────
def vbo(tf_df: pd.DataFrame, L=20, atr_n=20, expand=1.5, tp_atr=2.5, sl_atr=1.2,
        ny_only=True, max_hold=24, reason="VBO") -> list[Entry]:
    df = add_time_features(tf_df).reset_index(drop=True)
    atr = _atr(df, atr_n)
    prior_hi = df["high"].rolling(L).max().shift(1)
    prior_lo = df["low"].rolling(L).min().shift(1)
    rng = df["high"] - df["low"]
    exp = rng > expand * atr
    c, cp = df["close"], df["close"].shift(1)
    sess = (df["hour"] >= 12) & (df["hour"] < 20) if ny_only else pd.Series(True, index=df.index)

    up = sess & exp & (c > prior_hi) & (cp <= prior_hi.shift(0))
    dn = sess & exp & (c < prior_lo) & (cp >= prior_lo.shift(0))

    entries = []
    for i in np.where(up.fillna(False).values)[0]:
        a = atr.iloc[i]
        if not np.isfinite(a) or a <= 0:
            continue
        entries.append(Entry(int(i), "BUY", c.iloc[i] - sl_atr * a, c.iloc[i] + tp_atr * a,
                             max_hold, reason + "_UP"))
    for i in np.where(dn.fillna(False).values)[0]:
        a = atr.iloc[i]
        if not np.isfinite(a) or a <= 0:
            continue
        entries.append(Entry(int(i), "SELL", c.iloc[i] + sl_atr * a, c.iloc[i] - tp_atr * a,
                             max_hold, reason + "_DN"))
    return entries


# ── 3. Downside-momentum continuation (tests the z<-2 'fade loses' asymmetry) ─
def drop_momentum(tf_df: pd.DataFrame, N=40, z_thr=2.0, atr_n=20, tp_atr=2.0, sl_atr=1.5,
                  ny_only=True, max_hold=24, reason="DROPMOM") -> list[Entry]:
    df = add_time_features(tf_df).reset_index(drop=True)
    atr = _atr(df, atr_n)
    ma = df["close"].rolling(N).mean()
    sd = df["close"].rolling(N).std()
    z = (df["close"] - ma) / sd
    c, zp = df["close"], z.shift(1)
    sess = (df["hour"] >= 12) & (df["hour"] < 20) if ny_only else pd.Series(True, index=df.index)
    trig = sess & (z < -z_thr) & (zp >= -z_thr)   # first bar crossing below -z_thr -> SHORT continuation
    entries = []
    for i in np.where(trig.fillna(False).values)[0]:
        a = atr.iloc[i]
        if not np.isfinite(a) or a <= 0:
            continue
        entries.append(Entry(int(i), "SELL", c.iloc[i] + sl_atr * a, c.iloc[i] - tp_atr * a,
                             max_hold, reason))
    return entries


# ── 4. CONTROL: z-score mean reversion fade (expected to FAIL on BTC) ────────
# ═════════════════════════════════════════════════════════════════════════════
# SWING (4h) MEAN-REVERSION FAMILY  — the data-supported edge:
#   intraday momentum, but 4h/daily MEAN-REVERTS (autocorr<0, VR<1); breakouts
#   FADE (Donchian fwd edge t=-3.08); z-extremes revert (t=+2.20).
# ═════════════════════════════════════════════════════════════════════════════
def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def _trend_strength(df: pd.DataFrame, fast=20, slow=50, atr_n=14) -> pd.Series:
    """|EMAfast - EMAslow| / ATR  — high => strong trend (suppress fades)."""
    atr = _atr(df, atr_n)
    return (_ema(df["close"], fast) - _ema(df["close"], slow)).abs() / atr


def _efficiency_ratio(close: pd.Series, n: int = 10) -> pd.Series:
    """Kaufman Efficiency Ratio over n bars (causal). ~1 => clean trend, ~0 => chop.
    Range-fades should only fire when ER is LOW (choppy)."""
    direction = (close - close.shift(n)).abs()
    volatility = close.diff().abs().rolling(n).sum()
    return direction / volatility


def weekly_trend_state(df: pd.DataFrame, ema_weeks: int = 6, strength: float = 1.0) -> np.ndarray:
    """Causal higher-timeframe (weekly) trend state for each row of `df` (4h bars).

    Returns an int array aligned to df rows: +1 strong weekly UPtrend,
    -1 strong weekly DOWNtrend, 0 range / weak. State at a 4h bar uses only the
    most recent weekly bar that has ALREADY CLOSED at or before that bar's time
    (merge_asof backward on the week-end), so there is no look-ahead.
    """
    s = df.set_index("time")["close"].sort_index()
    wk = pd.DataFrame({"wclose": s.resample("1W").last()}).dropna()
    if len(wk) < ema_weeks + 2:
        return np.zeros(len(df), dtype=int)
    wk["wema"] = wk["wclose"].ewm(span=ema_weeks, adjust=False).mean()
    wk["watr"] = wk["wclose"].diff().abs().rolling(ema_weeks).mean()
    dev = (wk["wclose"] - wk["wema"]) / wk["watr"].replace(0, np.nan)
    wk["state"] = np.where(dev > strength, 1, np.where(dev < -strength, -1, 0))
    wk = wk.reset_index().rename(columns={"time": "week_end"})

    left = df[["time"]].reset_index(drop=True).sort_values("time")
    merged = pd.merge_asof(left, wk[["week_end", "state"]], left_on="time",
                           right_on="week_end", direction="backward")
    return merged["state"].fillna(0).astype(int).values


def sweep_reversal(tf_df: pd.DataFrame, L=20, atr_n=14, sl_buf=0.5, tp_frac=1.0,
                   max_hold=12, trend_guard=None, reason="SWEEPREV") -> list[Entry]:
    """ICT turtle-soup / liquidity-sweep reversal on the swing TF.

    Poke beyond the Donchian-L extreme then CLOSE back inside (rejection) -> fade.
      SL beyond the sweep extreme (+ buffer*ATR);  TP = reversion to MA(L),
      scaled by tp_frac. Optional trend_guard: skip when trend_strength > guard.
    """
    df = add_time_features(tf_df).reset_index(drop=True)
    atr = _atr(df, atr_n)
    ma = df["close"].rolling(L).mean()
    hi = df["high"].rolling(L).max().shift(1)
    lo = df["low"].rolling(L).min().shift(1)
    c = df["close"]
    ts = _trend_strength(df) if trend_guard is not None else None

    sweep_up = (df["high"] > hi) & (c < hi)   # took BSL, closed back -> SHORT
    sweep_dn = (df["low"] < lo) & (c > lo)     # took SSL, closed back -> LONG

    entries = []
    for i in np.where(sweep_up.fillna(False).values)[0]:
        a = atr.iloc[i]
        if not np.isfinite(a) or a <= 0 or not np.isfinite(ma.iloc[i]):
            continue
        if ts is not None and ts.iloc[i] > trend_guard:
            continue
        entry_ref = c.iloc[i]
        tp = entry_ref - tp_frac * (entry_ref - ma.iloc[i])
        if entry_ref - tp < 0.5 * a:          # target too small vs noise
            continue
        sl = df["high"].iloc[i] + sl_buf * a
        entries.append(Entry(int(i), "SELL", sl, tp, max_hold, reason + "_S"))
    for i in np.where(sweep_dn.fillna(False).values)[0]:
        a = atr.iloc[i]
        if not np.isfinite(a) or a <= 0 or not np.isfinite(ma.iloc[i]):
            continue
        if ts is not None and ts.iloc[i] > trend_guard:
            continue
        entry_ref = c.iloc[i]
        tp = entry_ref + tp_frac * (ma.iloc[i] - entry_ref)
        if tp - entry_ref < 0.5 * a:
            continue
        sl = df["low"].iloc[i] - sl_buf * a
        entries.append(Entry(int(i), "BUY", sl, tp, max_hold, reason + "_L"))
    return entries


def zscore_revert_swing(tf_df: pd.DataFrame, N=30, z_thr=2.0, atr_n=14, sl_atr=2.0,
                        max_hold=12, trend_guard=None, er_max=None, er_n=10,
                        htf_gate=None, htf_ema_weeks=6, htf_strength=1.0,
                        reason="ZREV") -> list[Entry]:
    """Fade |z|>thr back to the mean on the swing TF. TP = MA(N), SL = sl_atr*ATR.

    Optional regime gates (all causal):
      trend_guard : skip when |EMAfast-EMAslow|/ATR > guard
      er_max      : skip when Kaufman Efficiency Ratio(er_n) > er_max (trending)
      htf_gate    : weekly-trend filter (weekly_trend_state):
                      'align'      -> don't fade AGAINST a strong weekly trend
                                      (block SHORT in weekly up, LONG in weekly down)
                      'range_only' -> only fade when weekly state is range (0)
    """
    df = add_time_features(tf_df).reset_index(drop=True)
    atr = _atr(df, atr_n)
    ma = df["close"].rolling(N).mean()
    sd = df["close"].rolling(N).std()
    z = (df["close"] - ma) / sd
    c, zp = df["close"], z.shift(1)
    ts = _trend_strength(df) if trend_guard is not None else None
    er = _efficiency_ratio(df["close"], er_n) if er_max is not None else None
    hstate = weekly_trend_state(df, htf_ema_weeks, htf_strength) if htf_gate else None

    def _regime_block(i):
        if ts is not None and ts.iloc[i] > trend_guard:
            return True
        if er is not None and (not np.isfinite(er.iloc[i]) or er.iloc[i] > er_max):
            return True
        return False

    def _htf_block(i, side):
        if hstate is None:
            return False
        st = hstate[i]
        if htf_gate == "range_only":
            return st != 0
        if htf_gate == "align":               # block fades fighting a strong trend
            return (side == "SELL" and st == 1) or (side == "BUY" and st == -1)
        return False

    short = (z > z_thr) & (zp <= z_thr)
    long_ = (z < -z_thr) & (zp >= -z_thr)
    entries = []
    for i in np.where(short.fillna(False).values)[0]:
        a = atr.iloc[i]
        if not np.isfinite(a) or a <= 0 or not np.isfinite(ma.iloc[i]) or _regime_block(i) or _htf_block(i, "SELL"):
            continue
        entries.append(Entry(int(i), "SELL", c.iloc[i] + sl_atr * a, ma.iloc[i], max_hold, reason + "_S"))
    for i in np.where(long_.fillna(False).values)[0]:
        a = atr.iloc[i]
        if not np.isfinite(a) or a <= 0 or not np.isfinite(ma.iloc[i]) or _regime_block(i) or _htf_block(i, "BUY"):
            continue
        entries.append(Entry(int(i), "BUY", c.iloc[i] - sl_atr * a, ma.iloc[i], max_hold, reason + "_L"))
    return entries


def btc_swing_mr(tf_df: pd.DataFrame, z_N=30, z_thr=2.0, don_L=20, sl_atr=5.0,
                 max_hold=6, atr_n=14, trend_guard=None, reason="BTC_SWING_MR") -> list[Entry]:
    """Combined BTC swing mean-reversion: z-extreme fade + liquidity-sweep fade.

    The data-supported edge (4h): both triggers fade extremes back to the mean
    with a WIDE catastrophe stop and a 24h (6-bar) time-exit. Merging the two
    distinct triggers raises trade count and diversifies the entry path.
    """
    z_entries = zscore_revert_swing(tf_df, N=z_N, z_thr=z_thr, atr_n=atr_n,
                                    sl_atr=sl_atr, max_hold=max_hold,
                                    trend_guard=trend_guard, reason=reason + "_Z")
    swp_entries = sweep_reversal(tf_df, L=don_L, atr_n=atr_n, sl_buf=sl_atr,
                                 tp_frac=1.0, max_hold=max_hold,
                                 trend_guard=trend_guard, reason=reason + "_SW")
    return sorted(z_entries + swp_entries, key=lambda e: e.i)


def zscore_mr(tf_df: pd.DataFrame, N=40, z_thr=2.0, atr_n=20, tp_atr=1.5, sl_atr=2.0,
              ny_only=False, max_hold=16, reason="ZMR_CTRL") -> list[Entry]:
    df = add_time_features(tf_df).reset_index(drop=True)
    atr = _atr(df, atr_n)
    ma = df["close"].rolling(N).mean()
    sd = df["close"].rolling(N).std()
    z = (df["close"] - ma) / sd
    c, zp = df["close"], z.shift(1)
    sess = (df["hour"] >= 12) & (df["hour"] < 20) if ny_only else pd.Series(True, index=df.index)
    short = sess & (z > z_thr) & (zp <= z_thr)     # fade up -> SHORT
    long_ = sess & (z < -z_thr) & (zp >= -z_thr)   # fade down -> LONG
    entries = []
    for i in np.where(short.fillna(False).values)[0]:
        a = atr.iloc[i]
        if not np.isfinite(a) or a <= 0:
            continue
        entries.append(Entry(int(i), "SELL", c.iloc[i] + sl_atr * a, c.iloc[i] - tp_atr * a,
                             max_hold, reason + "_S"))
    for i in np.where(long_.fillna(False).values)[0]:
        a = atr.iloc[i]
        if not np.isfinite(a) or a <= 0:
            continue
        entries.append(Entry(int(i), "BUY", c.iloc[i] - sl_atr * a, c.iloc[i] + tp_atr * a,
                             max_hold, reason + "_L"))
    return entries
