"""
liquidity_scalper.py — Variation 1
----------------------------------
Single-timeframe 5m liquidity sweep scalper.

Mirrors backtest_engine.replay_var1: every completed 5m candle, build the
recent liquidity pools, check for a sweep, require a rejection wick, and
enter at close with fixed TP / SL-from-pool.

Run:  python liquidity_scalper.py
"""

from __future__ import annotations

import os
import sys
import time
import logging
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shared.tsdb_reader import fetch_candles
from shared.market_timing import is_market_closed_utc
from strategy.ict_engine import EntrySignal
from strategy.liquidity_engine import (
    detect_liquidity_pools,
    detect_sweep,
    is_rejection_wick,
    compute_tp_sl,
)
from strategy.entry_manager import place_entry

# Config — mirrors backtest/backtest_scalper.py
SYMBOL         = os.getenv("XAUUSD_SYMBOL", "XAU_USD")
POLL_INTERVAL  = int(os.getenv("VAR1_POLL_SEC", "5"))
COOLDOWN       = int(os.getenv("VAR1_COOLDOWN_S", "30"))
FIXED_TP       = float(os.getenv("VAR1_FIXED_TP", "4.0"))
SL_BUFFER      = float(os.getenv("VAR1_SL_BUFFER", "1.0"))
DYNAMIC_RANGE  = float(os.getenv("VAR1_DYNAMIC_RANGE", "15.0"))
MIN_WICK_RATIO = float(os.getenv("VAR1_MIN_WICK", "2.0"))
N_POOLS        = int(os.getenv("VAR1_N_POOLS", "3"))
POOL_LOOKBACK  = int(os.getenv("VAR1_POOL_LB", "3"))
TF             = "5m"
LOOKBACK_DAYS  = 3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("liquidity_scalper")

_last_5m_time:  object        = None
_last_entry_ts: datetime | None = None


def _is_new_5m(candles) -> bool:
    global _last_5m_time
    if len(candles) < 2:
        return False
    t = candles.iloc[-2]["time"]
    if t == _last_5m_time:
        return False
    _last_5m_time = t
    return True


def _cooldown_active() -> bool:
    if _last_entry_ts is None:
        return False
    return (datetime.now(timezone.utc) - _last_entry_ts).total_seconds() < COOLDOWN


def run():
    global _last_entry_ts
    log.info("=== LiquidityScalper (VAR1) started | symbol=%s | cooldown=%ds ===",
             SYMBOL, COOLDOWN)

    while True:
        try:
            if is_market_closed_utc():
                log.debug("market closed — skipping tick")
                time.sleep(POLL_INTERVAL)
                continue

            candles = fetch_candles(TF, days=LOOKBACK_DAYS, symbol=SYMBOL)
            if candles.empty or not _is_new_5m(candles):
                time.sleep(POLL_INTERVAL)
                continue
            if _cooldown_active():
                time.sleep(POLL_INTERVAL)
                continue

            completed = candles.iloc[:-1].copy()
            candle = completed.iloc[-1]

            pools = detect_liquidity_pools(completed, lookback=POOL_LOOKBACK, n_pools=N_POOLS)
            if not pools:
                time.sleep(POLL_INTERVAL); continue

            sweep = detect_sweep(candle, pools)
            if sweep is None:
                time.sleep(POLL_INTERVAL); continue

            if not is_rejection_wick(candle, min_wick_ratio=MIN_WICK_RATIO):
                time.sleep(POLL_INTERVAL); continue

            entry_price = float(candle["close"])
            tp, sl, mode = compute_tp_sl(
                sweep, entry_price, pools, fvgs=[],
                fixed_tp=FIXED_TP, sl_buffer=SL_BUFFER, dynamic_range=DYNAMIC_RANGE,
            )

            signal = EntrySignal(
                side=sweep.direction,
                entry_price=round(entry_price, 2),
                stop_loss=sl,
                take_profit=tp,
                reason=f"LIQ_SCALP_VAR1_{mode}",
                zone_low=sweep.pool.level,
                zone_high=sweep.pool.level,
            )

            log.info(
                "[VAR1 SIGNAL] %s @ %.2f | SL=%.2f TP=%.2f | pool=%s level=%.2f | mode=%s",
                signal.side, entry_price, sl, tp,
                sweep.pool.type, sweep.pool.level, mode,
            )

            placed = place_entry(signal, symbol=SYMBOL, variation="VAR1")
            if placed:
                _last_entry_ts = datetime.now(timezone.utc)
                log.info("[VAR1 ORDER] Placed | cooldown starts (%ds)", COOLDOWN)
            else:
                log.info("[VAR1 ORDER] Skipped (open position / cap / DB error)")

        except KeyboardInterrupt:
            log.info("=== LiquidityScalper stopped ===")
            break
        except Exception:
            log.exception("[VAR1 LOOP ERROR] continuing")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
