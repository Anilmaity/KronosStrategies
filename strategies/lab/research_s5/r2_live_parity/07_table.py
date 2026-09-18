"""07_table.py — the (a) table on live-active windows only: harness at the four cost points,
sim-with-drift-gate, live; and the D8 gap decomposition restricted to live-active windows.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import RES, live_trades, pf   # noqa: E402

LEGS = ["s93_fvg_scalp", "s94_sweep_reversal", "s99_mss_fvg", "s100_m3_combo"]


def main():
    lt = live_trades()
    rows, dec = [], []
    for key in LEGS:
        p = pd.read_csv(RES / f"parity_{key}.csv")
        a = p[p.live_active].copy()
        lv = lt[lt.key == key]
        row = dict(strategy=key, harness_n=len(a))
        for lab, col in (("mid@0.75", a.pts0 - 0.75), ("mid@1.00", a.pts0 - 1.00), ("quote@0.45", a.q_pts0 - 0.45), ("quote@0.70", a.q_pts0 - 0.70)):
            row[f"{lab}_pts"] = round(col.sum(), 1); row[f"{lab}_pf"] = round(pf(col), 2)
        g = a[a.g_outcome.isin(["SL", "TP", "TIME"])]
        row.update(gate_n=len(g), gate_pts=round(g.g_pts.sum(), 1), gate_pf=round(pf(g.g_pts), 2),
                   live_n=len(lv), live_pts=round(lv.pts.sum(), 1), live_pf=round(pf(lv.pts), 2), live_meanR=round(lv.r.mean(), 3),
                   live_usd=round(lv.usd.sum(), 0))
        rows.append(row)
        # decomposition on live-active windows at quote@0.45
        sim_q = a.q_pts.sum(); live_tot = lv.pts.sum()
        terms = a.groupby("live_status").q_pts.sum()
        lvi = lv.set_index("signal_id")
        mp = a[a.live_status == "PLACED"].copy()
        mp["live_pts"] = lvi.reindex(mp.live_signal_id).pts.to_numpy()
        mp = mp.dropna(subset=["live_pts"])
        live_only = lv[~lv.index.isin(lv.index[lv.signal_id.isin(mp.live_signal_id)])]
        d = dict(strategy=key, gap=round(sim_q - live_tot, 1), **{f"rej_{k}": round(v, 1) for k, v in terms.items() if k != "PLACED"},
                 minus_live_only=round(-live_only.pts.sum(), 1), n_live_only=len(live_only),
                 execution=round((mp.q_pts - mp.live_pts).sum(), 1), n_pairs=len(mp),
                 exec_per_pair=round((mp.q_pts - mp.live_pts).mean(), 3) if len(mp) else np.nan)
        dec.append(d)
    T = pd.DataFrame(rows); Dc = pd.DataFrame(dec).fillna(0)
    txt = "=== (a) live-active windows: harness (4 cost points) vs sim-with-drift-gate vs live ===\n" + T.to_string(index=False)
    txt += "\n\n=== D8 decomposition on live-active windows, quote@0.45: harness - live = Σ rejected-by-gate + sim_only - live_only + execution ===\n" + Dc.to_string(index=False)
    tot = Dc.drop(columns=["strategy"]).sum(numeric_only=True)
    txt += "\nfour-leg totals: " + ", ".join(f"{k} {v:+.1f}" for k, v in tot.items() if k not in ("n_live_only", "n_pairs", "exec_per_pair"))
    print(txt)
    (RES / "07_table.txt").write_text(txt + "\n")
    T.to_csv(RES / "07_table_a.csv", index=False); Dc.to_csv(RES / "07_table_decomp.csv", index=False)


if __name__ == "__main__":
    main()
