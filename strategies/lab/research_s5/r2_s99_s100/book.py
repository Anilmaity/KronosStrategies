"""book.py -- what the book loses/gains: baseline S99/S100 trades/day, daily-points correlation
with the survivors (c03 / s14 Stage-1 trades at shipped config), and the harness's own read of
the live window 2026-07-01 -> 2026-09-17 (mid-M1 @0.75 and quote-S5 @0.45/0.70) next to the live
record. Output: results/book.txt.

    ../.venv/bin/python -m lab.research_s5.r2_s99_s100.book
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
STRAT = HERE.parents[2]
RES = HERE / "results"
STAGE1 = STRAT / "lab" / "results" / "xau2y_stage1"


def pf(v):
    v = np.asarray(v, float); gw, gl = v[v > 0].sum(), -v[v <= 0].sum()
    return float(gw / gl) if gl > 0 else float("inf")


def daily(df: pd.DataFrame, col: str) -> pd.Series:
    d = pd.to_datetime(df.entry_time, utc=True).dt.floor("D")
    return df.groupby(d)[col].sum()


def main() -> None:
    lines = []
    def p(*a):
        s = " ".join(str(x) for x in a); print(s); lines.append(s)

    # survivors: Stage-1 trades at 0.80 (mid-M1), converted to 1.00 (the stress convention) -> R per trade
    surv = {}
    for s in ("c03_fvg_fill", "s14_ob_mit_bias"):
        t = pd.read_parquet(STAGE1 / f"{s}_c0.80.trades.parquet")
        t["pts100"] = t.pts - 0.20
        t["R"] = t.pts100 / t.risk
        surv[s] = t
    base = {}
    for s, lab in (("s99", "s99_base_c0.75"), ("s100", "s100_base_c0.75")):
        q = pd.read_parquet(RES / "quote" / f"{lab}.parquet")
        q["mid100"] = q.mid_raw - 1.00
        q["q70"] = q.quote_raw - 0.70
        q["R_mid"] = q.mid100 / q.risk
        q["R_q"] = q.q70 / q.risk
        base[s] = q

    p("# book context (baselines at shipped config)")
    days = (pd.Timestamp("2026-09-17") - pd.Timestamp("2024-09-01")).days * 5 / 7
    for s, q in base.items():
        te = q[pd.to_datetime(q.entry_time, utc=True) >= pd.Timestamp("2025-12-01", tz="UTC")]
        p(f"{s}: n {len(q)} over 24 mo = {len(q)/days:.2f} trades/weekday; median risk {q.risk.median():.2f} pt; "
          f"mid-M1@1.00 R/trade {q.R_mid.mean():+.3f}, quote-S5@0.70 R/trade {q.R_q.mean():+.3f}; "
          f"TEST quote@0.70 pts {te.q70.sum():+.1f}, R {te.R_q.sum():+.1f}")
    p("\n## daily-R correlation (equal-risk R per trade, daily sums, days where either traded)")
    ds = {f"{s}(R_q70)": daily(q, "R_q") for s, q in base.items()}
    ds.update({f"{s}(R_mid100)": daily(t, "R") for s, t in surv.items()})
    D = pd.DataFrame(ds).fillna(0.0)
    with pd.option_context("display.width", 200):
        p(D.corr().round(3).to_string())
    p("\n## equal-risk book: survivors only vs survivors + S99 + S100 (daily R, 24 mo, 1 R per trade)")
    S = D[["c03_fvg_fill(R_mid100)", "s14_ob_mit_bias(R_mid100)"]].sum(axis=1)
    for name, extra in (("c03+s14", []), ("+S99", ["s99(R_q70)"]), ("+S100", ["s100(R_q70)"]), ("+both", ["s99(R_q70)", "s100(R_q70)"])):
        B = S + D[extra].sum(axis=1) if extra else S
        eq = B.cumsum(); dd = float((eq - eq.cummax()).min())
        m = B.groupby(B.index.strftime("%Y-%m")).sum()
        p(f"{name:<8} total R {B.sum():+8.1f}  daily PF {pf(B):.2f}  maxDD {dd:7.1f} R  worst day {B.min():6.1f}  +months {int((m>0).sum())}/{len(m)}")

    p("\n## the harness's read of the live window 2026-07-01 -> 2026-09-17 (ungated signal population)")
    lo, hi = pd.Timestamp("2026-07-01", tz="UTC"), pd.Timestamp("2026-09-18", tz="UTC")
    for s, q in base.items():
        w = q[(pd.to_datetime(q.entry_time, utc=True) >= lo) & (pd.to_datetime(q.entry_time, utc=True) < hi)]
        p(f"{s}: sim n {len(w)}  mid-M1@0.75 PF {pf(w.mid_raw-0.75):.3f} pts {(w.mid_raw-0.75).sum():+.1f} | "
          f"quote-S5@0.45 PF {pf(w.quote_raw-0.45):.3f} pts {(w.quote_raw-0.45).sum():+.1f} | @0.70 PF {pf(w.quote_raw-0.70):.3f} pts {(w.quote_raw-0.70).sum():+.1f}"
          f" | WR(q70) {100*((w.quote_raw-0.70)>0).mean():.1f}%  median risk {w.risk.median():.2f}")
        by_m = w.assign(m=pd.to_datetime(w.entry_time, utc=True).dt.strftime("%Y-%m")).groupby("m").apply(
            lambda x: pd.Series({"n": len(x), "q70_pts": round((x.quote_raw-0.70).sum(), 1), "q70_pf": round(pf(x.quote_raw-0.70), 3)}), include_groups=False)
        p(by_m.to_string().replace("\n", " | "))
    try:
        for s in ("s99", "s100"):
            lv = pd.read_csv(RES / f"live_closed_{s}.csv")
            p(f"{s} live (closed, since 07-01): n {len(lv)}  USD {lv.usd.sum():+.0f}  PF(usd) {pf(lv.usd):.3f}  PF(pts) {pf(lv.pts.dropna()):.3f}  pts {lv.pts.sum():+.1f}")
    except FileNotFoundError:
        p("(live csvs not found)")
    (RES / "book.txt").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
