"""Recovery sweep after mid-run contamination (coordinator shipped _HOURS
(7,8,9,12,13,14) -> (13,14) in backtest_strategies/s93_fvg_scalp.py while the
first full sweep was running). From here on EVERY run explicitly patches
_HOURS to the exact set intended, regardless of the module's current shipped
default, so results are immune to any further concurrent edits to the shared
strategy file.

Produces:
  Group A: clean (13,14)-hours reference, both filters ON, cost 0.45 + 0.80
           (the 2 replays the coordinator asked for).
  Group B: D-dimension (SOFT_VETO / GAP_CAP_ATR) complete table under the
           (13,14) reference, cost 0.45 + 0.80 stress.
  Group C: stress (0.80) for the OLD 6-hour-baseline-family arms that passed
           the phase-1 screen (min_sl_dist_pts 2.0/3.0/3.5/4.0, MIN_FVG_ATR 0.2),
           all explicitly pinned to _HOURS=(7,8,9,12,13,14).
  Group D: overlap probe -- min_sl_dist_pts swept WITHIN the shipped (13,14)
           hours, cost 0.45, + stress for the best value.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
from lab.harness import Cfg, load_bars, replay  # noqa: E402

OUT = Path(__file__).resolve().parent / "results" / "s93_recover.json"
OUT.parent.mkdir(exist_ok=True)

STRAT = "s93_fvg_scalp"
START, END = "2025-01-05", "2026-08-12"
SPLIT = "2026-02-01"
OLD_HOURS = (7, 8, 9, 12, 13, 14)
NEW_HOURS = (13, 14)

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


def run(label: str, cfg: Cfg, bars) -> dict:
    if label in records:
        return records[label]
    t0 = time.time()
    res = replay(STRAT, bars, start=START, end=END, cfg=cfg)
    df = res["trades"]
    train = _half(df, START, SPLIT)
    test = _half(df, SPLIT, END)
    rec = dict(label=label, cost=cfg.cost_pts, min_sl=cfg.min_sl_dist_pts,
               block_hours=list(cfg.block_hours), env=dict(cfg.env or {}),
               patch={k: (list(v) if isinstance(v, tuple) else v) for k, v in (cfg.patch or {}).items()},
               train=train, test=test, dt=round(time.time() - t0, 1))
    records[label] = rec
    OUT.write_text(json.dumps(records, indent=2, default=str))
    print(f"[{rec['dt']:>5.1f}s] {label:<40} "
          f"TRAIN n={train['n']:<4} pts={train['pts']:>8} pf={train['pf']} | "
          f"TEST n={test['n']:<4} pts={test['pts']:>8} pf={test['pf']} wr={test['wr']} "
          f"dd={test['maxdd']}", flush=True)
    return rec


def main():
    bars = load_bars()
    print("bars loaded -- RECOVERY sweep, every run explicitly pins _HOURS", flush=True)

    # ---------------- Group A: clean (13,14) reference, both filters ON ----------------
    run("NEWBASE_13_14_c0.45", Cfg(patch={"_HOURS": NEW_HOURS}), bars)
    run("NEWBASE_13_14_c0.80", Cfg(cost_pts=0.80, patch={"_HOURS": NEW_HOURS}), bars)

    # ---------------- Group B: D-dimension under (13,14), complete table ----------------
    run("D_veto_off_c0.80", Cfg(cost_pts=0.80, patch={"_HOURS": NEW_HOURS},
                                env={"S93_SOFT_VETO": "off"}), bars)
    for g in (1.0, 2.5):
        run(f"D_gapcap_{g}_c0.45", Cfg(patch={"_HOURS": NEW_HOURS},
                                       env={"S93_GAP_CAP_ATR": g}), bars)
    for g in (0, 1.0, 2.5):
        run(f"D_gapcap_{g}_c0.80", Cfg(cost_pts=0.80, patch={"_HOURS": NEW_HOURS},
                                       env={"S93_GAP_CAP_ATR": g}), bars)

    # ---------------- Group C: OLD 6-hour-baseline family, 0.80 stress ----------------
    run("OLDBASE_6hr_c0.80", Cfg(cost_pts=0.80, patch={"_HOURS": OLD_HOURS}), bars)
    for msl in (2.0, 3.0, 3.5, 4.0):
        run(f"A_minsl_{msl}_c0.80", Cfg(cost_pts=0.80, min_sl_dist_pts=msl,
                                        patch={"_HOURS": OLD_HOURS}), bars)
    run("C_MINFVGATR_0.2_c0.80", Cfg(cost_pts=0.80, patch={"_HOURS": OLD_HOURS,
                                                            "_MIN_FVG_ATR": 0.2}), bars)

    print("\n=== GROUPS A-C DONE ===\n", flush=True)

    # ---------------- Group D: overlap probe -- min_sl WITHIN shipped (13,14) ----------
    overlap = {}
    for msl in (2.0, 2.5, 3.0):
        overlap[msl] = run(f"OVERLAP_13_14_minsl_{msl}_c0.45",
                           Cfg(min_sl_dist_pts=msl, patch={"_HOURS": NEW_HOURS}), bars)

    base_new = records["NEWBASE_13_14_c0.45"]
    best_msl = None
    best_pf = base_new["test"]["pf"] or 0
    for msl, rec in overlap.items():
        t = rec["test"]
        if t["n"] >= 60 and t["pf"] is not None and t["pf"] > best_pf and t["pts"] > base_new["test"]["pts"]:
            if t["pf"] > best_pf:
                best_pf = t["pf"]
                best_msl = msl
    print(f"Overlap screen best candidate: msl={best_msl} (pf={best_pf}) "
          f"vs new-base test pf={base_new['test']['pf']}", flush=True)
    if best_msl is not None:
        run(f"OVERLAP_13_14_minsl_{best_msl}_c0.80",
            Cfg(cost_pts=0.80, min_sl_dist_pts=best_msl, patch={"_HOURS": NEW_HOURS}), bars)
    else:
        print("No overlap min_sl candidate beat the (13,14) reference -- no stress needed.",
              flush=True)

    print("\n=== ALL DONE ===", flush=True)


if __name__ == "__main__":
    main()
