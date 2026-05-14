"""
protocol.py
-----------
Run the full evaluation gauntlet on every strategy in a backtest CSV.

Per-strategy reports:
  - In-sample summary (60% / 20% / 20% split)
  - Walk-forward analysis (anchored, n=6 windows, 70% IS each)
  - Monte Carlo (1000 shuffles)
  - Acceptance verdict against the plan_ thresholds:

    | trades   >= 100         | sample size       |
    | wr%      >= 50          | win rate          |
    | pf       >= 1.3         | profit factor     |
    | max_dd_% <= 25 of pnl   | drawdown          |
    | exp      >= 0.15R       | expectancy (R-multiples)
    | wfe      >= 0.5         | walk-forward eff. |

Usage:
  python backtest/protocol.py results/all_strategies_cache_20260512_1030.csv
"""
from __future__ import annotations

import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from walk_forward import walk_forward, summarize as wf_summarize, _stats
from monte_carlo import monte_carlo

THRESHOLDS = {
    "min_trades":   100,
    "min_wr":       50.0,
    "min_pf":       1.3,
    "max_dd_ratio": 0.25,    # max DD / total PnL <= 0.25 (i.e. PnL >= 4× DD)
    "min_exp_R":    0.15,
    "min_wfe":      0.5,
    "min_consist":  60.0,    # OOS positive in >=60% of windows
}


def _r_multiple(closed: pd.DataFrame) -> float:
    """Approximate average R per trade: pnl / avg loss magnitude."""
    pnl_col = "pnl_pts" if "pnl_pts" in closed.columns else "pnl_points"
    losses = closed[closed["outcome"] == "SL"][pnl_col]
    avg_loss_abs = abs(float(losses.mean())) if len(losses) else 0.0
    if avg_loss_abs == 0:
        return 0.0
    return round(float(closed[pnl_col].mean()) / avg_loss_abs, 3)


def evaluate_strategy(strat_df: pd.DataFrame, strategy: str) -> dict:
    closed = strat_df[strat_df["outcome"].isin(["TP", "SL"])]
    s = _stats(closed)
    s["exp_R"] = _r_multiple(closed)

    wf = walk_forward(strat_df, n_windows=6, is_frac=0.7, anchored=False)
    wfs = wf_summarize(wf) if not wf.empty else {}

    mc = monte_carlo(strat_df, n_iter=1000) if s["n"] else {}

    # Verdicts
    pnl       = s["pnl"]
    dd        = abs(s["max_dd"])
    dd_ratio  = (dd / pnl) if pnl > 0 else float("inf")

    checks = {
        "trades >=100":      s["n"] >= THRESHOLDS["min_trades"],
        "wr >= 50%":         s["wr"] >= THRESHOLDS["min_wr"],
        "pf >= 1.3":         s["pf"] >= THRESHOLDS["min_pf"],
        "dd <= 25% of pnl":  dd_ratio <= THRESHOLDS["max_dd_ratio"],
        "expectancy >=0.15R": s["exp_R"] >= THRESHOLDS["min_exp_R"],
        "WFE >= 0.5":        wfs.get("avg_wfe", 0) >= THRESHOLDS["min_wfe"],
        "consistency >=60%": wfs.get("consistency%", 0) >= THRESHOLDS["min_consist"],
    }
    verdict = "PASS" if all(checks.values()) else "FAIL"

    return {
        "strategy":    strategy,
        "trades":      s["n"],
        "wr":          s["wr"],
        "pnl":         s["pnl"],
        "pf":          s["pf"],
        "exp_R":       s["exp_R"],
        "max_dd":      s["max_dd"],
        "dd_ratio":    round(dd_ratio, 2) if dd_ratio != float("inf") else float("inf"),
        "wf_avg_wfe":  wfs.get("avg_wfe"),
        "wf_consist%": wfs.get("consistency%"),
        "wf_avg_oos_wr": wfs.get("avg_oos_wr"),
        "mc_pnl_p05":  mc.get("pnl_p05"),
        "mc_pnl_p50":  mc.get("pnl_p50"),
        "mc_dd_p05":   mc.get("dd_p05_worst"),
        "mc_p_negative": mc.get("p_negative"),
        "verdict":     verdict,
        "checks":      checks,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--strategy", default=None, help="evaluate only one strategy")
    args = ap.parse_args()

    df = pd.read_csv(args.csv)
    if "strategy" not in df.columns:
        print("CSV missing 'strategy' column")
        return

    strategies = [args.strategy] if args.strategy else sorted(df["strategy"].unique())
    print(f"Evaluating {len(strategies)} strategies against plan_ thresholds:")
    print(f"  {THRESHOLDS}")
    print()

    rows = []
    for s in strategies:
        sub = df[df["strategy"] == s]
        if sub.empty:
            continue
        rows.append(evaluate_strategy(sub, s))

    out = pd.DataFrame([{k: v for k, v in r.items() if k != "checks"} for r in rows])
    out = out.sort_values(["verdict", "pnl"], ascending=[True, False])
    print(out.to_string(index=False))
    print()

    # Per-strategy check details for any near-pass
    for r in rows:
        passed = sum(r["checks"].values())
        if r["verdict"] == "PASS" or passed >= 5:
            mark = lambda b: "✓" if b else "✗"
            print(f"\n[{r['verdict']}] {r['strategy']}  ({passed}/7 checks)")
            for k, v in r["checks"].items():
                print(f"  {mark(v)} {k}")


if __name__ == "__main__":
    main()
