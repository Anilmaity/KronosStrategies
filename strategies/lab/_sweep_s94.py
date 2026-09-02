"""One-off sweep driver for the S94 sweep-reversal optimization campaign.

Writes incremental results to lab/results/s94_sweep.json (list of records) and
prints progress to stdout as it goes, so a long background run is inspectable
and resumable-in-spirit even if killed midway.

Window: shortened from the full 19.5mo cache to keep this strategy's slow
(1500-bar M5 window) replay affordable -- see lab/REPORT_s94.md for the
explicit note. Train/test split logic is preserved.

Uses the harness's warm-up fix (i0 guarantees win_5m*5+5 M1 bars of history, not
just win_1m) -- our START (2025-12-01) is ~10 months past the cache origin, so
it was never affected by the pre-fix truncated-level-universe bug, but we picked
up the fixed harness regardless since it landed before any real sweep result was
recorded here (only a throwaway timing probe was run pre-fix, and its result was
discarded, not used in any table below).

Stress-cost (0.80 pt) numbers are NOT re-run -- cost_pts only affects the
per-trade P&L bookkeeping in harness.summarize/replay, never signal
generation or any entry gate, so exit prices are unchanged and the stress
result is recovered exactly from the same trade rows: raw = pts + base_cost;
pts_stress = raw - stress_cost. This is verified once against a real re-run
below (see verify_stress_recompute).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np   # noqa: E402
import pandas as pd  # noqa: E402
from lab.harness import Cfg, load_bars, replay  # noqa: E402

OUT = Path(__file__).resolve().parent / "results" / "s94_sweep.json"
OUT.parent.mkdir(exist_ok=True)

STRAT = "s94_sweep_reversal"
# Shortened window -- see module docstring AND lab/REPORT_s94.md for the explicit
# justification. A live timing probe (1.4mo, baseline cfg) measured ~134s/month
# under this session's CPU contention (several sibling sweeps running concurrently
# for S93/S99/S100), ~5x slower than the "5-10 min for 19.5mo" planning estimate.
# TRAIN=2mo / TEST=3mo keeps ~20 replays inside a ~2.5h wall-clock budget while
# still giving TEST n far above the n>=40 floor (baseline rate ~60 trades/month).
START, SPLIT, END = "2025-12-01", "2026-02-01", "2026-05-01"
BASE_COST = 0.45
STRESS_COST = 0.80
TIME_BUDGET_S = 150 * 60   # wall-clock cap for the whole sweep; low-priority arms
                           # (D) are skipped past this and reported as untested.
_t_start = time.time()

records = []


def _half_stats(d: pd.DataFrame) -> dict:
    if len(d) == 0:
        return dict(n=0, pts=0.0, pf=None, wr=0.0, maxdd=0.0)
    w, l = d[d.pts > 0], d[d.pts <= 0]
    gl = -l.pts.sum()
    eq = d.pts.cumsum()
    return dict(n=int(len(d)), pts=round(float(d.pts.sum()), 1),
                pf=round(float(w.pts.sum() / gl), 3) if gl > 0 else None,
                wr=round(100.0 * len(w) / len(d), 1),
                maxdd=round(float((eq - eq.cummax()).min()), 1))


def _slice(df: pd.DataFrame, lo, hi) -> pd.DataFrame:
    if len(df) == 0:
        return df
    m = (df.entry_time >= pd.Timestamp(lo, tz="UTC")) & (df.entry_time < pd.Timestamp(hi, tz="UTC"))
    return df[m]


def _with_stress(df: pd.DataFrame, base_cost: float, stress_cost: float) -> pd.DataFrame:
    """Recompute pts/r at a different round-trip cost without re-running the replay."""
    if len(df) == 0:
        return df
    df = df.copy()
    raw = df.pts + base_cost
    df["pts"] = raw - stress_cost
    df["r"] = np.where(df.risk > 0, df.pts / df.risk, 0.0)
    return df


def run(label: str, cfg: Cfg, bars) -> dict:
    t0 = time.time()
    res = replay(STRAT, bars, start=START, end=END, cfg=cfg)
    df = res["trades"]
    train = _slice(df, START, SPLIT)
    test = _slice(df, SPLIT, END)
    train_s = _with_stress(train, cfg.cost_pts, STRESS_COST)
    test_s = _with_stress(test, cfg.cost_pts, STRESS_COST)

    # effective k = |tp - entry| / risk, per trade (module always stores tp)
    def kstats(d):
        if len(d) == 0:
            return {}
        k = (d.tp - d.entry_px).abs() / d.risk.replace(0, np.nan)
        return dict(k_median=round(float(k.median()), 2),
                    k_q1=round(float(k.quantile(0.25)), 2),
                    k_q3=round(float(k.quantile(0.75)), 2))

    rec = dict(label=label, cost=cfg.cost_pts, min_sl=cfg.min_sl_dist_pts,
               block_hours=list(cfg.block_hours), env=dict(cfg.env or {}),
               patch={k: (list(v) if isinstance(v, tuple) else v) for k, v in (cfg.patch or {}).items()},
               train=_half_stats(train), test=_half_stats(test),
               train_stress=_half_stats(train_s), test_stress=_half_stats(test_s),
               train_k=kstats(train), test_k=kstats(test),
               dt=round(time.time() - t0, 1))
    records.append(rec)
    OUT.write_text(json.dumps(records, indent=2, default=str))
    print(f"[{rec['dt']:>5.1f}s] {label:<28} "
          f"TRAIN n={rec['train']['n']:<4} pts={rec['train']['pts']:>8} pf={rec['train']['pf']} | "
          f"TEST n={rec['test']['n']:<4} pts={rec['test']['pts']:>8} pf={rec['test']['pf']} wr={rec['test']['wr']} | "
          f"TEST@0.80 pts={rec['test_stress']['pts']:>8} pf={rec['test_stress']['pf']} | "
          f"k_med(test)={rec['test_k'].get('k_median')}",
          flush=True)
    return rec, df


def verify_stress_recompute(bars) -> None:
    """One real re-run at cost=0.80 to prove the arithmetic recompute is exact."""
    res_real = replay(STRAT, bars, start=START, end=SPLIT, cfg=Cfg(cost_pts=STRESS_COST))
    res_base = replay(STRAT, bars, start=START, end=SPLIT, cfg=Cfg(cost_pts=BASE_COST))
    df_base = res_base["trades"]
    recon = _with_stress(df_base, BASE_COST, STRESS_COST)
    real_pts = round(float(res_real["trades"].pts.sum()), 3) if len(res_real["trades"]) else 0.0
    recon_pts = round(float(recon.pts.sum()), 3) if len(recon) else 0.0
    ok = abs(real_pts - recon_pts) < 1e-6
    print(f"[VERIFY] real cost=0.80 total pts={real_pts}  recomputed={recon_pts}  MATCH={ok}", flush=True)
    if not ok:
        raise SystemExit("stress recompute does NOT match a real re-run -- abort sweep design")


def main():
    bars = load_bars()
    print(f"bars loaded; window {START}..{END}, split {SPLIT}", flush=True)

    verify_stress_recompute(bars)

    all_trades = {}

    # ---- baseline ----
    rec, df = run("BASELINE", Cfg(), bars)
    all_trades["BASELINE"] = df

    # ---- A. min_sl_dist_pts (top-prior hypothesis from the live book) ----
    for msl in (2.5, 3.5, 4.5):
        rec, df = run(f"A_minsl_{msl}", Cfg(min_sl_dist_pts=msl), bars)
        all_trades[f"A_minsl_{msl}"] = df

    # ---- C. _SD_MULT (TP multiple of break-bar leg -- directly sets k; coordinator
    #        flagged this as top-priority alongside F) ----
    for sd in (1.5, 2.5, 3.0):
        rec, df = run(f"C_SDMULT_{sd}", Cfg(patch={"_SD_MULT": sd}), bars)
        all_trades[f"C_SDMULT_{sd}"] = df

    # ---- F. _MIN_RR (coordinator-requested: break-even-WR arithmetic) ----
    for mr in (1.5, 2.0, 2.5):
        rec, df = run(f"F_MINRR_{mr}", Cfg(patch={"_MIN_RR": mr}), bars)
        all_trades[f"F_MINRR_{mr}"] = df

    # ---- B. block_hours ----
    for bh, tag in [((0, 1, 2, 3, 4, 5), "B_block_0-5"),
                    ((0, 1, 2, 3, 4, 5, 6, 23), "B_block_0-6_23")]:
        rec, df = run(tag, Cfg(block_hours=bh), bars)
        all_trades[tag] = df

    # ---- D. _STOP_BUF / _CONFIRM_N -- lowest priority; only run if the wall-clock
    #        budget allows (A+C+F+B already cover the coordinator's decisive tests) ----
    elapsed = time.time() - _t_start
    if elapsed < TIME_BUDGET_S:
        for sb in (0.05, 0.25):
            rec, df = run(f"D_STOPBUF_{sb}", Cfg(patch={"_STOP_BUF": sb}), bars)
            all_trades[f"D_STOPBUF_{sb}"] = df
        for cn in (10, 25):
            rec, df = run(f"D_CONFIRMN_{cn}", Cfg(patch={"_CONFIRM_N": cn}), bars)
            all_trades[f"D_CONFIRMN_{cn}"] = df
    else:
        print(f"\n[SKIP] dimension D skipped -- time budget {TIME_BUDGET_S}s exceeded "
              f"({elapsed:.0f}s elapsed)", flush=True)

    print("\n=== MAIN SWEEP DONE ===", flush=True)

    # ---- raw (pre-cost) WR by stop-distance bucket, baseline trades, train+test ----
    base_df = all_trades["BASELINE"]
    bins = [0, 2, 3, 4, 6, 9, np.inf]
    labels = ["<2", "2-3", "3-4", "4-6", "6-9", "9+"]
    for half_name, lo, hi in [("TRAIN", START, SPLIT), ("TEST", SPLIT, END)]:
        d = _slice(base_df, lo, hi).copy()
        if len(d) == 0:
            continue
        d["raw_pts"] = d.pts + BASE_COST
        d["bucket"] = pd.cut(d.risk, bins=bins, labels=labels)
        print(f"\n--- {half_name} raw WR by stop-distance bucket (baseline) ---")
        for b in labels:
            sub = d[d.bucket == b]
            if len(sub) == 0:
                continue
            raw_wr = 100.0 * (sub.raw_pts > 0).mean()
            cost_over_risk = 100.0 * BASE_COST / sub.risk.mean()
            print(f"  {b:<5} n={len(sub):<4} raw_WR={raw_wr:5.1f}%  "
                  f"cost/risk={cost_over_risk:5.1f}%  mean_risk={sub.risk.mean():.2f}pt")

    # save raw trades for the report step
    for k, d in all_trades.items():
        d.to_csv(OUT.parent / f"s94_trades_{k}.csv", index=False)

    print("\nSaved per-arm trade CSVs + s94_sweep.json", flush=True)


if __name__ == "__main__":
    main()
