"""extras.py -- derived figures for REPORT.md, read from results/trades_*.parquet.
    ../.venv/bin/python lab/research_s5/crt/extras.py > lab/research_s5/crt/results/extras_output.txt
"""
from pathlib import Path
import numpy as np, pandas as pd
RES = Path(__file__).resolve().parent / "results"
print("grid  n  gross_mid_pts/trade(ms_raw)  gross_quote_pts/trade(q_raw)  spread_paid  gross_mid_R  risk_med  rr_med  TIME%  TEST_gross_mid_R")
for g in ["H1_UTC", "H4_UTC", "H4_NY"]:
    t = pd.read_parquet(RES / f"trades_{g}.parquet")
    te = t[t.split == "TEST"]
    print(f"{g:7s} {len(t):5d}  {t.ms_raw.mean():+.3f}  {t.q_raw.mean():+.3f}  {(t.ms_raw - t.q_raw).mean():.3f}  "
          f"{(t.ms_raw / t.risk).mean():+.4f}  {t.risk.median():.2f}  {(t.reward / t.risk).median():.2f}  "
          f"{100 * (t.q_outcome == 'TIME').mean():.1f}  {(te.ms_raw / te.risk).mean():+.4f}")
    # friction as a share of R by year (why 2024 looks worst)
    byy = t.groupby("year").apply(lambda d: pd.Series({"risk_med": d.risk.median(), "friction_R_at_0.45": ((d.ms_raw - d.q_raw + 0.45) / d.risk).mean()}), include_groups=False)
    print(byy.round(3).to_string())
# 95% CI for the daily test's WR 60% on n=25 (normal approx) and its expectancy
p, n = 0.60, 25
se = np.sqrt(p * (1 - p) / n)
print(f"\ndaily test WR 60% on n=25: 95% CI {100*(p-1.96*se):.1f}..{100*(p+1.96*se):.1f} pp")
# MDE for TEST n on WR vs a 47% control
for n in (1239, 277, 279, 139, 129):
    print(f"TEST n={n}: 95% MDE on WR vs control ~ +-{100*1.96*np.sqrt(0.47*0.53/n):.1f} pp")
# post-hoc slot cells: how many with PF>1 out of how many
sl = pd.read_csv(RES / "by_slot.csv")
sl = sl[sl.arm.str.endswith("nofilt")]
print(f"\npost-hoc slot cells (nofilt, 0.45): {len(sl)} cells, PF>1 in {int((sl.pf > 1).sum())}: "
      + ", ".join(f"{r.arm.replace('crt_','').replace('_nofilt','')} slot {int(r.c3_slot)} PF {r.pf:.2f} n {int(r.n)}" for r in sl[sl.pf > 1].itertuples()))
