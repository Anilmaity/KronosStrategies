"""
btc_research/run_swing.py
-------------------------
Backtest the 4h swing mean-reversion family (the data-supported edge):
sweep-reversal (turtle soup) + z-score reversion, with/without the trend guard,
cost sensitivity, and a YEAR-BY-YEAR breakdown (range-regime robustness).
"""
from __future__ import annotations

import os
from collections import defaultdict

from btc_research.data import get_candles
from btc_research import engine, strategies as S

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

CANDIDATES = {
    "SWEEPREV":        lambda df: S.sweep_reversal(df, L=20, trend_guard=None),
    "SWEEPREV_guard":  lambda df: S.sweep_reversal(df, L=20, trend_guard=2.0),
    "ZREV":            lambda df: S.zscore_revert_swing(df, N=30, z_thr=2.0, trend_guard=None),
    "ZREV_guard":      lambda df: S.zscore_revert_swing(df, N=30, z_thr=2.0, trend_guard=2.0),
}


def per_year(trades):
    buckets = defaultdict(list)
    for t in trades:
        buckets[t.entry_time.year].append(t)
    return {y: engine.summarize(ts) for y, ts in sorted(buckets.items())}


def main(tf="4h"):
    df = get_candles(tf).reset_index(drop=True)
    print(f"Swing backtest on {tf}: {len(df):,} candles  "
          f"{df['time'].iloc[0]} -> {df['time'].iloc[-1]}\n")

    hdr = (f"{'strategy':16s} {'cost':>4s} {'n':>4s} {'wr%':>6s} {'pnl_pts':>10s} "
           f"{'pf':>6s} {'expR':>6s} {'maxDD':>9s} {'dd/pnl':>7s}")
    print(hdr); print("-" * len(hdr))
    all_trades = []
    for name, gen in CANDIDATES.items():
        entries = gen(df)
        for cost in (30.0, 60.0):
            trades = engine.run(df, entries, name, cost_pts=cost)
            s = engine.summarize(trades)
            if s.get("n", 0) == 0:
                print(f"{name:16s} {cost:>4.0f}   no trades"); continue
            print(f"{name:16s} {cost:>4.0f} {s['n']:>4d} {s['wr']:>6.1f} {s['pnl']:>10.1f} "
                  f"{s['pf']:>6.2f} {s['exp_R']:>6.3f} {s['max_dd']:>9.1f} {str(s['dd_ratio']):>7s}")
            if cost == 30.0:
                all_trades.extend(trades)
        # per-year at cost=30
        py = per_year([t for t in engine.run(df, entries, name, cost_pts=30.0)])
        ys = "  ".join(f"{y}:{v.get('pnl',0):+.0f}({v.get('n',0)})" for y, v in py.items())
        print(f"    by year: {ys}")

    path = os.path.join(RESULTS_DIR, f"btc_swing_{tf}.csv")
    engine.save_csv(all_trades, path)
    print(f"\nsaved {len(all_trades)} trades -> {path}")


if __name__ == "__main__":
    main("4h")
