"""r3_s95/run.py -- H1-H4 of PROTOCOL.md. All numbers to results/run_output.txt."""
from __future__ import annotations
import os, sys
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path(__file__).resolve().parent; STRAT = HERE.parents[2]; sys.path.insert(0, str(STRAT))
from lab.s5exit import load_s5, resolve                       # noqa: E402
from shared.gate_rules import drift_budget_pts               # noqa: E402
RES = HERE / "results"; RES.mkdir(exist_ok=True)
FILL_OFFSET_S, SLIP_SL, MAX_HOLD = 66, 0.26, 180.0
CUT = pd.Timestamp("2025-12-01", tz="UTC")
out = []
def log(s=""): print(s); out.append(s)
def pf(v): v = pd.Series(v).dropna(); gw, gl = v[v > 0].sum(), -v[v <= 0].sum(); return float(gw / gl) if gl > 0 else float("inf")

t = pd.read_parquet(STRAT / "lab/results/xau2y_stage1_fixed/s95_session_breakout_c0.75.trades.parquet")
t["entry_time"] = pd.to_datetime(t.entry_time, utc=True); t["exit_time"] = pd.to_datetime(t.exit_time, utc=True)
s5 = load_s5(); t5 = s5["time"].dt.tz_convert(None).to_numpy("datetime64[ns]")
bid, ask, close = s5.bid_c.to_numpy(float), s5.ask_c.to_numpy(float), s5.c.to_numpy(float)

# ---- H1/H2: 02c fill model + standard quote-S5 nominal series -------------------------------
rows = []
for r in t.itertuples():
    ent = np.datetime64(r.entry_time.tz_convert("UTC").tz_localize(None))
    i = int(np.searchsorted(t5, ent + np.timedelta64(FILL_OFFSET_S, "s"), "right")) - 1
    j = int(np.searchsorted(t5, ent + np.timedelta64(int(MAX_HOLD * 60), "s"), "right"))
    long_ = r.side == "BUY"
    rec = dict(entry_time=r.entry_time, side=r.side, entry_px=r.entry_px, sl=r.sl, tp=r.tp, risk=r.risk, mid_pts=r.pts)
    if i < 0 or j <= i + 1:
        rows.append(rec | dict(fill=np.nan, adverse=np.nan, oc=None, pts02c=np.nan)); continue
    fill = ask[i] if long_ else bid[i]
    adverse = (fill - r.entry_px) if long_ else (r.entry_px - fill)
    rec |= dict(fill=fill, adverse=adverse)
    if adverse > drift_budget_pts(r.entry_px, r.sl):
        rows.append(rec | dict(oc="REJ", pts02c=np.nan)); continue
    px = bid[i + 1:j] if long_ else ask[i + 1:j]
    s = np.flatnonzero(px <= r.sl) if long_ else np.flatnonzero(px >= r.sl)
    tt = np.flatnonzero(px >= r.tp) if long_ else np.flatnonzero(px <= r.tp)
    ks = s[0] if len(s) else np.inf; kt = tt[0] if len(tt) else np.inf
    if ks == np.inf and kt == np.inf: oc, xpx = "TIME", close[j - 1]
    elif ks <= kt: oc, xpx = "SL", r.sl
    else: oc, xpx = "TP", r.tp
    raw = (xpx - fill) if long_ else (fill - xpx)
    rows.append(rec | dict(oc=oc, pts02c=raw - (SLIP_SL if oc == "SL" else 0.0)))
d = pd.DataFrame(rows)
# standard quote-S5 nominal-entry series (lab.s5exit.resolve, start_offset 60) at 0.45 / 0.70
for cost in (0.45, 0.70):
    v = []
    for r in t.itertuples():
        rr = resolve(r, s5, t5, "quote_s5", cost, MAX_HOLD, start_offset_s=60)
        if rr is None: v.append(np.nan); continue
        oc, px = rr; raw = (px - r.entry_px) if r.side == "BUY" else (r.entry_px - px); v.append(raw - cost)
    d[f"q{cost:.2f}"] = v
d.to_parquet(RES / "trades_02c.parquet", index=False)
acc = d[d.oc.isin(["SL", "TP", "TIME"])]; tr, te = acc[acc.entry_time < CUT], acc[acc.entry_time >= CUT]
log("=== H1: s95 honest trades under the 02c fill model (market fill @+66 s on the quote, live drift gate, quote exits, +0.26 on SL) ===")
log(f"harness trades {len(d)} | drift-gate rejects {int((d.oc=='REJ').sum())} ({100*(d.oc=='REJ').mean():.1f}%) | unresolvable {int(d.oc.isna().sum())}")
for name, x in (("ALL", acc), ("TRAIN", tr), ("TEST", te)):
    log(f"{name:5s} n={len(x):4d}  02c PF {pf(x.pts02c):.3f} pts {x.pts02c.sum():+8.1f} pts/trade {x.pts02c.mean():+.3f} | "
        f"mid-M1@0.75 PF {pf(x.mid_pts):.3f} pts {x.mid_pts.sum():+8.1f} | quote-S5 nominal @0.45 PF {pf(x['q0.45']):.3f} @0.70 PF {pf(x['q0.70']):.3f} pts {x['q0.70'].sum():+8.1f}")
    log(f"      outcomes 02c: {x.oc.value_counts().to_dict()}")
m = te.groupby(te.entry_time.dt.strftime("%Y-%m")).pts02c.sum()
log(f"TEST months positive (02c): {(m>0).mean():.2f}  max month share {m.max()/m.sum():.2f}" if m.sum() > 0 else f"TEST months positive: {(m>0).mean():.2f} (net <= 0)")
log("monthly 02c pts (TEST): " + ", ".join(f"{k}:{v:+.0f}" for k, v in m.items()))
# regime independence on the full window (gold monthly close-to-close from the daily cache)
dd = pd.read_parquet(STRAT / "backtest/results/bars_cache_2y/is_XAU_USD_1d.parquet"); dd["time"] = pd.to_datetime(dd.time, utc=True)
g = dd.set_index("time").close.resample("ME").agg(["first", "last"]); g = ((g["last"] / g["first"] - 1) * 100); g.index = g.index.strftime("%Y-%m")
fm = acc.groupby(acc.entry_time.dt.strftime("%Y-%m")).pts02c.sum(); j = pd.concat([fm.rename("pts"), g.rename("gold")], axis=1).dropna()
log(f"regime: up-months pts {j[j.gold>0].pts.sum():+.0f} (n {int((j.gold>0).sum())}), down-months pts {j[j.gold<=0].pts.sum():+.0f} (n {int((j.gold<=0).sum())}), corr {np.corrcoef(j.pts, j.gold)[0,1]:+.2f}")
bars = dict(n=len(te) >= 40, test_pf_02c=pf(te.pts02c) > 1, test_pf_q070=pf(te["q0.70"]) > 1, train_pf=pf(tr.pts02c) > 0.9,
            monthly=bool((m > 0).mean() >= 0.55 and m.sum() > 0 and m.max() / m.sum() <= 0.5),
            regime=bool((j[j.gold>0].pts.sum() > 0 and j[j.gold<=0].pts.sum() > 0) or abs(np.corrcoef(j.pts, j.gold)[0,1]) < 0.4))
log(f"BARS (02c): {bars}  -> {'PASS' if all(bars.values()) else 'FAIL'}")

log("\n=== H2: the nominal-entry term ===")
f = d.dropna(subset=["adverse"])
log(f"mean adverse (fill - nominal, in trade direction): {f.adverse.mean():+.3f} pt  median {f.adverse.median():+.3f}  share fill BETTER than nominal: {(f.adverse<0).mean():.2f}  "
    f"(execution study nominal_gap_mean -1.07: the harness books entries worse than the market)")
log(f"by side: BUY {f[f.side=='BUY'].adverse.mean():+.3f}  SELL {f[f.side=='SELL'].adverse.mean():+.3f}")

log("\n=== H3: live window 2026-07-01 -> 07-17 (consistency check only, n=13 live) ===")
lw = acc[(acc.entry_time >= "2026-07-01") & (acc.entry_time < "2026-07-18")]
log(f"02c sim in window: n={len(lw)} pts {lw.pts02c.sum():+.1f} PF {pf(lw.pts02c):.2f} outcomes {lw.oc.value_counts().to_dict()}")
log("live: 'Session Breakout M5 ORB' 8 trades 07-01..07-06 -$86 (stale-fill bug period: entries one M5 bar late); 'S95 Session Breakout' 5 trades 07-08..07-17 +$28 PF 1.93; 2 entry_drift rejects (+11.76, +1.14 pt), 1 no_add_to_loser")
lw_days = lw.assign(day=lw.entry_time.dt.date).groupby("day").pts02c.sum()
log("02c by day: " + ", ".join(f"{k}:{v:+.0f}" for k, v in lw_days.items()))

log("\n=== H4: book fit with S93 (honest Stage-1 lists, equal risk 1 R per trade) ===")
s93 = pd.read_parquet(STRAT / "lab/results/xau2y_stage1_fixed/s93_fvg_scalp_c0.75.trades.parquet"); s93["entry_time"] = pd.to_datetime(s93.entry_time, utc=True)
r95 = acc.assign(R=acc.pts02c / acc.risk).groupby(acc.entry_time.dt.date).R.sum()
r93 = s93.assign(R=s93.pts / s93.risk).groupby(s93.entry_time.dt.date).R.sum()
both = pd.concat([r95.rename("s95"), r93.rename("s93")], axis=1).fillna(0)
log(f"daily-R corr s95/s93: {both.s95.corr(both.s93):+.3f}  (days {len(both)})")
for name, ser in (("S93 alone", both.s93), ("S93 + s95", both.s93 + both.s95), ("s95 alone", both.s95)):
    mm = ser.groupby(pd.to_datetime(ser.index).strftime("%Y-%m")).sum()
    log(f"{name:10s} total {ser.sum():+8.1f} R | months positive {(mm>0).mean():.2f} ({int((mm>0).sum())}/{len(mm)}) | worst month {mm.min():+.1f} R | max DD {(ser.cumsum() - ser.cumsum().cummax()).min():+.1f} R")
(RES / "run_output.txt").write_text("\n".join(out) + "\n")
