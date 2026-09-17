"""lab/sweep.py -- parallel, resumable campaign runner over lab.harness.replay().

Why this exists: the 2026-09 campaign ran arms one at a time in one process, so a
six-arm S94 sweep took most of a night, and a killed process lost every arm that
had not yet been appended to its .log. This runner keeps the harness untouched
and changes only HOW arms are scheduled:

  * arms run in a process pool (default: cores - 2); every worker loads the bars
    cache once and then replays its arms, so per-arm cost is unchanged while a
    campaign's wall-clock drops by roughly the worker count;
  * Cfg.patch / Cfg.env are applied inside the worker by replay() itself, so
    arms cannot contaminate each other -- the failure that made concurrent
    sweeps unsafe in August;
  * each arm writes lab/results/<campaign>/<slug>.json (summary + timing) and
    <slug>.trades.parquet the moment it finishes. A re-run skips arms that
    already have a result file, so a kill costs only the in-flight arms;
  * an arm that raises writes <slug>.error.json with the traceback and the
    campaign carries on -- it never takes the other arms down with it.

Usage (a campaign file declares NAME and ARMS, nothing else):

    # lab/campaigns/s94_sdmult.py
    from lab.harness import Cfg
    from lab.sweep import Arm
    NAME = "s94_sdmult"
    W = dict(start="2025-11-01", end="2026-08-12", split="2026-02-01")
    ARMS = [Arm("sd2.0_c0.45", "s94_sweep_reversal", Cfg(cost_pts=0.45, patch={"_SD_MULT": 2.0}), **W),
            Arm("sd2.5_c0.45", "s94_sweep_reversal", Cfg(cost_pts=0.45, patch={"_SD_MULT": 2.5}), **W)]

    python -m lab.sweep lab/campaigns/s94_sdmult.py            # run (resumes)
    python -m lab.sweep lab/campaigns/s94_sdmult.py --summary  # tabulate results only
    python -m lab.sweep lab/campaigns/s94_sdmult.py --force    # re-run finished arms too

Results are POINTS-primary like the harness; USD is deliberately absent.
"""
from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import json
import os
import re
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from multiprocessing import get_context
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve().parent
_STRAT = _HERE.parent
if str(_STRAT) not in sys.path:
    sys.path.insert(0, str(_STRAT))

from lab.harness import Cfg, load_bars, replay   # noqa: E402

RESULTS = _HERE / "results"

# Summary columns copied from replay()'s dict into the result row / json.
_SUMMARY_KEYS = ("strategy", "cost", "min_sl", "block_hours", "n", "pts", "pf", "wr", "r",
                 "exp_r", "exp_pts", "maxdd_pts", "avg_win", "avg_loss")


@dataclass(frozen=True)
class Arm:
    """One replay: a label, the strategy module, its Cfg, and the window.

    `split` (optional, ISO date) additionally reports TRAIN (< split) and TEST
    (>= split) halves by entry_time -- the convention the S93/S94/S99/S100
    reports already use."""
    label: str
    strategy: str
    cfg: Cfg
    start: str
    end: str
    split: str | None = None


def slug(label: str) -> str:
    """Filesystem-safe name for a label: runs of anything but [A-Za-z0-9._-]
    collapse to one underscore, trimmed at the ends."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", label).strip("_")


def _cfg_json(cfg: Cfg) -> dict:
    d = dataclasses.asdict(cfg)
    d["block_hours"] = list(d["block_hours"])
    return d


def _half_stats(df: pd.DataFrame) -> dict:
    if not len(df):
        return dict(n=0, pts=0.0, pf=0.0, wr=0.0, maxdd_pts=0.0)
    w, l = df[df.pts > 0], df[df.pts <= 0]
    gp, gl = w.pts.sum(), -l.pts.sum()
    eq = df.pts.cumsum()
    return dict(n=int(len(df)), pts=round(float(df.pts.sum()), 1),
                pf=round(float(gp / gl), 3) if gl > 0 else float("inf"),
                wr=round(100.0 * len(w) / len(df), 1),
                maxdd_pts=round(float((eq - eq.cummax()).min()), 1))


def _halves(trades: pd.DataFrame, split: str) -> dict:
    if len(trades):
        cut = pd.Timestamp(split, tz="UTC")
        et = pd.to_datetime(trades["entry_time"], utc=True)
        parts = (("train", trades[et < cut]), ("test", trades[et >= cut]))
    else:
        parts = (("train", trades), ("test", trades))
    return {f"{name}_{k}": v for name, part in parts for k, v in _half_stats(part).items()}


# ---- worker side ------------------------------------------------------------------
_BARS: dict | None = None      # per-process cache: loaded once, reused for every arm


def _bars() -> dict:
    global _BARS
    if _BARS is None:
        _BARS = load_bars()
    return _BARS


def _run_one(arm: Arm, out_dir: str) -> dict:
    """Replay one arm and persist its result. Returns the summary row. Never
    raises: a failure is written to <slug>.error.json and returned as status=error."""
    d = Path(out_dir)
    # plain concatenation: Path.with_suffix() would treat the ".45" in "c0.45" as a suffix
    j_path = d / f"{slug(arm.label)}.json"
    t_path = d / f"{slug(arm.label)}.trades.parquet"
    e_path = d / f"{slug(arm.label)}.error.json"
    row = dict(label=arm.label, strategy=arm.strategy, start=arm.start, end=arm.end,
               split=arm.split, cfg=_cfg_json(arm.cfg))
    t0 = time.perf_counter()
    try:
        res = replay(arm.strategy, _bars(), start=arm.start, end=arm.end, cfg=arm.cfg)
        trades: pd.DataFrame = res["trades"].reset_index(drop=True)
        row.update({k: res[k] for k in _SUMMARY_KEYS if k in res})
        if arm.split:
            row.update(_halves(trades, arm.split))
        row["elapsed_s"] = round(time.perf_counter() - t0, 2)
        row["status"] = "ok"
        trades.to_parquet(t_path)
        _write_json(j_path, row)
        if e_path.exists():
            e_path.unlink()
    except Exception as exc:                        # noqa: BLE001 -- reported, not hidden
        row["elapsed_s"] = round(time.perf_counter() - t0, 2)
        row["status"] = "error"
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["traceback"] = traceback.format_exc()
        _write_json(e_path, row)
    return row


def _write_json(path: Path, row: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(row, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)          # atomic: a kill mid-write never leaves a half file


# ---- driver side ------------------------------------------------------------------
def _read_row(path: Path) -> dict:
    row = json.loads(path.read_text(encoding="utf-8"))
    row.pop("traceback", None)
    return row


def run_campaign(name: str, arms: list[Arm], out_dir: Path | str = RESULTS,
                 workers: int | None = None, force: bool = False,
                 progress: bool = False) -> pd.DataFrame:
    """Run every arm not already finished (unless `force`), across `workers`
    processes, and return one row per arm (finished, skipped, or errored)."""
    labels = [a.label for a in arms]
    if len(set(labels)) != len(labels):
        raise ValueError(f"{name}: duplicate arm labels: "
                         f"{sorted({l for l in labels if labels.count(l) > 1})}")
    d = Path(out_dir) / name
    d.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    todo: list[Arm] = []
    for arm in arms:
        f = d / f"{slug(arm.label)}.json"
        if f.exists() and not force:
            row = _read_row(f)
            row["status"] = "skipped"
            rows.append(row)
        else:
            todo.append(arm)

    if progress:
        print(f"[{name}] {len(todo)} to run, {len(rows)} already done", flush=True)

    if todo:
        workers = max(1, workers or (os.cpu_count() or 2) - 2)
        workers = min(workers, len(todo))
        if workers == 1:
            for arm in todo:
                rows.append(_run_one(arm, str(d)))
                if progress:
                    _print_row(rows[-1])
        else:
            # spawn: identical semantics on macOS/Linux/Windows, and no forked
            # copy of this process's (possibly patched) strategy modules.
            with ProcessPoolExecutor(max_workers=workers,
                                     mp_context=get_context("spawn")) as pool:
                futs = {pool.submit(_run_one, arm, str(d)): arm for arm in todo}
                for fut in as_completed(futs):
                    rows.append(fut.result())
                    if progress:
                        _print_row(rows[-1])

    order = {l: i for i, l in enumerate(labels)}
    rows.sort(key=lambda r: order[r["label"]])
    return pd.DataFrame(rows)


def load_campaign(name: str, out_dir: Path | str = RESULTS) -> pd.DataFrame:
    """Tabulate every result file of a campaign (finished and errored)."""
    d = Path(out_dir) / name
    rows = [_read_row(p) for p in sorted(d.glob("*.json")) if not p.name.endswith(".tmp")]
    return pd.DataFrame(rows)


def _print_row(r: dict) -> None:
    if r.get("status") == "error":
        print(f"  {r['label']:<28} ERROR {r.get('error')}", flush=True)
        return
    line = (f"  {r['label']:<28} n={r.get('n', 0):<5} pts={r.get('pts', 0):>8.1f} "
            f"pf={r.get('pf', 0):<6} wr={r.get('wr', 0):<5} dd={r.get('maxdd_pts', 0):>7.1f}")
    if "train_pf" in r:
        line += (f" | TRAIN n={r['train_n']} pf={r['train_pf']}"
                 f" | TEST n={r['test_n']} pf={r['test_pf']}")
    print(line + f"  ({r.get('elapsed_s', 0)}s, {r.get('status')})", flush=True)


def _load_campaign_file(path: str):
    spec = importlib.util.spec_from_file_location("_campaign", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)             # type: ignore[union-attr]
    if not hasattr(mod, "NAME") or not hasattr(mod, "ARMS"):
        raise SystemExit(f"{path}: a campaign file must define NAME and ARMS")
    return mod.NAME, list(mod.ARMS)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("campaign", help="python file defining NAME and ARMS")
    ap.add_argument("--workers", type=int, default=None, help="default: cores - 2")
    ap.add_argument("--force", action="store_true", help="re-run arms that already have results")
    ap.add_argument("--summary", action="store_true", help="only tabulate existing results")
    a = ap.parse_args(argv)

    name, arms = _load_campaign_file(a.campaign)
    if a.summary:
        df = load_campaign(name)
    else:
        t0 = time.perf_counter()
        df = run_campaign(name, arms, workers=a.workers, force=a.force, progress=True)
        print(f"[{name}] done in {time.perf_counter() - t0:.0f}s", flush=True)
    cols = [c for c in ("label", "status", "n", "pts", "pf", "wr", "maxdd_pts",
                        "train_n", "train_pf", "test_n", "test_pf", "elapsed_s") if c in df]
    with pd.option_context("display.width", 200, "display.max_rows", 500):
        print(df[cols].to_string(index=False))
    return 1 if (df.get("status") == "error").any() else 0


if __name__ == "__main__":
    raise SystemExit(main())
