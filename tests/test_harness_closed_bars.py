"""lab.harness must hand get_signal() only CLOSED higher-timeframe bars, exactly as
research_runner does (fetch_candles filters on OANDA's `complete` flag).

Found 2026-09-18 (r2_c03): the M5/M15 windows were sliced with
searchsorted(t5, t1[i], "right") on OPEN-stamped bars, so at 4 of every 5 M1 ticks the
"last closed M5 bar" was the bar in progress -- its close/high/low 1-4 min in the future
(14 min for M15). c03 took 69 % of its Stage-1 entries at the leaky minute; s14's PF
was 1.87 there vs 0.91 at the one honest minute. Every screen result depended on it.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import pytest

_STRAT_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "strategies"))
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from lab.harness import CACHE, Cfg, load_bars, replay   # noqa: E402

pytestmark = pytest.mark.skipif(
    not (CACHE / "is_XAU_USD_1m.parquet").exists(), reason="bars cache not present")

_PROBE = Path(_STRAT_DIR) / "backtest_strategies" / "zz_probe_closed_bars.py"
_LOG = Path(_STRAT_DIR) / "backtest_strategies" / "zz_probe_closed_bars.log"
_PROBE_SRC = '''
"""Throwaway probe written by tests/test_harness_closed_bars.py -- safe to delete."""
from datetime import timedelta
from backtest_strategies.base import StrategyConfig
NAME = "PROBE_CLOSED_BARS"
CONFIG = StrategyConfig(name=NAME, description="probe", cooldown_s=0)
MIN_BARS_1M = 10; MIN_BARS_5M = 5; MIN_BARS_15M = 3
_LOG = __file__[:-3] + ".log"
def get_signal(w1m, w5m, w15m, now_utc):
    # the newest bar shown on each frame must have CLOSED by the end of this M1 bar
    m1_close = w1m["time"].iloc[-1] + timedelta(minutes=1)
    c5 = w5m["time"].iloc[-1] + timedelta(minutes=5)
    c15 = w15m["time"].iloc[-1] + timedelta(minutes=15)
    with open(_LOG, "a") as fh:
        fh.write(f"{now_utc.isoformat()},{(c5 - m1_close).total_seconds()},{(c15 - m1_close).total_seconds()}\\n")
    return None
'''


@pytest.fixture
def probe():
    _PROBE.write_text(_PROBE_SRC)
    if _LOG.exists():
        _LOG.unlink()
    try:
        yield
    finally:
        for p in (_PROBE, _LOG):
            if p.exists():
                p.unlink()


def test_htf_windows_contain_only_closed_bars(probe):
    bars = load_bars()
    replay("zz_probe_closed_bars", bars, start="2026-05-04", end="2026-05-05",
           cfg=Cfg(win_1m=10, win_5m=5, win_15m=3))
    log = pd.read_csv(_LOG, names=["now", "m5_close_lead_s", "m15_close_lead_s"])
    assert len(log) > 500, "probe was not called"
    # a positive lead means the bar's close lies AFTER this M1 bar's close: look-ahead
    leaky5 = (log.m5_close_lead_s > 0).mean()
    leaky15 = (log.m15_close_lead_s > 0).mean()
    assert leaky5 == 0 and leaky15 == 0, (
        f"M5 window leaks the bar in progress on {leaky5:.0%} of M1 ticks, "
        f"M15 on {leaky15:.0%} (expected 0%)")
    # and the newest closed bar is shown promptly: at the M1 bar that completes an M5 bar,
    # that bar is already in the window (lead == 0 on ~1/5 of ticks, never < -300 s)
    assert (log.m5_close_lead_s == 0).mean() > 0.15
    assert log.m5_close_lead_s.min() >= -300
