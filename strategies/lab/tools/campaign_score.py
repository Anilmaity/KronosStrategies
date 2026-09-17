"""lab/tools/campaign_score.py -- score sweep results against the pre-registered bars.

Reads a campaign directory produced by lab.sweep (one <arm>.json + <arm>.trades.parquet
per arm) and computes, per arm, the Stage-1 bars of PROTOCOL_xau2y_2026-09-18.md:

  1  TEST n >= n_min
  2  TEST PF > 1 at base cost AND at stress cost   (needs the paired arm; see --pair)
  3  TRAIN PF > 0.9 at base cost
  4  >= 55% TEST months positive and no TEST month > 50% of TEST net points
  5  positive in both gold-up and gold-down months, OR |corr(monthly pts, gold %)| < 0.4

The gold monthly series comes from the cache's daily frame (close-to-close by calendar
month). Output: one row per arm with each bar's value and PASS/FAIL, plus the joint
verdict. Numbers in reports must be quoted from this tool, not typed.

    python -m lab.tools.campaign_score lab/results/xau2y_stage1 --split 2025-12-01 \
        --cache backtest/results/bars_cache_2y [--pair-suffixes _c0.45 _c0.80] [--csv out.csv]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
_STRAT = _HERE.parent.parent
if str(_STRAT) not in sys.path:
    sys.path.insert(0, str(_STRAT))

from lab.harness import CACHE, TF_FILES   # noqa: E402


def gold_monthly(cache: Path) -> pd.Series:
    d = pd.read_parquet(cache / TF_FILES["1d"])
    t = pd.to_datetime(d["time"], utc=True).dt.tz_convert(None)
    d = d.assign(time=t).sort_values("time").set_index("time")
    m = d["close"].resample("ME").agg(["first", "last"])
    g = (m["last"] / m["first"] - 1.0) * 100.0
    g.index = g.index.strftime("%Y-%m")
    return g.rename("gold_pct")


def monthly_pts(trades: pd.DataFrame) -> pd.Series:
    if not len(trades):
        return pd.Series(dtype=float)
    m = pd.to_datetime(trades["entry_time"], utc=True).dt.strftime("%Y-%m")
    return trades.groupby(m)["pts"].sum()


def _pf(df: pd.DataFrame) -> float:
    if not len(df):
        return 0.0
    gw = df.pts[df.pts > 0].sum()
    gl = -df.pts[df.pts <= 0].sum()
    return float(gw / gl) if gl > 0 else float("inf")


def score_arm(arm_json: Path, gold: pd.Series, split: str, n_min: int = 40) -> dict:
    row = json.loads(arm_json.read_text(encoding="utf-8"))
    out = dict(label=row["label"], strategy=row.get("strategy"), status=row.get("status"),
               cost=row.get("cost"), n=row.get("n"), pf=row.get("pf"), pts=row.get("pts"))
    if row.get("status") != "ok":
        out["verdict"] = "ERROR"
        return out
    trades = pd.read_parquet(arm_json.with_name(arm_json.name[:-5] + ".trades.parquet"))
    cut = pd.Timestamp(split, tz="UTC")
    et = pd.to_datetime(trades["entry_time"], utc=True) if len(trades) else None
    train = trades[et < cut] if len(trades) else trades
    test = trades[et >= cut] if len(trades) else trades

    out.update(train_n=int(len(train)), train_pf=round(_pf(train), 3), train_pts=round(float(train.pts.sum()), 1) if len(train) else 0.0,
               test_n=int(len(test)), test_pf=round(_pf(test), 3), test_pts=round(float(test.pts.sum()), 1) if len(test) else 0.0)

    # bar 4: monthly consistency on TEST
    tm = monthly_pts(test)
    if len(tm):
        pos_share = float((tm > 0).mean())
        net = float(tm.sum())
        max_share = float(tm.max() / net) if net > 0 else float("inf")
    else:
        pos_share, max_share = 0.0, float("inf")
    out.update(test_months=int(len(tm)), test_pos_month_share=round(pos_share, 2),
               test_max_month_share=round(max_share, 2) if np.isfinite(max_share) else None)

    # bar 5: regime independence on the FULL window
    fm = monthly_pts(trades)
    j = pd.concat([fm.rename("pts"), gold], axis=1).dropna()
    if len(j) >= 6:
        up, dn = j[j.gold_pct > 0], j[j.gold_pct <= 0]
        corr = float(np.corrcoef(j.pts, j.gold_pct)[0, 1]) if j.pts.std() > 0 else 0.0
        out.update(gold_corr=round(corr, 2), up_months_pts=round(float(up.pts.sum()), 1),
                   dn_months_pts=round(float(dn.pts.sum()), 1), n_up=int(len(up)), n_dn=int(len(dn)))
        bar5 = (up.pts.sum() > 0 and dn.pts.sum() > 0) or abs(corr) < 0.4
    else:
        out.update(gold_corr=None, up_months_pts=None, dn_months_pts=None)
        bar5 = False

    out["bar1_n"] = out["test_n"] >= n_min
    out["bar2_base"] = out["test_pf"] > 1.0
    out["bar3_train"] = out["train_pf"] > 0.9
    out["bar4_monthly"] = pos_share >= 0.55 and max_share <= 0.5
    out["bar5_regime"] = bool(bar5)
    return out


def score_campaign(results_dir: Path, cache: Path, split: str, base_suffix: str = "_c0.45",
                   stress_suffix: str = "_c0.80", n_min: int = 40) -> pd.DataFrame:
    gold = gold_monthly(cache)
    rows = [score_arm(p, gold, split, n_min) for p in sorted(results_dir.glob("*.json"))
            if not p.name.endswith(".error.json")]
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # bar 2 needs the stress twin: pair base arms with their _c0.80 sibling by label
    stress_pf = {r["label"]: r.get("test_pf") for r in rows}
    def _stress(label):
        if label.endswith(base_suffix):
            return stress_pf.get(label[: -len(base_suffix)] + stress_suffix)
        return None
    df["test_pf_stress"] = df.label.map(_stress)
    df["bar2_stress"] = df.test_pf_stress.map(lambda v: bool(v is not None and v > 1.0))
    bars = ["bar1_n", "bar2_base", "bar2_stress", "bar3_train", "bar4_monthly", "bar5_regime"]
    for b in bars:
        if b not in df:
            df[b] = False
    df["bars_passed"] = df[bars].fillna(False).sum(axis=1)
    df["verdict"] = np.where(df.status != "ok", "ERROR",
                     np.where(df[bars].fillna(False).all(axis=1), "PASS",
                     np.where(df.bars_passed >= len(bars) - 1, "NEAR", "FAIL")))
    # only base-cost arms carry a verdict; stress arms are inputs
    df.loc[~df.label.str.endswith(base_suffix) & (df.status == "ok"), "verdict"] = "(stress arm)"
    return df


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("results_dir")
    ap.add_argument("--split", required=True)
    ap.add_argument("--cache", default=None, help="bars cache dir (default: harness CACHE)")
    ap.add_argument("--n-min", type=int, default=40)
    ap.add_argument("--csv", default=None)
    a = ap.parse_args(argv)
    cache = Path(a.cache) if a.cache else CACHE
    df = score_campaign(Path(a.results_dir), cache, a.split, n_min=a.n_min)
    if df.empty:
        print("no results"); return 1
    cols = ["label", "verdict", "bars_passed", "test_n", "test_pf", "test_pf_stress", "test_pts",
            "train_n", "train_pf", "test_pos_month_share", "test_max_month_share",
            "gold_corr", "up_months_pts", "dn_months_pts"]
    cols = [c for c in cols if c in df]
    with pd.option_context("display.width", 250, "display.max_rows", 500, "display.max_columns", 40):
        print(df.sort_values(["verdict", "test_pf"], ascending=[True, False])[cols].to_string(index=False))
    if a.csv:
        df.to_csv(a.csv, index=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
