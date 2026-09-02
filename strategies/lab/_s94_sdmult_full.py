"""S94 _SD_MULT on the FULL 19.5-month window.

The sub-agent found _SD_MULT 2.5 and 3.0 both clear all five pre-registered bars, but on a
5-MONTH window only (TRAIN 2025-12..2026-02 / TEST 2026-02..2026-05) forced by CPU
contention. It flagged, correctly, that S94 is precisely the strategy whose published
result did NOT survive a wider sample -- so shipping a narrow-window result here would
repeat the exact mistake this campaign just diagnosed.

This is that confirmation. Same split as the rest of the campaign:
TRAIN 2025-01-05..2026-02-01, TEST 2026-02-01..2026-08-12.
Known reference: baseline (_SD_MULT=2.0) full window @0.45 = TRAIN pf 0.843 / TEST pf 0.858.
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

bars = load_bars(); print("bars loaded", flush=True)
for cost in (0.45, 0.80):
    for sd in (2.0, 2.5, 3.0):
        r = replay("s94_sweep_reversal", bars, start="2025-01-05", end="2026-08-12",
                   cfg=Cfg(cost_pts=cost, patch={"_SD_MULT": sd}))
        h = halves(r)
        print(f"  SD={sd} c={cost}  TRAIN {h['train']}\n  {'':<16} TEST  {h['test']}", flush=True)
