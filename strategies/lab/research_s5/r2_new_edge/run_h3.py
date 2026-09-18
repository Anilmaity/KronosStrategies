"""run_h3.py -- H3: 10:00 ET release-range straddle, direction-agnostic (PROTOCOL §3).

Range 09:45-10:00 ET (primary) or 09:30-10:00 (variant); OCO stop orders at both edges
filled on the quote during 10:00-11:10 ET; stop = opposite edge; target = M x range
(M in {1, 2}, TRAIN-selected); time exit 12:00 ET. Controls: C-NOON (same rule at 12:00 ET)
and C-RAND (geometry-matched random). Output: results/h3_output.txt, results/h3_*.parquet.
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from lab.research_s5.r2_new_edge import common as C
from lab.research_s5.po3_judas import po3_common as P

NY = "America/New_York"
M_GRID = (1.0, 2.0)


class NYClock:
    """Per-bar NY wall clock: day ordinal, seconds of day, and a monotone key."""

    def __init__(self, s5: C.S5):
        idx = pd.DatetimeIndex(pd.to_datetime(s5.t, unit="s", utc=True)).tz_convert(NY)
        self.sod = (idx.hour * 3600 + idx.minute * 60 + idx.second).to_numpy(np.int64)
        naive = idx.tz_localize(None)
        self.day = (naive.normalize().asi8 // (86400 * 10**9)).astype(np.int64)   # NY date ordinal
        self.wd = naive.weekday.to_numpy()
        self.key = self.day * 86400 + self.sod
        assert np.all(np.diff(self.key) >= 0), "NY key not monotone"
        self.anchor = s5.t - self.sod                                          # epoch of 00:00 ET that day

    def idx(self, day: int, hms: str) -> int:
        h, m, s = (int(x) for x in hms.split(":"))
        return int(np.searchsorted(self.key, day * 86400 + h * 3600 + m * 60 + s, "left"))


def ny_days(s5: C.S5, ck: NYClock) -> list[int]:
    days = np.unique(ck.day)
    out = []
    for d in days:
        a, b = ck.idx(d, "09:00:00"), ck.idx(d, "12:00:00")
        if b - a < 1080:
            continue
        if ck.wd[a] >= 5:
            continue
        out.append(int(d))
    return out


def run_rule(s5: C.S5, ck: NYClock, days, r0: str, r1: str, e0: str, e1: str, x1: str, M: float) -> pd.DataFrame:
    rows = []
    st = dict(days=0, no_range=0, no_trigger=0, both_same_bar=0, beyond_target=0, gap_through=0, no_bars=0, bad_window=0)
    for d in days:
        st["days"] += 1
        a0, a1 = ck.idx(d, r0), ck.idx(d, r1)
        if a1 - a0 < 60:
            st["no_range"] += 1; continue
        r_hi, r_lo = float(s5.h[a0:a1].max()), float(s5.l[a0:a1].min())
        rng = r_hi - r_lo
        sp_med = float(np.median(s5.spread[a0:a1]))
        if rng < 2 * sp_med:
            st["no_range"] += 1; continue
        b0, b1 = ck.idx(d, e0), ck.idx(d, e1)
        if b1 - b0 < 12:
            st["no_bars"] += 1; continue
        up = s5.ask_c[b0:b1] > r_hi
        dn = s5.bid_c[b0:b1] < r_lo
        iu = int(np.argmax(up)) if up.any() else None
        idn = int(np.argmax(dn)) if dn.any() else None
        if iu is None and idn is None:
            st["no_trigger"] += 1; continue
        if iu is not None and idn is not None and iu == idn:
            st["both_same_bar"] += 1; continue
        if idn is None or (iu is not None and iu < idn):
            side, e = "BUY", b0 + iu
        else:
            side, e = "SELL", b0 + idn
        long_ = side == "BUY"
        fill = float(s5.ask_c[e]) if long_ else float(s5.bid_c[e])
        sl = r_lo if long_ else r_hi
        tp = r_hi + M * rng if long_ else r_lo - M * rng
        if (long_ and fill >= tp) or ((not long_) and fill <= tp):
            st["beyond_target"] += 1; continue
        if (long_ and fill <= sl) or ((not long_) and fill >= sl):
            st["gap_through"] += 1; continue
        end = min(ck.idx(d, x1) - 1, s5.n - 1)
        r = C.walk_fast(s5, e, end, long_, sl, tp)
        if r is None:
            st["no_bars"] += 1; continue
        oc, x, px = r
        if C.crosses_bad(s5, e, x):
            st["bad_window"] += 1; continue
        rows.append(dict(day=d, anchor=int(ck.anchor[e]), side=side, M=M, entry_time=P.ts(s5.t[e]), fill_idx=e,
                         fill=fill, sl=sl, tp=tp, risk=abs(fill - sl), reward=abs(tp - fill),
                         sl_dist=abs(fill - sl), tp_dist=abs(tp - fill),
                         exit_deadline_off_s=int(s5.t[end] - s5.t[e]),
                         r_hi=r_hi, r_lo=r_lo, rng=rng, spread_e=float(s5.spread[e]),
                         trig_sod=int(ck.sod[e]), trig_min=(int(ck.sod[e]) - int(ck.sod[b0])) / 60.0,
                         slip=(fill - r_hi) if long_ else (r_lo - fill),
                         wd=int(ck.wd[e]), outcome=oc, exit_time=P.ts(s5.t[x]), exit_px=px,
                         raw_pts=C.raw_pts(long_, fill, px), hold_s=int(s5.t[x] - s5.t[e])))
    df = pd.DataFrame(rows)
    df.attrs["stats"] = st
    return df


def main() -> None:
    log = C.Tee(C.RESULTS / "h3_output.txt")
    t0 = time.time()
    s5 = C.S5()
    ck = NYClock(s5)
    days = ny_days(s5, ck)
    lo = int(pd.Timestamp("2024-09-01", tz="UTC").timestamp()); hi = int(pd.Timestamp("2026-09-18", tz="UTC").timestamp())
    days = [d for d in days if lo <= d * 86400 + 5 * 3600 < hi]
    log(f"S5 bars {s5.n:,}; NY trading days with >=50% coverage 09-12 ET: {len(days)}; prep {time.time()-t0:.0f}s")
    anchors = np.array(sorted({int(ck.anchor[ck.idx(d, '09:45:00')]) for d in days}), dtype=np.int64)

    # ---- grid on TRAIN ----------------------------------------------------------------
    log("\n=== H3 grid: range 09:45-10:00 ET, OCO 10:00-11:10, stop opposite edge, exit 12:00 ET ===")
    books, grid = {}, []
    for M in M_GRID:
        df = run_rule(s5, ck, days, "09:45:00", "10:00:00", "10:00:00", "11:10:00", "12:00:00", M)
        books[M] = df
        df.to_parquet(C.RESULTS / f"h3_trades_r15_M{M}.parquet", index=False)
        et = pd.to_datetime(df["entry_time"], utc=True)
        b = P.book(df[et < C.SPLIT], C.COSTS[1])
        grid.append(dict(M=M, n=len(df), train_n=b["n"], train_pf_070=b["pf"], train_pts=b["pts"], **df.attrs["stats"]))
        log(f"  M={M}: n={len(df)} TRAIN n={b['n']} PF@0.70={b['pf']} pts={b['pts']} WR={b['wr']}% | median risk {df.risk.median():.2f} "
            f"reward {df.reward.median():.2f} slip {df.slip.median():.2f} | stats {df.attrs['stats']}")
    pd.DataFrame(grid).to_csv(C.RESULTS / "h3_grid.csv", index=False)
    M_sel = float(max(M_GRID, key=lambda m: grid[M_GRID.index(m)]["train_pf_070"]))
    log(f"\nSELECTED on TRAIN PF@0.70: M={M_sel}")

    d0 = books[M_sel]
    log("\n--- anatomy (primary arm) ---")
    log(f"  trigger minute after 10:00: median {d0.trig_min.median():.1f} (IQR {d0.trig_min.quantile(.25):.1f}-{d0.trig_min.quantile(.75):.1f}); "
        f"share in the 10:00 minute {(d0.trig_min < 1).mean():.3f}")
    log(f"  range median {d0.rng.median():.2f}; risk median {d0.risk.median():.2f}; reward {d0.reward.median():.2f}; RR {(d0.reward/d0.risk).median():.2f}; "
        f"fill slip beyond the edge median {d0.slip.median():.2f} (p90 {d0.slip.quantile(.9):.2f}); spread at fill {d0.spread_e.median():.2f}")
    log(f"  side {d0.side.value_counts().to_dict()}; outcome {d0.outcome.value_counts().to_dict()}; hold median {d0.hold_s.median()/60:.1f} min")

    # ---- judged arms ------------------------------------------------------------------
    log("\n=== JUDGED ARMS (TEST read once) ===")
    C.book_line(d0, f"H3 primary r15 M={M_sel}", log)
    v30 = run_rule(s5, ck, days, "09:30:00", "10:00:00", "10:00:00", "11:10:00", "12:00:00", M_sel)
    v30.to_parquet(C.RESULTS / f"h3_trades_r30_M{M_sel}.parquet", index=False)
    log(f"  r30 stats {v30.attrs['stats']}")
    C.book_line(v30, f"H3 variant r30 M={M_sel}", log)
    log("\n--- other grid cell TEST @0.70 (record only) ---")
    for M, df in books.items():
        et = pd.to_datetime(df["entry_time"], utc=True)
        b = P.book(df[et >= C.SPLIT], C.COSTS[1])
        log(f"  M={M}: TEST n={b['n']} PF {b['pf']} pts {b['pts']}")

    # ---- splits -----------------------------------------------------------------------
    log("\n=== pre-declared splits of the primary arm (PF/pts @0.70) ===")
    p = d0.copy()
    C.split_lines(p, "side", "primary", log)
    p["weekday"] = p.wd.map({0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"})
    C.split_lines(p, "weekday", "primary", log)
    p["release_minute"] = np.where(p.trig_min < 1, "10:00 burst", "later")
    C.split_lines(p, "release_minute", "primary", log)
    p["year"] = pd.to_datetime(p.entry_time, utc=True).dt.year
    C.split_lines(p, "year", "primary", log)

    # ---- C-NOON control ---------------------------------------------------------------
    log("\n=== C-NOON control: range 11:45-12:00 ET, OCO 12:00-13:10, exit 14:00 ET ===")
    for M in (M_sel,):
        cn = run_rule(s5, ck, days, "11:45:00", "12:00:00", "12:00:00", "13:10:00", "14:00:00", M)
        cn.to_parquet(C.RESULTS / f"h3_ctrl_noon_M{M}.parquet", index=False)
        log(f"  C-NOON stats {cn.attrs['stats']}; range median {cn.rng.median():.2f} vs primary {d0.rng.median():.2f}")
        C.book_line(cn, f"C-NOON r15 M={M}", log)

    # ---- C-RAND -----------------------------------------------------------------------
    log("\n=== C-RAND control: 200 geometry-matched random draws per trade (same NY clock minute, day +-30) ===")
    for name, df in (("primary", d0), ("r30", v30)):
        if not len(df):
            continue
        tr = df.copy(); tr["exit_off_s"] = tr["exit_deadline_off_s"]
        ctrl = C.crand(s5, tr, anchors, n_draws=200)
        ctrl.to_parquet(C.RESULTS / f"h3_ctrl_rand_{name}.parquet", index=False)
        log(f"  {name}: {len(ctrl)} control rows, dropped {ctrl.attrs['dropped']}")
        et = pd.to_datetime(df["entry_time"], utc=True)
        for half, rm in (("ALL", np.ones(len(df), bool)), ("TRAIN", (et < C.SPLIT).to_numpy()), ("TEST", (et >= C.SPLIT).to_numpy())):
            cm = ctrl["trade"].isin(df.index[rm]).to_numpy()
            for c in C.COSTS:
                C.null_summary(df[rm], ctrl[cm], c, f"C-RAND {name} {half}", log)

    log("\n=== daily-R correlation with the book (primary, @0.70) ===")
    C.book_corr(d0, "H3 primary", log)
    log(f"\ndone in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
