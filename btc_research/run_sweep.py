"""
btc_research/run_sweep.py
-------------------------
Diagnose & fix the MR exit geometry. Raw scan proved the fade edge is +EV, but
tight ATR stops tag the overshoot before reversion. Sweep stop width + hold +
threshold + guard, looking for a PROFITABLE PLATEAU that holds in BOTH years
(not a single lucky peak). Cost held at a realistic $30 round-trip.
"""
from __future__ import annotations

import itertools
from collections import defaultdict

from btc_research.data import get_candles
from btc_research import engine, strategies as S


def per_year_pnl(trades):
    b = defaultdict(float)
    for t in trades:
        b[t.entry_time.year] += t.pnl_pts
    return dict(b)


def main(tf="4h"):
    df = get_candles(tf).reset_index(drop=True)
    print(f"Param sweep on {tf}: {len(df):,} candles\n")

    grid = {
        "z_thr":       [1.5, 2.0],
        "sl_atr":      [2.5, 3.5, 5.0, 99.0],   # 99 == effectively time-exit only
        "max_hold":    [6, 12, 20],
        "trend_guard": [None, 2.5],
    }
    keys = list(grid)
    rows = []
    for combo in itertools.product(*grid.values()):
        p = dict(zip(keys, combo))
        entries = S.zscore_revert_swing(df, N=30, atr_n=14, **p)
        trades = engine.run(df, entries, "ZREV", cost_pts=30.0)
        s = engine.summarize(trades)
        if s.get("n", 0) < 30:
            continue
        py = per_year_pnl(trades)
        both_pos = all(v > 0 for v in py.values()) and len(py) >= 2
        rows.append({**p, **s, "y2025": round(py.get(2025, 0)), "y2026": round(py.get(2026, 0)),
                     "both_pos": both_pos})

    rows.sort(key=lambda r: r["pf"], reverse=True)
    print(f"{'z':>4s} {'sl':>5s} {'hold':>4s} {'guard':>6s} | {'n':>4s} {'wr%':>5s} "
          f"{'pnl':>9s} {'pf':>5s} {'expR':>6s} {'maxDD':>8s} {'y25':>7s} {'y26':>7s} {'both+':>5s}")
    print("-" * 92)
    for r in rows:
        g = "-" if r["trend_guard"] is None else f"{r['trend_guard']}"
        flag = "YES" if r["both_pos"] else ""
        print(f"{r['z_thr']:>4} {r['sl_atr']:>5} {r['max_hold']:>4} {g:>6s} | "
              f"{r['n']:>4d} {r['wr']:>5.1f} {r['pnl']:>9.0f} {r['pf']:>5.2f} {r['exp_R']:>6.3f} "
              f"{r['max_dd']:>8.0f} {r['y2025']:>7d} {r['y2026']:>7d} {flag:>5s}")

    prof = [r for r in rows if r["pf"] > 1.0]
    both = [r for r in rows if r["both_pos"]]
    print(f"\nconfigs PF>1.0: {len(prof)}/{len(rows)}    profitable in BOTH years: {len(both)}/{len(rows)}")


if __name__ == "__main__":
    main("4h")
