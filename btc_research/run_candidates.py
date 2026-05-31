"""
btc_research/run_candidates.py
------------------------------
Backtest the candidate BTC strategies on 15m candles and print a summary table.
Immediate cost-sensitivity check ($30 vs stressed $60 round-trip).
Saves combined trades CSV for deeper validation.
"""
from __future__ import annotations

import os

from btc_research.data import get_candles
from btc_research import engine, strategies as S

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

CANDIDATES = {
    "ORB_NY":   lambda df: S.orb_ny(df),
    "VBO_NY":   lambda df: S.vbo(df, ny_only=True),
    "VBO_ALL":  lambda df: S.vbo(df, ny_only=False),
    "DROPMOM":  lambda df: S.drop_momentum(df),
    "ZMR_CTRL": lambda df: S.zscore_mr(df),
}


def main(tf="15m"):
    df = get_candles(tf).reset_index(drop=True)
    print(f"Backtest on {tf}: {len(df):,} candles  {df['time'].iloc[0]} -> {df['time'].iloc[-1]}\n")

    all_trades = []
    hdr = f"{'strategy':10s} {'cost':>5s} {'n':>5s} {'wr%':>6s} {'pnl_pts':>10s} {'pf':>6s} {'expR':>6s} {'maxDD':>9s} {'dd/pnl':>7s}"
    print(hdr)
    print("-" * len(hdr))
    for name, gen in CANDIDATES.items():
        entries = gen(df)
        for cost in (30.0, 60.0):
            trades = engine.run(df, entries, name, cost_pts=cost)
            s = engine.summarize(trades)
            if s.get("n", 0) == 0:
                print(f"{name:10s} {cost:>5.0f}    no trades")
                continue
            print(f"{name:10s} {cost:>5.0f} {s['n']:>5d} {s['wr']:>6.1f} {s['pnl']:>10.1f} "
                  f"{s['pf']:>6.2f} {s['exp_R']:>6.3f} {s['max_dd']:>9.1f} {str(s['dd_ratio']):>7s}")
            if cost == 30.0:
                all_trades.extend(trades)

    path = os.path.join(RESULTS_DIR, f"btc_candidates_{tf}.csv")
    engine.save_csv(all_trades, path)
    print(f"\nsaved {len(all_trades)} trades -> {path}")


if __name__ == "__main__":
    main("15m")
