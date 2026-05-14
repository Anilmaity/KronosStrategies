"""
backtest_scalper_eq.py — VAR1 equal-pool-only backtest
-------------------------------------------------------
Same as backtest_scalper.py but only enters on equal highs/lows (is_equal=True).

Run:  python backtest/backtest_scalper_eq.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from shared.tsdb_reader import fetch_candles
from backtest.backtest_engine import replay_var1, print_summary, save_csv

BACKTEST_DAYS  = 30
SYMBOL         = "XAU_USD"
FIXED_TP       = 4.0
SL_BUFFER      = 1.0
DYNAMIC_RANGE  = 15.0
MIN_WICK_RATIO = 2.0
N_POOLS        = 3
POOL_LOOKBACK  = 3
COOLDOWN       = 30


def main():
    print(f"[VAR1-EQ] Fetching {BACKTEST_DAYS} days of 5m candles …")
    candles_5m = fetch_candles("5m", days=BACKTEST_DAYS, symbol=SYMBOL)

    if candles_5m.empty:
        print("[VAR1-EQ] No candle data.")
        return

    print(f"[VAR1-EQ] {len(candles_5m)} candles loaded. Running equal-pool replay …")
    trades = replay_var1(
        candles_5m,
        variation="VAR1-EQ",
        fixed_tp=FIXED_TP,
        sl_buffer=SL_BUFFER,
        dynamic_range=DYNAMIC_RANGE,
        min_wick_ratio=MIN_WICK_RATIO,
        n_pools=N_POOLS,
        pool_lookback=POOL_LOOKBACK,
        cooldown_s=COOLDOWN,
        equal_pools_only=True,
    )

    print_summary(trades, label=f"VAR1-EQ  ({BACKTEST_DAYS}d)")

    stamp = datetime.now().strftime("%Y%m%d")
    results_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
    save_csv(trades, os.path.join(results_dir, f"var1_eq_backtest_{stamp}.csv"))


if __name__ == "__main__":
    main()
