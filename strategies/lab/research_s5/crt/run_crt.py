"""run_crt.py -- run the pre-registered CRT study (PROTOCOL.md) and write results/.

    cd KronosStrategies/strategies
    ../.venv/bin/python -m lab.research_s5.crt.run_crt | tee lab/research_s5/crt/results/run_output.txt
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

from lab.tools.qa_s5_cache import load_s5                     # noqa: E402
from lab.tools.campaign_score import score_campaign            # noqa: E402
from lab.research_s5.crt import crt_lib as L                   # noqa: E402

CACHE = _STRAT / "backtest" / "results" / "bars_cache_2y"
RES = _HERE / "results"
ARMS = RES / "arms"
WIN_LO = np.datetime64("2024-09-01T00:00:00")
SPLIT = "2025-12-01"
CUT = np.datetime64(SPLIT + "T00:00:00")
COSTS = (0.45, 0.80)
TRADE_GRIDS = ["H1_UTC", "H4_UTC", "H4_NY"]
K_CTL = 20

pd.set_option("display.width", 220, "display.max_columns", 40, "display.max_rows", 200)


def hdr(s):
    print("\n" + "=" * 100 + f"\n{s}\n" + "=" * 100)


def crosscheck(built: pd.DataFrame, cached_file: Path, name: str) -> float:
    c = pd.read_parquet(cached_file)
    c["time"] = pd.to_datetime(c["time"], utc=True).dt.tz_localize(None)
    b = built.set_index("start_utc")[["h", "l"]]
    j = c.set_index("time")[["high", "low"]].join(b, how="inner")
    agree = (np.isclose(j.h, j.high, atol=0.0051) & np.isclose(j.l, j.low, atol=0.0051)).mean() * 100
    print(f"cross-check {name}: built {len(built)} vs cached {len(c)}; overlap {len(j)}; h/l agree {agree:.3f}%")
    return agree


def block(df: pd.DataFrame, col: str, label: str) -> dict:
    s = L.summ(df, col)
    s["label"] = label
    return s


def split_tables(tr: pd.DataFrame, tag: str, col_prefix: str = "pts") -> pd.DataFrame:
    rows = []
    tr_train, tr_test = tr[tr.entry_time < CUT], tr[tr.entry_time >= CUT]
    for cst in COSTS:
        col = f"{col_prefix}_c{cst:.2f}"
        for nm, d in (("ALL", tr), ("TRAIN", tr_train), ("TEST", tr_test)):
            rows.append(dict(arm=tag, cost=cst, split=nm, **L.summ(d, col)))
    return pd.DataFrame(rows)


def by_group(tr: pd.DataFrame, key: str, tag: str, cost: float = 0.45) -> pd.DataFrame:
    col = f"pts_c{cost:.2f}"
    rows = []
    for g, d in tr.groupby(key):
        rows.append(dict(arm=tag, **{key: g}, **L.summ(d, col)))
    return pd.DataFrame(rows)


def write_arm(label: str, tr: pd.DataFrame, cost: float):
    col = f"pts_c{cost:.2f}"
    t = tr[["entry_time", "side", "entry_px", "sl", "tp", "risk", col]].rename(columns={col: "pts"})
    t["entry_time"] = pd.to_datetime(t["entry_time"], utc=True)
    t.to_parquet(ARMS / f"{label}.trades.parquet", index=False)
    (ARMS / f"{label}.json").write_text(json.dumps(dict(
        label=label, strategy="crt", status="ok", cost=cost, n=int(len(t)),
        pf=L.pf(t.pts.values) if len(t) else 0.0, pts=float(t.pts.sum()))))


def main():
    t0 = time.time()
    RES.mkdir(exist_ok=True); ARMS.mkdir(exist_ok=True)
    for f in ARMS.glob("*"):
        f.unlink()

    hdr("DATA")
    m1df = pd.read_parquet(CACHE / "is_XAU_USD_1m.parquet")
    m1df["time"] = pd.to_datetime(m1df["time"], utc=True)
    m1df = m1df.sort_values("time").reset_index(drop=True)
    print(f"M1: {len(m1df):,} bars {m1df.time.min()} -> {m1df.time.max()}")
    s5df = load_s5()
    print(f"S5: {len(s5df):,} bars {s5df.time.min()} -> {s5df.time.max()}  load {time.time()-t0:.0f}s")
    s5 = L.s5_arrays(s5df)
    m1 = L.m1_arrays(m1df)
    m1_end = m1.t[-1]
    del s5df

    candles = {g: L.build_candles(m1df, g) for g in L.GRIDS}
    for g, cd in candles.items():
        print(f"{g}: {len(cd)} candles; complete {int(cd.complete.sum())}; slots {sorted(cd.slot.unique().tolist())}")
    a1 = crosscheck(candles["H1_UTC"], CACHE / "is_XAU_USD_1h.parquet", "H1_UTC")
    a4 = crosscheck(candles["H4_UTC"], CACHE / "is_XAU_USD_4h.parquet", "H4_UTC")
    ad = crosscheck(candles["D1_UTC"], CACHE / "is_XAU_USD_1d.parquet", "D1_UTC")
    if min(a1, a4, ad) < 99.5:
        print("CROSS-CHECK FAILED -- stopping per protocol"); return 1
    ny = candles["H4_NY"]
    print("H4_NY minutes per slot (median):", ny.groupby("slot").n.median().to_dict())
    print("H4_NY first-M1 delay after scheduled start (median s) per slot:",
          ny.assign(d=(ny.first_utc - ny.start_utc).dt.total_seconds()).groupby("slot").d.median().to_dict())

    all_split, all_year, all_side, all_slot, all_flip, all_ctl, all_wick = [], [], [], [], [], [], []
    trades = {}
    for g in TRADE_GRIDS:
        hdr(f"GRID {g}")
        cd = candles[g]
        atr = L.wilder_atr(cd)
        ev = L.detect_events(cd, atr)
        ev = ev[(ev.c3_start >= WIN_LO) & (ev.c3_end <= m1_end + np.timedelta64(1, "m"))].reset_index(drop=True)
        print(f"C2 sweep events in window: {len(ev)}  (sweep_up→SELL {int(ev.sweep_up.sum())}, sweep_dn→BUY {int(ev.sweep_dn.sum())}, both {int(ev.both.sum())})")
        res = L.resolve_events(ev, s5, m1, COSTS)
        print("skips:", res.skip.value_counts(dropna=False).to_dict())
        tr = res[res.skip.isna()].copy().reset_index(drop=True)
        # bias filter from the parent grid
        pe, pu = L.bias_series(candles[L.PARENT[g]])
        b = L.bias_at(pe, pu, tr.c3_start.values.astype("datetime64[ns]"))
        tr["bias_up"] = b
        tr["bias_ok"] = ((tr.side == "BUY") & (b == 1)) | ((tr.side == "SELL") & (b == 0))
        tr["year"] = pd.to_datetime(tr.entry_time).dt.year
        tr["split"] = np.where(tr.entry_time < CUT, "TRAIN", "TEST")
        tr["grid"] = g
        trades[g] = tr
        tr.to_parquet(RES / f"trades_{g}.parquet", index=False)
        print(f"fillable trades: {len(tr)}  TRAIN {int((tr.split=='TRAIN').sum())}  TEST {int((tr.split=='TEST').sum())}"
              f"  bias-filter keeps {int(tr.bias_ok.sum())}  median fill delay {tr.fill_delay_s.median():.0f}s"
              f"  median risk {tr.risk.median():.2f} pts  median reward/risk {(tr.reward/tr.risk).median():.2f}")
        print("outcomes quote_s5:", tr.q_outcome.value_counts().to_dict())
        print(f"fills later than 60 s after the scheduled open: {int((tr.fill_delay_s > 60).sum())}"
              f"  mean spread paid (quote raw vs mid-S5 raw, pts): {float((tr.ms_raw - tr.q_raw).mean()):.3f}"
              f"  TP-rate honest {100*float((tr.q_outcome=='TP').mean()):.1f}%  SL-rate {100*float((tr.q_outcome=='SL').mean()):.1f}%  TIME-rate {100*float((tr.q_outcome=='TIME').mean()):.1f}%")

        for fl, sub, tagf in (("nofilt", tr, "nofilt"), ("bias", tr[tr.bias_ok], "bias")):
            tag = f"crt_{g}_{tagf}"
            st = split_tables(sub, tag); all_split.append(st)
            print(f"\n-- {tag} (quote_s5) --\n" + st.to_string(index=False))
            for cst in COSTS:
                write_arm(f"{tag}_c{cst:.2f}", sub, cst)
            all_year.append(by_group(sub, "year", tag))
            all_side.append(by_group(sub, "side", tag))
            all_slot.append(by_group(sub, "c3_slot", tag))

        # resolution flip: mid_m1 vs quote_s5, same events, cost 0.45
        hdr(f"{g}: resolution flip (mid_M1 vs quote_S5), all fillable trades")
        st_m1 = split_tables(tr, f"crt_{g}_nofilt_midM1", "m1_pts")
        st_ms = split_tables(tr, f"crt_{g}_nofilt_midS5", "ms_pts")
        print(pd.concat([st_m1, st_ms]).to_string(index=False))
        ct = pd.crosstab(tr.m1_outcome, tr.q_outcome, margins=True)
        print("\ncrosstab mid_M1 (rows) vs quote_S5 (cols):\n" + ct.to_string())
        flip = (tr.m1_outcome != tr.q_outcome)
        for nm, d, fm in (("ALL", tr, flip), ("TRAIN", tr[tr.split == "TRAIN"], flip[tr.split == "TRAIN"]),
                          ("TEST", tr[tr.split == "TEST"], flip[tr.split == "TEST"])):
            row = dict(grid=g, split=nm, n=len(d), flip_frac=round(float(fm.mean()), 3),
                       m1_pts_c045=round(float(d["m1_pts_c0.45"].sum()), 1), q_pts_c045=round(float(d["pts_c0.45"].sum()), 1),
                       m1_pf_c045=round(L.pf(d["m1_pts_c0.45"].values), 3), q_pf_c045=round(L.pf(d["pts_c0.45"].values), 3),
                       m1_wr=round(100 * float((d["m1_pts_c0.45"] > 0).mean()), 1), q_wr=round(100 * float((d["pts_c0.45"] > 0).mean()), 1),
                       m1_TP=int((d.m1_outcome == "TP").sum()), q_TP=int((d.q_outcome == "TP").sum()),
                       m1_SL=int((d.m1_outcome == "SL").sum()), q_SL=int((d.q_outcome == "SL").sum()))
            all_flip.append(row)
        print(pd.DataFrame(all_flip[-3:]).to_string(index=False))

        # control (a): random C3, concept removed
        hdr(f"{g}: control (a) random C3 (same slot, ±30 d, no sweep), K={K_CTL}, transplanted ATR-scaled geometry")
        is_sig = np.zeros(len(cd), dtype=bool)
        evall = L.detect_events(cd, atr)
        is_sig[evall.i2.values] = True
        ft = L.candle_fill_table(cd, s5)
        ctl = L.random_control(tr, cd, atr, is_sig, ft, s5, K=K_CTL, costs=COSTS)
        ctl["split"] = np.where(ctl.entry_time < CUT, "TRAIN", "TEST")
        ctl.to_parquet(RES / f"control_random_{g}.parquet", index=False)
        covered = tr.index.isin(ctl.trade_idx.unique())
        print(f"real trades with a control pool: {int(covered.sum())}/{len(tr)}")
        for cst in COSTS:
            col = f"pts_c{cst:.2f}"
            for nm in ("TRAIN", "TEST", "ALL"):
                d_real = tr[covered] if nm == "ALL" else tr[covered & (tr.split == nm).values]
                d_ctl = ctl if nm == "ALL" else ctl[ctl.split == nm]
                real_pf, real_pts = L.pf(d_real[col].values), float(d_real[col].sum())
                real_wr = 100 * float((d_real[col] > 0).mean()) if len(d_real) else np.nan
                per_rep = d_ctl.groupby("rep")[col].agg(pts="sum", pf=L.pf, wr=lambda v: 100 * (v > 0).mean(), n="size")
                row = dict(grid=g, cost=cst, split=nm, n_real=len(d_real), real_pf=round(real_pf, 3), real_pts=round(real_pts, 1),
                           real_wr=round(real_wr, 1), ctl_n_per_rep=int(per_rep.n.median()),
                           ctl_pf_mean=round(float(per_rep.pf.replace(np.inf, np.nan).mean()), 3),
                           ctl_pf_p5=round(float(per_rep.pf.quantile(0.05)), 3), ctl_pf_p95=round(float(per_rep.pf.quantile(0.95)), 3),
                           ctl_pts_mean=round(float(per_rep.pts.mean()), 1), ctl_wr_mean=round(float(per_rep.wr.mean()), 1),
                           share_ctl_pf_ge_real=round(float((per_rep.pf >= real_pf).mean()), 2),
                           share_ctl_pts_ge_real=round(float((per_rep.pts >= real_pts).mean()), 2))
                all_ctl.append(row)
        print(pd.DataFrame([r for r in all_ctl if r["grid"] == g]).to_string(index=False))

        # control (b): the C2-wick geometric artefact
        hdr(f"{g}: control (b) C2-wick artefact -- vault metric vs the honest rule, by wick quintile")
        w = L.wick_control(tr, s5, m1)
        w["wick_q"] = pd.qcut(w.wick_frac, 5, labels=["Q1_small", "Q2", "Q3", "Q4", "Q5_large"])
        w.to_parquet(RES / f"wick_control_{g}.parquet", index=False)
        rows = []
        for q, d in w.groupby("wick_q", observed=True):
            rows.append(dict(grid=g, wick_q=str(q), n=len(d), wick_frac_med=round(float(d.wick_frac.median()), 3),
                             vault_delivered_pct=round(100 * float(d.vault_delivered.mean()), 1),
                             cushion_R_med=round(float(d.cushion_R.median()), 3),
                             entry_beyond_target_pct=round(100 * float(d.entry_beyond_target.mean()), 1),
                             c2close_entry_c2open_TP_pct=round(100 * float(d.c2close_entry_c2open_outcome.isin(["TP", "TP_at_entry"]).mean()), 1),
                             moved_entry_c2open_TP_pct=round(100 * float(d.moved_entry_c2open_outcome.isin(["TP", "TP_at_entry"]).mean()), 1),
                             moved_entry_TP_at_entry_pct=round(100 * float((d.moved_entry_c2open_outcome == "TP_at_entry").mean()), 1),
                             honest_dist_R_med=round(float(d.dist_to_c1_target_R.median()), 3),
                             honest_TP_pct=round(100 * float((d.q_outcome == "TP").mean()), 1),
                             honest_SL_pct=round(100 * float((d.q_outcome == "SL").mean()), 1),
                             honest_wr_c045=round(100 * float((d["pts_c0.45"] > 0).mean()), 1),
                             honest_avgR_c045=round(float((d["pts_c0.45"] / d.risk).mean()), 3),
                             honest_pf_c045=round(L.pf(d["pts_c0.45"].values), 3)))
        wt = pd.DataFrame(rows); all_wick.append(wt)
        print(wt.to_string(index=False))
        q1, q5 = wt.iloc[0], wt.iloc[-1]
        print(f"Q1-Q5 gap: vault delivered {q1.vault_delivered_pct - q5.vault_delivered_pct:+.1f} pp"
              f" | C2-close entry/C2-open target {q1.c2close_entry_c2open_TP_pct - q5.c2close_entry_c2open_TP_pct:+.1f} pp"
              f" | moved-entry/C2-open target {q1.moved_entry_c2open_TP_pct - q5.moved_entry_c2open_TP_pct:+.1f} pp"
              f" | honest rule TP {q1.honest_TP_pct - q5.honest_TP_pct:+.1f} pp"
              f" | honest avgR {q1.honest_avgR_c045 - q5.honest_avgR_c045:+.3f} R")
        print(f"elapsed {time.time()-t0:.0f}s")

    hdr("BY YEAR (cost 0.45, quote_s5)")
    yr = pd.concat(all_year); print(yr.to_string(index=False)); yr.to_csv(RES / "by_year.csv", index=False)
    hdr("BY SIDE (cost 0.45, quote_s5)")
    sd = pd.concat(all_side); print(sd.to_string(index=False)); sd.to_csv(RES / "by_side.csv", index=False)
    hdr("BY C3 SLOT (cost 0.45, quote_s5, nofilt)")
    sl = pd.concat(all_slot); print(sl[sl.arm.str.endswith("nofilt")].to_string(index=False)); sl.to_csv(RES / "by_slot.csv", index=False)
    pd.concat(all_split).to_csv(RES / "split_tables.csv", index=False)
    pd.DataFrame(all_flip).to_csv(RES / "flip.csv", index=False)
    pd.DataFrame(all_ctl).to_csv(RES / "control_random.csv", index=False)
    pd.concat(all_wick).to_csv(RES / "wick_control.csv", index=False)

    hdr("BARS (lab.tools.campaign_score.score_campaign on results/arms, split 2025-12-01)")
    sc = score_campaign(ARMS, CACHE, SPLIT)
    cols = ["label", "verdict", "bars_passed", "test_n", "test_pf", "test_pf_stress", "test_pts", "train_n", "train_pf",
            "test_pos_month_share", "test_max_month_share", "gold_corr", "up_months_pts", "dn_months_pts",
            "bar1_n", "bar2_base", "bar2_stress", "bar3_train", "bar4_monthly", "bar5_regime"]
    print(sc[cols].to_string(index=False))
    sc.to_csv(RES / "bars.csv", index=False)

    hdr("SUMMARY: resolution flip")
    print(pd.DataFrame(all_flip).to_string(index=False))
    hdr("SUMMARY: random control")
    print(pd.DataFrame(all_ctl).to_string(index=False))
    print(f"\ntotal elapsed {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
