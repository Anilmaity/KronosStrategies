"""
test_manager_sim.py
-------------------
Tests for the offline Strategy Manager simulator engine (Tasks 2-4).

sys.path: conftest.py adds repo root (so strategy_manager is a namespace pkg).
This file adds strategies/ so backtest.* and backtest_strategies.* resolve.
"""
from __future__ import annotations

import os
import sys

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies")
)
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

# ── Task 2 ────────────────────────────────────────────────────────────────────

from datetime import datetime, timezone
from types import SimpleNamespace

from backtest.manager_sim_engine import (
    GuardState, SimConfig, evaluate_gates, STRAT_SPECS,
)

UTC = timezone.utc

def _snap(vol="NORMAL", trend="TRENDING", d1="bullish", h4="long",
          session="LONDON", closed=False):
    return SimpleNamespace(vol_regime=vol, trend_regime=trend, d1_bias=d1,
                           h4_bias=h4, session=session, market_closed=closed)

def _cfg(**kw):
    return SimConfig(start=datetime(2026, 4, 1, tzinfo=UTC),
                     end=datetime(2026, 7, 2, tzinfo=UTC), **kw)

def test_market_closed_gates_everything_off():
    g = evaluate_gates(_snap(closed=True), datetime(2026, 4, 6, 8, 0, tzinfo=UTC),
                       GuardState(), 0, _cfg())
    assert all(v[0] is False for v in g.values())

def test_kill_switch_gates_everything_off():
    guard = GuardState(kill_tripped_date="2026-04-06")
    g = evaluate_gates(_snap(), datetime(2026, 4, 6, 8, 0, tzinfo=UTC),
                       guard, 0, _cfg())
    assert all(v[0] is False for v in g.values())

def test_max_concurrent_blocks_new_entries():
    g = evaluate_gates(_snap(), datetime(2026, 4, 6, 8, 0, tzinfo=UTC),
                       GuardState(), 3, _cfg(max_concurrent=3))
    assert all(v[0] is False for v in g.values())

def test_policies_route_correctly():
    # 08:00 UTC LONDON, NORMAL vol, TRENDING, bullish d1, long h4:
    # session_vol in window -> s95 & SESSION_BREAKOUT True;
    # trending -> s96 True; quiet_fade (3-9h, LOW/NORMAL, directional) -> s97 True
    g = evaluate_gates(_snap(), datetime(2026, 4, 6, 8, 0, tzinfo=UTC),
                       GuardState(), 0, _cfg())
    assert g["KRONOS_S95_SESSION_BREAKOUT"][0] is True
    assert g["KRONOS_S96_H1_MOMENTUM"][0] is True
    assert g["KRONOS_S97_SNAP_SCALPER"][0] is True
    assert g["SESSION_BREAKOUT"][0] is True

def test_policy_pauses_outside_window():
    # 11:00 UTC: outside session_vol windows and outside quiet_fade window
    g = evaluate_gates(_snap(), datetime(2026, 4, 6, 11, 0, tzinfo=UTC),
                       GuardState(), 0, _cfg())
    assert g["KRONOS_S95_SESSION_BREAKOUT"][0] is False
    assert g["KRONOS_S97_SNAP_SCALPER"][0] is False
    assert g["KRONOS_S96_H1_MOMENTUM"][0] is True  # trending is time-free

def test_ungated_mode_all_true_despite_guards():
    g = evaluate_gates(_snap(closed=False), datetime(2026, 4, 6, 11, 0, tzinfo=UTC),
                       GuardState(kill_tripped_date="2026-04-06"), 99,
                       _cfg(gated=False))
    assert all(v[0] is True for v in g.values())
