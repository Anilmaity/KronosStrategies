"""run_h4.py -- H4: H4 CRT with a three-candle hold (PROTOCOL §4).

Same events, fills, stops and targets as lab/research_s5/crt (crt_lib imported, not
edited); the only change is the exit horizon: the end of candle i2+3 (C3, C4, C5) instead of
the end of C3. Gate: gross mid-M1 expectancy on TRAIN >= +2.0 pts/trade. Control:
crt_lib.random_control with the extended horizon. Output: results/h4_output.txt.
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from lab.research_s5.r2_new_edge import common as C
from lab.research_s5.po3_judas import po3_common as P
from lab.research_s5.crt import crt_lib as L
from lab.tools.qa_s5_cache import load_s5

CACHE = C.STRAT / "backtest" / "results" / "bars_cache_2y"
WIN_LO = np.datetime64("2024-09-01T00:00:00")
HOLD = 3          # candles after C2 (C3..C5)
GRIDS = ["H4_UTC", "H4_NY"]
GATE_PTS = 2.0


def resolve_extended(ev: pd.DataFrame, cd: pd.DataFrame, s5: L.S5, m1: L.M1) -> pd.DataFrame:
    ends = cd["end_utc"].values.astype("datetime64[ns]")
    keys = cd["key"].values
    stepd = pd.Timedelta(hours=int(cd["step_h"].iloc[0]))
    rows = []
    for r in ev.itertuples(index=False):
        d = {"skip": None}
        i5 = r.i2 + HOLD
        if i5 >= len(cd) or (keys[i5] - keys[r.i2]) != HOLD * stepd:
            d["skip"] = "no_consecutive_c5"; rows.append(d); continue
        long_ = r.side == "BUY"
        start, end = np.datetime64(r.c3_start, "ns"), ends[i5]
        if r.both:
            d["skip"] = "both_sides_swept"; rows.append(d); continue
        if (start < L.BAD_HI) and (end > L.BAD_LO):
            d["skip"] = "spike_window"; rows.append(d); continue
        ij, why = L.fill_index(s5, start, end)
        if ij is None:
            d["skip"] = why; rows.append(d); continue
        i, j = ij
        fill = s5.ask[i] if long_ else s5.bid[i]
        sl = r.c2_l - 0.1 * r.atr if long_ else r.c2_h + 0.1 * r.atr
        tp = r.c1_h if long_ else r.c1_l
        if not ((sl < fill < tp) if long_ else (tp < fill < sl)):
            d["skip"] = "fill_outside_geometry"; rows.append(d); continue
        d.update(entry_time=pd.Timestamp(s5.t[i], tz="UTC"), entry_px=fill, sl=sl, tp=tp, risk=abs(fill - sl),
                 reward=abs(tp - fill), i_entry=i, j_end=j, c5_end=end)
        oc, px, k = L.walk_quote(s5, i, j, long_, sl, tp)
        d.update(q_outcome=oc, outcome=oc, q_exit=px, raw_pts=(px - fill) if long_ else (fill - px), q_exit_time=s5.t[k])
        a = int(np.searchsorted(m1.t, start, "left")); b = int(np.searchsorted(m1.t, end, "left"))
        if b > a:
            oc3, px3 = L.walk_mid(m1.h, m1.l, m1.c, a, b, long_, sl, tp)
            m1fill = m1.o[a]
            d.update(m1_outcome=oc3, m1_raw=(px3 - m1fill) if long_ else (m1fill - px3))
        else:
            d.update(m1_outcome=None, m1_raw=np.nan)
        rows.append(d)
    return pd.concat([ev.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


def main() -> None:
    log = C.Tee(C.RESULTS / "h4_output.txt")
    t0 = time.time()
    m1df = pd.read_parquet(CACHE / "is_XAU_USD_1m.parquet")
    m1df["time"] = pd.to_datetime(m1df["time"], utc=True)
    m1df = m1df.sort_values("time").reset_index(drop=True)
    s5df = load_s5()
    s5 = L.s5_arrays(s5df); m1 = L.m1_arrays(m1df)
    m1_end = m1.t[-1]
    del s5df
    log(f"M1 {len(m1df):,} bars; S5 {s5.t.size:,} bars; load {time.time()-t0:.0f}s")
    candles = {g: L.build_candles(m1df, g) for g in ("H4_UTC", "H4_NY", "D1_UTC", "D1_NY")}

    ctl_rows = []
    for g in GRIDS:
        log(f"\n=== {g}: CRT with {HOLD}-candle hold ===")
        cd = candles[g]
        atr = L.wilder_atr(cd)
        ev = L.detect_events(cd, atr)
        ev = ev[(ev.c3_start >= WIN_LO)].reset_index(drop=True)
        res = resolve_extended(ev, cd, s5, m1)
        res = res[pd.isna(res.skip) | (res.skip.isna())]
        res = res[res.c5_end.notna()]
        res = res[res.c5_end.values.astype("datetime64[ns]") <= m1_end + np.timedelta64(1, "m")]
        tr = res[res.skip.isna()].copy().reset_index(drop=True)
        log(f"  events {len(ev)}; skips {resolve_extended(ev, cd, s5, m1).skip.value_counts(dropna=False).to_dict()}; trades {len(tr)}")
        pe, pu = L.bias_series(candles[L.PARENT[g]])
        b = L.bias_at(pe, pu, tr.c3_start.values.astype("datetime64[ns]"))
        tr["bias_ok"] = ((tr.side == "BUY") & (b == 1)) | ((tr.side == "SELL") & (b == 0))
        tr["entry_time"] = pd.to_datetime(tr["entry_time"], utc=True)
        et = tr["entry_time"]
        log(f"  outcomes quote: {tr.q_outcome.value_counts().to_dict()}; median risk {tr.risk.median():.2f}; RR {(tr.reward/tr.risk).median():.2f}; "
            f"median hold {((pd.to_datetime(tr.q_exit_time) - et.dt.tz_localize(None)).dt.total_seconds()/3600).median():.1f} h")
        for fl, sub in (("nofilt", tr), ("bias", tr[tr.bias_ok])):
            sub = sub.reset_index(drop=True)
            sub.to_parquet(C.RESULTS / f"h4_trades_{g}_{fl}.parquet", index=False)
            trn = sub[sub.entry_time < C.SPLIT]
            gross = float(trn.m1_raw.mean()) if len(trn) else float("nan")
            gate = gross >= GATE_PTS
            log(f"\n  -- {g} {fl}: TRAIN gross mid-M1 expectancy {gross:+.3f} pts/trade (n={len(trn)}) -> gate {'PASS' if gate else 'FAIL'} (>= {GATE_PTS})")
            log(f"     TRAIN gross quote expectancy {float(trn.raw_pts.mean()):+.3f}; TEST gross mid {float(sub[sub.entry_time >= C.SPLIT].m1_raw.mean()):+.3f}")
            C.book_line(sub, f"H4 {g} {fl} hold{HOLD}", log)
        # control
        is_sig = np.zeros(len(cd), dtype=bool)
        is_sig[L.detect_events(cd, atr).i2.values] = True
        ft = L.candle_fill_table(cd, s5)
        ends = cd["end_utc"].values.astype("datetime64[ns]")
        j2 = np.minimum(np.arange(len(cd)) + HOLD - 1, len(cd) - 1)
        ft["j_end"] = np.searchsorted(s5.t, ends[j2], "left")
        ctl = L.random_control(tr, cd, atr, is_sig, ft, s5, K=20, costs=C.COSTS)
        ctl["entry_time"] = pd.to_datetime(ctl["entry_time"], utc=True)
        ctl.to_parquet(C.RESULTS / f"h4_ctrl_{g}.parquet", index=False)
        log(f"\n  control (random C3', {HOLD}-candle hold, K=20): rows {len(ctl)}; covered trades {ctl.trade_idx.nunique()}/{len(tr)}")
        for fl, sub in (("nofilt", tr), ("bias", tr[tr.bias_ok])):
            cs = ctl[ctl.trade_idx.isin(sub.index)]
            for nm in ("TRAIN", "TEST"):
                dr = sub[(sub.entry_time < C.SPLIT) if nm == "TRAIN" else (sub.entry_time >= C.SPLIT)]
                dc = cs[(cs.entry_time < C.SPLIT) if nm == "TRAIN" else (cs.entry_time >= C.SPLIT)]
                for cst in C.COSTS:
                    col = f"pts_c{cst:.2f}"
                    rv = dr.raw_pts.to_numpy() - cst
                    per = dc.groupby("rep")[col].agg(pts="sum", pf=L.pf)
                    row = dict(grid=g, filt=fl, split=nm, cost=cst, n_real=len(dr), real_pf=round(L.pf(rv), 3), real_pts=round(float(rv.sum()), 1),
                               ctl_pf_mean=round(float(per.pf.replace(np.inf, np.nan).mean()), 3), ctl_pf_p5=round(float(per.pf.quantile(.05)), 3),
                               ctl_pf_p95=round(float(per.pf.quantile(.95)), 3), ctl_pts_mean=round(float(per.pts.mean()), 1),
                               share_ctl_pf_ge_real=round(float((per.pf >= L.pf(rv)).mean()), 2))
                    ctl_rows.append(row)
                    log(f"    {fl} {nm} @{cst:.2f}: real PF {row['real_pf']} pts {row['real_pts']} | ctl PF mean {row['ctl_pf_mean']} (p5 {row['ctl_pf_p5']}, p95 {row['ctl_pf_p95']}) pts {row['ctl_pts_mean']} | share ctl >= real {row['share_ctl_pf_ge_real']}")
    pd.DataFrame(ctl_rows).to_csv(C.RESULTS / "h4_control.csv", index=False)
    log(f"\ndone in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
