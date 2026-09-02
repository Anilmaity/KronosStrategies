"""lab/run.py -- experiment CLI over the offline harness.

Every experiment writes a JSON result + a trades CSV under lab/results/, so a
sweep is resumable and comparable across sub-agents.

Examples
--------
  # baseline, full cached period
  python -m lab.run --strategy s93_fvg_scalp --tag base

  # stop-distance floor sweep, train/test split
  python -m lab.run --strategy s93_fvg_scalp --tag slfloor \
      --min-sl 1.5 2.0 2.5 3.0 3.5 4.0 --split 2026-02-01

  # block Asia hours
  python -m lab.run --strategy s94_sweep_reversal --tag hours --block-hours 0 1 2 3
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab.harness import Cfg, fmt, load_bars, replay   # noqa: E402

OUT = Path(__file__).resolve().parent / "results"
OUT.mkdir(exist_ok=True)

FULL_START, FULL_END = "2025-01-05", "2026-08-12"


def _slice(res: dict, lo=None, hi=None) -> dict:
    """Recompute headline stats over a sub-period of an existing result."""
    import pandas as pd
    df = res["trades"]
    if len(df) == 0:
        return {"n": 0, "pts": 0.0, "pf": 0.0, "wr": 0.0, "r": 0.0, "exp_r": 0.0}
    m = pd.Series(True, index=df.index)
    if lo is not None:
        m &= df.entry_time >= pd.Timestamp(lo, tz="UTC")
    if hi is not None:
        m &= df.entry_time < pd.Timestamp(hi, tz="UTC")
    d = df[m]
    if len(d) == 0:
        return {"n": 0, "pts": 0.0, "pf": 0.0, "wr": 0.0, "r": 0.0, "exp_r": 0.0}
    w, l = d[d.pts > 0], d[d.pts <= 0]
    gl = -l.pts.sum()
    eq = d.pts.cumsum()
    return {"n": int(len(d)), "pts": round(float(d.pts.sum()), 1),
            "pf": round(float(w.pts.sum() / gl), 3) if gl > 0 else None,
            "wr": round(100.0 * len(w) / len(d), 1),
            "r": round(float(d.r.sum()), 2), "exp_r": round(float(d.r.mean()), 4),
            "maxdd_pts": round(float((eq - eq.cummax()).min()), 1)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--tag", default="run")
    ap.add_argument("--start", default=FULL_START)
    ap.add_argument("--end", default=FULL_END)
    ap.add_argument("--split", default=None, help="train/test boundary date")
    ap.add_argument("--cost", type=float, nargs="+", default=[0.45])
    ap.add_argument("--min-sl", type=float, nargs="+", default=[1.5])
    ap.add_argument("--block-hours", type=int, nargs="*", default=None,
                    help="one arm; repeat the flag via --grid-hours for a sweep")
    ap.add_argument("--grid-hours", nargs="*", default=None,
                    help='arms like "" "0,1,2,3" "0,1,2,3,12"')
    ap.add_argument("--blackout", default="12:25-12:45")
    ap.add_argument("--env", nargs="*", default=[], help="K=V pairs")
    ap.add_argument("--save-trades", action="store_true")
    a = ap.parse_args()

    env = dict(kv.split("=", 1) for kv in a.env)
    hour_arms = ([tuple(int(x) for x in s.split(",") if x != "") for s in a.grid_hours]
                 if a.grid_hours is not None
                 else [tuple(a.block_hours or ())])

    bars = load_bars()
    out = []
    for cost in a.cost:
        for msl in a.min_sl:
            for hrs in hour_arms:
                cfg = Cfg(cost_pts=cost, min_sl_dist_pts=msl, block_hours=hrs,
                          news_blackout=a.blackout, env=env)
                res = replay(a.strategy, bars, start=a.start, end=a.end, cfg=cfg)
                rec = {k: v for k, v in res.items() if k != "trades"}
                if a.split:
                    rec["train"] = _slice(res, a.start, a.split)
                    rec["test"] = _slice(res, a.split, a.end)
                out.append(rec)
                print(fmt(res), f"| cost={cost} minSL={msl} block={list(hrs)}", flush=True)
                if a.split:
                    print(f"    train {rec['train']}\n    test  {rec['test']}", flush=True)
                if a.save_trades and len(res["trades"]):
                    res["trades"].to_csv(
                        OUT / f"{a.strategy}_{a.tag}_c{cost}_s{msl}_h{'-'.join(map(str,hrs)) or 'none'}.csv",
                        index=False)

    p = OUT / f"{a.strategy}_{a.tag}.json"
    p.write_text(json.dumps(out, indent=2, default=str))
    print("\nwrote", p)


if __name__ == "__main__":
    main()
