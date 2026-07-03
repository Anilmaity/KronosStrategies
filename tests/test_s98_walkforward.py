"""M15-native S98 harness: fill/exit conventions and OOS slice hygiene.

sys.path: conftest.py adds repo root; this file adds strategies/ so that
`backtest.*` and `backtest_strategies.*` resolve -- same pattern as
test_manager_sim.py / test_backfill_merge.py.
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies")
)
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from backtest.backtest_s98_walkforward import (   # noqa: E402
    FRICTION_PTS, TRAIN_END, simulate, slice_train, slice_oos,
)


def _df(closes, start="2025-06-02 03:00:00+00:00", highs=None, lows=None):
    idx = pd.date_range(pd.Timestamp(start), periods=len(closes),
                        freq="15min", tz="UTC")
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "time": idx, "open": closes,
        "high": highs if highs is not None else closes,
        "low":  lows  if lows  is not None else closes,
        "close": closes,
    })


# NOTE: S98's ADF gate needs >= 50 non-NaN (close - SMA50) residuals, so the
# earliest bar that can carry a valid ADF window is index 98 (SMA50 leaves 49
# NaNs, then 50 residuals). The signal bar must ALSO be in-session (03:00-09:00
# UTC); with a 03:00 start that means bar indices 96..119. spike_at=98 is the
# earliest bar that is both tradeable and in-session. seed chosen so the single
# trade's TP (harness SMA at the signal bar) lines up with the fixture's base
# mean within the assertion tolerance -- per the brief, tune the seed not the
# gate.
def _mr_series(n=138, spike_at=98, spike_sigma=2.5, seed=0):
    rng = np.random.default_rng(seed)
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = 0.5 * x[i - 1] + rng.normal(0, 1.0)
    closes = 4000.0 + x
    base = pd.Series(closes[:spike_at])
    sd = float(base.rolling(50).std(ddof=1).iloc[-1])
    mean = float(base.rolling(50).mean().iloc[-1])
    closes[spike_at] = mean + spike_sigma * sd     # SELL cross at spike_at
    closes[spike_at + 1:] = mean                   # immediate full reversion
    return closes, mean, sd


def test_tp_exit_and_friction():
    closes, mean, sd = _mr_series()
    res = simulate(_df(closes), 50, 2.0, 3.5)
    assert res["trades"] == 1
    # SELL fill = signal close - 0.25; TP exit = tp + 0.25 (adverse friction
    # both sides). pnl = fill - exit = (entry - 0.25) - (tp + 0.25).
    entry = closes[98]                              # spike_at (signal bar)
    expected = (entry - FRICTION_PTS) - (round(mean, 2) + FRICTION_PTS)
    assert res["net_pts"] == pytest.approx(expected, abs=0.02)


def test_sl_first_when_both_touched():
    """A bar whose high tags SL and low tags TP books the SL (conservative)."""
    closes, mean, sd = _mr_series()
    df = _df(closes)
    j = 99                                          # first bar after entry (spike_at + 1)
    df.loc[j, "high"] = mean + 4.0 * sd             # beyond SL (3.5 sd)
    df.loc[j, "low"] = mean - 1.0                   # beyond TP too
    res = simulate(df, 50, 2.0, 3.5)
    assert res["trades"] == 1
    assert res["net_pts"] < 0, "both-touched bar must resolve as SL"


def test_train_slice_never_contains_2026():
    idx = pd.date_range("2025-12-30", "2026-01-03", freq="15min", tz="UTC")
    df = pd.DataFrame({"time": idx, "open": 4000.0, "high": 4000.0,
                       "low": 4000.0, "close": 4000.0})
    tr, oo = slice_train(df), slice_oos(df)
    assert (tr["time"] < TRAIN_END).all()
    assert (oo["time"] >= TRAIN_END).all()
    assert len(tr) + len(oo) == len(df)
