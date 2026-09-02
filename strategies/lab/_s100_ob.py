"""Does S100 improve with the OB entry model disabled?

The S100 sub-agent's decomposition of the baseline trade list found OB edge-retest is the
only one of the three models negative in BOTH halves and over the full period
(TRAIN PF 0.997 / TEST 0.957 / full 0.976 over 1274 trades), while FVG and RSI trade off
between halves. It correctly refused to call that shippable: OB shares the single-slot
pending-retrace machine with FVG, so removing it changes which LATER signals can fire — an
effect a filtered CSV cannot measure and only a filtered replay can.

This is that replay. OB is disabled by patching `_OB_DISP` so high that the arming test
`body >= _OB_DISP * atr` can never pass; FVG and RSI are untouched.

Why this and not more S94 work: S100 is the roster's biggest strategy and its only clearly
profitable one live (+$814). Removing dead weight from the earner is worth more than tuning
the marginal members.

Resumable: each arm appends to lab/results/s100_ob.log on completion and is skipped on
restart. Known reference, same window/split, OB enabled, cost 0.45:
    TRAIN n=2276 pts=+12.6 pf=1.002 | TEST n=1656 pts=+152.2 pf=1.027
"""
import os
import sys

sys.path.insert(0, "E:/Projects/Kronos/KronosStrategies/strategies")

import pandas as pd

from lab.harness import Cfg, load_bars, replay

SPLIT = pd.Timestamp("2026-02-01", tz="UTC")
LOG = "E:/Projects/Kronos/KronosStrategies/strategies/lab/results/s100_ob.log"
OB_OFF = 1e9   # body >= _OB_DISP * atr can never hold -> OB never arms


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
        models = "/".join(sorted(set(g.reason.str.replace(r'_(LONG|SHORT)$', '', regex=True))))
        out[lab] = (f"n={len(g):<5} pts={g.pts.sum():>8.1f} "
                    f"pf={(w.pts.sum()/gl if gl > 0 else float('inf')):.3f} "
                    f"wr={100*len(w)/len(g):.1f} dd={float((eq-eq.cummax()).min()):.1f} "
                    f"[{models}]")
    return out


done = set()
if os.path.exists(LOG):
    for line in open(LOG, encoding="utf-8"):
        if line.startswith("ARM "):
            done.add(line.split("|", 1)[0].strip())

ARMS = [
    ("ARM obOFF_c0.45", dict(cost_pts=0.45, patch={"_OB_DISP": OB_OFF})),
    ("ARM obOFF_c0.80", dict(cost_pts=0.80, patch={"_OB_DISP": OB_OFF})),
    ("ARM obON_c0.80",  dict(cost_pts=0.80)),
]

bars = load_bars()
print("bars loaded; already done:", sorted(done), flush=True)
with open(LOG, "a", encoding="utf-8") as fh:
    for label, kw in ARMS:
        if label in done:
            print("skip:", label, flush=True)
            continue
        res = replay("s100_m3_combo", bars, start="2025-01-05", end="2026-08-12",
                     cfg=Cfg(**kw))
        h = halves(res)
        line = f"{label} | TRAIN {h['train']} | TEST {h['test']}"
        print(line, flush=True)
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())
