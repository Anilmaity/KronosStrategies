"""drift.py -- H7a: how much of c03 would the live `entry_drift` gate reject? (descriptive)

For every trade of an arm (default F_H0_base), the runner sees the closed M1 bar delta seconds
after its close (5 s poll + up to 20 s candle-cache TTL), then fetches the LTP for the gate:
    drift  = (LTP - entry) * dir   (positive = adverse)
    reject iff drift > min(MAX_ENTRY_DRIFT_PTS 0.5, MAX_ENTRY_DRIFT_FRAC 0.25 * stop)
LTP model = the S5 close at entry-bar open + 60 s + delta (quote.py writes drift_{5,15,30}s).
Uses the real predicate shared.gate_rules.entry_drift_exceeded. Also reports the quote-S5
result (at 0.70) of the admitted vs rejected sets -- a measurement of the gate's selection,
not a strategy change (PROTOCOL H7).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
STRAT = HERE.parents[2]
sys.path.insert(0, str(STRAT))
from shared.gate_rules import entry_drift_exceeded, drift_budget_pts   # noqa: E402

RES = HERE / "results"
SPLIT = pd.Timestamp("2025-12-01", tz="UTC")


def pf(v):
    v = np.asarray(v, float); gw, gl = v[v > 0].sum(), -v[v <= 0].sum()
    return float(gw / gl) if gl > 0 else float("inf")


def main(label: str = "F_H0_base") -> None:
    tr = pd.read_parquet(RES / f"{label}.trades.parquet")
    q = pd.read_parquet(RES / f"{label}.quote.parquet")
    assert len(tr) == len(q)
    d = pd.concat([tr.reset_index(drop=True), q.drop(columns=["entry_time", "side", "risk"]).reset_index(drop=True)], axis=1)
    d["entry_time"] = pd.to_datetime(d.entry_time, utc=True)
    d["q_pts"] = d.quote_pts0 - 0.70
    d["budget"] = [drift_budget_pts(e, s) for e, s in zip(d.entry_px, d.sl)]
    print(f"{label}: n={len(d)}; drift budget: median {d.budget.median():.2f} pt, share at the 0.5 cap {(d.budget >= 0.5 - 1e-9).mean()*100:.1f} %")
    print(f"nominal gap (market mid at the signal close - entry, adverse>0): mean {d.nominal_gap.mean():+.3f}, |median| {d.nominal_gap.abs().median():.3f}")
    print("\n delta  rejected%  TRAIN rej%  TEST rej%   admitted: n / PF@0.70 / pts     rejected: n / PF@0.70 / pts   drift mean(adverse>0)")
    for delta in (5, 15, 30):
        col = f"drift_{delta}s"
        rej = np.array([entry_drift_exceeded(s, e, sl, ltp)[0] for s, e, sl, ltp in zip(d.side, d.entry_px, d.sl, d[f"ltp_{delta}s"])])
        adm, rj = d[~rej], d[rej]
        tr_m, te_m = d.entry_time < SPLIT, d.entry_time >= SPLIT
        print(f" {delta:>3}s   {rej.mean()*100:5.1f}%     {rej[tr_m].mean()*100:5.1f}%     {rej[te_m].mean()*100:5.1f}%    "
              f"{len(adm):5d} / {pf(adm.q_pts):.3f} / {adm.q_pts.sum():8.1f}    {len(rj):4d} / {pf(rj.q_pts) if len(rj) else float('nan'):.3f} / {rj.q_pts.sum():7.1f}    {d[col].mean():+.3f}")
    # by stop bucket at delta=15
    rej15 = np.array([entry_drift_exceeded(s, e, sl, ltp)[0] for s, e, sl, ltp in zip(d.side, d.entry_px, d.sl, d.ltp_15s)])
    d["bucket"] = pd.cut(d.risk, [0, 2, 3, 4, 6, 1000], labels=["<2", "2-3", "3-4", "4-6", "6+"])
    g = d.assign(rej=rej15).groupby("bucket", observed=True).agg(n=("rej", "size"), rej_pct=("rej", lambda x: round(100 * x.mean(), 1)),
                                                               budget=("budget", "median"), q_pts=("q_pts", "sum"))
    print("\n by stop bucket (delta 15 s):"); print(g.to_string())
    # the retry: live re-evaluates the next minute; how often does the same setup fire again within 4 min?
    et = d.entry_time.to_numpy()
    print(f"\n note: a rejected signal is re-evaluated by the runner on the next M1 bar (no cooldown is set); the harness has no LTP and enters at the signal bar's close.")


if __name__ == "__main__":
    main(*(sys.argv[1:2] or []))
