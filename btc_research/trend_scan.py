"""
btc_research/trend_scan.py
--------------------------
Intraday is near-efficient (VR~1, all bracket candidates PF<1). This scans the
HIGHER timeframes (4h, 1d) where crypto structure usually lives:

  - price-path / regime summary (is the sample trending or range-bound?)
  - daily & 4h return autocorrelation + variance ratio (momentum vs reversion)
  - Donchian-N breakout forward edge (trend/momentum hypothesis)
  - daily z-score reversion forward edge (range-fade hypothesis)

Decides which family to build at the swing horizon. No look-ahead.
"""
from __future__ import annotations

import json
import math
import os

import numpy as np
import pandas as pd

from btc_research.data import get_candles

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "trend_scan.json")
R: dict = {}


def t_stat(x):
    x = np.asarray(x, float); x = x[~np.isnan(x)]
    if len(x) < 2 or x.std(ddof=1) == 0:
        return 0.0
    return float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x))))


def vr(r, q):
    r = r[~np.isnan(r)]
    v1 = np.var(r, ddof=1)
    if v1 == 0:
        return float("nan")
    rq = pd.Series(r).rolling(q).sum().dropna().values
    return float(np.var(rq, ddof=1) / (q * v1))


def regime():
    d = get_candles("1d")
    px = d["close"].values
    ret = d["close"].pct_change().dropna()
    print("=" * 70); print("PRICE PATH / REGIME (1d)"); print("=" * 70)
    print(f"  bars(days)   : {len(d)}")
    print(f"  start/end    : {px[0]:.0f} -> {px[-1]:.0f}  ({(px[-1]/px[0]-1)*100:+.1f}% total)")
    print(f"  min/max      : {px.min():.0f} / {px.max():.0f}  (range {(px.max()/px.min()-1)*100:.0f}%)")
    print(f"  up-days      : {(ret>0).mean()*100:.1f}%   daily sigma {ret.std()*100:.2f}%")
    # is end inside the historical range? -> range-bound vs trending
    R["regime"] = {
        "days": len(d), "start": round(float(px[0]), 1), "end": round(float(px[-1]), 1),
        "min": round(float(px.min()), 1), "max": round(float(px.max()), 1),
        "total_ret_pct": round(float(px[-1]/px[0]-1)*100, 1),
        "up_day_pct": round(float((ret > 0).mean()*100), 1),
        "daily_sigma_pct": round(float(ret.std()*100), 2),
    }


def structure():
    print("\n" + "=" * 70); print("MOMENTUM vs REVERSION by timeframe"); print("=" * 70)
    R["structure"] = {}
    for tf in ("4h", "1d"):
        d = get_candles(tf)
        r = d["close"].pct_change().dropna().values
        ac = {lag: round(float(pd.Series(r).autocorr(lag)), 4) for lag in (1, 2, 3, 5)}
        vrs = {q: round(vr(r, q), 3) for q in (2, 3, 5, 10)}
        print(f"\n  {tf}: n={len(r)}")
        print(f"    autocorr {ac}")
        print(f"    VR       {vrs}   (>1 trend, <1 revert)")
        R["structure"][tf] = {"autocorr": ac, "vr": vrs, "n": len(r)}


def donchian_edge():
    """Trend hypothesis: after Donchian-L breakout, forward H-bar return in break dir."""
    print("\n" + "=" * 70); print("DONCHIAN BREAKOUT forward edge (trend)"); print("=" * 70)
    R["donchian"] = {}
    for tf, Ls, H in [("4h", (10, 20, 30), 6), ("1d", (5, 10, 20), 5)]:
        d = get_candles(tf).reset_index(drop=True)
        base = float(d["close"].pct_change().abs().mean())
        for L in Ls:
            hi = d["high"].rolling(L).max().shift(1)
            lo = d["low"].rolling(L).min().shift(1)
            c, cp = d["close"], d["close"].shift(1)
            up = (c > hi) & (cp <= hi)
            dn = (c < lo) & (cp >= lo)
            fwd = d["close"].shift(-H) / d["close"] - 1.0
            up_e = fwd[up].values; dn_e = -fwd[dn].values
            both = np.concatenate([up_e[~np.isnan(up_e)], dn_e[~np.isnan(dn_e)]])
            if len(both) < 10:
                continue
            print(f"  {tf} Donchian{L} (+{H}bars): n={len(both):4d}  "
                  f"edge={both.mean()*1e4:+7.1f}bps  win={ (both>0).mean()*100:4.1f}%  "
                  f"t={t_stat(both):+5.2f}  base={base*1e4:.0f}bps")
            R["donchian"][f"{tf}_L{L}"] = {
                "n": len(both), "edge_bps": round(float(both.mean()*1e4), 1),
                "win_pct": round(float((both > 0).mean()*100), 1), "t": round(t_stat(both), 2),
            }


def reversion_edge():
    """Range-fade hypothesis: at daily/4h z-score extreme, forward return reverts."""
    print("\n" + "=" * 70); print("Z-SCORE REVERSION forward edge (range fade)"); print("=" * 70)
    R["reversion"] = {}
    for tf, N, H in [("4h", 30, 6), ("1d", 20, 5)]:
        d = get_candles(tf).reset_index(drop=True)
        base = float(d["close"].pct_change().abs().mean())
        ma = d["close"].rolling(N).mean()
        sd = d["close"].rolling(N).std()
        z = (d["close"] - ma) / sd
        fwd = d["close"].shift(-H) / d["close"] - 1.0
        for thr in (1.5, 2.0):
            hi = z > thr   # fade short
            lo = z < -thr  # fade long
            s = -fwd[hi].values; lng = fwd[lo].values
            both = np.concatenate([s[~np.isnan(s)], lng[~np.isnan(lng)]])
            if len(both) < 10:
                continue
            print(f"  {tf} |z|>{thr} fade (+{H}bars): n={len(both):4d}  "
                  f"edge={both.mean()*1e4:+7.1f}bps  win={(both>0).mean()*100:4.1f}%  "
                  f"t={t_stat(both):+5.2f}")
            R["reversion"][f"{tf}_z{thr}"] = {
                "n": len(both), "edge_bps": round(float(both.mean()*1e4), 1),
                "win_pct": round(float((both > 0).mean()*100), 1), "t": round(t_stat(both), 2),
            }


if __name__ == "__main__":
    regime()
    structure()
    donchian_edge()
    reversion_edge()
    with open(OUT, "w") as fh:
        json.dump(R, fh, indent=2, default=str)
    print(f"\n[trend_scan] wrote {OUT}")
