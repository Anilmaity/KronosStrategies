"""PROTOCOL.md §1 appendix: re-run the four survivors through harness_gate.py with the B1
gate INSIDE the harness and compare with the filtered saved trade list.

    cd strategies && LAB_BARS_CACHE=backtest/results/bars_cache_2y \
        ../.venv/bin/python -m lab.research_s5.r2_htf_overlay.rerun_check
"""
from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
_STRAT = _HERE.parents[2]
os.environ.setdefault("LAB_BARS_CACHE", str(_STRAT / "backtest" / "results" / "bars_cache_2y"))
for p in (str(_STRAT), str(_HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import htf_bias as hb            # noqa: E402
import harness_gate as hg        # noqa: E402

RES = _HERE / "results"
SURVIVORS = ["s93_fvg_scalp", "c03_fvg_fill", "s14_ob_mit_bias", "s95_session_breakout"]
WIN = {"s95_session_breakout": dict(win_5m=300)}   # Stage-1 campaign override (xau2y_stage1.py)


class B1Gate:
    """Newest closed valid NY daily candle's previous-candle-engine bias vs the side."""
    def __init__(self):
        d = hb.ny_daily(hb.load_m1())
        dv = d[d["valid"]].reset_index(drop=True)
        eng = hb.previous_candle_engine(dv)
        self.closes = dv["utc_end_close"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy("datetime64[ns]")
        self.bias = eng["implied_bias"].to_numpy()

    def __call__(self, now, side, entry_px) -> bool:
        fill = np.datetime64(pd.Timestamp(now).tz_convert("UTC").tz_localize(None), "ns") + np.timedelta64(60, "s")
        i = int(np.searchsorted(self.closes, fill, side="right")) - 1
        if i < 0:
            return False
        return self.bias[i] == ("bullish" if side == "BUY" else "bearish")


def run_one(mod: str) -> dict:
    t0 = time.time()
    bars = hg.load_bars(tfs=("1m", "5m", "15m", "1d"))
    cfg = hg.Cfg(cost_pts=0.80, entry_gate=B1Gate(), **WIN.get(mod, {}))
    res = hg.replay(mod, bars, start="2024-09-01", end="2026-09-17", cfg=cfg)
    tr = res["trades"]
    tr.to_parquet(RES / f"rerun_B1_{mod}.parquet", index=False)
    return dict(module=mod, elapsed_s=round(time.time() - t0), n=res["n"], pts=res["pts"], pf=res["pf"])


def compare(mod: str) -> dict:
    rr = pd.read_parquet(RES / f"rerun_B1_{mod}.parquet")
    rr["entry_time"] = pd.to_datetime(rr["entry_time"], utc=True)
    lab = pd.read_parquet(RES / f"labels_{mod}.parquet")
    lab["entry_time"] = pd.to_datetime(lab["entry_time"], utc=True)
    filt = lab[lab["B1"] == "aligned"]
    key_f = set(filt["entry_time"]); key_r = set(rr["entry_time"])
    common = key_f & key_r
    only_r = rr[~rr["entry_time"].isin(key_f)]
    only_f = filt[~filt["entry_time"].isin(key_r)]
    pf = lambda p: float(p[p > 0].sum() / -p[p <= 0].sum()) if (p <= 0).any() else float("inf")
    return dict(module=mod, filtered_n=len(filt), filtered_pts=round(float(filt.pts.sum()), 1), filtered_pf=round(pf(filt.pts), 3),
                rerun_n=len(rr), rerun_pts=round(float(rr.pts.sum()), 1), rerun_pf=round(pf(rr.pts), 3),
                common=len(common), only_in_rerun=len(only_r), only_in_rerun_pts=round(float(only_r.pts.sum()), 1),
                only_in_filtered=len(only_f), only_in_filtered_pts=round(float(only_f.pts.sum()), 1))


if __name__ == "__main__":
    todo = [m for m in SURVIVORS if not (RES / f"rerun_B1_{m}.parquet").exists()]
    if todo:
        with ProcessPoolExecutor(max_workers=3, mp_context=get_context("spawn")) as ex:
            for r in ex.map(run_one, todo):
                print(r, flush=True)
    rows = [compare(m) for m in SURVIVORS]
    df = pd.DataFrame(rows)
    df.to_csv(RES / "rerun_check.csv", index=False)
    print(df.to_string(index=False))
