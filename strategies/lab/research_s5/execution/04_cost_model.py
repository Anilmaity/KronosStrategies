"""04_cost_model.py -- assemble the cost model cost(h, d) = E(h) + G(x)*s + P(SL)*S(h, d) from TRAIN,
validate the geometry term on TEST against quote_S5 (pre-registered X), derive the flat-cost
equivalents for 07-16 UTC and apply decision rule (b) to the Stage-1 arms."""
from __future__ import annotations

import numpy as np
import pandas as pd

import common
from common import RES, Tee, X_EDGES, X_LABELS, COSTS, pf

out = Tee(RES / "04_cost_model.txt")
tr = pd.read_parquet(RES / "trades_quote_s5.parquet")
tr["xb"] = pd.cut(tr.risk / tr.spread_entry, X_EDGES, labels=X_LABELS, right=False).astype(str)
curve = pd.read_csv(RES / "geometry_curve.csv").set_index(["set", "x_bucket"])
G_train = curve.loc["TRAIN"].G
G_test = curve.loc["TEST"].G
sp_h = pd.read_csv(RES / "spread_hour.csv").set_index("hour")
slip_h = pd.read_csv(RES / "jumps_slippage_hour.csv").set_index("hour")
ov_b = pd.read_csv(RES / "overshoot_stopbucket.csv").set_index("rb")
ec_h = pd.read_csv(RES / "entry_component_hour.csv").set_index("hour")
D_B = [("<2", 1.5), ("2-3", 2.5), ("3-5", 4.0), ("5-10", 7.0), ("10+", 15.0)]      # bucket, representative d
train = tr[tr.train]; test = tr[~tr.train]
DRIFT = float(train.drift_mkt.mean())

# ---------------------------------------------------------------- 1. the model table (TRAIN-fitted)
def build(G, slip_col, ec_col, drift, label):
    rows = []
    for h in range(24):
        s = sp_h.loc[h, "p50"]
        n_h = ec_h.loc[h, "n"] if h in ec_h.index else 0
        E = ec_h.loc[h, ec_col] if (h in ec_h.index and n_h >= 200 and not np.isnan(ec_h.loc[h, ec_col])) else s / 2 + drift
        S = slip_h.loc[h, slip_col]
        row = dict(hour=h, spread_p50=s, entry=E, slip_per_stop=S)
        for b, d in D_B:
            x = d / s
            xb = X_LABELS[int(np.searchsorted(X_EDGES, x, "right")) - 1]
            g = G[xb] * s
            # stop-distance dependence of the overshoot, as a multiplier on the hour value (TRAIN-fitted ratios)
            mult = ov_b.loc[b, "train"] / ov_b["train"].mean()
            row[f"geom_{b}"] = g
            row[f"slip_{b}"] = S * mult
            row[f"total_{b}"] = E + g + 0.5 * S * mult
        rows.append(row)
    t = pd.DataFrame(rows)
    t.insert(0, "fit", label)
    return t

tab_train = build(G_train, "slip_train", "entry_mkt_train", DRIFT, "TRAIN")
tab_test = build(G_test, "slip_test", "entry_mkt_test", float(test.drift_mkt.mean()), "TEST_era")
pd.concat([tab_train, tab_test]).to_csv(RES / "cost_model.csv", index=False, float_format="%.3f")
out("=== cost model, TRAIN-fitted: per-trade points relative to the mid-M1 harness at cost 0 ===")
out("cost(h, d) = entry(h) + geom(d, h) + P(SL) * slip(h, d); the total_* columns assume P(SL) = 0.5")
out("entry(h) = measured fill vs the market mid at the signal close (half-spread + 5-s drift), generic; the strategy-specific nominal-entry gap is NOT in this table (see nominal_gap_by_strategy.csv); hours with < 200 Stage-1 entries use p50 spread/2 + pooled drift")
out(tab_train.drop(columns="fit").round(3).to_string(index=False))
out("\n=== the same table with TEST-era inputs (2025-12 -> 2026-09: wider spreads, 2x jumps) ===")
out(tab_test.drop(columns="fit").round(3).to_string(index=False))
core = (tab_train.hour >= 7) & (tab_train.hour <= 16)
out("\n07-16 UTC means, TRAIN-fitted: " + ", ".join(f"{c}={tab_train.loc[core, c].mean():.3f}" for c in ["entry", "slip_per_stop"] + [f"total_{b}" for b, _ in D_B]))
out("07-16 UTC means, TEST-era:     " + ", ".join(f"{c}={tab_test.loc[core, c].mean():.3f}" for c in ["entry", "slip_per_stop"] + [f"total_{b}" for b, _ in D_B]))

# ---------------------------------------------------------------- 2. pre-registered validation on TEST (geometry term)
def predict_delta(d, G):
    return G.reindex(d.xb).to_numpy() * d.spread_entry.to_numpy()

rows = []
for st, d in test.groupby("strategy"):
    pd_ = predict_delta(d, G_train)
    for cost in COSTS:
        mid = d.pts0 - cost; qu = d.quote_pts0 - cost
        pred = mid - pd_
        act_net, pred_net = qu.sum(), pred.sum()
        rows.append(dict(strategy=st, cost=cost, n=len(d), mid_net=mid.sum(), quote_net=act_net, pred_net=pred_net,
                         actual_haircut=d.delta.sum(), pred_haircut=pd_.sum(), err_pts=pred_net - act_net,
                         X_pct=100 * abs(pred_net - act_net) / abs(act_net) if abs(act_net) > 0 else np.nan,
                         err_per_trade=(pred_net - act_net) / len(d),
                         mid_pf=pf(mid), quote_pf=pf(qu), pred_pf=pf(pred), pf_err=pf(pred) - pf(qu),
                         eligible=abs(act_net) >= 100))
val = pd.DataFrame(rows)
val.to_csv(RES / "validation_test.csv", index=False, float_format="%.4f")
out("\n=== TEST validation (read once): predicted quote net = mid_M1 net - sum G_train(x) * spread_entry ===")
for cost in COSTS:
    v = val[val.cost == cost]
    out(f"-- cost {cost}")
    out(v[["strategy", "n", "mid_net", "quote_net", "pred_net", "actual_haircut", "pred_haircut", "err_pts", "X_pct", "mid_pf", "quote_pf", "pred_pf", "pf_err", "eligible"]].round(3).to_string(index=False))
    pooled_X = 100 * abs(v.pred_net.sum() - v.quote_net.sum()) / abs(v.quote_net.sum())
    pooled_h = 100 * (v.pred_haircut.sum() - v.actual_haircut.sum()) / v.actual_haircut.sum()
    out(f"pooled TEST: actual quote net {v.quote_net.sum():.1f}, predicted {v.pred_net.sum():.1f}, X = {pooled_X:.2f} % of |quote net|; "
        f"haircut predicted {v.pred_haircut.sum():.1f} vs actual {v.actual_haircut.sum():.1f} ({pooled_h:+.1f} %); "
        f"per-strategy median X (eligible) {v[v.eligible].X_pct.median():.1f} %, max {v[v.eligible].X_pct.max():.1f} %; "
        f"|PF err| median {v.pf_err.abs().median():.3f}, max {v.pf_err.abs().max():.3f}")
    ok = pooled_X <= 10 and v[v.eligible].X_pct.median() <= 15
    out(f"pre-declared adequacy (pooled X <= 10 % and eligible median X <= 15 %): {'PASS' if ok else 'FAIL'}")
# the haircut itself, pooled, as the cleaner test of the geometry model (independent of the cost level)
h_act = test.delta.sum(); h_pred = predict_delta(test, G_train).sum()
out(f"\nTEST geometry haircut, all 15 strategies pooled: actual {h_act:.1f} pts on {len(test)} trades ({h_act/len(test):.3f}/trade), "
    f"predicted {h_pred:.1f} ({h_pred/len(test):.3f}/trade): over-prediction {100*(h_pred/h_act-1):+.1f} %")
# post-hoc diagnostics (NOT adopted, labelled): flat-points model and TEST-fitted G
flat = train.delta.mean()
out(f"post-hoc, not adopted: a flat TRAIN-mean haircut of {flat:.3f} pts/trade predicts TEST haircut {flat*len(test):.1f} ({100*(flat*len(test)/h_act-1):+.1f} %); "
    f"the same G(x) curve refitted on TEST would be exact by construction (G_test pooled {test.delta.sum()/test.spread_entry.sum():.3f} vs G_train {train.delta.sum()/train.spread_entry.sum():.3f})")

# ---------------------------------------------------------------- 3. flat-cost equivalents (pre-declared rule) for 07-16 UTC
def full_cost(d, G, slip_col, ov_col, ec_col, nominal=False):
    """Per-trade all-in cost: entry + geometry + 1[SL] * slippage(hour, stop bucket)."""
    E = d.entry_mkt.to_numpy() + (d.nominal_gap.to_numpy() if nominal else 0.0)
    g = G.reindex(d.xb).to_numpy() * d.spread_entry.to_numpy()
    S_h = slip_h.loc[d.hour.to_numpy(), slip_col].to_numpy()
    rb = pd.cut(d.risk, [0, 2, 3, 5, 10, 1e9], labels=["<2", "2-3", "3-5", "5-10", "10+"]).astype(str)
    mult = (ov_b.loc[rb, ov_col].to_numpy() / ov_b[ov_col].mean())
    is_sl = (d.quote_outcome == "SL").to_numpy()
    obs = np.where(is_sl, np.nan_to_num(d.quote_overshoot.to_numpy()), 0.0)
    return pd.DataFrame({"entry": E, "geom": g, "slip_model": is_sl * S_h * mult, "slip_obs": obs}, index=d.index)

rows = []
for label, d, G, sc, oc, ecc in (("TRAIN", train, G_train, "slip_train", "train", "entry_comp_train"),
                                 ("TEST", test, G_test, "slip_test", "test", "entry_comp_test")):
    c = full_cost(d, G, sc, oc, ecc)
    for win, m in (("07-16 UTC", (d.hour >= 7) & (d.hour <= 16)), ("all hours", np.ones(len(d), bool))):
        x = c[m.to_numpy() if hasattr(m, "to_numpy") else m]
        rows.append(dict(set=label, window=win, n=len(x), entry=x.entry.mean(), geom=x.geom.mean(), slip_model=x.slip_model.mean(),
                         slip_observed=x.slip_obs.mean(), total_model=(x.entry + x.geom + x.slip_model).mean(),
                         total_observed=(x.entry + x.geom + x.slip_obs).mean(),
                         actual_delta=d[m].delta.mean(), p_sl=(d[m].quote_outcome == "SL").mean(), risk_median=d[m].risk.median()))
fe = pd.DataFrame(rows); fe.to_csv(RES / "flat_equivalents.csv", index=False, float_format="%.4f")
out("\n=== flat-cost equivalents: mean per-trade all-in cost over the Stage-1 trades (entry + geometry + stop slippage) ===")
out(fe.round(3).to_string(index=False))
# by stop bucket, 07-16, TRAIN and TEST
rows = []
for label, d, G, sc, oc, ecc in (("TRAIN", train, G_train, "slip_train", "train", "entry_comp_train"),
                                 ("TEST", test, G_test, "slip_test", "test", "entry_comp_test")):
    c = full_cost(d, G, sc, oc, ecc)
    m = (d.hour >= 7) & (d.hour <= 16)
    rb = pd.cut(d.risk, [0, 2, 3, 5, 10, 1e9], labels=["<2", "2-3", "3-5", "5-10", "10+"])
    for b in ["<2", "2-3", "3-5", "5-10", "10+"]:
        mm = (m & (rb == b)).to_numpy()
        x = c[mm]
        rows.append(dict(set=label, stop=b, n=int(mm.sum()), entry=x.entry.mean(), geom=x.geom.mean(), slip_obs=x.slip_obs.mean(),
                         total_observed=(x.entry + x.geom + x.slip_obs).mean(), p_sl=(d[mm].quote_outcome == "SL").mean()))
fb = pd.DataFrame(rows); fb.to_csv(RES / "flat_equivalents_by_stop.csv", index=False, float_format="%.4f")
out("\n07-16 UTC all-in cost by stop bucket (observed slippage):")
out(fb.round(3).to_string(index=False))

# ---------------------------------------------------------------- 4. decision rule (b): does the table move any Stage-1 verdict?
out("\n=== decision rule (b): TEST PF of every Stage-1 strategy under the flat costs vs the model ===")
rows = []
for st, d in tr.groupby("strategy"):
    te = d[~d.train]; trn = d[d.train]
    c_tr = full_cost(trn, G_train, "slip_train", "train", "entry_comp_train")
    c_te = full_cost(te, G_train, "slip_train", "train", "entry_comp_train")          # TRAIN-fitted model applied to TEST
    c_te2 = full_cost(te, G_test, "slip_test", "test", "entry_comp_test")             # TEST-era inputs (regime-current)
    c_ten = full_cost(te, G_train, "slip_train", "train", "entry_comp_train", nominal=True)   # + the strategy's nominal-fill gap
    c_trn = full_cost(trn, G_train, "slip_train", "train", "entry_comp_train", nominal=True)
    r = dict(strategy=st, test_n=len(te),
             test_pf_0_45=pf(te.pts0 - .45), test_pf_0_80=pf(te.pts0 - .80),
             test_pf_model=pf(te.pts0 - (c_te.entry + c_te.geom + c_te.slip_model)),
             test_pf_model_testera=pf(te.pts0 - (c_te2.entry + c_te2.geom + c_te2.slip_model)),
             test_pf_quote_s5_0_80=pf(te.quote_pts0 - .80),
             train_pf_0_45=pf(trn.pts0 - .45), train_pf_model=pf(trn.pts0 - (c_tr.entry + c_tr.geom + c_tr.slip_model)),
             model_cost_per_trade_test=(c_te.entry + c_te.geom + c_te.slip_model).mean(),
             testera_cost_per_trade=(c_te2.entry + c_te2.geom + c_te2.slip_model).mean(),
             test_pf_model_nominal=pf(te.pts0 - (c_ten.entry + c_ten.geom + c_ten.slip_model)),
             train_pf_model_nominal=pf(trn.pts0 - (c_trn.entry + c_trn.geom + c_trn.slip_model)),
             nominal_gap_per_trade=te.nominal_gap.mean())
    r["flat_pass"] = (r["test_pf_0_45"] > 1) and (r["test_pf_0_80"] > 1) and (r["train_pf_0_45"] > 0.9) and len(te) >= 40
    r["model_pass"] = (r["test_pf_model"] > 1) and (r["test_pf_model_testera"] > 1) and (r["train_pf_model"] > 0.9) and len(te) >= 40
    r["model_nominal_pass"] = (r["test_pf_model_nominal"] > 1) and (r["train_pf_model_nominal"] > 0.9) and len(te) >= 40
    rows.append(r)
dec = pd.DataFrame(rows); dec.to_csv(RES / "decision_rule_b.csv", index=False, float_format="%.4f")
out(dec.round(3).to_string(index=False))
out(f"verdict changes, generic table (hour x stop) vs flat costs: {int((dec.flat_pass != dec.model_pass).sum())} -> " +
    str(dec.loc[dec.flat_pass != dec.model_pass, "strategy"].tolist()))
out(f"verdict changes once the strategy's nominal-fill gap is added: {int((dec.flat_pass != dec.model_nominal_pass).sum())} -> " +
    str(dec.loc[dec.flat_pass != dec.model_nominal_pass, "strategy"].tolist()))

# ---------------------------------------------------------------- 5. post-hoc diagnostic (recorded, not adopted): why G fell on TEST
jm = pd.read_csv(RES / "jumps_month.csv").set_index("month")
tr["month"] = tr.entry_time.dt.strftime("%Y-%m")
gm = tr.groupby("month").apply(lambda x: pd.Series({"n": len(x), "G": x.delta.sum() / x.spread_entry.sum(), "delta": x.delta.mean(),
                                                    "spread": x.spread_entry.mean()}), include_groups=False)
gm["J_mean_0716"] = jm["mean"]; gm["J_over_s"] = gm.J_mean_0716 / gm.spread; gm["train"] = gm.index < "2025-12"
gm.to_csv(RES / "geometry_by_month.csv", float_format="%.4f")
t = gm[gm.train]; e = gm[~gm.train]
b, a = np.polyfit(t.J_over_s, t.G, 1)
out("\n=== post-hoc diagnostic (TRAIN months only for the fit; recorded as a next hypothesis, not adopted) ===")
out(gm.round(3).to_string())
out(f"TRAIN months: corr(G, J/s) = {np.corrcoef(t.G, t.J_over_s)[0,1]:+.3f}; corr(delta pts, spread) = {np.corrcoef(t.delta, t.spread)[0,1]:+.3f}; "
    f"fit G = {a:.3f} {b:+.3f} * (mean 5-s jump / spread). Predicted TEST G at J/s = {e.J_over_s.mean():.3f}: {a + b*e.J_over_s.mean():.3f}; actual TEST G {e.G.mean():.3f}")
out("done")
