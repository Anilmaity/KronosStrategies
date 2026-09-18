"""Same-harness control arm for the S100 OB-off experiment.

`_s100_ob.py` compares its OB-off arms against the stored baseline in
`results/s100_m3_combo_base.json` (TRAIN n=2276 pf=1.002 / TEST n=1656 pf=1.027).
That baseline was written at 03:32 on 2026-09-02; `harness.py` was last modified at
05:18, i.e. AFTER it -- the warm-up fix (i0 must satisfy every frame, not just win_1m)
and the env-restore fix both landed in between. The window and split are identical
(2025-01-05..2026-08-12, split 2026-02-01), so only the harness version differs, but
that is enough to make "OB-off vs stored baseline" a cross-version comparison.

This runs the OB-ON arm at cost 0.45 under the CURRENT harness so the 0.45 comparison
is same-code, the way `_s100_ob.py`'s obON_c0.80 arm already makes the 0.80 comparison
same-code. Separate log file so it can run concurrently with `_s100_ob.py` without two
processes appending to one file.
"""
import os
import sys

sys.path.insert(0, "E:/Projects/Kronos/KronosStrategies/strategies")

import pandas as pd

from lab.harness import Cfg, load_bars, replay

SPLIT = pd.Timestamp("2026-02-01", tz="UTC")
LOG = "E:/Projects/Kronos/KronosStrategies/strategies/lab/results/s100_ob_control.log"


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

ARMS = [("ARM obON_c0.45", dict(cost_pts=0.45))]

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
