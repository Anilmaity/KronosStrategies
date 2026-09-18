"""run_qa.py -- Question A: does sweep anatomy separate reversals from continuations?

Reads results/events.parquet. Writes results/qa_labels.csv, results/qa_perm.csv.
    ../.venv/bin/python lab/research_s5/sweeps/run_qa.py | tee lab/research_s5/sweeps/results/run_qa.log
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sweeps_lib as sl

OUT = Path(__file__).resolve().parent / "results"


def main():
    ev = pd.read_parquet(OUT / "events.parquet")
    sw = ev[ev.time_beyond <= 300].copy()
    sw["speed"] = pd.cut(sw.time_beyond, [-1, 0, 30, 120, 300], labels=["0s", "5-30s", "35-120s", "125-300s"])
    sw["depth_ter"] = pd.qcut(sw.depth, 3, labels=["D-low", "D-mid", "D-high"])
    print(f"SWEEP(300) events: {len(sw):,}")

    # label shares
    lab_rows = []
    for lab in ("lab1", "lab2", "labf"):
        for grp, g in [("ALL", sw)] + [(k, sw[sw.level_type == k]) for k in sl.LEVEL_TYPES]:
            vc = g[lab].value_counts()
            n = len(g); nr, nc, nu = int(vc.get("REV", 0)), int(vc.get("CONT", 0)), int(vc.get("UNRESOLVED", 0))
            lab_rows.append(dict(label=lab, group=grp, n=n, REV=nr, CONT=nc, UNRESOLVED=nu,
                                 rev_share_of_resolved=round(nr / max(nr + nc, 1), 3)))
    labdf = pd.DataFrame(lab_rows)
    labdf.to_csv(OUT / "qa_labels.csv", index=False)
    print("\nlabel shares (lab1 = 1xD, lab2 = 2xD, labf = fixed 1.0 pt; REV before revisit of extreme, 4 h horizon):")
    print(labdf.to_string(index=False))

    print("\nREV share (lab1 / labf) by reclaim-speed bucket and by depth tercile -- the geometric confound made visible:")
    for col in ("speed", "depth_ter"):
        g = sw[sw.lab1 != "UNRESOLVED"].groupby(col, observed=True).agg(n=("lab1", "size"), rev1=("lab1", lambda v: (v == "REV").mean()))
        g2 = sw[sw.labf != "UNRESOLVED"].groupby(col, observed=True).agg(n_f=("labf", "size"), revf=("labf", lambda v: (v == "REV").mean()))
        g3 = sw.groupby(col, observed=True).agg(depth_p50=("depth", "median"), tb_p50=("time_beyond", "median"))
        print(pd.concat([g, g2, g3], axis=1).round(3).to_string())

    # permutation tests on difference of medians, REV vs CONT
    rows = []
    for lab in ("lab1", "lab2", "labf"):
        for grp, g in [("ALL", sw)] + [(k, sw[sw.level_type == k]) for k in sl.LEVEL_TYPES]:
            r, c = g[g[lab] == "REV"], g[g[lab] == "CONT"]
            for var, tx in (("log_depth", lambda v: np.log(v.depth.to_numpy())), ("time_beyond", lambda v: v.time_beyond.to_numpy()),
                            ("depth_spr", lambda v: v.depth_spr.to_numpy())):
                d, p = sl.perm_test_median(tx(r), tx(c), n_perm=10000, seed=0)
                rows.append(dict(label=lab, group=grp, var=var, n_rev=len(r), n_cont=len(c),
                                 med_rev=round(float(np.median(tx(r))), 3) if len(r) else None,
                                 med_cont=round(float(np.median(tx(c))), 3) if len(c) else None,
                                 diff_median=round(d, 3), p_perm=round(p, 4)))
    pdf = pd.DataFrame(rows)
    pdf.to_csv(OUT / "qa_perm.csv", index=False)
    print("\npermutation tests (10,000 shuffles, difference of medians, REV minus CONT):")
    with pd.option_context("display.width", 200):
        print(pdf.to_string(index=False))

    # duration within depth terciles (depth held ~constant) for lab1 and labf
    print("\nwithin-depth-tercile: median time_beyond REV vs CONT (lab1, labf), pooled:")
    rows = []
    for lab in ("lab1", "labf"):
        for ter, g in sw.groupby("depth_ter", observed=True):
            r, c = g[g[lab] == "REV"], g[g[lab] == "CONT"]
            d, p = sl.perm_test_median(r.time_beyond.to_numpy(), c.time_beyond.to_numpy(), n_perm=5000, seed=0)
            rows.append(dict(label=lab, tercile=ter, n_rev=len(r), n_cont=len(c), tb_med_rev=float(r.time_beyond.median()),
                             tb_med_cont=float(c.time_beyond.median()), diff=round(d, 2), p_perm=round(p, 4),
                             depth_med_rev=round(float(r.depth.median()), 3), depth_med_cont=round(float(c.depth.median()), 3)))
    tdf = pd.DataFrame(rows)
    tdf.to_csv(OUT / "qa_within_tercile.csv", index=False)
    print(tdf.to_string(index=False))

    # split stability of the fixed-distance REV share
    print("\nREVF share by split and level type (fixed 1.0 pt target vs revisit):")
    g = sw[sw.labf != "UNRESOLVED"].groupby(["level_type", "split"]).agg(n=("labf", "size"), revf=("labf", lambda v: round((v == "REV").mean(), 3)))
    print(g.to_string())


if __name__ == "__main__":
    main()
