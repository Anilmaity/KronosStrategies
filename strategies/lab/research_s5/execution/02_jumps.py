"""02_jumps.py -- 5-second close-to-close jump distribution by hour; gap-through probability
P(J > d) for d in {1,2,3,5,10}; expected slippage per stop-out (level-crossing estimator
E[J^2]/(2E[J]) and E[J-d | J>d]). PROTOCOL.md §Definitions (Jump / Gap-through / Slippage 1)."""
from __future__ import annotations

import numpy as np
import pandas as pd

import common
from common import RES, Tee, D_LIST

out = Tee(RES / "02_jumps.txt")
s5 = common.load()
dt = np.diff(s5.ts)
J = np.abs(np.diff(s5.c))
on_grid = dt == 5
hr = s5.hour[1:]; wd = s5.wd[1:]; t = s5.t[1:]
out(f"consecutive bar pairs: {len(J):,}; exactly 5 s apart: {on_grid.sum():,} ({100*on_grid.mean():.2f} %); "
    f"gap pairs (dt > 5 s): {(~on_grid).sum():,} -- excluded from the 5-s jump statistics")
gap = ~on_grid
out(f"gap pairs by dt: <=60 s {((dt>5)&(dt<=60)).sum():,}  1-10 min {((dt>60)&(dt<=600)).sum():,}  >10 min {(dt>600).sum():,}")
out(f"jump across gaps <= 60 s: p50 {np.median(J[(dt>5)&(dt<=60)]):.3f} p99 {np.quantile(J[(dt>5)&(dt<=60)],.99):.3f}; "
    f"across the daily break (dt 50-70 min, weekdays): p50 {np.median(J[(dt>3000)&(dt<4200)]):.3f} p95 {np.quantile(J[(dt>3000)&(dt<4200)],.95):.3f} max {J[(dt>3000)&(dt<4200)].max():.2f} (n {((dt>3000)&(dt<4200)).sum()})")
wk = dt > 40 * 3600
out(f"weekend/holiday gaps (> 40 h): n {wk.sum()}; |open gap| p50 {np.median(J[wk]):.2f} p90 {np.quantile(J[wk],.9):.2f} max {J[wk].max():.2f}")

Jg, hg, wg, tg = J[on_grid], hr[on_grid], wd[on_grid], t[on_grid]
split = tg < common.SPLIT

def stats(v):
    v = np.asarray(v)
    d = dict(n=len(v), mean=v.mean(), p50=np.median(v), p90=np.quantile(v, .9), p99=np.quantile(v, .99),
             p999=np.quantile(v, .999), max=v.max(), share_zero=(v == 0).mean(),
             E_J2_over_2EJ=(v ** 2).mean() / (2 * v.mean()))
    for dd in D_LIST:
        m = v > dd
        d[f"P(J>{dd})"] = m.mean()
        d[f"E[J-{dd}|J>{dd}]"] = (v[m] - dd).mean() if m.any() else np.nan
        d[f"per_hour_count_J>{dd}"] = m.sum() / (len(v) / 720.0)   # expected count per trading hour (720 bars)
    return d

rows = [dict(hour=h, **stats(Jg[hg == h])) for h in range(24)]
byh = pd.DataFrame(rows)
byh.to_csv(RES / "jumps_hour.csv", index=False, float_format="%.6f")
out("\n=== 5-s |close-to-close| jump by UTC hour ===")
out(byh[["hour", "n", "mean", "p50", "p90", "p99", "p999", "max", "share_zero", "E_J2_over_2EJ"]].round(4).to_string(index=False))
out("\n=== gap-through probability per 5-s bar, P(J > d), by hour (x1e-3) ===")
out((byh[["hour"] + [f"P(J>{d})" for d in D_LIST]].set_index("hour") * 1e3).round(3).to_string())
out("\n=== expected count of jumps > d per hour of trading (720 bars) ===")
out(byh[["hour"] + [f"per_hour_count_J>{d}" for d in D_LIST]].set_index("hour").round(3).to_string())
out("\n=== expected overshoot given the jump exceeds d, E[J-d | J>d] (pts) ===")
out(byh[["hour"] + [f"E[J-{d}|J>{d}]" for d in D_LIST]].set_index("hour").round(3).to_string())

# level-crossing slippage per stop-out: E[J^2]/(2E[J]) -- by hour, and TRAIN vs TEST, and with the top 0.1 % winsorised
def lc(v):
    return (v ** 2).mean() / (2 * v.mean())
rows = []
for h in range(24):
    m = hg == h
    v = Jg[m]; vt = Jg[m & split]; vs = Jg[m & ~split]
    w = np.minimum(v, np.quantile(v, .999))
    rows.append(dict(hour=h, slip_all=lc(v), slip_train=lc(vt), slip_test=lc(vs), slip_wins999=lc(w),
                     slip_excl_top10=lc(np.sort(v)[:-10])))
lcd = pd.DataFrame(rows)
lcd.to_csv(RES / "jumps_slippage_hour.csv", index=False, float_format="%.4f")
out("\n=== level-crossing slippage per stop-out E[J^2]/(2E[J]) by hour: all / TRAIN / TEST / winsorised p99.9 / excluding the 10 largest ===")
out(lcd.round(3).to_string(index=False))
core = (hg >= 7) & (hg <= 16)
out(f"\n07-16 UTC pooled: slip {lc(Jg[core]):.3f} (TRAIN {lc(Jg[core&split]):.3f}, TEST {lc(Jg[core&~split]):.3f}); "
    f"winsorised {lc(np.minimum(Jg[core], np.quantile(Jg[core],.999))):.3f}; mean J {Jg[core].mean():.3f}; P(J>1) {1e3*(Jg[core]>1).mean():.3f}e-3")
out(f"all hours pooled: slip {lc(Jg):.3f}; mean J {Jg.mean():.3f}")

# by month (drift) for the core hours
rows = []
mo = tg.astype("datetime64[M]")
for m in np.unique(mo):
    v = Jg[(mo == m) & core]
    rows.append(dict(month=str(m), mean=v.mean(), p99=np.quantile(v, .99), slip=lc(v), P_J_gt_1=(v > 1).mean(), P_J_gt_3=(v > 3).mean()))
bm = pd.DataFrame(rows); bm.to_csv(RES / "jumps_month.csv", index=False, float_format="%.6f")
out("\n=== 07-16 UTC jumps by month ===")
out(bm.round(4).to_string(index=False))

# scheduled-release minutes vs the rest of the hour (New York clock)
tny = pd.DatetimeIndex(tg).tz_localize("UTC").tz_convert("America/New_York")
nym = (tny.hour * 60 + tny.minute).to_numpy(); nyw = tny.weekday.to_numpy()
rel = (nyw <= 4) & ((np.abs(nym - (8 * 60 + 30)) <= 1) | (np.abs(nym - 10 * 60) <= 1) | (np.abs(nym - 14 * 60) <= 1))
out(f"\nrelease minutes (08:30/10:00/14:00 ET +-1 min, Mon-Fri): n {rel.sum():,}; mean J {Jg[rel].mean():.3f} vs other core-hour bars {Jg[core&~rel].mean():.3f}; "
    f"P(J>1) {1e3*(Jg[rel]>1).mean():.2f}e-3 vs {1e3*(Jg[core&~rel]>1).mean():.3f}e-3; slip per stop-out {lc(Jg[rel]):.3f} vs {lc(Jg[core&~rel]):.3f}")
out(f"share of all core-hour jumps > 3 pt that occur in release minutes: {100*(rel&core&(Jg>3)).sum()/(core&(Jg>3)).sum():.1f} % "
    f"(release minutes are {100*(rel&core).sum()/core.sum():.2f} % of core bars)")
out("done")
