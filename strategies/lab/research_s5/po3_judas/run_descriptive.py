"""run_descriptive.py -- Q1 of PROTOCOL.md: per-day AMD/Judas table, extreme-location
rates, Judas-time distribution, sweep persistence, AR-vs-PD, Null A (label shuffle) and
Null B (within-day return shuffle).  Writes results/days_<anchor>.csv and
results/descriptive.txt (the raw output every number in REPORT.md is copied from).

    cd strategies && ../.venv/bin/python -m lab.research_s5.po3_judas.run_descriptive
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from lab.research_s5.po3_judas.po3_common import (H, RESULTS, S5, SPLIT, Tee, bucket, day_bounds,
                                                  first_breaches, london_open, trading_days, ts)

ANCHORS = ("A1", "A2", "A3")
N_PERM_A = 5000
N_PERM_B = 200
BUCKETS = ["prev-evening", "Asia", "London", "post-London", "NY late", "post-break"]


def build_days(s5: S5, days: list[pd.Timestamp], anchor: str) -> pd.DataFrame:
    rows, prev = [], None
    for date in days:
        d0 = int(date.timestamp()); lon0 = london_open(date, anchor); lon1 = lon0 + 3 * H
        day0, day1 = day_bounds(date, anchor)
        ia0, il0, il1, i16 = s5.idx(d0), s5.idx(lon0), s5.idx(lon1), s5.idx(d0 + 16 * H)
        j0, j1 = s5.idx(day0), s5.idx(day1)
        ar_hi, ar_lo = float(s5.h[ia0:il0].max()), float(s5.l[ia0:il0].min())
        p07 = float(s5.o[il0]) if s5.t[il0] - lon0 < 600 else np.nan
        close = float(s5.c[j1 - 1])
        ih, il_ = j0 + int(np.argmax(s5.h[j0:j1])), j0 + int(np.argmin(s5.l[j0:j1]))
        r = dict(date=date.strftime("%Y-%m-%d"), anchor=anchor, ar_hi=ar_hi, ar_lo=ar_lo, ar_range=ar_hi - ar_lo,
                 p07=p07, close=close, direction=("UP" if close > p07 else "DOWN" if close < p07 else "FLAT"),
                 day_hi=float(s5.h[ih]), day_lo=float(s5.l[il_]), t_hi=int(s5.t[ih]), t_lo=int(s5.t[il_]),
                 hi_bucket=bucket(int(s5.t[ih]), d0, anchor), lo_bucket=bucket(int(s5.t[il_]), d0, anchor),
                 hi_min_after_lon=(s5.t[ih] - lon0) / 60.0, lo_min_after_lon=(s5.t[il_] - lon0) / 60.0,
                 lo_in_london=bool(lon0 <= s5.t[il_] < lon1), hi_in_london=bool(lon0 <= s5.t[ih] < lon1),
                 lo_in_asia=bool(d0 <= s5.t[il_] < lon0), hi_in_asia=bool(d0 <= s5.t[ih] < lon0),
                 spread_med_lon=float(np.median(s5.spread[il0:il1])),
                 pdh=(prev["day_hi"] if prev else np.nan), pdl=(prev["day_lo"] if prev else np.nan))
        # first London sweep of the Asian range
        ev = first_breaches(s5, ar_lo, ar_hi, il0, il1, i16)
        r.update(n_sides_breached=len(ev), sweep_side=None, sweep_t_s=np.nan, sweep_depth=np.nan,
                 sweep_dur_s=np.nan, sweep_reclaimed=None, sweep_reclaim_by_10=None, sweep_extreme=np.nan,
                 sweep_beyond_pd=None)
        if ev:
            e = ev[0]
            r.update(sweep_side=e["side"], sweep_t_s=int(s5.t[e["breach_idx"]] - lon0), sweep_depth=e["depth"],
                     sweep_dur_s=e["duration"], sweep_reclaimed=e["reclaim_idx"] is not None,
                     sweep_reclaim_by_10=(e["reclaim_idx"] is not None and e["reclaim_idx"] < il1),
                     sweep_extreme=e["extreme"])
            if prev:
                r["sweep_beyond_pd"] = bool(e["extreme"] > prev["day_hi"]) if e["side"] == "UP" else bool(e["extreme"] < prev["day_lo"])
        # previous-day extreme breach in London, and which level first
        if prev:
            m = (s5.h[il0:il1] > prev["day_hi"]) | (s5.l[il0:il1] < prev["day_lo"])
            ipd = il0 + int(np.argmax(m)) if m.any() else None
            iar = ev[0]["breach_idx"] if ev else None
            if iar is None and ipd is None:
                which = "none"
            elif ipd is None:
                which = "AR only"
            elif iar is None:
                which = "PD only"
            elif iar < ipd:
                which = "both, AR first"
            elif ipd < iar:
                which = "both, PD first"
            else:
                which = "both, same bar"
        else:
            which = "n/a"
        r["level_first"] = which
        r.update(ix_j0=j0, ix_j1=j1, ix_il0=il0, ix_il1=il1)
        rows.append(r)
        prev = r
    return pd.DataFrame(rows)


def rate(mask_ext, cond):
    return float(mask_ext[cond].mean()) if cond.sum() else np.nan


def null_a(df: pd.DataFrame, rng: np.random.Generator):
    """label permutation: shuffle directions across days, recompute P(low in London | up)
    and P(high in London | down)."""
    up = (df.direction == "UP").to_numpy(); lo = df.lo_in_london.to_numpy(); hi = df.hi_in_london.to_numpy()
    dn = (df.direction == "DOWN").to_numpy()
    obs = (rate(lo, up), rate(hi, dn))
    n = len(df); outs = np.empty((N_PERM_A, 2))
    for k in range(N_PERM_A):
        p = rng.permutation(n)
        outs[k] = (rate(lo, up[p]), rate(hi, dn[p]))
    return obs, outs


def null_b(s5: S5, df: pd.DataFrame, rng: np.random.Generator):
    """within-day return shuffle from the London open to the day's close; returns the
    observed-on-closes rates and N_PERM_B null rates for (low|up) and (high|down)."""
    acc = np.zeros((N_PERM_B, 4))       # sums: low-in-lon & up, up count, high-in-lon & down, down count
    obs = np.zeros(4)
    for r in df.itertuples():
        j0, j1, il0, il1 = r.ix_j0, r.ix_j1, r.ix_il0, r.ix_il1
        kL = il1 - il0
        c = s5.c
        pre_c = c[j0:il0]
        pre_lo, pre_hi = float(pre_c.min()), float(pre_c.max())
        seg = c[il0 - 1:j1]                  # starts at the last pre-London close
        ret = np.diff(seg)
        N = len(ret)
        if N < kL + 10:
            continue
        base = float(seg[0])
        close = float(seg[-1]); p07 = float(s5.o[il0])
        up, dn = close > p07, close < p07
        # observed on closes
        path = seg[1:]
        lo_i, hi_i = int(np.argmin(path)), int(np.argmax(path))
        lo_in = lo_i < kL and path[lo_i] < pre_lo
        hi_in = hi_i < kL and path[hi_i] > pre_hi
        obs += (lo_in and up, up, hi_in and dn, dn)
        # permuted paths
        R = rng.permuted(np.broadcast_to(ret, (N_PERM_B, N)), axis=1)
        P = base + np.cumsum(R, axis=1)
        lo_is, hi_is = P.argmin(axis=1), P.argmax(axis=1)
        lo_v, hi_v = P.min(axis=1), P.max(axis=1)
        lo_in_p = (lo_is < kL) & (lo_v < pre_lo)
        hi_in_p = (hi_is < kL) & (hi_v > pre_hi)
        acc[:, 0] += lo_in_p & up; acc[:, 1] += up; acc[:, 2] += hi_in_p & dn; acc[:, 3] += dn
    obs_r = (obs[0] / obs[1], obs[2] / obs[3])
    null_r = np.column_stack((acc[:, 0] / acc[:, 1], acc[:, 2] / acc[:, 3]))
    return obs_r, null_r, int(obs[1]), int(obs[3])


def main() -> None:
    RESULTS.mkdir(exist_ok=True)
    say = Tee(RESULTS / "descriptive.txt")
    t0 = time.time()
    s5 = S5()
    say(f"S5 bars {s5.n:,}  {ts(s5.t[0])} -> {ts(s5.t[-1])}   load {time.time()-t0:.0f}s")
    days = trading_days(s5)
    say(f"trading days in window: {len(days)}  ({days[0].date()} .. {days[-1].date()});  TRAIN < {SPLIT} <= TEST")
    rng = np.random.default_rng(20260918)

    for anchor in ANCHORS:
        say(f"\n{'='*100}\nANCHOR {anchor}  " + {"A1": "UTC day, London 07:00-10:00 UTC (primary)",
                                                 "A2": "NY day (17:00 ET), London 07:00-10:00 UTC",
                                                 "A3": "UTC day, London 08:00-11:00 Europe/London (DST-aware)"}[anchor])
        df = build_days(s5, days, anchor)
        df.drop(columns=[c for c in df if c.startswith("ix_")]).to_csv(RESULTS / f"days_{anchor}.csv", index=False)
        n = len(df); up = df.direction == "UP"; dn = df.direction == "DOWN"
        say(f"days {n}: UP {up.sum()}  DOWN {dn.sum()}  FLAT {(~up & ~dn).sum()};  join: p07 present {df.p07.notna().mean()*100:.1f}%, "
            f"PD present {df.pdh.notna().mean()*100:.1f}%")
        say(f"Asian range (pts): median {df.ar_range.median():.2f}  IQR {df.ar_range.quantile(.25):.2f}-{df.ar_range.quantile(.75):.2f}; "
            f"London median spread {df.spread_med_lon.median():.3f}")

        say("\n-- 1. extreme-location by day direction (share of days; rows sum to 1)")
        for lab, col in (("LOW", "lo_bucket"), ("HIGH", "hi_bucket")):
            tab = pd.crosstab(df.direction, df[col], normalize="index").reindex(columns=BUCKETS, fill_value=0)
            say(f"{lab}:\n" + (tab * 100).round(1).to_string())
        say(f"\nP(day LOW in London | UP)   = {rate(df.lo_in_london.to_numpy(), up.to_numpy())*100:.1f}%   "
            f"(n_up={up.sum()});  unconditional P(low in London) = {df.lo_in_london.mean()*100:.1f}%;  "
            f"P(low in London | DOWN) = {rate(df.lo_in_london.to_numpy(), dn.to_numpy())*100:.1f}%")
        say(f"P(day HIGH in London | DOWN) = {rate(df.hi_in_london.to_numpy(), dn.to_numpy())*100:.1f}%   "
            f"(n_down={dn.sum()});  unconditional P(high in London) = {df.hi_in_london.mean()*100:.1f}%;  "
            f"P(high in London | UP) = {rate(df.hi_in_london.to_numpy(), up.to_numpy())*100:.1f}%")
        say(f"P(day LOW in Asia | UP) = {rate(df.lo_in_asia.to_numpy(), up.to_numpy())*100:.1f}%;  "
            f"P(day HIGH in Asia | DOWN) = {rate(df.hi_in_asia.to_numpy(), dn.to_numpy())*100:.1f}%")
        say(f"Judas proper (extreme in London AND opposite the close): {(up & df.lo_in_london).sum() + (dn & df.hi_in_london).sum()} of {n} days "
            f"= {((up & df.lo_in_london).sum() + (dn & df.hi_in_london).sum())/n*100:.1f}%")

        say("\n-- 2. Judas-extreme time (minutes after London open), 10-min bins")
        jt = pd.concat([df.loc[up & df.lo_in_london, "lo_min_after_lon"], df.loc[dn & df.hi_in_london, "hi_min_after_lon"]])
        say(f"n={len(jt)}  median {jt.median():.1f}  IQR {jt.quantile(.25):.1f}-{jt.quantile(.75):.1f}  mean {jt.mean():.1f}")
        hist, edges = np.histogram(jt, bins=np.arange(0, 181, 10))
        say("  " + "  ".join(f"{int(e):3d}-{int(e)+10:<3d}:{h:3d}" for e, h in zip(edges[:-1], hist)))

        say("\n-- 3. first London sweep of the Asian range: persistence")
        sw = df[df.sweep_side.notna()]
        say(f"days with a London breach of the AR: {len(sw)} of {n} ({len(sw)/n*100:.1f}%);  both sides breached in London: {(df.n_sides_breached==2).sum()}; "
            f"side UP {(sw.sweep_side=='UP').sum()}  DOWN {(sw.sweep_side=='DOWN').sum()}")
        say(f"breach time after open (min): median {sw.sweep_t_s.median()/60:.1f}  IQR {sw.sweep_t_s.quantile(.25)/60:.1f}-{sw.sweep_t_s.quantile(.75)/60:.1f}")
        say(f"reclaimed (close back inside) by 10:00: {sw.sweep_reclaim_by_10.mean()*100:.1f}%;  by 16:00: {sw.sweep_reclaimed.mean()*100:.1f}%;  never by 16:00: {(~sw.sweep_reclaimed.astype(bool)).sum()}")
        rec = sw[sw.sweep_reclaimed.astype(bool)]
        q = rec.sweep_dur_s.quantile([.1, .25, .5, .75, .9]).round(0).astype(int).to_dict()
        say(f"duration beyond the level, reclaimed sweeps (s): p10 {q[.1]} p25 {q[.25]} p50 {q[.5]} p75 {q[.75]} p90 {q[.9]}; "
            f"<=60s {(rec.sweep_dur_s<=60).mean()*100:.1f}%  <=300s {(rec.sweep_dur_s<=300).mean()*100:.1f}%  <=900s {(rec.sweep_dur_s<=900).mean()*100:.1f}%")
        say(f"depth beyond the level (pts): median {sw.sweep_depth.median():.2f}  IQR {sw.sweep_depth.quantile(.25):.2f}-{sw.sweep_depth.quantile(.75):.2f}; "
            f"'real' (depth > day's London median spread): {(sw.sweep_depth > sw.spread_med_lon).mean()*100:.1f}%  "
            f"(reclaimed-within-900s only: {(rec[rec.sweep_dur_s<=900].sweep_depth > rec[rec.sweep_dur_s<=900].spread_med_lon).mean()*100:.1f}%)")
        # does the sweep side predict the direction (the Judas claim, sweep-conditioned)
        for side, want in (("DOWN", "UP"), ("UP", "DOWN")):
            sub = sw[sw.sweep_side == side]
            subr = sub[sub.sweep_reclaim_by_10.astype(bool)]
            say(f"first sweep {side:4s}: n={len(sub)}  P(day closes {want} vs 07:00) = {(sub.direction==want).mean()*100:.1f}%;  "
                f"reclaimed-by-10:00 subset n={len(subr)}: {(subr.direction==want).mean()*100:.1f}%;  "
                f"P(day extreme on the swept side is IN London | sweep {side}) = "
                f"{(sub.lo_in_london if side=='DOWN' else sub.hi_in_london).mean()*100:.1f}%")

        say("\n-- 4. Asian range vs previous-day extreme (London window)")
        say(df.level_first.value_counts().to_string())
        say(f"first AR breach also exceeded the previous-day extreme on that side: {sw.sweep_beyond_pd.dropna().astype(bool).mean()*100:.1f}% of {sw.sweep_beyond_pd.notna().sum()}")

        say("\n-- 5. Null A: label permutation (shuffle day directions across days), "
            f"{N_PERM_A} perms")
        obs, outs = null_a(df, rng)
        for k, lab in enumerate(("P(low in London | UP)", "P(high in London | DOWN)")):
            o, v = obs[k], outs[:, k]
            p = float((v >= o).mean())
            say(f"{lab}: observed {o*100:.1f}%  null mean {v.mean()*100:.1f}%  95% band {np.percentile(v,2.5)*100:.1f}-{np.percentile(v,97.5)*100:.1f}%  "
                f"p(null >= obs) = {p:.4f}")

        say(f"\n-- 6. Null B: within-day return shuffle (London open -> day close), {N_PERM_B} perms/day, on closes")
        tb = time.time()
        obs_r, null_r, n_up, n_dn = null_b(s5, df, rng)
        for k, lab, nn in ((0, "P(low in London | UP)", n_up), (1, "P(high in London | DOWN)", n_dn)):
            o, v = obs_r[k], null_r[:, k]
            p = float((v >= o).mean())
            say(f"{lab} (closes, n={nn}): observed {o*100:.1f}%  null mean {v.mean()*100:.1f}%  sd {v.std()*100:.2f}  "
                f"95% band {np.percentile(v,2.5)*100:.1f}-{np.percentile(v,97.5)*100:.1f}%  p(null >= obs) = {p:.3f}  "
                f"excess over null = {(o - v.mean())*100:+.1f} pp")
        say(f"(null B {time.time()-tb:.0f}s)")

        # TRAIN / TEST split of the headline rate
        cut = pd.Timestamp(SPLIT)
        for lab, m in (("TRAIN", pd.to_datetime(df.date) < cut), ("TEST", pd.to_datetime(df.date) >= cut)):
            sub = df[m]; u = sub.direction == "UP"; d = sub.direction == "DOWN"
            say(f"{lab}: days {len(sub)}  P(low in London | UP) {rate(sub.lo_in_london.to_numpy(), u.to_numpy())*100:.1f}% (n_up {u.sum()})  "
                f"P(high in London | DOWN) {rate(sub.hi_in_london.to_numpy(), d.to_numpy())*100:.1f}% (n_down {d.sum()})  "
                f"unconditional low/high in London {sub.lo_in_london.mean()*100:.1f}% / {sub.hi_in_london.mean()*100:.1f}%")
    say(f"\ntotal {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
