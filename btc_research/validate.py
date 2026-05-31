"""
btc_research/validate.py
------------------------
Honest validation gauntlet for a chosen BTC strategy (operates on pnl_pts so
TIME exits are counted, unlike protocol.py which only keeps TP/SL):

  1. OOS time-split (anchored 60/40) with a FIXED config — IS vs OOS, WFE.
  2. K-fold consistency (5 sequential calendar folds) — % folds positive.
  3. Monte Carlo: bootstrap PnL distribution + shuffled-order max-DD distribution.
  4. Cost stress ($30 / $60 / $90 round-trip).

Verdict thresholds mirror the project's protocol.py.
"""
from __future__ import annotations

import json
import os

import numpy as np

from btc_research.data import get_candles
from btc_research import engine, strategies as S

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "validation.json")

THRESH = {"min_trades": 100, "min_wr": 50.0, "min_pf": 1.3,
          "max_dd_ratio": 0.25, "min_exp_R": 0.15, "min_wfe": 0.5, "min_consist": 60.0}


def _seg(trades, lo, hi):
    ts = sorted(trades, key=lambda t: t.entry_time)
    n = len(ts)
    return ts[int(n * lo):int(n * hi)]


def oos_split(trades):
    is_t, oos_t = _seg(trades, 0, 0.6), _seg(trades, 0.6, 1.0)
    si, so = engine.summarize(is_t), engine.summarize(oos_t)
    wfe = (so.get("exp_R", 0) / si["exp_R"]) if si.get("exp_R", 0) > 0 else 0.0
    return {"is": si, "oos": so, "wfe": round(wfe, 2)}


def kfold(trades, k=5):
    ts = sorted(trades, key=lambda t: t.entry_time)
    n = len(ts)
    folds = []
    for f in range(k):
        seg = ts[int(n * f / k):int(n * (f + 1) / k)]
        s = engine.summarize(seg)
        folds.append({"fold": f + 1, "n": s.get("n", 0), "pnl": s.get("pnl", 0), "pf": s.get("pf", 0)})
    pos = sum(1 for f in folds if f["pnl"] > 0)
    return {"folds": folds, "consistency_pct": round(pos / k * 100, 1)}


def monte_carlo(trades, n_iter=3000):
    pnls = np.array([t.pnl_pts for t in trades if t.outcome != "OPEN"], float)
    if len(pnls) < 10:
        return {}
    rng = np.random.default_rng(42)
    totals = np.empty(n_iter)
    dds = np.empty(n_iter)
    for k in range(n_iter):
        # bootstrap PnL total
        sample = rng.choice(pnls, size=len(pnls), replace=True)
        totals[k] = sample.sum()
        # shuffled-order max drawdown
        perm = rng.permutation(pnls)
        eq = np.cumsum(perm)
        dds[k] = (np.maximum.accumulate(eq) - eq).max()
    return {
        "pnl_p05": round(float(np.percentile(totals, 5)), 1),
        "pnl_p50": round(float(np.percentile(totals, 50)), 1),
        "pnl_p95": round(float(np.percentile(totals, 95)), 1),
        "p_negative": round(float((totals <= 0).mean()), 3),
        "dd_p95_worst": round(float(np.percentile(dds, 95)), 1),
    }


def verdict(full, oos, kf):
    pnl = full.get("pnl", 0)
    dd = full.get("max_dd", 0)
    dd_ratio = (dd / pnl) if pnl > 0 else float("inf")
    checks = {
        "trades>=100":      full.get("n", 0) >= THRESH["min_trades"],
        "wr>=50":           full.get("wr", 0) >= THRESH["min_wr"],
        "pf>=1.3":          full.get("pf", 0) >= THRESH["min_pf"],
        "dd<=25%pnl":       dd_ratio <= THRESH["max_dd_ratio"],
        "expR>=0.15":       full.get("exp_R", 0) >= THRESH["min_exp_R"],
        "wfe>=0.5":         oos.get("wfe", 0) >= THRESH["min_wfe"],
        "consistency>=60":  kf.get("consistency_pct", 0) >= THRESH["min_consist"],
    }
    passed = sum(checks.values())
    v = "PASS" if passed == 7 else ("REFINE" if passed >= 5 else "ABANDON")
    return {"checks": checks, "passed": passed, "verdict": v, "dd_ratio": round(dd_ratio, 2)}


def validate(name, gen_fn, tf="4h", base_cost=30.0):
    df = get_candles(tf).reset_index(drop=True)
    entries = gen_fn(df)
    trades = engine.run(df, entries, name, cost_pts=base_cost)
    full = engine.summarize(trades)

    cost_stress = {}
    for c in (30.0, 60.0, 90.0):
        cost_stress[c] = engine.summarize(engine.run(df, entries, name, cost_pts=c))

    oos = oos_split(trades)
    kf = kfold(trades)
    mc = monte_carlo(trades)
    vd = verdict(full, oos, kf)

    print("=" * 72)
    print(f"VALIDATION — {name}  ({tf}, cost ${base_cost:.0f})")
    print("=" * 72)
    print(f"  full: n={full.get('n')} wr={full.get('wr')}% pnl={full.get('pnl')} "
          f"pf={full.get('pf')} expR={full.get('exp_R')} maxDD={full.get('max_dd')} "
          f"dd/pnl={vd['dd_ratio']}")
    print(f"  OOS split (60/40): IS pf={oos['is'].get('pf')} expR={oos['is'].get('exp_R')} | "
          f"OOS pf={oos['oos'].get('pf')} expR={oos['oos'].get('exp_R')} | WFE={oos['wfe']}")
    print(f"  k-fold consistency: {kf['consistency_pct']}%  " +
          " ".join(f"f{f['fold']}:{f['pnl']:+.0f}({f['n']})" for f in kf["folds"]))
    print(f"  Monte Carlo: pnl p05={mc.get('pnl_p05')} p50={mc.get('pnl_p50')} "
          f"p95={mc.get('pnl_p95')}  P(loss)={mc.get('p_negative')}  ddP95={mc.get('dd_p95_worst')}")
    print(f"  cost stress: " + "  ".join(
        f"${int(c)}:pf={s.get('pf')}/pnl={s.get('pnl')}" for c, s in cost_stress.items()))
    print(f"  CHECKS {vd['passed']}/7: " + " ".join(
        ("[Y]" if v else "[n]") + k for k, v in vd["checks"].items()))
    print(f"  VERDICT: {vd['verdict']}")
    return {"name": name, "tf": tf, "full": full, "oos": oos, "kfold": kf,
            "monte_carlo": mc, "cost_stress": {str(k): v for k, v in cost_stress.items()},
            "verdict": vd}


CANDIDATES = {
    # primary: standalone z-score swing reversion (the data-supported edge)
    "ZREV_z2.0": lambda df: S.zscore_revert_swing(df, N=30, z_thr=2.0, sl_atr=5.0, max_hold=6),
    "ZREV_z1.5": lambda df: S.zscore_revert_swing(df, N=30, z_thr=1.5, sl_atr=5.0, max_hold=6),
    # documented negatives (kept for honesty in the writeup)
    "SWEEPREV":  lambda df: S.sweep_reversal(df, L=20, sl_buf=5.0, max_hold=6),
    "COMBINED":  lambda df: S.btc_swing_mr(df, z_thr=2.0, sl_atr=5.0, max_hold=6),
}

if __name__ == "__main__":
    results = []
    for name, gen in CANDIDATES.items():
        results.append(validate(name, gen))
        print()
    with open(OUT, "w") as fh:
        json.dump(results, fh, indent=2, default=str)
    print(f"[validate] wrote {OUT}")
