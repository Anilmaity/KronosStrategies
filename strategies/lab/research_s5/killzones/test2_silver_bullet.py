"""test2_silver_bullet.py -- the pre-registered Silver Bullet rule vs two controls,
entries/exits resolved on S5 bid/ask closes (PROTOCOL Test 2).

Rule (fixed, no sweeps): in window [t0, t0+60m): direction = sign of the M15 bar ending
at t0; first M5 FVG in that direction with third bar inside the window and gap >= 1.5 pt;
limit at CE from the first S5 bar after the third bar's close until window end; stop = far
gap edge -/+ 0.2; target 2R; quote exits, stop-before-target; time exit at 17:00 NY.
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np, pandas as pd
from common import *

GAP_MIN = 1.5
STOP_PAD = 0.2
RR = 2.0
WIN_MIN = 60
COSTS = (0.45, 0.80)
N_SHUFFLE = 10
rng = np.random.default_rng(20260918)

g = build_grid(); s0 = int(g["slot0"])
c, bid, ask, has, tdm = g["c"], g["bid_c"], g["ask_c"], g["has_bar"], g["tdm"]
D = day_table(g); D = D[D.valid & D.in_window].reset_index(drop=True)
assert (tdm[D.slot_a.values] == 0).all(), "day slot_a must be tdm 0"

m5 = pd.read_parquet(CACHE_2Y / "is_XAU_USD_5m.parquet"); m5["time"] = pd.to_datetime(m5.time, utc=True)
m5 = m5.sort_values("time").reset_index(drop=True)
m15 = pd.read_parquet(CACHE_2Y / "is_XAU_USD_15m.parquet"); m15["time"] = pd.to_datetime(m15.time, utc=True)
m15 = m15.sort_values("time").reset_index(drop=True)
m15["disp"] = m15.close - m15.open
tr = np.maximum(m15.high - m15.low, np.maximum((m15.high - m15.close.shift()).abs(), (m15.low - m15.close.shift()).abs()))
m15["atr20"] = tr.rolling(20).mean()
t5 = m5.time.values.astype("datetime64[s]").astype(np.int64)
t15 = m15.time.values.astype("datetime64[s]").astype(np.int64)
hi5, lo5 = m5.high.to_numpy(), m5.low.to_numpy()
# FVGs on consecutive bars only (bars i-2, i-1, i exactly 5 min apart)
consec = np.r_[False, False, (t5[2:] - t5[:-2]) == 600]
bull_gap = np.where(consec, lo5 - np.r_[np.nan, np.nan, hi5[:-2]], np.nan)   # >0 -> bullish FVG
bear_gap = np.where(consec, np.r_[np.nan, np.nan, lo5[:-2]] - hi5, np.nan)   # >0 -> bearish FVG

SB = windows_tdm("silver_bullet")
CTRL_A = [(f"A_{n}-1h", a - 60, b - 60) for n, a, b in SB] + [(f"A_{n}+1h", a + 60, b + 60) for n, a, b in SB]
sb_cov = union_cov(SB)


def resolve(k_from, k_end, side, ce, sl, tp, last_slot):
    """limit fill then quote exits. returns (fill_slot, exit_slot, outcome, exit_px) or None."""
    if side > 0:
        f = ask[k_from:k_end] <= ce
    else:
        f = bid[k_from:k_end] >= ce
    if not f.any():
        return None
    kf = k_from + int(np.argmax(f))
    px = bid[kf:last_slot + 1] if side > 0 else ask[kf:last_slot + 1]
    if side > 0:
        hs = px <= sl; ht = px >= tp
    else:
        hs = px >= sl; ht = px <= tp
    ks = int(np.argmax(hs)) if hs.any() else 10**9
    kt = int(np.argmax(ht)) if ht.any() else 10**9
    if ks == kt == 10**9:
        return kf, last_slot, "TIME", float(px[-1])
    if ks <= kt:
        return kf, kf + ks, "SL", sl
    return kf, kf + kt, "TP", tp


def fire(day, arm, wname, a, need_atr=False):
    """one window on one day -> trade dict (or a 'no trade' reason)."""
    ks = day.slot_a + int(a) * 12                 # slot of t0
    ke = ks + WIN_MIN * 12                        # slot of window end
    t0 = (ks + s0) * 5
    if ke > day.last or ks < day.first + 60:
        return dict(reason="outside_day")
    j = int(np.searchsorted(t15, t0 - 900, "right")) - 1     # M15 bar ending at or before t0
    if j < 0 or t15[j] + 900 > t0:
        return dict(reason="no_m15")
    d = m15.disp.iat[j]
    if d == 0 or np.isnan(d):
        return dict(reason="zero_disp")
    if need_atr and not (abs(d) >= m15.atr20.iat[j]):
        return dict(reason="disp_lt_atr")
    side = 1 if d > 0 else -1
    i0 = int(np.searchsorted(t5, t0, "left")); i1 = int(np.searchsorted(t5, t0 + WIN_MIN * 60 - 300, "right"))  # third bar closes <= end
    gaps = (bull_gap if side > 0 else bear_gap)[i0:i1]
    ok = np.flatnonzero(gaps >= GAP_MIN)
    if len(ok) == 0:
        return dict(reason="no_fvg")
    i = i0 + int(ok[0])
    if side > 0:
        top, bot = lo5[i], hi5[i - 2]; sl = bot - STOP_PAD
    else:
        top, bot = lo5[i - 2], hi5[i]; sl = top + STOP_PAD
    ce = (top + bot) / 2; R = abs(ce - sl); tp = ce + side * RR * R
    sig_slot = int((t5[i] + 300 - s0 * 5) // 5)          # S5 bar labelled with the signal instant
    assert sig_slot * 5 + s0 * 5 >= t0 and sig_slot >= ks
    r = resolve(sig_slot, ke, side, ce, sl, tp, day.last)
    if r is None:
        return dict(reason="no_fill", gap=top - bot)
    kf, kx, oc, px = r
    raw = (px - ce) * side
    return dict(reason="trade", arm=arm, window=wname, day=day.day, split=day.split, side=side, disp=d,
                gap=top - bot, ce=ce, sl=sl, tp=tp, R=R, signal_utc=pd.Timestamp(t5[i] + 300, unit="s", tz="UTC"),
                fill_utc=pd.Timestamp((kf + s0) * 5, unit="s", tz="UTC"), exit_utc=pd.Timestamp((kx + s0) * 5, unit="s", tz="UTC"),
                hold_min=(kx - kf) * 5 / 60, outcome=oc, raw=raw,
                **{f"net_{cst}": raw - cst for cst in COSTS}, **{f"Rm_{cst}": (raw - cst) / R for cst in COSTS})


rows, reasons = [], []
for day in D.itertuples():
    for n, a, b in SB:
        for arm, need in (("SB", False), ("SB_atr", True)):
            r = fire(day, arm, n, a, need); reasons.append((arm, n, day.split, r["reason"]))
            if r["reason"] == "trade": rows.append(r)
    for n, a, b in CTRL_A:
        r = fire(day, "CTRL_A", n, a); reasons.append(("CTRL_A", n, day.split, r["reason"]))
        if r["reason"] == "trade": rows.append(r)
    # control B: random 60-min windows not overlapping SB, inside [35, 1260]
    for k in range(N_SHUFFLE):
        for n, a, b in SB:
            for _ in range(100):
                s = int(rng.integers(35, DAY_MIN - 120))
                if not sb_cov[s:s + WIN_MIN].any():
                    break
            r = fire(day, f"CTRL_B{k}", f"B{k}_{n}", s); reasons.append((f"CTRL_B{k}", n, day.split, r["reason"]))
            if r["reason"] == "trade": rows.append(r)
T = pd.DataFrame(rows)
T.to_parquet(RESULTS / "t2_trades.parquet", index=False)
RS = pd.DataFrame(reasons, columns=["arm", "window", "split", "reason"])
RS["armgrp"] = RS.arm.str.replace(r"B\d+$", "B", regex=True)
print("=== window outcomes (why a window did or did not trade) ===")
print(pd.crosstab([RS.armgrp, RS.split], RS.reason).to_string())

# gold-up / gold-down months from the daily cache
d1 = pd.read_parquet(CACHE_2Y / "is_XAU_USD_1d.parquet"); d1["time"] = pd.to_datetime(d1.time, utc=True)
mc = d1.set_index("time").close.resample("MS").last()
gold_pct = (mc.pct_change() * 100).rename("gold_pct")
gold_pct.index = gold_pct.index.strftime("%Y-%m")


def bars(x, label):
    """xau2y bars on a trade frame x (one arm)."""
    out = dict(arm=label)
    for s in ("TRAIN", "TEST"):
        q = x[x.split == s]
        out[f"{s}_n"] = len(q)
        for cst in COSTS:
            v = q[f"net_{cst}"]
            out[f"{s}_PF_{cst}"] = round(pf(v), 3) if len(q) else np.nan
            out[f"{s}_pts_{cst}"] = round(v.sum(), 1)
            out[f"{s}_meanR_{cst}"] = round(q[f"Rm_{cst}"].mean(), 3) if len(q) else np.nan
        out[f"{s}_WR"] = round(100 * (q.raw > 0).mean(), 1) if len(q) else np.nan
    q = x[x.split == "TEST"]
    if len(q):
        mo = q.groupby(pd.to_datetime(q.fill_utc).dt.strftime("%Y-%m"))["net_0.8"].sum()
        out["TEST_months_pos"] = round(100 * (mo > 0).mean(), 0)
        out["TEST_maxmonth_share"] = round(float(mo.max() / mo.sum()), 2) if mo.sum() > 0 else np.nan
    mo_all = x.groupby(pd.to_datetime(x.fill_utc).dt.strftime("%Y-%m"))["net_0.8"].sum().to_frame().join(gold_pct)
    out["pts_goldup"] = round(mo_all[mo_all.gold_pct > 0]["net_0.8"].sum(), 1)
    out["pts_golddown"] = round(mo_all[mo_all.gold_pct <= 0]["net_0.8"].sum(), 1)
    out["corr_gold"] = round(mo_all["net_0.8"].corr(mo_all.gold_pct), 2)
    ok = (out["TEST_n"] >= 40 and out["TEST_PF_0.45"] > 1 and out["TEST_PF_0.8"] > 1 and out["TRAIN_PF_0.45"] > 0.9
          and out.get("TEST_months_pos", 0) >= 55 and (out.get("TEST_maxmonth_share") or 1) <= 0.5
          and ((out["pts_goldup"] > 0 and out["pts_golddown"] > 0) or abs(out["corr_gold"]) < 0.4))
    out["bars_pass"] = bool(ok)
    return out


S = []
S.append(bars(T[T.arm == "SB"], "SB (primary)"))
S.append(bars(T[T.arm == "SB_atr"], "SB |disp|>=ATR (variant)"))
S.append(bars(T[T.arm == "CTRL_A"], "CTRL_A adjacent hours"))
for k in range(N_SHUFFLE):
    S.append(bars(T[T.arm == f"CTRL_B{k}"], f"CTRL_B{k} shuffled"))
S = pd.DataFrame(S); S.to_csv(RESULTS / "t2_summary.csv", index=False)
pd.set_option("display.width", 300); pd.set_option("display.max_columns", 40)
print("\n=== Test 2: xau2y bars, SB vs controls (net = raw - cost; R-multiples on net) ===")
cols = ["arm", "TRAIN_n", "TRAIN_WR", "TRAIN_PF_0.45", "TRAIN_PF_0.8", "TRAIN_meanR_0.8", "TEST_n", "TEST_WR", "TEST_PF_0.45", "TEST_PF_0.8",
        "TEST_pts_0.45", "TEST_pts_0.8", "TEST_meanR_0.8", "TEST_months_pos", "TEST_maxmonth_share", "pts_goldup", "pts_golddown", "corr_gold", "bars_pass"]
print(S[cols].to_string(index=False))
b = S[S.arm.str.startswith("CTRL_B")]
print(f"\nCTRL_B (10 shuffles) TEST PF@0.8: mean {b['TEST_PF_0.8'].mean():.3f}  p95 {b['TEST_PF_0.8'].quantile(0.95):.3f}   TEST meanR@0.8: mean {b['TEST_meanR_0.8'].mean():.3f}  p95 {b['TEST_meanR_0.8'].quantile(0.95):.3f}")
print(f"CTRL_B (10 shuffles) TRAIN PF@0.8: mean {b['TRAIN_PF_0.8'].mean():.3f}  p95 {b['TRAIN_PF_0.8'].quantile(0.95):.3f}   TRAIN meanR@0.8: mean {b['TRAIN_meanR_0.8'].mean():.3f}  p95 {b['TRAIN_meanR_0.8'].quantile(0.95):.3f}")

print("\n=== SB per window (primary arm) ===")
x = T[T.arm == "SB"]
per = x.groupby(["window", "split"]).apply(lambda q: pd.Series(dict(n=len(q), WR=round(100 * (q.raw > 0).mean(), 1), PF045=round(pf(q["net_0.45"]), 3),
                                                                     PF080=round(pf(q["net_0.8"]), 3), meanR080=round(q["Rm_0.8"].mean(), 3),
                                                                     medR=round(q.R.median(), 2), medgap=round(q.gap.median(), 2), TIME=int((q.outcome == "TIME").sum()))), include_groups=False)
print(per.to_string())
print("\n=== outcome mix (SB primary) ===")
print(pd.crosstab(x.split, x.outcome).to_string())
print(f"median hold (min): SB {x.hold_min.median():.0f}   median R {x.R.median():.2f} pt   median gap {x.gap.median():.2f}")
print("\n=== SB primary by side ===")
print(x.groupby(["split", "side"]).apply(lambda q: pd.Series(dict(n=len(q), WR=round(100 * (q.raw > 0).mean(), 1), PF080=round(pf(q["net_0.8"]), 3), meanR080=round(q["Rm_0.8"].mean(), 3))), include_groups=False).to_string())
print("\n=== monthly net @0.80, SB primary ===")
print(x.groupby(pd.to_datetime(x.fill_utc).dt.strftime("%Y-%m"))["net_0.8"].agg(["size", "sum"]).round(1).T.to_string())
# t-stat of SB vs CTRL_A on TEST mean R (0.80)
from scipy import stats
for s in ("TRAIN", "TEST"):
    a_ = T[(T.arm == "SB") & (T.split == s)]["Rm_0.8"]; b_ = T[(T.arm == "CTRL_A") & (T.split == s)]["Rm_0.8"]
    t, p = stats.ttest_ind(a_, b_, equal_var=False)
    print(f"{s}: SB meanR {a_.mean():+.3f} (n={len(a_)}, se {a_.std()/np.sqrt(len(a_)):.3f}) vs CTRL_A {b_.mean():+.3f} (n={len(b_)})  Welch t={t:.2f} p={p:.3f}")
