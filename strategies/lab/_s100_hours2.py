"""S100 hour arms, trimmed. The 0.45 baseline is already known from
lab/results/s100_m3_combo_base.json (train PF 1.002 / test PF 1.027, n=3932) under
identical Cfg defaults, so recomputing it wastes ~40 min of contended CPU.

Arms kept: the decisive restriction, its stress, a plateau neighbour, and the baseline
at 0.80 (needed as the stress reference).
"""
import sys; sys.path.insert(0, ".")
import pandas as pd
from lab.harness import load_bars, replay, Cfg
SPLIT = pd.Timestamp("2026-02-01", tz="UTC")

def halves(res):
    d = res["trades"]; out = {}
    for lab, m in (("train", d.entry_time < SPLIT), ("test", d.entry_time >= SPLIT)):
        g = d[m]
        if not len(g): out[lab] = "n=0"; continue
        w = g[g.pts > 0]; gl = -g[g.pts <= 0].pts.sum(); eq = g.pts.cumsum()
        out[lab] = (f"n={len(g):<5} pts={g.pts.sum():>8.1f} "
                    f"pf={(w.pts.sum()/gl if gl>0 else float('inf')):.3f} "
                    f"wr={100*len(w)/len(g):.1f} dd={float((eq-eq.cummax()).min()):.1f}")
    return out

BASE = (1,2,3,4,5,6,7,8,13,14,15)
ARMS = [("drop_1_2_3@0.45", (4,5,6,7,8,13,14,15), 0.45),
        ("drop_1to5@0.45",  (6,7,8,13,14,15),     0.45),
        ("baseline@0.80",   BASE,                 0.80),
        ("drop_1_2_3@0.80", (4,5,6,7,8,13,14,15), 0.80)]
bars = load_bars(); print("bars loaded", flush=True)
print("reference: baseline@0.45 = TRAIN n=2276 pf=1.002 | TEST n=1656 pf=1.027", flush=True)
for name, hrs, cost in ARMS:
    r = replay("s100_m3_combo", bars, start="2025-01-05", end="2026-08-12",
               cfg=Cfg(cost_pts=cost, patch={"_HOURS": hrs}))
    h = halves(r)
    print(f"  {name:<18} TRAIN {h['train']} | TEST {h['test']}", flush=True)
