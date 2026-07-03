"""
backtest_s98_walkforward.py
---------------------------
Standalone M15-native validation of S98 z-score MR on the bars cache.

Fill/exit conventions (mirrors manager_sim market fills at 0.02 lots):
  entry : signal-bar close +/- FRICTION_PTS (adverse).
  TP    : tp level +/- FRICTION_PTS (adverse).  SL: sl level +/- FRICTION_PTS.
  TIME  : bar close +/- FRICTION_PTS after 16 M15 bars (240 min).
  Both SL and TP inside one bar -> SL books first (conservative).
  One position at a time; entries only 03:00-09:00 UTC; exits any time.

Modes:
  --train-sweep : 5x5x5 grid (lookback, entry_z, stop_z each +/-30% in 5
                  steps) on TRAIN bars ONLY (time < 2026-01-01). Writes
                  results/s98/train_grid.{json,md} with a plateau verdict.
  --oos-once    : ONE run of the spec parameters on OOS bars (>= 2026-01-01).
                  THE OOS GATE -- run exactly once, by the Task-5 controller.

Run from repo root:
  .venv/Scripts/python.exe -m backtest.backtest_s98_walkforward --train-sweep
(cwd strategies/ also works via the sys.path shim.)
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backtest_strategies.s98_zscore_mr_m15 import (   # noqa: E402
    _ADF_P, _ENTRY_Z, _LOOKBACK, _MAX_HOLD_MIN, _SESSION_END_H,
    _SESSION_START_H, _STOP_Z,
)

CACHE = Path(__file__).resolve().parent / "results" / "bars_cache" / "is_XAU_USD_15m.parquet"
OUT_DIR = Path(__file__).resolve().parent / "results" / "s98"

FRICTION_PTS = 0.25            # per side (spread 0.30/2 + slippage 0.10)
LOTS = 0.02
TRAIN_END = pd.Timestamp("2026-01-01 00:00:00+00:00")
HOLD_BARS = _MAX_HOLD_MIN // 15


def slice_train(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["time"] < TRAIN_END].reset_index(drop=True)


def slice_oos(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["time"] >= TRAIN_END].reset_index(drop=True)


def _pts_to_usd(pts: float) -> float:
    return pts * LOTS * 100.0


def simulate(df: pd.DataFrame, lookback: int, entry_z: float,
             stop_z: float) -> dict:
    """Replay S98 rules bar-by-bar on closed M15 bars (vectorised z)."""
    c = df["close"].astype(float)
    hi = df["high"].astype(float).to_numpy()
    lo = df["low"].astype(float).to_numpy()
    cl = c.to_numpy()
    t = pd.to_datetime(df["time"], utc=True)
    hours = t.dt.hour.to_numpy()

    sma = c.rolling(lookback).mean()
    sd = c.rolling(lookback).std(ddof=1)
    z = ((c - sma) / sd).to_numpy()
    sma_v, sd_v = sma.to_numpy(), sd.to_numpy()
    resid = (c - sma).to_numpy()

    trades: list[float] = []          # pnl in pts
    equity: list[float] = []          # cumulative USD after each trade
    i = lookback + 1
    n = len(df)
    while i < n:
        z_now, z_prev = z[i], z[i - 1]
        if (np.isnan(z_now) or np.isnan(z_prev) or sd_v[i] <= 0
                or not (_SESSION_START_H <= hours[i] < _SESSION_END_H)
                or abs(z_prev) >= entry_z or abs(z_now) < entry_z):
            i += 1
            continue
        # ADF gate, evaluated only on crossing candidates (cheap).
        window = resid[i - lookback + 1: i + 1]
        if np.isnan(window).any():
            i += 1
            continue
        try:
            p = adfuller(window, autolag="AIC")[1]
        except Exception:
            i += 1
            continue
        if not (p < _ADF_P):
            i += 1
            continue

        side = "SELL" if z_now >= entry_z else "BUY"
        mean = round(float(sma_v[i]), 2)
        if side == "SELL":
            fill = cl[i] - FRICTION_PTS
            sl = round(float(sma_v[i] + stop_z * sd_v[i]), 2)
            tp = mean
        else:
            fill = cl[i] + FRICTION_PTS
            sl = round(float(sma_v[i] - stop_z * sd_v[i]), 2)
            tp = mean

        pnl = None
        exit_j = min(i + HOLD_BARS, n - 1)
        for j in range(i + 1, exit_j + 1):
            if side == "SELL":
                if hi[j] >= sl:                       # SL first (conservative)
                    pnl = fill - (sl + FRICTION_PTS)
                    break
                if lo[j] <= tp:
                    pnl = fill - (tp + FRICTION_PTS)
                    break
            else:
                if lo[j] <= sl:
                    pnl = (sl - FRICTION_PTS) - fill
                    break
                if hi[j] >= tp:
                    pnl = (tp - FRICTION_PTS) - fill
                    break
        else:
            j = exit_j
        if pnl is None:                               # TIME exit at bar close
            pnl = (fill - (cl[j] + FRICTION_PTS) if side == "SELL"
                   else (cl[j] - FRICTION_PTS) - fill)
        trades.append(pnl)
        equity.append((equity[-1] if equity else 0.0) + _pts_to_usd(pnl))
        i = j + 1                                     # one position at a time

    wins = sum(1 for p in trades if p > 0)
    gw = sum(p for p in trades if p > 0)
    gl = -sum(p for p in trades if p < 0)
    peak, max_dd = 0.0, 0.0
    for e in equity:
        peak = max(peak, e)
        max_dd = max(max_dd, peak - e)
    return {
        "trades": len(trades),
        "wins": wins,
        "wr": (100.0 * wins / len(trades)) if trades else 0.0,
        "net_pts": round(sum(trades), 4),
        "net_usd": round(_pts_to_usd(sum(trades)), 2),
        "pf": round(gw / gl, 4) if gl > 0 else None,
        "max_dd_usd": round(max_dd, 2),
    }


def _axis(center: float, is_int: bool = False) -> list:
    vals = [center * f for f in (0.70, 0.85, 1.00, 1.15, 1.30)]
    return [int(round(v)) for v in vals] if is_int else [round(v, 2) for v in vals]


def train_sweep(df_train: pd.DataFrame) -> dict:
    grid = {}
    looks = _axis(_LOOKBACK, is_int=True)
    entries = _axis(_ENTRY_Z)
    stops = _axis(_STOP_Z)
    total = len(looks) * len(entries) * len(stops)
    for k, (lb, ez, sz) in enumerate(
            itertools.product(looks, entries, stops), 1):
        r = simulate(df_train, lb, ez, sz)
        grid[f"{lb}/{ez}/{sz}"] = r
        print(f"  [{k}/{total}] lb={lb} ez={ez} sz={sz} -> "
              f"{r['trades']} trades net=${r['net_usd']:+.2f} pf={r['pf']}")
    # Plateau check: spec point + its 6 axis neighbours all PF > 1.0.
    def _pf(lb, ez, sz):
        r = grid.get(f"{lb}/{ez}/{sz}")
        return r["pf"] if r and r["pf"] is not None else 0.0
    center = (_LOOKBACK, _ENTRY_Z, _STOP_Z)
    neighbours = []
    for axis_vals, idx in ((looks, 0), (entries, 1), (stops, 2)):
        ci = axis_vals.index(center[idx] if idx else int(center[idx]))
        for d in (-1, 1):
            if 0 <= ci + d < len(axis_vals):
                p = list(center)
                p[idx] = axis_vals[ci + d]
                neighbours.append(tuple(p))
    plateau_pfs = [_pf(*center)] + [_pf(*nb) for nb in neighbours]
    plateau_ok = all(p > 1.0 for p in plateau_pfs)
    return {"grid": grid, "plateau_ok": plateau_ok,
            "plateau_pfs": plateau_pfs,
            "center": f"{_LOOKBACK}/{_ENTRY_Z}/{_STOP_Z}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train-sweep", action="store_true")
    ap.add_argument("--oos-once", action="store_true")
    args = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.read_parquet(CACHE)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.sort_values("time").reset_index(drop=True)

    if args.train_sweep:
        tr = slice_train(df)
        print(f"TRAIN bars: {len(tr)} ({tr['time'].min()} .. {tr['time'].max()})")
        out = train_sweep(tr)
        (OUT_DIR / "train_grid.json").write_text(
            json.dumps(out, indent=2), encoding="utf-8")
        lines = ["# S98 train sweep (TRAIN < 2026-01-01)", "",
                 f"Plateau at {out['center']} +/-1 axis step, all PF > 1.0: "
                 f"**{'PASS' if out['plateau_ok'] else 'FAIL'}** "
                 f"(PFs: {out['plateau_pfs']})", "",
                 "| lb/ez/sz | Trades | WR% | PF | Net USD | Max DD $ |",
                 "|---|---:|---:|---:|---:|---:|"]
        for kkey, r in out["grid"].items():
            lines.append(f"| {kkey} | {r['trades']} | {r['wr']:.1f} | "
                         f"{r['pf']} | {r['net_usd']:+.2f} | {r['max_dd_usd']:.2f} |")
        (OUT_DIR / "train_grid.md").write_text("\n".join(lines) + "\n",
                                               encoding="utf-8")
        print(f"Plateau: {'PASS' if out['plateau_ok'] else 'FAIL'}")
        return 0

    if args.oos_once:
        oo = slice_oos(df)
        print(f"OOS bars: {len(oo)} ({oo['time'].min()} .. {oo['time'].max()})")
        r = simulate(oo, _LOOKBACK, _ENTRY_Z, _STOP_Z)
        r["gate"] = {"pf_ok": bool(r["pf"] and r["pf"] >= 1.15),
                     "trades_ok": r["trades"] >= 60}
        r["verdict"] = "PASS" if (r["gate"]["pf_ok"] and r["gate"]["trades_ok"]) else "FAIL"
        (OUT_DIR / "oos_result.json").write_text(
            json.dumps(r, indent=2), encoding="utf-8")
        print(json.dumps(r, indent=2))
        return 0

    ap.error("choose --train-sweep or --oos-once")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
