"""
walk_forward.py
---------------
Rolling walk-forward analysis on a backtest trade CSV.

Given a trade log (any backtest_all_*.csv with columns including
strategy, entry_time, outcome, pnl_pts), split into N anchored or
rolling windows and report per-window IS/OOS metrics + Walk-Forward
Efficiency (OOS_return / IS_return).

Usage:
  python backtest/walk_forward.py results/all_strategies_cache_20260512_1030.csv \
      --strategy M90_BIAS --n-windows 6 --is-frac 0.7
"""
from __future__ import annotations

import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def _stats(closed: pd.DataFrame) -> dict:
    n = len(closed)
    if n == 0:
        return {"n": 0, "wr": 0.0, "pnl": 0.0, "pf": 0.0, "exp": 0.0, "max_dd": 0.0}
    wins   = closed[closed["outcome"] == "TP"]
    losses = closed[closed["outcome"] == "SL"]
    pnl_col = "pnl_pts" if "pnl_pts" in closed.columns else "pnl_points"
    cum = closed[pnl_col].cumsum()
    pf  = (wins[pnl_col].sum() / abs(losses[pnl_col].sum())) if len(losses) and float(losses[pnl_col].sum()) != 0 else float("inf")
    return {
        "n":      n,
        "wr":     round(len(wins) / n * 100, 1),
        "pnl":    round(float(closed[pnl_col].sum()), 2),
        "pf":     round(pf, 2),
        "exp":    round(float(closed[pnl_col].mean()), 3),
        "max_dd": round(float((cum - cum.cummax()).min()), 2),
    }


def walk_forward(
    trades: pd.DataFrame,
    n_windows: int = 6,
    is_frac: float = 0.7,
    anchored: bool = False,
) -> pd.DataFrame:
    """Split trades by entry_time into n rolling windows of (IS, OOS) and return per-window stats.

    anchored=True keeps the IS start fixed at the first trade (anchored walk-forward).
    anchored=False rolls both edges (rolling window).
    """
    df = trades.copy()
    df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True, errors="coerce")
    df = df.dropna(subset=["entry_time"]).sort_values("entry_time").reset_index(drop=True)

    closed = df[df["outcome"].isin(["TP", "SL"])].reset_index(drop=True)
    if closed.empty:
        return pd.DataFrame()

    t_min = closed["entry_time"].min()
    t_max = closed["entry_time"].max()
    total_span = (t_max - t_min).total_seconds()
    if total_span <= 0:
        return pd.DataFrame()

    rows = []
    win_span_s = total_span / n_windows
    for w in range(n_windows):
        if anchored:
            is_start  = t_min
        else:
            is_start  = t_min + pd.Timedelta(seconds=w * win_span_s)
        is_end_off  = (w + is_frac) if not anchored else (w + is_frac)
        is_end      = t_min + pd.Timedelta(seconds=is_end_off * win_span_s)
        oos_end     = t_min + pd.Timedelta(seconds=(w + 1) * win_span_s)
        if is_end >= oos_end or oos_end > t_max + pd.Timedelta(seconds=1):
            continue

        is_  = closed[(closed["entry_time"] >= is_start) & (closed["entry_time"] < is_end)]
        oos_ = closed[(closed["entry_time"] >= is_end)   & (closed["entry_time"] < oos_end)]
        is_s  = _stats(is_)
        oos_s = _stats(oos_)
        wfe   = (oos_s["pnl"] / is_s["pnl"]) if is_s["pnl"] not in (0, 0.0) else float("nan")

        rows.append({
            "window":      w + 1,
            "is_start":    is_start.strftime("%Y-%m-%d"),
            "is_end":      is_end.strftime("%Y-%m-%d"),
            "oos_end":     oos_end.strftime("%Y-%m-%d"),
            "is_n":        is_s["n"],
            "is_wr":       is_s["wr"],
            "is_pnl":      is_s["pnl"],
            "is_pf":       is_s["pf"],
            "oos_n":       oos_s["n"],
            "oos_wr":      oos_s["wr"],
            "oos_pnl":     oos_s["pnl"],
            "oos_pf":      oos_s["pf"],
            "wfe":         round(wfe, 2) if wfe == wfe else float("nan"),
        })

    return pd.DataFrame(rows)


def summarize(wf: pd.DataFrame) -> dict:
    if wf.empty:
        return {}
    return {
        "windows":       len(wf),
        "avg_is_wr":     round(wf["is_wr"].mean(), 1),
        "avg_oos_wr":    round(wf["oos_wr"].mean(), 1),
        "avg_wfe":       round(wf["wfe"].mean(skipna=True), 2),
        "oos_wins_n":    int((wf["oos_pnl"] > 0).sum()),
        "oos_total_pnl": round(wf["oos_pnl"].sum(), 2),
        "consistency%":  round((wf["oos_pnl"] > 0).sum() / len(wf) * 100, 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", help="path to backtest trade CSV")
    ap.add_argument("--strategy", default=None, help="filter to one strategy NAME")
    ap.add_argument("--n-windows", type=int, default=6)
    ap.add_argument("--is-frac",   type=float, default=0.7, help="IS fraction within each window (0 < x < 1)")
    ap.add_argument("--anchored",  action="store_true", help="anchored walk-forward (IS start fixed)")
    args = ap.parse_args()

    df = pd.read_csv(args.csv)
    if "strategy" in df.columns and args.strategy:
        df = df[df["strategy"] == args.strategy]
        if df.empty:
            print(f"No trades for strategy '{args.strategy}'")
            return

    label = args.strategy or "ALL"
    print(f"Walk-forward — strategy={label}  windows={args.n_windows}  is_frac={args.is_frac}  anchored={args.anchored}")
    wf = walk_forward(df, n_windows=args.n_windows, is_frac=args.is_frac, anchored=args.anchored)
    if wf.empty:
        print("No usable windows.")
        return
    print(wf.to_string(index=False))
    print()
    s = summarize(wf)
    print(f"SUMMARY: avg IS WR={s['avg_is_wr']}%  avg OOS WR={s['avg_oos_wr']}%  "
          f"avg WFE={s['avg_wfe']}  OOS positive windows={s['oos_wins_n']}/{s['windows']} "
          f"({s['consistency%']}%)  OOS total PnL={s['oos_total_pnl']}")


if __name__ == "__main__":
    main()
