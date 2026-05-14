"""
backtest_micro.py — VAR3 (Micro Liquidity Scalper) backtest
------------------------------------------------------------
Replays 30 days of 1m candles through the micro scalper logic.
Reports trades/hour, P&L/hour, win rate, daily-cap impact.

Run:  python backtest/backtest_micro.py
"""

from __future__ import annotations

import os
import sys
import csv
from dataclasses import dataclass, fields, astuple
from datetime import datetime, timezone, date, timedelta

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from shared.tsdb_reader import fetch_candles
from strategy.scalper_engine import get_micro_signal, is_session_active

BACKTEST_DAYS       = 30
SYMBOL              = "XAU_USD"
TP_POINTS           = 1.5      # fixed TP fallback
SL_POINTS           = 1.0
COOLDOWN_S          = 60
MIN_WICK_RATIO      = 1.3
MIN_AVG_RANGE       = 0.5
COUNTER_TREND_MAX   = 4.0
N_POOLS             = 5
POOL_LOOKBACK       = 2
EQUAL_POOLS_ONLY    = False    # all pools — kept too few trades when restricted
KILL_ZONES_ONLY     = False    # full 07-16 UTC window
DAILY_LOSS_LIMIT    = -15.0
DAILY_PROFIT_TARGET = 50.0


@dataclass
class MicroTrade:
    entry_time:  datetime
    direction:   str
    entry_price: float
    stop_loss:   float
    take_profit: float
    exit_price:  float
    exit_time:   datetime
    outcome:     str
    pnl_dollars: float    # for 0.01 lots, pnl_points = pnl_dollars
    is_equal:    bool
    session_date: date


def _to_utc(ts) -> datetime:
    if isinstance(ts, pd.Timestamp):
        ts = ts.to_pydatetime()
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


def _simulate_exit(direction, entry_price, tp, sl, future_1m):
    for _, row in future_1m.iterrows():
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
    last = future_1m.iloc[-1] if not future_1m.empty else None
    return ("OPEN",
            float(last["close"]) if last is not None else entry_price,
            last["time"] if last is not None else datetime.now(timezone.utc))


def replay(candles_1m: pd.DataFrame, candles_5m: pd.DataFrame) -> list[MicroTrade]:
    trades: list[MicroTrade] = []
    last_entry_ts: datetime | None = None

    daily_realized: dict[date, float] = {}
    daily_trades:   dict[date, int]   = {}

    # Precompute 5m time→index for fast slicing
    candles_5m = candles_5m.reset_index(drop=True)
    candles_5m_times = candles_5m["time"]

    n = len(candles_1m)
    # Walk through 1m candles; signal logic fires on completed candles
    # Skip first 30 candles to have history for pools + 5m EMA
    for i in range(30, n - 1):
        candle = candles_1m.iloc[i]
        candle_time = candle["time"]
        candle_dt = _to_utc(candle_time)
        session_date = candle_dt.date()

        # Session filter
        if not is_session_active(candle_dt, kill_zones_only=KILL_ZONES_ONLY):
            continue

        # Cooldown
        if last_entry_ts is not None and (candle_dt - last_entry_ts).total_seconds() < COOLDOWN_S:
            continue

        # Daily caps
        realized = daily_realized.get(session_date, 0.0)
        if realized <= DAILY_LOSS_LIMIT or realized >= DAILY_PROFIT_TARGET:
            continue

        # Rolling 1m window for signal (last 60 candles is plenty for n_pools=5, lookback=2)
        win_1m = candles_1m.iloc[max(0, i - 60):i + 1]

        # Matched 5m window — find 5m candles up to this 1m candle's time
        cutoff_idx = candles_5m_times.searchsorted(candle_time, side="right")
        win_5m = candles_5m.iloc[max(0, cutoff_idx - 30):cutoff_idx]

        signal = get_micro_signal(
            win_1m, win_5m,
            tp_points=TP_POINTS,
            sl_points=SL_POINTS,
            min_wick_ratio=MIN_WICK_RATIO,
            min_avg_range=MIN_AVG_RANGE,
            counter_trend_max_dist=COUNTER_TREND_MAX,
            n_pools=N_POOLS,
            pool_lookback=POOL_LOOKBACK,
            equal_pools_only=EQUAL_POOLS_ONLY,
            kill_zones_only=KILL_ZONES_ONLY,
            now_utc=candle_dt,
            require_session=False,  # already checked above
        )

        if signal is None:
            continue

        # Simulate exit on future 1m candles
        future = candles_1m.iloc[i + 1:]
        outcome, exit_price, exit_time = _simulate_exit(
            signal.side, signal.entry_price, signal.take_profit, signal.stop_loss, future,
        )

        if signal.side == "BUY":
            pnl = exit_price - signal.entry_price
        else:
            pnl = signal.entry_price - exit_price
        pnl = round(pnl, 2)  # 1 pt = $1 for 0.01 lots

        trades.append(MicroTrade(
            entry_time=candle_dt,
            direction=signal.side,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            exit_price=round(exit_price, 2),
            exit_time=exit_time,
            outcome=outcome,
            pnl_dollars=pnl,
            is_equal=signal.is_equal,
            session_date=session_date,
        ))

        daily_realized[session_date] = realized + pnl
        daily_trades[session_date]   = daily_trades.get(session_date, 0) + 1
        last_entry_ts = candle_dt

    return trades


def print_report(trades: list[MicroTrade]):
    if not trades:
        print("=== VAR3 Micro Scalper — no trades ===")
        return

    closed = [t for t in trades if t.outcome != "OPEN"]
    wins   = [t for t in closed  if t.outcome == "TP"]
    losses = [t for t in closed  if t.outcome == "SL"]

    total_pnl = sum(t.pnl_dollars for t in closed)
    avg_win   = sum(t.pnl_dollars for t in wins)   / len(wins)   if wins   else 0
    avg_loss  = sum(t.pnl_dollars for t in losses) / len(losses) if losses else 0
    win_rate  = len(wins) / len(closed) * 100 if closed else 0

    # Trading hours covered — kill zones (4 hrs/day) vs full session (9 hrs/day)
    hrs_per_day = 4 if KILL_ZONES_ONLY else 9
    days = len({t.session_date for t in trades})
    trading_hours = days * hrs_per_day
    trades_per_hour = len(closed) / trading_hours if trading_hours else 0
    pnl_per_hour    = total_pnl / trading_hours if trading_hours else 0

    # Drawdown
    running = peak = max_dd = 0.0
    for t in closed:
        running += t.pnl_dollars
        peak     = max(peak, running)
        max_dd   = max(max_dd, peak - running)

    # Per-day breakdown
    by_day: dict[date, float] = {}
    by_day_n: dict[date, int] = {}
    for t in closed:
        by_day[t.session_date]   = by_day.get(t.session_date, 0) + t.pnl_dollars
        by_day_n[t.session_date] = by_day_n.get(t.session_date, 0) + 1

    winning_days = sum(1 for v in by_day.values() if v > 0)

    print(f"\n=== VAR3 Micro Scalper Backtest — {BACKTEST_DAYS} days ===")
    print(f"Days traded     : {days}")
    print(f"Trading hours   : {trading_hours} (9h/day, 07-16 UTC)")
    print(f"Total trades    : {len(closed)}  ({len(trades)-len(closed)} open)")
    print(f"Trades / hour   : {trades_per_hour:.2f}")
    print(f"Win rate        : {win_rate:.1f}%  ({len(wins)} TP / {len(losses)} SL)")
    print(f"Total P&L       : ${total_pnl:+.2f}")
    print(f"P&L per hour    : ${pnl_per_hour:+.2f}   (target: $5.00)")
    print(f"P&L per day     : ${total_pnl/days:+.2f}")
    print(f"Avg win         : ${avg_win:+.2f}")
    print(f"Avg loss        : ${avg_loss:+.2f}")
    print(f"Max drawdown    : -${max_dd:.2f}")
    print(f"Winning days    : {winning_days}/{days} ({winning_days/days*100:.0f}%)")
    print(f"Best day        : ${max(by_day.values()):+.2f}")
    print(f"Worst day       : ${min(by_day.values()):+.2f}")

    # Equal vs std pool perf
    eq_wins  = [t for t in wins   if t.is_equal]
    eq_total = [t for t in closed if t.is_equal]
    if eq_total:
        eq_rate  = len(eq_wins) / len(eq_total) * 100
        print(f"Equal pool wins : {eq_rate:.1f}%  ({len(eq_total)} trades)")


def save_csv(trades: list[MicroTrade], filepath: str):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    field_names = [f.name for f in fields(MicroTrade)]
    with open(filepath, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(field_names)
        for t in trades:
            writer.writerow(astuple(t))
    print(f"[CSV] Saved {len(trades)} trades → {filepath}")


def main():
    print(f"[VAR3] Fetching {BACKTEST_DAYS}d of 1m+5m candles …")
    candles_1m = fetch_candles("1m", days=BACKTEST_DAYS, symbol=SYMBOL)
    candles_5m = fetch_candles("5m", days=BACKTEST_DAYS, symbol=SYMBOL)

    if candles_1m.empty:
        print("[VAR3] No 1m candle data.")
        return

    print(f"[VAR3] 1m={len(candles_1m)} 5m={len(candles_5m)} candles. Running replay …")
    trades = replay(candles_1m, candles_5m)

    print_report(trades)

    stamp = datetime.now().strftime("%Y%m%d")
    results_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
    save_csv(trades, os.path.join(results_dir, f"var3_micro_backtest_{stamp}.csv"))


if __name__ == "__main__":
    main()
