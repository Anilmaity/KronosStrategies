"""
Merge research-family + live-extras backtest CSVs into one combined view
and regenerate the Excel report with all strategies.
"""
from __future__ import annotations

import glob
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))

RESEARCH_CSV = os.path.join(ROOT, "all_strategies_cache_mp_20260516_0212.csv")
import re
def _is_combined(p):
    base = os.path.basename(p)
    m = re.match(r"live_extras_\d{8}_\d{4}\.csv$", base)
    return m is not None
EXTRAS_GLOB  = os.path.join(ROOT, "strategies", "backtest", "results", "live_extras_*.csv")
OUT_XLSX     = os.path.join(ROOT, "strategy_pnl_all_live.xlsx")


def daily_stats(sub: pd.DataFrame, full_idx) -> dict:
    daily = sub.groupby("date")["pnl_pts"].sum().reindex(full_idx, fill_value=0.0)
    cum = daily.cumsum()
    peak = cum.cummax()
    dd = cum - peak
    sharpe = (daily.mean() / daily.std() * np.sqrt(252)) if daily.std() > 0 else np.nan
    monthly = sub.groupby("month")["pnl_pts"].sum()
    sharpe_m = (monthly.mean() / monthly.std() * np.sqrt(12)) if monthly.std() > 0 else np.nan
    n = len(sub)
    wins = (sub["outcome"] == "TP").sum()
    return dict(
        trades=n,
        win_rate_pct=round(wins / n * 100, 2) if n else 0,
        total_pnl=round(sub["pnl_pts"].sum(), 2),
        avg_pnl=round(sub["pnl_pts"].mean(), 4) if n else 0,
        daily_sharpe=round(sharpe, 3) if pd.notna(sharpe) else None,
        monthly_sharpe=round(sharpe_m, 3) if pd.notna(sharpe_m) else None,
        max_drawdown_pts=round(dd.min(), 2),
        pnl_to_dd=round(sub["pnl_pts"].sum() / abs(dd.min()), 2) if dd.min() < 0 else None,
    )


def main():
    extras_files = sorted([p for p in glob.glob(EXTRAS_GLOB) if _is_combined(p)])
    if not extras_files:
        sys.exit(f"No combined extras CSV found at {EXTRAS_GLOB}")
    latest_extras = extras_files[-1]
    print(f"Research CSV : {RESEARCH_CSV}")
    print(f"Extras CSV   : {latest_extras}")

    df1 = pd.read_csv(RESEARCH_CSV, parse_dates=["entry_time"])
    df2 = pd.read_csv(latest_extras, parse_dates=["entry_time"])
    df = pd.concat([df1, df2], ignore_index=True)
    df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True, errors="coerce").dt.tz_localize(None)
    df["date"] = df["entry_time"].dt.date
    df["month"] = df["entry_time"].dt.to_period("M").astype(str)
    df = df[df["outcome"].isin(["TP", "SL", "OPEN"])]
    print(f"Total trades: {len(df):,}  ({df['strategy'].nunique()} strategies)")

    full_idx = pd.date_range(df["date"].min(), df["date"].max(), freq="D").date

    rows = []
    for strat in sorted(df["strategy"].unique()):
        s = daily_stats(df[df["strategy"] == strat], full_idx)
        s["strategy"] = strat
        rows.append(s)
    risk = pd.DataFrame(rows).set_index("strategy").sort_values("total_pnl", ascending=False)
    print("\n=== Combined risk table ===")
    print(risk.to_string())

    monthly = df.pivot_table(index="month", columns="strategy", values="pnl_pts", aggfunc="sum").fillna(0).round(2)
    monthly.loc["TOTAL"] = monthly.sum()

    daily = df.pivot_table(index="date", columns="strategy", values="pnl_pts", aggfunc="sum").fillna(0).round(2)

    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as xw:
        risk.to_excel(xw, sheet_name="RISK")
        # Summary (subset)
        summary = risk[["trades", "win_rate_pct", "total_pnl", "avg_pnl", "monthly_sharpe", "max_drawdown_pts", "pnl_to_dd"]]
        summary.to_excel(xw, sheet_name="SUMMARY")
        monthly.to_excel(xw, sheet_name="MONTHLY_ALL")
        daily.to_excel(xw, sheet_name="DAILY_ALL")
        for strat in sorted(df["strategy"].unique()):
            sub = df[df["strategy"] == strat]
            m = sub.groupby("month")["pnl_pts"].agg(["sum", "count", "mean"]).round(3)
            m.columns = ["pnl_pts", "trades", "avg_pnl"]
            m["cum_pnl"] = m["pnl_pts"].cumsum().round(2)
            d = sub.groupby("date")["pnl_pts"].agg(["sum", "count", "mean"]).round(3)
            d.columns = ["pnl_pts", "trades", "avg_pnl"]
            d["cum_pnl"] = d["pnl_pts"].cumsum().round(2)
            sheet = strat[:31]
            m.to_excel(xw, sheet_name=sheet, startrow=0, startcol=0)
            d.to_excel(xw, sheet_name=sheet, startrow=0, startcol=m.shape[1] + 2)
    print(f"\n[XLSX] {OUT_XLSX}")


if __name__ == "__main__":
    main()
