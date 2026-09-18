"""01_harness_replay.py — the real modules through lab.harness over the live window, with the
live configuration of PROTOCOL.md D6, then every trade re-resolved on the S5 quote.

    cd strategies && LAB_BARS_CACHE=backtest/results/bars_cache_2y \
        ../.venv/bin/python lab/research_s5/r2_live_parity/01_harness_replay.py

Outputs results/sim_<arm>.parquet with columns from harness rows plus
  pts0        harness mid-M1 points at cost 0  (pts at cost c = pts0 - c)
  q_outcome, q_pts0   quote-S5 outcome and points at cost 0 (lab.s5exit.resolve, start_offset 60)
and results/01_harness_replay.txt with the per-arm summary at 0.75 / 1.00 (mid) and 0.45 / 0.70 (quote).
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
STRAT = HERE.parents[2]
sys.path.insert(0, str(STRAT))
os.environ.setdefault("LAB_BARS_CACHE", str(STRAT / "backtest" / "results" / "bars_cache_2y"))

from lab.harness import Cfg, load_bars, replay          # noqa: E402
from lab.s5exit import load_s5, resolve                # noqa: E402
import importlib                                       # noqa: E402

OUT = HERE / "results"
START, END = "2026-07-01", "2026-09-18"     # END exclusive; M1 cache ends 2026-09-17
S93_SWITCH = "2026-09-02"                   # _HOURS (7,8,9,12,13,14) -> (13,14) shipped 09-01 22:52 UTC

# label -> (module, start, end, Cfg)
ARMS = {
    # ---- the live roster as configured in the window (D6) ----
    "s93_pre":     ("s93_fvg_scalp", START, S93_SWITCH, Cfg(cost_pts=0.0, patch={"_HOURS": (7, 8, 9, 12, 13, 14)})),
    "s93_post":    ("s93_fvg_scalp", S93_SWITCH, END, Cfg(cost_pts=0.0, patch={"_HOURS": (13, 14)})),
    "s93_pre_noveto": ("s93_fvg_scalp", START, S93_SWITCH,
                       Cfg(cost_pts=0.0, patch={"_HOURS": (7, 8, 9, 12, 13, 14)}, env={"S93_SOFT_VETO": "off"})),
    "s94":         ("s94_sweep_reversal", START, END, Cfg(cost_pts=0.0, env={"S94_SIDES": "BUY,SELL", "S94_SD_MULT": "2.0"})),
    "s99":         ("s99_mss_fvg", START, END, Cfg(cost_pts=0.0)),
    "s100":        ("s100_m3_combo", START, END, Cfg(cost_pts=0.0, env={"S100_ER_GATE": "off"})),
    # ---- the 09-18 roster, prospective (D10 / D12) ----
    "s94_long25":  ("s94_sweep_reversal", START, END, Cfg(cost_pts=0.0, env={"S94_SIDES": "BUY", "S94_SD_MULT": "2.5"})),
    "s93_cur":     ("s93_fvg_scalp", START, END, Cfg(cost_pts=0.0, patch={"_HOURS": (13, 14)})),
    "c03":         ("c03_fvg_fill", START, END, Cfg(cost_pts=0.0)),
    "s14_30":      ("s14_ob_mit_bias", START, END, Cfg(cost_pts=0.0, min_sl_dist_pts=3.0)),
}


def _pf(v: pd.Series) -> float:
    gw, gl = v[v > 0].sum(), -v[v <= 0].sum()
    return float(gw / gl) if gl > 0 else float("inf")


def _max_hold(module: str) -> float:
    pkg = "concept_strategies" if module.startswith("c") else "backtest_strategies"
    mod = importlib.import_module(f"{pkg}.{module}")
    mh = getattr(mod, "_MAX_HOLD_MIN", None)
    return float(mh) if mh else 5 * 24 * 60.0      # no time exit -> long horizon


def main() -> None:
    only = set(sys.argv[1:])
    bars = load_bars(tfs=("1m", "5m", "15m"))
    s5 = load_s5()
    t5 = s5["time"].dt.tz_convert(None).to_numpy("datetime64[ns]")
    lines = []
    for label, (module, start, end, cfg) in ARMS.items():
        if only and label not in only:
            continue
        t0 = time.time()
        res = replay(module, bars, start=start, end=end, cfg=cfg)
        df = res["trades"].copy()
        if len(df) == 0:
            lines.append(f"{label:<16} n=0")
            print(lines[-1]); continue
        df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True)
        df["exit_time"] = pd.to_datetime(df["exit_time"], utc=True)
        df = df.rename(columns={"pts": "pts0"})
        mh = _max_hold(module)
        qo, qp = [], []
        for t in df.itertuples():
            r = resolve(t, s5, t5, "quote_s5", 0.0, mh, start_offset_s=60)
            if r is None:
                qo.append(None); qp.append(np.nan); continue
            oc, px = r
            raw = (px - t.entry_px) if t.side == "BUY" else (t.entry_px - px)
            qo.append(oc); qp.append(raw)
        df["q_outcome"], df["q_pts0"] = qo, qp
        df["arm"] = label
        df["module"] = module
        df.to_parquet(OUT / f"sim_{label}.parquet", index=False)
        n = len(df)
        m75, m100 = df.pts0 - 0.75, df.pts0 - 1.00
        q = df.q_pts0.dropna()
        q45, q70 = q - 0.45, q - 0.70
        lines.append(
            f"{label:<16} n={n:<4} mid@0.75 PF {_pf(m75):5.2f} pts {m75.sum():8.1f} | mid@1.00 PF {_pf(m100):5.2f} pts {m100.sum():8.1f}"
            f" | quote@0.45 PF {_pf(q45):5.2f} pts {q45.sum():8.1f} | quote@0.70 PF {_pf(q70):5.2f} pts {q70.sum():8.1f}"
            f" | WR(q45) {100*(q45>0).mean():4.1f}%  med stop {df.risk.median():.2f}  ({time.time()-t0:.0f}s)")
        print(lines[-1], flush=True)
    with open(OUT / "01_harness_replay.txt", "a") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
