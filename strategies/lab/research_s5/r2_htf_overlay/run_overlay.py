"""r2_htf_overlay — label every Stage-1 trade with each HTF-bias gate, re-cost, resolve on
S5 quotes, and compute the pre-registered statistics (PROTOCOL.md §4–§6).

    cd strategies && ../.venv/bin/python -m lab.research_s5.r2_htf_overlay.run_overlay

Outputs (results/):
  labels_<module>.parquet   per-trade labels, raw mid points, raw quote-S5 points
  cells.csv                 every (module, gate, cost model, split) cell with stats + nulls
  regime2x2.csv             each corpus gate conditional on the SMA20 control
  pooled.csv                the book-level test of the corpus claim
  survivor_bars.csv         bars 1–7 for the gated arms of the four survivors
  info.json                 join-resolution / missing counts per gate and module
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
_STRAT = _HERE.parents[2]
if str(_STRAT) not in sys.path:
    sys.path.insert(0, str(_STRAT))
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import htf_bias as hb                      # noqa: E402
from s5quote import HORIZON_MIN, S5Quotes  # noqa: E402

RES = _HERE / "results"
RES.mkdir(exist_ok=True)
STAGE1 = _STRAT / "lab" / "results" / "xau2y_stage1"
SPLIT = pd.Timestamp("2025-12-01", tz="UTC")
MODULES = ["c03_fvg_fill", "s14_ob_mit_bias", "s95_session_breakout", "s93_fvg_scalp",
           "s97_snap_scalper_m5", "s96_h1_momentum", "s100_m3_combo", "s12_m90_fade_bias",
           "s03_ob_mitigation", "s99_mss_fvg", "s04_breaker_block", "s94_sweep_reversal",
           "s11_m90_fade_ny", "s10_90min_fade", "s98_zscore_mr_m15"]
SURVIVORS = ["c03_fvg_fill", "s14_ob_mit_bias", "s95_session_breakout", "s93_fvg_scalp"]
GATES = ["B1", "B2", "B3", "B4", "B5", "B6", "C2"]
VARIANTS = ["B1_utc", "B2_lb2", "B2_lb5", "B3_db0.05", "B3_db0.10", "B5_w2"]
COSTS = [("mid", 0.75), ("mid", 1.00), ("q", 0.45), ("q", 0.70)]
N_FLIPS = 2000
N_PERM = 500
RNG = np.random.default_rng(20260918)


# ── statistics ───────────────────────────────────────────────────────────────
def pf_of(pts: np.ndarray) -> float:
    gw = pts[pts > 0].sum()
    gl = -pts[pts <= 0].sum()
    return float(gw / gl) if gl > 0 else (float("inf") if gw > 0 else 0.0)


def class_stats(pts: np.ndarray, R: np.ndarray) -> dict:
    n = len(pts)
    if n == 0:
        return dict(n=0, pf=np.nan, pts=0.0, meanR=np.nan, wr=np.nan)
    return dict(n=int(n), pf=round(pf_of(pts), 3), pts=round(float(pts.sum()), 1),
                meanR=round(float(R.mean()), 4), wr=round(100 * float((pts > 0).mean()), 1))


def day_sums(day_id: np.ndarray, n_days: int, pts: np.ndarray, R: np.ndarray, mask: np.ndarray):
    """Per-day sums for one label class: [sumR, n, gross_win, gross_loss, pts]."""
    out = np.zeros((5, n_days))
    if mask.any():
        d = day_id[mask]
        out[0] = np.bincount(d, weights=R[mask], minlength=n_days)
        out[1] = np.bincount(d, minlength=n_days)
        p = pts[mask]
        out[2] = np.bincount(d, weights=np.where(p > 0, p, 0.0), minlength=n_days)
        out[3] = np.bincount(d, weights=np.where(p <= 0, -p, 0.0), minlength=n_days)
        out[4] = np.bincount(d, weights=p, minlength=n_days)
    return out


def sign_flip_null(day_id: np.ndarray, pts: np.ndarray, R: np.ndarray, lab: np.ndarray,
                   n_reps: int = N_FLIPS, rng=RNG) -> dict:
    """Day-block sign flip (PROTOCOL §4 C1). Returns the observed dR, its two-sided p, and
    the 2.5/97.5 % band of the admitted set's PF and points under the null."""
    al, ag = lab == "aligned", lab == "against"
    if al.sum() == 0 or ag.sum() == 0:
        return dict(dR=np.nan, p_flip=np.nan, pf_lo=np.nan, pf_hi=np.nan, pts_lo=np.nan, pts_hi=np.nan,
                    dR_lo=np.nan, dR_hi=np.nan)
    days, day_idx = np.unique(day_id, return_inverse=True)
    D = len(days)
    A = day_sums(day_idx, D, pts, R, al)
    G = day_sums(day_idx, D, pts, R, ag)
    obs = R[al].mean() - R[ag].mean()
    F = rng.random((n_reps, D)) < 0.5            # True -> that day's labels swapped
    Fm = F.astype(float)
    # numpy on Apple Accelerate raises spurious FP-flag warnings inside matmul on finite
    # inputs (verified exact vs explicit loops, 2026-09-18); silence them here only.
    _es = np.errstate(all="ignore"); _es.__enter__()
    nal = Fm @ G[1] + (1 - Fm) @ A[1]
    nag = Fm @ A[1] + (1 - Fm) @ G[1]
    ral = Fm @ G[0] + (1 - Fm) @ A[0]
    rag = Fm @ A[0] + (1 - Fm) @ G[0]
    with np.errstate(invalid="ignore", divide="ignore"):
        dR = ral / nal - rag / nag
        gw = Fm @ G[2] + (1 - Fm) @ A[2]
        gl = Fm @ G[3] + (1 - Fm) @ A[3]
        pfs = gw / gl
        ptss = Fm @ G[4] + (1 - Fm) @ A[4]
    _es.__exit__(None, None, None)
    dR = dR[np.isfinite(dR)]
    p = float((np.abs(dR) >= abs(obs)).mean()) if len(dR) else np.nan
    return dict(dR=round(float(obs), 4), p_flip=round(p, 4),
                dR_lo=round(float(np.nanpercentile(dR, 2.5)), 4), dR_hi=round(float(np.nanpercentile(dR, 97.5)), 4),
                pf_lo=round(float(np.nanpercentile(pfs[np.isfinite(pfs)], 2.5)), 3),
                pf_hi=round(float(np.nanpercentile(pfs[np.isfinite(pfs)], 97.5)), 3),
                pts_lo=round(float(np.nanpercentile(ptss, 2.5)), 1), pts_hi=round(float(np.nanpercentile(ptss, 97.5)), 1))


def month_perm_null(month_id: np.ndarray, R: np.ndarray, lab: np.ndarray,
                    n_reps: int = N_PERM, rng=RNG) -> float:
    """Trade-level permutation of labels within calendar month (PROTOCOL §4 C1b): p-value of dR."""
    al, ag = lab == "aligned", lab == "against"
    keep = al | ag
    if al.sum() == 0 or ag.sum() == 0:
        return np.nan
    Rk, mk, lk = R[keep], month_id[keep], al[keep]
    obs = Rk[lk].mean() - Rk[~lk].mean()
    cnt = 0
    order_m = np.argsort(mk, kind="stable")
    for _ in range(n_reps):
        keys = mk * 10.0 + rng.random(len(mk))     # permute within month via sort
        perm = np.argsort(keys, kind="stable")
        lp = np.empty_like(lk)
        lp[order_m] = lk[perm]
        d = Rk[lp].mean() - Rk[~lp].mean()
        if abs(d) >= abs(obs):
            cnt += 1
    return round(cnt / n_reps, 4)


# ── per module ───────────────────────────────────────────────────────────────
def load_trades(mod: str) -> pd.DataFrame:
    t = pd.read_parquet(STAGE1 / f"{mod}_c0.80.trades.parquet")
    t["entry_time"] = pd.to_datetime(t["entry_time"], utc=True)
    t["raw_mid"] = t["pts"] + 0.80          # harness charged 0.80 once at entry
    return t.reset_index(drop=True)


def cost_series(t: pd.DataFrame, model: str, cost: float):
    raw = t["raw_mid"].to_numpy(float) if model == "mid" else t["q_raw"].to_numpy(float)
    ok = ~np.isnan(raw)
    pts = raw - cost
    R = pts / t["risk"].to_numpy(float)
    return pts, R, ok


def analyse_module(mod: str, t: pd.DataFrame, L: pd.DataFrame) -> tuple[list, list]:
    rows, rows2x2 = [], []
    et = t["entry_time"]
    is_train = (et < SPLIT).to_numpy()
    day_id = L["trading_day"].to_numpy("datetime64[D]").astype("int64")
    month_id = (et.dt.year * 12 + et.dt.month).to_numpy(float)
    base_all = None
    for model, cost in COSTS:
        pts_all, R_all, ok_all = cost_series(t, model, cost)
        for split, smask in (("ALL", np.ones(len(t), bool)), ("TRAIN", is_train), ("TEST", ~is_train)):
            m = smask & ok_all
            pts, R, dd, mm = pts_all[m], R_all[m], day_id[m], month_id[m]
            base = class_stats(pts, R)
            for g in GATES + VARIANTS:
                lab = L[g].to_numpy()[m]
                al, ag = lab == "aligned", lab == "against"
                row = dict(module=mod, gate=g, model=model, cost=cost, split=split,
                           n_total=int(m.sum()), n_aligned=int(al.sum()), n_against=int(ag.sum()),
                           n_none=int((lab == "none").sum()), n_missing=int((lab == "missing").sum()),
                           admitted_frac=round(float(al.mean()), 3) if m.sum() else np.nan)
                for k, v in base.items():
                    row[f"base_{k}"] = v
                for k, v in class_stats(pts[al], R[al]).items():
                    row[f"al_{k}"] = v
                for k, v in class_stats(pts[ag], R[ag]).items():
                    row[f"ag_{k}"] = v
                row.update(sign_flip_null(dd, pts, R, lab))
                stress = (model == "mid" and cost == 1.00) or (model == "q" and cost == 0.70)
                row["p_perm"] = month_perm_null(mm, R, lab) if (stress and g in GATES) else np.nan
                # sanity floors (PROTOCOL §6)
                flags = []
                for pre in ("al", "ag"):
                    if row[f"{pre}_n"] >= 100 and (row[f"{pre}_wr"] < 10 or row[f"{pre}_wr"] > 90
                                                   or abs(row[f"{pre}_meanR"]) > 1.0 or row[f"{pre}_pf"] > 3):
                        flags.append(pre)
                row["sanity_flag"] = ",".join(flags)
                rows.append(row)
                # 2x2 against the dumb filter
                if g in GATES and g != "C2" and stress:
                    c2 = L["C2"].to_numpy()[m]
                    for cls in ("aligned", "against"):
                        cm = c2 == cls
                        r2 = dict(module=mod, gate=g, model=model, cost=cost, split=split, c2_class=cls,
                                  n=int(cm.sum()), n_al=int((cm & al).sum()), n_ag=int((cm & ag).sum()))
                        r2.update({f"in_{k}": v for k, v in class_stats(pts[cm], R[cm]).items()})
                        r2.update({f"al_{k}": v for k, v in class_stats(pts[cm & al], R[cm & al]).items()})
                        r2.update({f"ag_{k}": v for k, v in class_stats(pts[cm & ag], R[cm & ag]).items()})
                        sub = sign_flip_null(dd[cm], pts[cm], R[cm], lab[cm], n_reps=1000)
                        r2.update(dR=sub["dR"], p_flip=sub["p_flip"])
                        rows2x2.append(r2)
    return rows, rows2x2


# ── pooled book test ─────────────────────────────────────────────────────────
def pooled(all_t: dict, all_L: dict) -> list:
    out = []
    subsets = {"all15": MODULES, "survivors": SURVIVORS,
               "non_survivors": [m for m in MODULES if m not in SURVIVORS]}
    for name, mods in subsets.items():
        T = pd.concat([all_t[m].assign(module=m) for m in mods], ignore_index=True)
        Lb = pd.concat([all_L[m] for m in mods], ignore_index=True)
        is_train = (T["entry_time"] < SPLIT).to_numpy()
        day_id = Lb["trading_day"].to_numpy("datetime64[D]").astype("int64")
        for model, cost in COSTS:
            pts_all, R_all, ok_all = cost_series(T, model, cost)
            for split, smask in (("ALL", np.ones(len(T), bool)), ("TRAIN", is_train), ("TEST", ~is_train)):
                m = smask & ok_all
                pts, R, dd = pts_all[m], R_all[m], day_id[m]
                for g in GATES:
                    lab = Lb[g].to_numpy()[m]
                    al, ag = lab == "aligned", lab == "against"
                    row = dict(subset=name, gate=g, model=model, cost=cost, split=split,
                               n_total=int(m.sum()), n_aligned=int(al.sum()), n_against=int(ag.sum()),
                               admitted_frac=round(float(al.mean()), 3))
                    row.update({f"base_{k}": v for k, v in class_stats(pts, R).items()})
                    row.update({f"al_{k}": v for k, v in class_stats(pts[al], R[al]).items()})
                    row.update({f"ag_{k}": v for k, v in class_stats(pts[ag], R[ag]).items()})
                    row.update(sign_flip_null(dd, pts, R, lab))
                    # conditional on the dumb filter (both classes)
                    c2 = Lb["C2"].to_numpy()[m]
                    for cls in ("aligned", "against"):
                        cm = c2 == cls
                        if g == "C2":
                            row[f"dR_in_c2_{cls}"] = np.nan; row[f"p_in_c2_{cls}"] = np.nan
                            continue
                        sub = sign_flip_null(dd[cm], pts[cm], R[cm], lab[cm], n_reps=1000)
                        row[f"dR_in_c2_{cls}"] = sub["dR"]; row[f"p_in_c2_{cls}"] = sub["p_flip"]
                    out.append(row)
    return out


# ── bars for the survivors' gated arms ───────────────────────────────────────
def gold_monthly() -> pd.Series:
    d = pd.read_parquet(_STRAT / "backtest" / "results" / "bars_cache_2y" / "is_XAU_USD_1d.parquet")
    t = pd.to_datetime(d["time"], utc=True).dt.tz_convert(None)
    d = d.assign(time=t).sort_values("time").set_index("time")
    m = d["close"].resample("ME").agg(["first", "last"])
    g = (m["last"] / m["first"] - 1.0) * 100.0
    g.index = g.index.strftime("%Y-%m")
    return g.rename("gold_pct")


def bars_for_arm(t: pd.DataFrame, admit: np.ndarray, gold: pd.Series, base_cost=0.45, stress_cost=0.70,
                 model="q") -> dict:
    et = t["entry_time"]
    is_train = (et < SPLIT).to_numpy()
    out = {}
    for tag, cost in (("base", base_cost), ("stress", stress_cost)):
        pts, R, ok = cost_series(t, model, cost)
        m = admit & ok
        tr, te = m & is_train, m & ~is_train
        out[f"{tag}_n"] = int(m.sum()); out[f"{tag}_pf"] = round(pf_of(pts[m]), 3); out[f"{tag}_pts"] = round(float(pts[m].sum()), 1)
        out[f"{tag}_train_n"] = int(tr.sum()); out[f"{tag}_train_pf"] = round(pf_of(pts[tr]), 3); out[f"{tag}_train_pts"] = round(float(pts[tr].sum()), 1)
        out[f"{tag}_test_n"] = int(te.sum()); out[f"{tag}_test_pf"] = round(pf_of(pts[te]), 3); out[f"{tag}_test_pts"] = round(float(pts[te].sum()), 1)
        if tag == "base":
            mon = pd.Series(pts[te]).groupby(et[te].dt.strftime("%Y-%m").to_numpy()).sum()
            net = float(mon.sum())
            out["test_months"] = int(len(mon))
            out["test_pos_month_share"] = round(float((mon > 0).mean()), 2) if len(mon) else 0.0
            out["test_max_month_share"] = round(float(mon.max() / net), 2) if net > 0 else np.inf
            fm = pd.Series(pts[m]).groupby(et[m].dt.strftime("%Y-%m").to_numpy()).sum().rename("pts")
            j = pd.concat([fm, gold], axis=1).dropna()
            up, dn = j[j.gold_pct > 0], j[j.gold_pct <= 0]
            corr = float(np.corrcoef(j.pts, j.gold_pct)[0, 1]) if len(j) >= 6 and j.pts.std() > 0 else 0.0
            out["gold_corr"] = round(corr, 2); out["up_pts"] = round(float(up.pts.sum()), 1); out["dn_pts"] = round(float(dn.pts.sum()), 1)
            out["bar5_regime"] = bool((up.pts.sum() > 0 and dn.pts.sum() > 0) or abs(corr) < 0.4)
    out["bar1_n"] = out["base_test_n"] >= 40
    out["bar2_base"] = out["base_test_pf"] > 1.0
    out["bar2_stress"] = out["stress_test_pf"] > 1.0
    out["bar3_train"] = out["base_train_pf"] > 0.9
    out["bar4_monthly"] = out["test_pos_month_share"] >= 0.55 and out["test_max_month_share"] <= 0.5
    return out


def survivor_bars(all_t: dict, all_L: dict) -> list:
    gold = gold_monthly()
    plateau_axes = {"B2": ["B2_lb2", "B2", "B2_lb5"], "B3": ["B3", "B3_db0.05", "B3_db0.10"],
                    "B5": ["B5", "B5_w2"], "B1": ["B1", "B1_utc"]}
    rows = []
    for mod in SURVIVORS:
        t, L = all_t[mod], all_L[mod]
        for model, base_c, stress_c in (("q", 0.45, 0.70), ("mid", 0.75, 1.00)):
            inc = bars_for_arm(t, np.ones(len(t), bool), gold, base_c, stress_c, model)
            rows.append(dict(module=mod, arm="incumbent", model=model, **inc))
            for g in GATES:
                adm = (L[g] == "aligned").to_numpy()
                b = bars_for_arm(t, adm, gold, base_c, stress_c, model)
                b["bar7_pf"] = b["stress_test_pf"] > inc["stress_test_pf"]
                b["bar7_pts"] = b["stress_test_pts"] > inc["stress_test_pts"]
                b["bar7"] = b["bar7_pf"] and b["bar7_pts"]
                # bar 6 plateau on the declared axis: TEST PF at stress across the axis
                axis = plateau_axes.get(g)
                if axis:
                    seq = [bars_for_arm(t, (L[a] == "aligned").to_numpy(), gold, base_c, stress_c, model)["stress_test_pf"] for a in axis]
                    b["plateau_seq"] = "/".join(f"{v:.3f}" for v in seq)
                    if len(seq) == 3:
                        pidx = axis.index(g)
                        if pidx == 1:
                            spike = (seq[1] > max(seq[0], seq[2]) + 0.05) or (seq[1] < min(seq[0], seq[2]) - 0.05)
                        else:
                            mono = (seq[0] <= seq[1] <= seq[2]) or (seq[0] >= seq[1] >= seq[2])
                            spike = not (mono or max(seq) - min(seq) <= 0.05)
                        b["bar6_plateau"] = not spike
                    else:
                        b["bar6_plateau"] = abs(seq[0] - seq[1]) <= 0.10   # binary sensitivity
                else:
                    b["plateau_seq"] = ""; b["bar6_plateau"] = None
                bars = ["bar1_n", "bar2_base", "bar2_stress", "bar3_train", "bar4_monthly", "bar5_regime"]
                b["bars_1_5"] = int(sum(bool(b[k]) for k in bars))
                b["verdict"] = ("PASS" if all(bool(b[k]) for k in bars) and b["bar7"] and b["bar6_plateau"] is not False
                                else ("quality-up/profit-down" if all(bool(b[k]) for k in bars) and b["bar7_pf"] and not b["bar7_pts"]
                                      else "FAIL"))
                rows.append(dict(module=mod, arm=g, model=model, **b))
    return rows


# ── main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    t0 = time.time()
    m1 = hb.load_m1()
    daily = hb.ny_daily(m1)
    q = S5Quotes()
    print(f"loaded M1 {len(m1)} rows, daily {len(daily)}, S5 {len(q.s5)} rows in {time.time()-t0:.0f}s")
    all_t, all_L, info, cells, cells2x2 = {}, {}, {}, [], []
    for mod in MODULES:
        t1 = time.time()
        t = load_trades(mod)
        L, inf = hb.build_all_labels(t, m1=m1, daily=daily)
        h = HORIZON_MIN[mod]
        cov = q.coverage_mask(t, h)
        qres = q.resolve_frame(t[cov], h)
        t["q_raw"] = np.nan
        t.loc[cov, "q_raw"] = qres["q_raw"].to_numpy()
        t["q_outcome"] = None
        t.loc[cov, "q_outcome"] = qres["q_outcome"].to_numpy()
        info[mod] = {k: {kk: int(vv) for kk, vv in v.items() if not isinstance(vv, np.ndarray)} for k, v in inf.items()}
        info[mod]["s5_covered"] = int(cov.sum()); info[mod]["n"] = int(len(t))
        pd.concat([t, L], axis=1).to_parquet(RES / f"labels_{mod}.parquet", index=False)
        r, r2 = analyse_module(mod, t, L)
        cells += r; cells2x2 += r2
        all_t[mod], all_L[mod] = t, L
        print(f"{mod:24s} n={len(t):5d} s5={cov.sum():5d} labelled+scored in {time.time()-t1:.0f}s")
    pd.DataFrame(cells).to_csv(RES / "cells.csv", index=False)
    pd.DataFrame(cells2x2).to_csv(RES / "regime2x2.csv", index=False)
    pd.DataFrame(pooled(all_t, all_L)).to_csv(RES / "pooled.csv", index=False)
    pd.DataFrame(survivor_bars(all_t, all_L)).to_csv(RES / "survivor_bars.csv", index=False)
    (RES / "info.json").write_text(json.dumps(info, indent=1))
    print(f"done in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()


# ── D1 (PROTOCOL deviations): module-demeaned pooled test ────────────────────
def pooled_demeaned(all_t: dict, all_L: dict) -> list:
    out = []
    subsets = {"all15": MODULES, "survivors": SURVIVORS,
               "non_survivors": [m for m in MODULES if m not in SURVIVORS]}
    for name, mods in subsets.items():
        T = pd.concat([all_t[m].assign(module=m) for m in mods], ignore_index=True)
        Lb = pd.concat([all_L[m] for m in mods], ignore_index=True)
        is_train = (T["entry_time"] < SPLIT).to_numpy()
        day_id = Lb["trading_day"].to_numpy("datetime64[D]").astype("int64")
        modv = T["module"].to_numpy()
        for model, cost in COSTS:
            pts_all, R_all, ok_all = cost_series(T, model, cost)
            for split, smask in (("ALL", np.ones(len(T), bool)), ("TRAIN", is_train), ("TEST", ~is_train)):
                m = smask & ok_all
                R = R_all[m].copy(); pts = pts_all[m]; dd = day_id[m]; mv = modv[m]
                # demean R by module within this split/cost
                Rd = R.copy()
                for mod in np.unique(mv):
                    sel = mv == mod
                    Rd[sel] = R[sel] - R[sel].mean()
                for g in GATES:
                    lab = Lb[g].to_numpy()[m]
                    al, ag = lab == "aligned", lab == "against"
                    row = dict(subset=name, gate=g, model=model, cost=cost, split=split,
                               n_aligned=int(al.sum()), n_against=int(ag.sum()))
                    s = sign_flip_null(dd, pts, Rd, lab)
                    row.update(dR_demeaned=s["dR"], p_flip=s["p_flip"], dR_lo=s["dR_lo"], dR_hi=s["dR_hi"])
                    c2 = Lb["C2"].to_numpy()[m]
                    for cls in ("aligned", "against"):
                        cm = c2 == cls
                        if g == "C2":
                            row[f"dR_in_c2_{cls}"] = np.nan; row[f"p_in_c2_{cls}"] = np.nan; continue
                        sub = sign_flip_null(dd[cm], pts[cm], Rd[cm], lab[cm], n_reps=1000)
                        row[f"dR_in_c2_{cls}"] = sub["dR"]; row[f"p_in_c2_{cls}"] = sub["p_flip"]
                    out.append(row)
    return out


def main_d1() -> None:
    all_t, all_L = {}, {}
    for mod in MODULES:
        d = pd.read_parquet(RES / f"labels_{mod}.parquet")
        d["entry_time"] = pd.to_datetime(d["entry_time"], utc=True)
        lab_cols = GATES + VARIANTS + ["trading_day"]
        all_L[mod] = d[lab_cols].copy()
        all_t[mod] = d.drop(columns=lab_cols)
    pd.DataFrame(pooled_demeaned(all_t, all_L)).to_csv(RES / "pooled_demeaned.csv", index=False)
    print("wrote pooled_demeaned.csv")
