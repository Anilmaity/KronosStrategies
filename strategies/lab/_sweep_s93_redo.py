"""Second recovery pass. The first recovery run (lab/_sweep_s93_recover.py)
only pinned _HOURS via patch -- Group C (OLDBASE_6hr_c0.80 onward) and the
overlap probe show trade counts inconsistent with a monotonic min_sl filter
(e.g. OVERLAP minsl=2.0 TRAIN n=335 > NEWBASE minsl=1.5 TRAIN n=267 -- raising
the stop floor can only ever reject MORE trades, never fewer, so n going UP is
impossible unless some other module constant drifted mid-run). This points to
a second, transient concurrent edit to a constant other than _HOURS (most
likely _MIN_FVG_ATR) during that window, since reverted (current on-disk
constants match the documented/expected values again).

Fix: pin EVERY relevant constant explicitly on every call (_HOURS,
_MIN_FVG_ATR, _TP_R, _BUF_ATR), not just _HOURS. Also assert the monotonicity
invariant live so any further drift is caught immediately rather than
discovered after the fact.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
from lab.harness import Cfg, load_bars, replay  # noqa: E402

OUT = Path(__file__).resolve().parent / "results" / "s93_redo.json"
OUT.parent.mkdir(exist_ok=True)

STRAT = "s93_fvg_scalp"
START, END = "2025-01-05", "2026-08-12"
SPLIT = "2026-02-01"
OLD_HOURS = (7, 8, 9, 12, 13, 14)
NEW_HOURS = (13, 14)
PINNED = {"_TP_R": 1.5, "_BUF_ATR": 0.2}  # _HOURS and _MIN_FVG_ATR set per-call below

records: dict[str, dict] = {}


def _half(df, lo, hi):
    if len(df) == 0:
        return dict(n=0, pts=0.0, pf=None, wr=0.0, maxdd=0.0)
    m = (df.entry_time >= pd.Timestamp(lo, tz="UTC")) & (df.entry_time < pd.Timestamp(hi, tz="UTC"))
    d = df[m]
    if len(d) == 0:
        return dict(n=0, pts=0.0, pf=None, wr=0.0, maxdd=0.0)
    w, l = d[d.pts > 0], d[d.pts <= 0]
    gl = -l.pts.sum()
    eq = d.pts.cumsum()
    return dict(n=int(len(d)), pts=round(float(d.pts.sum()), 1),
                pf=round(float(w.pts.sum() / gl), 3) if gl > 0 else None,
                wr=round(100.0 * len(w) / len(d), 1),
                maxdd=round(float((eq - eq.cummax()).min()), 1))


def run(label, hours, min_fvg_atr, cfg_extra, bars):
    patch = dict(PINNED)
    patch["_HOURS"] = hours
    patch["_MIN_FVG_ATR"] = min_fvg_atr
    cfg = Cfg(patch=patch, **cfg_extra)
    t0 = time.time()
    res = replay(STRAT, bars, start=START, end=END, cfg=cfg)
    df = res["trades"]
    train = _half(df, START, SPLIT)
    test = _half(df, SPLIT, END)
    rec = dict(label=label, cost=cfg.cost_pts, min_sl=cfg.min_sl_dist_pts,
               hours=list(hours), min_fvg_atr=min_fvg_atr,
               train=train, test=test, dt=round(time.time() - t0, 1))
    records[label] = rec
    OUT.write_text(json.dumps(records, indent=2, default=str))
    print(f"[{rec['dt']:>5.1f}s] {label:<32} hours={hours} fvgATR={min_fvg_atr} msl={cfg.min_sl_dist_pts} cost={cfg.cost_pts} "
          f"TRAIN n={train['n']:<4} pts={train['pts']:>8} pf={train['pf']} | "
          f"TEST n={test['n']:<4} pts={test['pts']:>8} pf={test['pf']} wr={test['wr']} dd={test['maxdd']}",
          flush=True)
    return rec


def main():
    bars = load_bars()
    print("bars loaded -- FULL-PIN redo (every constant explicit on every call)", flush=True)

    r_base45 = run("OLDBASE_6hr_c0.45_CHECK", OLD_HOURS, 0.3, dict(cost_pts=0.45), bars)
    assert r_base45["train"]["n"] == 629 and r_base45["test"]["n"] == 361, \
        f"SANITY FAIL: expected 629/361, got {r_base45['train']['n']}/{r_base45['test']['n']}"
    print("  sanity OK: matches trusted phase-1 baseline exactly (629/361)\n", flush=True)

    r_base80 = run("OLDBASE_6hr_c0.80", OLD_HOURS, 0.3, dict(cost_pts=0.80), bars)

    prev_n = r_base45["train"]["n"]
    for msl in (2.0, 3.0, 3.5, 4.0):
        rec = run(f"A_minsl_{msl}_c0.80", OLD_HOURS, 0.3, dict(cost_pts=0.80, min_sl_dist_pts=msl), bars)
        if rec["train"]["n"] > prev_n:
            print(f"  ** MONOTONICITY VIOLATION ** msl={msl} train n={rec['train']['n']} > previous {prev_n}",
                  flush=True)
        prev_n = rec["train"]["n"]

    run("C_MINFVGATR_0.2_c0.80", OLD_HOURS, 0.2, dict(cost_pts=0.80), bars)

    print("\n--- overlap probe (min_sl WITHIN shipped 13,14), full-pinned ---\n", flush=True)
    r_new45 = run("NEWBASE_13_14_c0.45_CHECK", NEW_HOURS, 0.3, dict(cost_pts=0.45), bars)
    assert r_new45["train"]["n"] == 267 and r_new45["test"]["n"] == 132, \
        f"SANITY FAIL: expected 267/132, got {r_new45['train']['n']}/{r_new45['test']['n']}"
    print("  sanity OK: matches trusted (13,14) reference exactly (267/132)\n", flush=True)

    prev_n = r_new45["train"]["n"]
    for msl in (2.0, 2.5, 3.0):
        rec = run(f"OVERLAP_13_14_minsl_{msl}_c0.45", NEW_HOURS, 0.3, dict(min_sl_dist_pts=msl), bars)
        if rec["train"]["n"] > prev_n:
            print(f"  ** MONOTONICITY VIOLATION ** msl={msl} train n={rec['train']['n']} > previous {prev_n}",
                  flush=True)
        prev_n = rec["train"]["n"]

    print("\n=== REDO DONE ===", flush=True)


if __name__ == "__main__":
    main()
