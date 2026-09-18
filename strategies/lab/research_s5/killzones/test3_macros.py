"""test3_macros.py -- do the NY macros continue or reverse the 20 m pre-macro drift, at the
burst level and net, vs a same-duration random-placement null (PROTOCOL Test 3). The
tradeable rule is run ONLY if the pooled TRAIN net statistic is > 2 sigma from the null."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np, pandas as pd
from common import *

N_PERM = 2000
rng = np.random.default_rng(20260918)
g = build_grid(); s0 = int(g["slot0"])
c, bid, ask, h, l = g["c"], g["bid_c"], g["ask_c"], g["h"], g["l"]
D = day_table(g); D = D[D.valid & D.in_window].reset_index(drop=True)
E = pd.read_parquet(RESULTS / "events.parquet")
B = E[E.kind == "BURST"].copy()
day_pos = {d: i for i, d in enumerate(D.day)}
B["di"] = B.day.map(day_pos).astype(int); B["minute"] = B.tdm.astype(int); B["sgn"] = np.sign(B.value)
B = B[B.sgn != 0]
bdi, bmin, bsgn = B.di.values, B.minute.values, B.sgn.values
MACROS = windows_tdm("macros")
slot_a = D.slot_a.values


def stats_at(start, dur, day_mask=None):
    """pooled continuation stats for windows [start, start+dur) on all days (vectorised over days)."""
    ks = slot_a + start * 12; ke = ks + dur * 12
    d = c[ks - 1] - c[ks - 241]                       # 20 m pre-drift, last close at/before t0
    net = c[ke - 1] - c[ks - 1]
    rng_ = np.array([h[a:b].max() - l[a:b].min() for a, b in zip(ks, ke)])
    sd = np.sign(d)
    ok = sd != 0
    if day_mask is not None:
        ok &= day_mask
    cont_net = (np.sign(net[ok]) == sd[ok]).mean()
    ratio = np.median(net[ok] * sd[ok] / np.abs(d[ok]))          # signed macro move in units of |drift|
    m = (bmin >= start) & (bmin < start + dur) & ok[bdi]
    cont_b = (bsgn[m] == sd[bdi[m]]).mean() if m.any() else np.nan
    return dict(n=int(ok.sum()), cont_net=cont_net, cont_burst=cont_b, n_bursts=int(m.sum()),
                med_range=float(np.median(rng_[ok])), med_absnet=float(np.median(np.abs(net[ok]))), med_net_over_drift=float(ratio))


rows = []
for split in ("ALL", "TRAIN", "TEST"):
    mask = None if split == "ALL" else (D.split.values == split)
    for name, a, b in MACROS + [("POOLED", None, None)]:
        if name == "POOLED":
            parts = [stats_at(a_, b_ - a_, mask) for _, a_, b_ in MACROS]
            obs = dict(n=sum(p["n"] for p in parts), n_bursts=sum(p["n_bursts"] for p in parts))
            obs["cont_net"] = sum(p["cont_net"] * p["n"] for p in parts) / obs["n"]
            obs["cont_burst"] = sum(p["cont_burst"] * p["n_bursts"] for p in parts) / obs["n_bursts"]
            durs = [b_ - a_ for _, a_, b_ in MACROS]
        else:
            obs = stats_at(a, b - a, mask); durs = [b - a]
        # null: same duration(s), common random start(s) per draw
        nn, nb, nr = [], [], []
        for k in range(N_PERM):
            ps = []
            for dur in durs:
                s = int(rng.integers(30, DAY_MIN - dur))
                ps.append(stats_at(s, dur, mask))
            n_ = sum(p["n"] for p in ps); nb_ = sum(p["n_bursts"] for p in ps)
            nn.append(sum(p["cont_net"] * p["n"] for p in ps) / n_)
            nb.append(sum(p["cont_burst"] * p["n_bursts"] for p in ps) / nb_ if nb_ else np.nan)
            nr.append(np.mean([p["med_range"] for p in ps]))
        nn, nb, nr = np.array(nn), np.array(nb), np.array(nr)
        r = dict(split=split, macro=name, n=obs["n"], n_bursts=obs["n_bursts"],
                 cont_net=round(obs["cont_net"], 3), null_net_mean=round(nn.mean(), 3), null_net_sd=round(nn.std(), 3),
                 z_net=round((obs["cont_net"] - nn.mean()) / nn.std(), 2),
                 p_net=round(float(np.mean(np.abs(nn - nn.mean()) >= abs(obs["cont_net"] - nn.mean()))), 4),
                 cont_burst=round(obs["cont_burst"], 3), null_burst_mean=round(np.nanmean(nb), 3), null_burst_sd=round(np.nanstd(nb), 3),
                 z_burst=round((obs["cont_burst"] - np.nanmean(nb)) / np.nanstd(nb), 2),
                 p_burst=round(float(np.nanmean(np.abs(nb - np.nanmean(nb)) >= abs(obs["cont_burst"] - np.nanmean(nb)))), 4))
        if name != "POOLED":
            r.update(med_range=round(obs["med_range"], 2), null_range=round(nr.mean(), 2), range_ratio=round(obs["med_range"] / nr.mean(), 2),
                     range_p=round(float((nr >= obs["med_range"]).mean()), 4), med_absnet=round(obs["med_absnet"], 2),
                     med_net_over_drift=round(obs["med_net_over_drift"], 2))
        rows.append(r)
R = pd.DataFrame(rows); R.to_csv(RESULTS / "t3_macros.csv", index=False)
pd.set_option("display.width", 300); pd.set_option("display.max_columns", 40)
print("=== Test 3: macro continuation of the 20 m pre-drift (0.5 = coin flip); null = same duration, random common start, 2000 draws ===")
print(R[["split", "macro", "n", "cont_net", "null_net_mean", "null_net_sd", "z_net", "p_net", "n_bursts", "cont_burst", "null_burst_mean", "null_burst_sd", "z_burst", "p_burst"]].to_string(index=False))
print("\n=== descriptive: range delivered inside the macro vs random same-duration windows (median of h-l, pts) ===")
print(R[R.macro != "POOLED"][["split", "macro", "med_range", "null_range", "range_ratio", "range_p", "med_absnet", "med_net_over_drift"]].to_string(index=False))

tr = R[(R.split == "TRAIN") & (R.macro == "POOLED")].iloc[0]
trig = abs(tr.z_net) > 2
print(f"\n=== tradeable gate: TRAIN pooled net z = {tr.z_net} -> {'TRIGGERED' if trig else 'NOT triggered (|z| <= 2): no trade rule is simulated, per PROTOCOL'} ===")
if trig:
    direction = 1 if tr.cont_net > tr.null_net_mean else -1   # +1 continuation, -1 reversal
    print("direction implied by TRAIN:", "continuation" if direction > 0 else "reversal")
    trades = []
    for day in D.itertuples():
        for name, a, b in MACROS:
            for arm, st in (("MACRO", a), ("CTRL_A", a - 30), ("CTRL_A", a + 30)):
                ks = day.slot_a + st * 12; ke = ks + (b - a) * 12
                if ke > day.last: continue
                d = c[ks - 1] - c[ks - 241]
                if d == 0: continue
                side = int(np.sign(d)) * direction
                ent = ask[ks] if side > 0 else bid[ks]      # market at the next S5 bar after t0
                lo20, hi20 = l[ks - 240:ks].min(), h[ks - 240:ks].max()
                sl = lo20 - 0.2 if side > 0 else hi20 + 0.2
                Rr = abs(ent - sl); tp = ent + side * 2 * Rr
                px = bid[ks:ke] if side > 0 else ask[ks:ke]
                hs = px <= sl if side > 0 else px >= sl; ht = px >= tp if side > 0 else px <= tp
                k1 = np.argmax(hs) if hs.any() else 10**9; k2 = np.argmax(ht) if ht.any() else 10**9
                if k1 == k2 == 10**9: oc, xp = "TIME", px[-1]
                elif k1 <= k2: oc, xp = "SL", sl
                else: oc, xp = "TP", tp
                raw = (xp - ent) * side
                trades.append(dict(arm=arm, macro=name, day=day.day, split=day.split, side=side, R=Rr, outcome=oc, raw=raw,
                                   fill_utc=pd.Timestamp((ks + s0) * 5, unit="s", tz="UTC"), **{f"net_{cst}": raw - cst for cst in (0.45, 0.8)}))
    Tt = pd.DataFrame(trades); Tt.to_parquet(RESULTS / "t3_trades.parquet", index=False)
    for arm in ("MACRO", "CTRL_A"):
        for s in ("TRAIN", "TEST"):
            q = Tt[(Tt.arm == arm) & (Tt.split == s)]
            print(f"{arm} {s}: n={len(q)} WR={100*(q.raw>0).mean():.1f}% PF@0.45={pf(q['net_0.45']):.3f} PF@0.80={pf(q['net_0.8']):.3f} pts@0.80={q['net_0.8'].sum():+.1f} meanR@0.80={(q['net_0.8']/q.R).mean():+.3f}")
