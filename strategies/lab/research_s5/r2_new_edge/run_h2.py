"""run_h2.py -- H2: s96 H1 momentum with the nominal-entry defect removed (PROTOCOL §2).

1. Harness replays (xau2y cache, win_15m=320) of s96_h1_momentum with get_signal patched to
   arm A (fixed) and arm B (stop-entry); mid-M1 books at 0.75 / 1.00.
2. Quote-S5 re-resolution of every entry (walk from entry_time + 60 s on bid/ask closes,
   stop before target, TIME at 14 days) at 0.45 / 0.70 -- validated against
   lab.s5exit.resolve on a sample. Also the Stage-1 s96 book (with the defect) on quote-S5.
3. C-RAND for arm A; splits; book correlation.
Output: results/h2_output.txt, results/h2_*.parquet.
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np
import pandas as pd

from lab.research_s5.r2_new_edge import common as C
from lab.research_s5.po3_judas import po3_common as P

os.environ["LAB_BARS_CACHE"] = str(C.STRAT / "backtest" / "results" / "bars_cache_2y")
from lab import harness as HZ                      # noqa: E402  (CACHE read at import)
from lab import s5exit                              # noqa: E402
from lab.research_s5.r2_new_edge import r2_s96_fixed as F   # noqa: E402

W = dict(start="2024-09-01", end="2026-09-17")
MAX_HOLD_MIN = 14 * 24 * 60
MID_COSTS = (0.75, 1.00)


def replay_arm(name: str, fn, bars) -> pd.DataFrame:
    cfg = HZ.Cfg(cost_pts=MID_COSTS[0], win_15m=320, patch={"get_signal": fn})
    t0 = time.time()
    res = HZ.replay("s96_h1_momentum", bars, cfg=cfg, **W)
    df = res["trades"].reset_index(drop=True)
    df["raw_mid"] = df["pts"] + MID_COSTS[0]
    df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True)
    df["exit_time"] = pd.to_datetime(df["exit_time"], utc=True)
    print(f"  {name}: n={len(df)} in {time.time()-t0:.0f}s; outcomes {df.outcome.value_counts().to_dict()}")
    return df


def quote_resolve(df: pd.DataFrame, s5: C.S5) -> pd.DataFrame:
    """Vectorised equivalent of s5exit.resolve(mode='quote_s5', start_offset_s=60)."""
    out = []
    for t in df.itertuples():
        ent = int(t.entry_time.timestamp())
        e = s5.idx(ent + 61) - 1                    # walk_fast tests bars e+1.. => first bar with t > ent+60 (s5exit 'right')
        end = min(s5.idx(ent + MAX_HOLD_MIN * 60 + 1) - 1, s5.n - 1)
        long_ = t.side == "BUY"
        r = C.walk_fast(s5, e, end, long_, float(t.sl), float(t.tp))
        if r is None:
            out.append(dict(q_outcome=None, q_exit_px=np.nan, q_raw=np.nan, fill_idx=e + 1, q_exit_time=pd.NaT, bad=False)); continue
        oc, x, px = r
        if oc == "TIME":
            px = float(s5.c[end])                     # s5exit convention: TIME exits at the mid close
        out.append(dict(q_outcome=oc, q_exit_px=px, q_raw=C.raw_pts(long_, float(t.entry_px), px),
                        fill_idx=e + 1, q_exit_time=P.ts(s5.t[x]), bad=C.crosses_bad(s5, e + 1, x)))
    q = pd.DataFrame(out, index=df.index)
    d = pd.concat([df, q], axis=1)
    d["raw_pts"] = d["q_raw"]
    return d


def validate_against_s5exit(d: pd.DataFrame, s5df: pd.DataFrame, t5, log, n: int = 150) -> None:
    samp = d.dropna(subset=["q_raw"]).sample(n=min(n, len(d)), random_state=1)
    agree_oc = agree_px = 0
    for t in samp.itertuples():
        r = s5exit.resolve(t, s5df, t5, "quote_s5", 0.0, MAX_HOLD_MIN, start_offset_s=60)
        if r is None:
            continue
        oc, px = r
        agree_oc += int(oc == t.q_outcome)
        agree_px += int(abs(px - t.q_exit_px) < 1e-9)
    log(f"  validation vs lab.s5exit.resolve on {len(samp)} trades: outcome agree {agree_oc}/{len(samp)}, exit px agree {agree_px}/{len(samp)}")


def mid_lines(df: pd.DataFrame, label: str, log) -> None:
    et = df["entry_time"]
    for name, sub in (("ALL", df), ("TRAIN", df[et < C.SPLIT]), ("TEST", df[et >= C.SPLIT])):
        cells = []
        for c in MID_COSTS:
            v = sub["raw_mid"].to_numpy() - c
            cells.append(f"@{c:.2f} PF {P.pf(v):.3f} pts {v.sum():+.1f} WR {100*(v>0).mean():.1f}%")
        log(f"  {label:<26} mid-M1 {name:<5} n={len(sub):<4} " + " | ".join(cells))


def main() -> None:
    log = C.Tee(C.RESULTS / "h2_output.txt")
    t0 = time.time()
    bars = HZ.load_bars(tfs=("1m", "5m", "15m", "1d"))
    log(f"xau2y bars loaded ({HZ.CACHE}); {time.time()-t0:.0f}s")

    log("\n=== harness replays (s96_h1_momentum, get_signal patched; win_15m=320; mid-M1 cost 0.75) ===")
    arms = {"A_fixed": replay_arm("A_fixed", F.get_signal_fixed, bars),
            "B_stop": replay_arm("B_stop", F.get_signal_stop, bars)}
    for k, df in arms.items():
        df.to_parquet(C.RESULTS / f"h2_mid_{k}.parquet", index=False)
        log(f"  {k}: minute-of-hour of entries: {df.entry_time.dt.minute.value_counts().head(5).to_dict()}; "
            f"median risk {df.risk.median():.2f}; reason {df.reason.value_counts().to_dict()}")
        mid_lines(df, k, log)

    # Stage-1 book (with the defect) for reference
    s1 = pd.read_parquet(C.STAGE1 / "s96_h1_momentum_c0.45.trades.parquet")
    s1["entry_time"] = pd.to_datetime(s1["entry_time"], utc=True)
    s1["raw_mid"] = s1["pts"] + 0.45
    log("\n  Stage-1 s96 (defective nominal entries), mid-M1:")
    mid_lines(s1, "stage1_s96", log)

    # ---- quote-S5 resolution ----------------------------------------------------------
    log("\n=== quote-S5 resolution (fill = the harness entry price = M1 close at signal; walk from +60 s; 14-day horizon) ===")
    s5 = C.S5()
    s5df = s5exit.load_s5()
    t5 = s5df["time"].dt.tz_convert(None).to_numpy("datetime64[ns]")
    resolved = {}
    for k, df in list(arms.items()) + [("stage1_s96", s1)]:
        d = quote_resolve(df, s5)
        d["bad"] = d["bad"].fillna(False).astype(bool)
        nbad = int(d["bad"].sum())
        d = d[d.q_raw.notna() & ~d["bad"]].copy()
        d.to_parquet(C.RESULTS / f"h2_quote_{k}.parquet", index=False)
        resolved[k] = d
        log(f"\n  {k}: resolved {len(d)} (dropped bad-window {nbad}); quote outcomes {d.q_outcome.value_counts().to_dict()}; "
            f"mid->quote outcome flips {(d.outcome != d.q_outcome).mean():.3f}; mean haircut {(d.raw_mid - d.q_raw).mean():.3f} pts/trade")
        if k == "A_fixed":
            validate_against_s5exit(d, s5df, t5, log)
        C.book_line(d, f"{k} quote-S5", log)

    # ---- splits (arm A) ---------------------------------------------------------------
    log("\n=== pre-declared splits, arm A quote-S5 (PF/pts @0.70) ===")
    a = resolved["A_fixed"].copy()
    C.split_lines(a, "side", "A", log)
    a["year"] = a.entry_time.dt.year
    C.split_lines(a, "year", "A", log)
    a["hblock"] = pd.cut(a.entry_time.dt.hour, [-1, 6, 15, 23], labels=["00-06", "07-15", "16-23"])
    C.split_lines(a, "hblock", "A", log)
    log("\n=== splits, arm B quote-S5 (record) ===")
    b = resolved["B_stop"].copy()
    C.split_lines(b, "side", "B", log)
    b["year"] = b.entry_time.dt.year
    C.split_lines(b, "year", "B", log)

    # ---- C-RAND (arm A) ---------------------------------------------------------------
    log("\n=== C-RAND control for arm A: 200 draws, same UTC clock minute, day +-30, same stop/target distances, 14-day horizon ===")
    tr = a.copy()
    tr["anchor"] = (tr.entry_time.dt.floor("D").astype("int64") // 10**9).astype(np.int64)
    tr["sl_dist"] = (tr.entry_px - tr.sl).abs()
    tr["tp_dist"] = (tr.tp - tr.entry_px).abs()
    tr["exit_off_s"] = MAX_HOLD_MIN * 60
    days = P.trading_days(s5)
    anchors = np.array([int(d.timestamp()) for d in days], dtype=np.int64)
    t1 = time.time()
    ctrl = C.crand(s5, tr, anchors, n_draws=200)
    ctrl.to_parquet(C.RESULTS / "h2_ctrl_rand_A.parquet", index=False)
    log(f"  {len(ctrl)} control rows, dropped {ctrl.attrs['dropped']}, {time.time()-t1:.0f}s; control outcomes {ctrl.outcome.value_counts().to_dict()}")
    et = a["entry_time"]
    for half, rm in (("ALL", np.ones(len(a), bool)), ("TRAIN", (et < C.SPLIT).to_numpy()), ("TEST", (et >= C.SPLIT).to_numpy())):
        cm = ctrl["trade"].isin(a.index[rm]).to_numpy()
        for c in C.COSTS:
            C.null_summary(a[rm], ctrl[cm], c, f"C-RAND A {half}", log)

    log("\n=== daily-R correlation with the book (arm A quote-S5 @0.70) ===")
    C.book_corr(a, "H2 A", log)
    log(f"\ndone in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
