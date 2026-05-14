"""
backtest_all_cache.py
---------------------
Same as backtest_all.py but reads candles from the 6-month cached tick JSON
files at Extras_Script/Tick_Data_Generator/cache_data/ instead of TimescaleDB.

Use this for longer-horizon evaluation and walk-forward validation.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from backtest.cache_loader import load_ticks, resample
from backtest.backtest_all import replay, print_comparison, save_csv

SYMBOL = "XAU_USD"


def main():
    t0 = time.time()
    print(f"Loading cached ticks for {SYMBOL} …")
    ticks = load_ticks(SYMBOL)
    if ticks.empty:
        print("[ERR] no cached ticks found")
        return
    print(f"  {len(ticks):,} ticks  ({ticks.time.min()} -> {ticks.time.max()})  in {time.time()-t0:.1f}s")

    print("Resampling to 1m / 5m / 15m …")
    t1 = time.time()
    c1m  = resample(ticks, "1m")
    c5m  = resample(ticks, "5m")
    c15m = resample(ticks, "15m")
    print(f"  1m={len(c1m):,}  5m={len(c5m):,}  15m={len(c15m):,}  in {time.time()-t1:.1f}s")

    days = (ticks.time.max() - ticks.time.min()).days or 1
    print(f"Window: {days} days")

    print("Running walk-forward replay across all strategies …")
    t2 = time.time()
    trades = replay(c1m, c5m, c15m)
    print(f"  {len(trades):,} signals fired in {time.time()-t2:.1f}s")

    n_days = len({t.entry_t.date() for t in trades}) or days
    print_comparison(trades, days=n_days)

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results",
                       f"all_strategies_cache_{stamp}.csv")
    save_csv(trades, out)
    print(f"Total elapsed: {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
