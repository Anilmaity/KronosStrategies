"""Does a break-even stop recover S94's published PF 1.82?

validate_oos.py produced 1.82 with simulate(..., be=True, single_pos=False, cost 0.30).
The shipped module has none of that. This isolates each difference in turn, so we can say
WHICH one carries the published number rather than just that a gap exists.
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
        be = f" be_armed={100*g.be_armed.mean():.0f}%" if "be_armed" in g else ""
        out[lab] = (f"n={len(g):<5} pts={g.pts.sum():>8.1f} "
                    f"pf={(w.pts.sum()/gl if gl>0 else float('inf')):.3f} "
                    f"wr={100*len(w)/len(g):.1f} dd={float((eq-eq.cummax()).min()):.1f}{be}")
    return out

ARMS = [
    ("shipped (static, c0.45, mc3)", dict(cost_pts=0.45)),
    ("+ BE at 1R",                   dict(cost_pts=0.45, be_at_r=1.0)),
    ("+ BE, cost 0.30",              dict(cost_pts=0.30, be_at_r=1.0)),
    ("+ BE, c0.30, maxconc 20",      dict(cost_pts=0.30, be_at_r=1.0, max_concurrent=20)),
    ("+ BE at 1R, cost 0.80",        dict(cost_pts=0.80, be_at_r=1.0)),
]
bars = load_bars(); print("bars loaded", flush=True)
for name, kw in ARMS:
    r = replay("s94_sweep_reversal", bars, start="2025-01-05", end="2026-08-12",
               cfg=Cfg(**kw))
    h = halves(r)
    print(f"  {name:<30} TRAIN {h['train']}\n  {'':<30} TEST  {h['test']}", flush=True)
