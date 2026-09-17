"""S94 level/sweep machine: the compiled kernel must reproduce the pure-Python walk.

tests/test_s94_parity.py proves incremental == full-rebuild *within* one implementation.
This file pins the implementation itself: `fixtures/s94_pending_trace.json` was recorded
from the pre-numba module (commit 665e973) by `_record()` below, over 60 real 1500-bar M5
windows of the bars cache, each slid forward one bar at a time for 40 steps -- the exact
window pattern the live runner and the harness feed _detect -- plus a synthetic frame
that exercises PD / session / swing births and a confirm. The `_pending` list after every
call is the trace. Any change to the machine that alters a single armed setup fails here.

Regenerate ONLY from a known-good module:  python tests/test_s94_kernel_golden.py --record
"""
from __future__ import annotations

import json
import os
import sys
from datetime import timezone

import numpy as np
import pandas as pd
import pytest

_STRAT_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "strategies"))
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from backtest_strategies import s94_sweep_reversal as s94   # noqa: E402
from lab.harness import CACHE, TF_FILES                       # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "s94_pending_trace.json")
WIN, STEPS, N_WINDOWS = 1500, 40, 60


def _m5_cache() -> pd.DataFrame:
    df = pd.read_parquet(CACHE / TF_FILES["5m"])
    df = df.assign(time=pd.to_datetime(df["time"], utc=True).dt.tz_convert(None)
                   .astype("datetime64[ns]")).sort_values("time").reset_index(drop=True)
    for c in ("open", "high", "low", "close"):
        df[c] = df[c].astype(float)
    return df


def _windows(df: pd.DataFrame):
    rng = np.random.default_rng(94)
    for i0 in rng.integers(0, len(df) - WIN - STEPS, size=N_WINDOWS):
        yield int(i0)


def _snap() -> list:
    return [[p["side"], p["level"], p["stop"], p["tp"],
             p["armed_after"].isoformat(), p["expires_at"].isoformat()]
            for p in s94._pending]


def _trace_one(df: pd.DataFrame, i0: int) -> list:
    s94.reset_state()
    out = []
    for k in range(STEPS):
        w = df.iloc[i0 + k: i0 + k + WIN].reset_index(drop=True)
        now = w["time"].iloc[-1].to_pydatetime().replace(tzinfo=timezone.utc)
        s94._detect(w, now)
        out.append(_snap())
    return out


def _trace_all(df: pd.DataFrame) -> dict:
    return {str(i0): _trace_one(df, i0) for i0 in _windows(df)}


@pytest.mark.skipif(not (CACHE / TF_FILES["5m"]).exists(), reason="bars cache not present")
def test_kernel_reproduces_recorded_pending_trace():
    assert os.path.exists(FIXTURE), "golden missing -- record it from a known-good module"
    golden = json.load(open(FIXTURE, encoding="utf-8"))
    got = _trace_all(_m5_cache())
    assert got.keys() == golden.keys()
    armed = 0
    for i0 in golden:
        assert got[i0] == golden[i0], f"pending trace diverged in window starting at row {i0}"
        armed += sum(len(s) for s in golden[i0])
    assert armed > 0, "golden has no armed setups -- it pins nothing"


def _record() -> None:
    os.makedirs(os.path.dirname(FIXTURE), exist_ok=True)
    trace = _trace_all(_m5_cache())
    with open(FIXTURE, "w", encoding="utf-8") as fh:
        json.dump(trace, fh, separators=(",", ":"))
    print(f"recorded {len(trace)} windows x {STEPS} steps, "
          f"{sum(len(s) for t in trace.values() for s in t)} pending snapshots -> {FIXTURE}")


if __name__ == "__main__":
    if "--record" in sys.argv:
        _record()
    else:
        print(__doc__)
