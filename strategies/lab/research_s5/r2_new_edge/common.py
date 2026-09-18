"""common.py -- shared pieces for the r2_new_edge study (PROTOCOL.md in this folder).

Reuses the po3_judas S5 loader / day-quality / bars() (imported, not edited) and adds:
  * walk_fast     -- chunked quote-level exit walk with early stop (long on bid, short on ask,
                     stop before target within a bar, TIME exit at `end` on the quote close)
  * crand         -- the geometry-matched random control of PROTOCOL §0 (same side, same
                     clock offset from the day anchor, random day within +-30 d, same stop and
                     target distances from the fill, same exit offset in seconds)
  * null_summary  -- the real book's percentile among the null books
  * book_corr     -- daily-R correlation with the c03 / s14 / s93 (and s95) Stage-1 books

Run from `strategies/`: ../.venv/bin/python -m lab.research_s5.r2_new_edge.run_h1
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
STRAT = HERE.parents[2]
if str(STRAT) not in sys.path:
    sys.path.insert(0, str(STRAT))

from lab.research_s5.po3_judas import po3_common as P   # noqa: E402

RESULTS = HERE / "results"
RESULTS.mkdir(exist_ok=True)
SPLIT = pd.Timestamp("2025-12-01", tz="UTC")
COSTS = (0.45, 0.70)          # judged (quote-S5 convention); 0.80 printed for comparability
COSTS_PRINT = (0.45, 0.70, 0.80)
BAD_LO = int(pd.Timestamp("2025-12-25 23:00", tz="UTC").timestamp())
BAD_HI = int(pd.Timestamp("2025-12-25 23:15", tz="UTC").timestamp())
STAGE1 = STRAT / "lab" / "results" / "xau2y_stage1"

S5 = P.S5
Tee = P.Tee
bars = P.bars
pf = P.pf


def walk_fast(s5: S5, e: int, end: int, long_: bool, sl: float, tp: float, chunk: int = 720):
    """Fill at bar e; test bars e+1 .. end (inclusive). Long: stop when bid <= sl, target
    when bid >= tp; short on the ask. Stop before target within a bar. Early-stopping
    version of po3_common.walk (identical outcomes)."""
    if e + 1 > end:
        return None
    px_all = s5.bid_c if long_ else s5.ask_c
    i = e + 1
    while i <= end:
        j = min(end + 1, i + chunk)
        px = px_all[i:j]
        if long_:
            m_sl, m_tp = px <= sl, px >= tp
        else:
            m_sl, m_tp = px >= sl, px <= tp
        a_sl, a_tp = m_sl.any(), m_tp.any()
        if a_sl or a_tp:
            i_sl = int(np.argmax(m_sl)) if a_sl else None
            i_tp = int(np.argmax(m_tp)) if a_tp else None
            if i_sl is not None and (i_tp is None or i_sl <= i_tp):
                return "SL", i + i_sl, sl
            return "TP", i + i_tp, tp
        i = j
    return "TIME", end, float(px_all[end])


def crosses_bad(s5: S5, e: int, x: int) -> bool:
    return (s5.t[e] <= BAD_HI) and (s5.t[x] >= BAD_LO)


def raw_pts(long_: bool, fill: float, px: float) -> float:
    return (px - fill) if long_ else (fill - px)


def crand(s5: S5, trades: pd.DataFrame, anchors: np.ndarray, n_draws: int = 200,
          seed: int = 20260918, window_days: int = 30, tol_s: int = 60) -> pd.DataFrame:
    """PROTOCOL §0 control. `trades` needs: anchor (epoch s of the trade's day anchor),
    fill_idx, side, sl_dist, tp_dist, exit_off_s. `anchors`: sorted epoch seconds of every
    valid trading day's anchor (00:00 UTC, or 00:00 ET for NY-clocked rules). Returns one row
    per (trade, draw) with raw_pts; draws that cannot be placed are dropped (counted)."""
    rng = np.random.default_rng(seed)
    anchors = np.asarray(anchors, dtype=np.int64)
    W = window_days * 86400
    out = []
    dropped = 0
    for t in trades.itertuples():
        lo = int(np.searchsorted(anchors, t.anchor - W, "left"))
        hi = int(np.searchsorted(anchors, t.anchor + W, "right"))
        pool = anchors[lo:hi]
        pool = pool[pool != t.anchor]
        if len(pool) == 0:
            dropped += n_draws
            continue
        off = int(s5.t[t.fill_idx] - t.anchor)
        long_ = t.side == "BUY"
        picks = rng.choice(pool, size=n_draws, replace=True)
        for d, a in enumerate(picks):
            target = int(a) + off
            e = s5.idx(target)
            if e >= s5.n or abs(int(s5.t[e]) - target) > tol_s:
                dropped += 1
                continue
            end = s5.idx(int(s5.t[e]) + int(t.exit_off_s))
            end = min(end, s5.n - 1)
            if end <= e:
                dropped += 1
                continue
            fill = float(s5.ask_c[e]) if long_ else float(s5.bid_c[e])
            sl = fill - t.sl_dist if long_ else fill + t.sl_dist
            tp = fill + t.tp_dist if long_ else fill - t.tp_dist
            r = walk_fast(s5, e, end, long_, sl, tp)
            if r is None or crosses_bad(s5, e, r[1]):
                dropped += 1
                continue
            oc, x, px = r
            out.append(dict(trade=t.Index, draw=d, side=t.side, entry_time=P.ts(s5.t[e]),
                            outcome=oc, raw_pts=raw_pts(long_, fill, px), risk=t.sl_dist))
    df = pd.DataFrame(out)
    df.attrs["dropped"] = dropped
    return df


def null_summary(real: pd.DataFrame, ctrl: pd.DataFrame, cost: float, label: str, log) -> dict:
    """Real book vs the n_draws null books (each draw index is one book), at one cost."""
    if not len(ctrl):
        log(f"  {label}: control empty"); return {}
    rp = real["raw_pts"].to_numpy() - cost
    real_net, real_pf = float(rp.sum()), pf(rp)
    g = ctrl.groupby("draw")["raw_pts"]
    nets = g.apply(lambda v: float((v - cost).sum())).to_numpy()
    pfs = g.apply(lambda v: pf(v.to_numpy() - cost)).to_numpy()
    pct_net = float((nets < real_net).mean())
    pct_pf = float((pfs < real_pf).mean())
    wr_ctrl = float(((ctrl["raw_pts"] - cost) > 0).mean())
    log(f"  {label} @{cost:.2f}: real n={len(real)} net={real_net:+.1f} PF={real_pf:.3f} "
        f"WR={100*float((rp>0).mean()):.1f}% | null (n_books={len(nets)}) net mean={nets.mean():+.1f} "
        f"p5={np.percentile(nets,5):+.1f} p95={np.percentile(nets,95):+.1f} PF mean={pfs.mean():.3f} "
        f"p95={np.percentile(pfs,95):.3f} WR={100*wr_ctrl:.1f}% | real at pct net={pct_net:.2f} pf={pct_pf:.2f}")
    return dict(label=label, cost=cost, real_n=len(real), real_net=real_net, real_pf=real_pf,
                null_net_mean=float(nets.mean()), null_net_p5=float(np.percentile(nets, 5)),
                null_net_p95=float(np.percentile(nets, 95)), null_pf_mean=float(pfs.mean()),
                null_pf_p95=float(np.percentile(pfs, 95)), pct_net=pct_net, pct_pf=pct_pf,
                null_wr=wr_ctrl)


def book_line(df: pd.DataFrame, label: str, log, costs=COSTS_PRINT) -> dict:
    """One line per split x cost; returns the bars dict (judged costs)."""
    et = pd.to_datetime(df["entry_time"], utc=True)
    parts = (("ALL", df), ("TRAIN", df[et < SPLIT]), ("TEST", df[et >= SPLIT]))
    for name, sub in parts:
        cells = []
        for c in costs:
            b = P.book(sub, c)
            cells.append(f"@{c:.2f} PF {b['pf']:.3f} pts {b['pts']:+.1f} WR {b['wr']}%")
        oc = sub["outcome"].value_counts().to_dict() if len(sub) else {}
        log(f"  {label:<28} {name:<5} n={len(sub):<4} " + " | ".join(cells) + f"  {oc}")
    b = bars(df, COSTS[0], COSTS[1]) if len(df) else {}
    if b:
        tr = df[et < SPLIT]
        sd = float((tr["raw_pts"] - COSTS[1]).std(ddof=1)) if len(tr) > 1 else float("nan")
        mde = 2 * sd / np.sqrt(max(int(b["test_n"]), 1))
        b.update(train_sd=round(sd, 2), test_mde=round(mde, 3))
        log(f"  {label:<28} BARS  {b['verdict']} {b['bars_passed']}/6  n:{b['bar1_n']} base:{b['bar2_base']} "
            f"stress:{b['bar2_stress']} train:{b['bar3_train']} monthly:{b['bar4_monthly']} "
            f"(pos {b['test_pos_share']}, max {b['test_max_share']}) regime:{b['bar5_regime']} "
            f"(corr {b['gold_corr']}, up {b['up_pts']}, dn {b['dn_pts']}) | TRAIN sd {sd:.2f} TEST MDE {mde:.2f} pts/trade")
    return b


def split_lines(df: pd.DataFrame, key: str, label: str, log, cost: float = COSTS[1]) -> None:
    et = pd.to_datetime(df["entry_time"], utc=True)
    for val, sub in df.groupby(key):
        tr, te = sub[et.loc[sub.index] < SPLIT], sub[et.loc[sub.index] >= SPLIT]
        a, b = P.book(tr, cost), P.book(te, cost)
        log(f"  {label} {key}={val!s:<12} TRAIN n={a['n']:<4} PF {a['pf']:<6} pts {a['pts']:>+8.1f} | "
            f"TEST n={b['n']:<4} PF {b['pf']:<6} pts {b['pts']:>+8.1f}")


def daily_r(df: pd.DataFrame, cost: float, pts_col: str = "raw_pts", risk_col: str = "risk") -> pd.Series:
    et = pd.to_datetime(df["entry_time"], utc=True).dt.floor("D")
    r = (df[pts_col] - cost) / df[risk_col]
    return r.groupby(et).sum()


def stage1_daily_r(name: str) -> pd.Series:
    d = pd.read_parquet(STAGE1 / f"{name}_c0.80.trades.parquet")
    et = pd.to_datetime(d["entry_time"], utc=True).dt.floor("D")
    return d["r"].groupby(et).sum()


def book_corr(df: pd.DataFrame, label: str, log, cost: float = COSTS[1]) -> dict:
    """Daily-R correlation vs the book (c03, s14, s93) and s95, on the union of days where
    either side traded (0 on days without a trade)."""
    mine = daily_r(df, cost)
    out = {}
    for name in ("c03_fvg_fill", "s14_ob_mit_bias", "s93_fvg_scalp", "s95_session_breakout"):
        other = stage1_daily_r(name)
        idx = mine.index.union(other.index)
        a, b = mine.reindex(idx).fillna(0.0), other.reindex(idx).fillna(0.0)
        c = float(np.corrcoef(a, b)[0, 1]) if a.std() > 0 and b.std() > 0 else float("nan")
        out[name] = round(c, 3)
    log(f"  {label} daily-R corr: " + ", ".join(f"{k} {v:+.3f}" for k, v in out.items()))
    return out
