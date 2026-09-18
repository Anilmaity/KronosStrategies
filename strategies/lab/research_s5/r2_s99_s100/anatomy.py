"""anatomy.py -- baseline trade anatomy under quote-S5 @0.70 (and mid-M1 @1.00) for S99 and S100:
by stop bucket, side x half, hour, entry model (S100 reason), outcomes. Output results/anatomy.txt."""
from __future__ import annotations
from pathlib import Path
import numpy as np, pandas as pd
RES = Path(__file__).resolve().parent / "results"
def pf(v):
    gw, gl = v[v > 0].sum(), -v[v <= 0].sum(); return round(gw / gl, 3) if gl > 0 else float("inf")
lines = []
def p(*a):
    s = " ".join(str(x) for x in a); print(s); lines.append(s)
for s in ("s99", "s100"):
    q = pd.read_parquet(RES / "quote" / f"{s}_base_c0.75.parquet")
    q["q70"] = q.quote_raw - 0.70; q["m100"] = q.mid_raw - 1.00
    q["bucket"] = pd.cut(q.risk, [0, 1.5, 2, 3, 4, 6, 9, 100], labels=["<1.5", "1.5-2", "2-3", "3-4", "4-6", "6-9", "9+"])
    q["half"] = np.where(pd.to_datetime(q.entry_time, utc=True) < pd.Timestamp("2025-12-01", tz="UTC"), "TRAIN", "TEST")
    q["model"] = q.reason.str.extract(r"_(FVG|OB|RSI3|MSS)")[0]
    p(f"\n## {s} baseline (n {len(q)}) -- quote-S5 @0.70 unless stated")
    p("haircut mid->quote pts/trade", round((q.mid_raw - q.quote_raw).mean(), 3), "| outcomes", q.quote_outcome.value_counts().to_dict())
    p(q.groupby("bucket", observed=True).agg(n=("q70", "size"), pf_q70=("q70", pf), pts_q70=("q70", "sum"), pf_m100=("m100", pf), wr=("q70", lambda v: round(100 * (v > 0).mean(), 1))).round(1).to_string())
    p(q.groupby(["half", "side"]).agg(n=("q70", "size"), pf_q70=("q70", pf), pts=("q70", "sum")).round(1).to_string())
    p(q.groupby("hour").agg(n=("q70", "size"), pf_q70=("q70", pf), pts=("q70", "sum")).round(1).to_string())
    if s == "s100":
        p(q.groupby(["half", "model"]).agg(n=("q70", "size"), pf_q70=("q70", pf), pts=("q70", "sum")).round(1).to_string())
    p(q.groupby("half").agg(n=("q70", "size"), wr=("q70", lambda v: round(100 * (v > 0).mean(), 1)), risk=("risk", "median")).to_string())
(RES / "anatomy.txt").write_text("\n".join(lines) + "\n")
