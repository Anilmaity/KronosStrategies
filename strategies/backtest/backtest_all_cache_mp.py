"""
backtest_all_cache_mp.py
------------------------
Parallel version of backtest_all_cache.py: fans out one worker per strategy
(or balanced chunks if more strategies than CPUs). Parent loads + resamples
ticks once, writes parquet to a temp dir, workers mmap-load + run replay on
their strategy subset.
"""
from __future__ import annotations

import os
import sys
import time
import tempfile
from datetime import datetime
from multiprocessing import Pool, cpu_count

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import pandas as pd

from backtest.cache_loader import load_ticks, resample
from backtest.backtest_all import replay, print_comparison, save_csv

SYMBOL = "XAU_USD"


def _worker(args):
    """Run replay() for one strategy in a child process."""
    strat_name, parquet_dir = args
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    import pandas as _pd
    from backtest_strategies import STRATEGIES as _ALL
    from backtest.backtest_all import replay as _replay

    strat = next((s for s in _ALL if s.NAME == strat_name), None)
    if strat is None:
        return strat_name, []

    c1m  = _pd.read_parquet(os.path.join(parquet_dir, "c1m.parquet"))
    c5m  = _pd.read_parquet(os.path.join(parquet_dir, "c5m.parquet"))
    c15m = _pd.read_parquet(os.path.join(parquet_dir, "c15m.parquet"))

    trades = _replay(c1m, c5m, c15m, strategies=[strat], label=strat_name)
    return strat_name, trades


def main():
    t0 = time.time()
    print(f"Loading cached ticks for {SYMBOL} …", flush=True)
    ticks = load_ticks(SYMBOL)
    if ticks.empty:
        print("[ERR] no cached ticks found")
        return
    print(f"  {len(ticks):,} ticks  ({ticks.time.min()} -> {ticks.time.max()})  in {time.time()-t0:.1f}s", flush=True)

    print("Resampling to 1m / 5m / 15m …", flush=True)
    t1 = time.time()
    c1m  = resample(ticks, "1m")
    c5m  = resample(ticks, "5m")
    c15m = resample(ticks, "15m")
    del ticks
    print(f"  1m={len(c1m):,}  5m={len(c5m):,}  15m={len(c15m):,}  in {time.time()-t1:.1f}s", flush=True)

    days = (c1m["time"].max() - c1m["time"].min()).days or 1
    print(f"Window: {days} days", flush=True)

    # Persist candle DataFrames to parquet so workers can mmap-load cheaply
    tmp = tempfile.mkdtemp(prefix="kronos_bt_")
    print(f"Writing parquet to {tmp} …", flush=True)
    c1m.to_parquet(os.path.join(tmp, "c1m.parquet"))
    c5m.to_parquet(os.path.join(tmp, "c5m.parquet"))
    c15m.to_parquet(os.path.join(tmp, "c15m.parquet"))

    from backtest_strategies import STRATEGIES
    strat_names = [s.NAME for s in STRATEGIES]
    n_workers = min(len(strat_names), cpu_count())
    print(f"Dispatching {len(strat_names)} strategies across {n_workers} workers: {strat_names}", flush=True)

    t2 = time.time()
    with Pool(processes=n_workers) as pool:
        results = pool.map(_worker, [(name, tmp) for name in strat_names])

    all_trades = []
    for name, trades in results:
        print(f"  {name:18s} {len(trades):,} trades", flush=True)
        all_trades.extend(trades)
    print(f"Total {len(all_trades):,} trades across all strategies in {time.time()-t2:.1f}s", flush=True)

    n_days = len({t.entry_t.date() for t in all_trades}) or days
    print_comparison(all_trades, days=n_days)

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results",
                       f"all_strategies_cache_mp_{stamp}.csv")
    save_csv(all_trades, out)
    print(f"Total elapsed: {time.time()-t0:.1f}s", flush=True)

    # cleanup
    for f in ("c1m.parquet", "c5m.parquet", "c15m.parquet"):
        try: os.remove(os.path.join(tmp, f))
        except Exception: pass
    try: os.rmdir(tmp)
    except Exception: pass


if __name__ == "__main__":
    main()
