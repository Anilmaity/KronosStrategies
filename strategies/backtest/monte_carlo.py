"""
monte_carlo.py
--------------
Trade-order Monte Carlo on a backtest trade CSV.

Shuffles the trade sequence N times. Reports the distribution of
final PnL and max drawdown — used to detect strategies whose return
depends on a lucky trade ordering.
"""
from __future__ import annotations

import argparse
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def monte_carlo(trades: pd.DataFrame, n_iter: int = 1000, seed: int = 42) -> dict:
    pnl_col = "pnl_pts" if "pnl_pts" in trades.columns else "pnl_points"
    closed = trades[trades["outcome"].isin(["TP", "SL"])][pnl_col].astype(float).to_numpy()
    if closed.size == 0:
        return {}

    rng = np.random.default_rng(seed)
    final_pnls = np.empty(n_iter)
    max_dds    = np.empty(n_iter)

    for i in range(n_iter):
        order = rng.permutation(closed.size)
        shuffled = closed[order]
        cum  = shuffled.cumsum()
        peak = np.maximum.accumulate(cum)
        final_pnls[i] = cum[-1]
        max_dds[i]    = (cum - peak).min()

    return {
        "n_trades":      int(closed.size),
        "n_iter":        n_iter,
        "pnl_p05":       round(float(np.percentile(final_pnls, 5)),  2),
        "pnl_p50":       round(float(np.percentile(final_pnls, 50)), 2),
        "pnl_p95":       round(float(np.percentile(final_pnls, 95)), 2),
        "pnl_mean":      round(float(final_pnls.mean()), 2),
        "dd_p05_worst":  round(float(np.percentile(max_dds, 5)),  2),  # 5th pct = worst-case
        "dd_p50":        round(float(np.percentile(max_dds, 50)), 2),
        "dd_p95_best":   round(float(np.percentile(max_dds, 95)), 2),
        "dd_mean":       round(float(max_dds.mean()), 2),
        "p_negative":    round(float((final_pnls < 0).mean()) * 100, 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--strategy", default=None)
    ap.add_argument("--n-iter", type=int, default=1000)
    args = ap.parse_args()

    df = pd.read_csv(args.csv)
    if "strategy" in df.columns and args.strategy:
        df = df[df["strategy"] == args.strategy]
        if df.empty:
            print(f"No trades for strategy '{args.strategy}'")
            return

    label = args.strategy or "ALL"
    res = monte_carlo(df, n_iter=args.n_iter)
    if not res:
        print("No closed trades.")
        return

    print(f"Monte Carlo — strategy={label}  n_trades={res['n_trades']}  iter={res['n_iter']}")
    print()
    print(f"  Final PnL distribution (pts):")
    print(f"    p05 (bad luck):  {res['pnl_p05']:+.2f}")
    print(f"    p50 (median):    {res['pnl_p50']:+.2f}")
    print(f"    p95 (good luck): {res['pnl_p95']:+.2f}")
    print(f"    mean:            {res['pnl_mean']:+.2f}")
    print(f"  Max drawdown distribution (pts):")
    print(f"    worst (p05):     {res['dd_p05_worst']:+.2f}")
    print(f"    median (p50):    {res['dd_p50']:+.2f}")
    print(f"    best   (p95):    {res['dd_p95_best']:+.2f}")
    print(f"  Probability of net loss across orderings: {res['p_negative']}%")


if __name__ == "__main__":
    main()
