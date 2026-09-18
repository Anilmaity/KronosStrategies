"""02_parity.py — live vs harness on the same dates (PROTOCOL D5, D7, D8).

    cd strategies && ../.venv/bin/python lab/research_s5/r2_live_parity/02_parity.py

Per strategy: the selection funnel (live generated → placed, harness generated), the
match table (harness trades ↔ live signals, window [-180 s, +60 s] on the harness bar-open
time), the D8 decomposition of sim_total − live_total, and the matched-PLACED execution
deltas. Comparator: quote-S5 @ 0.45 (live points are net of spread/slippage); mid-M1 @ 0.75
alongside.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import RES, live_trades, load_signals, pf   # noqa: E402

ARMS = {"s93_fvg_scalp": ["s93_pre", "s93_post"], "s94_sweep_reversal": ["s94"],
        "s99_mss_fvg": ["s99"], "s100_m3_combo": ["s100"]}
W_LO, W_HI = -180.0, 60.0     # seconds: harness bar-open relative to live signal_at
QC, MC = 0.45, 0.75


def load_sim(arms):
    d = pd.concat([pd.read_parquet(RES / f"sim_{a}.parquet") for a in arms], ignore_index=True)
    d["entry_time"] = pd.to_datetime(d.entry_time, utc=True)
    d["q_pts"] = d.q_pts0 - QC
    d["m_pts"] = d.pts0 - MC
    return d.sort_values("entry_time").reset_index(drop=True)


def match(sim: pd.DataFrame, sig: pd.DataFrame):
    """Greedy nearest-first, each row once (parity_harness.match_trades rule + side key)."""
    cands = []
    st = sim.entry_time.to_numpy("datetime64[ns]").astype("int64") / 1e9
    lt = sig.signal_at.to_numpy("datetime64[ns]").astype("int64") / 1e9
    for i in range(len(sim)):
        dt = st[i] - lt                       # harness bar-open minus live signal_at
        ok = (dt >= W_LO) & (dt <= W_HI) & (sig.side.to_numpy() == sim.side.iloc[i])
        for j in np.flatnonzero(ok):
            cands.append((abs(dt[j]), i, j))
    cands.sort()
    used_s, used_l, pairs = set(), set(), []
    for _d, i, j in cands:
        if i in used_s or j in used_l:
            continue
        used_s.add(i); used_l.add(j); pairs.append((i, j))
    return pairs, used_s, used_l


def main():
    lt = live_trades()
    sig = load_signals()
    out = []
    rows = []
    for key, arms in ARMS.items():
        sim = load_sim(arms)
        ls = sig[sig.key == key].reset_index(drop=True)
        lv = lt[lt.key == key].set_index("signal_id")
        pairs, used_s, used_l = match(sim, ls)
        sim["live_idx"] = -1
        for i, j in pairs:
            sim.loc[i, "live_idx"] = j
        sim["live_status"] = np.where(sim.live_idx >= 0, ls.reason_key.reindex(sim.live_idx.clip(lower=0)).to_numpy(), "sim_only")
        sim["live_signal_id"] = np.where(sim.live_idx >= 0, ls.signal_id.reindex(sim.live_idx.clip(lower=0)).to_numpy(), None)
        ls["sim_idx"] = -1
        for i, j in pairs:
            ls.loc[j, "sim_idx"] = i
        # ---- funnel ----
        gen_live = len(ls)
        gen_live_nocap = int((ls.reason_key != "open_position_cap").sum())
        placed = int((ls.status == "PLACED").sum())
        out.append(f"\n=== {key} ===")
        out.append(f"live generated {gen_live} (excl. open_position_cap {gen_live_nocap}) | placed {placed} "
                   f"| live closed trades {len(lv)} | harness trades {len(sim)}")
        out.append("live rejection reasons: " + ", ".join(f"{k} {v}" for k, v in ls.reason_key.value_counts().items()))
        # ---- match table ----
        vc = sim.live_status.value_counts()
        out.append("harness trades by live status: " + ", ".join(f"{k} {v}" for k, v in vc.items()))
        lm = ls[(ls.status == "PLACED")]
        n_live_only = int((lm.sim_idx < 0).sum())
        out.append(f"live PLACED matched to a harness trade: {int((lm.sim_idx >= 0).sum())} / {len(lm)}  (live-only {n_live_only})")
        # time offsets of matches
        if pairs:
            dts = [(sim.entry_time.iloc[i] - ls.signal_at.iloc[j]).total_seconds() for i, j in pairs]
            out.append(f"bar-open minus signal_at (s): median {np.median(dts):.0f}, p10 {np.percentile(dts,10):.0f}, p90 {np.percentile(dts,90):.0f}")
            lvl = [abs(sim.entry_px.iloc[i] - ls.entry_price.iloc[j]) for i, j in pairs]
            out.append(f"|harness entry - live signal entry| pts: median {np.median(lvl):.2f}, p90 {np.percentile(lvl,90):.2f}, share <= 0.5: {np.mean(np.array(lvl) <= 0.5):.0%}")
        # ---- totals ----
        live_tot = lv.pts.sum()
        sim_q, sim_m = sim.q_pts.sum(), sim.m_pts.sum()
        out.append(f"TOTAL live pts {live_tot:+.1f} (PF {pf(lv.pts):.2f}, n {len(lv)}, mean R {lv.r.mean():+.3f}) | "
                   f"harness quote@{QC} {sim_q:+.1f} (PF {pf(sim.q_pts):.2f}) | mid@{MC} {sim_m:+.1f} (PF {pf(sim.m_pts):.2f})")
        # ---- D8 decomposition at quote@0.45 ----
        out.append(f"D8 decomposition of harness(quote@{QC}) - live = {sim_q - live_tot:+.1f}:")
        terms = {}
        for st, g in sim.groupby("live_status"):
            if st in ("PLACED",):
                continue
            terms[st] = g.q_pts.sum()
            out.append(f"   harness pts of trades live {st:<22} n={len(g):<4} {g.q_pts.sum():+8.1f}   (mid@{MC} {g.m_pts.sum():+8.1f})")
        live_only_ids = lm[lm.sim_idx < 0].signal_id
        lo_pts = lv.reindex(live_only_ids).pts.dropna()
        out.append(f"   minus live pts of live-only trades       n={len(lo_pts):<4} {-lo_pts.sum():+8.1f}")
        # matched PLACED pairs: execution term
        mp = sim[sim.live_status == "PLACED"].copy()
        mp["live_pts"] = lv.reindex(mp.live_signal_id).pts.to_numpy()
        mp["live_r"] = lv.reindex(mp.live_signal_id).r.to_numpy()
        mp["live_fill"] = lv.reindex(mp.live_signal_id).in_px.to_numpy()
        mp["live_exit"] = lv.reindex(mp.live_signal_id).out_px.to_numpy()
        mp["live_kind"] = lv.reindex(mp.live_signal_id).exit_kind.to_numpy()
        mp["live_sig_entry"] = lv.reindex(mp.live_signal_id).sig_entry.to_numpy()
        mp = mp.dropna(subset=["live_pts"])
        ex = (mp.q_pts - mp.live_pts).sum()
        out.append(f"   matched PLACED (sim - live) execution     n={len(mp):<4} {ex:+8.1f}   (sim q {mp.q_pts.sum():+.1f} vs live {mp.live_pts.sum():+.1f}; mid {mp.m_pts.sum():+.1f})")
        chk = sum(terms.values()) - lo_pts.sum() + ex
        out.append(f"   sum of terms {chk:+.1f}  (identity check vs {sim_q - live_tot:+.1f})")
        if len(mp):
            agree = (mp.q_outcome == mp.live_kind).mean()
            out.append(f"   matched pairs: outcome agreement {agree:.0%}; per-pair (sim-live) pts median {np.median(mp.q_pts-mp.live_pts):+.2f}, "
                       f"mean {np.mean(mp.q_pts-mp.live_pts):+.2f}; live PF {pf(mp.live_pts):.2f} vs sim PF {pf(mp.q_pts):.2f} on the SAME trades")
            ct = pd.crosstab(mp.q_outcome, mp.live_kind)
            out.append("   crosstab sim outcome (rows) vs live exit (cols):\n" + ct.to_string())
        # ---- live subsets the harness did not have ----
        lv2 = lv.copy(); lv2["matched"] = lv2.index.isin(lm[lm.sim_idx >= 0].signal_id)
        g = lv2.groupby("matched").agg(n=("pts", "size"), pts=("pts", "sum"), meanR=("r", "mean"))
        g["pf"] = [pf(lv2[lv2.matched == m].pts) for m in g.index]
        out.append("   live trades by whether the harness reproduced them:\n" + g.to_string())
        sim.to_csv(RES / f"parity_{key}.csv", index=False)
        rows.append(dict(strategy=key, live_n=len(lv), live_pts=round(live_tot, 1), live_pf=round(pf(lv.pts), 3),
                         live_meanR=round(lv.r.mean(), 4), sim_n=len(sim), sim_q45_pts=round(sim_q, 1),
                         sim_q45_pf=round(pf(sim.q_pts), 3), sim_m75_pts=round(sim_m, 1), sim_m75_pf=round(pf(sim.m_pts), 3),
                         matched_placed=len(mp), live_only=n_live_only, sim_only=int((sim.live_status == "sim_only").sum()),
                         exec_term=round(ex, 1), **{f"term_{k}": round(v, 1) for k, v in terms.items()}))
    pd.DataFrame(rows).to_csv(RES / "02_parity_summary.csv", index=False)
    txt = "\n".join(out)
    print(txt)
    (RES / "02_parity.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
