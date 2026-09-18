"""03_geometry.py -- quote-vs-mid trigger geometry on every Stage-1 trade (15 strategies), plus a
matched random-entry control. Vectorised replica of lab/s5exit.resolve() (mode quote_s5 / mid_s5,
start_offset_s=60, SL checked before TP within a bar, horizon = the strategy's max hold or 45 d).
Validated against lab/results/s5exit/{c03,s14}_c0.80.parquet before anything is trusted."""
from __future__ import annotations

import numpy as np
import pandas as pd

import common
from common import RES, Tee, X_EDGES, X_LABELS, COSTS, pf

out = Tee(RES / "03_geometry.txt")
s5 = common.load()
T = s5.t.astype("datetime64[s]")
CHUNKS = (17_280, 120_960, 10**9)      # 1 day, then 7 days, then the rest of the horizon


def resolve_one(i, j, long_, sl, tp, mode):
    """Return (outcome, k) with k the absolute bar index of the exit (j-1 for TIME)."""
    if j <= i:
        return None, -1
    a = i
    for ch in CHUNKS:
        b = min(j, a + ch)
        if mode == "quote":
            lo = s5.bid[a:b] if long_ else s5.ask[a:b]
            hi = lo
        else:
            lo, hi = s5.l[a:b], s5.h[a:b]
        if long_:
            sh, th = lo <= sl, hi >= tp
        else:
            sh, th = hi >= sl, lo <= tp
        ks = int(np.argmax(sh)) if sh.any() else None
        kt = int(np.argmax(th)) if th.any() else None
        if ks is not None and (kt is None or ks <= kt):
            return "SL", a + ks
        if kt is not None:
            return "TP", a + kt
        a = b
        if a >= j:
            break
    return "TIME", j - 1


def resolve_frame(df, horizon_s, mode):
    ent = df.entry_time.dt.tz_convert(None).to_numpy("datetime64[s]")
    i = np.searchsorted(T, ent + np.timedelta64(60, "s"), "right")
    j = np.searchsorted(T, ent + np.timedelta64(int(horizon_s), "s"), "right")
    long_ = (df.side == "BUY").to_numpy()
    sl, tp, epx = df.sl.to_numpy(float), df.tp.to_numpy(float), df.entry_px.to_numpy(float)
    oc, k, px = [], np.empty(len(df), int), np.empty(len(df))
    for n in range(len(df)):
        o, kk = resolve_one(int(i[n]), int(j[n]), bool(long_[n]), sl[n], tp[n], mode)
        oc.append(o); k[n] = kk
        px[n] = sl[n] if o == "SL" else tp[n] if o == "TP" else (s5.c[kk] if kk >= 0 else np.nan)
    oc = np.array(oc, dtype=object)
    raw = np.where(long_, px - epx, epx - px)
    r = pd.DataFrame({f"{mode}_outcome": oc, f"{mode}_k": k, f"{mode}_pts0": raw})
    if mode == "quote":
        # observed overshoot at the triggering bar (the S5-close crossing), only for stops
        trig = np.where(long_, sl - s5.bid[np.maximum(k, 0)], s5.ask[np.maximum(k, 0)] - sl)
        r["quote_overshoot"] = np.where(oc == "SL", trig, np.nan)
        r["quote_trig_spread"] = np.where(k >= 0, s5.spread[np.maximum(k, 0)], np.nan)
        r["hold_s"] = np.where(k >= 0, s5.ts[np.maximum(k, 0)] - ent.astype("int64"), np.nan)
    r["fill_i"] = i
    ok = i < len(T)
    ii = np.minimum(i, len(T) - 1)
    r["spread_entry"] = np.where(ok, s5.spread[ii], np.nan)
    r["fill_dt_s"] = np.where(ok, s5.ts[ii] - ent.astype("int64"), np.nan)
    r["entry_comp"] = np.where(ok, np.where(long_, s5.ask[ii] - epx, epx - s5.bid[ii]), np.nan)
    r["mid_next"] = np.where(ok, np.where(long_, s5.c[ii] - epx, epx - s5.c[ii]), np.nan)
    # PROTOCOL CHANGE (recorded 2026-09-18 after the first run): entry_px is the strategy's NOMINAL entry,
    # which for s95/s96/s97/s98 is not the market price at signal time (a level / M5 close). The entry
    # component is therefore split: entry_mkt = fill vs the market mid at the signal bar's close (the
    # last S5 close inside the entry minute) = half-spread + 5-s drift, generic by hour; nominal_gap =
    # market mid vs the nominal entry, strategy-specific (positive = the harness credits a better price
    # than the market offered). entry_comp = entry_mkt + nominal_gap is unchanged.
    km = np.searchsorted(T, ent + np.timedelta64(60, "s"), "left") - 1
    km = np.clip(km, 0, len(T) - 1)
    m1c = s5.c[km]
    r["m1c"] = m1c
    r["entry_mkt"] = np.where(ok, np.where(long_, s5.ask[ii] - m1c, m1c - s5.bid[ii]), np.nan)
    r["nominal_gap"] = np.where(long_, m1c - epx, epx - m1c)
    r["drift_mkt"] = np.where(ok, np.where(long_, s5.c[ii] - m1c, m1c - s5.c[ii]), np.nan)
    return r


# ---------------------------------------------------------------- validation vs lab/results/s5exit
out("=== validation of the vectorised resolver against lab/results/s5exit (quote_s5, cost 0.80) ===")
for st in ("c03_fvg_fill", "s14_ob_mit_bias"):
    ref = pd.read_parquet(common.S5EXIT / f"{st}_c0.80.parquet")
    ref["entry_time"] = pd.to_datetime(ref.entry_time, utc=True)
    tr = common.load_trades(st)
    m = tr.merge(ref[["entry_time", "quote_s5_outcome", "quote_s5_pts", "mid_s5_outcome", "mid_s5_pts"]], on="entry_time", how="inner")
    hz = common.NO_HOLD_HORIZON_MIN * 60
    q = resolve_frame(m, hz, "quote"); g = resolve_frame(m, hz, "mid")
    same_oc = (q.quote_outcome.to_numpy() == m.quote_s5_outcome.to_numpy())
    same_pts = np.isclose(q.quote_pts0 - 0.80, m.quote_s5_pts, atol=1e-6)
    ref_time = (m.quote_s5_outcome == "TIME").to_numpy()
    out(f"{st}: matched {len(m)} of {len(tr)} trades; quote outcome agree {same_oc.mean()*100:.3f} % "
        f"({(~same_oc).sum()} differ, of which reference=TIME {int((~same_oc & ref_time).sum())}); pts agree {same_pts.mean()*100:.3f} %; "
        f"mid_s5 outcome agree {(g.mid_outcome.to_numpy()==m.mid_s5_outcome.to_numpy()).mean()*100:.3f} %")
    if (~same_oc & ~ref_time).any():
        d = m[~same_oc & ~ref_time][["entry_time", "side", "sl", "tp", "quote_s5_outcome"]].assign(mine=q.quote_outcome[~same_oc & ~ref_time].values)
        out(d.head(10).to_string(index=False))

# ---------------------------------------------------------------- all strategies
rng = np.random.default_rng(0)
frames, ctrls = [], []
for st in common.strategies():
    tr = common.load_trades(st)
    mh = common.MAX_HOLD[st]
    hz = (mh * 60) if mh else common.NO_HOLD_HORIZON_MIN * 60
    q = resolve_frame(tr, hz, "quote"); g = resolve_frame(tr, hz, "mid")
    d = pd.concat([tr.reset_index(drop=True), q, g[["mid_outcome", "mid_k", "mid_pts0"]]], axis=1)
    d["horizon_s"] = hz
    frames.append(d)
    # ---- matched random-entry control: same side / time-of-day / risk / target distance, day +-30
    ent = tr.entry_time.dt.tz_convert(None).to_numpy("datetime64[s]")
    risk = np.abs(tr.entry_px - tr.sl).to_numpy(); tdist = np.abs(tr.tp - tr.entry_px).to_numpy()
    long_ = (tr.side == "BUY").to_numpy()
    c_ent, c_px, keep = [], [], []
    for n in range(len(tr)):
        got = False
        for _ in range(12):
            off = int(rng.integers(-30, 31))
            if off == 0:
                continue
            e = ent[n] + np.timedelta64(off, "D")
            if ((e.astype("datetime64[D]").astype(int) + 3) % 7) >= 5:      # weekend
                continue
            kk = np.searchsorted(T, e + np.timedelta64(60, "s"), "left") - 1   # last bar inside the entry minute
            if kk < 0 or T[kk] < e or kk + 1 >= len(T):
                continue
            c_ent.append(e); c_px.append(s5.c[kk]); keep.append(n); got = True
            break
    keep = np.array(keep)
    c_px = np.array(c_px)
    ctrl = pd.DataFrame({"strategy": st, "entry_time": pd.to_datetime(np.array(c_ent)).tz_localize("UTC"),
                         "side": tr.side.to_numpy()[keep], "entry_px": c_px,
                         "sl": np.where(long_[keep], c_px - risk[keep], c_px + risk[keep]),
                         "tp": np.where(long_[keep], c_px + tdist[keep], c_px - tdist[keep]),
                         "risk": risk[keep], "real_idx": keep})
    cq = resolve_frame(ctrl, hz, "quote"); cg = resolve_frame(ctrl, hz, "mid")
    ctrls.append(pd.concat([ctrl.reset_index(drop=True), cq, cg[["mid_outcome", "mid_k", "mid_pts0"]]], axis=1))
    out(f"{st:22s} n={len(tr):5d} resolved quote: {dict(pd.Series(q.quote_outcome).value_counts())}  control n={len(ctrl)}")

all_ = pd.concat(frames, ignore_index=True)
ctl = pd.concat(ctrls, ignore_index=True)
for d in (all_, ctl):
    d["x"] = d.risk / d.spread_entry
    d["xb"] = pd.cut(d.x, X_EDGES, labels=X_LABELS, right=False)
    d["train"] = d.entry_time < pd.Timestamp(common.SPLIT, tz="UTC")
    d["hour"] = d.entry_time.dt.hour
all_["delta"] = all_.pts0 - all_.quote_pts0            # mid_M1 - quote_S5, cost-free
all_["delta_s5"] = all_.mid_pts0 - all_.quote_pts0
ctl["delta_s5"] = ctl.mid_pts0 - ctl.quote_pts0
all_.to_parquet(RES / "trades_quote_s5.parquet", index=False)
ctl.to_parquet(RES / "control_trades.parquet", index=False)
unres = all_.quote_outcome.isna().sum()
out(f"\nall Stage-1 trades: {len(all_):,}; unresolved (no S5 bars after entry): {unres}; "
    f"TIME under a 45-d horizon for no-hold strategies: {int(((all_.horizon_s == common.NO_HOLD_HORIZON_MIN*60) & (all_.quote_outcome=='TIME')).sum())}")
out(f"fill bar offset from entry_time: p50 {np.nanmedian(all_.fill_dt_s):.0f} s, p99 {np.nanquantile(all_.fill_dt_s,.99):.0f} s, >120 s: {(all_.fill_dt_s>120).sum()}")

# ---------------------------------------------------------------- per-strategy summary at both costs
rows = []
for st, d in all_.groupby("strategy"):
    for cost in COSTS:
        for part, m in (("ALL", np.ones(len(d), bool)), ("TRAIN", d.train.to_numpy()), ("TEST", ~d.train.to_numpy())):
            x = d[m]
            mid = x.pts0 - cost; qu = x.quote_pts0 - cost
            tp_mid = x.outcome == "TP"
            rows.append(dict(strategy=st, cost=cost, part=part, n=len(x), mid_pf=pf(mid), mid_pts=mid.sum(),
                             quote_pf=pf(qu), quote_pts=qu.sum(), pf_ratio=pf(qu) / pf(mid) if pf(mid) not in (0, np.inf) else np.nan,
                             tp_to_sl=int((tp_mid & (x.quote_outcome == "SL")).sum()), tp_mid=int(tp_mid.sum()),
                             tp_to_sl_share=(tp_mid & (x.quote_outcome == "SL")).sum() / max(tp_mid.sum(), 1),
                             delta_mean=x.delta.mean(), spread_entry_mean=x.spread_entry.mean(),
                             delta_over_spread=x.delta.mean() / x.spread_entry.mean(), x_median=x.x.median(),
                             risk_median=x.risk.median(), mid_s5_pf=pf(x.mid_pts0 - cost)))
summ = pd.DataFrame(rows)
summ.to_csv(RES / "geometry_by_strategy.csv", index=False, float_format="%.4f")
out("\n=== per strategy, cost 0.80, ALL: mid_M1 vs quote_S5 ===")
s8 = summ[(summ.cost == 0.80) & (summ.part == "ALL")]
out(s8[["strategy", "n", "mid_pf", "quote_pf", "pf_ratio", "mid_pts", "quote_pts", "tp_to_sl", "tp_mid", "tp_to_sl_share",
        "delta_mean", "spread_entry_mean", "delta_over_spread", "x_median", "risk_median"]].round(3).to_string(index=False))
out("\n=== per strategy TRAIN / TEST quote PF at both costs ===")
piv = summ[summ.part != "ALL"].pivot_table(index="strategy", columns=["cost", "part"], values=["mid_pf", "quote_pf"])
out(piv.round(3).to_string())

# ---------------------------------------------------------------- pooled curve by x bucket (TRAIN fit, ALL and TEST shown)
def curve(d, dcol, label):
    g = d.groupby("xb", observed=True).apply(lambda x: pd.Series({
        "n": len(x), "spread_mean": x.spread_entry.mean(), "risk_mean": x.risk.mean(),
        "tp_to_sl_share": ((x.outcome == "TP") & (x.quote_outcome == "SL")).mean() if "outcome" in x else ((x.mid_outcome == "TP") & (x.quote_outcome == "SL")).mean(),
        "delta_mean": x[dcol].mean(), "G": x[dcol].sum() / x.spread_entry.sum(),
        "G_se": x[dcol].std() / np.sqrt(len(x)) / x.spread_entry.mean(),
        "pf_mid_0.80": pf((x.pts0 if "pts0" in x else x.mid_pts0) - 0.80), "pf_quote_0.80": pf(x.quote_pts0 - 0.80),
    }), include_groups=False)
    g["pf_haircut_0.80"] = g["pf_quote_0.80"] / g["pf_mid_0.80"] - 1
    g.index.name = "x_bucket"; g.insert(0, "set", label)
    return g

cv = pd.concat([curve(all_[all_.train], "delta", "TRAIN"), curve(all_[~all_.train], "delta", "TEST"), curve(all_, "delta", "ALL"),
                curve(ctl[ctl.train], "delta_s5", "CONTROL_TRAIN"), curve(ctl, "delta_s5", "CONTROL_ALL"),
                curve(all_[all_.train], "delta_s5", "TRAIN_vs_midS5")])
cv.to_csv(RES / "geometry_curve.csv", float_format="%.4f")
out("\n=== pooled geometry curve by stop distance in spreads (x = risk / spread at entry) ===")
out("G = sum(mid - quote pts) / sum(spread at entry): the haircut in spread units. Random-walk null: G = 0.5 in every bucket.")
for lab in ("TRAIN", "TEST", "ALL", "CONTROL_TRAIN", "CONTROL_ALL", "TRAIN_vs_midS5"):
    out(f"-- {lab}")
    out(cv[cv.set == lab].drop(columns="set").round(3).to_string())
out(f"\nTRAIN pooled G = {all_[all_.train].delta.sum()/all_[all_.train].spread_entry.sum():.3f}; TEST {all_[~all_.train].delta.sum()/all_[~all_.train].spread_entry.sum():.3f}; "
    f"CONTROL TRAIN {ctl[ctl.train].delta_s5.sum()/ctl[ctl.train].spread_entry.sum():.3f}, CONTROL ALL {ctl.delta_s5.sum()/ctl.spread_entry.sum():.3f}")
out(f"mid_M1 vs mid_S5 (ALL): pts0 sum {all_.pts0.sum():.1f} vs {all_.mid_pts0.sum():.1f}; outcome agree {(all_.outcome==all_.mid_outcome).mean()*100:.2f} %")

# per-strategy G and residual vs the pooled TRAIN curve (fit check on TRAIN; TEST is script 04)
Gt = cv[cv.set == "TRAIN"].G
rows = []
for st, d in all_.groupby("strategy"):
    for part, m in (("TRAIN", d.train), ("ALL", np.ones(len(d), bool))):
        x = d[m]
        pred = (Gt.reindex(x.xb.astype(str)).to_numpy() * x.spread_entry.to_numpy())
        rows.append(dict(strategy=st, part=part, n=len(x), G_own=x.delta.sum() / x.spread_entry.sum(),
                         G_ctrl=ctl[(ctl.strategy == st) & (ctl.train if part == "TRAIN" else True)].delta_s5.sum() /
                                ctl[(ctl.strategy == st) & (ctl.train if part == "TRAIN" else True)].spread_entry.sum(),
                         actual_haircut=x.delta.sum(), pred_haircut=np.nansum(pred),
                         resid_pts=x.delta.sum() - np.nansum(pred),
                         resid_per_trade=(x.delta.sum() - np.nansum(pred)) / len(x)))
pr = pd.DataFrame(rows); pr.to_csv(RES / "geometry_residual_by_strategy.csv", index=False, float_format="%.4f")
out("\n=== per-strategy G (own) vs control G, and residual of the pooled TRAIN curve (TRAIN = in-sample fit check) ===")
out(pr[pr.part == "TRAIN"].round(3).to_string(index=False))

# per-strategy x-bucket table (0.80, ALL) for the report appendix
bt = all_.groupby(["strategy", "xb"], observed=True).apply(lambda x: pd.Series({
    "n": len(x), "G": x.delta.sum() / x.spread_entry.sum(), "pf_mid": pf(x.pts0 - .8), "pf_quote": pf(x.quote_pts0 - .8)}), include_groups=False)
bt.to_csv(RES / "geometry_by_strategy_bucket.csv", float_format="%.4f")

# ---------------------------------------------------------------- entry component by hour (market term) + nominal gap per strategy
ec = all_.groupby("hour").apply(lambda x: pd.Series({"n": len(x), "entry_mkt_mean": x.entry_mkt.mean(), "entry_mkt_p50": x.entry_mkt.median(),
                                                     "half_spread_mean": x.spread_entry.mean() / 2, "drift_5s": x.drift_mkt.mean(),
                                                     "entry_mkt_train": x[x.train].entry_mkt.mean(), "entry_mkt_test": x[~x.train].entry_mkt.mean(),
                                                     "entry_comp_nominal_mean": x.entry_comp.mean()}), include_groups=False)
ec.to_csv(RES / "entry_component_hour.csv", float_format="%.4f")
out("\n=== entry component by hour, MARKET term: (ask_fill - mid at signal close) long / (mid - bid_fill) short = half-spread + 5-s drift ===")
out(ec.round(3).to_string())
out(f"pooled: entry_mkt {all_.entry_mkt.mean():.3f} = half-spread {all_.spread_entry.mean()/2:.3f} + drift {all_.drift_mkt.mean():.3f}; "
    f"TRAIN {all_[all_.train].entry_mkt.mean():.3f}, TEST {all_[~all_.train].entry_mkt.mean():.3f}")
ng = all_.groupby("strategy").apply(lambda x: pd.Series({"n": len(x), "nominal_gap_mean": x.nominal_gap.mean(), "nominal_gap_p50": x.nominal_gap.median(),
                                                         "abs_gap_p50": x.nominal_gap.abs().median(), "share_abs_gap_gt_0.05": (x.nominal_gap.abs() > 0.05).mean(),
                                                         "entry_mkt_mean": x.entry_mkt.mean(), "entry_comp_vs_nominal": x.entry_comp.mean(),
                                                         "drift_5s": x.drift_mkt.mean()}), include_groups=False)
ng.to_csv(RES / "nominal_gap_by_strategy.csv", float_format="%.4f")
out("\n=== nominal entry vs market at signal time, per strategy (positive gap = harness books a better price than the market offered) ===")
out(ng.round(3).to_string())

# ---------------------------------------------------------------- observed overshoot at the triggering bar
so = all_[all_.quote_outcome == "SL"]
ov = so.groupby("hour").apply(lambda x: pd.Series({"n_stops": len(x), "overshoot_mean": x.quote_overshoot.mean(), "overshoot_p50": x.quote_overshoot.median(),
                                                   "overshoot_p90": x.quote_overshoot.quantile(.9), "overshoot_train": x[x.train].quote_overshoot.mean(),
                                                   "overshoot_test": x[~x.train].quote_overshoot.mean(), "share_gt_1pt": (x.quote_overshoot > 1).mean()}), include_groups=False)
ov.to_csv(RES / "overshoot_hour.csv", float_format="%.4f")
out("\n=== observed overshoot beyond the stop at the triggering S5 bar (quote stop-outs), by entry hour ===")
out(ov.round(3).to_string())
so2 = so.assign(rb=pd.cut(so.risk, [0, 2, 3, 5, 10, 1e9], labels=["<2", "2-3", "3-5", "5-10", "10+"]))
ovb = so2.groupby("rb", observed=True).apply(lambda x: pd.Series({"n_stops": len(x), "overshoot_mean": x.quote_overshoot.mean(), "overshoot_p90": x.quote_overshoot.quantile(.9),
                                                                  "train": x[x.train].quote_overshoot.mean(), "test": x[~x.train].quote_overshoot.mean()}), include_groups=False)
ovb.to_csv(RES / "overshoot_stopbucket.csv", float_format="%.4f")
out("by stop distance (pts):"); out(ovb.round(3).to_string())
core = (so.hour >= 7) & (so.hour <= 16)
out(f"07-16 UTC stop-outs: overshoot mean {so[core].quote_overshoot.mean():.3f} (TRAIN {so[core & so.train].quote_overshoot.mean():.3f}, TEST {so[core & ~so.train].quote_overshoot.mean():.3f}); "
    f"stop-out share of trades {100*(all_.quote_outcome=='SL').mean():.1f} %")
out("done")
