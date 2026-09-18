"""Question B: c03-like CE-limit trade on every M5 FVG, with vs without the top S5 feature
filter (threshold chosen on TRAIN from the declared 3-value grid), killzone as a second axis,
gap-size as the confound arm, permutation control, xau2y bars. Output: results/qb_*.csv,
results/qb_output.txt.
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import pandas as pd
from common import RESULTS, COSTS, DIRECTION, HORIZON_S, pf, gold_monthly

NPERM = 200
rng = np.random.default_rng(20260918)
ev = pd.read_parquet(RESULTS / "events.parquet")
top = (RESULTS / "qa_top_feature.txt").read_text().strip()
sgn = DIRECTION[top]
gold = gold_monthly()
gold.index = gold.index.strftime("%Y-%m")

tr_all = ev[ev.split == "TRAIN"]
gap_med = float(tr_all.gap_atr.median())
grid_q = [0.33, 0.50, 0.67]
grid = {q: float(np.quantile(sgn * tr_all[top], 1 - q)) for q in grid_q}   # keep the "good" q-share
print(f"top feature: {top} (direction {sgn:+d}); gap_atr TRAIN median {gap_med:.4f}")
print("threshold grid (keep sgn*feature >= thr, i.e. the best q share of TRAIN):",
      {q: round(sgn * v, 4) for q, v in grid.items()})
tr = ev[ev.filled].copy()
tr["fscore"] = sgn * tr[top]
print(f"filled trades: {len(tr)} of {len(ev)} events  (TRAIN {int((tr.split=='TRAIN').sum())}, TEST {int((tr.split=='TEST').sum())})")
print("outcome mix:", tr.groupby("split").outcome.value_counts().unstack().to_string())


def stats(t, cost):
    x = t.raw_pts - cost
    out = {}
    for nm in ("TRAIN", "TEST"):
        m = t.split == nm
        v = x[m]
        out[f"{nm}_n"] = int(m.sum()); out[f"{nm}_pf"] = pf(v); out[f"{nm}_net"] = float(v.sum())
        out[f"{nm}_wr"] = float((v > 0).mean()) if m.sum() else np.nan
    m = t.split == "TEST"
    mo = x[m].groupby(t.month[m]).sum()
    out["TEST_months"] = len(mo)
    out["TEST_months_pos"] = float((mo > 0).mean()) if len(mo) else np.nan
    out["TEST_max_month_share"] = float(mo.max() / mo.sum()) if len(mo) and mo.sum() > 0 else np.nan
    allmo = x.groupby(t.month).sum()
    g = gold.reindex(allmo.index)
    out["up_months_net"] = float(allmo[g > 0].sum()); out["down_months_net"] = float(allmo[g <= 0].sum())
    out["gold_corr"] = float(np.corrcoef(allmo.values, g.fillna(0).values)[0, 1]) if len(allmo) > 2 else np.nan
    b1 = out["TEST_n"] >= 40
    b3 = out["TRAIN_pf"] > 0.9
    b4 = (out["TEST_months_pos"] >= 0.55) and (out["TEST_max_month_share"] <= 0.5)
    b5 = (out["up_months_net"] > 0 and out["down_months_net"] > 0) or abs(out["gold_corr"]) < 0.4
    out.update(bar_n=b1, bar_train=b3, bar_months=b4, bar_regime=b5)
    return out


def arm_pop(name, thr=None):
    m = np.ones(len(tr), bool)
    kz = tr.killzone.to_numpy(); gs = (tr.gap_atr >= gap_med).to_numpy()
    fe = (tr.fscore >= thr).to_numpy() if thr is not None else None
    return {"A0": m, "A1": kz, "A2": gs, "A3": kz & gs, "A4": fe, "A5": kz & fe, "A6": kz & gs & fe}[name]


# ---- threshold choice on TRAIN (arm A4, PF @0.80)
print("\n== threshold grid on TRAIN, arm A4 (feature only), cost 0.80 ==")
rows = []
for q, thr in grid.items():
    t = tr[tr.fscore >= thr]
    s = stats(t, 0.80)
    rows.append(dict(q_keep=q, thr=sgn * thr, TRAIN_n=s["TRAIN_n"], TRAIN_pf=s["TRAIN_pf"], TRAIN_net=s["TRAIN_net"]))
gr = pd.DataFrame(rows); print(gr.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
gr.to_csv(RESULTS / "qb_threshold_grid.csv", index=False)
best = gr.sort_values(["TRAIN_pf", "TRAIN_n"], ascending=[False, False]).iloc[0]
q_star = float(best.q_keep); thr_star = grid[q_star]
print(f"T* = keep best {q_star:.2f} share -> {top} {'<=' if sgn<0 else '>='} {sgn*thr_star:.4f}")

# ---- arms
print("\n== arms (TRAIN/TEST x cost) ==")
rows = []
for arm in ["A0", "A1", "A2", "A3", "A4", "A5", "A6"]:
    m = arm_pop(arm, thr_star)
    for cost in COSTS:
        s = stats(tr[m], cost); s.update(arm=arm, cost=cost); rows.append(s)
res = pd.DataFrame(rows)
cols = ["arm", "cost", "TRAIN_n", "TRAIN_pf", "TRAIN_net", "TRAIN_wr", "TEST_n", "TEST_pf", "TEST_net", "TEST_wr",
        "TEST_months_pos", "TEST_max_month_share", "up_months_net", "down_months_net", "gold_corr"]
print(res[cols].to_string(index=False, float_format=lambda v: f"{v:.3f}"))
res["bar_pf_both"] = res.groupby("arm").TEST_pf.transform(lambda v: (v > 1).all())
res["PASS"] = res.bar_n & res.bar_pf_both & res.bar_train & res.bar_months & res.bar_regime
print("\nbars:")
print(res[["arm", "cost", "bar_n", "bar_pf_both", "bar_train", "bar_months", "bar_regime", "PASS"]].to_string(index=False))
res.to_csv(RESULTS / "qb_arms.csv", index=False)

# ---- permutation control: same parent population, feature values shuffled, same count
print(f"\n== permutation control ({NPERM} shuffles of {top} across the parent population; TEST) ==")
rows = []
parent = {"A4": "A0", "A5": "A1", "A6": "A3"}
for arm, par in parent.items():
    pm = arm_pop(par, thr_star)
    base = tr[pm]
    keep = (base.fscore >= thr_star).to_numpy()
    real = {c: stats(base[keep], c) for c in COSTS}
    fs = base.fscore.to_numpy()
    sims = {c: [] for c in COSTS}
    for _ in range(NPERM):
        kp = rng.permutation(fs) >= thr_star
        sub = base[kp]
        for c in COSTS:
            x = sub.raw_pts - c; m = sub.split == "TEST"
            sims[c].append((pf(x[m]), float(x[m].sum())))
    for c in COSTS:
        a = np.array(sims[c])
        rows.append(dict(arm=arm, parent=par, cost=c, n_real=real[c]["TEST_n"], real_TEST_pf=real[c]["TEST_pf"],
                         real_TEST_net=real[c]["TEST_net"],
                         ctrl_pf_med=np.median(a[:, 0]), ctrl_pf_p95=np.quantile(a[:, 0], .95),
                         ctrl_net_med=np.median(a[:, 1]), ctrl_net_p95=np.quantile(a[:, 1], .95),
                         pct_rank_pf=float((a[:, 0] < real[c]["TEST_pf"]).mean()),
                         pct_rank_net=float((a[:, 1] < real[c]["TEST_net"]).mean())))
ctrl = pd.DataFrame(rows); print(ctrl.to_string(index=False, float_format=lambda v: f"{v:.3f}"))
ctrl.to_csv(RESULTS / "qb_control.csv", index=False)

# ---- does the feature add anything beyond gap size and killzone? (TEST PF @0.80)
print("\n== feature increment on TEST PF @0.80 ==")
r80 = res[res.cost == 0.80].set_index("arm")
for a, b in (("A4", "A0"), ("A5", "A1"), ("A6", "A3")):
    print(f"  {a} vs {b}: PF {r80.loc[a,'TEST_pf']:.3f} vs {r80.loc[b,'TEST_pf']:.3f}  (delta {r80.loc[a,'TEST_pf']-r80.loc[b,'TEST_pf']:+.3f});"
          f" net {r80.loc[a,'TEST_net']:+.1f} vs {r80.loc[b,'TEST_net']:+.1f}")

# ---- risk-bucket anatomy (why the base arm looks the way it does)
print("\n== A0 anatomy by risk (R = |CE - SL|) bucket, cost 0.45 ==")
tr["Rb"] = pd.cut(tr.risk, [0, 0.6, 0.8, 1.0, 1.5, 2.5, 100], labels=["<0.6", "0.6-0.8", "0.8-1", "1-1.5", "1.5-2.5", "2.5+"])
tr["on_fill_bar"] = tr.exit_t == tr.fill_t
an = tr.groupby("Rb", observed=True).apply(lambda x: pd.Series({
    "n": len(x), "TP%": 100 * (x.outcome == "TP").mean(), "SL_on_fill_bar%": 100 * x.on_fill_bar.mean(),
    "net@0.45": (x.raw_pts - 0.45).sum(), "PF@0.45": pf(x.raw_pts - 0.45)}), include_groups=False)
print(an.to_string(float_format=lambda v: f"{v:.2f}"))
an.to_csv(RESULTS / "qb_anatomy_by_risk.csv")

# ---- sensitivity: one open/resting order per direction at a time (all arms, T*)
print("\n== sensitivity: one resting/open order per direction (greedy in t0 order) ==")
rows = []
evs = ev.sort_values("t0")
for arm in ["A0", "A1", "A2", "A3", "A4", "A5", "A6"]:
    kz = evs.killzone.to_numpy(); gs = (evs.gap_atr >= gap_med).to_numpy(); fe = (sgn * evs[top] >= thr_star).to_numpy()
    m = {"A0": np.ones(len(evs), bool), "A1": kz, "A2": gs, "A3": kz & gs, "A4": fe, "A5": kz & fe, "A6": kz & gs & fe}[arm]
    sub = evs[m]
    busy = {1: -1, -1: -1}; take = np.zeros(len(sub), bool)
    t0v = sub.t0.to_numpy(); dv = sub["dir"].to_numpy(); fl = sub.filled.to_numpy(); ex = sub.exit_t.to_numpy()
    for k in range(len(sub)):
        d = dv[k]
        if t0v[k] >= busy[d]:
            take[k] = True
            busy[d] = ex[k] if fl[k] else t0v[k] + HORIZON_S
    t = sub[take & fl]
    for cost in COSTS:
        s = stats(t, cost); s.update(arm=arm, cost=cost); rows.append(s)
sens = pd.DataFrame(rows)
print(sens[["arm", "cost", "TRAIN_n", "TRAIN_pf", "TRAIN_net", "TEST_n", "TEST_pf", "TEST_net", "TEST_months_pos"]].to_string(index=False, float_format=lambda v: f"{v:.3f}"))
sens.to_csv(RESULTS / "qb_arms_one_per_direction.csv", index=False)
print(f"\ncomparisons: TRAIN grid 3; TEST arms 7 x 2 costs = 14; Q.A 9 TRAIN + 4 TEST reads; controls 3 x 2.")
