"""
backtest_engine.py
------------------
Shared replay + reporting logic for liquidity sweep backtests.
No DB writes, no MetaAPI calls — pure simulation over historical tick data.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, fields, astuple
from datetime import datetime, timezone
from typing import Callable

import pandas as pd

from strategy.liquidity_engine import (
    SweepEvent,
    detect_liquidity_pools,
    detect_sweep,
    is_rejection_wick,
    compute_tp_sl,
)
from strategy.ict_engine import detect_fvgs


# ──────────────────────────────────────────────────────────────────────────────
# Trade record
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class TradeRecord:
    variation:    str
    entry_time:   datetime
    direction:    str
    entry_price:  float
    stop_loss:    float
    take_profit:  float
    exit_price:   float
    exit_time:    datetime
    outcome:      str       # 'TP' | 'SL' | 'OPEN'
    pnl_points:   float
    tp_mode:      str       # 'FIXED' | 'DYNAMIC_FVG' | 'DYNAMIC_POOL'
    pool_type:    str
    is_equal_pool: bool


# ──────────────────────────────────────────────────────────────────────────────
# TP/SL simulation
# ──────────────────────────────────────────────────────────────────────────────

def _simulate_exit(
    direction: str,
    entry_price: float,
    tp: float,
    sl: float,
    future_candles: pd.DataFrame,
) -> tuple[str, float, datetime]:
    """
    Walk future candles to find which of TP or SL is hit first.
    Returns (outcome, exit_price, exit_time).
    """
    for _, row in future_candles.iterrows():
        if direction == "BUY":
            if row["low"] <= sl:
                return "SL", sl, row["time"]
            if row["high"] >= tp:
                return "TP", tp, row["time"]
        else:
            if row["high"] >= sl:
                return "SL", sl, row["time"]
            if row["low"] <= tp:
                return "TP", tp, row["time"]

    # Not closed within window
    last = future_candles.iloc[-1] if not future_candles.empty else None
    exit_price = float(last["close"]) if last is not None else entry_price
    exit_time  = last["time"]        if last is not None else datetime.now(timezone.utc)
    return "OPEN", exit_price, exit_time


# ──────────────────────────────────────────────────────────────────────────────
# VAR1 replay
# ──────────────────────────────────────────────────────────────────────────────

_POOL_WINDOW = 200  # candles fed to detect_liquidity_pools — caps O(n²) to O(n)


def replay_var1(
    candles_5m: pd.DataFrame,
    variation: str = "VAR1",
    fixed_tp: float = 4.0,
    sl_buffer: float = 1.0,
    dynamic_range: float = 15.0,
    min_wick_ratio: float = 2.0,
    n_pools: int = 3,
    pool_lookback: int = 3,
    cooldown_s: int = 30,
    equal_pools_only: bool = False,
) -> list[TradeRecord]:
    """Walk-forward replay of VAR1 on 5m candles. No lookahead."""
    trades: list[TradeRecord] = []
    last_entry_ts: datetime | None = None

    n = len(candles_5m)
    min_candles = pool_lookback * 2 + 3

    for i in range(min_candles, n - 1):
        # Rolling window: only pass recent candles to pool detection (O(1) per iter)
        window_start = max(0, i - _POOL_WINDOW)
        past = candles_5m.iloc[window_start:i]
        candle = past.iloc[-1]

        # Cooldown check (simulate using candle time)
        candle_time = candle["time"]
        if isinstance(candle_time, pd.Timestamp):
            candle_dt = candle_time.to_pydatetime()
        else:
            candle_dt = candle_time
        candle_dt = candle_dt.replace(tzinfo=timezone.utc) if candle_dt.tzinfo is None else candle_dt

        if last_entry_ts is not None:
            elapsed = (candle_dt - last_entry_ts).total_seconds()
            if elapsed < cooldown_s:
                continue

        pools = detect_liquidity_pools(past, lookback=pool_lookback, n_pools=n_pools)
        if equal_pools_only:
            pools = [p for p in pools if p.is_equal]
        sweep = detect_sweep(candle, pools)
        if sweep is None:
            continue
        if not is_rejection_wick(candle, min_wick_ratio=min_wick_ratio):
            continue

        entry_price = float(candle["close"])
        tp, sl, mode = compute_tp_sl(
            sweep, entry_price, pools, fvgs=[],
            fixed_tp=fixed_tp, sl_buffer=sl_buffer, dynamic_range=dynamic_range,
        )

        future = candles_5m.iloc[i:].copy()
        outcome, exit_price, exit_time = _simulate_exit(sweep.direction, entry_price, tp, sl, future)

        pnl = (exit_price - entry_price) if sweep.direction == "BUY" else (entry_price - exit_price)
        pnl = round(pnl, 2)

        trades.append(TradeRecord(
            variation=variation,
            entry_time=candle_dt,
            direction=sweep.direction,
            entry_price=round(entry_price, 2),
            stop_loss=sl,
            take_profit=tp,
            exit_price=round(exit_price, 2),
            exit_time=exit_time,
            outcome=outcome,
            pnl_points=pnl,
            tp_mode=mode,
            pool_type=sweep.pool.type,
            is_equal_pool=sweep.pool.is_equal,
        ))

        last_entry_ts = candle_dt

    return trades


# ──────────────────────────────────────────────────────────────────────────────
# VAR2 replay
# ──────────────────────────────────────────────────────────────────────────────

def replay_var2(
    candles_15m: pd.DataFrame,
    candles_5m:  pd.DataFrame,
    candles_1m:  pd.DataFrame,
    variation: str = "VAR2",
    fixed_tp: float = 5.0,
    sl_buffer: float = 1.0,
    dynamic_range: float = 15.0,
    min_wick_ratio: float = 2.0,
    n_pools: int = 3,
    pool_lookback: int = 3,
    entry_window: int = 3,
    cooldown_s: int = 60,
    equal_pools_only: bool = False,
    pool_tp_only: bool = False,
) -> list[TradeRecord]:
    """Walk-forward replay of VAR2. No lookahead.

    pool_tp_only: when True, skip signals where compute_tp_sl returns a mode
    other than DYNAMIC_POOL. Mirrors the live POOL_TP_ONLY filter."""
    trades: list[TradeRecord] = []
    last_entry_ts: datetime | None = None

    n_5m = len(candles_5m)
    min_candles = pool_lookback * 2 + 3

    # Pre-sort for fast time-based slicing
    candles_15m = candles_15m.reset_index(drop=True)
    candles_1m  = candles_1m.reset_index(drop=True)

    for i in range(min_candles, n_5m - 1):
        candle_5m = candles_5m.iloc[i - 1]
        candle_time = candle_5m["time"]

        if isinstance(candle_time, pd.Timestamp):
            candle_dt = candle_time.to_pydatetime()
        else:
            candle_dt = candle_time
        candle_dt = candle_dt.replace(tzinfo=timezone.utc) if candle_dt.tzinfo is None else candle_dt

        if last_entry_ts is not None:
            elapsed = (candle_dt - last_entry_ts).total_seconds()
            if elapsed < cooldown_s:
                continue

        # Rolling window for 15m pool detection
        past_15m_all = candles_15m[candles_15m["time"] <= candle_time]
        if len(past_15m_all) < min_candles:
            continue
        past_15m = past_15m_all.iloc[max(0, len(past_15m_all) - _POOL_WINDOW):]

        pools_15m = detect_liquidity_pools(past_15m, lookback=pool_lookback, n_pools=n_pools)
        if equal_pools_only:
            pools_15m = [p for p in pools_15m if p.is_equal]
        fvgs_15m  = detect_fvgs(past_15m)

        if not pools_15m:
            continue

        sweep = detect_sweep(candle_5m, pools_15m)
        if sweep is None:
            continue

        # Now check up to entry_window 1m candles for rejection wick
        window_1m = candles_1m[
            (candles_1m["time"] > candle_time) &
            (candles_1m["time"] <= candles_5m.iloc[i + min(entry_window, n_5m - i - 1)]["time"])
        ].copy()

        triggered = False
        for _, row_1m in window_1m.iterrows():
            if not is_rejection_wick(row_1m, min_wick_ratio=min_wick_ratio):
                continue

            entry_price = float(row_1m["close"])
            entry_time  = row_1m["time"]
            if isinstance(entry_time, pd.Timestamp):
                entry_dt = entry_time.to_pydatetime()
            else:
                entry_dt = entry_time
            entry_dt = entry_dt.replace(tzinfo=timezone.utc) if entry_dt.tzinfo is None else entry_dt

            tp, sl, mode = compute_tp_sl(
                sweep, entry_price, pools_15m, fvgs_15m,
                fixed_tp=fixed_tp, sl_buffer=sl_buffer, dynamic_range=dynamic_range,
            )

            if pool_tp_only and mode != "DYNAMIC_POOL":
                continue

            future = candles_1m[candles_1m["time"] > entry_time].copy()
            outcome, exit_price, exit_time = _simulate_exit(sweep.direction, entry_price, tp, sl, future)

            pnl = (exit_price - entry_price) if sweep.direction == "BUY" else (entry_price - exit_price)
            pnl = round(pnl, 2)

            trades.append(TradeRecord(
                variation=variation,
                entry_time=entry_dt,
                direction=sweep.direction,
                entry_price=round(entry_price, 2),
                stop_loss=sl,
                take_profit=tp,
                exit_price=round(exit_price, 2),
                exit_time=exit_time,
                outcome=outcome,
                pnl_points=pnl,
                tp_mode=mode,
                pool_type=sweep.pool.type,
                is_equal_pool=sweep.pool.is_equal,
            ))

            last_entry_ts = entry_dt
            triggered = True
            break  # one trade per sweep

    return trades


# ──────────────────────────────────────────────────────────────────────────────
# Reporting
# ──────────────────────────────────────────────────────────────────────────────

def print_summary(trades: list[TradeRecord], label: str = "") -> None:
    if not trades:
        print(f"=== {label} Backtest — no trades ===")
        return

    closed = [t for t in trades if t.outcome != "OPEN"]
    wins   = [t for t in closed  if t.outcome == "TP"]
    losses = [t for t in closed  if t.outcome == "SL"]

    total_pnl   = sum(t.pnl_points for t in closed)
    avg_win     = sum(t.pnl_points for t in wins)   / len(wins)   if wins   else 0
    avg_loss    = sum(t.pnl_points for t in losses) / len(losses) if losses else 0
    win_rate    = len(wins) / len(closed) * 100 if closed else 0

    # Drawdown
    running = 0.0
    peak    = 0.0
    max_dd  = 0.0
    for t in closed:
        running += t.pnl_points
        peak     = max(peak, running)
        dd       = peak - running
        max_dd   = max(max_dd, dd)

    # TP mode split
    mode_counts: dict[str, int] = {}
    for t in closed:
        mode_counts[t.tp_mode] = mode_counts.get(t.tp_mode, 0) + 1
    mode_str = "  ".join(f"{k}={round(v/len(closed)*100)}%" for k, v in mode_counts.items())

    # Equal pool win rate
    eq_wins  = [t for t in wins   if t.is_equal_pool]
    eq_total = [t for t in closed if t.is_equal_pool]
    eq_rate  = len(eq_wins) / len(eq_total) * 100 if eq_total else 0
    neq_wins  = [t for t in wins   if not t.is_equal_pool]
    neq_total = [t for t in closed if not t.is_equal_pool]
    neq_rate  = len(neq_wins) / len(neq_total) * 100 if neq_total else 0

    print(f"\n=== {label} Backtest — {len(trades)} days ===")
    print(f"Total trades   : {len(trades)}  ({len(closed)} closed, {len(trades)-len(closed)} open)")
    print(f"Win rate       : {win_rate:.1f}%  ({len(wins)} TP / {len(losses)} SL)")
    print(f"Total P&L      : {total_pnl:+.1f} pts")
    print(f"Avg win        : {avg_win:+.1f} pts")
    print(f"Avg loss       : {avg_loss:+.1f} pts")
    print(f"Max drawdown   : -{max_dd:.1f} pts")
    print(f"TP mode split  : {mode_str}")
    print(f"Equal pool wins: {eq_rate:.1f}%  (vs {neq_rate:.1f}% non-equal)")


def save_csv(trades: list[TradeRecord], filepath: str) -> None:
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    field_names = [f.name for f in fields(TradeRecord)]
    with open(filepath, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(field_names)
        for t in trades:
            writer.writerow(astuple(t))
    print(f"[CSV] Saved {len(trades)} trades → {filepath}")
