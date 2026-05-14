"""
backtest_sweep_eq.py — VAR2 equal-pool-only backtest
-----------------------------------------------------
Same as backtest_sweep.py but only enters on equal highs/lows (is_equal=True).

Run:  python backtest/backtest_sweep_eq.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from shared.tsdb_reader import fetch_candles
from backtest.backtest_engine import replay_var2, print_summary, save_csv

BACKTEST_DAYS  = 30
SYMBOL         = "XAU_USD"
FIXED_TP       = 5.0
SL_BUFFER      = 1.0
DYNAMIC_RANGE  = 15.0
MIN_WICK_RATIO = 2.0
N_POOLS        = 3
POOL_LOOKBACK  = 3
ENTRY_WINDOW   = 3
COOLDOWN       = 60


def main():
    print(f"[VAR2-EQ] Fetching {BACKTEST_DAYS} days of candles …")
    candles_15m = fetch_candles("15m", days=BACKTEST_DAYS, symbol=SYMBOL)
    candles_5m  = fetch_candles("5m",  days=BACKTEST_DAYS, symbol=SYMBOL)
    candles_1m  = fetch_candles("1m",  days=BACKTEST_DAYS, symbol=SYMBOL)

    if candles_15m.empty or candles_5m.empty or candles_1m.empty:
        print("[VAR2-EQ] No candle data.")
        return

    print(f"[VAR2-EQ] 15m={len(candles_15m)} 5m={len(candles_5m)} 1m={len(candles_1m)} candles. Running equal-pool replay …")
    trades = replay_var2(
        candles_15m, candles_5m, candles_1m,
        variation="VAR2-EQ",
        fixed_tp=FIXED_TP,
        sl_buffer=SL_BUFFER,
        dynamic_range=DYNAMIC_RANGE,
        min_wick_ratio=MIN_WICK_RATIO,
        n_pools=N_POOLS,
        pool_lookback=POOL_LOOKBACK,
        entry_window=ENTRY_WINDOW,
        cooldown_s=COOLDOWN,
        equal_pools_only=True,
    )

    print_summary(trades, label=f"VAR2-EQ  ({BACKTEST_DAYS}d)")

    stamp = datetime.now().strftime("%Y%m%d")
    results_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
    save_csv(trades, os.path.join(results_dir, f"var2_eq_backtest_{stamp}.csv"))


if __name__ == "__main__":
    main()
