"""run_qb.py -- Question B: sweep-and-reclaim entry vs two controls, xau2y bars.

Reads results/events.parquet. Writes results/campaign/<arm>.json + .trades.parquet (real, C-RAND
replicates, C-TOUCH), results/campaign_opp/ (secondary target), results/qb_*.csv.
    ../.venv/bin/python lab/research_s5/sweeps/run_qb.py | tee lab/research_s5/sweeps/results/run_qb.log
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sweeps_lib as sl
from lab.tools.campaign_score import score_campaign   # noqa: E402  read-only reuse
from lab import s5exit                                # noqa: E402  read-only reuse (cross-check)

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
CAMP, CAMP_OPP = OUT / "campaign", OUT / "campaign_opp"
CACHE2Y = sl._STRAT / "backtest" / "results" / "bars_cache_2y"
COSTS = (0.45, 0.80)
SPLIT = "2025-12-01"


def write_arm(d: Path, label: str, tr: pd.DataFrame, cost: float, meta: dict):
    d.mkdir(parents=True, exist_ok=True)
    t = tr.copy()
    t["pts"] = t.raw - cost
    t["entry_time"] = pd.to_datetime(t.entry_time, utc=True)
    t["exit_time"] = pd.to_datetime(t.exit_time, utc=True)
    lab = f"{label}_c{cost:.2f}"
    row = dict(label=lab, strategy=label, status="ok", cost=cost, n=int(len(t)),
               pts=round(float(t.pts.sum()), 1), pf=round(sl.pf(t.pts.to_numpy()), 3) if len(t) else None, **meta)
    (d / f"{lab}.json").write_text(json.dumps(row, indent=1, default=str))
    t.to_parquet(d / f"{lab}.trades.parquet", index=False)
    return t


def split_summ(t: pd.DataFrame, cost: float) -> dict:
    v = t.raw.to_numpy() - cost
    tr = t.entry_time.to_numpy("datetime64[s]") < sl.SPLIT
    a, b = sl.summarise(v[tr]), sl.summarise(v[~tr])
    return {f"train_{k}": x for k, x in a.items()} | {f"test_{k}": x for k, x in b.items()}


class _T:  # minimal trade object for lab.s5exit.resolve
    def __init__(self, r):
        self.entry_time = pd.Timestamp(r.entry_time).tz_localize("UTC"); self.side = r.side
        self.sl = r.sl; self.tp = r.tp; self.entry_px = r.entry_px


def cross_check(s: sl.S5, s5df: pd.DataFrame, t5, tr: pd.DataFrame, n=300):
    """My resolver vs lab.s5exit.resolve(mode='quote_s5', start_offset_s=0, max_hold=240)."""
    sub = tr.sample(min(n, len(tr)), random_state=0)
    agree_oc = agree_px = 0; time_mid_diff = 0
    for r in sub.itertuples():
        res = s5exit.resolve(_T(r), s5df, t5, "quote_s5", 0.0, sl.HOLD_S / 60, start_offset_s=0)
        oc, px = res
        agree_oc += (oc == r.outcome)
        if oc == "TIME" and r.outcome == "TIME":
            time_mid_diff += 1     # documented: s5exit time-exits on mid close, this study on the quote
            agree_px += 1
        else:
            agree_px += abs(px - r.exit_px) < 1e-9
    print(f"  cross-check vs lab.s5exit.resolve on {len(sub)} trades: outcome agree {agree_oc}/{len(sub)}, "
          f"exit px agree {agree_px}/{len(sub)} (TIME exits compared on outcome only: {time_mid_diff})")
    assert agree_oc == len(sub) and agree_px == len(sub), "resolver disagreement -- harness fault"


def main():
    t0 = time.time()
    s = sl.load()
    ev = pd.read_parquet(OUT / "events.parquet")
    pools = sl._hour_pools(s)
    # entry-index assertion: signal bar j, entry bar j+1
    sw_all = ev[ev.time_beyond <= 300]
    dt_gap = s.t[sw_all.j.to_numpy() + 1] - s.t[sw_all.j.to_numpy()]
    print(f"entry bar is the bar after the reclaim bar: gap p50 {np.median(dt_gap):.0f}s, max {dt_gap.max():.0f}s, "
          f"share exactly 5s {np.mean(dt_gap==5)*100:.1f}%")

    summary, ctrl_rows, edge_rows, speed_rows, skip_rows = [], [], [], [], []
    real_trades = {}
    s5df = None
    for lt in sl.LEVEL_TYPES:
        e_lt = ev[ev.level_type == lt]
        # C-TOUCH: every cross (sweep or break), fade at the touch, stop beyond the cross bar's extreme
        touch_ev = e_lt.sort_values("i")
        stop_touch = lambda r: (s.h[int(r.i)] + sl.STOP_BUF) if r.side == "high" else (s.l[int(r.i)] - sl.STOP_BUF)
        tt, sk = sl.run_arm(s, touch_ev, "i", stop_touch, "2R")
        skip_rows.append(dict(arm=f"touch_{lt}", n_events=len(touch_ev), n_trades=len(tt), **sk))
        for cost in COSTS:
            write_arm(CAMP, f"touch_{lt}", tt, cost, dict(level_type=lt, kind="C-TOUCH"))
        for T in sl.T_GRID:
            sw = e_lt[e_lt.time_beyond <= T].sort_values("j")
            stop_real = lambda r: (r.extreme + sl.STOP_BUF) if r.side == "high" else (r.extreme - sl.STOP_BUF)
            tr, sk = sl.run_arm(s, sw, "j", stop_real, "2R")
            real_trades[(lt, T)] = tr
            skip_rows.append(dict(arm=f"sweep_{lt}_T{T}", n_events=len(sw), n_trades=len(tr), **sk))
            tro, sko = sl.run_arm(s, sw, "j", stop_real, "opp")
            skip_rows.append(dict(arm=f"sweepopp_{lt}_T{T}", n_events=len(sw), n_trades=len(tro), **sko))
            if s5df is None and len(tr) >= 50:
                s5df = s5exit.load_s5(); t5 = s5df["time"].dt.tz_convert(None).to_numpy("datetime64[ns]")
                cross_check(s, s5df, t5, tr)
            rands = [sl.random_control(s, tr, pools, seed=k) for k in range(5)]
            for cost in COSTS:
                write_arm(CAMP, f"sweep_{lt}_T{T}", tr, cost, dict(level_type=lt, T=T, kind="REAL"))
                write_arm(CAMP_OPP, f"sweepopp_{lt}_T{T}", tro, cost, dict(level_type=lt, T=T, kind="REAL-opp"))
                for k, rc in enumerate(rands):
                    write_arm(CAMP, f"rand_{lt}_T{T}_r{k}", rc, cost, dict(level_type=lt, T=T, kind="C-RAND", seed=k))
                # per-split summaries of real, C-RAND (mean + range over replicates), C-TOUCH
                real = split_summ(tr, cost); opp = split_summ(tro, cost)
                rr = [split_summ(rc, cost) for rc in rands]
                tch = split_summ(tt, cost)
                row = dict(level_type=lt, T=T, cost=cost, n_events=len(sw),
                           real_train_n=real["train_n"], real_train_pf=real["train_pf"], real_train_pts=real["train_pts"], real_train_wr=real["train_wr"],
                           real_test_n=real["test_n"], real_test_pf=real["test_pf"], real_test_pts=real["test_pts"], real_test_wr=real["test_wr"],
                           rand_train_pf_mean=round(float(np.mean([x["train_pf"] for x in rr])), 3), rand_train_pf_min=min(x["train_pf"] for x in rr), rand_train_pf_max=max(x["train_pf"] for x in rr),
                           rand_test_pf_mean=round(float(np.mean([x["test_pf"] for x in rr])), 3), rand_test_pf_min=min(x["test_pf"] for x in rr), rand_test_pf_max=max(x["test_pf"] for x in rr),
                           rand_test_pts_mean=round(float(np.mean([x["test_pts"] for x in rr])), 1),
                           touch_train_n=tch["train_n"], touch_train_pf=tch["train_pf"], touch_test_n=tch["test_n"], touch_test_pf=tch["test_pf"], touch_test_pts=tch["test_pts"],
                           opp_train_n=opp["train_n"], opp_train_pf=opp["train_pf"], opp_test_n=opp["test_n"], opp_test_pf=opp["test_pf"], opp_test_pts=opp["test_pts"])
                summary.append(row)
                # edge beyond controls: mean pts/trade difference with bootstrap CI, per split
                for split, m in (("TRAIN", "train"), ("TEST", "test")):
                    is_tr = tr.entry_time.to_numpy("datetime64[s]") < sl.SPLIT
                    x = (tr.raw.to_numpy() - cost)[is_tr if split == "TRAIN" else ~is_tr]
                    rc_all = pd.concat(rands, ignore_index=True)
                    is_rc = rc_all.entry_time.to_numpy("datetime64[s]") < sl.SPLIT
                    y = (rc_all.raw.to_numpy() - cost)[is_rc if split == "TRAIN" else ~is_rc]
                    is_tt = tt.entry_time.to_numpy("datetime64[s]") < sl.SPLIT
                    z = (tt.raw.to_numpy() - cost)[is_tt if split == "TRAIN" else ~is_tt]
                    d1, lo1, hi1 = sl.boot_diff_ci(x, y); d2, lo2, hi2 = sl.boot_diff_ci(x, z)
                    edge_rows.append(dict(level_type=lt, T=T, cost=cost, split=split, n_real=len(x), real_mean=round(float(x.mean()), 3) if len(x) else None,
                                          rand_mean=round(float(y.mean()), 3) if len(y) else None, edge_vs_rand=round(d1, 3), ci_lo=round(lo1, 3), ci_hi=round(hi1, 3),
                                          touch_mean=round(float(z.mean()), 3) if len(z) else None, edge_vs_touch=round(d2, 3), ci2_lo=round(lo2, 3), ci2_hi=round(hi2, 3)))
            # reclaim-speed buckets on the T=300 arm, both costs, per split
            if T == 300 and len(tr):
                b = pd.cut(tr.time_beyond, [-1, 0, 30, 120, 300], labels=["0s", "5-30s", "35-120s", "125-300s"])
                is_tr = tr.entry_time.to_numpy("datetime64[s]") < sl.SPLIT
                for cost in (0.0,) + COSTS:
                    for split, m in (("TRAIN", is_tr), ("TEST", ~is_tr)):
                        for bk, g in tr[m].groupby(b[m], observed=True):
                            v = g.raw.to_numpy() - cost
                            speed_rows.append(dict(level_type=lt, cost=cost, split=split, speed=str(bk), **sl.summarise(v), risk_p50=round(float(g.risk.median()), 2)))
        print(f"  {lt} done  {time.time()-t0:.0f}s")

    summ = pd.DataFrame(summary); summ.to_csv(OUT / "qb_summary.csv", index=False)
    edge = pd.DataFrame(edge_rows); edge.to_csv(OUT / "qb_edge_vs_controls.csv", index=False)
    speed = pd.DataFrame(speed_rows); speed.to_csv(OUT / "qb_speed_buckets.csv", index=False)
    skips = pd.DataFrame(skip_rows); skips.to_csv(OUT / "qb_skips.csv", index=False)
    with pd.option_context("display.width", 260, "display.max_columns", 60):
        print("\n=== skip / coverage counts per arm ===\n" + skips.to_string(index=False))
        print("\n=== primary arms (2R target): real vs C-RAND (5 replicates) vs C-TOUCH, per split and cost ===")
        cols = ["level_type", "T", "cost", "n_events", "real_train_n", "real_train_pf", "real_train_pts", "real_train_wr", "real_test_n", "real_test_pf", "real_test_pts", "real_test_wr",
                "rand_train_pf_mean", "rand_test_pf_mean", "rand_test_pf_min", "rand_test_pf_max", "rand_test_pts_mean", "touch_train_pf", "touch_test_n", "touch_test_pf", "touch_test_pts"]
        print(summ[cols].to_string(index=False))
        print("\n=== secondary target (opposite side of the swept range), exploratory ===")
        print(summ[["level_type", "T", "cost", "opp_train_n", "opp_train_pf", "opp_test_n", "opp_test_pf", "opp_test_pts"]].to_string(index=False))
        print("\n=== edge beyond controls: mean pts/trade, real minus control, bootstrap 95% CI ===")
        print(edge.to_string(index=False))
        print("\n=== T=300 arms by reclaim-speed bucket (cost 0 = gross at quote-level, then 0.45 / 0.80) ===")
        print(speed.to_string(index=False))

    # pre-declared selection on TRAIN: best TRAIN PF at 0.80 among arms with TRAIN n >= 80
    s80 = summ[(summ.cost == 0.80) & (summ.real_train_n >= 80)].sort_values("real_train_pf", ascending=False)
    print("\n=== TRAIN ranking at 0.80 (selection basis; TRAIN n >= 80) ===")
    print(s80[["level_type", "T", "real_train_n", "real_train_pf", "real_train_pts", "rand_train_pf_mean", "touch_train_pf"]].to_string(index=False))
    pick = s80.iloc[0]
    print(f"\nPRE-DECLARED TEST CANDIDATE: sweep_{pick.level_type}_T{int(pick['T'])}  (TRAIN PF@0.80 = {pick.real_train_pf})")

    print("\n=== xau2y bars (lab.tools.campaign_score.score_campaign, split 2025-12-01, cache bars_cache_2y) -- primary campaign ===")
    sc = score_campaign(CAMP, CACHE2Y, SPLIT)
    sc.to_csv(OUT / "qb_bars_campaign.csv", index=False)
    cols = ["label", "verdict", "bars_passed", "test_n", "test_pf", "test_pf_stress", "test_pts", "train_n", "train_pf", "test_pos_month_share", "test_max_month_share", "gold_corr", "up_months_pts", "dn_months_pts"]
    with pd.option_context("display.width", 260, "display.max_rows", 500):
        main_rows = sc[sc.label.str.startswith("sweep_") & sc.label.str.endswith("_c0.45")]
        print(main_rows[cols].to_string(index=False))
        print("\n-- controls (C-TOUCH per level type; C-RAND replicate r0 only, others in the csv) --")
        ctl = sc[(sc.label.str.startswith("touch_") | sc.label.str.contains("_r0_")) & sc.label.str.endswith("_c0.45")]
        print(ctl[cols].to_string(index=False))
        print("\n-- secondary target campaign --")
        sc2 = score_campaign(CAMP_OPP, CACHE2Y, SPLIT); sc2.to_csv(OUT / "qb_bars_campaign_opp.csv", index=False)
        print(sc2[sc2.label.str.endswith("_c0.45")][cols].to_string(index=False))
    cand = f"sweep_{pick.level_type}_T{int(pick['T'])}_c0.45"
    print(f"\nVERDICT for the pre-declared candidate {cand}:")
    print(sc[sc.label == cand][cols + ["bar1_n", "bar2_base", "bar2_stress", "bar3_train", "bar4_monthly", "bar5_regime"]].T.to_string())
    print(f"\ntotal {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
