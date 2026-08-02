"""Task 4 (Manager Backtest fidelity fix): matched-USD via inferred live risk.

sizing.infer_live_risk_usd() derives live's per-trade risk budget (median
|usd| of live losers) so the sim can be re-priced at the same risk-sizing
clamp entry_manager._risk_sized_qty uses live. results.add_matched_usd()
attaches a {"usd": {...}} block per strategy to the sim points map.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies"))
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from audit_worker import results  # noqa: E402
from audit_worker.sizing import infer_live_risk_usd, matched_usd  # noqa: E402
from backtest.manager_sim_engine import TradeRecord  # noqa: E402

T0 = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def _t(pnl_pts, sl_dist=3.0, strategy="A", minutes=0, side="BUY",
      entry_px=100.0):
    sl = entry_px - sl_dist if side == "BUY" else entry_px + sl_dist
    return TradeRecord(
        strategy=strategy, entry_time=T0 + timedelta(minutes=minutes - 5),
        side=side, entry_px=entry_px, sl=sl, tp=entry_px + 10,
        exit_px=entry_px + pnl_pts, exit_time=T0 + timedelta(minutes=minutes),
        outcome="TP" if pnl_pts > 0 else "SL", pnl_pts=pnl_pts,
        pnl_usd=0.0, gate_reason="")


def test_infer_uses_median_abs_of_losers():
    # losers cost ~ -R each; median |usd| == 38
    assert infer_live_risk_usd([-36.0, -38.0, -40.0, -38.0, -37.0]) == 38.0
    assert infer_live_risk_usd([-38.0, -40.0]) is None          # below floor
    assert infer_live_risk_usd([], floor=5) is None


def test_matched_usd_prices_sim_trade_like_live():
    # 3pt stop, $38 budget -> lots = 38/(3*100)=0.1267 -> clamp/round to 0.12
    # a +6pt winner -> 6 * 0.12 * 100 = $72
    usd = matched_usd(pnl_pts=6.0, sl_dist_pts=3.0, risk_usd=38.0)
    assert round(usd, 2) == 72.0


def test_add_matched_usd_sums_per_strategy():
    trades = [
        _t(6.0, sl_dist=3.0, strategy="A", minutes=1),   # matched_usd = 72.0
        _t(-3.0, sl_dist=3.0, strategy="A", minutes=2),  # matched_usd = -36.0
        _t(4.0, sl_dist=2.0, strategy="B", minutes=3),   # lots~0.19 -> 76.0
    ]
    sim_map = {
        "A": {"points": {"pnl_pts": 3.0, "trades": 2, "win_rate": 50.0,
                         "profit_factor": None}},
        "B": {"points": {"pnl_pts": 4.0, "trades": 1, "win_rate": 100.0,
                         "profit_factor": None}},
    }
    results.add_matched_usd(sim_map, trades, risk_usd=38.0)

    assert sim_map["A"]["usd"]["pnl_usd"] == 36.0    # 72.0 + (-36.0)
    assert sim_map["A"]["usd"]["trades"] == 2
    assert sim_map["A"]["usd"]["win_rate"] == 50.0
    assert sim_map["B"]["usd"]["pnl_usd"] == 76.0
    assert sim_map["B"]["usd"]["trades"] == 1
    assert sim_map["B"]["usd"]["win_rate"] == 100.0
    # points block untouched
    assert sim_map["A"]["points"]["pnl_pts"] == 3.0
