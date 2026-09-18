"""run_events.py -- build levels, detect every excursion (sweep or break), label reversals.

Writes results/events.parquet and prints coverage + counts. Run from strategies/:
    ../.venv/bin/python lab/research_s5/sweeps/run_events.py | tee lab/research_s5/sweeps/results/run_events.log
"""
from __future__ import annotations
import sys, time
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sweeps_lib as sl

OUT = Path(__file__).resolve().parent / "results"


def main():
    t0 = time.time()
    s = sl.load()
    print(f"S5 bars {s.n:,}  {s.dt[0]} -> {s.dt[-1]}  load {time.time()-t0:.0f}s")
    lv = sl.build_levels(s)
    win = (s.dt >= sl.WINDOW[0]) & (s.dt < sl.WINDOW[1])
    print("\nlevel coverage (share of in-window bars with the level defined):")
    for k, (H, L) in lv.items():
        print(f"  {k:5s} {np.isfinite(H[win]).mean()*100:5.1f}%   median range {np.nanmedian((H-L)[win]):.2f} pts")
    evs = []
    for k in sl.LEVEL_TYPES:
        H, L = lv[k]
        for side in ("high", "low"):
            t1 = time.time()
            e = sl.find_events(s, H, L, side, k)
            print(f"  events {k:5s} {side:4s}: {len(e):7,} raw excursions  ({time.time()-t1:.1f}s)")
            evs.append(e)
    ev = pd.concat(evs, ignore_index=True)
    ev = sl.apply_window(ev)
    ev = ev.sort_values("i").reset_index(drop=True)
    ev = sl.label_reversal(s, ev)
    ev.to_parquet(OUT / "events.parquet", index=False)
    print(f"\nin-window excursions: {len(ev):,}  (TRAIN {int((ev.split=='TRAIN').sum()):,} / TEST {int((ev.split=='TEST').sum()):,})")
    print("\nsweep vs break share by level type and T (share of excursions reclaimed within T):")
    rows = []
    for k in sl.LEVEL_TYPES:
        e = ev[ev.level_type == k]
        r = dict(level_type=k, excursions=len(e), reclaim_4h=int(e.time_beyond.notna().sum()))
        for T in sl.T_GRID:
            r[f"sweep_T{T}"] = int((e.time_beyond <= T).sum())
            r[f"share_T{T}"] = round(float((e.time_beyond <= T).mean()), 3)
        rows.append(r)
    print(pd.DataFrame(rows).to_string(index=False))
    sw = ev[ev.time_beyond <= 300]
    print("\nSWEEP(300) anatomy quantiles by level type (depth pts, depth in spreads, time beyond s):")
    q = sw.groupby("level_type").agg(n=("depth", "size"),
                                     d_p25=("depth", lambda v: v.quantile(.25)), d_p50=("depth", "median"), d_p75=("depth", lambda v: v.quantile(.75)), d_p95=("depth", lambda v: v.quantile(.95)),
                                     ds_p50=("depth_spr", "median"), ds_p90=("depth_spr", lambda v: v.quantile(.9)),
                                     tb_p50=("time_beyond", "median"), tb_p75=("time_beyond", lambda v: v.quantile(.75)), tb_p90=("time_beyond", lambda v: v.quantile(.9)),
                                     tb0_share=("time_beyond", lambda v: (v == 0).mean()))
    print(q.round(3).to_string())
    print("\nSWEEP(300) by reclaim-speed bucket (pooled):")
    b = pd.cut(sw.time_beyond, [-1, 0, 30, 120, 300], labels=["0s", "5-30s", "35-120s", "125-300s"])
    print(sw.groupby(b, observed=True).agg(n=("depth", "size"), depth_p50=("depth", "median"), depth_spr_p50=("depth_spr", "median")).round(3).to_string())
    print(f"\ntotal {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
