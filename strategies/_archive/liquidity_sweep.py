"""
liquidity_sweep.py — Variation 2
----------------------------------
Multi-timeframe liquidity sweep strategy.

15m map → 5m sweep confirmation → 1m rejection wick entry.

Run:  python liquidity_sweep.py
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
from strategy.ict_engine import EntrySignal, detect_fvgs
from strategy.liquidity_engine import (
    LiquidityPool,
    SweepEvent,
    detect_liquidity_pools,
    detect_sweep,
    is_rejection_wick,
    compute_tp_sl,
)
from strategy.entry_manager import place_entry

# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────
SYMBOL         = "XAU_USD"
POLL_INTERVAL  = 5
COOLDOWN       = 60
FIXED_TP       = 5.0
SL_BUFFER      = 1.0
DYNAMIC_RANGE  = 15.0
MIN_WICK_RATIO = 2.0
ENTRY_WINDOW   = 3     # max 1m candles to wait for rejection after 5m sweep
POOL_TP_ONLY   = True  # only fire when TP can target an opposing liquidity pool
                       # (backtest 2026-05-11: 79.5% WR / PF 5.25 in this subset
                       #  vs 50% WR for FVG-mid TPs)
TF_MAP         = "15m"
TF_SWEEP       = "5m"
TF_ENTRY       = "1m"
LOOKBACK_DAYS  = 7
N_POOLS        = 3
POOL_LOOKBACK  = 3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("liquidity_sweep")

# State
_last_15m_time: object          = None
_last_5m_time:  object          = None
_last_1m_time:  object          = None
_last_entry_ts: datetime | None = None

# Entry window state
_active_sweep:       Optional[SweepEvent]       = None
_active_15m_pools:   list[LiquidityPool]        = []
_active_15m_fvgs:    list                       = []
_entry_window_count: int                        = 0


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _is_new(candles: "pd.DataFrame", last_attr: str) -> bool:
    """Returns True if the last completed candle timestamp changed."""
    global _last_15m_time, _last_5m_time, _last_1m_time
    if len(candles) < 2:
        return False
    completed_time = candles.iloc[-2]["time"]
    last = globals()[last_attr]
    if completed_time == last:
        return False
    globals()[last_attr] = completed_time
    return True


def _cooldown_active() -> bool:
    ts = _last_entry_ts
    if ts is None:
        return False
    return (datetime.now(timezone.utc) - ts).total_seconds() < COOLDOWN


def _reset_entry_window():
    global _active_sweep, _active_15m_pools, _active_15m_fvgs, _entry_window_count
    _active_sweep       = None
    _active_15m_pools   = []
    _active_15m_fvgs    = []
    _entry_window_count = 0


# ──────────────────────────────────────────────────────────────────────────────
# Main loop
# ──────────────────────────────────────────────────────────────────────────────

def run():
    global _last_entry_ts, _active_sweep, _active_15m_pools, _active_15m_fvgs, _entry_window_count

    log.info("=== LiquiditySweep (VAR2) started | symbol=%s | cooldown=%ds ===",
             SYMBOL, COOLDOWN)

    while True:
        try:
            candles_15m = fetch_candles(TF_MAP,   days=LOOKBACK_DAYS, symbol=SYMBOL)
            candles_5m  = fetch_candles(TF_SWEEP, days=3,             symbol=SYMBOL)
            candles_1m  = fetch_candles(TF_ENTRY, days=1,             symbol=SYMBOL)

            if candles_15m.empty or candles_5m.empty or candles_1m.empty:
                log.debug("Not enough candle data yet — waiting")
                time.sleep(POLL_INTERVAL)
                continue

            # ── 1. Update 15m pool map on new 15m candle ──────────────────────
            if _is_new(candles_15m, "_last_15m_time"):
                completed_15m = candles_15m.iloc[:-1].copy()
                _active_15m_pools = detect_liquidity_pools(
                    completed_15m, lookback=POOL_LOOKBACK, n_pools=N_POOLS
                )
                _active_15m_fvgs = detect_fvgs(completed_15m)
                log.debug("[VAR2] 15m map updated | pools=%d fvgs=%d",
                          len(_active_15m_pools), len(_active_15m_fvgs))

            # ── 2. Check 5m candle for sweep of a 15m pool ───────────────────
            if _is_new(candles_5m, "_last_5m_time"):
                completed_5m = candles_5m.iloc[:-1].copy()
                candle_5m    = completed_5m.iloc[-1]

                if _active_sweep is None and _active_15m_pools:
                    sweep = detect_sweep(candle_5m, _active_15m_pools)
                    if sweep is not None:
                        _active_sweep       = sweep
                        _entry_window_count = 0
                        log.info(
                            "[VAR2] 5m sweep detected | dir=%s pool=%s level=%.2f | opening 1m entry window",
                            sweep.direction, sweep.pool.type, sweep.pool.level,
                        )

            # ── 3. Watch 1m candles for rejection wick in entry window ────────
            if _active_sweep is not None and _is_new(candles_1m, "_last_1m_time"):
                _entry_window_count += 1

                # Expire window
                if _entry_window_count > ENTRY_WINDOW:
                    log.info("[VAR2] Entry window expired (%d candles) — discarding sweep", ENTRY_WINDOW)
                    _reset_entry_window()
                    time.sleep(POLL_INTERVAL)
                    continue

                if _cooldown_active():
                    time.sleep(POLL_INTERVAL)
                    continue

                completed_1m = candles_1m.iloc[:-1].copy()
                candle_1m    = completed_1m.iloc[-1]

                if is_rejection_wick(candle_1m, min_wick_ratio=MIN_WICK_RATIO):
                    entry_price = float(candle_1m["close"])
                    tp, sl, mode = compute_tp_sl(
                        _active_sweep, entry_price, _active_15m_pools, _active_15m_fvgs,
                        fixed_tp=FIXED_TP, sl_buffer=SL_BUFFER, dynamic_range=DYNAMIC_RANGE,
                    )

                    if POOL_TP_ONLY and mode != "DYNAMIC_POOL":
                        log.debug("[VAR2] No opposing pool TP — skipping (mode=%s)", mode)
                        _reset_entry_window()
                        time.sleep(POLL_INTERVAL)
                        continue

                    signal = EntrySignal(
                        side=_active_sweep.direction,
                        entry_price=round(entry_price, 2),
                        stop_loss=sl,
                        take_profit=tp,
                        reason=f"LIQ_SWEEP_VAR2_{mode}",
                        zone_low=_active_sweep.pool.level,
                        zone_high=_active_sweep.pool.level,
                    )

                    log.info(
                        "[VAR2 SIGNAL] %s @ %.2f | SL=%.2f TP=%.2f | pool=%s level=%.2f | mode=%s",
                        signal.side, entry_price, sl, tp,
                        _active_sweep.pool.type, _active_sweep.pool.level, mode,
                    )

                    placed = place_entry(signal, symbol=SYMBOL, variation="VAR2")
                    if placed:
                        _last_entry_ts = datetime.now(timezone.utc)
                        log.info("[VAR2 ORDER] Placed | cooldown starts (%ds)", COOLDOWN)
                    else:
                        log.info("[VAR2 ORDER] Skipped (cap reached / DB error)")

                    _reset_entry_window()

        except KeyboardInterrupt:
            log.info("=== LiquiditySweep stopped ===")
            break
        except Exception:
            log.exception("[VAR2 LOOP ERROR] continuing")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
