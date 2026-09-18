"""POST-HOC DIAGNOSTIC (not a pre-registered arm): re-resolve the same A0 entries under three
exit/fill models to show how much of Q.B's loss is quote geometry vs price behaviour.
  quote_hl : the protocol model (fill on ask-low / bid-high, exits on bid/ask extremes)
  quote_c  : the lab/s5exit convention (bid_c / ask_c closes only)
  mid_hl   : mid h/l, no spread at all (the harness-style optimistic bound)
Also: the same entries with the stop widened to far side + 1.0 pt (diagnostic only).
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np, pandas as pd
from common import RESULTS, HORIZON_S, load_s5_arrays, pf

ev = pd.read_parquet(RESULTS / "events.parquet")
a = load_s5_arrays()
t5, h5, l5, c5, bid, ask = a["t"], a["h"], a["l"], a["c"], a["bid_c"], a["ask_c"]
hs5 = (ask - bid) / 2


def sim(ev, model, stop_pad):
    out = np.full(len(ev), np.nan); oc = np.full(len(ev), "", dtype=object)
    for k, r in enumerate(ev.itertuples()):
        d = r.dir; glo, ghi = r.gap_low, r.gap_high
        ce = (glo + ghi) / 2
        sl = glo - stop_pad if d == 1 else ghi + stop_pad
        R = abs(ce - sl); tp = ce + 2 * R * d
        t0 = r.t0
        fs = int(np.searchsorted(t5, t0 + 5, "left")); e = int(np.searchsorted(t5, t0 + HORIZON_S, "left"))
        if e <= fs:
            continue
        if model == "quote_hl":
            f_lo, f_hi = l5[fs:e] + hs5[fs:e], h5[fs:e] - hs5[fs:e]
        elif model == "quote_c":
            f_lo, f_hi = ask[fs:e], bid[fs:e]
        else:
            f_lo, f_hi = l5[fs:e], h5[fs:e]
        fill = (f_lo <= ce) if d == 1 else (f_hi >= ce)
        if not fill.any():
            continue
        fi = fs + int(fill.argmax())
        he = int(np.searchsorted(t5, t5[fi] + HORIZON_S, "left"))
        w = slice(fi, he)                                         # fill bar included, stop-first
        if model == "quote_hl":
            x_lo, x_hi = l5[w] - hs5[w], h5[w] + hs5[w]           # long stop on bid-low; short stop on ask-high
            tg_lo, tg_hi = l5[w] + hs5[w], h5[w] - hs5[w]         # short target on ask-low; long target on bid-high
            cl_long, cl_short = bid[w], ask[w]
        elif model == "quote_c":
            x_lo = tg_lo = cl_long = bid[w]; x_hi = tg_hi = cl_short = ask[w]
            # long: stop bid<=sl, target bid>=tp ; short: stop ask>=sl, target ask<=tp
            x_lo, tg_hi = bid[w], bid[w]; x_hi, tg_lo = ask[w], ask[w]
        else:
            x_lo = tg_lo = l5[w]; x_hi = tg_hi = h5[w]; cl_long = cl_short = c5[w]
        if d == 1:
            st = x_lo <= sl; tg = tg_hi >= tp
        else:
            st = x_hi >= sl; tg = tg_lo <= tp
        tg[0] = False                                            # no target on the fill bar
        si = int(st.argmax()) if st.any() else 10**9
        gi = int(tg.argmax()) if tg.any() else 10**9
        if si <= gi and si < 10**9:
            out[k] = -R; oc[k] = "SL"
        elif gi < 10**9:
            out[k] = 2 * R; oc[k] = "TP"
        else:
            px = cl_long[-1] if d == 1 else cl_short[-1]
            out[k] = (px - ce) if d == 1 else (ce - px); oc[k] = "TIME"
    return out, oc


rows = []
for model in ("quote_hl", "quote_c", "mid_hl"):
    for pad in (0.2, 1.0):
        pts, oc = sim(ev, model, pad)
        m = ~np.isnan(pts)
        for cost in (0.45, 0.80):
            for sp in ("TRAIN", "TEST"):
                mm = m & (ev.split == sp).to_numpy()
                v = pts[mm] - cost
                rows.append(dict(model=model, stop_pad=pad, cost=cost, split=sp, n=int(mm.sum()),
                                 tp_rate=float((oc[mm] == "TP").mean()), pf=pf(v), net=float(v.sum())))
r = pd.DataFrame(rows)
print("POST-HOC DIAGNOSTIC — A0 entries under alternative exit models (not pre-registered):")
print(r.to_string(index=False, float_format=lambda v: f"{v:.3f}"))
r.to_csv(RESULTS / "diag_exit_model.csv", index=False)
