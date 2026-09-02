"""S100 hour-restriction arms, run by the orchestrator after the S100 sub-agent's own
sweep appears to have died. Highest-value open question in the campaign: S100 is the
biggest and only clearly profitable roster member, and its live record shows hours 1-3
losing -$380 at ~11% win rate against a 30% baseline.

Baseline _HOURS = (1,2,3,4,5,6,7,8,13,14,15).
"""
import sys
sys.path.insert(0, ".")
import pandas as pd
from lab.harness import load_bars, replay, Cfg

SPLIT = pd.Timestamp("2026-02-01", tz="UTC")


def halves(res):
    d = res["trades"]
    out = {}
    for lab, m in (("train", d.entry_time < SPLIT), ("test", d.entry_time >= SPLIT)):
        g = d[m]
        if not len(g):
            out[lab] = "n=0"
            continue
        w = g[g.pts > 0]
        gl = -g[g.pts <= 0].pts.sum()
        eq = g.pts.cumsum()
        out[lab] = (f"n={len(g):<5} pts={g.pts.sum():>8.1f} "
                    f"pf={(w.pts.sum()/gl if gl > 0 else float('inf')):.3f} "
                    f"wr={100*len(w)/len(g):.1f} dd={float((eq-eq.cummax()).min()):.1f}")
    return out


ARMS = [
    ("baseline",     (1, 2, 3, 4, 5, 6, 7, 8, 13, 14, 15)),
    ("drop_1_2_3",   (4, 5, 6, 7, 8, 13, 14, 15)),
    ("drop_1_2_3_4", (5, 6, 7, 8, 13, 14, 15)),
    ("drop_1to5",    (6, 7, 8, 13, 14, 15)),
    ("NY_only",      (13, 14, 15)),
]

bars = load_bars()
print("bars loaded", flush=True)
for cost in (0.45, 0.80):
    print("=" * 78, flush=True)
    print("cost =", cost, flush=True)
    for name, hrs in ARMS:
        r = replay("s100_m3_combo", bars, start="2025-01-05", end="2026-08-12",
                   cfg=Cfg(cost_pts=cost, patch={"_HOURS": hrs}))
        h = halves(r)
        print(f"  {name:<14} TRAIN {h['train']} | TEST {h['test']}", flush=True)
