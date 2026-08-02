"""Task 6 (Manager Backtest plan): result-JSON assembly wiring
(points/matched-usd/reconciliation into `assemble_v2`)."""
from __future__ import annotations

import os
import sys

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies"))
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from audit_worker import results  # noqa: E402


def test_assemble_carries_points_and_reconciliation():
    per_strategy = {
        "S93 FVG Scalp": {
            "sim": {"points": {"pnl_pts": 12.0, "trades": 5, "win_rate": 40.0,
                               "profit_factor": 1.2}},
            "live": {"points": {"pnl_pts": -3.0, "trades": 3, "win_rate": 33.0,
                                "profit_factor": 0.8},
                     "usd": {"pnl_usd": -30.0, "trades": 3, "win_rate": 33.0}},
            "reconciliation": {"live_generated": 6, "live_placed": 3,
                               "rejected": {"entry_drift": 3}, "sim_trades": 5},
        }
    }
    out = results.assemble_v2(per_strategy=per_strategy,
                              summary={"gated": {}}, curves={}, s5_report={},
                              notes=["x"], trades_csv="/tmp/t.csv",
                              live_risk_usd_inferred=38.0,
                              kill_trips=[], paused_pct={})
    assert out["per_strategy"]["S93 FVG Scalp"]["reconciliation"]["sim_trades"] == 5
    assert out["live_risk_usd_inferred"] == 38.0
    assert "trades_csv" in out


def test_assemble_v2_ungated_kwarg_is_accepted_but_not_embedded():
    # ungated is accepted for call-site symmetry with the worker (which folds
    # it into summary/curves via build_arms before calling assemble_v2) but
    # must not leak into the output on its own.
    out = results.assemble_v2(per_strategy={}, summary={"gated": {}}, curves={},
                              s5_report={}, notes=[], trades_csv="x",
                              live_risk_usd_inferred=None, kill_trips=[],
                              paused_pct={}, ungated=object())
    assert "ungated" not in out
