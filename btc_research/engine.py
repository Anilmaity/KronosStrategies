"""
btc_research/engine.py
----------------------
Lean walk-forward backtest engine for BTC research.

Design (separates fast vectorised signal generation from exit simulation):

  strategy.generate(df) -> list[Entry]      # causal indicators only, NO look-ahead
  run(df, entries, ...) -> list[Trade]      # simulate fills + TP/SL/time exits

Realism / friction (per backtest-expert discipline):
  - Signal fires on a CLOSED bar i; fill at bar i+1 OPEN (no same-bar fill).
  - Round-trip cost (spread + slippage) subtracted from every trade's PnL.
  - Conservative intrabar ordering: if a bar's range spans both SL and TP,
    assume SL hit first.
  - Time-exit (max_hold_bars) closes at that bar's close.

Output rows are protocol.py-compatible:
  strategy, entry_time, direction, entry_price, stop_loss, take_profit,
  exit_price, exit_time, outcome (TP/SL/TIME/OPEN), pnl_pts
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Entry:
    i: int                 # index of the SIGNAL bar (closed). Fill at i+1 open.
    side: str              # 'BUY' | 'SELL'
    stop_loss: float
    take_profit: float
    max_hold_bars: int
    reason: str = ""


@dataclass
class Trade:
    strategy: str
    entry_time: datetime
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    exit_price: float
    exit_time: datetime
    outcome: str           # TP | SL | TIME | OPEN
    pnl_pts: float
    reason: str


def run(df, entries: list[Entry], name: str,
        cost_pts: float = 30.0, cooldown_bars: int = 1,
        max_concurrent: int = 1) -> list[Trade]:
    """Simulate trades. df must have columns time/open/high/low/close (RangeIndex)."""
    times = df["time"].values
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    n = len(df)

    trades: list[Trade] = []
    open_until_bar = -1   # last bar index currently occupied (cooldown / 1-position)

    for e in sorted(entries, key=lambda x: x.i):
        fill_i = e.i + 1
        if fill_i >= n:
            continue
        if fill_i <= open_until_bar:      # respect 1-position + cooldown
            continue

        entry_price = float(o[fill_i])
        side = 1 if e.side == "BUY" else -1
        sl, tp = e.stop_loss, e.take_profit

        outcome, exit_price, exit_i = "OPEN", float(c[-1]), n - 1
        last_bar = min(n - 1, fill_i + e.max_hold_bars)
        for j in range(fill_i, last_bar + 1):
            if side == 1:
                if l[j] <= sl:
                    outcome, exit_price, exit_i = "SL", sl, j; break
                if h[j] >= tp:
                    outcome, exit_price, exit_i = "TP", tp, j; break
            else:
                if h[j] >= sl:
                    outcome, exit_price, exit_i = "SL", sl, j; break
                if l[j] <= tp:
                    outcome, exit_price, exit_i = "TP", tp, j; break
            if j == last_bar:
                outcome, exit_price, exit_i = "TIME", float(c[j]), j

        raw = (exit_price - entry_price) * side
        pnl = raw - cost_pts            # round-trip friction
        trades.append(Trade(
            strategy=name,
            entry_time=pd_ts(times[fill_i]),
            direction=e.side,
            entry_price=round(entry_price, 2),
            stop_loss=round(sl, 2),
            take_profit=round(tp, 2),
            exit_price=round(exit_price, 2),
            exit_time=pd_ts(times[exit_i]),
            outcome=outcome,
            pnl_pts=round(pnl, 2),
            reason=e.reason,
        ))
        open_until_bar = exit_i + cooldown_bars

    return trades


def pd_ts(v):
    import pandas as pd
    return pd.Timestamp(v).to_pydatetime()


def summarize(trades: list[Trade]) -> dict:
    closed = [t for t in trades if t.outcome != "OPEN"]
    if not closed:
        return {"n": 0}
    wins = [t for t in closed if t.pnl_pts > 0]
    losses = [t for t in closed if t.pnl_pts <= 0]
    pnl = sum(t.pnl_pts for t in closed)
    gross_win = sum(t.pnl_pts for t in wins)
    gross_loss = -sum(t.pnl_pts for t in losses)
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    # equity / drawdown
    eq, peak, mdd = 0.0, 0.0, 0.0
    for t in closed:
        eq += t.pnl_pts
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    avg_loss = (sum(t.pnl_pts for t in losses) / len(losses)) if losses else 0.0
    exp = pnl / len(closed)
    exp_R = (exp / abs(avg_loss)) if avg_loss != 0 else 0.0
    return {
        "n": len(closed),
        "wr": round(len(wins) / len(closed) * 100, 1),
        "pnl": round(pnl, 1),
        "pf": round(pf, 2) if pf != float("inf") else 999.0,
        "avg_pnl": round(exp, 2),
        "exp_R": round(exp_R, 3),
        "max_dd": round(mdd, 1),
        "dd_ratio": round(mdd / pnl, 2) if pnl > 0 else float("inf"),
    }


def save_csv(trades: list[Trade], path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cols = ["strategy", "entry_time", "direction", "entry_price", "stop_loss",
            "take_profit", "exit_price", "exit_time", "outcome", "pnl_pts", "reason"]
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for t in trades:
            w.writerow([t.strategy, t.entry_time, t.direction, t.entry_price,
                        t.stop_loss, t.take_profit, t.exit_price, t.exit_time,
                        t.outcome, t.pnl_pts, t.reason])
