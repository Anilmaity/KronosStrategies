"""lab/book.py -- portfolio-level combination of per-strategy replay trade lists.

A per-strategy result is not the deliverable: the live book runs all four through ONE
account under ONE set of manager rails. This module merges the trade lists and applies
the book-level constraints that a single-strategy replay cannot see:

  * a global concurrency cap across the whole roster (ManagerConfig.max_concurrent_positions)
  * the daily kill-switch and soft brake, in USD, at a stated lot size
  * a shared friction assumption

It deliberately does NOT re-simulate signals. It takes the trade lists as given and asks
"what would the book have done with them", which is exactly the question the manager
answers live. Where a rail rejects a trade, the trade is dropped -- never re-priced.

Usage:
    python -m lab.book --lots 0.10 --cap 3 --kill 150 --soft 120
    python -m lab.book --min-sl 3.5 --block-hours 0 1 2 3
"""
from __future__ import annotations

import argparse
import glob
import os
from pathlib import Path

import numpy as np
import pandas as pd

RES = Path(__file__).resolve().parent / "results"
USD_PER_PT_PER_LOT = 100.0
SPLIT = pd.Timestamp("2026-02-01", tz="UTC")


def load_book(pattern: str = "*_base_c0.45_s1.5_hnone.csv") -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(str(RES / pattern))):
        d = pd.read_csv(f, parse_dates=["entry_time", "exit_time"])
        if len(d):
            frames.append(d)
    if not frames:
        raise SystemExit(f"no trade lists matching {pattern} in {RES}")
    b = pd.concat(frames, ignore_index=True).sort_values("entry_time").reset_index(drop=True)
    b["hour"] = b.entry_time.dt.hour
    return b


def apply_rails(b: pd.DataFrame, lots: float, cap: int, kill: float, soft: float,
                min_sl: float, block_hours: tuple) -> pd.DataFrame:
    """Walk the merged tape chronologically applying the book-level rails.

    Rejections are recorded rather than silently dropped, so the caller can see WHICH
    rail did the work -- the same discipline as StrategySignal.rejection_reason live.
    """
    b = b.copy()
    b["usd"] = b.pts * lots * USD_PER_PT_PER_LOT
    open_until: list = []          # exit_time of each currently-open position
    day_pnl: dict = {}
    killed: set = set()
    taken, why = [], []

    for r in b.itertuples():
        day = r.entry_time.date()
        open_until = [t for t in open_until if t > r.entry_time]

        if r.risk < min_sl:
            taken.append(False); why.append("sl_too_tight"); continue
        if r.hour in block_hours:
            taken.append(False); why.append("blocked_hour"); continue
        if day in killed:
            taken.append(False); why.append("kill_switch"); continue
        pnl = day_pnl.get(day, 0.0)
        if soft > 0 and pnl <= -soft:
            taken.append(False); why.append("soft_brake"); continue
        if len(open_until) >= cap:
            taken.append(False); why.append("concurrency_cap"); continue

        taken.append(True); why.append("")
        open_until.append(r.exit_time)
        day_pnl[day] = pnl + r.usd
        if kill > 0 and day_pnl[day] <= -kill:
            killed.add(day)

    b["taken"] = taken
    b["reject"] = why
    return b


def report(b: pd.DataFrame, label: str) -> dict:
    t = b[b.taken]
    if not len(t):
        print(f"{label}: no trades survived"); return {}

    def st(g):
        w = g[g.usd > 0]
        gl = -g[g.usd <= 0].usd.sum()
        eq = g.usd.cumsum()
        return dict(n=len(g), usd=round(g.usd.sum(), 1),
                    wr=round(100 * len(w) / len(g), 1),
                    pf=round(w.usd.sum() / gl, 3) if gl > 0 else np.inf,
                    dd=round(float((eq - eq.cummax()).min()), 1))

    all_, tr, te = st(t), st(t[t.entry_time < SPLIT]), st(t[t.entry_time >= SPLIT])
    print(f"\n=== {label} ===")
    print(f"  full  {all_}")
    print(f"  train {tr}")
    print(f"  test  {te}")
    print("  per strategy (test half):")
    for s, g in t[t.entry_time >= SPLIT].groupby("strategy"):
        print(f"    {s:<22} {st(g)}")
    rej = b[~b.taken].reject.value_counts().to_dict()
    print(f"  rejected: {rej}")
    return dict(label=label, full=all_, train=tr, test=te)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lots", type=float, default=0.10)
    ap.add_argument("--cap", type=int, default=3)
    ap.add_argument("--kill", type=float, default=150.0)
    ap.add_argument("--soft", type=float, default=120.0)
    ap.add_argument("--min-sl", type=float, nargs="+", default=[1.5])
    ap.add_argument("--block-hours", type=int, nargs="*", default=[])
    ap.add_argument("--pattern", default="*_base_c0.45_s1.5_hnone.csv")
    a = ap.parse_args()

    b0 = load_book(a.pattern)
    print(f"loaded {len(b0)} trades from {b0.strategy.nunique()} strategies "
          f"({b0.entry_time.min().date()} .. {b0.entry_time.max().date()})")
    for msl in a.min_sl:
        b = apply_rails(b0, a.lots, a.cap, a.kill, a.soft, msl, tuple(a.block_hours))
        report(b, f"lots={a.lots} cap={a.cap} kill={a.kill} soft={a.soft} "
                  f"min_sl={msl} block={a.block_hours}")


if __name__ == "__main__":
    main()
