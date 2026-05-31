"""
btc_research/characterize.py
----------------------------
Phase B — research BTC_USD across multiple lenses to find where a real edge
*could* live, BEFORE building any strategy.

Two parts:
  1. CHARACTERIZATION — descriptive stats (vol by hour/session, daily range &
     weekday, return autocorrelation, variance ratio = mean-revert vs trend).
  2. RAW EDGE SCANS — measure the conditional forward-return edge of each
     hypothesis as a simple statistic. No TP/SL sim yet; just "does price behave
     differently after this condition vs baseline?" with a t-stat for honesty.

Strict no-look-ahead: every condition uses only the signalling (closed) bar and
prior bars; forward returns are measured strictly AFTER the signal bar.

Writes btc_research/cache/characterization.json for the dashboard and prints a
readable report.
"""
from __future__ import annotations

import json
import math
import os

import numpy as np
import pandas as pd

from btc_research.data import get_candles, add_time_features

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "characterization.json")
RESULTS: dict = {}


def _t_stat(sample: np.ndarray) -> float:
    """One-sample t-stat vs mean 0. |t|>~2 => mean differs from 0 at ~5%."""
    sample = sample[~np.isnan(sample)]
    n = len(sample)
    if n < 2:
        return 0.0
    sd = sample.std(ddof=1)
    if sd == 0:
        return 0.0
    return float(sample.mean() / (sd / math.sqrt(n)))


def _edge_report(name: str, fwd: np.ndarray, side: int, horizon: str,
                 baseline_abs: float, extra: dict | None = None) -> dict:
    """
    fwd  : forward pct returns AFTER the signal (raw, not yet signed)
    side : +1 if hypothesis is long, -1 if short  → signed edge = side*fwd
    """
    fwd = fwd[~np.isnan(fwd)]
    signed = side * fwd
    n = len(signed)
    if n == 0:
        return {"name": name, "n": 0}
    mean_bps = float(signed.mean() * 1e4)
    win = float((signed > 0).mean() * 100)
    t = _t_stat(signed)
    rec = {
        "name": name, "n": n, "horizon": horizon, "side": side,
        "mean_edge_bps": round(mean_bps, 2),
        "win_pct": round(win, 1),
        "t_stat": round(t, 2),
        "baseline_move_bps": round(baseline_abs * 1e4, 2),
        "edge_vs_baseline": round(mean_bps / (baseline_abs * 1e4 + 1e-9), 2),
    }
    if extra:
        rec.update(extra)
    return rec


# ─────────────────────────────────────────────────────────────────────────────
# 1. CHARACTERIZATION
# ─────────────────────────────────────────────────────────────────────────────
def characterize():
    c1 = add_time_features(get_candles("1m"))
    c5 = add_time_features(get_candles("5m"))
    c15 = add_time_features(get_candles("15m"))
    c1h = get_candles("1h")
    cd = add_time_features(get_candles("1d"))

    print("=" * 72)
    print("CHARACTERIZATION")
    print("=" * 72)

    # vol by hour (mean abs 5m return, bps)
    c5 = c5.copy()
    c5["ret"] = c5["close"].pct_change()
    by_hour = c5.groupby("hour")["ret"].apply(lambda s: s.abs().mean() * 1e4)
    print("\n5m mean |return| by UTC hour (bps):")
    for h, v in by_hour.items():
        bar = "#" * int(v / by_hour.max() * 40)
        print(f"  {h:02d}  {v:6.1f}  {bar}")
    RESULTS["vol_by_hour_bps"] = {int(h): round(float(v), 2) for h, v in by_hour.items()}

    # vol by session
    by_sess = c5.groupby("session")["ret"].apply(lambda s: s.abs().mean() * 1e4)
    print("\n5m mean |return| by session (bps):")
    for s, v in by_sess.sort_values(ascending=False).items():
        print(f"  {s:7s} {v:6.1f}")
    RESULTS["vol_by_session_bps"] = {s: round(float(v), 2) for s, v in by_sess.items()}

    # daily range pct, by weekday
    cd = cd.copy()
    cd["range_pct"] = (cd["high"] - cd["low"]) / cd["open"] * 100
    print(f"\nDaily range %: mean={cd['range_pct'].mean():.2f}  median={cd['range_pct'].median():.2f}  "
          f"p90={cd['range_pct'].quantile(.9):.2f}")
    by_wd = cd.groupby("weekday")["range_pct"].mean()
    wd_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    print("Daily range % by weekday:")
    for wd, v in by_wd.items():
        print(f"  {wd_names[int(wd)]} {v:5.2f}")
    RESULTS["daily_range_pct"] = {
        "mean": round(float(cd["range_pct"].mean()), 3),
        "median": round(float(cd["range_pct"].median()), 3),
        "p90": round(float(cd["range_pct"].quantile(.9)), 3),
        "by_weekday": {wd_names[int(wd)]: round(float(v), 3) for wd, v in by_wd.items()},
    }

    # autocorrelation + variance ratio per TF: mean-revert (VR<1) vs trend (VR>1)
    print("\nReturn autocorr(lag1) & variance ratio (VR<1 mean-revert, >1 trend):")
    ac = {}
    for tf, df in [("5m", c5), ("15m", c15), ("1h", c1h)]:
        r = df["close"].pct_change().dropna().values
        lag1 = float(np.corrcoef(r[:-1], r[1:])[0, 1])
        # variance ratio at q=4
        q = 4
        r_q = pd.Series(df["close"]).pct_change(q).dropna().values
        vr = float(np.var(r_q, ddof=1) / (q * np.var(r, ddof=1))) if np.var(r, ddof=1) > 0 else float("nan")
        print(f"  {tf:4s}  autocorr1={lag1:+.4f}   VR(4)={vr:.3f}")
        ac[tf] = {"autocorr1": round(lag1, 5), "vr4": round(vr, 4)}
    RESULTS["mean_revert_vs_trend"] = ac


# ─────────────────────────────────────────────────────────────────────────────
# 2. RAW EDGE SCANS
# ─────────────────────────────────────────────────────────────────────────────
def scan_session_sweep():
    """ICT: sweep of Asian-session range during London/NY, close back inside -> fade."""
    df = add_time_features(get_candles("15m")).reset_index(drop=True)
    df["ret"] = df["close"].pct_change()
    baseline = float(df["ret"].abs().mean())

    # Asia range per date (00:00-06:59)
    asia = df[df["hour"] < 7].groupby("date").agg(asia_hi=("high", "max"), asia_lo=("low", "min"))
    df = df.merge(asia, on="date", how="left")

    K = 4  # forward horizon bars (1h on 15m)
    fwd = (df["close"].shift(-K) / df["close"] - 1.0)

    trade_window = (df["hour"] >= 7) & (df["hour"] < 20) & df["asia_hi"].notna()
    sweep_up = trade_window & (df["high"] > df["asia_hi"]) & (df["close"] < df["asia_hi"])   # fade short
    sweep_dn = trade_window & (df["low"] < df["asia_lo"]) & (df["close"] > df["asia_lo"])    # fade long

    recs = []
    recs.append(_edge_report("asia_sweep_high_fade_short", fwd[sweep_up].values, -1, f"{K}x15m", baseline,
                             {"signals": int(sweep_up.sum())}))
    recs.append(_edge_report("asia_sweep_low_fade_long", fwd[sweep_dn].values, +1, f"{K}x15m", baseline,
                             {"signals": int(sweep_dn.sum())}))
    return recs


def scan_pdh_pdl_sweep():
    """ICT: sweep of prior-day high/low, close back inside -> fade."""
    df = add_time_features(get_candles("15m")).reset_index(drop=True)
    df["ret"] = df["close"].pct_change()
    baseline = float(df["ret"].abs().mean())

    daily = df.groupby("date").agg(d_hi=("high", "max"), d_lo=("low", "min")).reset_index()
    daily["pdh"] = daily["d_hi"].shift(1)
    daily["pdl"] = daily["d_lo"].shift(1)
    df = df.merge(daily[["date", "pdh", "pdl"]], on="date", how="left")

    K = 8  # 2h
    fwd = (df["close"].shift(-K) / df["close"] - 1.0)
    valid = df["pdh"].notna()
    sweep_up = valid & (df["high"] > df["pdh"]) & (df["close"] < df["pdh"])
    sweep_dn = valid & (df["low"] < df["pdl"]) & (df["close"] > df["pdl"])

    return [
        _edge_report("pdh_sweep_fade_short", fwd[sweep_up].values, -1, f"{K}x15m", baseline,
                     {"signals": int(sweep_up.sum())}),
        _edge_report("pdl_sweep_fade_long", fwd[sweep_dn].values, +1, f"{K}x15m", baseline,
                     {"signals": int(sweep_dn.sum())}),
    ]


def scan_zscore_mr():
    """Statistical: price stretched k*std from rolling mean -> fade back."""
    df = get_candles("15m").reset_index(drop=True)
    df["ret"] = df["close"].pct_change()
    baseline = float(df["ret"].abs().mean())

    N = 40
    ma = df["close"].rolling(N).mean()
    sd = df["close"].rolling(N).std()
    z = (df["close"] - ma) / sd

    K = 4
    fwd = (df["close"].shift(-K) / df["close"] - 1.0)
    recs = []
    for thr in (1.5, 2.0, 2.5):
        hi = z > thr      # over-extended up -> fade short
        lo = z < -thr     # over-extended down -> fade long
        recs.append(_edge_report(f"zscore>{thr}_fade_short", fwd[hi].values, -1, f"{K}x15m", baseline,
                                 {"signals": int(hi.sum()), "z_thr": thr}))
        recs.append(_edge_report(f"zscore<-{thr}_fade_long", fwd[lo].values, +1, f"{K}x15m", baseline,
                                 {"signals": int(lo.sum()), "z_thr": thr}))
    return recs


def scan_breakout():
    """Momentum: break of prior-K high with range expansion -> continuation."""
    df = get_candles("15m").reset_index(drop=True)
    df["ret"] = df["close"].pct_change()
    baseline = float(df["ret"].abs().mean())

    L = 20
    prior_hi = df["high"].rolling(L).max().shift(1)
    prior_lo = df["low"].rolling(L).min().shift(1)
    rng = (df["high"] - df["low"])
    atr = rng.rolling(L).mean()
    expand = rng > 1.5 * atr

    K = 4
    fwd = (df["close"].shift(-K) / df["close"] - 1.0)
    brk_up = (df["close"] > prior_hi) & expand     # continuation long
    brk_dn = (df["close"] < prior_lo) & expand     # continuation short
    return [
        _edge_report("breakout_up_continue_long", fwd[brk_up].values, +1, f"{K}x15m", baseline,
                     {"signals": int(brk_up.sum())}),
        _edge_report("breakout_dn_continue_short", fwd[brk_dn].values, -1, f"{K}x15m", baseline,
                     {"signals": int(brk_dn.sum())}),
    ]


def scan_round_number():
    """Do big round numbers ($1000 / $500 grid) act as magnets / fade levels?"""
    df = get_candles("5m").reset_index(drop=True)
    df["ret"] = df["close"].pct_change()
    baseline = float(df["ret"].abs().mean())

    recs = []
    for grid in (1000.0, 500.0):
        nearest = (df["close"] / grid).round() * grid
        dist = (df["close"] - nearest).abs()
        near = dist < (grid * 0.0008)   # within ~0.08% of a round level (~$60 at 1000-grid)
        # fade toward the level: if price just above level -> short bias; just below -> long
        above = near & (df["close"] > nearest)
        below = near & (df["close"] < nearest)
        K = 6  # 30m
        fwd = (df["close"].shift(-K) / df["close"] - 1.0)
        recs.append(_edge_report(f"round{int(grid)}_above_fade_short", fwd[above].values, -1, f"{K}x5m", baseline,
                                 {"signals": int(above.sum())}))
        recs.append(_edge_report(f"round{int(grid)}_below_fade_long", fwd[below].values, +1, f"{K}x5m", baseline,
                                 {"signals": int(below.sum())}))
    return recs


def run_scans():
    print("\n" + "=" * 72)
    print("RAW EDGE SCANS  (signed edge in bps; |t|>2 ~ significant; baseline = avg |bar move|)")
    print("=" * 72)
    all_recs = []
    for fn in (scan_session_sweep, scan_pdh_pdl_sweep, scan_zscore_mr, scan_breakout, scan_round_number):
        all_recs.extend(fn())

    all_recs = [r for r in all_recs if r.get("n", 0) > 0]
    all_recs.sort(key=lambda r: r.get("t_stat", 0), reverse=True)
    print(f"\n{'edge':38s} {'n':>6s} {'edge_bps':>9s} {'win%':>6s} {'t':>6s} {'vs_base':>8s}")
    print("-" * 80)
    for r in all_recs:
        print(f"{r['name']:38s} {r['n']:>6d} {r['mean_edge_bps']:>9.1f} "
              f"{r['win_pct']:>6.1f} {r['t_stat']:>6.2f} {r['edge_vs_baseline']:>8.2f}")
    RESULTS["edge_scans"] = all_recs
    return all_recs


if __name__ == "__main__":
    characterize()
    run_scans()
    with open(OUT, "w") as fh:
        json.dump(RESULTS, fh, indent=2, default=str)
    print(f"\n[characterize] wrote {OUT}")
