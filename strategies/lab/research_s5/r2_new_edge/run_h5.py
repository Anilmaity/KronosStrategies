"""run_h5.py -- H5: TTrades daily-candle gate overlay on c03 / s14 (PROTOCOL §5).

Stage 1 (`--run`): six harness arms via lab.sweep.run_campaign (1 worker, in-process after a BrokenProcessPool with 2): per strategy
{incumbent, G1, G2} at mid-M1 cost 0.75, win_15m=200 for all (the incumbent at the same
window is the bar-7 reference). 1.00 books are derived (cost is additive per trade).
Stage 2 (`--score`): bars 1-5 + bar 7 vs the incumbent at 1.00 on TEST PF AND points,
TRAIN-justified; quote-S5 only for an arm that passes bar 7. Output: results/h5_output.txt.
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np
import pandas as pd

from lab.research_s5.r2_new_edge import common as C
from lab.research_s5.po3_judas import po3_common as P

os.environ["LAB_BARS_CACHE"] = str(C.STRAT / "backtest" / "results" / "bars_cache_2y")
from lab.harness import Cfg                     # noqa: E402
from lab.sweep import Arm, run_campaign         # noqa: E402
from lab.research_s5.r2_new_edge import r2_htf_gate as G   # noqa: E402

NAME = "r2_h5_htf_gate"
OUT = C.RESULTS / "h5_campaign"
W = dict(start="2024-09-01", end="2026-09-17", split="2025-12-01")
STRATS = {"c03": "c03_fvg_fill", "s14": "s14_ob_mit_bias"}
GATES = {"G1": {"c03": G.c03_g1, "s14": G.s14_g1}, "G2": {"c03": G.c03_g2, "s14": G.s14_g2}}


def arms() -> list[Arm]:
    out = []
    for k, mod in STRATS.items():
        out.append(Arm(f"{k}_incumbent", mod, Cfg(cost_pts=0.75, win_15m=200), **W))
        for g, fns in GATES.items():
            out.append(Arm(f"{k}_{g}", mod, Cfg(cost_pts=0.75, win_15m=200, patch={"get_signal": fns[k]}), **W))
    return out


def stats(df: pd.DataFrame, cost: float) -> dict:
    v = df["pts"].to_numpy() + 0.75 - cost
    return dict(n=len(df), pf=round(P.pf(v), 3), pts=round(float(v.sum()), 1), wr=round(100 * float((v > 0).mean()), 1))


def score() -> None:
    log = C.Tee(C.RESULTS / "h5_output.txt")
    rows = {}
    for a in arms():
        p = OUT / NAME / f"{a.label}.trades.parquet"
        if not p.exists():
            log(f"  {a.label}: missing"); continue
        d = pd.read_parquet(p)
        d["entry_time"] = pd.to_datetime(d["entry_time"], utc=True)
        rows[a.label] = d
    log("=== H5 arms, mid-M1 (harness), TRAIN / TEST at 0.75 and 1.00 ===")
    tab = []
    for label, d in rows.items():
        tr, te = d[d.entry_time < C.SPLIT], d[d.entry_time >= C.SPLIT]
        r = dict(arm=label)
        for nm, sub in (("train", tr), ("test", te)):
            for c in (0.75, 1.00):
                s = stats(sub, c)
                r[f"{nm}_n"] = s["n"]; r[f"{nm}_pf_{c:.2f}"] = s["pf"]; r[f"{nm}_pts_{c:.2f}"] = s["pts"]; r[f"{nm}_wr_{c:.2f}"] = s["wr"]
        # bars 1-5 at 0.75 / 1.00 using the shared bars() (raw_pts = pts + 0.75)
        dd = d.assign(raw_pts=d["pts"] + 0.75)
        b = P.bars(dd, 0.75, 1.00)
        r.update(bars=f"{b['verdict']} {b['bars_passed']}/6", monthly=b["test_pos_share"], max_share=b["test_max_share"], corr=b["gold_corr"])
        tab.append(r)
        log(f"  {label:<16} TRAIN n={r['train_n']:<5} PF {r['train_pf_0.75']:<6}/{r['train_pf_1.00']:<6} pts {r['train_pts_0.75']:>8}/{r['train_pts_1.00']:>8} | "
            f"TEST n={r['test_n']:<5} PF {r['test_pf_0.75']:<6}/{r['test_pf_1.00']:<6} pts {r['test_pts_0.75']:>8}/{r['test_pts_1.00']:>8} WR {r['test_wr_1.00']}% | bars {r['bars']} (months+ {r['monthly']}, max {r['max_share']}, corr {r['corr']})")
    pd.DataFrame(tab).to_csv(C.RESULTS / "h5_table.csv", index=False)
    log("\n=== bar 7: gated arm vs incumbent at stress 1.00 on TEST PF AND points; TRAIN-justified (same ordering on TRAIN) ===")
    for k in STRATS:
        inc = next((r for r in tab if r["arm"] == f"{k}_incumbent"), None)
        if inc is None:
            continue
        for g in GATES:
            r = next((r for r in tab if r["arm"] == f"{k}_{g}"), None)
            if r is None:
                continue
            b7 = r["test_pf_1.00"] > inc["test_pf_1.00"] and r["test_pts_1.00"] > inc["test_pts_1.00"]
            tj = r["train_pf_1.00"] > inc["train_pf_1.00"] and r["train_pts_1.00"] > inc["train_pts_1.00"]
            log(f"  {k} {g}: TEST PF {r['test_pf_1.00']} vs {inc['test_pf_1.00']}, pts {r['test_pts_1.00']} vs {inc['test_pts_1.00']} -> bar7 {'PASS' if b7 else 'FAIL'}; "
                f"TRAIN PF {r['train_pf_1.00']} vs {inc['train_pf_1.00']}, pts {r['train_pts_1.00']} vs {inc['train_pts_1.00']} -> TRAIN-justified {tj}; "
                f"trades kept {r['test_n']}/{inc['test_n']} TEST")
    # what the gates removed: the incumbent's trades not in the gated arm (record only; the gated arm was re-run, not filtered)
    log("\n=== record: side mix of the gated arms (TEST) ===")
    for label, d in rows.items():
        te = d[d.entry_time >= C.SPLIT]
        log(f"  {label:<16} TEST sides {te.side.value_counts().to_dict()}")


def main() -> None:
    if "--score" in sys.argv:
        score(); return
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    only = [a for a in sys.argv[1:] if a in STRATS]          # e.g. `c03` / `s14`: run one strategy's arms
    sel = [a for a in arms() if not only or a.label.split("_")[0] in only]
    df = run_campaign(NAME, sel, out_dir=OUT, workers=1, progress=True)
    print(df[["label", "status", "n", "pf", "pts", "elapsed_s"]].to_string(index=False))
    print(f"campaign done in {time.time()-t0:.0f}s")
    score()


if __name__ == "__main__":
    main()
