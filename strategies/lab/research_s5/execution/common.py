"""Shared loader for the execution study. Loads the S5 cache ONCE (via the QA loader), trims
to the brief's window, drops the 2025-12-25 spike, and keeps numpy arrays. A .npz mirror is
kept in the session scratchpad so later scripts start in seconds instead of minutes."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
STRAT = HERE.parents[2]                       # KronosStrategies/strategies
RES = HERE / "results"
RES.mkdir(exist_ok=True)
STAGE1 = STRAT / "lab" / "results" / "xau2y_stage1"
S5EXIT = STRAT / "lab" / "results" / "s5exit"
CACHE = Path(os.environ.get("S5_NPZ_CACHE", "/private/tmp/claude-501/-Users-anil-Projects-Kronos/"
                            "979109dc-9856-4311-91b0-14d2dda5b135/scratchpad/s5_window.npz"))

WIN_LO = np.datetime64("2024-09-01T00:00:00")
WIN_HI = np.datetime64("2026-09-18T00:00:00")     # window 2024-09-01 -> 2026-09-17 inclusive
SPIKE_LO = np.datetime64("2025-12-25T23:00:00")
SPIKE_HI = np.datetime64("2025-12-25T23:15:00")
SPLIT = np.datetime64("2025-12-01T00:00:00")
COSTS = (0.45, 0.80)
X_EDGES = [0, 1.5, 2.5, 3.5, 5, 7, 10, 15, 25, np.inf]
X_LABELS = ["<1.5", "1.5-2.5", "2.5-3.5", "3.5-5", "5-7", "7-10", "10-15", "15-25", "25+"]
D_LIST = (1, 2, 3, 5, 10)

# the strategy's own max-hold horizon (minutes), from _MAX_HOLD_MIN in the module; None = no time exit
MAX_HOLD = {
    "c03_fvg_fill": None, "s03_ob_mitigation": None, "s04_breaker_block": None,
    "s10_90min_fade": None, "s11_m90_fade_ny": None, "s12_m90_fade_bias": None,
    "s14_ob_mit_bias": None, "s96_h1_momentum": None,
    "s93_fvg_scalp": 120, "s94_sweep_reversal": 1200, "s95_session_breakout": 180,
    "s97_snap_scalper_m5": 30, "s98_zscore_mr_m15": 240, "s99_mss_fvg": 480,
    "s100_m3_combo": 72,
}
NO_HOLD_HORIZON_MIN = 45 * 24 * 60


class S5:
    """Numpy view of the S5 window: t (datetime64[s], naive UTC), o h l c bid ask vol, spread."""

    def __init__(self, t, o, h, l, c, bid, ask, vol):
        self.t, self.o, self.h, self.l, self.c, self.bid, self.ask, self.vol = t, o, h, l, c, bid, ask, vol
        self.spread = ask - bid
        self.ts = t.astype("datetime64[s]").astype(np.int64)          # epoch seconds
        self.hour = ((self.ts // 3600) % 24).astype(np.int8)
        self.wd = (((self.ts // 86400) + 3) % 7).astype(np.int8)       # 1970-01-01 was a Thursday -> Mon=0
        self.month = t.astype("datetime64[M]")

    def __len__(self):
        return len(self.t)


def load() -> S5:
    if CACHE.exists():
        z = np.load(CACHE)
        return S5(z["t"], z["o"], z["h"], z["l"], z["c"], z["bid"], z["ask"], z["vol"])
    sys.path.insert(0, str(STRAT))
    from lab.tools.qa_s5_cache import load_s5
    d = load_s5()
    t = d["time"].dt.tz_convert(None).to_numpy("datetime64[s]")
    keep = (t >= WIN_LO) & (t < WIN_HI) & ~((t >= SPIKE_LO) & (t < SPIKE_HI))
    arrs = dict(t=t[keep], o=d.o.to_numpy(float)[keep], h=d.h.to_numpy(float)[keep],
                l=d.l.to_numpy(float)[keep], c=d.c.to_numpy(float)[keep],
                bid=d.bid_c.to_numpy(float)[keep], ask=d.ask_c.to_numpy(float)[keep],
                vol=d.volume.to_numpy(float)[keep])
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    np.savez(CACHE, **arrs)
    return S5(**arrs)


def load_trades(strategy: str) -> pd.DataFrame:
    """One frame per strategy; asserts the 0.45 and 0.80 files hold the same trades."""
    a = pd.read_parquet(STAGE1 / f"{strategy}_c0.45.trades.parquet")
    b = pd.read_parquet(STAGE1 / f"{strategy}_c0.80.trades.parquet")
    assert len(a) == len(b)
    for col in ("entry_time", "side", "entry_px", "sl", "tp", "outcome"):
        assert (a[col].to_numpy() == b[col].to_numpy()).all(), (strategy, col)
    assert np.allclose(a.pts + 0.45, b.pts + 0.80, atol=1e-3), strategy
    a = a.copy()
    a["entry_time"] = pd.to_datetime(a.entry_time, utc=True)
    a["exit_time"] = pd.to_datetime(a.exit_time, utc=True)
    a["pts0"] = a.pts + 0.45                       # cost-free mid_M1 points
    return a.drop(columns=["pts", "r"])


def strategies() -> list[str]:
    return sorted({p.name.split("_c0.")[0] for p in STAGE1.glob("*.trades.parquet")})


def pf(v: np.ndarray) -> float:
    v = np.asarray(v, float)
    gw, gl = v[v > 0].sum(), -v[v <= 0].sum()
    return float(gw / gl) if gl > 0 else float("inf")


class Tee:
    def __init__(self, path):
        self.f = open(path, "w")
    def __call__(self, *a):
        s = " ".join(str(x) for x in a)
        print(s); self.f.write(s + "\n"); self.f.flush()
