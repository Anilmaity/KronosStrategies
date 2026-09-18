"""score.py -- bars 1-7 of PROTOCOL.md for every r2_c03 arm.

mid-M1 numbers come from <arm>.trades.parquet (harness at cost 0.75; 1.00 = pts - 0.25, which is
exact because the harness charges cost as a constant at exit). quote-S5 numbers come from
<arm>.quote.parquet (cost-free quote_pts0, minus 0.45 / 0.70). Bars 1-5 follow
lab/tools/campaign_score.py (imported for gold_monthly / monthly_pts); bar 7 compares each arm's
quote-S5 @0.70 TEST PF AND TEST points with the incumbent H0_base. Bar 6 (plateau) is read from
the printed grid, per hypothesis, in REPORT.md.

    cd strategies && ../.venv/bin/python lab/research_s5/r2_c03/score.py [--csv results/SCORE.csv]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
STRAT = HERE.parents[2]
sys.path.insert(0, str(STRAT))
from lab.tools.campaign_score import gold_monthly, monthly_pts   # noqa: E402

RES = HERE / "results"
CACHE = STRAT / "backtest" / "results" / "bars_cache_2y"
SPLIT = pd.Timestamp("2025-12-01", tz="UTC")
MID_BASE, MID_STRESS = 0.75, 1.00
Q_BASE, Q_STRESS = 0.45, 0.70
INCUMBENT = "H0_base"


def pf(v) -> float:
    v = np.asarray(v, float)
    gw, gl = v[v > 0].sum(), -v[v <= 0].sum()
    return float(gw / gl) if gl > 0 else float("inf")


def score_arm(label: str, gold: pd.Series) -> dict:
    row = json.loads((RES / f"{label}.json").read_text())
    tr = pd.read_parquet(RES / f"{label}.trades.parquet")
    out = dict(label=label, n=len(tr))
    if not len(tr):
        return out
    assert abs(row["cost"] - MID_BASE) < 1e-9, label
    et = pd.to_datetime(tr.entry_time, utc=True)
    tr = tr.assign(pts_s=tr.pts - (MID_STRESS - MID_BASE))
    train, test = tr[et < SPLIT], tr[et >= SPLIT]
    out.update(test_n=len(test), train_n=len(train),
               mid_train_pf=round(pf(train.pts), 3), mid_test_pf=round(pf(test.pts), 3),
               mid_test_pf_s=round(pf(test.pts_s), 3), mid_test_pts_s=round(float(test.pts_s.sum()), 1),
               mid_all_pf_s=round(pf(tr.pts_s), 3), mid_all_pts_s=round(float(tr.pts_s.sum()), 1),
               median_risk=round(float(tr.risk.median()), 2), wr=round(100 * (tr.pts > 0).mean(), 1))
    tm = monthly_pts(test)
    pos_share = float((tm > 0).mean()) if len(tm) else 0.0
    net = float(tm.sum()) if len(tm) else 0.0
    max_share = float(tm.max() / net) if net > 0 else float("inf")
    fm = monthly_pts(tr)
    j = pd.concat([fm.rename("pts"), gold], axis=1).dropna()
    up, dn = j[j.gold_pct > 0], j[j.gold_pct <= 0]
    corr = float(np.corrcoef(j.pts, j.gold_pct)[0, 1]) if len(j) >= 6 and j.pts.std() > 0 else 0.0
    out.update(test_pos_months=round(pos_share, 2), test_max_month_share=round(max_share, 2) if np.isfinite(max_share) else None,
               gold_corr=round(corr, 2), up_pts=round(float(up.pts.sum()), 1), dn_pts=round(float(dn.pts.sum()), 1))
    out["bar1"] = len(test) >= 40
    out["bar2"] = out["mid_test_pf"] > 1.0 and out["mid_test_pf_s"] > 1.0
    out["bar3"] = out["mid_train_pf"] > 0.9
    out["bar4"] = pos_share >= 0.55 and max_share <= 0.5
    out["bar5"] = bool((up.pts.sum() > 0 and dn.pts.sum() > 0) or abs(corr) < 0.4)
    # quote-S5
    qp = RES / f"{label}.quote.parquet"
    if qp.exists():
        q = pd.read_parquet(qp)
        assert len(q) == len(tr), label
        qet = pd.to_datetime(q.entry_time, utc=True)
        for tag, c in (("b", Q_BASE), ("s", Q_STRESS)):
            v = q.quote_pts0 - c
            out[f"q_all_pf_{tag}"] = round(pf(v), 3); out[f"q_all_pts_{tag}"] = round(float(v.sum()), 1)
            out[f"q_train_pf_{tag}"] = round(pf(v[qet < SPLIT]), 3); out[f"q_train_pts_{tag}"] = round(float(v[qet < SPLIT].sum()), 1)
            out[f"q_test_pf_{tag}"] = round(pf(v[qet >= SPLIT]), 3); out[f"q_test_pts_{tag}"] = round(float(v[qet >= SPLIT].sum()), 1)
        out["q_wr_s"] = round(100 * ((q.quote_pts0 - Q_STRESS) > 0).mean(), 1)
        out["q_time_exits"] = int((q.quote_outcome == "TIME").sum())
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(RES / "SCORE.csv"))
    a = ap.parse_args()
    gold = gold_monthly(CACHE)
    labels = sorted(p.name[:-5] for p in RES.glob("*.json") if not p.name.endswith(".error.json"))
    rows = [score_arm(l, gold) for l in labels]
    df = pd.DataFrame(rows)
    if INCUMBENT in set(df.label) and "q_test_pf_s" in df:
        inc = df[df.label == INCUMBENT].iloc[0]
        df["bar7_pf"] = df.q_test_pf_s > inc.q_test_pf_s
        df["bar7_pts"] = df.q_test_pts_s > inc.q_test_pts_s
        df["bar7"] = df.bar7_pf & df.bar7_pts
        df["bars1to5"] = df[["bar1", "bar2", "bar3", "bar4", "bar5"]].all(axis=1)
    df.to_csv(a.csv, index=False)
    cols = [c for c in ("label", "n", "test_n", "mid_train_pf", "mid_test_pf", "mid_test_pf_s", "mid_test_pts_s",
                        "q_train_pf_s", "q_train_pts_s", "q_test_pf_s", "q_test_pts_s", "q_test_pf_b",
                        "median_risk", "test_pos_months", "gold_corr", "bars1to5", "bar7") if c in df]
    with pd.option_context("display.width", 250, "display.max_columns", 40, "display.max_rows", 100):
        print(df[cols].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
