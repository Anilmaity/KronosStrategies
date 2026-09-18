"""Build every M5 FVG in the window, attach S5 displacement features, outcome labels and the
per-event CE-limit trade simulation (raw points, before cost). Output: results/events.parquet.
Run from KronosStrategies/strategies:  ../.venv/bin/python lab/research_s5/fvg_displacement/build_events.py
"""
from __future__ import annotations
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import pandas as pd
from common import (M5_PATH, RESULTS, WINDOW_START, SPIKE_LO, SPIKE_HI, HORIZON_S, KZ_HOURS,
                    load_s5_arrays, dense_grid)

T0 = time.time()
RESULTS.mkdir(exist_ok=True)

# ---------------------------------------------------------------- M5 FVG events
m5 = pd.read_parquet(M5_PATH)
m5["time"] = pd.to_datetime(m5["time"], utc=True)
m5 = m5.sort_values("time").reset_index(drop=True)
ts = (m5["time"].astype("int64") // 10**9).to_numpy(np.int64)
H, L, O, C = (m5[k].to_numpy(float) for k in ("high", "low", "open", "close"))
rng = H - L
atr5 = pd.Series(rng).rolling(20).mean().to_numpy()

i = np.arange(2, len(m5))
consec = (ts[i] - ts[i - 2]) == 600
bull = H[i - 2] < L[i]
bear = L[i - 2] > H[i]
gap_b = L[i] - H[i - 2]
gap_s = L[i - 2] - H[i]
sel_b = consec & bull & (gap_b >= 0.5)
sel_s = consec & bear & (gap_s >= 0.5)
rows = []
for sel, d, gsz, lo, hi in ((sel_b, 1, gap_b, H[i - 2], L[i]), (sel_s, -1, gap_s, H[i], L[i - 2])):
    k = i[sel]
    rows.append(pd.DataFrame({
        "i3": k, "dir": d, "gap_size": gsz[sel], "gap_low": lo[sel], "gap_high": hi[sel],
        "b1_start": ts[k - 2], "b2_start": ts[k - 1], "t0": ts[k] + 300,
        "atr5": atr5[k],
        "b2_range": rng[k - 1],
        "range3": np.maximum.reduce([H[k - 2], H[k - 1], H[k]]) - np.minimum.reduce([L[k - 2], L[k - 1], L[k]]),
        "b2_body_dir": np.sign(C[k - 1] - O[k - 1]),
    }))
ev = pd.concat(rows, ignore_index=True).sort_values("t0").reset_index(drop=True)
n_all = len(ev)
m5_end = int(ts[-1]) + 300
w0 = int(WINDOW_START.timestamp())
ev = ev[(ts[ev.i3] >= w0) & (ev.t0 + HORIZON_S <= m5_end) & ev.atr5.notna()].reset_index(drop=True)
n_win = len(ev)
sp_lo, sp_hi = int(SPIKE_LO.timestamp()), int(SPIKE_HI.timestamp())
# Deviation (recorded in PROTOCOL): exclusion span is [b1_start, t0 + 16 h] so that a trade's
# 8 h hold after an 8 h fill window can never cross the feed spike either.
ov = (ev.b1_start <= sp_hi) & (ev.t0 + 2 * HORIZON_S >= sp_lo)
ev = ev[~ov].reset_index(drop=True)
print(f"M5 FVGs (gap>=0.5, consecutive): {n_all}  in window: {n_win}  after spike exclusion: {len(ev)}")
print(f"  bullish {int((ev.dir==1).sum())}  bearish {int((ev.dir==-1).sum())}")

# ---------------------------------------------------------------- S5
a = load_s5_arrays()
print(f"S5 bars {len(a['t']):,}  ({time.time()-T0:.0f}s)")
g = dense_grid(a)
c, hg, lg, vg, pres, origin = g["c"], g["h"], g["l"], g["v"], g["present"], g["origin"]
print(f"dense grid slots {g['n']:,}  ({time.time()-T0:.0f}s)")

dirv = ev["dir"].to_numpy(float)[:, None]
s2 = ((ev.b2_start.to_numpy(np.int64) - origin) // 5)
s1 = ((ev.b1_start.to_numpy(np.int64) - origin) // 5)
assert (s2 - 6 >= 0).all()

def feats(start, nslots, denom_range):
    idx = start[:, None] + np.arange(-6, nslots)[None, :]          # 6 slots of context before
    cc = c[idx]                                                    # (n, nslots+6)
    dc = np.diff(cc, axis=1)[:, 5:]                                # deltas for the nslots (uses c_{-1})
    d30 = (cc[:, 6:] - cc[:, :-6])                                 # c_k - c_{k-6}
    pr = pres[idx[:, 6:]]
    v = vg[idx[:, 6:]]
    V = v.sum(1)
    peak = (dirv * dc).max(1)
    burst = np.clip((dirv * d30).max(1) / denom_range, 0, 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        hhi = np.where(V > 0, ((v / V[:, None]) ** 2).sum(1), np.nan)
        npres = pr.sum(1)
        pull = np.where(npres > 0, ((dirv * dc < 0) & pr).sum(1) / npres, np.nan)
        sabs = np.abs(dc).sum(1)
        er = np.where(sabs > 0, np.abs(cc[:, -1] - cc[:, 6]) / sabs, np.nan)
    return peak, burst, hhi, pull, er, npres

atr = ev.atr5.to_numpy()
peak, burst, hhi, pull, er, npres = feats(s2, 60, ev.b2_range.to_numpy())
ev["peak_vel"] = peak / atr
ev["burst30"] = burst
ev["vol_hhi"] = hhi
ev["pullback_frac"] = pull
ev["er2"] = er
ev["n_s5_bar2"] = npres
peak3, burst3, _, _, _, npres3 = feats(s1, 180, ev.range3.to_numpy())
ev["peak_vel3"] = peak3 / atr
ev["burst30_3"] = burst3
ev["n_s5_bars123"] = npres3
ev["gap_atr"] = ev.gap_size / ev.atr5
ev["peak_vel_pts"] = peak
print(f"features done ({time.time()-T0:.0f}s); events with <12 S5 bars in bar2: {int((ev.n_s5_bar2 < 12).sum())}")

# ---------------------------------------------------------------- labels + trade sim (loop over events)
t5, h5, l5, bid, ask = a["t"], a["h"], a["l"], a["bid_c"], a["ask_c"]
hs5 = (ask - bid) / 2
t0v = ev.t0.to_numpy(np.int64)
N = len(ev)
lab = np.full(N, "untouched", dtype=object)
touch_t = np.full(N, np.nan); resolve_t = np.full(N, np.nan)
# secondary label (recorded deviation): the return must reach CE, matching the Q.B fill
lab_ce = np.full(N, "untouched", dtype=object)
touch_ce_t = np.full(N, np.nan); resolve_ce_t = np.full(N, np.nan)
filled = np.zeros(N, bool); fill_t = np.full(N, np.nan); exit_t = np.full(N, np.nan)
outcome = np.full(N, "", dtype=object); raw_pts = np.full(N, np.nan); risk = np.full(N, np.nan)
n_after_fill = np.zeros(N, int)

gl_, gh_, gs_ = ev.gap_low.to_numpy(), ev.gap_high.to_numpy(), ev.gap_size.to_numpy()
dv = ev["dir"].to_numpy()
for k in range(N):
    t0 = t0v[k]; d = dv[k]; glo, ghi, gs = gl_[k], gh_[k], gs_[k]
    s = int(np.searchsorted(t5, t0, "left")); e = int(np.searchsorted(t5, t0 + HORIZON_S, "left"))
    if e <= s:
        continue
    hh, ll = h5[s:e], l5[s:e]
    if d == 1:
        enter = ll <= ghi; through = ll < glo; leave = hh >= ghi + gs
    else:
        enter = hh >= glo; through = hh > ghi; leave = ll <= glo - gs
    ce = (glo + ghi) / 2
    enter_ce = (ll <= ce) if d == 1 else (hh >= ce)
    for enter_, lab_, tt_, rt_ in ((enter, lab, touch_t, resolve_t), (enter_ce, lab_ce, touch_ce_t, resolve_ce_t)):
        if enter_.any():
            ei = int(enter_.argmax()); tt_[k] = t5[s + ei]
            th = through[ei:]; lv = leave[ei:]
            ti = int(th.argmax()) if th.any() else 10**9
            li = int(lv.argmax()) if lv.any() else 10**9
            if ti == 10**9 and li == 10**9:
                lab_[k] = "unresolved"
            elif li < ti:
                lab_[k] = "respected"; rt_[k] = t5[s + ei + li]
            else:
                lab_[k] = "filled_through"; rt_[k] = t5[s + ei + ti]
    # ---- trade: CE limit, fill from t0+5s, up to 8 h; hold 8 h from fill
    sl = glo - 0.2 if d == 1 else ghi + 0.2
    R = abs(ce - sl); tp = ce + 2 * R * d
    risk[k] = R
    fs = int(np.searchsorted(t5, t0 + 5, "left"))
    if e <= fs:
        continue
    hsw = hs5[fs:e]
    fill = (l5[fs:e] + hsw <= ce) if d == 1 else (h5[fs:e] - hsw >= ce)
    if not fill.any():
        continue
    fi = fs + int(fill.argmax())
    filled[k] = True; fill_t[k] = t5[fi]
    # stop-first on the fill bar
    if (d == 1 and l5[fi] - hs5[fi] <= sl) or (d == -1 and h5[fi] + hs5[fi] >= sl):
        outcome[k] = "SL"; exit_t[k] = t5[fi]; raw_pts[k] = -R
        continue
    he = int(np.searchsorted(t5, t5[fi] + HORIZON_S, "left"))
    w = slice(fi + 1, he)
    n_after_fill[k] = he - fi - 1
    if he <= fi + 1:
        outcome[k] = "TIME"; exit_t[k] = t5[fi]
        raw_pts[k] = (bid[fi] - ce) if d == 1 else (ce - ask[fi])
        continue
    hsw = hs5[w]
    if d == 1:
        st = l5[w] - hsw <= sl; tg = h5[w] - hsw >= tp
    else:
        st = h5[w] + hsw >= sl; tg = l5[w] + hsw <= tp
    si = int(st.argmax()) if st.any() else 10**9
    gi = int(tg.argmax()) if tg.any() else 10**9
    if si <= gi and si < 10**9:
        outcome[k] = "SL"; exit_t[k] = t5[fi + 1 + si]; raw_pts[k] = -R
    elif gi < 10**9:
        outcome[k] = "TP"; exit_t[k] = t5[fi + 1 + gi]; raw_pts[k] = 2 * R
    else:
        j = he - 1
        outcome[k] = "TIME"; exit_t[k] = t5[j]
        raw_pts[k] = (bid[j] - ce) if d == 1 else (ce - ask[j])

ev["label"] = lab; ev["touch_t"] = touch_t; ev["resolve_t"] = resolve_t
ev["label_ce"] = lab_ce; ev["touch_ce_t"] = touch_ce_t; ev["resolve_ce_t"] = resolve_ce_t
ev["filled"] = filled; ev["fill_t"] = fill_t; ev["exit_t"] = exit_t
ev["outcome"] = outcome; ev["raw_pts"] = raw_pts; ev["risk"] = risk; ev["n_after_fill"] = n_after_fill
ev["t0_ts"] = pd.to_datetime(ev.t0, unit="s", utc=True)
ev["hour"] = ev.t0_ts.dt.hour
ev["killzone"] = ev.hour.isin(KZ_HOURS)
ev["month"] = ev.t0_ts.dt.strftime("%Y-%m")
ev["split"] = np.where(ev.t0_ts < pd.Timestamp("2025-12-01", tz="UTC"), "TRAIN", "TEST")
print(f"labels + trades done ({time.time()-T0:.0f}s)")
print("label counts (primary, near-edge touch):\n" + ev.groupby(["split", "label"]).size().unstack(fill_value=0).to_string())
print("label counts (secondary, CE touch):\n" + ev.groupby(["split", "label_ce"]).size().unstack(fill_value=0).to_string())
print("trade outcomes:\n" + ev.groupby(["split", "outcome"]).size().unstack(fill_value=0).to_string())
print("feature summary:\n" + ev[["gap_size", "gap_atr", "peak_vel", "burst30", "vol_hhi", "pullback_frac", "er2",
                                  "peak_vel3", "burst30_3", "n_s5_bar2"]].describe().T.to_string())
out = RESULTS / "events.parquet"
ev.to_parquet(out, index=False)
print(f"wrote {out}  rows={len(ev)}  ({time.time()-T0:.0f}s)")
