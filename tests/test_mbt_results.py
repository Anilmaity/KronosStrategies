"""Task 9 (Manager Backtest plan): result assembly math."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies"))
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from audit_worker import results  # noqa: E402
from backtest.manager_sim_engine import (  # noqa: E402
    SimConfig, SimResult, TradeRecord,
)

T0 = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
CFG = SimConfig(start=T0, end=T0 + timedelta(days=30))


def _t(pnl, strategy="A", minutes=0):
    return TradeRecord(
        strategy=strategy, entry_time=T0 + timedelta(minutes=minutes - 5),
        side="BUY", entry_px=100.0, sl=99.0, tp=102.0,
        exit_px=100.0 + pnl, exit_time=T0 + timedelta(minutes=minutes),
        outcome="TP" if pnl > 0 else "SL", pnl_pts=pnl,
        pnl_usd=CFG.pts_to_usd(pnl), gate_reason="")


def test_arm_summary_math():
    trades = [_t(2.0, minutes=1), _t(-1.0, minutes=2), _t(-3.0, minutes=3),
              _t(4.0, minutes=4)]
    s = results.arm_summary(trades, CFG)
    assert s["pnl_pts"] == 2.0
    assert s["pnl_usd"] == pytest.approx(CFG.pts_to_usd(2.0), abs=0.01)
    assert s["trades"] == 4
    assert s["win_rate"] == 50.0
    # cum: 2, 1, -2, 2 -> peak 2, trough -2 -> max_dd 4
    assert s["max_dd_pts"] == 4.0
    assert s["profit_factor"] == pytest.approx(6.0 / 4.0)


def test_arm_summary_no_losses_pf_none():
    s = results.arm_summary([_t(1.0)], CFG)
    assert s["profit_factor"] is None


def test_equity_downsample_keeps_final_point():
    trades = [_t(1.0, minutes=i) for i in range(2501)]
    curve = results.equity_curve(trades)
    assert len(curve) <= results.EQUITY_MAX_POINTS + 1
    assert curve[-1][1] == pytest.approx(2501.0)   # final cum value survives
    assert curve[0][1] == pytest.approx(1.0)


def test_sim_per_strategy_shape():
    trades = [_t(2.0, "A"), _t(-1.0, "A"), _t(3.0, "B")]
    m = results.sim_per_strategy(trades, CFG)
    assert m["A"]["points"]["trades"] == 2
    assert m["A"]["points"]["win_rate"] == 50.0
    assert m["A"]["points"]["pnl_pts"] == pytest.approx(1.0)
    assert m["A"]["points"]["profit_factor"] == pytest.approx(2.0 / 1.0)
    assert m["B"]["points"]["pnl_pts"] == pytest.approx(3.0)
    assert m["B"]["points"]["profit_factor"] is None
    assert "_wins" not in m["A"]["points"]


def test_build_arms_and_assemble_v2():
    gated = SimResult(trades=[_t(2.0)], regime_rows=[], kill_trips=["2026-06-05"],
                      paused_pct={"A": 10.0})
    ungated = SimResult(trades=[_t(2.0), _t(1.0, minutes=1)], regime_rows=[],
                        kill_trips=[], paused_pct={})
    summary, curves = results.build_arms(gated, ungated, CFG)
    out = results.assemble_v2(
        per_strategy={"A": {"sim": {}, "live": None, "delta": None}},
        summary=summary, curves=curves, s5_report={"n_ambiguous": 0},
        notes=["note1"], trades_csv="/tmp/x.csv",
        live_risk_usd_inferred=38.0, kill_trips=gated.kill_trips,
        paused_pct=gated.paused_pct, ungated=ungated)
    assert set(out) == {"summary", "per_strategy", "equity_curve",
                       "s5_resolution", "trades_csv", "notes", "kill_trips",
                       "paused_pct", "live_risk_usd_inferred"}
    assert out["summary"]["ungated"]["trades"] == 2
    assert out["equity_curve"]["gated"][0][1] == 2.0
    assert out["notes"] == ["note1"]
    assert out["live_risk_usd_inferred"] == 38.0
    assert out["kill_trips"] == ["2026-06-05"]
    assert out["paused_pct"] == {"A": 10.0}

    solo_summary, solo_curves = results.build_arms(gated, None, CFG)
    solo = results.assemble_v2(
        per_strategy={}, summary=solo_summary, curves=solo_curves,
        s5_report={}, notes=[], trades_csv="x",
        live_risk_usd_inferred=None, kill_trips=[], paused_pct={})
    assert "ungated" not in solo["summary"]
