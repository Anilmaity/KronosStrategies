"""run_arms.py -- r2_c03 campaign driver (PROTOCOL.md). Reuses lab.sweep's Arm/_run_one (json +
trades parquet per arm, resumable) but with a worker initializer that makes this folder's
`c03r2.py` importable as `concept_strategies.c03r2`, which lab.harness._load_module expects.
Every arm runs once at mid-M1 cost 0.75 (the trade list is cost-independent; score.py derives
1.00 and the quote-S5 costs).

    cd strategies && LAB_BARS_CACHE=backtest/results/bars_cache_2y \
        ../.venv/bin/python lab/research_s5/r2_c03/run_arms.py [--only H1,H2] [--workers 3]
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import get_context
from pathlib import Path

HERE = Path(__file__).resolve().parent
STRAT = HERE.parents[2]
if str(STRAT) not in sys.path:
    sys.path.insert(0, str(STRAT))
os.environ.setdefault("LAB_BARS_CACHE", str(STRAT / "backtest" / "results" / "bars_cache_2y"))

from lab.harness import Cfg          # noqa: E402
from lab.sweep import Arm, _print_row, _cfg_json, _halves, _write_json, _SUMMARY_KEYS   # noqa: E402
import pandas as pd   # noqa: E402
import traceback   # noqa: E402

OUT = HERE / "results"
W = dict(start="2024-09-01", end="2026-09-17", split="2025-12-01")
COST = 0.75
V = "c03r2"


_HARNESS = None
_BARS = None


def _init_worker(harness: str):
    global _HARNESS
    sys.path.insert(0, str(STRAT)); sys.path.insert(0, str(HERE))
    import concept_strategies
    if str(HERE) not in concept_strategies.__path__:
        concept_strategies.__path__.append(str(HERE))
    if harness == "fixed":
        import harness_fixed as h          # closed-bar M5/M15 windows (PROTOCOL H8)
    else:
        import lab.harness as h            # the shared harness (in-progress bar leak)
    _HARNESS = h


def _run_one(arm: Arm, out_dir: str) -> dict:
    """lab.sweep._run_one, with the replay taken from the selected harness."""
    global _BARS
    if _BARS is None:
        _BARS = _HARNESS.load_bars(tfs=("1m", "5m", "15m", "1d"))
    d = Path(out_dir)
    from lab.sweep import slug
    j_path, t_path, e_path = d / f"{slug(arm.label)}.json", d / f"{slug(arm.label)}.trades.parquet", d / f"{slug(arm.label)}.error.json"
    row = dict(label=arm.label, strategy=arm.strategy, start=arm.start, end=arm.end, split=arm.split,
               cfg=_cfg_json(arm.cfg), harness=_HARNESS.__name__)
    t0 = time.perf_counter()
    try:
        res = _HARNESS.replay(arm.strategy, _BARS, start=arm.start, end=arm.end, cfg=arm.cfg)
        trades = res["trades"].reset_index(drop=True)
        row.update({k: res[k] for k in _SUMMARY_KEYS if k in res})
        if arm.split:
            row.update(_halves(trades, arm.split))
        row["elapsed_s"] = round(time.perf_counter() - t0, 2); row["status"] = "ok"
        trades.to_parquet(t_path); _write_json(j_path, row)
    except Exception as exc:   # noqa: BLE001
        row.update(elapsed_s=round(time.perf_counter() - t0, 2), status="error", error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
        _write_json(e_path, row)
    return row


def arms(P: str) -> dict[str, list[Arm]]:
    """P = label prefix: 'F' (closed-bar harness_fixed) or 'L' (shared leaky harness)."""
    A = {}
    A["H0"] = [Arm("H0_base", "c03_fvg_fill", Cfg(cost_pts=COST), **W),
               Arm("H0_v_default", V, Cfg(cost_pts=COST), **W)]
    A["H1"] = [Arm(f"H1_minsl{f}", V, Cfg(cost_pts=COST, min_sl_dist_pts=f), **W) for f in (2.0, 2.5, 3.0, 3.5)]
    A["H2"] = [Arm(f"H2_slbuf{b}", V, Cfg(cost_pts=COST, patch={"_SL_BUF_ATR": b}), **W) for b in (0.0, 0.25, 0.50)]
    A["H3"] = ([Arm(f"H3_tplb{n}", V, Cfg(cost_pts=COST, patch={"_TP_LOOKBACK": n}), **W) for n in (10, 40)] +
               [Arm(f"H3_minrr{r}", V, Cfg(cost_pts=COST, patch={"_MIN_RR": r}), **W) for r in (1.5, 2.0)])
    A["H4"] = []
    for rule in ("prev_candle", "pdhl_engine"):
        A["H4"].append(Arm(f"H4_{rule}", V, Cfg(cost_pts=COST, patch={"_DAILY_GATE": rule}), **W))
        for s in (1, 2):
            A["H4"].append(Arm(f"H4_{rule}_shuffle{s}", V, Cfg(cost_pts=COST, patch={"_DAILY_GATE": f"{rule}:shuffle{s}"}), **W))
    A["H5"] = [Arm("H5_kz_all", V, Cfg(cost_pts=COST, patch={"_KZ": ((7, 20),)}), **W),
               Arm("H5_kz_add12", V, Cfg(cost_pts=COST, patch={"_KZ": ((7, 11), (12, 18))}), **W),
               Arm("H5_kz_narrow", V, Cfg(cost_pts=COST, patch={"_KZ": ((8, 11), (13, 16))}), **W)]
    A["H6"] = [Arm(f"H6_bias_{m}", V, Cfg(cost_pts=COST, patch={"_BIAS_MODE": m}), **W)
               for m in ("h1_resampled", "ema15_21", "none")]
    # H8b (PROTOCOL deviation 13:55): the other live strategy screened by the same harness
    A["S14"] = [Arm("S14_floor1.5", "s14_ob_mit_bias", Cfg(cost_pts=COST), **W),
                Arm("S14_floor3.0", "s14_ob_mit_bias", Cfg(cost_pts=COST, min_sl_dist_pts=3.0), **W)]
    return {k: [Arm(f"{P}_{a.label}", a.strategy, a.cfg, a.start, a.end, a.split) for a in v] for k, v in A.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--harness", choices=("fixed", "leaky"), default="fixed")
    ap.add_argument("--only", default=None, help="comma list of hypothesis keys, e.g. H0,H1")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    P = "F" if a.harness == "fixed" else "L"
    A = arms(P)
    keys = a.only.split(",") if a.only else list(A)
    todo = [arm for k in keys for arm in A[k]]
    if not a.force:
        todo = [arm for arm in todo if not (OUT / f"{arm.label}.json").exists()]
    print(f"[r2_c03] {len(todo)} arms to run with {min(a.workers, 3)} workers, harness={a.harness}", flush=True)
    t0 = time.perf_counter()
    rows = []
    with ProcessPoolExecutor(max_workers=min(a.workers, 3), mp_context=get_context("spawn"),
                             initializer=_init_worker, initargs=(a.harness,)) as pool:
        futs = {pool.submit(_run_one, arm, str(OUT)): arm for arm in todo}
        for fut in as_completed(futs):
            rows.append(fut.result())
            _print_row(rows[-1])
    print(f"[r2_c03] done in {time.perf_counter() - t0:.0f}s", flush=True)
    return 1 if any(r.get("status") == "error" for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
