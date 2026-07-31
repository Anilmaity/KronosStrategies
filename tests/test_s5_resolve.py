"""Task 7 (Manager Backtest plan): S5 ambiguity resolver."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pandas as pd

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies"))
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from audit_worker.s5_resolve import resolve_ambiguous  # noqa: E402
from backtest.manager_sim_engine import SimConfig, TradeRecord  # noqa: E402

T0 = pd.Timestamp("2026-06-01 12:00", tz="UTC")
CFG = SimConfig(start=datetime(2026, 6, 1, tzinfo=timezone.utc),
                end=datetime(2026, 6, 2, tzinfo=timezone.utc))
FR = CFG.entry_friction_pts  # 0.25


def _trade(outcome, side="BUY", entry=100.0, sl=99.0, tp=102.0, exit_px=None,
           pnl=None):
    if exit_px is None:
        level = tp if outcome == "TP" else sl
        exit_px = (level - FR) if side == "BUY" else (level + FR)
    if pnl is None:
        pnl = (exit_px - entry) if side == "BUY" else (entry - exit_px)
    return TradeRecord(
        strategy="S", entry_time=(T0 - pd.Timedelta(minutes=5)).to_pydatetime(),
        side=side, entry_px=entry, sl=sl, tp=tp, exit_px=exit_px,
        exit_time=T0.to_pydatetime(), outcome=outcome, pnl_pts=pnl,
        pnl_usd=CFG.pts_to_usd(pnl), gate_reason="")


def _m1(low, high):
    return pd.DataFrame({
        "time": [T0], "open": [(low + high) / 2], "high": [high],
        "low": [low], "close": [(low + high) / 2],
    })


def _s5(rows):
    """rows: list of (sec_offset, low, high)."""
    return pd.DataFrame({
        "time": [T0 + pd.Timedelta(seconds=s) for s, _, _ in rows],
        "open": [(l + h) / 2 for _, l, h in rows],
        "high": [h for _, _, h in rows],
        "low": [l for _, l, _ in rows],
        "close": [(l + h) / 2 for _, l, h in rows],
    })


def test_buy_flips_sl_to_tp_when_s5_shows_tp_first():
    # M1 said SL (step_position checks SL first); S5 shows TP touched at +5s,
    # SL only at +30s -> verdict flips to TP, pnl recomputed with friction.
    trade = _trade("SL")
    s5 = _s5([(0, 99.5, 102.1), (30, 98.8, 100.0)])
    # first S5 bar touches BOTH -> still ambiguous; use separated bars instead
    s5 = _s5([(0, 99.5, 102.1), (30, 98.8, 100.0)])
    s5.loc[0, "low"] = 99.5   # tp touch only (99.5 > sl 99.0 strict)
    out, rep = resolve_ambiguous([trade], _m1(98.8, 102.1),
                                 lambda a, b: s5, CFG)
    assert rep == {"n_ambiguous": 1, "n_flipped": 1, "n_unresolved": 0,
                   "pnl_delta_pts": rep["pnl_delta_pts"]}
    assert out[0].outcome == "TP"
    assert out[0].exit_px == 102.0 - FR
    assert out[0].pnl_pts == (102.0 - FR) - 100.0
    assert rep["pnl_delta_pts"] > 0


def test_unambiguous_bar_untouched():
    trade = _trade("SL", side="SELL", entry=100.0, sl=101.0, tp=98.0)
    # SELL: sl touch needs high > 101; tp touch needs low <= 98. Bar touches
    # only SL -> not ambiguous, provider must never be called.
    called = []
    out, rep = resolve_ambiguous(
        [trade], _m1(99.0, 101.5),
        lambda a, b: called.append(1) or pd.DataFrame(), CFG)
    assert out == [trade] and called == []
    assert rep["n_ambiguous"] == 0


def test_time_exit_never_touched():
    # step_position checks SL/TP before TIME, so a TIME verdict means neither
    # level was touched in the exit bar; the resolver must leave TIME trades
    # alone even when handed a bar that (impossibly) spans both levels.
    trade = _trade("TIME", exit_px=100.5, pnl=0.5)
    called = []
    out, rep = resolve_ambiguous(
        [trade], _m1(98.0, 103.0),
        lambda a, b: called.append(1) or pd.DataFrame(), CFG)
    assert out == [trade] and called == []
    assert rep["n_ambiguous"] == 0


def test_empty_s5_keeps_m1_verdict():
    trade = _trade("SL")
    out, rep = resolve_ambiguous([trade], _m1(98.8, 102.1),
                                 lambda a, b: pd.DataFrame(), CFG)
    assert out == [trade]
    assert rep == {"n_ambiguous": 1, "n_flipped": 0, "n_unresolved": 1,
                   "pnl_delta_pts": 0.0}


def test_trail_and_open_never_touched():
    trades = [_trade("TRAIL"), _trade("OPEN", exit_px=100.2, pnl=0.2)]
    called = []
    out, rep = resolve_ambiguous(
        trades, _m1(0.0, 100000.0),
        lambda a, b: called.append(1) or pd.DataFrame(), CFG)
    assert out == trades and called == []
    assert rep["n_ambiguous"] == 0
