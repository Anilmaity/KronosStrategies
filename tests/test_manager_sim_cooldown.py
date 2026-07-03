"""Cooldown enforcement in run_sim must mirror research_runner.py:133.

sys.path: conftest.py adds repo root; this file adds strategies/ so
backtest.* and backtest_strategies.* resolve (same pattern as the
sibling test_manager_sim*.py modules).
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies")
)
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from backtest.manager_sim_engine import SimConfig, StratSpec, run_sim
import backtest.manager_sim_engine as eng
from backtest_strategies.base import Signal, StrategyConfig


@dataclass
class _Snap:
    d1_bias: str = "bullish"
    h4_bias: str = "bullish"
    vol_regime: str = "NORMAL"
    trend_regime: str = "MIXED"
    session: str = "ASIA"
    market_closed: bool = False
    details: dict = field(default_factory=dict)


def _frames(n_1m: int = 60, start="2026-04-01 03:00:00+00:00"):
    """Flat tape: n_1m one-minute bars at price 100, plus minimal HTF frames."""
    t0 = pd.Timestamp(start)
    def bars(freq, n):
        idx = pd.date_range(t0 - pd.Timedelta(days=2), periods=n, freq=freq, tz="UTC")
        return pd.DataFrame({"time": idx, "open": 100.0, "high": 100.0,
                             "low": 100.0, "close": 100.0})
    f = {tf: bars(freq, 80) for tf, freq in
         [("5m", "5min"), ("15m", "15min"), ("1h", "1h"), ("4h", "4h"), ("1d", "1D")]}
    idx = pd.date_range(t0, periods=n_1m, freq="1min", tz="UTC")
    f["1m"] = pd.DataFrame({"time": idx, "open": 100.0, "high": 100.0,
                            "low": 100.0, "close": 100.0})
    return f


def _spec(cooldown_s: int, calls: list):
    """Strategy stub: fires a far-TP/far-SL BUY on every call, 1-min time exit."""
    def get_signal(w1m, w5m, w15m, now_utc):
        calls.append(now_utc)
        return Signal(side="BUY", entry_price=100.0, stop_loss=50.0,
                      take_profit=200.0, reason="STUB", max_hold_min=1)
    mod = SimpleNamespace(
        NAME="STUB", get_signal=get_signal,
        CONFIG=StrategyConfig(name="STUB", description="", cooldown_s=cooldown_s,
                              session_start_hour=None, session_end_hour=None),
    )
    return StratSpec("STUB", mod, "always_on", {})


def _run(cooldown_s: int, monkeypatch):
    monkeypatch.setattr(eng, "compute_regime", lambda frames, now: _Snap())
    frames = _frames()
    cfg = SimConfig(start=datetime(2026, 4, 1, 3, 0, tzinfo=timezone.utc),
                    end=datetime(2026, 4, 1, 4, 0, tzinfo=timezone.utc),
                    gated=False)
    calls: list = []
    res = run_sim(frames, cfg, specs=[_spec(cooldown_s, calls)])
    return res, calls


def test_cooldown_spaces_entries(monkeypatch):
    res, _ = _run(600, monkeypatch)
    entries = sorted(t.entry_time for t in res.trades)
    assert len(entries) >= 2, "need at least two entries to measure spacing"
    gaps = [(b - a).total_seconds() for a, b in zip(entries, entries[1:])]
    assert all(g >= 600 for g in gaps), f"entries inside cooldown: {gaps}"


def test_cooldown_blocks_get_signal_calls(monkeypatch):
    """Live semantics: cooldown suppresses signal GENERATION, not just entry."""
    _, calls = _run(600, monkeypatch)
    gaps = [(b - a).total_seconds() for a, b in zip(calls, calls[1:])]
    assert all(g >= 600 for g in gaps), "get_signal called inside cooldown"


def test_zero_cooldown_preserves_old_behavior(monkeypatch):
    res_0, _ = _run(0, monkeypatch)
    # Flat tape, 1-min TIME exits: without cooldown, re-entry on ~every bar
    # after each exit. With 60 bars expect far more trades than the 600s run.
    res_600, _ = _run(600, monkeypatch)
    assert len(res_0.trades) > len(res_600.trades) * 2
