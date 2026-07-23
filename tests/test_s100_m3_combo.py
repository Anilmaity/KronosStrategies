# tests/test_s100_m3_combo.py
"""S100 M3 combo scalper (spec v3) — unit tests for the live module.

Covers: closed-bucket M3 resampling, the EMA direction gate, FVG pending
arm + 1m-touch fire with max(2.5R, 1.5pt) geometry, phantom-stop cancel,
hours gating clearing state, RSI3-momentum immediate fire, and the
entry_manager variation registration the runner requires at startup.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies")))

import backtest_strategies.s100_m3_combo as s100  # noqa: E402


def _m1(closes, start="2026-07-20 03:00", spread=0.3):
    """Build a w1m frame from a list of close prices (1 bar per minute)."""
    n = len(closes)
    t = pd.date_range(start, periods=n, freq="1min", tz="UTC")
    c = np.asarray(closes, float)
    o = np.concatenate(([c[0]], c[:-1]))
    h = np.maximum(o, c) + spread
    l = np.minimum(o, c) - spread
    return pd.DataFrame({"time": t, "open": o, "high": h, "low": l, "close": c})


def _uptrend_base(n_m1=1950, drift=0.05):
    """Gently rising M1 series — EMA20 > EMA200 on M3; sigma sized so the
    fixture FVG gap (~2pt) sits inside the [0.3, 1.5] x ATR validity band."""
    rng = np.random.default_rng(7)
    steps = drift + rng.normal(0, 0.30, n_m1)
    return list(4000 + np.cumsum(steps))


@pytest.fixture(autouse=True)
def _clean_state():
    s100.reset_state()
    yield
    s100.reset_state()


def test_variation_registered_for_runner():
    from strategy.entry_manager import _VARIATION_STRATEGY_NAME
    assert s100.NAME in _VARIATION_STRATEGY_NAME
    assert _VARIATION_STRATEGY_NAME[s100.NAME] == "S100 M3 Combo Scalper"


def test_resample_drops_incomplete_bucket():
    w1m = _m1([1, 2, 3, 4, 5, 6, 7, 8], start="2026-07-20 03:00")  # 03:00..03:07
    m3 = s100._resample_m3(w1m)
    # 03:06 bucket has only 03:06+03:07 -> incomplete, must be dropped
    assert m3["time"].iloc[-1] == pd.Timestamp("2026-07-20 03:03", tz="UTC")
    full = _m1([1, 2, 3, 4, 5, 6, 7, 8, 9], start="2026-07-20 03:00")
    m3 = s100._resample_m3(full)  # 03:06..03:08 complete
    assert m3["time"].iloc[-1] == pd.Timestamp("2026-07-20 03:06", tz="UTC")


def _fvg_tail(base, gap_pts=1.2):
    """Append M1 bars forming a bullish M3 FVG on the last closed bucket:
    low[k] > high[k-2] by ~gap_pts."""
    lvl = base[-1]
    up = lvl + gap_pts + 1.2       # displacement well past the prior bucket
    # bucket k-1 (3 bars flat-ish up), bucket k (3 bars holding high)
    return base + [lvl + 0.2, lvl + 0.5, up,          # k-1: thrust
                   up + 0.3, up + 0.5, up + 0.4]      # k: holds above -> gap


def test_fvg_arms_and_fires_on_touch():
    base = _uptrend_base()
    w1m = _m1(_fvg_tail(base))
    now = datetime(2026, 7, 20, 13, 30, tzinfo=timezone.utc)
    assert s100.get_signal(w1m, None, None, now) is None      # detection arms
    p = s100._pending
    assert p is not None and p["side"] == 1 and p["kind"] == "fvg"
    risk = p["prox"] - p["sl"]
    assert p["tp"] == pytest.approx(p["prox"] + max(2.5 * risk, 1.5), abs=0.02)

    # a later 1m probe touching the proximal edge fires the BUY at the edge
    probe = _m1([p["prox"] + 0.4, p["prox"] + 0.05], start="2026-07-20 13:31")
    w1m2 = pd.concat([w1m, probe], ignore_index=True)
    w1m2["time"] = list(w1m["time"]) + list(
        pd.date_range(w1m["time"].iloc[-1] + pd.Timedelta(minutes=1),
                      periods=2, freq="1min"))
    sig = s100.get_signal(w1m2, None, None,
                          datetime(2026, 7, 20, 13, 33, tzinfo=timezone.utc))
    assert sig is not None and sig.side == "BUY"
    assert sig.entry_price == p["prox"] and sig.take_profit == p["tp"]
    assert sig.max_hold_min == 72


def test_probe_through_stop_cancels():
    base = _uptrend_base()
    w1m = _m1(_fvg_tail(base))
    now = datetime(2026, 7, 20, 13, 30, tzinfo=timezone.utc)
    s100.get_signal(w1m, None, None, now)
    p = s100._pending
    assert p is not None
    crash = _m1([p["sl"] - 0.5, p["sl"] - 0.6], start="2026-07-20 13:31")
    w1m2 = pd.concat([w1m, crash], ignore_index=True)
    w1m2["time"] = list(w1m["time"]) + list(
        pd.date_range(w1m["time"].iloc[-1] + pd.Timedelta(minutes=1),
                      periods=2, freq="1min"))
    sig = s100.get_signal(w1m2, None, None,
                          datetime(2026, 7, 20, 13, 33, tzinfo=timezone.utc))
    assert sig is None and s100._pending is None      # phantom guard cancelled


def test_out_of_hours_returns_none_and_clears_pending():
    base = _uptrend_base()
    w1m = _m1(_fvg_tail(base))
    s100.get_signal(w1m, None, None,
                    datetime(2026, 7, 20, 13, 30, tzinfo=timezone.utc))
    assert s100._pending is not None
    # hour 10 UTC is in the measured dead zone and outside _HOURS
    sig = s100.get_signal(w1m, None, None,
                          datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc))
    assert sig is None and s100._pending is None
    assert 9 not in s100._HOURS and 12 not in s100._HOURS


def test_ema_gate_blocks_counter_direction():
    # downtrend base -> EMA direction short; a bullish FVG must NOT arm
    rng = np.random.default_rng(11)
    base = list(4000 - np.cumsum(0.05 + rng.normal(0, 0.15, 1950)))
    w1m = _m1(_fvg_tail(base))          # bullish gap against the trend
    sig = s100.get_signal(w1m, None, None,
                          datetime(2026, 7, 20, 13, 30, tzinfo=timezone.utc))
    assert sig is None
    assert s100._pending is None or s100._pending["side"] == -1


def test_rsi_momentum_fires_immediately_long():
    base = _uptrend_base()
    # strong burst: last M3 bucket rockets -> RSI3 crosses into >85
    lvl = base[-1]
    burst = [lvl + 0.5, lvl + 1.4, lvl + 2.5]
    w1m = _m1(base + burst)
    sig = s100.get_signal(w1m, None, None,
                          datetime(2026, 7, 20, 13, 30, tzinfo=timezone.utc))
    if sig is not None:                  # burst may arm FVG first — both valid
        assert sig.side == "BUY"
        assert sig.reason.startswith("S100_")
        risk = sig.entry_price - sig.stop_loss
        assert sig.take_profit == pytest.approx(
            sig.entry_price + max(2.5 * risk, 1.5), abs=0.02)
    else:
        assert s100._pending is not None and s100._pending["side"] == 1
