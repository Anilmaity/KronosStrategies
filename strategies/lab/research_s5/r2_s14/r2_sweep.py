"""r2_s14/r2_sweep.py -- lab/sweep.py's runner pointed at r2_s14/harness_closed.py.

Same arm/result semantics as lab.sweep (json + trades.parquet per arm, resumable, spawn
pool), but replay() comes from the study's harness copy so Cfg.closed_frames is honoured
and strategy variant modules in this folder are importable (sys.path includes r2_s14).
Results go to r2_s14/results/<campaign>/. Never more than 3 workers (CONTEXT.md §7).

    ../../../../.venv/bin/python r2_sweep.py campaigns/h0_closed_frames.py --workers 3
"""
from __future__ import annotations

import argparse
import dataclasses
import importlib
import importlib.util
import json
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from multiprocessing import get_context
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve().parent
_STRAT = _HERE.parents[2]
for p in (str(_STRAT), str(_HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ.setdefault("LAB_BARS_CACHE", str(_STRAT / "backtest" / "results" / "bars_cache_2y"))

import harness_closed as H   # noqa: E402
from harness_closed import Cfg  # noqa: E402

RESULTS = _HERE / "results"
_SUMMARY_KEYS = ("strategy", "cost", "min_sl", "block_hours", "sides", "regime", "n", "pts", "pf", "wr", "r",
                 "exp_r", "exp_pts", "maxdd_pts", "avg_win", "avg_loss")


@dataclass(frozen=True)
class Arm:
    label: str
    strategy: str      # module name; a bare name is resolved by harness_closed._load_module,
                       # a name with a dot ("r2mod.s14_v1") is imported as-is from this folder
    cfg: Cfg
    start: str
    end: str
    split: str | None = None


_load_module_orig = H._load_module


def _load_module(name: str):
    if "." in name:
        return importlib.import_module(name)
    return _load_module_orig(name)


H._load_module = _load_module


def _cfg_json(cfg):
    d = dataclasses.asdict(cfg)
    d["block_hours"] = list(d["block_hours"]); d["sides"] = list(d["sides"])
    return d


def _half_stats(df):
    if not len(df):
        return dict(n=0, pts=0.0, pf=0.0, wr=0.0, maxdd_pts=0.0)
    w, l = df[df.pts > 0], df[df.pts <= 0]
    gp, gl = w.pts.sum(), -l.pts.sum()
    eq = df.pts.cumsum()
    return dict(n=int(len(df)), pts=round(float(df.pts.sum()), 1),
                pf=round(float(gp / gl), 3) if gl > 0 else float("inf"),
                wr=round(100.0 * len(w) / len(df), 1),
                maxdd_pts=round(float((eq - eq.cummax()).min()), 1))


def _halves(trades, split):
    if len(trades):
        cut = pd.Timestamp(split, tz="UTC")
        et = pd.to_datetime(trades["entry_time"], utc=True)
        parts = (("train", trades[et < cut]), ("test", trades[et >= cut]))
    else:
        parts = (("train", trades), ("test", trades))
    return {f"{name}_{k}": v for name, part in parts for k, v in _half_stats(part).items()}


_BARS = None


def _bars():
    global _BARS
    if _BARS is None:
        _BARS = H.load_bars(tfs=("1m", "5m", "15m", "1d"))
    return _BARS


def _run_one(arm: Arm, out_dir: str) -> dict:
    d = Path(out_dir)
    j_path, t_path, e_path = d / f"{arm.label}.json", d / f"{arm.label}.trades.parquet", d / f"{arm.label}.error.json"
    row = dict(label=arm.label, strategy=arm.strategy, start=arm.start, end=arm.end, split=arm.split,
               cfg=_cfg_json(arm.cfg))
    t0 = time.perf_counter()
    try:
        res = H.replay(arm.strategy, _bars(), start=arm.start, end=arm.end, cfg=arm.cfg)
        trades = res["trades"].reset_index(drop=True)
        row.update({k: res[k] for k in _SUMMARY_KEYS if k in res})
        if arm.split:
            row.update(_halves(trades, arm.split))
        row["elapsed_s"] = round(time.perf_counter() - t0, 2); row["status"] = "ok"
        trades.to_parquet(t_path)
        j_path.write_text(json.dumps(row, indent=1, default=str))
        if e_path.exists():
            e_path.unlink()
    except Exception as exc:  # noqa: BLE001
        row["elapsed_s"] = round(time.perf_counter() - t0, 2); row["status"] = "error"
        row["error"] = f"{type(exc).__name__}: {exc}"; row["traceback"] = traceback.format_exc()
        e_path.write_text(json.dumps(row, indent=1, default=str))
    return row


def run_campaign(name, arms, workers=3, force=False):
    workers = min(3, workers)
    d = RESULTS / name; d.mkdir(parents=True, exist_ok=True)
    rows, todo = [], []
    for arm in arms:
        f = d / f"{arm.label}.json"
        if f.exists() and not force:
            r = json.loads(f.read_text()); r["status"] = "skipped"; rows.append(r)
        else:
            todo.append(arm)
    print(f"[{name}] {len(todo)} to run, {len(rows)} done", flush=True)
    if todo:
        if workers == 1 or len(todo) == 1:
            for arm in todo:
                rows.append(_run_one(arm, str(d))); _print(rows[-1])
        else:
            with ProcessPoolExecutor(max_workers=min(workers, len(todo)), mp_context=get_context("spawn")) as pool:
                futs = {pool.submit(_run_one, arm, str(d)): arm for arm in todo}
                for fut in as_completed(futs):
                    rows.append(fut.result()); _print(rows[-1])
    order = {a.label: i for i, a in enumerate(arms)}
    rows.sort(key=lambda r: order[r["label"]])
    return pd.DataFrame(rows)


def _print(r):
    if r.get("status") == "error":
        print(f"  {r['label']:<36} ERROR {r.get('error')}", flush=True); return
    line = (f"  {r['label']:<36} n={r.get('n', 0):<5} pts={r.get('pts', 0):>8.1f} pf={r.get('pf', 0):<6} "
            f"wr={r.get('wr', 0):<5} dd={r.get('maxdd_pts', 0):>7.1f}")
    if "train_pf" in r:
        line += f" | TRAIN n={r['train_n']} pts={r['train_pts']} pf={r['train_pf']} | TEST n={r['test_n']} pts={r['test_pts']} pf={r['test_pf']}"
    print(line + f"  ({r.get('elapsed_s', 0)}s)", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("campaign"); ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    spec = importlib.util.spec_from_file_location("_campaign", a.campaign)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    t0 = time.perf_counter()
    arms = list(mod.ARMS)
    sl = os.environ.get("R2_SLICE")          # "k/n": run arms with index % n == k (in-process drivers)
    if sl:
        k, n = (int(x) for x in sl.split("/"))
        arms = [x for i, x in enumerate(arms) if i % n == k]
    if os.environ.get("R2_REVERSE"):           # a second driver on the same slice works from the far end
        arms = arms[::-1]
    df = run_campaign(mod.NAME, arms, workers=a.workers, force=a.force)
    print(f"[{mod.NAME}] done in {time.perf_counter() - t0:.0f}s")
    cols = [c for c in ("label", "status", "n", "pts", "pf", "train_n", "train_pts", "train_pf",
                        "test_n", "test_pts", "test_pf", "elapsed_s") if c in df]
    with pd.option_context("display.width", 250, "display.max_rows", 500):
        print(df[cols].to_string(index=False))
    return 1 if (df.get("status") == "error").any() else 0


if __name__ == "__main__":
    raise SystemExit(main())
