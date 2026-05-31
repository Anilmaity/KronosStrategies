"""
btc_research/forward_test.py
----------------------------
Push the swing z-reversion toward deployable WITHOUT data-snooping:

  1. CALIBRATE the weekly-trend gate ONLY on a design window (default < 2026-01-01).
  2. LOCK it, evaluate ONCE on the held-out forward window (>= 2026-01-01),
     ungated vs gated, with Monte-Carlo on the forward trades.
  3. ROLLING walk-forward (anchored): re-fit the gate each fold on in-sample,
     score the next out-of-sample fold -> honest WFE / consistency.

The base strategy is fixed (z_thr=1.5, N=30, sl_atr=5.0, hold=6 — the best honest
config). Only the GATE is calibrated, and only on data the forward window never sees.

  python -m btc_research.forward_test                 # run on cached data
  python -m btc_research.forward_test --refresh        # re-pull latest ticks first
  python -m btc_research.forward_test --forward-start 2026-03-01
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime

import numpy as np

from btc_research.data import get_candles, build_cache
from btc_research import engine, strategies as S
from btc_research.validate import monte_carlo

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "forward_test.json")

BASE = dict(N=30, z_thr=1.5, atr_n=14, sl_atr=5.0, max_hold=6)

# Gate grid (form fixed a priori; only thresholds vary). 'ungated' is the control.
def _gate_grid():
    grid = [("ungated", dict())]
    for mode in ("align", "range_only"):
        for wk in (4, 6, 8):
            for st in (0.5, 1.0, 1.5):
                grid.append((f"{mode}_w{wk}_s{st}",
                             dict(htf_gate=mode, htf_ema_weeks=wk, htf_strength=st)))
    return grid


def _trades_for(df, gate_kwargs):
    entries = S.zscore_revert_swing(df, **BASE, **gate_kwargs)
    return engine.run(df, entries, "ZREV_gated", cost_pts=30.0)


def _subset(trades, lo=None, hi=None):
    out = []
    for t in trades:
        et = t.entry_time
        if lo and et < lo:
            continue
        if hi and et >= hi:
            continue
        out.append(t)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-pull latest ticks from DB first")
    ap.add_argument("--forward-start", default="2026-01-01")
    args = ap.parse_args()

    if args.refresh:
        build_cache("BTC_USD")

    df = get_candles("4h").reset_index(drop=True)
    split = datetime.fromisoformat(args.forward_start)
    print(f"data {df['time'].iloc[0]} -> {df['time'].iloc[-1]}   forward split @ {split.date()}\n")

    grid = _gate_grid()
    # precompute full-history trades for each config once (indicators stay causal)
    cfg_trades = {name: _trades_for(df, kw) for name, kw in grid}

    # ── 1+2. calibrate on DESIGN, evaluate ONCE on held-out FORWARD ──────────
    design = {name: engine.summarize(_subset(ts, hi=split)) for name, ts in cfg_trades.items()}
    # pick best gated config by DESIGN expectancy, with a trade floor
    gated = [(n, s) for n, s in design.items()
             if n != "ungated" and s.get("n", 0) >= 25]
    gated.sort(key=lambda kv: kv[1].get("exp_R", -9), reverse=True)
    best_name = gated[0][0] if gated else "ungated"
    best_kw = dict(grid)[best_name]

    print("CALIBRATION on design window (top 5 by expR):")
    for n, s in ([("ungated", design["ungated"])] +
                 sorted([g for g in gated], key=lambda kv: kv[1].get("exp_R", -9), reverse=True)[:5]):
        print(f"  {n:16s} n={s.get('n',0):>4} pf={s.get('pf',0):>5} expR={s.get('exp_R',0):>6} pnl={s.get('pnl',0):>9}")
    print(f"\n  LOCKED gate: {best_name}  {best_kw}\n")

    def report(label, ts):
        s = engine.summarize(ts)
        mc = monte_carlo(ts)
        print(f"  {label:22s} n={s.get('n',0):>4} wr={s.get('wr',0):>5}% pf={s.get('pf',0):>5} "
              f"expR={s.get('exp_R',0):>6} pnl={s.get('pnl',0):>9} maxDD={s.get('max_dd',0):>8} "
              f"MC P(loss)={mc.get('p_negative')}")
        return {"summary": s, "mc": mc}

    print("HELD-OUT FORWARD window (never seen during calibration):")
    fwd_ungated = report("ungated (control)", _subset(cfg_trades["ungated"], lo=split))
    fwd_gated = report(f"gated [{best_name}]", _subset(cfg_trades[best_name], lo=split))

    # ── 3. rolling anchored walk-forward: re-fit gate each fold ─────────────
    print("\nROLLING WALK-FORWARD (anchored; gate re-fit each fold on in-sample):")
    # split timeline into 5 sequential calendar segments
    t0 = df["time"].iloc[0].to_pydatetime()
    t1 = df["time"].iloc[-1].to_pydatetime()
    K = 5
    bounds = [t0 + (t1 - t0) * k / K for k in range(K + 1)]
    wf = []
    for k in range(1, K):
        is_hi = bounds[k]
        oos_lo, oos_hi = bounds[k], bounds[k + 1]
        # choose best gate on IS (anchored: everything before is_hi)
        cand = [(n, engine.summarize(_subset(ts, hi=is_hi))) for n, ts in cfg_trades.items()]
        cand = [(n, s) for n, s in cand if n != "ungated" and s.get("n", 0) >= 20]
        if cand:
            cand.sort(key=lambda kv: kv[1].get("exp_R", -9), reverse=True)
            pick = cand[0][0]
        else:
            pick = "ungated"
        is_s = engine.summarize(_subset(cfg_trades[pick], hi=is_hi))
        oos_s = engine.summarize(_subset(cfg_trades[pick], lo=oos_lo, hi=oos_hi))
        wf.append({"fold": k, "pick": pick,
                   "is_expR": is_s.get("exp_R", 0), "oos_expR": oos_s.get("exp_R", 0),
                   "oos_pnl": oos_s.get("pnl", 0), "oos_n": oos_s.get("n", 0),
                   "oos_pf": oos_s.get("pf", 0)})
        print(f"  fold{k}: pick={pick:16s} IS expR={is_s.get('exp_R',0):>6} | "
              f"OOS n={oos_s.get('n',0):>3} pf={oos_s.get('pf',0):>5} expR={oos_s.get('exp_R',0):>6} "
              f"pnl={oos_s.get('pnl',0):>8}")
    is_mean = np.mean([w["is_expR"] for w in wf]) if wf else 0
    oos_mean = np.mean([w["oos_expR"] for w in wf]) if wf else 0
    wfe = round(oos_mean / is_mean, 2) if is_mean > 0 else 0
    consist = round(sum(1 for w in wf if w["oos_pnl"] > 0) / len(wf) * 100, 1) if wf else 0
    print(f"\n  walk-forward: mean IS expR={is_mean:.3f}  mean OOS expR={oos_mean:.3f}  "
          f"WFE={wfe}  OOS-consistency={consist}%")

    result = {
        "forward_start": args.forward_start, "locked_gate": best_name, "gate_kwargs": best_kw,
        "design_top": dict(design),
        "forward_ungated": fwd_ungated, "forward_gated": fwd_gated,
        "walk_forward": wf, "wf_wfe": wfe, "wf_consistency": consist,
    }
    with open(OUT, "w") as fh:
        json.dump(result, fh, indent=2, default=str)
    print(f"\n[forward_test] wrote {OUT}")

    # verdict
    fg, fu = fwd_gated["summary"], fwd_ungated["summary"]
    improved = fg.get("exp_R", 0) > fu.get("exp_R", 0) and fg.get("pf", 0) > 1.0
    print("\nVERDICT:")
    print(f"  gate {'IMPROVED' if improved else 'did NOT improve'} held-out forward expectancy "
          f"(ungated expR {fu.get('exp_R')} -> gated {fg.get('exp_R')}, pf {fu.get('pf')} -> {fg.get('pf')}).")
    if improved and wfe >= 0.5 and consist >= 60:
        print("  -> DEPLOYABLE-CANDIDATE: forward-positive AND walk-forward robust. Paper-trade next.")
    elif improved:
        print("  -> PROMISING but walk-forward not yet robust. Needs a genuine forward window (more data).")
    else:
        print("  -> STILL REFINE: weekly gate did not rescue the held-out window. Edge remains regime-fragile.")


if __name__ == "__main__":
    main()
