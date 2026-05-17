"""
backtest_all.py
----------------
Single walk-forward pass over 30 days of XAUUSD tick data. Calls every
strategy registered in `strategies/` on each completed 1m candle. Records
trades + simulates TP/SL exits independently per strategy. Prints a
comparison table at the end.
"""

from __future__ import annotations

import csv
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from shared.tsdb_reader import fetch_candles
from backtest_strategies import STRATEGIES
from backtest_strategies.base import Signal, StrategyConfig, in_session

BACKTEST_DAYS = 30
SYMBOL        = "XAU_USD"

# Window sizes per timeframe (candles passed to each get_signal call)
WIN_1M  = 60
WIN_5M  = 80
WIN_15M = 100


@dataclass
class Trade:
    strategy:  str
    entry_t:   datetime
    side:      str
    entry_px:  float
    sl:        float
    tp:        float
    exit_px:   float
    exit_t:    datetime
    outcome:   str
    pnl_pts:   float
    reason:    str


def _to_utc(ts):
    if isinstance(ts, pd.Timestamp):
        ts = ts.to_pydatetime()
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


def _simulate_exit_np(direction, entry_px, tp, sl,
                      highs, lows, closes, times, start_idx):
    """Vectorized exit search using pre-extracted numpy arrays."""
    import numpy as np
    n = highs.shape[0]
    if start_idx >= n:
        return "OPEN", entry_px, datetime.now(timezone.utc)

    if direction == "BUY":
        sl_hits = lows[start_idx:]  <= sl
        tp_hits = highs[start_idx:] >= tp
    else:
        sl_hits = highs[start_idx:] >= sl
        tp_hits = lows[start_idx:]  <= tp

    sl_idx = sl_hits.argmax() if sl_hits.any() else -1
    tp_idx = tp_hits.argmax() if tp_hits.any() else -1

    if sl_idx == -1 and tp_idx == -1:
        return "OPEN", float(closes[-1]), times[-1]
    if sl_idx == -1:
        return "TP", tp, times[start_idx + tp_idx]
    if tp_idx == -1:
        return "SL", sl, times[start_idx + sl_idx]
    if sl_idx <= tp_idx:
        return "SL", sl, times[start_idx + sl_idx]
    return "TP", tp, times[start_idx + tp_idx]


def replay(candles_1m: pd.DataFrame, candles_5m: pd.DataFrame, candles_15m: pd.DataFrame,
           strategies: list | None = None, label: str = ""):
    """Walk-forward replay across registered strategies (defaults to all)."""
    if strategies is None:
        strategies = STRATEGIES
    candles_5m  = candles_5m.reset_index(drop=True)
    candles_15m = candles_15m.reset_index(drop=True)
    times_5m   = candles_5m["time"]
    times_15m  = candles_15m["time"]

    trades: list[Trade] = []
    last_entry_t: dict[str, datetime] = {}

    # Pre-extract numpy arrays for fast vectorized exit simulation
    import numpy as np
    import time as _t
    arr_high   = candles_1m["high"].to_numpy(dtype=np.float64)
    arr_low    = candles_1m["low"].to_numpy(dtype=np.float64)
    arr_close  = candles_1m["close"].to_numpy(dtype=np.float64)
    arr_time   = candles_1m["time"].to_numpy()

    n = len(candles_1m)
    _t0 = _t.time()
    _log_every = 10000
    for i in range(max(WIN_1M, 30), n - 1):
        if i % _log_every == 0:
            pct = 100.0 * i / n
            elapsed = _t.time() - _t0
            rate = i / elapsed if elapsed > 0 else 0
            eta = (n - i) / rate if rate > 0 else 0
            tag = f"[{label}]" if label else "[replay]"
            print(f"  {tag} {i:>7d}/{n} ({pct:5.1f}%) trades={len(trades):,} elapsed={elapsed:.0f}s eta={eta:.0f}s", flush=True)
        candle = candles_1m.iloc[i]
        t      = candle["time"]
        tdt    = _to_utc(t)

        w1m  = candles_1m.iloc[max(0, i - WIN_1M):i + 1]

        idx_5m  = times_5m.searchsorted(t, side="right")
        w5m  = candles_5m.iloc[max(0, idx_5m - WIN_5M):idx_5m]

        idx_15m = times_15m.searchsorted(t, side="right")
        w15m = candles_15m.iloc[max(0, idx_15m - WIN_15M):idx_15m]

        for strat in strategies:
            cfg: StrategyConfig = strat.CONFIG
            name = strat.NAME

            if not in_session(tdt, cfg):
                continue

            last_t = last_entry_t.get(name)
            if last_t and (tdt - last_t).total_seconds() < cfg.cooldown_s:
                continue

            try:
                sig: Signal | None = strat.get_signal(w1m, w5m, w15m, tdt)
            except Exception as e:
                # Log first error per strategy then suppress
                if not getattr(strat, "_errored", False):
                    print(f"[{name}] error: {type(e).__name__}: {e}")
                    strat._errored = True
                continue

            if sig is None:
                continue

            # Validate signal directionality
            if sig.side == "BUY" and not (sig.stop_loss < sig.entry_price < sig.take_profit):
                continue
            if sig.side == "SELL" and not (sig.stop_loss > sig.entry_price > sig.take_profit):
                continue

            outcome, exit_px, exit_t = _simulate_exit_np(
                sig.side, sig.entry_price, sig.take_profit, sig.stop_loss,
                arr_high, arr_low, arr_close, arr_time, i + 1,
            )

            if sig.side == "BUY":
                pnl = exit_px - sig.entry_price
            else:
                pnl = sig.entry_price - exit_px

            trades.append(Trade(
                strategy=name,
                entry_t=tdt,
                side=sig.side,
                entry_px=sig.entry_price,
                sl=sig.stop_loss,
                tp=sig.take_profit,
                exit_px=round(exit_px, 2),
                exit_t=exit_t,
                outcome=outcome,
                pnl_pts=round(pnl, 2),
                reason=sig.reason,
            ))

            last_entry_t[name] = tdt

    return trades


def print_comparison(trades: list[Trade], days: int):
    if not trades:
        print("=== No trades fired across any strategy ===")
        return

    # Group by strategy
    by_strat: dict[str, list[Trade]] = {}
    for t in trades:
        by_strat.setdefault(t.strategy, []).append(t)

    print(f"\n{'='*98}")
    print(f"{'Strategy':<14} {'Trades':>7} {'Wins':>6} {'WR%':>6} {'P&L pts':>9} {'Avg W':>7} {'Avg L':>7} {'MaxDD':>7} {'$/day':>7}")
    print("-" * 98)

    # Sort by total P&L descending
    rows = []
    for name, ts in by_strat.items():
        closed = [t for t in ts if t.outcome != "OPEN"]
        if not closed:
            continue
        wins   = [t for t in closed if t.outcome == "TP"]
        losses = [t for t in closed if t.outcome == "SL"]
        total  = sum(t.pnl_pts for t in closed)
        avg_w  = sum(t.pnl_pts for t in wins) / len(wins) if wins else 0
        avg_l  = sum(t.pnl_pts for t in losses) / len(losses) if losses else 0
        wr     = len(wins) / len(closed) * 100

        running = peak = mdd = 0.0
        for t in closed:
            running += t.pnl_pts
            peak    = max(peak, running)
            mdd     = max(mdd, peak - running)
        per_day = total / days

        rows.append((total, name, len(closed), len(wins), wr, total, avg_w, avg_l, mdd, per_day))

    rows.sort(reverse=True)
    for _, name, n, w, wr, total, avg_w, avg_l, mdd, per_day in rows:
        print(f"{name:<14} {n:>7} {w:>6} {wr:>5.1f}% {total:>+8.1f} {avg_w:>+6.1f} {avg_l:>+6.1f} {mdd:>6.1f} {per_day:>+6.2f}")
    print("-" * 98)
    print(f"(at 0.01 lots — 1 pt = $1; multiply $/day by lot scale for live results)")


def save_csv(trades: list[Trade], filepath: str):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["strategy", "entry_time", "side", "entry_px", "sl", "tp",
                         "exit_px", "exit_time", "outcome", "pnl_pts", "reason"])
        for t in trades:
            writer.writerow([t.strategy, t.entry_t, t.side, t.entry_px, t.sl, t.tp,
                             t.exit_px, t.exit_t, t.outcome, t.pnl_pts, t.reason])
    print(f"[CSV] Saved {len(trades)} trades -> {filepath}")


def main():
    print(f"Loaded {len(STRATEGIES)} strategies: {[s.NAME for s in STRATEGIES]}")
    print(f"Fetching {BACKTEST_DAYS}d of 1m/5m/15m candles …")
    c1m  = fetch_candles("1m",  days=BACKTEST_DAYS, symbol=SYMBOL)
    c5m  = fetch_candles("5m",  days=BACKTEST_DAYS, symbol=SYMBOL)
    c15m = fetch_candles("15m", days=BACKTEST_DAYS, symbol=SYMBOL)

    if c1m.empty:
        print("No 1m candles — TimescaleDB empty?")
        return

    print(f"Candles: 1m={len(c1m)} 5m={len(c5m)} 15m={len(c15m)}")
    print("Running walk-forward replay across all strategies …")
    trades = replay(c1m, c5m, c15m)
    print(f"Total signals fired: {len(trades)}")

    days = len({t.entry_t.date() for t in trades}) or 1
    print_comparison(trades, days=days)

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results",
                       f"all_strategies_{stamp}.csv")
    save_csv(trades, out)


if __name__ == "__main__":
    main()
