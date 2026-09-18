"""Shared loaders for the fvg_displacement study. Run from KronosStrategies/strategies."""
from __future__ import annotations
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
STRAT = HERE.parent.parent.parent            # KronosStrategies/strategies
RESULTS = HERE / "results"
M5_PATH = STRAT / "backtest/results/bars_cache_2y/is_XAU_USD_5m.parquet"
D1_PATH = STRAT / "backtest/results/bars_cache_2y/is_XAU_USD_1d.parquet"

WINDOW_START = pd.Timestamp("2024-09-01", tz="UTC")
SPLIT = pd.Timestamp("2025-12-01", tz="UTC")
SPIKE_LO = pd.Timestamp("2025-12-25 23:00", tz="UTC")
SPIKE_HI = pd.Timestamp("2025-12-25 23:15", tz="UTC")
HORIZON_S = 8 * 3600
COSTS = (0.45, 0.80)
KZ_HOURS = set(range(7, 11)) | set(range(12, 16))
FEATURES = ["peak_vel", "burst30", "vol_hhi", "pullback_frac", "er2", "peak_vel3", "burst30_3"]
# declared quality direction: +1 = higher is better, -1 = lower is better
DIRECTION = {"peak_vel": 1, "burst30": 1, "vol_hhi": 1, "pullback_frac": -1, "er2": 1,
             "peak_vel3": 1, "burst30_3": 1}


def load_s5_arrays():
    import sys
    sys.path.insert(0, str(STRAT))
    from lab.tools.qa_s5_cache import load_s5
    df = load_s5()
    t = (df["time"].astype("int64") // 10**9).to_numpy(np.int64)   # epoch seconds
    a = {k: df[k].to_numpy(np.float64) for k in ("o", "h", "l", "c", "bid_c", "ask_c", "volume")}
    a["t"] = t
    del df
    return a


def dense_grid(a):
    """5-second dense grid over the whole S5 span: ffilled close, h, l, volume, present."""
    t = a["t"]
    origin = int(t[0])
    slot = (t - origin) // 5
    n = int(slot[-1]) + 1
    present = np.zeros(n, bool); present[slot] = True
    c = np.full(n, np.nan); c[slot] = a["c"]
    # forward fill closes
    idx = np.where(present, np.arange(n), 0)
    np.maximum.accumulate(idx, out=idx)
    c = c[idx]
    h = np.full(n, np.nan); h[slot] = a["h"]
    l = np.full(n, np.nan); l[slot] = a["l"]
    v = np.zeros(n); v[slot] = a["volume"]
    return {"origin": origin, "c": c, "h": h, "l": l, "v": v, "present": present, "n": n}


def pf(x):
    x = np.asarray(x, float)
    gw, gl = x[x > 0].sum(), -x[x <= 0].sum()
    return float(gw / gl) if gl > 0 else float("inf")


def gold_monthly():
    d = pd.read_parquet(D1_PATH)
    d["time"] = pd.to_datetime(d["time"], utc=True)
    m = d.set_index("time")["close"].resample("MS").last()
    return (m.pct_change() * 100).rename("gold_pct")
