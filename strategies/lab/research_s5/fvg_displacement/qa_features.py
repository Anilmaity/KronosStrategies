"""Question A: rank S5 displacement features by association with 'respected' (TRAIN only,
stratified on gap-size quintile, permutation null), then read TEST once for the top feature
and for gap size. Output: results/qa_train_ranking*.csv, results/qa_test*.csv, results/qa_output.txt.
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import pandas as pd
from scipy.stats import rankdata, spearmanr
from common import RESULTS, FEATURES, DIRECTION

NPERM = 1000
rng = np.random.default_rng(20260918)
ev = pd.read_parquet(RESULTS / "events.parquet")


def auc(x, y):
    """AUC of score x for binary y (1 = positive)."""
    r = rankdata(x); n1 = y.sum(); n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return np.nan
    return (r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


def strat_auc(x, y, strata):
    tot = 0.0; n = 0
    for s in np.unique(strata):
        m = strata == s
        a = auc(x[m], y[m])
        if not np.isnan(a):
            tot += a * m.sum(); n += m.sum()
    return tot / n


def perm_null(x, y, strata, nperm):
    """Stratified AUC under label permutation within strata (vectorised per stratum)."""
    out = np.zeros(nperm)
    N = len(y)
    for s in np.unique(strata):
        m = strata == s
        r = rankdata(x[m]); ys = y[m]; n1 = ys.sum(); n0 = len(ys) - n1
        if n1 == 0 or n0 == 0:
            continue
        # permuted positives = random subsets of size n1: sum of ranks via shuffled index
        idx = np.argsort(rng.random((nperm, len(ys))), axis=1)[:, :n1]
        sums = r[idx].sum(1)
        out += ((sums - n1 * (n1 + 1) / 2) / (n1 * n0)) * m.sum() / N
    return out


def run(label_col, tag):
    print(f"\n{'='*90}\n== label = {label_col} ({tag})\n{'='*90}")
    d = ev[ev[label_col].isin(["respected", "filled_through"])].copy()
    d["y"] = (d[label_col] == "respected").astype(int)
    tr = d[d.split == "TRAIN"]; te = d[d.split == "TEST"]
    for nm, x in (("TRAIN", tr), ("TEST", te)):
        allx = ev[ev.split == nm]
        print(f"{nm}: events {len(allx)}  touched {(allx[label_col]!='untouched').sum()} "
              f"({100*(allx[label_col]!='untouched').mean():.1f}%)  unresolved {(allx[label_col]=='unresolved').sum()}  "
              f"resolved {len(x)}  respected {x.y.sum()}  BASE RATE {100*x.y.mean():.2f}%")
    edges = np.quantile(tr.gap_atr, [0, .2, .4, .6, .8, 1.0]); edges[0] = -np.inf; edges[-1] = np.inf
    print("gap_atr quintile edges (TRAIN):", np.round(edges[1:-1], 3))
    for x in (tr, te):
        x["gq"] = np.digitize(x.gap_atr, edges[1:-1])
    print("TRAIN respect rate by gap_atr quintile:",
          tr.groupby("gq").y.mean().round(3).to_dict(), " n:", tr.groupby("gq").size().to_dict())
    print("TRAIN respect rate killzone vs not:", tr.groupby("killzone").y.mean().round(3).to_dict())
    print("TRAIN respect rate bull vs bear:", tr.groupby("dir").y.mean().round(3).to_dict())

    rows = []
    ytr = tr.y.to_numpy(); gq = tr.gq.to_numpy()
    for f in FEATURES + ["gap_atr", "gap_size"]:
        s = DIRECTION.get(f, 1)
        x = s * tr[f].to_numpy()
        a = auc(x, ytr)
        sa = strat_auc(x, ytr, gq)
        null = perm_null(x, ytr, gq, NPERM)
        p = float((null >= sa).mean())
        thr = np.median(tr[f])
        good = (s * tr[f] >= s * thr)
        lift = tr.y[good].mean() / tr.y.mean()
        diff = 100 * (tr.y[good].mean() - tr.y[~good].mean())
        rho = spearmanr(tr[f], tr.gap_atr).correlation
        rows.append(dict(feature=f, direction=s, n=len(tr), auc=a, strat_auc=sa, perm_p=p,
                         null_mean=null.mean(), null_p95=np.quantile(null, .95), train_median=thr,
                         lift_good_half=lift, diff_pp=diff, spearman_vs_gap_atr=rho))
    rk = pd.DataFrame(rows).sort_values("strat_auc", ascending=False)
    print("\nTRAIN ranking (AUC oriented in declared direction; strat = within gap_atr quintile; "
          f"perm null {NPERM} within-stratum label shuffles; Bonferroni 7 -> p < {0.05/7:.4f}):")
    print(rk.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    rk.to_csv(RESULTS / f"qa_train_ranking_{tag}.csv", index=False)
    # killzone as a binary
    kz_rate = tr.groupby("killzone").y.mean()
    print(f"\nkillzone: TRAIN respect rate in KZ {kz_rate.get(True, np.nan):.4f} vs out {kz_rate.get(False, np.nan):.4f}")

    top = rk[rk.feature.isin(FEATURES)].iloc[0].feature
    print(f"\nTOP FEATURE (TRAIN, stratified AUC): {top}")
    # TEST read: once, for the top feature and gap_atr
    out = []
    yte = te.y.to_numpy(); gqte = te.gq.to_numpy()
    for f in (top, "gap_atr"):
        s = DIRECTION.get(f, 1)
        thr = rk.set_index("feature").loc[f, "train_median"]
        for nm, x, y, q in (("TRAIN", tr, ytr, gq), ("TEST", te, yte, gqte)):
            good = (s * x[f] >= s * thr)
            out.append(dict(feature=f, split=nm, n=len(x), auc=auc(s * x[f].to_numpy(), y),
                            strat_auc=strat_auc(s * x[f].to_numpy(), y, q),
                            base_rate=y.mean(), rate_good_half=x.y[good].mean(), rate_bad_half=x.y[~good].mean(),
                            lift=x.y[good].mean() / y.mean(), diff_pp=100 * (x.y[good].mean() - x.y[~good].mean())))
    t = pd.DataFrame(out)
    print("\nTRAIN/TEST for the top feature and gap_atr (threshold = TRAIN median):")
    print(t.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    t.to_csv(RESULTS / f"qa_test_{tag}.csv", index=False)
    # respect rate by top-feature quintile within each gap quintile (TRAIN) — the confound table
    tr["fq"] = pd.qcut(DIRECTION.get(top, 1) * tr[top], 5, labels=False)
    tab = tr.pivot_table(index="gq", columns="fq", values="y", aggfunc="mean")
    print(f"\nTRAIN respect rate: rows gap_atr quintile, cols {top} quintile (declared direction, 0 = worst):")
    print(tab.round(3).to_string())
    tab.to_csv(RESULTS / f"qa_confound_table_{tag}.csv")
    return top


top_primary = run("label", "primary")
top_ce = run("label_ce", "ce")
print(f"\nTOP FEATURE for Q.B (primary label, as pre-registered): {top_primary}   [CE-label top: {top_ce}]")
(RESULTS / "qa_top_feature.txt").write_text(top_primary + "\n")
