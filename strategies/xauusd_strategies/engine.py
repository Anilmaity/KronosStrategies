"""Generic ICT/SMC backtest engine.

Each strategy provides a `detect_setup(df, i, atr) -> Optional[PendingOrder]`
function. The engine handles fills, SL/TP, invalidation, costs, metrics.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

# defaults (overridable per strategy via run_backtest kwargs)
ATR_PERIOD = 14
VALID_BARS = 96
SPREAD = 0.30


@dataclass
class PendingOrder:
    side: str  # 'long' | 'short'
    entry: float
    stop: float
    tp: float
    invalidate_below: float   # long: close < this -> cancel
    invalidate_above: float   # short: close > this -> cancel
    detected_idx: int
    order_type: str = "limit"  # 'limit' or 'market'


@dataclass
class Trade:
    side: str
    entry_time: pd.Timestamp
    entry: float
    stop: float
    tp: float
    exit_time: pd.Timestamp
    exit: float
    reason: str
    r_multiple: float


def atr(df: pd.DataFrame, n: int = ATR_PERIOD) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


Detector = Callable[[pd.DataFrame, int, float], Optional[PendingOrder]]


def _r_mult(side: str, entry: float, stop: float, exit_px: float) -> float:
    risk = abs(entry - stop)
    if risk == 0:
        return 0.0
    if side == "long":
        return (exit_px - entry) / risk
    return (entry - exit_px) / risk


def run_backtest(df: pd.DataFrame, detector: Detector,
                 valid_bars: int = VALID_BARS,
                 spread: float = SPREAD,
                 atr_period: int = ATR_PERIOD) -> list[Trade]:
    df = df.copy()
    df["atr"] = atr(df, atr_period)
    trades: list[Trade] = []
    pending: Optional[PendingOrder] = None
    position: Optional[PendingOrder] = None
    position_entry_time: Optional[pd.Timestamp] = None
    times = df.index.to_list()

    for i in range(len(df)):
        bar = df.iloc[i]
        a = bar["atr"]
        if np.isnan(a):
            continue

        # 1) manage open position
        if position is not None:
            if position.side == "long":
                hit_sl = bar["low"] <= position.stop
                hit_tp = bar["high"] >= position.tp
            else:
                hit_sl = bar["high"] >= position.stop
                hit_tp = bar["low"] <= position.tp

            if hit_sl:  # conservative: SL first when both hit
                exit_px = position.stop - spread if position.side == "long" else position.stop + spread
                trades.append(Trade(position.side, position_entry_time, position.entry,
                                    position.stop, position.tp, times[i], exit_px, "sl",
                                    _r_mult(position.side, position.entry, position.stop, exit_px)))
                position = None
            elif hit_tp:
                exit_px = position.tp - spread if position.side == "long" else position.tp + spread
                trades.append(Trade(position.side, position_entry_time, position.entry,
                                    position.stop, position.tp, times[i], exit_px, "tp",
                                    _r_mult(position.side, position.entry, position.stop, exit_px)))
                position = None

        # 2) pending fill / invalidate / expire
        if position is None and pending is not None:
            age = i - pending.detected_idx
            invalidated = (
                (pending.side == "long" and bar["close"] < pending.invalidate_below) or
                (pending.side == "short" and bar["close"] > pending.invalidate_above)
            )
            if age > valid_bars or invalidated:
                pending = None
            else:
                filled = False
                if pending.order_type == "market":
                    filled = True
                    fill_px = bar["open"]
                elif bar["low"] <= pending.entry <= bar["high"]:
                    filled = True
                    fill_px = pending.entry
                if filled:
                    adj = spread if pending.side == "long" else -spread
                    new_entry = fill_px + adj
                    # recompute TP keeping same R distance
                    risk = abs(pending.entry - pending.stop)
                    tp = new_entry + (pending.tp - pending.entry)  # preserve target offset
                    position = PendingOrder(pending.side, new_entry, pending.stop, tp,
                                            pending.invalidate_below, pending.invalidate_above,
                                            pending.detected_idx, pending.order_type)
                    position_entry_time = times[i]
                    pending = None

        # 3) scan for new setup
        if position is None and pending is None:
            setup = detector(df, i, a)
            if setup is not None:
                pending = setup

    return trades


def metrics(trades: list[Trade]) -> dict:
    if not trades:
        return {"trades": 0}
    rs = np.array([t.r_multiple for t in trades])
    wins = rs[rs > 0]
    losses = rs[rs <= 0]
    equity = np.cumsum(rs)
    peak = np.maximum.accumulate(equity)
    dd = equity - peak
    pf = wins.sum() / -losses.sum() if losses.size and losses.sum() < 0 else float("inf")
    sharpe = (rs.mean() / rs.std() * np.sqrt(252)) if rs.std() > 0 else 0.0
    return {
        "trades": len(trades),
        "wins": int((rs > 0).sum()),
        "losses": int((rs <= 0).sum()),
        "win_rate_%": round(100 * (rs > 0).mean(), 2),
        "total_R": round(float(rs.sum()), 2),
        "avg_R": round(float(rs.mean()), 3),
        "expectancy_R": round(float(rs.mean()), 3),
        "profit_factor": round(float(pf), 2),
        "max_dd_R": round(float(dd.min()), 2),
        "sharpe_trade_basis": round(float(sharpe), 2),
        "longs": int(sum(1 for t in trades if t.side == "long")),
        "shorts": int(sum(1 for t in trades if t.side == "short")),
        "tp_exits": int(sum(1 for t in trades if t.reason == "tp")),
        "sl_exits": int(sum(1 for t in trades if t.reason == "sl")),
    }


def save_results(trades: list[Trade], out_dir: Path, name: str, notes: str = "") -> dict:
    out_dir.mkdir(exist_ok=True)
    if trades:
        pd.DataFrame([asdict(t) for t in trades]).to_csv(out_dir / "trades.csv", index=False)
    m = metrics(trades)
    lines = [name, "=" * len(name)]
    for k, v in m.items():
        lines.append(f"{k:>22}: {v}")
    if notes:
        lines += ["", notes]
    (out_dir / "report.txt").write_text("\n".join(lines))
    return m
