"""
analyze.py — summarize backtest CSVs.

Usage: python analyze.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

RESULTS = Path(__file__).parent / "results"


def _max_drawdown(pnl: pd.Series) -> float:
    cum = pnl.cumsum()
    peak = cum.cummax()
    return float((cum - peak).min())


def _summarize(df: pd.DataFrame, pnl_col: str, label: str, group_col: str | None = None) -> pd.DataFrame:
    def _row(sub: pd.DataFrame) -> dict:
        closed = sub[sub["outcome"].isin(["TP", "SL"])]
        wins = closed[closed["outcome"] == "TP"]
        losses = closed[closed["outcome"] == "SL"]
        n = len(closed)
        wr = (len(wins) / n * 100) if n else 0.0
        return {
            "trades": n,
            "open": int((sub["outcome"] == "OPEN").sum()),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate%": round(wr, 1),
            "total_pnl": round(float(closed[pnl_col].sum()), 2),
            "avg_win": round(float(wins[pnl_col].mean()), 2) if len(wins) else 0.0,
            "avg_loss": round(float(losses[pnl_col].mean()), 2) if len(losses) else 0.0,
            "expectancy": round(float(closed[pnl_col].mean()), 3) if n else 0.0,
            "max_dd": round(_max_drawdown(closed[pnl_col]), 2) if n else 0.0,
            "profit_factor": round(
                float(wins[pnl_col].sum()) / abs(float(losses[pnl_col].sum())), 2
            ) if len(losses) and float(losses[pnl_col].sum()) != 0 else float("inf"),
        }

    if group_col and group_col in df.columns:
        rows = {g: _row(sub) for g, sub in df.groupby(group_col)}
        rows["__ALL__"] = _row(df)
        out = pd.DataFrame(rows).T
    else:
        out = pd.DataFrame([_row(df)], index=[label])
    return out


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)


def main() -> None:
    files = [
        ("VAR1 (5m scalper)",       "var1_backtest_20260511.csv",       "pnl_points"),
        ("VAR2 (15m->5m->1m sweep)",  "var2_backtest_20260511.csv",       "pnl_points"),
        ("VAR3 (micro scalper)",    "var3_micro_backtest_20260511.csv", "pnl_dollars"),
        ("VAR1 equal-pool only",    "var1_eq_backtest_20260511.csv",    "pnl_points"),
        ("VAR2 equal-pool only",    "var2_eq_backtest_20260511.csv",    "pnl_points"),
    ]

    print("=" * 100)
    print("PER-STRATEGY SUMMARY  (VAR1/VAR2 = pnl_points; VAR3 = pnl_dollars)")
    print("=" * 100)
    summaries = []
    for label, fname, pnl_col in files:
        path = RESULTS / fname
        if not path.exists():
            print(f"[skip] {fname} not found")
            continue
        df = _read(path)
        s = _summarize(df, pnl_col=pnl_col, label=label)
        s.index = [label]
        summaries.append(s)
    overall = pd.concat(summaries)
    print(overall.to_string())
    print()

    # ── TP-mode breakdown for VAR1 / VAR2 ─────────────────────────────────────
    for label, fname in [
        ("VAR1 by TP mode", "var1_backtest_20260511.csv"),
        ("VAR2 by TP mode", "var2_backtest_20260511.csv"),
    ]:
        path = RESULTS / fname
        if not path.exists():
            continue
        df = _read(path)
        print("=" * 100)
        print(label)
        print("=" * 100)
        print(_summarize(df, pnl_col="pnl_points", label=label, group_col="tp_mode").to_string())
        print()

    # ── Equal-pool effect ────────────────────────────────────────────────────
    for label, fname in [
        ("VAR1 by is_equal_pool", "var1_backtest_20260511.csv"),
        ("VAR2 by is_equal_pool", "var2_backtest_20260511.csv"),
    ]:
        path = RESULTS / fname
        if not path.exists():
            continue
        df = _read(path)
        print("=" * 100)
        print(label)
        print("=" * 100)
        print(_summarize(df, pnl_col="pnl_points", label=label, group_col="is_equal_pool").to_string())
        print()

    # ── Direction split ──────────────────────────────────────────────────────
    for label, fname in [
        ("VAR1 by direction", "var1_backtest_20260511.csv"),
        ("VAR2 by direction", "var2_backtest_20260511.csv"),
    ]:
        path = RESULTS / fname
        if not path.exists():
            continue
        df = _read(path)
        print("=" * 100)
        print(label)
        print("=" * 100)
        print(_summarize(df, pnl_col="pnl_points", label=label, group_col="direction").to_string())
        print()

    # VAR3 has its own column name
    p3 = RESULTS / "var3_micro_backtest_20260511.csv"
    if p3.exists():
        df = _read(p3)
        print("=" * 100)
        print("VAR3 by direction")
        print("=" * 100)
        print(_summarize(df, pnl_col="pnl_dollars", label="VAR3", group_col="direction").to_string())
        print()
        print("=" * 100)
        print("VAR3 by is_equal")
        print("=" * 100)
        print(_summarize(df, pnl_col="pnl_dollars", label="VAR3", group_col="is_equal").to_string())
        print()

    # ── Latest combined run ──────────────────────────────────────────────────
    combined_files = sorted(RESULTS.glob("all_strategies_*.csv"))
    if combined_files:
        latest = combined_files[-1]
        df = _read(latest)
        print("=" * 100)
        print(f"COMBINED RUN — {latest.name}  (group by `strategy`)")
        print("=" * 100)
        print(_summarize(df, pnl_col="pnl_pts", label="combined", group_col="strategy").to_string())


if __name__ == "__main__":
    main()
