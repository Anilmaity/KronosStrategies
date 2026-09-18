"""06_book.py — live edge per leg, correlations, and equal-risk vs edge-weighted book (PROTOCOL D11, D12).

    cd strategies && ../.venv/bin/python lab/research_s5/r2_live_parity/06_book.py

D11: live mean R with 10k-draw bootstrap 90% CI, PF (pts), SQN, WR, USD, "negative since" (last
high-water mark of cumulative pts), rolling-30 mean R. Floors: n<40 no verdict; 40-99 indicative.
D12: daily-R correlations (live; harness 24-month); equal vs edge-weighted book with weights
fitted on TRAIN (< 2025-12-01) of the 24-month mid-M1 @0.80 trade lists (the nearest available
to the 0.75 convention), read once on TEST and on the live window. Weight_i = clip(meanR/varR, 0)
normalised to the same total risk as equal weight (sum = n_legs), each leg capped at 2x equal.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
STRAT = HERE.parents[2]
sys.path.insert(0, str(HERE))
from common import RES, live_trades, pf, boot_ci   # noqa: E402

LAB = STRAT / "lab" / "results"
LEGS4 = ["s93_fvg_scalp", "s94_sweep_reversal", "s99_mss_fvg", "s100_m3_combo"]
# 24-month lists for the 09-18 roster (mid-M1 @0.80)
ROSTER_LISTS = {
    "s93_fvg_scalp": LAB / "xau2y_stage1" / "s93_fvg_scalp_c0.80.trades.parquet",
    "s94_sweep_reversal": LAB / "s94_sides" / "long_sd2.5_c0.80.trades.parquet",
    "s99_mss_fvg": LAB / "xau2y_stage1" / "s99_mss_fvg_c0.80.trades.parquet",
    "s100_m3_combo": LAB / "xau2y_stage1" / "s100_m3_combo_c0.80.trades.parquet",
    "c03_fvg_fill": LAB / "xau2y_stage1" / "c03_fvg_fill_c0.80.trades.parquet",
    "s14_ob_mit_bias": LAB / "s14_minstop" / "s14_ob_mit_bias_minsl3.0_c0.80.trades.parquet",
}
SPLIT = pd.Timestamp("2025-12-01", tz="UTC")
LIVE_LO, LIVE_HI = pd.Timestamp("2026-07-01", tz="UTC"), pd.Timestamp("2026-09-18", tz="UTC")


def sqn(r):
    r = pd.Series(r).dropna()
    return float(r.mean() / r.std() * np.sqrt(len(r))) if len(r) > 1 and r.std() > 0 else np.nan


def neg_since(df, col="pts"):
    """Date of the last running high-water mark of cumulative `col` (None if the last trade set a new high)."""
    d = df.sort_values("entry_created_at")
    cum = d[col].cumsum()
    hwm = cum.cummax()
    at_high = cum >= hwm - 1e-9
    last_high_idx = np.flatnonzero(at_high.to_numpy())
    if len(last_high_idx) == 0:
        return None, 0, 0.0
    i = last_high_idx[-1]
    n_since = len(d) - 1 - i
    dd = float(cum.iloc[-1] - hwm.iloc[-1])
    return d.entry_created_at.iloc[i].date(), n_since, dd


def daily_r(df, tcol, rcol):
    return df.assign(day=pd.to_datetime(df[tcol], utc=True).dt.floor("D")).groupby("day")[rcol].sum()


def book_stats(daily: pd.DataFrame, w: pd.Series):
    b = (daily * w).sum(axis=1)
    eq = b.cumsum()
    return dict(netR=round(b.sum(), 1), maxDD_R=round(float((eq - eq.cummax()).min()), 1), worst_day=round(b.min(), 1),
                sharpe_d=round(b.mean() / b.std() * np.sqrt(252), 2) if b.std() > 0 else np.nan, days_pos=round(100 * (b > 0).mean(), 0))


def main():
    out = []
    lt = live_trades()
    # ---- D11 per leg ----
    out.append("=== (d1) live edge per leg (broker truth; R = pts / nominal stop) ===")
    rows = []
    for key, g in lt.groupby("key"):
        lo, hi = boot_ci(g.r)
        since, n_since, dd = neg_since(g)
        roll = g.sort_values("entry_created_at").r.rolling(30).mean().iloc[-1] if len(g) >= 30 else np.nan
        verdict = "no verdict (n<40)" if len(g) < 40 else ("NEGATIVE live (CI upper < 0)" if hi < 0 else ("positive (CI lower > 0)" if lo > 0 else "indistinguishable from 0"))
        if 40 <= len(g) < 100:
            verdict += " [indicative, n<100]"
        rows.append(dict(leg=key, n=len(g), meanR=round(g.r.mean(), 3), ci_lo=round(lo, 3), ci_hi=round(hi, 3), pf_pts=round(pf(g.pts), 2),
                         wr=round(100 * (g.pts > 0).mean(), 0), sqn=round(sqn(g.r), 2), pts=round(g.pts.sum(), 1), usd=round(g.usd.sum(), 0),
                         usd_per_trade=round(g.usd.mean(), 1), hwm_date=since, trades_since_hwm=n_since, dd_from_hwm_pts=round(dd, 1),
                         roll30_R=round(roll, 3) if roll == roll else np.nan, verdict=verdict))
    d1 = pd.DataFrame(rows).sort_values("n", ascending=False)
    out.append(d1.to_string(index=False))
    d1.to_csv(RES / "book_live_legs.csv", index=False)
    # monthly by leg
    m = lt.assign(m=lt.entry_created_at.dt.strftime("%Y-%m")).groupby(["key", "m"]).agg(n=("r", "size"), R=("r", "sum"), pts=("pts", "sum"), usd=("usd", "sum")).round(1)
    out.append("\nmonthly by leg:\n" + m.to_string())
    # copy slots in USD
    pos = pd.read_parquet(RES / "live_positions.parquet")
    pos["exit_created_at"] = pd.to_datetime(pos.exit_created_at, utc=True)
    cp = pos[pos.key.isin(["copy_free", "copy_vip"]) & (pos.quantity == 0)].copy()
    cp["usd"] = cp.realized_units * 100
    out.append("\ncopy slots (USD, Position.realized; one row per broker leg):\n" +
               cp.groupby("key").agg(n=("usd", "size"), usd=("usd", "sum"), usd_per_leg=("usd", "mean"), pf=("usd", pf), wr=("usd", lambda x: 100 * (x > 0).mean())).round(1).to_string())
    cpm = cp.assign(m=cp.exit_created_at.dt.strftime("%Y-%m")).groupby(["key", "m"]).usd.sum().round(0)
    out.append("copy slots monthly USD:\n" + cpm.to_string())

    # ---- D12 correlations: live daily R (engine legs) + copy daily USD/100 ----
    dl = {k: daily_r(lt[lt.key == k], "entry_created_at", "r") for k in LEGS4}
    dl["copy_usd/100"] = cp.assign(day=cp.exit_created_at.dt.floor("D")).groupby("day").usd.sum() / 100.0
    D = pd.DataFrame(dl).fillna(0.0)
    D = D[(D.index >= LIVE_LO) & (D.index < LIVE_HI)]
    out.append(f"\n=== (d2) live daily-R correlations (days with any trade filled as 0; n days {len(D)}) ===")
    out.append(D.corr().round(2).to_string())
    out.append("live daily R by leg: mean / sd / worst: " + ", ".join(f"{c} {D[c].mean():+.2f}/{D[c].std():.2f}/{D[c].min():+.1f}" for c in D.columns))
    # ---- 24-month harness book ----
    H = {}
    legs_stats = []
    for key, f in ROSTER_LISTS.items():
        t = pd.read_parquet(f)
        t["entry_time"] = pd.to_datetime(t.entry_time, utc=True)
        H[key] = daily_r(t, "entry_time", "r")
        tr, te = t[t.entry_time < SPLIT], t[t.entry_time >= SPLIT]
        legs_stats.append(dict(leg=key, n_train=len(tr), meanR_train=round(tr.r.mean(), 4), varR_train=round(tr.r.var(), 4), pf_train=round(pf(tr.pts), 2),
                               n_test=len(te), meanR_test=round(te.r.mean(), 4), pf_test=round(pf(te.pts), 2), trades_per_day_test=round(len(te) / max(1, te.entry_time.dt.floor("D").nunique()), 2)))
    L = pd.DataFrame(legs_stats)
    out.append("\n=== (d3) 24-month harness legs (mid-M1 @0.80 lists; S94 long-only SD 2.5; s14 floor 3.0; S93 hours 13-14) ===")
    out.append(L.to_string(index=False))
    HD = pd.DataFrame(H).fillna(0.0)
    tr_days = HD[HD.index < SPLIT]; te_days = HD[HD.index >= SPLIT]; lv_days = HD[(HD.index >= LIVE_LO) & (HD.index < LIVE_HI)]
    out.append("\nharness daily-R correlations TRAIN:\n" + tr_days.corr().round(2).to_string())
    out.append("harness daily-R correlations TEST:\n" + te_days.corr().round(2).to_string())
    # weights from TRAIN only
    w_eq = pd.Series(1.0, index=HD.columns)
    raw = (L.set_index("leg").meanR_train / L.set_index("leg").varR_train).clip(lower=0.0)
    w_edge = raw / raw.sum() * len(raw)
    w_edge = w_edge.clip(upper=2.0)
    w_edge = w_edge / w_edge.sum() * len(raw)
    out.append("\n=== (d4) equal-risk vs edge-weighted (weights from TRAIN meanR/varR, cap 2x, same total risk) ===")
    out.append("edge weights: " + ", ".join(f"{k} {v:.2f}" for k, v in w_edge.items()))
    for name, dd in (("TRAIN (fit)", tr_days), ("TEST (read once)", te_days), ("live window 07-01..09-17 (harness)", lv_days)):
        out.append(f"{name:<38} equal {book_stats(dd, w_eq)}   edge {book_stats(dd, w_edge)}")
    # per-leg TEST contribution under equal weight and what dropping each leg does
    out.append("\nleg-drop test on TEST (equal risk): book netR / maxDD without each leg")
    for k in HD.columns:
        w = w_eq.copy(); w[k] = 0.0
        out.append(f"  drop {k:<20} {book_stats(te_days, w)}")
    out.append(f"  full book                 {book_stats(te_days, w_eq)}")
    # the live record as the same book: what did the four live legs deliver per day in R, and what would c03/s14 (harness) have added
    lv4 = D[LEGS4]
    out.append(f"\nlive four-leg book (daily R, live window): {book_stats(lv4, pd.Series(1.0, index=lv4.columns))}")
    hv = HD[(HD.index >= LIVE_LO) & (HD.index < LIVE_HI)]
    out.append(f"harness 09-18 roster over the same window (daily R): {book_stats(hv, w_eq)}; c03+s14 only: {book_stats(hv[['c03_fvg_fill','s14_ob_mit_bias']], pd.Series(1.0, index=['c03_fvg_fill','s14_ob_mit_bias']))}")
    # USD translation at $38/R and the daily-loss rails
    b_te = (te_days * w_eq).sum(axis=1) * 38.0
    out.append(f"\nTEST book at $38 per R (6 legs, equal): mean day {b_te.mean():+.0f}, sd {b_te.std():.0f}, p5 {b_te.quantile(.05):+.0f}, worst {b_te.min():+.0f}; "
               f"days <= -120: {(b_te <= -120).sum()} of {len(b_te)} ({100*(b_te<=-120).mean():.1f}%), <= -150: {(b_te <= -150).sum()}, <= -250: {(b_te <= -250).sum()}")
    for risk in (25.0, 38.0, 50.0):
        b = (te_days * w_eq).sum(axis=1) * risk
        out.append(f"  at ${risk:.0f}/R: days <= -150: {(b <= -150).sum()} ({100*(b<=-150).mean():.1f}%), <= -250: {(b <= -250).sum()}; annualised net ${b.sum()/len(b)*252:+,.0f}; maxDD ${((b.cumsum()-b.cumsum().cummax()).min()):+,.0f}")
    D.to_csv(RES / "book_live_daily_R.csv"); HD.to_csv(RES / "book_harness_daily_R.csv")
    txt = "\n".join(out); print(txt)
    (RES / "06_book.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
