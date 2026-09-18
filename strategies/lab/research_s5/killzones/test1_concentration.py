"""test1_concentration.py -- share of delivery events inside ICT windows vs clock share,
with a window-placement permutation null (PROTOCOL Test 1)."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np, pandas as pd
from common import *

N_PERM = 2000
rng = np.random.default_rng(20260918)
E = pd.read_parquet(RESULTS / "events.parquet")
DS = pd.read_parquet(RESULTS / "days.parquet")
E["minute"] = np.clip(E.tdm.astype(int), 0, DAY_MIN - 1)
REOPEN_EXCL = 35  # first 30 min after the 18:05 reopen

KINDS = {"H": ["H"], "L": ["L"], "HL": ["H", "L"], "X60": ["X60"], "X300": ["X300"],
         "BURST": ["BURST"], "BURSTREL": ["BURSTREL"]}
SETS = ["killzones", "macros", "silver_bullet", "calib_0800_1159", "reopen_15", "reopen_30"]


def perm_null(wins, counts, n=N_PERM):
    """random independent starts per window (same placement for all days); returns ratios."""
    total = counts.sum()
    out = np.empty(n)
    durs = [b - a for _, a, b in wins]
    for k in range(n):
        cov = np.zeros(DAY_MIN, bool)
        for d in durs:
            s = rng.integers(0, DAY_MIN)
            ks = (s + np.arange(d)) % DAY_MIN
            cov[ks] = True
        out[k] = (counts[cov].sum() / total) / (cov.sum() / DAY_MIN)
    return out


rows = []
for split in ("ALL", "TRAIN", "TEST"):
    Es = E if split == "ALL" else E[E.split == split]
    for excl in (False, True):
        Ex = Es[Es.minute >= REOPEN_EXCL] if excl else Es
        for kname, klist in KINDS.items():
            ek = Ex[Ex.kind.isin(klist)]
            counts = np.bincount(ek.minute.values, minlength=DAY_MIN).astype(float)
            total = counts.sum()
            if total == 0:
                continue
            # per-day mean of shares (check on the pooled convention)
            for sname in SETS:
                wins = windows_tdm(sname)
                per = list(wins) + ([("UNION", None, None)] if len(wins) > 1 else [])
                for w in per:
                    ws = wins if w[0] == "UNION" else [w]
                    cov = union_cov(ws)
                    clock = cov.sum() / DAY_MIN
                    share = counts[cov].sum() / total
                    ratio = share / clock
                    perday = ek.groupby("day").apply(lambda x: cov[x.minute.values].mean(), include_groups=False).mean()
                    r = dict(split=split, reopen_excluded=excl, kind=kname, set=sname, window=w[0],
                             n_events=int(total), clock_share=round(clock, 4), share=round(share, 4),
                             share_dayavg=round(float(perday), 4), ratio=round(ratio, 3))
                    is_union = (w[0] == "UNION") or len(wins) == 1
                    if not excl and kname in ("HL", "X60", "X300", "BURST", "BURSTREL") and sname in ("killzones", "macros", "silver_bullet") \
                            and (is_union or split == "ALL"):
                        null = perm_null(ws, counts, N_PERM if is_union else 500)
                        r.update(null_mean=round(null.mean(), 3), null_sd=round(null.std(), 3),
                                 z=round((ratio - null.mean()) / null.std(), 2),
                                 p_one_sided=round(float((null >= ratio).mean()), 4),
                                 primary=bool(is_union))
                        if is_union:   # day-bootstrap CI of the observed ratio (sampling error, not placement)
                            dm = ek.groupby("day").agg(n=("minute", "size"), k=("minute", lambda m: int(cov[m.values].sum())))
                            nb, kb = dm.n.values, dm.k.values
                            bs = np.array([kb[i].sum() / nb[i].sum() for i in (rng.integers(0, len(nb), len(nb)) for _ in range(500))]) / clock
                            r.update(ci_lo=round(float(np.percentile(bs, 2.5)), 3), ci_hi=round(float(np.percentile(bs, 97.5)), 3))
                    rows.append(r)
R = pd.DataFrame(rows)
R.to_csv(RESULTS / "t1_concentration.csv", index=False)

pd.set_option("display.width", 250)
print("=== Test 1: concentration of delivery events (pooled share / clock share) ===")
print(f"events: {E.kind.value_counts().to_dict()}  days: {E.day.nunique()}  (TRAIN {E[E.split=='TRAIN'].day.nunique()}, TEST {E[E.split=='TEST'].day.nunique()})")
cal = R[(R.set == "calib_0800_1159") & (R.kind == "HL") & (~R.reopen_excluded)]
print("\n--- calibration: 08:00-11:59 NY block, daily H/L (Session-Timing note: 29.9% vs 17.4% = 1.72x) ---")
print(cal[["split", "n_events", "clock_share", "share", "share_dayavg", "ratio"]].to_string(index=False))
ro = R[(R.set.isin(["reopen_15", "reopen_30"])) & (R.kind == "HL") & (~R.reopen_excluded)]
print("\n--- reopen artefact rows, daily H/L (note: 6.02x / 4.08x at 15 / 30 min) ---")
print(ro[["split", "window", "share", "clock_share", "ratio"]].to_string(index=False))

for sname in ("killzones", "macros", "silver_bullet"):
    print(f"\n--- {sname}: per window and union, ALL days, reopen included ---")
    x = R[(R.set == sname) & (R.split == "ALL") & (~R.reopen_excluded) & (R.kind.isin(["HL", "X60", "X300", "BURST", "BURSTREL"]))]
    print(x.pivot(index="window", columns="kind", values="ratio").to_string())
print("\n=== primary: UNION ratio with permutation null (independent random window starts, 2000 draws) ===")
prim = R[R.z.notna() & (R.primary == True)]
print(prim[["split", "kind", "set", "window", "n_events", "clock_share", "share", "ratio", "ci_lo", "ci_hi", "null_mean", "null_sd", "z", "p_one_sided"]].to_string(index=False))
print("\n=== exploratory (not pre-registered claims): per-window placement null, ALL days, 500 draws ===")
ex = R[R.z.notna() & (R.primary == False)]
print(ex.pivot(index=["set", "window"], columns="kind", values="p_one_sided").to_string())
print("\n=== robustness: reopen (first 30 min) excluded, ALL ===")
x = R[(R.split == "ALL") & (R.reopen_excluded) & (R.window.isin(["UNION", "CAL_08_12"])) & (R.kind.isin(["HL", "X60", "X300", "BURST", "BURSTREL"]))]
print(x.pivot(index="set", columns="kind", values="ratio").to_string())

# descriptive: where in the day do extremes / bursts fall (30-min bins, NY clock)
def ny_label(m):
    hm = (m + SESSION_OPEN_MIN) % 1440
    return f"{hm//60:02d}:{hm%60:02d}"
E["bin30"] = (E.minute // 30) * 30
hist = E.groupby(["kind", "bin30"]).size().unstack(0).fillna(0)
hist.index = [ny_label(m) for m in hist.index]
hist["HL%"] = (100 * (hist.H + hist.L) / (hist.H + hist.L).sum()).round(1)
hist["BURST%"] = (100 * hist.BURST / hist.BURST.sum()).round(1)
hist["X60%"] = (100 * hist.X60 / hist.X60.sum()).round(1)
print("\n=== 30-min NY bins: share of daily H/L, X60, bursts (uniform = 2.17% per bin) ===")
print(hist[["HL%", "X60%", "BURST%"]].to_string())
hist.to_csv(RESULTS / "t1_histogram_30min.csv")

print("\n=== descriptive: burst episodes vs the day's range ===")
v = DS[DS.valid & DS.in_window]
for s in ("TRAIN", "TEST"):
    q = v[v.split == s]
    print(f"{s}: days {len(q)}  bursts/day median {q.n_bursts.median():.0f}  burst clock share median {100*q.burst_clock_share.median():.1f}%  "
          f"sum|burst move|/day range median {q.burst_abs_over_range.median():.2f}  |net burst|/range median {q.burst_net_over_range.median():.2f}  day range median {q.day_range.median():.1f} pt")
