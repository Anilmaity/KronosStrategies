"""run_tradeable.py -- Q2 of PROTOCOL.md: fade the London sweep of the Asian range on its
reclaim (T in {60,300,900} s), stop beyond the sweep extreme + 0.2, target T1 = opposite
side of the range (primary) / 2R (secondary), quote exits, time exit 16:00 UTC.
Controls: C1 (same rule on a 00-04 range swept in 04-07 UTC, no London) and C2 (time-
shuffled, geometry-matched random entries within +-30 days, 200 draws).
Writes results/trades_*.parquet, results/c2_null_*.csv and results/tradeable.txt.

    cd strategies && ../.venv/bin/python -m lab.research_s5.po3_judas.run_tradeable
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from lab.research_s5.po3_judas.po3_common import (H, RESULTS, S5, SPLIT, STOP_PAD, Tee, bars, book, d1_bias,
                                                  entry_quote, first_breaches, london_open, trading_days, ts, walk)

T_GRID = (60, 300, 900)
COSTS = (0.45, 0.80)
N_C2 = 200


# ---------------------------------------------------------------- event / trade builders

def day_events(s5: S5, date: pd.Timestamp, anchor: str = "A1", control: bool = False,
               quote_reclaim: bool = False) -> list[dict]:
    d0 = int(date.timestamp())
    if control:                                   # C1: same rule, no London
        r0, r1 = d0, d0 + 4 * H
        w0, w1 = d0 + 4 * H, d0 + 7 * H
    else:
        lon0 = london_open(date, anchor)
        r0, r1 = d0, lon0
        w0, w1 = lon0, lon0 + 3 * H
    ir0, ir1, iw0, iw1, i16 = s5.idx(r0), s5.idx(r1), s5.idx(w0), s5.idx(w1), s5.idx(d0 + 16 * H)
    r_hi, r_lo = float(s5.h[ir0:ir1].max()), float(s5.l[ir0:ir1].min())
    evs = first_breaches(s5, r_lo, r_hi, iw0, iw1, i16)
    if quote_reclaim:                             # robustness: far-side quote back inside
        for e in evs:
            b = e["breach_idx"]
            back = (s5.bid_c[b + 1:i16] > r_lo) if e["side"] == "DOWN" else (s5.ask_c[b + 1:i16] < r_hi)
            rec = b + 1 + int(np.argmax(back)) if back.any() else None
            e["reclaim_idx"] = rec
            seg_end = (rec + 1) if rec is not None else i16
            if e["side"] == "UP":
                e["extreme"] = float(s5.h[b:seg_end].max()); e["depth"] = e["extreme"] - r_hi
            else:
                e["extreme"] = float(s5.l[b:seg_end].min()); e["depth"] = r_lo - e["extreme"]
            e["duration"] = (s5.t[rec] - s5.t[b]) if rec is not None else np.nan
    sp = float(np.median(s5.spread[iw0:iw1]))
    for e in evs:
        e.update(date=date.strftime("%Y-%m-%d"), d0=d0, r_hi=r_hi, r_lo=r_lo, r_range=r_hi - r_lo,
                 spread_med=sp, real=bool(e["depth"] > sp), i16=i16)
    return evs


def build_trades(s5: S5, evs: list[dict], label: str) -> pd.DataFrame:
    rows = []
    for e in evs:
        if e["reclaim_idx"] is None:
            continue
        for T in T_GRID:
            if e["duration"] > T:
                continue
            ent = e["reclaim_idx"] + 1
            assert ent > e["reclaim_idx"] > e["breach_idx"]
            if ent >= e["i16"]:
                continue
            long_ = e["side"] == "DOWN"
            px = entry_quote(s5, ent, long_)
            sl = e["extreme"] - STOP_PAD if long_ else e["extreme"] + STOP_PAD
            risk = abs(px - sl)
            for tgt in ("T1", "2R"):
                if tgt == "T1":
                    tp = e["r_hi"] if long_ else e["r_lo"]
                else:
                    tp = px + 2 * risk if long_ else px - 2 * risk
                ok = (sl < px < tp) if long_ else (tp < px < sl)
                if not ok:
                    continue
                res = walk(s5, ent, e["i16"], long_, sl, tp)
                if res is None:
                    continue
                oc, xi, xpx = res
                raw = (xpx - px) if long_ else (px - xpx)
                rows.append(dict(label=label, T=T, target=tgt, date=e["date"], side=("BUY" if long_ else "SELL"),
                                 breach_time=ts(s5.t[e["breach_idx"]]), reclaim_time=ts(s5.t[e["reclaim_idx"]]),
                                 entry_time=ts(s5.t[ent]), entry_idx=ent, entry_px=px, sl=sl, tp=tp, risk=risk,
                                 reward=abs(tp - px), rr=abs(tp - px) / risk, depth=e["depth"], duration=e["duration"],
                                 real=e["real"], spread_med=e["spread_med"], r_range=e["r_range"],
                                 outcome=oc, exit_time=ts(s5.t[xi]), exit_px=xpx, raw_pts=raw, d0=e["d0"],
                                 tod=int(s5.t[ent] - e["d0"])))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- C2 null

def c2_null(s5: S5, tr: pd.DataFrame, days: list[pd.Timestamp], rng: np.random.Generator, n_draw: int = N_C2) -> np.ndarray:
    """time-shuffled, geometry-matched: same side / time-of-day / stop & target distance on
    a random other trading day within +-30 days. Returns raw_pts array (n_draw, n)."""
    day_epochs = np.array([int(d.timestamp()) for d in days])
    out = np.full((n_draw, len(tr)), np.nan)
    side = (tr.side == "BUY").to_numpy(); tod = tr.tod.to_numpy(); risk = tr.risk.to_numpy(); rew = tr.reward.to_numpy()
    d0s = tr.d0.to_numpy()
    for j in range(len(tr)):
        cand = day_epochs[(np.abs(day_epochs - d0s[j]) <= 30 * 86400) & (day_epochs != d0s[j])]
        picks = rng.choice(cand, size=n_draw, replace=True)
        for k, D in enumerate(picks):
            e = s5.idx(int(D) + tod[j]); end = s5.idx(int(D) + 16 * H)
            if e >= end:
                continue
            px = entry_quote(s5, e, side[j])
            sl = px - risk[j] if side[j] else px + risk[j]
            tp = px + rew[j] if side[j] else px - rew[j]
            res = walk(s5, e, end, side[j], sl, tp)
            if res is None:
                continue
            out[k, j] = (res[2] - px) if side[j] else (px - res[2])
    return out


# ---------------------------------------------------------------- reporting

def cell_table(say, df: pd.DataFrame, title: str):
    say(f"\n{title}")
    say(f"{'T':>4} {'tgt':>3} | {'TRAIN n':>7} {'PF.45':>6} {'PF.80':>6} {'pts.80':>8} | {'TEST n':>6} {'PF.45':>6} {'PF.80':>6} {'pts.80':>8} | "
        f"{'mo+':>4} {'maxmo':>5} {'corr':>5} | bars verdict")
    for T in T_GRID:
        for tgt in ("T1", "2R"):
            sub = df[(df["T"] == T) & (df.target == tgt)]
            if not len(sub):
                say(f"{T:>4} {tgt:>3} | (no trades)"); continue
            b = bars(sub)
            say(f"{T:>4} {tgt:>3} | {b['train_n']:>7} {b['train_pf_0.45']:>6} {b['train_pf_0.8']:>6} {b['train_pts_0.8']:>8} | "
                f"{b['test_n']:>6} {b['test_pf_0.45']:>6} {b['test_pf_0.8']:>6} {b['test_pts_0.8']:>8} | "
                f"{b['test_pos_share']:>4} {b['test_max_share']!s:>5} {b['gold_corr']!s:>5} | {b['bars_passed']}/6 {b['verdict']}")


def split_rows(say, sub: pd.DataFrame, name: str):
    et = pd.to_datetime(sub.entry_time, utc=True); cut = pd.Timestamp(SPLIT, tz="UTC")
    tr, te = sub[et < cut], sub[et >= cut]
    a, b, c, d = book(tr, 0.45), book(tr, 0.80), book(te, 0.45), book(te, 0.80)
    say(f"{name:<34} | TRAIN n {a['n']:>4} PF {a['pf']:>6} / {b['pf']:>6}  pts {b['pts']:>8} WR {b['wr']:>5} | "
        f"TEST n {c['n']:>4} PF {c['pf']:>6} / {d['pf']:>6}  pts {d['pts']:>8} WR {d['wr']:>5}")


def main() -> None:
    RESULTS.mkdir(exist_ok=True)
    say = Tee(RESULTS / "tradeable.txt")
    t0 = time.time()
    s5 = S5()
    days = trading_days(s5)
    say(f"S5 bars {s5.n:,}; trading days {len(days)} ({days[0].date()} .. {days[-1].date()}); TRAIN < {SPLIT} <= TEST; costs {COSTS}")
    rng = np.random.default_rng(20260918)

    # --- event sets
    sets = {
        "A1": [e for d in days for e in day_events(s5, d, "A1")],
        "C1": [e for d in days for e in day_events(s5, d, "A1", control=True)],
        "A3": [e for d in days for e in day_events(s5, d, "A3")],
        "A1q": [e for d in days for e in day_events(s5, d, "A1", quote_reclaim=True)],
    }
    trades = {k: build_trades(s5, v, k) for k, v in sets.items()}
    for k, v in trades.items():
        v.to_parquet(RESULTS / f"trades_{k}.parquet", index=False)
    say(f"events: " + ", ".join(f"{k} {len(v)} (reclaimed {sum(e['reclaim_idx'] is not None for e in v)})" for k, v in sets.items()))
    say(f"build {time.time()-t0:.0f}s")

    A1 = trades["A1"]
    say("\nA1 trade geometry (T=900, T1): " + ", ".join(
        f"{k} median {A1[(A1['T']==900)&(A1.target=='T1')][k].median():.2f}" for k in ("risk", "reward", "rr", "depth", "duration", "r_range")))
    t1 = A1[(A1["T"] == 900) & (A1.target == "T1")]
    say(f"outcomes T=900/T1: " + t1.outcome.value_counts().to_dict().__str__() + f";  sides {t1.side.value_counts().to_dict()}")

    cell_table(say, A1, "== A1 primary: London sweep of the Asian range, reclaim within T, fade (all cells; bars at base cost 0.45 unless stated)")
    cell_table(say, trades["C1"], "== C1 control: same rule, range 00-04 UTC swept in 04-07 UTC (no London)")

    # --- TRAIN selection of T (T1 target, PF at 0.80)
    et = pd.to_datetime(A1.entry_time, utc=True); cut = pd.Timestamp(SPLIT, tz="UTC")
    train_pf = {T: book(A1[(A1["T"] == T) & (A1.target == "T1") & (et < cut)], 0.80)["pf"] for T in T_GRID}
    T_sel = max(train_pf, key=lambda k: (train_pf[k] if np.isfinite(train_pf[k]) else -1))
    say(f"\n== TRAIN selection (T1 target, PF at 0.80): {train_pf} -> declared arm T={T_sel}")
    arm = A1[(A1["T"] == T_sel) & (A1.target == "T1")]
    tr_arm, te_arm = arm[et[arm.index] < cut], arm[et[arm.index] >= cut]
    sd = float((tr_arm.raw_pts - 0.80).std(ddof=1))
    mde = 2 * sd / np.sqrt(max(len(te_arm), 1))
    say(f"power: TRAIN sd(pts) {sd:.2f}, TEST n {len(te_arm)} -> MDE (2 sd/sqrt n) = {mde:.2f} pts/trade;  "
        f"TEST mean pts at 0.80 = {book(te_arm, 0.80)['mean']}")
    b = bars(arm)
    say("declared arm bars: " + ", ".join(f"{k}={v}" for k, v in b.items()))

    # --- splits on the declared arm
    say(f"\n== splits, declared arm T={T_sel}, T1 (PF at 0.45 / 0.80; pts and WR at 0.80)")
    split_rows(say, arm, "all")
    for y in (2024, 2025, 2026):
        split_rows(say, arm[et[arm.index].dt.year == y], f"year {y}")
    for s in ("BUY", "SELL"):
        split_rows(say, arm[arm.side == s], f"side {s}")
    split_rows(say, arm[arm.real], "real sweep (depth > median spread)")
    split_rows(say, arm[~arm.real], "flicker sweep (depth <= spread)")
    bias = d1_bias()
    bday = pd.to_datetime(arm.date, utc=True).map(bias)
    say(f"D1 bias join: {bday.notna().mean()*100:.1f}% of trades have a bias value")
    with_bias = arm[((arm.side == "BUY") & (bday > 0)) | ((arm.side == "SELL") & (bday < 0))]
    against = arm[((arm.side == "BUY") & (bday < 0)) | ((arm.side == "SELL") & (bday > 0))]
    split_rows(say, with_bias, "D1 bias filter (with bias)")
    split_rows(say, against, "D1 bias (against, for contrast)")
    A3 = trades["A3"]; split_rows(say, A3[(A3["T"] == T_sel) & (A3.target == "T1")], "anchor A3 (DST-aware London)")
    A1q = trades["A1q"]; split_rows(say, A1q[(A1q["T"] == T_sel) & (A1q.target == "T1")], "reclaim on the quote")
    C1 = trades["C1"]; split_rows(say, C1[(C1["T"] == T_sel) & (C1.target == "T1")], "C1 control at the same T")
    arm2 = A1[(A1["T"] == T_sel) & (A1.target == "2R")]; split_rows(say, arm2, "2R target (secondary)")
    say("\nby year x side, declared arm (n / PF at 0.80 / pts at 0.80):")
    for y in (2024, 2025, 2026):
        for s in ("BUY", "SELL"):
            sub = arm[(et[arm.index].dt.year == y) & (arm.side == s)]; bk = book(sub, 0.80)
            say(f"  {y} {s}: {bk['n']} / {bk['pf']} / {bk['pts']}")
    say("\nmonthly pts at 0.80, declared arm:")
    say((arm.raw_pts - 0.80).groupby(et[arm.index].dt.strftime("%Y-%m")).agg(["size", "sum"]).round(1).T.to_string())

    # --- C2 null on every T1 cell
    say(f"\n== C2 control: time-shuffled, geometry-matched random entries, {N_C2} draws of the whole book (+-30 days)")
    for T in T_GRID:
        cell = A1[(A1["T"] == T) & (A1.target == "T1")].reset_index(drop=True)
        tc = time.time()
        null = c2_null(s5, cell, days, rng)
        pd.DataFrame(null).to_csv(RESULTS / f"c2_null_T{T}.csv", index=False)
        ec = pd.to_datetime(cell.entry_time, utc=True)
        for lab, m in (("ALL", np.ones(len(cell), bool)), ("TRAIN", (ec < cut).to_numpy()), ("TEST", (ec >= cut).to_numpy())):
            for cost in COSTS:
                real = float((cell.raw_pts[m] - cost).sum()); real_pf = book(cell[m], cost)["pf"]
                nv = null[:, m] - cost
                npts = np.nansum(nv, axis=1)
                npf = np.array([(v[v > 0].sum() / -v[v <= 0].sum()) if (v <= 0).any() and -v[v <= 0].sum() > 0 else np.inf for v in (nv[i][~np.isnan(nv[i])] for i in range(len(nv)))])
                pct = float((npts < real).mean())
                say(f"T={T:>3} {lab:<5} cost {cost}: real pts {real:8.1f} PF {real_pf:6.3f} | null pts mean {npts.mean():8.1f} p5 {np.percentile(npts,5):8.1f} "
                    f"p95 {np.percentile(npts,95):8.1f}  null PF mean {npf[np.isfinite(npf)].mean():.3f} | real at null percentile {pct*100:.0f}%  "
                    f"(null resolves {np.mean(~np.isnan(null[:, m]))*100:.1f}% of draws)")
        say(f"  (C2 T={T} {time.time()-tc:.0f}s)")
    say(f"\ntotal {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
