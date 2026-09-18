"""02c_drift_model.py — the harness with the live entry_drift gate and a market fill (PROTOCOL deviation 2).

For each harness trade of the four live legs (live-active windows only): fill at the sided OANDA
S5 quote at bar-open + 66 s; adverse = side x (fill - nominal); reject if adverse > budget
(shared.gate_rules.drift_budget_pts with the live defaults 0.5 / 0.25); accepted trades resolve
SL/TP on the quote from the fill with the strategy's max hold; +0.26 slip on SL outcomes.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
STRAT = HERE.parents[2]
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(STRAT))
from common import RES, live_trades, pf, boot_ci        # noqa: E402
from lab.s5exit import load_s5                         # noqa: E402
from shared.gate_rules import drift_budget_pts         # noqa: E402

MAX_HOLD = {"s93_fvg_scalp": 120, "s94_sweep_reversal": 1200, "s99_mss_fvg": 480, "s100_m3_combo": 72}
FILL_OFFSET_S = 66
SLIP_SL = 0.26


def main():
    s5 = load_s5()
    t5 = s5["time"].dt.tz_convert(None).to_numpy("datetime64[ns]")
    bid, ask, close = s5.bid_c.to_numpy(float), s5.ask_c.to_numpy(float), s5.c.to_numpy(float)
    lt = live_trades()
    lines = ["=== sim-with-drift-gate (market fill at bar-open+66 s, live drift budget, quote exits, +0.26 on SL) vs live, live-active windows ==="]
    summary = []
    for key, mh in MAX_HOLD.items():
        p = pd.read_csv(RES / f"parity_{key}.csv", parse_dates=["entry_time"])
        p["entry_time"] = pd.to_datetime(p.entry_time, utc=True)
        p = p[p.live_active].copy()
        res = []
        for r in p.itertuples():
            ent = np.datetime64(r.entry_time.tz_convert("UTC").tz_localize(None))
            i = int(np.searchsorted(t5, ent + np.timedelta64(FILL_OFFSET_S, "s"), "right")) - 1
            j = int(np.searchsorted(t5, ent + np.timedelta64(int(mh * 60), "s"), "right"))
            if i < 0 or j <= i + 1:
                res.append((np.nan, None, np.nan, np.nan)); continue
            long_ = r.side == "BUY"
            fill = ask[i] if long_ else bid[i]
            adverse = (fill - r.entry_px) if long_ else (r.entry_px - fill)
            budget = drift_budget_pts(r.entry_px, r.sl)
            if adverse > budget:
                res.append((adverse, "REJ", np.nan, np.nan)); continue
            px = bid[i + 1:j] if long_ else ask[i + 1:j]
            if long_:
                s = np.flatnonzero(px <= r.sl); t = np.flatnonzero(px >= r.tp)
            else:
                s = np.flatnonzero(px >= r.sl); t = np.flatnonzero(px <= r.tp)
            ks = s[0] if len(s) else np.inf; kt = t[0] if len(t) else np.inf
            if ks == np.inf and kt == np.inf:
                oc, xpx = "TIME", close[j - 1]
            elif ks <= kt:
                oc, xpx = "SL", r.sl
            else:
                oc, xpx = "TP", r.tp
            raw = (xpx - fill) if long_ else (fill - xpx)
            res.append((adverse, oc, raw - (SLIP_SL if oc == "SL" else 0.0), raw))
        p[["g_adverse", "g_outcome", "g_pts", "g_raw"]] = pd.DataFrame(res, index=p.index)
        acc = p[p.g_outcome.isin(["SL", "TP", "TIME"])]
        rej = p[p.g_outcome == "REJ"]
        lv = lt[lt.key == key]
        lines.append(f"\n--- {key} ---")
        lines.append(f"harness live-active n {len(p)}: drift-gate accepts {len(acc)} ({100*len(acc)/max(1,len(p)):.0f}%), rejects {len(rej)}; "
                     f"live: generated-excl-cap placed share = {len(lv)} placed")
        lines.append(f"plain harness quote@0.45 : n {len(p):<4} pts {p.q_pts.sum():+8.1f}  PF {pf(p.q_pts):.2f}  pts/trade {p.q_pts.mean():+.3f}")
        lines.append(f"sim-with-drift-gate      : n {len(acc):<4} pts {acc.g_pts.sum():+8.1f}  PF {pf(acc.g_pts):.2f}  pts/trade {acc.g_pts.mean():+.3f}  "
                     f"mean fill adverse {acc.g_adverse.mean():+.3f}  WR {100*(acc.g_pts>0).mean():.0f}%")
        lines.append(f"  gate-rejected in the sim : n {len(rej):<4} (adverse mean {rej.g_adverse.mean():+.2f})")
        lines.append(f"LIVE (broker truth)      : n {len(lv):<4} pts {lv.pts.sum():+8.1f}  PF {pf(lv.pts):.2f}  pts/trade {lv.pts.mean():+.3f}  "
                     f"mean fill adverse {(np.where(lv.side=='BUY',1,-1)*(lv.in_px-lv.sig_entry)).mean():+.3f}  WR {100*(lv.pts>0).mean():.0f}%")
        lo, hi = boot_ci(acc.g_pts); lo2, hi2 = boot_ci(lv.pts)
        lines.append(f"  pts/trade 90% CI: sim-with-gate [{lo:+.3f}, {hi:+.3f}]  live [{lo2:+.3f}, {hi2:+.3f}]")
        # matched PLACED pairs under the gate model
        mp = acc[acc.live_status == "PLACED"].copy()
        lvi = lv.set_index("signal_id")
        mp["live_pts"] = lvi.reindex(mp.live_signal_id).pts.to_numpy()
        mp = mp.dropna(subset=["live_pts"])
        if len(mp):
            lines.append(f"  matched PLACED pairs n {len(mp)}: sim-with-gate {mp.g_pts.sum():+.1f} vs live {mp.live_pts.sum():+.1f} "
                         f"(per pair sim-live mean {np.mean(mp.g_pts-mp.live_pts):+.3f}, median {np.median(mp.g_pts-mp.live_pts):+.3f})")
        summary.append(dict(strategy=key, harness_n=len(p), harness_q45=round(p.q_pts.sum(), 1), harness_pf=round(pf(p.q_pts), 3),
                            gate_n=len(acc), gate_pts=round(acc.g_pts.sum(), 1), gate_pf=round(pf(acc.g_pts), 3),
                            live_n=len(lv), live_pts=round(lv.pts.sum(), 1), live_pf=round(pf(lv.pts), 3)))
        p.to_csv(RES / f"parity_{key}.csv", index=False)
    pd.DataFrame(summary).to_csv(RES / "02c_drift_model_summary.csv", index=False)
    txt = "\n".join(lines); print(txt)
    (RES / "02c_drift_model.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
