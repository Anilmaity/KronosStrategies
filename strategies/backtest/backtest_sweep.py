"""
backtest_sweep.py — VAR2 backtest runner
-----------------------------------------
Replays 30 days of TimescaleDB tick data through the VAR2 multi-timeframe
liquidity sweep signal logic. No DB writes, no MetaAPI calls.

Run:  python backtest/backtest_sweep.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from shared.tsdb_reader import fetch_candles
from backtest.backtest_engine import replay_var2, print_summary, save_csv

BACKTEST_DAYS = 30
SYMBOL        = "XAU_USD"

# Strategy params (mirror liquidity_sweep.py config)
FIXED_TP       = 5.0
SL_BUFFER      = 1.0
DYNAMIC_RANGE  = 15.0
MIN_WICK_RATIO = 2.0
N_POOLS        = 3
POOL_LOOKBACK  = 3
ENTRY_WINDOW   = 3
COOLDOWN       = 60
POOL_TP_ONLY   = True


def main():
    print(f"[VAR2] Fetching {BACKTEST_DAYS} days of candles from TimescaleDB …")
    candles_15m = fetch_candles("15m", days=BACKTEST_DAYS, symbol=SYMBOL)
    candles_5m  = fetch_candles("5m",  days=BACKTEST_DAYS, symbol=SYMBOL)
    candles_1m  = fetch_candles("1m",  days=BACKTEST_DAYS, symbol=SYMBOL)

    if candles_15m.empty or candles_5m.empty or candles_1m.empty:
        print("[VAR2] No candle data — make sure TimescaleDB is running.")
        return

    print(f"[VAR2] 15m={len(candles_15m)} 5m={len(candles_5m)} 1m={len(candles_1m)} candles. Running replay …")
    trades = replay_var2(
        candles_15m, candles_5m, candles_1m,
        fixed_tp=FIXED_TP,
        sl_buffer=SL_BUFFER,
        dynamic_range=DYNAMIC_RANGE,
        min_wick_ratio=MIN_WICK_RATIO,
        n_pools=N_POOLS,
        pool_lookback=POOL_LOOKBACK,
        entry_window=ENTRY_WINDOW,
        cooldown_s=COOLDOWN,
        pool_tp_only=POOL_TP_ONLY,
    )

    print_summary(trades, label=f"VAR2  ({BACKTEST_DAYS}d)")

    stamp = datetime.now().strftime("%Y%m%d")
    results_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
    save_csv(trades, os.path.join(results_dir, f"var2_backtest_{stamp}.csv"))


if __name__ == "__main__":
    main()
