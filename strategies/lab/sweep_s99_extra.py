"""lab/sweep_s99_extra.py -- ad hoc sweep script for the S99 MSS+FVG optimization
campaign (2026-09-02). Covers dimensions that need module-constant patching
(_TP_R, _HOURS, _SWEEP_N, _RETRACE_W, _MAX_HOLD_MIN) which lab/run.py's CLI
does not expose. Loads bars ONCE and reuses them across all arms.

Also computes the raw (pre-cost) win-rate-by-stop-distance-bucket table for
S99, requested by the campaign coordinator as a cross-check against the S93
finding that the "tight stop underperforms" effect is pure cost/risk arithmetic
rather than a fitted win-rate effect.

Run from strategies/: ../.venv/Scripts/python.exe -m lab.sweep_s99_extra
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from lab.harness import Cfg, load_bars, replay  # noqa: E402

OUT = Path(__file__).resolve().parent / "results"
OUT.mkdir(exist_ok=True)

FULL_START, FULL_END = "2025-01-05", "2026-08-12"
SPLIT = "2026-02-01"
STRAT = "s99_mss_fvg"


def half(df: pd.DataFrame, lo=None, hi=None) -> dict:
    if len(df) == 0:
        return {"n": 0, "pts": 0.0, "pf": None, "wr": 0.0, "maxdd_pts": 0.0}
    m = pd.Series(True, index=df.index)
    if lo is not None:
        m &= df.entry_time >= pd.Timestamp(lo, tz="UTC")
    if hi is not None:
        m &= df.entry_time < pd.Timestamp(hi, tz="UTC")
    d = df[m]
    if len(d) == 0:
        return {"n": 0, "pts": 0.0, "pf": None, "wr": 0.0, "maxdd_pts": 0.0}
    w, l = d[d.pts > 0], d[d.pts <= 0]
    gl = -l.pts.sum()
    eq = d.pts.cumsum()
    return {"n": int(len(d)), "pts": round(float(d.pts.sum()), 1),
            "pf": round(float(w.pts.sum() / gl), 3) if gl > 0 else None,
            "wr": round(100.0 * len(w) / len(d), 1),
            "maxdd_pts": round(float((eq - eq.cummax()).min()), 1)}


def run_arm(bars, label, patch=None, cost=0.45, min_sl=1.5, block_hours=()):
    cfg = Cfg(cost_pts=cost, min_sl_dist_pts=min_sl, block_hours=block_hours,
              patch=patch or {})
    res = replay(STRAT, bars, start=FULL_START, end=FULL_END, cfg=cfg)
    df = res["trades"]
    rec = dict(label=label, cost=cost, min_sl=min_sl, patch=patch or {},
               full_n=res["n"], full_pts=res["pts"], full_pf=res["pf"],
               train=half(df, FULL_START, SPLIT), test=half(df, SPLIT, FULL_END))
    print(f"{label:<28} cost={cost:<5} "
          f"TRAIN n={rec['train']['n']:<4} pts={rec['train']['pts']:>8} "
          f"pf={rec['train']['pf']} wr={rec['train']['wr']}  |  "
          f"TEST n={rec['test']['n']:<4} pts={rec['test']['pts']:>8} "
          f"pf={rec['test']['pf']} wr={rec['test']['wr']} dd={rec['test']['maxdd_pts']}",
          flush=True)
    return rec, df


def main():
    bars = load_bars()
    results = []

    # ---- baseline (also used for the raw-WR-by-stop-bucket analysis) ----
    base_rec, base_df = run_arm(bars, "BASELINE")
    results.append(base_rec)

    # ---- raw (pre-cost) WR by stop-distance bucket, S99, full period ----
    d = base_df.copy()
    d["raw_pts"] = d["pts"] + base_rec["cost"]
    edges = [0, 2, 3, 4, 6, 9, float("inf")]
    labels = ["<2", "2-3", "3-4", "4-6", "6-9", "9+"]
    d["bucket"] = pd.cut(d["risk"], bins=edges, labels=labels, right=False)
    print("\n-- S99 raw (pre-cost) WR by stop-distance bucket (baseline, full period) --")
    bucket_rows = []
    for lb in labels:
        sub = d[d["bucket"] == lb]
        if len(sub) == 0:
            print(f"  {lb:<5} n=0")
            continue
        raw_wr = 100.0 * (sub["raw_pts"] > 0).mean()
        net_wr = 100.0 * (sub["pts"] > 0).mean()
        avg_risk = sub["risk"].mean()
        cost_over_risk = 100.0 * base_rec["cost"] / avg_risk if avg_risk > 0 else float("nan")
        w, l = sub[sub.pts > 0], sub[sub.pts <= 0]
        gl = -l.pts.sum()
        pf = w.pts.sum() / gl if gl > 0 else float("inf")
        row = dict(bucket=lb, n=int(len(sub)), avg_risk=round(float(avg_risk), 2),
                   cost_over_risk_pct=round(cost_over_risk, 1),
                   raw_wr=round(raw_wr, 1), net_wr=round(net_wr, 1),
                   net_pf=round(float(pf), 3), net_pts=round(float(sub["pts"].sum()), 1))
        bucket_rows.append(row)
        print(f"  {lb:<5} n={row['n']:<4} avgRisk={row['avg_risk']:<6} "
              f"c/R%={row['cost_over_risk_pct']:<6} rawWR={row['raw_wr']:<6} "
              f"netWR={row['net_wr']:<6} netPF={row['net_pf']:<6} netPts={row['net_pts']}")
    results.append({"label": "RAW_WR_BUCKETS", "rows": bucket_rows})

    # ================= C. TP_R (PROMOTED TOP PRIORITY per coordinator) =================
    print("\n== C1. _TP_R sweep (screen @ cost 0.45, stress @ cost 0.80) ==")
    for tp in (2.0, 2.5, 3.0, 3.5):
        for cost in (0.45, 0.80):
            rec, _ = run_arm(bars, f"TP_R={tp}", patch={"_TP_R": tp}, cost=cost)
            results.append(rec)

    # ================= B. Session hours =================
    print("\n== B. _HOURS variants (screen @ cost 0.45) ==")
    hours_arms = {
        "HOURS_drop12": tuple(h for h in range(6, 16) if h != 12),
        "HOURS_9_13_14_15": (9, 13, 14, 15),
        "HOURS_7_15": tuple(range(7, 16)),
    }
    for label, hrs in hours_arms.items():
        rec, _ = run_arm(bars, label, patch={"_HOURS": hrs}, cost=0.45)
        results.append(rec)

    # ================= C2. _SWEEP_N =================
    print("\n== C2. _SWEEP_N sweep (screen @ cost 0.45) ==")
    for sn in (24, 96):
        rec, _ = run_arm(bars, f"SWEEP_N={sn}", patch={"_SWEEP_N": sn}, cost=0.45)
        results.append(rec)

    # ================= D. _RETRACE_W =================
    print("\n== D1. _RETRACE_W sweep (screen @ cost 0.45) ==")
    for rw in (12, 36):
        rec, _ = run_arm(bars, f"RETRACE_W={rw}", patch={"_RETRACE_W": rw}, cost=0.45)
        results.append(rec)

    # ================= D. _MAX_HOLD_MIN =================
    print("\n== D2. _MAX_HOLD_MIN sweep (screen @ cost 0.45) ==")
    for mh in (120, 240):
        rec, _ = run_arm(bars, f"MAX_HOLD_MIN={mh}", patch={"_MAX_HOLD_MIN": mh}, cost=0.45)
        results.append(rec)

    p = OUT / f"{STRAT}_extra_sweep.json"
    p.write_text(json.dumps(results, indent=2, default=str))
    print("\nwrote", p)


if __name__ == "__main__":
    main()
