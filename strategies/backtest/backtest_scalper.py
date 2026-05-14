"""
backtest_scalper.py — VAR1 backtest runner
------------------------------------------
Replays 30 days of TimescaleDB tick data through the VAR1 liquidity scalper
signal logic. No DB writes, no MetaAPI calls.

Run:  python backtest/backtest_scalper.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from shared.tsdb_reader import fetch_candles
from backtest.backtest_engine import replay_var1, print_summary, save_csv

BACKTEST_DAYS = 30
SYMBOL        = "XAU_USD"

# Strategy params (mirror liquidity_scalper.py config)
FIXED_TP       = 4.0
SL_BUFFER      = 1.0
DYNAMIC_RANGE  = 15.0
MIN_WICK_RATIO = 2.0
N_POOLS        = 3
POOL_LOOKBACK  = 3
COOLDOWN       = 30


def main():
    print(f"[VAR1] Fetching {BACKTEST_DAYS} days of 5m candles from TimescaleDB …")
    candles_5m = fetch_candles("5m", days=BACKTEST_DAYS, symbol=SYMBOL)

    if candles_5m.empty:
        print("[VAR1] No candle data — make sure TimescaleDB is running.")
        return

    print(f"[VAR1] {len(candles_5m)} candles loaded. Running replay …")
    trades = replay_var1(
        candles_5m,
        fixed_tp=FIXED_TP,
        sl_buffer=SL_BUFFER,
        dynamic_range=DYNAMIC_RANGE,
        min_wick_ratio=MIN_WICK_RATIO,
        n_pools=N_POOLS,
        pool_lookback=POOL_LOOKBACK,
        cooldown_s=COOLDOWN,
    )

    print_summary(trades, label=f"VAR1  ({BACKTEST_DAYS}d)")

    stamp = datetime.now().strftime("%Y%m%d")
    results_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
    save_csv(trades, os.path.join(results_dir, f"var1_backtest_{stamp}.csv"))


if __name__ == "__main__":
    main()
