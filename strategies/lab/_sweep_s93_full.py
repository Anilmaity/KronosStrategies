"""S93 FVG scalp optimization sweep -- SEQUENTIAL, single process (no concurrent
background jobs -- two concurrent replay processes crashed earlier in this
campaign, exit 1, no traceback, likely a per-session resource cap).

Run entirely under the CURRENT harness.py (post warm-up-fix: i0 accounts for
win_5m/win_15m, not just win_1m). All numbers in this file's output are
therefore internally consistent -- no cross-version comparison needed.

Phase 1 (cheap screen, cost=0.45 only): baseline + every dimension arm.
Phase 2 (stress, cost=0.80): baseline + only the arms that passed the
    phase-1 screen (test PF and test points both improve vs baseline).
Phase 3: overlap probe -- min_sl_dist_pts swept WITHIN the best surviving
    hour-arm, if any hour-arm passed phase 1+2.
Phase 4: best combination of everything that passed, + its own stress run.

Writes lab/results/s93_full.json incrementally.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
from lab.harness import Cfg, load_bars, replay  # noqa: E402

OUT = Path(__file__).resolve().parent / "results" / "s93_full.json"
OUT.parent.mkdir(exist_ok=True)

STRAT = "s93_fvg_scalp"
START, END = "2025-01-05", "2026-08-12"
SPLIT = "2026-02-01"

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


def passes_screen(rec, base_rec, min_n=60):
    t, bt = rec["test"], base_rec["test"]
    if t["n"] < min_n or t["pf"] is None:
        return False
    return (t["pf"] > (bt["pf"] or 0)) and (t["pts"] > bt["pts"])


def main():
    bars = load_bars()
    print("bars loaded -- running under CURRENT (post warm-up-fix) harness.py", flush=True)

    # =========================== PHASE 1 (cost=0.45) ===========================
    base = run("BASELINE_c0.45", Cfg(), bars)

    # -- A: min_sl_dist_pts --
    A = {}
    for msl in (2.0, 2.5, 3.0, 3.5, 4.0):
        A[msl] = run(f"A_minsl_{msl}_c0.45", Cfg(min_sl_dist_pts=msl), bars)

    # -- HOURS (folds in coordinator's high-priority finding + original plan B) --
    HOUR_ARMS = {
        "block12_gate":        dict(cfg=Cfg(block_hours=(12,))),
        "drop12_patch":        dict(cfg=Cfg(patch={"_HOURS": (7, 8, 9, 13, 14)})),
        "H_13_14":             dict(cfg=Cfg(patch={"_HOURS": (13, 14)})),
        "H_9_13_14":           dict(cfg=Cfg(patch={"_HOURS": (9, 13, 14)})),
        "H_8_9_13_14":         dict(cfg=Cfg(patch={"_HOURS": (8, 9, 13, 14)})),
        "H_12_13_14":          dict(cfg=Cfg(patch={"_HOURS": (12, 13, 14)})),
    }
    HOURS = {}
    for name, d in HOUR_ARMS.items():
        HOURS[name] = run(f"HOURS_{name}_c0.45", d["cfg"], bars)

    # -- C: _TP_R --
    TPR = {}
    for tp in (1.0, 2.0, 2.5):
        TPR[tp] = run(f"C_TPR_{tp}_c0.45", Cfg(patch={"_TP_R": tp}), bars)

    # -- C: _MIN_FVG_ATR --
    FVGATR = {}
    for a in (0.2, 0.45, 0.6):
        FVGATR[a] = run(f"C_MINFVGATR_{a}_c0.45", Cfg(patch={"_MIN_FVG_ATR": a}), bars)

    # -- D: SOFT_VETO / GAP_CAP --
    D = {}
    D["veto_off"] = run("D_veto_off_c0.45", Cfg(env={"S93_SOFT_VETO": "off"}), bars)
    for g in (0, 1.0, 2.5):
        D[f"gapcap_{g}"] = run(f"D_gapcap_{g}_c0.45", Cfg(env={"S93_GAP_CAP_ATR": g}), bars)

    print("\n=== PHASE 1 DONE ===\n", flush=True)

    # =========================== SCREEN ===========================
    def cfg_for_label(label):
        # reconstruct the Cfg used for a given phase-1 label so we can re-run at 0.80
        if label.startswith("A_minsl_"):
            msl = float(label.split("_")[2])
            return Cfg(min_sl_dist_pts=msl, cost_pts=0.80)
        if label.startswith("HOURS_"):
            name = label[len("HOURS_"):-len("_c0.45")]
            base_cfg = HOUR_ARMS[name]["cfg"]
            return Cfg(cost_pts=0.80, min_sl_dist_pts=base_cfg.min_sl_dist_pts,
                       block_hours=base_cfg.block_hours, patch=dict(base_cfg.patch or {}),
                       env=dict(base_cfg.env or {}))
        if label.startswith("C_TPR_"):
            tp = float(label.split("_")[2])
            return Cfg(cost_pts=0.80, patch={"_TP_R": tp})
        if label.startswith("C_MINFVGATR_"):
            a = float(label.split("_")[2])
            return Cfg(cost_pts=0.80, patch={"_MIN_FVG_ATR": a})
        if label == "D_veto_off_c0.45":
            return Cfg(cost_pts=0.80, env={"S93_SOFT_VETO": "off"})
        if label.startswith("D_gapcap_"):
            g = label.split("_")[2]
            g = float(g) if "." in g else int(g)
            return Cfg(cost_pts=0.80, env={"S93_GAP_CAP_ATR": g})
        raise KeyError(label)

    phase1_all = {**{f"A_minsl_{k}_c0.45": v for k, v in A.items()},
                  **{f"HOURS_{k}_c0.45": v for k, v in HOURS.items()},
                  **{f"C_TPR_{k}_c0.45": v for k, v in TPR.items()},
                  **{f"C_MINFVGATR_{k}_c0.45": v for k, v in FVGATR.items()},
                  **{f"D_{k}_c0.45": v for k, v in D.items()}}

    candidates = [lbl for lbl, rec in phase1_all.items() if passes_screen(rec, base)]
    print("Phase-1 screen -- candidates improving TEST pf & pts vs baseline (n>=60):", flush=True)
    for c in candidates:
        print("   ", c, phase1_all[c]["test"], flush=True)

    # =========================== PHASE 2 (stress, cost=0.80) ===========================
    base80 = run("BASELINE_c0.80", Cfg(cost_pts=0.80), bars)
    for lbl in candidates:
        stress_lbl = lbl.replace("_c0.45", "_c0.80")
        run(stress_lbl, cfg_for_label(lbl), bars)

    print("\n=== PHASE 2 DONE ===\n", flush=True)

    survivors = []
    for lbl in candidates:
        stress_lbl = lbl.replace("_c0.45", "_c0.80")
        rec80 = records[stress_lbl]
        ok80 = passes_screen(rec80, base80)
        print(f"{lbl}: passes @0.80 stress = {ok80}", flush=True)
        if ok80:
            survivors.append(lbl)

    print(f"\nSurvivors of screen + stress (still need plateau/mechanism review): {survivors}\n", flush=True)

    # =========================== PHASE 3: overlap probe ===========================
    hour_survivors = [s for s in survivors if s.startswith("HOURS_")]
    if hour_survivors:
        best_lbl = max(hour_survivors, key=lambda l: phase1_all[l]["test"]["pf"] or 0)
        best_name = best_lbl[len("HOURS_"):-len("_c0.45")]
        best_hrs = HOUR_ARMS[best_name]["cfg"].patch.get("_HOURS")
        best_block = HOUR_ARMS[best_name]["cfg"].block_hours
        print(f"Overlap probe: sweeping min_sl WITHIN best hour arm {best_name} "
              f"(_HOURS={best_hrs}, block_hours={best_block})", flush=True)
        for msl in (2.0, 2.5, 3.0):
            patch = {"_HOURS": best_hrs} if best_hrs else {}
            run(f"OVERLAP_{best_name}_minsl_{msl}_c0.45",
                Cfg(min_sl_dist_pts=msl, block_hours=best_block, patch=patch), bars)
        for msl in (2.0, 2.5, 3.0):
            patch = {"_HOURS": best_hrs} if best_hrs else {}
            run(f"OVERLAP_{best_name}_minsl_{msl}_c0.80",
                Cfg(cost_pts=0.80, min_sl_dist_pts=msl, block_hours=best_block, patch=patch), bars)
    else:
        print("No hour arm survived screen+stress -- skipping overlap probe.", flush=True)

    print("\n=== ALL DONE ===", flush=True)


if __name__ == "__main__":
    main()
