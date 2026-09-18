"""01_spread.py -- spread distribution: hour x weekday, month drift, daily break, Sunday open,
top-1 % tail, widening events (> 3x hour median) and their classification. PROTOCOL.md §Definitions."""
from __future__ import annotations

import numpy as np
import pandas as pd

import common
from common import RES, Tee

out = Tee(RES / "01_spread.txt")
s5 = common.load()
sp, hr, wd = s5.spread, s5.hour, s5.wd
out(f"S5 window bars: {len(s5):,}  {s5.t[0]} -> {s5.t[-1]}  (2025-12-25 23:00-23:15 dropped)")


def q(v, ps=(0.5, 0.95, 0.99)):
    return {f"p{int(p*100)}": float(np.quantile(v, p)) for p in ps}


# ---- 1. hour x weekday -----------------------------------------------------------------
rows = []
for h in range(24):
    for w in range(7):
        m = (hr == h) & (wd == w)
        if m.sum() < 100:
            continue
        v = sp[m]
        rows.append(dict(hour=h, weekday=w, n=int(m.sum()), mean=v.mean(), **q(v)))
hw = pd.DataFrame(rows)
hw.to_csv(RES / "spread_hour_weekday.csv", index=False, float_format="%.4f")
out("\n=== spread median by UTC hour (rows) x weekday (cols, Mon=0) ===")
out(hw.pivot(index="hour", columns="weekday", values="p50").round(3).to_string())
out("\n=== spread p95 by UTC hour x weekday ===")
out(hw.pivot(index="hour", columns="weekday", values="p95").round(2).to_string())
byh = pd.DataFrame([dict(hour=h, n=int((hr == h).sum()), mean=sp[hr == h].mean(), **q(sp[hr == h], (0.5, 0.95, 0.99, 0.999)))
                    for h in range(24)])
byh.to_csv(RES / "spread_hour.csv", index=False, float_format="%.4f")
out("\n=== spread by UTC hour (all weekdays) ===")
out(byh.round(3).to_string(index=False))

# ---- 2. by month: is it drifting? ---------------------------------------------------------
months = np.unique(s5.month)
rows = []
core = (hr >= 7) & (hr <= 16)
for m in months:
    mm = s5.month == m
    v = sp[mm]; vc = sp[mm & core]
    rows.append(dict(month=str(m), n=int(mm.sum()), mean=v.mean(), **q(v),
                     core_p50=float(np.median(vc)), core_p95=float(np.quantile(vc, .95)),
                     price=float(np.median(s5.c[mm]))))
bym = pd.DataFrame(rows)
bym["p50_bp"] = 1e4 * bym.p50 / bym.price          # spread in basis points of price
bym.to_csv(RES / "spread_month.csv", index=False, float_format="%.4f")
out("\n=== spread by month (p50 / p95 / core 07-16 p50 / median price / p50 in bp of price) ===")
out(bym[["month", "n", "p50", "p95", "core_p50", "core_p95", "price", "p50_bp"]].round(3).to_string(index=False))
x = np.arange(len(bym))
sl_pts = np.polyfit(x, bym.p50, 1)[0]; sl_bp = np.polyfit(x, bym.p50_bp, 1)[0]
r_pts = np.corrcoef(x, bym.p50)[0, 1]; r_bp = np.corrcoef(x, bym.p50_bp)[0, 1]
out(f"\ndrift: monthly p50 slope {sl_pts*12:+.3f} pts/yr (corr {r_pts:+.2f}); in bp of price "
    f"{sl_bp*12:+.3f} bp/yr (corr {r_bp:+.2f}); first 6 mo p50 mean {bym.p50[:6].mean():.3f}, last 6 mo {bym.p50[-6:].mean():.3f}")
tr = bym[bym.month < "2025-12"]; te = bym[bym.month >= "2025-12"]
out(f"TRAIN months p50 mean {tr.p50.mean():.3f} (p95 {tr.p95.mean():.3f})   TEST {te.p50.mean():.3f} (p95 {te.p95.mean():.3f})")

# ---- 3. around the daily break (20:30-22:30 UTC, weekdays) and the Sunday open ------------
mod = (s5.ts % 86400)
minute_of_day = mod // 60
rows = []
sel = (wd <= 4) & (minute_of_day >= 20 * 60 + 30) & (minute_of_day < 22 * 60 + 30)
for mn in range(20 * 60 + 30, 22 * 60 + 30):
    m = sel & (minute_of_day == mn)
    if m.sum() == 0:
        rows.append(dict(utc=f"{mn//60:02d}:{mn%60:02d}", n=0)); continue
    v = sp[m]
    rows.append(dict(utc=f"{mn//60:02d}:{mn%60:02d}", n=int(m.sum()), **q(v)))
brk = pd.DataFrame(rows)
brk.to_csv(RES / "spread_daily_break.csv", index=False, float_format="%.4f")
out("\n=== spread around the daily break, by UTC minute (Mon-Fri; n = bars) -- every 5th minute shown ===")
out(brk.iloc[::5].round(3).to_string(index=False))
out("first 12 minutes after the reopen:")
out(brk[(brk.utc >= "22:00") & (brk.utc < "22:12")].round(3).to_string(index=False))
# Sunday open: weekday 6, from 22:00
rows = []
for mn in range(22 * 60, 24 * 60):
    m = (wd == 6) & (minute_of_day == mn)
    if m.sum() == 0:
        continue
    rows.append(dict(utc=f"{mn//60:02d}:{mn%60:02d}", n=int(m.sum()), **q(sp[m])))
sun = pd.DataFrame(rows)
sun.to_csv(RES / "spread_sunday_open.csv", index=False, float_format="%.4f")
out("\n=== Sunday open, by UTC minute (first 15 minutes, then every 15) ===")
out(pd.concat([sun.head(15), sun.iloc[15::15]]).round(3).to_string(index=False))
out(f"Sunday 22:00-23:00 p50 {np.median(sp[(wd==6)&(hr==22)]):.3f} p95 {np.quantile(sp[(wd==6)&(hr==22)],.95):.3f}; "
    f"Mon-Fri 22:00-23:00 p50 {np.median(sp[(wd<=4)&(hr==22)]):.3f} p95 {np.quantile(sp[(wd<=4)&(hr==22)],.95):.3f}")

# ---- 4. top-1 % tail ------------------------------------------------------------------------
p99 = float(np.quantile(sp, .99))
tail = sp >= p99
out(f"\n=== tail: p99 {p99:.3f}  p99.9 {np.quantile(sp,.999):.3f}  max {sp.max():.3f} at {s5.t[sp.argmax()]} ===")
th = pd.Series(hr[tail]).value_counts().sort_index()
out("top-1 % bars by UTC hour (share of the tail, %):")
out((100 * th / th.sum()).round(1).to_string())
tw = pd.Series(wd[tail]).value_counts().sort_index()
out("top-1 % bars by weekday (share, %): " + str((100 * tw / tw.sum()).round(1).to_dict()))
out(f"share of the tail that falls 21:00-23:59 UTC: {100*np.isin(hr[tail],[21,22,23]).mean():.1f} %; "
    f"07-16 UTC: {100*((hr[tail]>=7)&(hr[tail]<=16)).mean():.1f} %")

# ---- 5. widening events ---------------------------------------------------------------------
med_h = np.array([np.median(sp[hr == h]) for h in range(24)])
wide = sp > 3 * med_h[hr]
idx = np.flatnonzero(wide)
out(f"\n=== widening bars (spread > 3x hour median): {len(idx):,} of {len(s5):,} ({100*len(idx)/len(s5):.3f} %) ===")
# merge runs with gaps <= 60 s between qualifying bars
ts = s5.ts
brk_pts = np.flatnonzero(np.diff(ts[idx]) > 60) + 1
starts = np.r_[0, brk_pts]; ends = np.r_[brk_pts, len(idx)]
ev = []
for a, b in zip(starts, ends):
    ii = idx[a:b]
    t0 = s5.t[ii[0]]; t1 = s5.t[ii[-1]]
    ev.append(dict(start=t0, end=t1, dur_s=int(ts[ii[-1]] - ts[ii[0]] + 5), n_bars=len(ii),
                   max_spread=float(sp[ii].max()), mean_spread=float(sp[ii].mean()),
                   hour_median=float(med_h[hr[ii[0]]]),
                   abs_move=float(abs(s5.c[ii[-1]] - s5.c[max(ii[0]-1, 0)]))))
ev = pd.DataFrame(ev)
st = pd.to_datetime(ev.start).dt.tz_localize("UTC")
ny = st.dt.tz_convert("America/New_York")
ev["ny_time"] = ny.dt.strftime("%H:%M")
ev["ny_wd"] = ny.dt.weekday
ev["utc_hour"] = st.dt.hour
mins = ny.dt.hour * 60 + ny.dt.minute + st.dt.second / 60
def near(slot_min):
    return (mins >= slot_min - 1) & (mins <= slot_min + 5)
sched = (ev.ny_wd <= 4) & (near(8 * 60 + 30) | near(10 * 60) | near(14 * 60))
umin = st.dt.hour * 60 + st.dt.minute
brk_cls = ((umin >= 20 * 60 + 55) & (umin <= 22 * 60 + 10)) | ((st.dt.weekday == 6) & (umin >= 21 * 60 + 55) & (umin < 23 * 60))
ev["cls"] = np.where(sched, "scheduled", np.where(brk_cls, "break", "unscheduled"))
# FOMC derivation. PROTOCOL CHANGE (recorded 2026-09-18 after the first run): the pre-registered
# "top decile of 14:00 ET magnitude" filter is void because the feed caps the quoted spread at
# 5.00 / 10.00 (133 events tie at exactly 5.00), so the decile is a tie. Replacement rule, data-only:
# fomc = a Wednesday event starting within [-1, +1] min of 14:00 ET; the 14:25-14:45 ET presser
# event is reported as a flag, not used as a filter. The day list and its spacing are printed so
# the reader can judge it against an 8-meetings-a-year calendar.
ev["ny_date"] = ny.dt.date
presser_days = set(ev.loc[(mins >= 14 * 60 + 25) & (mins <= 14 * 60 + 45), "ny_date"])
fomc = (ev.ny_wd == 2) & (mins >= 14 * 60 - 1) & (mins <= 14 * 60 + 1)
ev.loc[fomc, "cls"] = "fomc"
ev["presser"] = ev.ny_date.isin(presser_days)
thr = float("nan")
ev.to_csv(RES / "events.csv", index=False, float_format="%.3f")
out(f"events (runs merged at <= 60 s): {len(ev):,}")
g = ev.groupby("cls").agg(n=("dur_s", "size"), dur_p50=("dur_s", "median"), dur_p90=("dur_s", lambda v: v.quantile(.9)),
                          dur_max=("dur_s", "max"), maxsp_p50=("max_spread", "median"), maxsp_p90=("max_spread", lambda v: v.quantile(.9)),
                          bars=("n_bars", "sum"))
g["share_of_wide_bars_%"] = (100 * g.bars / g.bars.sum()).round(1)
out(g.round(2).to_string())
out("\nevents by UTC hour of start (count) and class:")
out(pd.crosstab(ev.utc_hour, ev.cls).to_string())
out("\nduration distribution (s), all events: " + str({f"p{p}": int(np.quantile(ev.dur_s, p/100)) for p in (50, 75, 90, 95, 99)}) + f" max {ev.dur_s.max()}")
long_ev = ev[ev.dur_s >= 300].sort_values("dur_s", ascending=False)
out(f"events >= 5 min: {len(long_ev)}; top 15:")
out(long_ev[["start", "dur_s", "max_spread", "abs_move", "cls"]].head(15).to_string(index=False))
fd = ev[fomc].sort_values("start")
fd = fd.drop_duplicates("ny_date")
out(f"\nderived FOMC-like days ({len(fd)}; Wednesday burst starting 13:59-14:01 ET):")
fd_days = pd.to_datetime(fd.ny_date.astype(str))
out(pd.DataFrame({"ny_date": fd.ny_date.astype(str).values, "max_spread": fd.max_spread.round(2).values,
                  "dur_s": fd.dur_s.values, "abs_move": fd.abs_move.round(1).values, "presser": fd.presser.values,
                  "gap_days": fd_days.diff().dt.days.values}).to_string(index=False))
out("scheduled-slot share by slot (ET): " + str(pd.Series(np.select([near(8*60+30), near(10*60), near(14*60)], ["08:30", "10:00", "14:00"], "-")[(ev.cls=="scheduled").to_numpy()]).value_counts().to_dict()))
# how much of the 07-16 UTC widening is scheduled?
core_ev = ev[(ev.utc_hour >= 7) & (ev.utc_hour <= 16)]
out(f"07-16 UTC events: {len(core_ev)}; by class: {core_ev.cls.value_counts().to_dict()}; wide-bar share by class: "
    + str((100*core_ev.groupby('cls').n_bars.sum()/core_ev.n_bars.sum()).round(1).to_dict()))
out("done")
