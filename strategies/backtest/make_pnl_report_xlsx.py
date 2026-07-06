"""
make_pnl_report_xlsx.py
-----------------------
Excel P&L report for the deployed manager roster (2026-07-06 configs) over the
last 3 months of the local bars cache. Simulates the four deployed strategies exactly
as deployed on the new account (0.01 lot each) and writes:

  Sheet "Summary"   — per-strategy + portfolio: trades, WR, PF, net, per-day
                      expectation, risk metrics (avg/max risk per trade, worst
                      day, max drawdown, kill-switch context)
  Sheet "Daily PnL" — one row per trading day: per-strategy $, portfolio $,
                      cumulative $
  Sheet "Trades"    — full trade log (entry/exit time+price, SL, TP, outcome)

Run (from strategies/):  python -m backtest.make_pnl_report_xlsx
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backtest.optimize_manager_strategies import (  # noqa: E402
    load, run_orb, run_donchian_h4, run_mss_fvg,
)

LOT = 0.01                    # deployed MANAGER_CHILD_LOT on the new account
USD_PER_PT = LOT * 100.0      # XAUUSD: $1/pt at 0.01 lot
MONTHS = 3
OUT = Path("/Users/anilmaity/PycharmProjects/Kronos/Manager_PnL_Report_last3mo.xlsx")
TICK_DIR = Path(__file__).resolve().parents[2] / ".history_data" / "XAU_USD"


# ── tick-accurate exit refinement (.history_data ~1s ticks, thru 2026-05-19) ──

_tick_cache: dict = {}


def _load_day_ticks(day) -> tuple | None:
    """(times_ns int64 array, prices float64 array) for one UTC date, or None
    when the day-file is absent. Files are D_M_YYYY.json: nested lists of
    {"time", "price"} dicts (the S5-split generator output)."""
    if day in _tick_cache:
        return _tick_cache[day]
    f = TICK_DIR / f"{day.day}_{day.month}_{day.year}.json"
    if not f.exists():
        _tick_cache[day] = None
        return None
    import json
    groups = json.loads(f.read_text())
    ts, px = [], []
    for g in groups:
        for t in g:
            ts.append(t["time"]); px.append(t["price"])
    # Force ns resolution: newer pandas parses these strings as datetime64[us],
    # and int64 comparisons against Timestamp.value (ns) would silently miss.
    times = (pd.to_datetime(pd.Series(ts), utc=True)
             .astype("datetime64[ns, UTC]").astype("int64").to_numpy())
    prices = pd.Series(px, dtype="float64").to_numpy()
    order = times.argsort()
    _tick_cache[day] = (times[order], prices[order])
    return _tick_cache[day]


def _ticks_between(t0: pd.Timestamp, t1: pd.Timestamp):
    """Concatenated (times_ns, prices) covering [t0, t1]; None if ANY day in
    the span lacks a tick file (partial coverage would bias the walk)."""
    import numpy as np
    days = pd.date_range(t0.normalize(), t1.normalize(), freq="D")
    parts = []
    for d in days:
        got = _load_day_ticks(d.date())
        if got is None:
            return None
        parts.append(got)
    times = np.concatenate([p[0] for p in parts])
    prices = np.concatenate([p[1] for p in parts])
    lo = times.searchsorted(t0.value, side="left")
    hi = times.searchsorted(t1.value, side="right")
    return times[lo:hi], prices[lo:hi]


def refine_exit_with_ticks(row: dict, fill_delay: pd.Timedelta,
                           bar_len: pd.Timedelta) -> dict:
    """Re-decide the exit of one bar-simulated trade on the tick stream:
    the first tick crossing SL or TP (position_monitor semantics) wins,
    removing the bar walk's SL-first same-bar pessimism. Time/EOD exits and
    trades without full tick coverage keep the bar result.

    bar_len extends the walk to the exit bar's CLOSE — bars are open-stamped,
    so ending at exit_time would exclude the bar where the cross happened."""
    t_entry = pd.Timestamp(row["entry_time"], tz="UTC")
    t_exit = pd.Timestamp(row["exit_time"], tz="UTC") + bar_len
    got = _ticks_between(t_entry, t_exit)
    if got is None:
        row["exit_basis"] = "bars (no tick coverage)"
        return row
    times, prices = got
    is_buy = row["side"] == "BUY"
    # Fill moment: ORB fills intrabar at the boundary touch; close-fill
    # strategies fill at the signal bar's close (entry_time + bar length).
    if fill_delay == pd.Timedelta(0):   # intrabar touch fill
        if row.get("retrace"):
            # limit-style retrace fill (MSS+FVG): BUY fills as price FALLS
            # to the edge, SELL as it RISES
            cross = (prices <= row["entry"]) if is_buy else (prices >= row["entry"])
        else:
            # breakout fill (ORB): BUY fills as price RISES to the boundary
            cross = (prices >= row["entry"]) if is_buy else (prices <= row["entry"])
        idx = cross.argmax() if cross.any() else 0
        start = idx + 1
    else:
        start = times.searchsorted((t_entry + fill_delay).value, side="left")
    sl, tp = row["sl"], row["tp"]
    for k in range(start, len(prices)):
        p = prices[k]
        if is_buy:
            if p <= sl:
                out, px = "SL", sl
            elif p >= tp:
                out, px = "TP", tp
            else:
                continue
        else:
            if p >= sl:
                out, px = "SL", sl
            elif p <= tp:
                out, px = "TP", tp
            else:
                continue
        pnl_pts = (px - row["entry"] if is_buy else row["entry"] - px) - 0.45
        row.update(exit=round(px, 2), outcome=out,
                   exit_time=pd.Timestamp(times[k]).tz_localize("UTC").tz_convert(None),
                   pnl_pts=round(pnl_pts, 2),
                   pnl_usd=round(pnl_pts * USD_PER_PT, 2),
                   exit_basis="ticks")
        return row
    row["exit_basis"] = "bars (no tick cross; time/EOD exit)"
    return row


def trades_df(trades, name):
    rows = []
    for t in trades:
        rows.append({
            "strategy": name,
            "entry_time": t.t_entry.tz_convert(None) if getattr(t.t_entry, "tzinfo", None) else t.t_entry,
            "side": "BUY" if t.side > 0 else "SELL",
            "entry": round(t.entry, 2),
            "sl": round(t.sl, 2),
            "tp": round(t.tp, 2),
            "exit_time": t.t_exit.tz_convert(None) if getattr(t.t_exit, "tzinfo", None) else t.t_exit,
            "exit": round(t.exit, 2),
            "outcome": t.outcome,
            "pnl_pts": round(t.pnl, 2),
            "pnl_usd": round(t.pnl * USD_PER_PT, 2),
            "risk_pts": round(abs(t.entry - t.sl), 2),
            "risk_usd": round(abs(t.entry - t.sl) * USD_PER_PT, 2),
        })
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame, label: str, n_days: int) -> dict:
    if df.empty:
        return {"Strategy": label, "Trades": 0}
    wins = df["pnl_usd"] > 0
    gp = df.loc[wins, "pnl_usd"].sum()
    gl = -df.loc[~wins, "pnl_usd"].sum()
    daily = df.groupby(df["entry_time"].dt.date)["pnl_usd"].sum()
    # max drawdown on the cumulative daily curve
    cum = daily.cumsum()
    dd = (cum - cum.cummax()).min() if len(cum) else 0.0
    return {
        "Strategy": label,
        "Trades": len(df),
        "Trades/day": round(len(df) / n_days, 2),
        "Win rate %": round(100.0 * wins.mean(), 1),
        "Profit factor": round(gp / gl, 2) if gl > 0 else "inf",
        "Net USD": round(df["pnl_usd"].sum(), 2),
        "Avg USD/trade": round(df["pnl_usd"].mean(), 2),
        "Expected USD/day": round(df["pnl_usd"].sum() / n_days, 2),
        "Avg win USD": round(df.loc[wins, "pnl_usd"].mean(), 2) if wins.any() else 0,
        "Avg loss USD": round(df.loc[~wins, "pnl_usd"].mean(), 2) if (~wins).any() else 0,
        "Biggest win USD": round(df["pnl_usd"].max(), 2),
        "Biggest loss USD": round(df["pnl_usd"].min(), 2),
        "Avg risk/trade USD": round(df["risk_usd"].mean(), 2),
        "Max risk/trade USD": round(df["risk_usd"].max(), 2),
        "Best day USD": round(daily.max(), 2),
        "Worst day USD": round(daily.min(), 2),
        "Green days %": round(100.0 * (daily > 0).mean(), 1),
        "Max drawdown USD": round(dd, 2),
    }


def main():
    m5, h4, h1 = load("5m"), load("4h"), load("1h")
    end = m5["time"].max()
    start = end - pd.DateOffset(months=MONTHS)
    print(f"window: {start.date()} -> {end.date()}, lot={LOT} (${USD_PER_PT:.0f}/pt)")

    runs = {
        "Session ORB (S95/live)": (
            run_orb(m5, or_min=30, tp_frac=0.8, sl_frac=2.0),
            pd.Timedelta(0), pd.Timedelta(minutes=5)),
        "Reversal S99 (MSS+FVG)": (
            run_mss_fvg(m5, sweep_n=48, tp_r=1.5, retrace_w=24,
                        hours=tuple(range(1, 16))),
            pd.Timedelta(0), pd.Timedelta(minutes=5)),
        "Momentum S96 (H1 Don24)": (
            run_donchian_h4(h1, n=24, k_atr=3.0, tp_r=0.4, hold_bars=0),
            pd.Timedelta(hours=1), pd.Timedelta(hours=1)),
        "Trend CHALLENGE_XAU (H4)": (
            run_donchian_h4(h4, n=20, k_atr=4.0, tp_r=0.4, hold_bars=0),
            pd.Timedelta(hours=4), pd.Timedelta(hours=4)),
    }
    frames = []
    for name, (trades, fill_delay, bar_len) in runs.items():
        df = trades_df(trades, name)
        cutoff = start.tz_localize(None) if df.empty or df["entry_time"].dt.tz is None else start
        df = df[df["entry_time"] >= cutoff].reset_index(drop=True)
        # Tick-accurate exit pass (~1s ticks in .history_data, thru 2026-05-19)
        recs = df.to_dict("records")
        for r in recs:
            r["retrace"] = name.startswith("Reversal")
        refined = [refine_exit_with_ticks(dict(r), fill_delay, bar_len)
                   for r in recs]
        frames.append(pd.DataFrame(refined))
    all_trades = pd.concat(frames, ignore_index=True).sort_values("entry_time")
    n_tick = (all_trades["exit_basis"] == "ticks").sum()
    print(f"tick-verified exits: {n_tick}/{len(all_trades)}")

    # trading days in window (days with at least one M5 bar)
    m5w = m5[m5["time"] >= start]
    n_days = m5w["time"].dt.date.nunique()

    # ── Summary ────────────────────────────────────────────────────────────────
    summary_rows = [summarize(df, name, n_days)
                    for df, name in zip(frames, runs.keys())]
    summary_rows.append(summarize(all_trades, "PORTFOLIO (all 4)", n_days))
    summary = pd.DataFrame(summary_rows)

    # ── Daily pivot ────────────────────────────────────────────────────────────
    piv = (all_trades
           .assign(day=all_trades["entry_time"].dt.date)
           .pivot_table(index="day", columns="strategy", values="pnl_usd",
                        aggfunc="sum")
           .fillna(0.0)
           .round(2))
    piv["Portfolio USD"] = piv.sum(axis=1).round(2)
    piv["Cumulative USD"] = piv["Portfolio USD"].cumsum().round(2)
    piv = piv.reset_index()

    # ── Write workbook ─────────────────────────────────────────────────────────
    ctx = pd.DataFrame({
        "Note": [
            f"Simulated on OANDA mid data {start.date()} -> {end.date()} "
            f"({n_days} trading days), cost 0.45pt/trade, {LOT} lot per "
            f"strategy (deployed MANAGER_CHILD_LOT).",
            f"Exits tick-verified on the local ~1s tick history for "
            f"{n_tick}/{len(all_trades)} trades (tick files end 2026-05-19; "
            f"later trades use the conservative bar walk, SL-before-TP).",
            "Configs = exactly what was deployed paused on account 4dbc9ccb "
            "on 2026-07-06 (commit e6d9ced).",
            "Daily kill-switch on the account: -$150 realized/day; max 3 "
            "concurrent positions (ManagerConfig).",
            "Dollars scale linearly with lot size: at 0.02 lot, double every "
            "$ figure.",
            "This window (Apr-Jul 2026) was a strong gold trend - treat the "
            "per-day expectation as optimistic; the 2025 train half ran at "
            "roughly a third of this rate.",
        ]
    })
    with pd.ExcelWriter(OUT, engine="openpyxl") as xl:
        summary.to_excel(xl, sheet_name="Summary", index=False)
        ctx.to_excel(xl, sheet_name="Summary", index=False,
                     startrow=len(summary) + 3)
        piv.to_excel(xl, sheet_name="Daily PnL", index=False)
        all_trades.to_excel(xl, sheet_name="Trades", index=False)

    # column widths
    from openpyxl import load_workbook
    wb = load_workbook(OUT)
    for ws in wb.worksheets:
        for col in ws.columns:
            w = max((len(str(c.value)) for c in col if c.value is not None),
                    default=8)
            ws.column_dimensions[col[0].column_letter].width = min(w + 2, 60)
    wb.save(OUT)

    print(f"wrote {OUT}")
    print(summary[["Strategy", "Trades", "Win rate %", "Net USD",
                   "Expected USD/day", "Worst day USD",
                   "Max drawdown USD"]].to_string(index=False))


if __name__ == "__main__":
    main()
