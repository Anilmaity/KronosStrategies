"""run_drift.py -- POST-HOC descriptive (added after Q-B results, logged in PROTOCOL.md):
signed forward mid drift after the reclaim bar (fade direction positive) at 1/5/15/60 min,
vs matched random bars (same UTC hour, +/-30 d). No stops, no costs -- an information test
independent of stop geometry. Not a tradeable claim.
    ../.venv/bin/python lab/research_s5/sweeps/run_drift.py | tee lab/research_s5/sweeps/results/run_drift.log
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sweeps_lib as sl

OUT = Path(__file__).resolve().parent / "results"
HORIZ = (60, 300, 900, 3600)


def fwd(s: sl.S5, idx: np.ndarray, sgn: np.ndarray, h: int) -> np.ndarray:
    k = np.searchsorted(s.t, s.t[idx] + h, side="right") - 1
    k = np.minimum(k, s.n - 1)
    return sgn * (s.c[k] - s.c[idx])


def main():
    s = sl.load()
    ev = pd.read_parquet(OUT / "events.parquet")
    sw = ev[ev.time_beyond <= 300].copy()
    sw["speed"] = pd.cut(sw.time_beyond, [-1, 0, 30, 120, 300], labels=["0s", "5-30s", "35-120s", "125-300s"])
    sw["depth_ter"] = pd.qcut(sw.depth, 3, labels=["D-low", "D-mid", "D-high"])
    pools = sl._hour_pools(s)
    rng = np.random.default_rng(0)
    j = sw.j.to_numpy(); sgn = np.where(sw.side == "high", -1.0, 1.0)   # fade direction: short after high sweep
    # matched random bar per event
    rj = np.empty(len(sw), np.int64)
    for r, (jj, hh) in enumerate(zip(j, sw.hod.to_numpy())):
        pool = pools[int(hh)]
        lo = np.searchsorted(s.t[pool], s.t[jj] - 30 * 86400); hi = np.searchsorted(s.t[pool], s.t[jj] + 30 * 86400)
        rj[r] = pool[rng.integers(lo, hi)]
    rows = []
    for h in HORIZ:
        sw[f"d{h}"] = fwd(s, j, sgn, h); sw[f"r{h}"] = fwd(s, rj, sgn, h)
    for grp_name, key in (("level_type", "level_type"), ("speed", "speed"), ("depth_ter", "depth_ter"), ("split", "split")):
        for gv, g in sw.groupby(key, observed=True):
            for h in HORIZ:
                d, lo, hi = sl.boot_diff_ci(g[f"d{h}"].to_numpy(), g[f"r{h}"].to_numpy(), n_boot=1000)
                rows.append(dict(group=grp_name, value=str(gv), horizon_s=h, n=len(g), drift_real=round(float(g[f"d{h}"].mean()), 3),
                                 drift_rand=round(float(g[f"r{h}"].mean()), 3), diff=round(d, 3), ci_lo=round(lo, 3), ci_hi=round(hi, 3),
                                 real_pos_share=round(float((g[f"d{h}"] > 0).mean()), 3)))
    for h in HORIZ:
        d, lo, hi = sl.boot_diff_ci(sw[f"d{h}"].to_numpy(), sw[f"r{h}"].to_numpy(), n_boot=1000)
        rows.append(dict(group="ALL", value="ALL", horizon_s=h, n=len(sw), drift_real=round(float(sw[f"d{h}"].mean()), 3),
                         drift_rand=round(float(sw[f"r{h}"].mean()), 3), diff=round(d, 3), ci_lo=round(lo, 3), ci_hi=round(hi, 3),
                         real_pos_share=round(float((sw[f"d{h}"] > 0).mean()), 3)))
    df = pd.DataFrame(rows); df.to_csv(OUT / "drift_after_reclaim.csv", index=False)
    with pd.option_context("display.width", 200, "display.max_rows", 200):
        print("signed mid drift after the reclaim bar close (fade direction +), pts, vs matched random bars; bootstrap 95% CI on the difference")
        print(df.to_string(index=False))


if __name__ == "__main__":
    main()
