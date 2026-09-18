"""Run the pre-registered arms (arms.py) with lab.sweep into THIS folder's results/.

    cd KronosStrategies/strategies
    LAB_BARS_CACHE=backtest/results/bars_cache_2y ../.venv/bin/python -m lab.research_s5.r2_s99_s100.run_sweep s99 --workers 3
    LAB_BARS_CACHE=backtest/results/bars_cache_2y ../.venv/bin/python -m lab.research_s5.r2_s99_s100.run_sweep s100 --workers 3

Resumable: finished arms are skipped (lab.sweep semantics).
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import pandas as pd

from lab.sweep import run_campaign
from lab.research_s5.r2_s99_s100.arms import S99_ARMS, S100_ARMS

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("which", choices=["s99", "s100"])
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--only", default=None, help="comma-separated arm labels")
    a = ap.parse_args()
    arms = S99_ARMS if a.which == "s99" else S100_ARMS
    if a.only:
        keep = set(a.only.split(","))
        arms = [x for x in arms if x.label in keep]
    t0 = time.perf_counter()
    df = run_campaign(f"sweep_{a.which}", arms, out_dir=OUT, workers=min(3, a.workers), progress=True)
    print(f"done in {time.perf_counter() - t0:.0f}s")
    cols = [c for c in ("label", "status", "n", "pts", "pf", "train_n", "train_pf", "test_n", "test_pf", "elapsed_s") if c in df]
    with pd.option_context("display.width", 200, "display.max_rows", 500):
        print(df[cols].to_string(index=False))
    return int((df.get("status") == "error").any())


if __name__ == "__main__":
    raise SystemExit(main())
