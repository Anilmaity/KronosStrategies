"""run_h1.py -- H1: trade WITH the first London breach of the Asian range (PROTOCOL §1).

Arms: hold gate T in {0,30,60} s x target M in {1.0, 0.5}; depth arm (T=0, M=1, depth >= 2
spreads). Controls: C-PRE (00-04 range breached 04-07) and C-RAND (geometry-matched random).
Output: results/h1_output.txt, results/h1_trades_*.parquet, results/h1_ctrl_*.parquet.
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from lab.research_s5.r2_new_edge import common as C
from lab.research_s5.po3_judas import po3_common as P

H = 3600
T_GRID = (0, 30, 60)
M_GRID = (1.0, 0.5)
EXIT_HM = 20 * H + 55 * 60


def run_rule(s5: C.S5, days, r0: int, r1: int, w0: int, w1: int, T: int, M: float,
             depth_gate: bool = False) -> pd.DataFrame:
    """Range over [r0, r1) hours, first breach in [w0, w1) hours, hold T s, target M x range."""
    rows = []
    stats = dict(days=0, no_range=0, no_breach=0, both_same_bar=0, fail_hold=0, fail_depth=0,
                 beyond_target=0, no_bars=0, bad_window=0)
    for d in days:
        d0 = int(d.timestamp())
        stats["days"] += 1
        a0, a1 = s5.idx(d0 + r0 * H), s5.idx(d0 + r1 * H)
        if a1 - a0 < 100:
            stats["no_range"] += 1; continue
        ar_hi, ar_lo = float(s5.h[a0:a1].max()), float(s5.l[a0:a1].min())
        sp_med = float(np.median(s5.spread[a0:a1]))
        rng = ar_hi - ar_lo
        if rng < 2 * sp_med:
            stats["no_range"] += 1; continue
        mid = (ar_hi + ar_lo) / 2
        b0, b1 = s5.idx(d0 + w0 * H), s5.idx(d0 + w1 * H)
        up = s5.h[b0:b1] > ar_hi
        dn = s5.l[b0:b1] < ar_lo
        iu = int(np.argmax(up)) if up.any() else None
        idn = int(np.argmax(dn)) if dn.any() else None
        if iu is None and idn is None:
            stats["no_breach"] += 1; continue
        if iu is not None and idn is not None and iu == idn:
            stats["both_same_bar"] += 1; continue
        if idn is None or (iu is not None and iu < idn):
            side, b = "BUY", b0 + iu
        else:
            side, b = "SELL", b0 + idn
        long_ = side == "BUY"
        k = b + T // 5
        if k >= s5.n - 2:
            stats["no_bars"] += 1; continue
        closes = s5.c[b:k + 1]
        held = bool((closes >= ar_hi).all()) if long_ else bool((closes <= ar_lo).all())
        if not held:
            stats["fail_hold"] += 1; continue
        depth = (float(s5.h[b]) - ar_hi) if long_ else (ar_lo - float(s5.l[b]))
        sp_b = float(s5.spread[b])
        if depth_gate and depth < 2 * sp_b:
            stats["fail_depth"] += 1; continue
        e = k + 1
        fill = float(s5.ask_c[e]) if long_ else float(s5.bid_c[e])
        sl = mid
        tp = ar_hi + M * rng if long_ else ar_lo - M * rng
        if (long_ and fill >= tp) or ((not long_) and fill <= tp):
            stats["beyond_target"] += 1; continue
        end = min(s5.idx(d0 + EXIT_HM), s5.n - 1)
        r = C.walk_fast(s5, e, end, long_, sl, tp)
        if r is None:
            stats["no_bars"] += 1; continue
        oc, x, px = r
        if C.crosses_bad(s5, e, x):
            stats["bad_window"] += 1; continue
        rows.append(dict(date=d.strftime("%Y-%m-%d"), anchor=d0, side=side, T=T, M=M,
                         breach_time=P.ts(s5.t[b]), entry_time=P.ts(s5.t[e]), fill_idx=e,
                         fill=fill, sl=sl, tp=tp, risk=abs(fill - sl), reward=abs(tp - fill),
                         sl_dist=abs(fill - sl), tp_dist=abs(tp - fill),
                         exit_off_s=int(s5.t[x] - s5.t[e]) if oc != "TIME" else int(s5.t[end] - s5.t[e]),
                         exit_deadline_off_s=int(s5.t[end] - s5.t[e]),
                         ar_hi=ar_hi, ar_lo=ar_lo, rng=rng, depth=depth, spread_b=sp_b,
                         breach_min=(int(s5.t[b]) - d0 - w0 * H) / 60.0,
                         outcome=oc, exit_time=P.ts(s5.t[x]), exit_px=px,
                         raw_pts=C.raw_pts(long_, fill, px), hold_s=int(s5.t[x] - s5.t[e])))
    df = pd.DataFrame(rows)
    df.attrs["stats"] = stats
    return df


def main() -> None:
    log = C.Tee(C.RESULTS / "h1_output.txt")
    t0 = time.time()
    s5 = C.S5()
    days = P.trading_days(s5)
    log(f"S5 bars {s5.n:,}; trading days {len(days)} ({days[0].date()} -> {days[-1].date()}); load {time.time()-t0:.0f}s")
    anchors = np.array([int(d.timestamp()) for d in days], dtype=np.int64)

    # ---- grid on TRAIN ----------------------------------------------------------------
    log("\n=== H1 grid: London breach of the 00-07 Asian range, breach window 07-10 UTC ===")
    books = {}
    grid = []
    for T in T_GRID:
        for M in M_GRID:
            df = run_rule(s5, days, 0, 7, 7, 10, T, M)
            books[(T, M)] = df
            df.to_parquet(C.RESULTS / f"h1_trades_T{T}_M{M}.parquet", index=False)
            st = df.attrs["stats"]
            et = pd.to_datetime(df["entry_time"], utc=True)
            tr = df[et < C.SPLIT]
            btr = P.book(tr, C.COSTS[1])
            grid.append(dict(T=T, M=M, n=len(df), train_n=btr["n"], train_pf_070=btr["pf"], train_pts_070=btr["pts"],
                             train_wr=btr["wr"], median_risk=round(float(df.risk.median()), 2) if len(df) else None,
                             median_reward=round(float(df.reward.median()), 2) if len(df) else None, **st))
            log(f"  T={T:<3} M={M}: n={len(df)} TRAIN n={btr['n']} PF@0.70={btr['pf']} pts={btr['pts']} WR={btr['wr']}% "
                f"| median risk {df.risk.median():.2f} reward {df.reward.median():.2f} | stats {st}")
    gdf = pd.DataFrame(grid)
    gdf.to_csv(C.RESULTS / "h1_grid.csv", index=False)
    cand = gdf[(gdf.M == 1.0) & (gdf.train_n >= 80)]
    if not len(cand):
        log("no T with TRAIN n >= 80 at M=1.0 -- selection impossible; reporting all arms descriptively")
        T_sel = 0
    else:
        T_sel = int(cand.sort_values("train_pf_070", ascending=False).iloc[0]["T"])
    log(f"\nSELECTED on TRAIN PF@0.70 (M=1.0, TRAIN n>=80): T={T_sel}")

    # descriptive: how the hold gate thins the events
    log("\n--- breach anatomy (T=0, M=1 arm; all fills) ---")
    d0 = books[(0, 1.0)]
    if len(d0):
        log(f"  breach minute after 07:00: median {d0.breach_min.median():.1f} (IQR {d0.breach_min.quantile(.25):.1f}-{d0.breach_min.quantile(.75):.1f})")
        log(f"  depth at breach bar: median {d0.depth.median():.3f}, spread {d0.spread_b.median():.2f}; depth>=2 spreads share {(d0.depth>=2*d0.spread_b).mean():.3f}")
        log(f"  range: median {d0.rng.median():.1f} pts; risk median {d0.risk.median():.2f}; reward median {d0.reward.median():.2f}; RR median {(d0.reward/d0.risk).median():.2f}")
        log(f"  side: {d0.side.value_counts().to_dict()}; outcome: {d0.outcome.value_counts().to_dict()}; hold median {d0.hold_s.median()/60:.0f} min")

    # ---- judged arms ------------------------------------------------------------------
    log("\n=== JUDGED ARMS (TEST read once) ===")
    judged = {}
    prim = books[(T_sel, 1.0)]
    judged["primary"] = prim
    C.book_line(prim, f"H1 primary T={T_sel} M=1.0", log)
    sec = books[(T_sel, 0.5)]
    judged["secondary_M0.5"] = sec
    C.book_line(sec, f"H1 secondary T={T_sel} M=0.5", log)
    dep = run_rule(s5, days, 0, 7, 7, 10, 0, 1.0, depth_gate=True)
    dep.to_parquet(C.RESULTS / "h1_trades_depth.parquet", index=False)
    judged["depth"] = dep
    log(f"  depth arm stats: {dep.attrs['stats']}")
    C.book_line(dep, "H1 depth T=0 M=1.0 d>=2sp", log)
    # the full grid on TEST, for the record (not judged)
    log("\n--- full grid TEST @0.70 (record only; not judged) ---")
    for (T, M), df in books.items():
        et = pd.to_datetime(df["entry_time"], utc=True)
        b = P.book(df[et >= C.SPLIT], C.COSTS[1])
        log(f"  T={T:<3} M={M}: TEST n={b['n']} PF {b['pf']} pts {b['pts']}")

    # ---- splits of the primary --------------------------------------------------------
    log("\n=== pre-declared splits of the primary arm (PF/pts @0.70) ===")
    prim = prim.copy()
    C.split_lines(prim, "side", "primary", log)
    bias = P.d1_bias()
    dts = pd.to_datetime(prim["date"], utc=True)
    bv = bias.reindex(dts).to_numpy()
    sgn = np.where(prim.side == "BUY", 1, -1)
    prim["d1_bias"] = np.where(np.isnan(bv), "none", np.where(bv * sgn > 0, "with", "against"))
    C.split_lines(prim, "d1_bias", "primary", log)
    prim["year"] = dts.dt.year
    C.split_lines(prim, "year", "primary", log)

    # ---- C-PRE control ----------------------------------------------------------------
    log("\n=== C-PRE control: 00-04 range, first breach 04-07 UTC, same T/M, exit 20:55 ===")
    for (T, M) in ((T_sel, 1.0), (T_sel, 0.5)):
        cp = run_rule(s5, days, 0, 4, 4, 7, T, M)
        cp.to_parquet(C.RESULTS / f"h1_ctrl_pre_T{T}_M{M}.parquet", index=False)
        log(f"  C-PRE T={T} M={M} stats {cp.attrs['stats']}")
        C.book_line(cp, f"C-PRE T={T} M={M}", log)

    # ---- C-RAND control ---------------------------------------------------------------
    log("\n=== C-RAND control: 200 geometry-matched random draws per trade ===")
    for name, df in (("primary", judged["primary"]), ("depth", judged["depth"])):
        if not len(df):
            continue
        t1 = time.time()
        tr = df.copy()
        tr["exit_off_s"] = tr["exit_deadline_off_s"]
        ctrl = C.crand(s5, tr, anchors, n_draws=200)
        ctrl.to_parquet(C.RESULTS / f"h1_ctrl_rand_{name}.parquet", index=False)
        log(f"  {name}: {len(ctrl)} control rows, dropped {ctrl.attrs['dropped']}, {time.time()-t1:.0f}s")
        et = pd.to_datetime(df["entry_time"], utc=True)
        cet = pd.to_datetime(ctrl["entry_time"], utc=True)
        for half, rm, cm in (("ALL", np.ones(len(df), bool), np.ones(len(ctrl), bool)),
                             ("TRAIN", (et < C.SPLIT).to_numpy(), ctrl["trade"].isin(df.index[et < C.SPLIT]).to_numpy()),
                             ("TEST", (et >= C.SPLIT).to_numpy(), ctrl["trade"].isin(df.index[et >= C.SPLIT]).to_numpy())):
            for c in C.COSTS:
                C.null_summary(df[rm], ctrl[cm], c, f"C-RAND {name} {half}", log)

    # ---- book correlation -------------------------------------------------------------
    log("\n=== daily-R correlation with the book (primary arm, @0.70) ===")
    C.book_corr(judged["primary"], "H1 primary", log)
    log(f"\ndone in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
