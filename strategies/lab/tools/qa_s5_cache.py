"""lab/tools/qa_s5_cache.py -- QA the S5 cache against the QA'd M1 cache.

1. Coverage: bars per month, first/last, largest gaps (classified: weekend,
   daily break, other).
2. Derivation: build M1 from S5 (o=first, h=max, l=min, c=last per closed
   minute) and compare with bars_cache_2y M1 on the minutes both hold:
   exact-match rate for h/l (the two prices s5exit uses), and the minutes
   present in one but not the other.
3. Spread: median / p95 ask-bid at S5 resolution by hour of day.

    python -m lab.tools.qa_s5_cache [--m1 backtest/results/bars_cache_2y/is_XAU_USD_1m.parquet]
"""
from __future__ import annotations

import argparse, glob
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
S5DIR = _HERE.parent.parent / "backtest" / "results" / "bars_cache" / "s5" / "XAU_USD"
M1 = _HERE.parent.parent / "backtest" / "results" / "bars_cache_2y" / "is_XAU_USD_1m.parquet"


def load_s5() -> pd.DataFrame:
    fs = sorted(glob.glob(str(S5DIR / "*.parquet")))
    df = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.drop_duplicates("time").sort_values("time").reset_index(drop=True)


def classify_gap(t0: pd.Timestamp, secs: float) -> str:
    if t0.weekday() == 4 and t0.hour >= 20 and secs > 40 * 3600:
        return "weekend"
    if t0.hour == 21 and 3000 <= secs <= 4200:
        return "daily-break"
    return "other"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--m1", default=str(M1))
    a = ap.parse_args(argv)
    s5 = load_s5()
    print(f"S5 bars: {len(s5):,}  {s5.time.min()} -> {s5.time.max()}  ({len(s5)*8*8/1e6:.0f} MB in memory)")

    # 0. integrity -- every check must be 0 for the cache to be usable
    import json
    man = json.loads((S5DIR / "MANIFEST.json").read_text()) if (S5DIR / "MANIFEST.json").exists() else {}
    per_file = {Path(f).stem: len(pd.read_parquet(f, columns=["time"])) for f in sorted(glob.glob(str(S5DIR / "*.parquet")))}
    man_mismatch = {k: (v["rows"], per_file.get(k)) for k, v in man.items() if per_file.get(k) != v["rows"]}
    checks = {
        "NaN in any column": int(s5.isna().any(axis=1).sum()),
        "non-positive price": int(((s5[["o", "h", "l", "c", "bid_c", "ask_c"]] <= 0).any(axis=1)).sum()),
        "high < low": int((s5.h < s5.l).sum()),
        "low > min(open,close)": int((s5.l > s5[["o", "c"]].min(axis=1) + 1e-9).sum()),
        "high < max(open,close)": int((s5.h < s5[["o", "c"]].max(axis=1) - 1e-9).sum()),
        "bid > ask": int((s5.bid_c > s5.ask_c).sum()),
        "mid close outside [bid,ask] by >0.01": int(((s5.c < s5.bid_c - 0.01) | (s5.c > s5.ask_c + 0.01)).sum()),
        "negative volume": int((s5.volume < 0).sum()),
        "timestamp off the 5s grid": int(((s5.time.astype("int64") // 10**9) % 5 != 0).sum()),
        "not strictly increasing": int((s5.time.diff().dt.total_seconds().dropna() <= 0).sum()),
        "duplicate timestamps": int(s5.time.duplicated().sum()),
        "bar range > 1% of price": int(((s5.h - s5.l) / s5.c > 0.01).sum()),
        "close-to-close jump > 1%": int((s5.c.pct_change().abs() > 0.01).sum()),
        "manifest rows != file rows": len(man_mismatch),
    }
    bad = {k: v for k, v in checks.items() if v}
    print("\nintegrity checks (count of offending bars):")
    for k, v in checks.items():
        print(f"  {'FAIL' if v else 'ok  '} {k}: {v:,}")
    if man_mismatch:
        print("  manifest mismatches:", man_mismatch)
    print("INTEGRITY:", "PASS" if not bad else f"FAIL ({len(bad)} checks)")
    bym = s5.groupby(s5.time.dt.strftime("%Y-%m")).size()
    print("\nbars per month:\n" + bym.to_string())

    d = s5.time.diff().dt.total_seconds()
    gaps = s5.loc[d > 600, ["time"]].assign(secs=d[d > 600].values, prev=s5.time.shift(1)[d > 600].values)
    gaps["kind"] = [classify_gap(pd.Timestamp(p), sec) for p, sec in zip(gaps.prev, gaps.secs)]
    print(f"\ngaps > 10 min: {len(gaps)}  by kind: {gaps.kind.value_counts().to_dict()}")
    other = gaps[gaps.kind == "other"].sort_values("secs", ascending=False)
    print("largest 'other' gaps (h):")
    for _, r in other.head(12).iterrows():
        print(f"  {pd.Timestamp(r.prev):%Y-%m-%d %H:%M} -> {r.time:%Y-%m-%d %H:%M}  {r.secs/3600:6.2f} h")

    # derive M1 and compare on high/low
    m = s5.set_index("time").resample("1min", label="left", closed="left").agg(o=("o", "first"), h=("h", "max"), l=("l", "min"), c=("c", "last"), n=("c", "size"))
    m = m[m.n > 0]
    m1 = pd.read_parquet(a.m1)
    m1["time"] = pd.to_datetime(m1["time"], utc=True)
    m1 = m1.set_index("time")
    both = m.index.intersection(m1.index)
    only_s5, only_m1 = m.index.difference(m1.index), m1.index.difference(m.index)
    x, y = m.loc[both], m1.loc[both]
    eq_h = np.isclose(x.h.values, y.high.values, atol=0.0051); eq_l = np.isclose(x.l.values, y.low.values, atol=0.0051)
    print(f"\nM1 derived from S5: {len(m):,} minutes | M1 cache: {len(m1):,} | overlap: {len(both):,}")
    print(f"  high exact: {eq_h.mean()*100:.3f}%  low exact: {eq_l.mean()*100:.3f}%  both: {(eq_h & eq_l).mean()*100:.3f}%")
    dh = np.abs(x.h.values - y.high.values); dl = np.abs(x.l.values - y.low.values)
    print(f"  |dh| p50/p99/max: {np.percentile(dh,50):.3f}/{np.percentile(dh,99):.3f}/{dh.max():.3f}   |dl| p50/p99/max: {np.percentile(dl,50):.3f}/{np.percentile(dl,99):.3f}/{dl.max():.3f}")
    print(f"  minutes only in S5-derived: {len(only_s5):,}   only in M1 cache: {len(only_m1):,}")
    if len(only_m1):
        om = pd.Series(only_m1).dt.strftime("%Y-%m").value_counts().sort_index()
        print("  M1-cache minutes missing from S5, by month:\n" + om.to_string())

    sp = (s5.ask_c - s5.bid_c)
    byh = sp.groupby(s5.time.dt.hour).agg(median="median", p95=lambda v: v.quantile(0.95))
    print("\nspread (ask-bid) by UTC hour:\n" + byh.round(3).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
