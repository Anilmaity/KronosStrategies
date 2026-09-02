"""S94's two open questions, minimal decisive arms, crash-resilient.

Earlier attempts at these were killed twice mid-run and lost everything, so:
  * every arm APPENDS to lab/results/s94_decisive.log the moment it finishes, and
  * arms already present in that log are SKIPPED on restart.
Relaunching after a kill therefore resumes rather than starting over.

Q1 -- does _SD_MULT 2.5/3.0 survive a WIDER window? The sub-agent found it clears all five
      bars but only on TRAIN Dec-Feb / TEST Feb-May, and S94 is precisely the strategy whose
      published number failed to survive a wider sample.

SCOPE COMPROMISE (stated plainly): three attempts at the full 19.5-month window were killed
mid-run without completing a single arm -- S94's 1500-bar M5 window makes it by far the most
expensive replay here. So this runs 2025-11-01 .. 2026-08-12 and judges the TEST half
2026-02-01 .. 2026-08-12: a 6.5-month held-out sample, more than TWICE the agent's 3-month
test half, at roughly half the cost of the full window. This is a genuine widening, NOT the
full-window confirmation originally demanded -- do not describe it as one.
Q2 -- does a break-even stop (the `be=True` that actually produced the published PF 1.82)
      hold up at REALISTIC cost, not the 0.30 the published run used?
"""
import sys, os
sys.path.insert(0, ".")
import pandas as pd
from lab.harness import load_bars, replay, Cfg

SPLIT = pd.Timestamp("2026-02-01", tz="UTC")
LOG = "lab/results/s94_decisive.log"


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


done = set()
if os.path.exists(LOG):
    for line in open(LOG, encoding="utf-8"):
        if line.startswith("ARM "):
            done.add(line.split("|", 1)[0].strip())

# (label, cfg kwargs) -- ordered by decisiveness, most decisive first
ARMS = [
    ("ARM sd2.0_c0.45",  dict(cost_pts=0.45, patch={"_SD_MULT": 2.0})),
    ("ARM sd2.5_c0.45",  dict(cost_pts=0.45, patch={"_SD_MULT": 2.5})),
    ("ARM sd2.5_c0.80",  dict(cost_pts=0.80, patch={"_SD_MULT": 2.5})),
    ("ARM sd3.0_c0.45",  dict(cost_pts=0.45, patch={"_SD_MULT": 3.0})),
    ("ARM be1R_c0.45",   dict(cost_pts=0.45, be_at_r=1.0)),
    ("ARM be1R_c0.80",   dict(cost_pts=0.80, be_at_r=1.0)),
]

bars = load_bars()
print("bars loaded; already done:", sorted(done), flush=True)
with open(LOG, "a", encoding="utf-8") as fh:
    fh.write("# reference: _SD_MULT=2.0 (shipped) full window @0.45 "
             "TRAIN pf=0.843 / TEST pf=0.858\n")
    fh.flush()
    for label, kw in ARMS:
        if label in done:
            print("skip (already done):", label, flush=True)
            continue
        res = replay("s94_sweep_reversal", bars,
                     start="2025-11-01", end="2026-08-12", cfg=Cfg(**kw))
        h = halves(res)
        line = f"{label} | TRAIN {h['train']} | TEST {h['test']}"
        print(line, flush=True)
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())
