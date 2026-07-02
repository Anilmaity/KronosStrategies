"""
test_manager_sim_report.py  (Task 5 — TDD)
-------------------------------------------
Tests for strategies/backtest/manager_sim_report.py.

Strategy:
  Hand-build SimResult objects with known P&L across two months, then assert
  exact numbers appear in the rendered markdown and that the rubric verdict
  flips correctly between a passing and a failing fixture.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone, timedelta

import pytest

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies")
)
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from backtest.manager_sim_engine import SimResult, SimConfig, TradeRecord
from backtest.manager_sim_report import write_report

UTC = timezone.utc


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _cfg():
    return SimConfig(
        start=datetime(2026, 4, 1, tzinfo=UTC),
        end=datetime(2026, 7, 2, tzinfo=UTC),
    )


def _tr(strategy, exit_date_str, pnl_usd, outcome="TP", side="BUY"):
    """Minimal TradeRecord helper. pnl_pts derived from pnl_usd assuming $2/pt."""
    exit_time = datetime.fromisoformat(exit_date_str).replace(tzinfo=UTC)
    entry_time = exit_time - timedelta(minutes=60)
    pnl_pts = pnl_usd / 2.0       # 0.02 lots * 100 = 2.0 $/pt
    return TradeRecord(
        strategy=strategy,
        entry_time=entry_time,
        side=side,
        entry_px=3300.0,
        sl=3295.0 if side == "BUY" else 3305.0,
        tp=3310.0 if side == "BUY" else 3290.0,
        exit_px=3300.0 + pnl_pts if side == "BUY" else 3300.0 - pnl_pts,
        exit_time=exit_time,
        outcome=outcome,
        pnl_pts=pnl_pts,
        pnl_usd=pnl_usd,
        gate_reason="test",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Fixture A — gated beats ungated on every rubric condition → RECOMMEND
#
# Gated trades (strategy "S1"):
#   Apr: +30.00 USD  (exit 2026-04-10)
#   May: +20.00 USD  (exit 2026-05-10)
#   Combined gated net = +50.00
#   Equity curve: 30 → 50  →  max DD = 0.00
#
# Ungated trades (strategy "S1"):
#   Apr 10:  +10.00 USD
#   Apr 15: -25.00 USD
#   May 10:  +10.00 USD
#   Combined ungated net = -5.00
#   Equity curve: +10 → -15 → -5  →  peak=10, trough=-15, max DD = 25.00
#
# Combined delta = 50.00 − (−5.00) = +55.00
# Month deltas: Apr +45.00, May +10.00  (gated wins both)
# ─────────────────────────────────────────────────────────────────────────────

def _fixture_a():
    gated = SimResult(
        trades=[
            _tr("S1", "2026-04-10T12:00:00", pnl_usd=30.0),
            _tr("S1", "2026-05-10T12:00:00", pnl_usd=20.0),
        ],
        regime_rows=[],
        kill_trips=[],
        paused_pct={"S1": 35.0},
    )
    ungated = SimResult(
        trades=[
            _tr("S1", "2026-04-10T12:00:00", pnl_usd=10.0),
            _tr("S1", "2026-04-15T12:00:00", pnl_usd=-25.0, outcome="SL"),
            _tr("S1", "2026-05-10T12:00:00", pnl_usd=10.0),
        ],
        regime_rows=[],
        kill_trips=[],
        paused_pct={"S1": 0.0},
    )
    return gated, ungated


# ─────────────────────────────────────────────────────────────────────────────
# Fixture B — ungated beats gated on net $ → DO NOT RECOMMEND
#
# Gated:   Apr 10  −30.00 USD  (net = −30.00)
# Ungated: Apr 10  +50.00 USD  (net = +50.00)
# ─────────────────────────────────────────────────────────────────────────────

def _fixture_b():
    gated = SimResult(
        trades=[_tr("S1", "2026-04-10T12:00:00", pnl_usd=-30.0, outcome="SL")],
        regime_rows=[],
        kill_trips=[],
        paused_pct={"S1": 50.0},
    )
    ungated = SimResult(
        trades=[_tr("S1", "2026-04-10T12:00:00", pnl_usd=50.0)],
        regime_rows=[],
        kill_trips=[],
        paused_pct={"S1": 0.0},
    )
    return gated, ungated


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_write_report_returns_md_path(tmp_path):
    """write_report must return a path ending in .md that exists."""
    gated, ungated = _fixture_a()
    path = write_report(gated, ungated, _cfg(), tmp_path, sensitivity=None)
    assert path.exists()
    assert path.suffix == ".md"


def test_csv_files_written(tmp_path):
    """write_report must also produce trades CSVs and a regime CSV."""
    gated, ungated = _fixture_a()
    write_report(gated, ungated, _cfg(), tmp_path, sensitivity=None)
    csv_files = list(tmp_path.glob("*.csv"))
    assert len(csv_files) >= 3, f"Expected >=3 CSVs, got {[f.name for f in csv_files]}"


def test_combined_delta_exact_number(tmp_path):
    """Combined delta net USD = 50.00 − (−5.00) = +55.00 must appear in the MD.

    Fixture A: gated net = +50.00, ungated net = −5.00  → delta = +55.00.
    """
    gated, ungated = _fixture_a()
    path = write_report(gated, ungated, _cfg(), tmp_path, sensitivity=None)
    text = path.read_text(encoding="utf-8")
    assert "+55.00" in text, (
        f"Expected '+55.00' (combined G-U delta) in report.\n\nActual report:\n{text}"
    )


def test_month_table_has_both_months(tmp_path):
    """Month-by-month table must contain rows for Apr 2026 and May 2026."""
    gated, ungated = _fixture_a()
    path = write_report(gated, ungated, _cfg(), tmp_path, sensitivity=None)
    text = path.read_text(encoding="utf-8")
    assert "2026-04" in text, "Missing Apr 2026 in month table"
    assert "2026-05" in text, "Missing May 2026 in month table"


def test_month_apr_gated_net(tmp_path):
    """Apr gated net = +30.00 must appear in the month table."""
    gated, ungated = _fixture_a()
    path = write_report(gated, ungated, _cfg(), tmp_path, sensitivity=None)
    text = path.read_text(encoding="utf-8")
    assert "+30.00" in text, "Apr gated net +30.00 missing"


def test_rubric_recommend_when_gated_wins(tmp_path):
    """Fixture A: rubric verdict is RECOMMEND master ON (gated wins net + DD + months)."""
    gated, ungated = _fixture_a()
    path = write_report(gated, ungated, _cfg(), tmp_path, sensitivity=None)
    text = path.read_text(encoding="utf-8")
    # The verdict line must contain RECOMMEND but NOT 'DO NOT RECOMMEND'
    assert "VERDICT: RECOMMEND" in text, "Expected VERDICT: RECOMMEND in report"
    assert "VERDICT: DO NOT RECOMMEND" not in text, (
        "Expected VERDICT: DO NOT RECOMMEND to be absent when gated wins"
    )


def test_rubric_provisional_when_no_sensitivity(tmp_path):
    """Rubric verdict must be marked PROVISIONAL when sensitivity=None."""
    gated, ungated = _fixture_a()
    path = write_report(gated, ungated, _cfg(), tmp_path, sensitivity=None)
    text = path.read_text(encoding="utf-8")
    assert "PROVISIONAL" in text, "Expected PROVISIONAL caveat when sensitivity=None"


def test_rubric_do_not_recommend_when_ungated_wins(tmp_path):
    """Fixture B: rubric verdict is DO NOT RECOMMEND (ungated net > gated net)."""
    gated, ungated = _fixture_b()
    path = write_report(gated, ungated, _cfg(), tmp_path, sensitivity=None)
    text = path.read_text(encoding="utf-8")
    assert "VERDICT: DO NOT RECOMMEND" in text, (
        "Expected VERDICT: DO NOT RECOMMEND when ungated beats gated"
    )


def test_sensitivity_section_absent_when_none(tmp_path):
    """When sensitivity=None, the grid section says it was not run."""
    gated, ungated = _fixture_a()
    path = write_report(gated, ungated, _cfg(), tmp_path, sensitivity=None)
    text = path.read_text(encoding="utf-8")
    assert "--sensitivity" in text or "not run" in text.lower(), (
        "Sensitivity section should note it was not run"
    )


def test_sensitivity_grid_rendered_when_present(tmp_path):
    """When sensitivity data is provided, the grid appears in the report."""
    gated, ungated = _fixture_a()
    sensitivity = [
        ("vol(20/70/90)", 48.00),
        ("vol(30/80/97)", 52.50),
    ]
    path = write_report(gated, ungated, _cfg(), tmp_path, sensitivity=sensitivity)
    text = path.read_text(encoding="utf-8")
    assert "vol(20/70/90)" in text
    assert "vol(30/80/97)" in text
    assert "48.00" in text


def test_sensitivity_sign_flip_triggers_do_not_recommend(tmp_path):
    """If a sensitivity variant flips the combined delta sign, verdict must be DO NOT RECOMMEND.

    Fixture A base delta = +55.00 (positive).
    Provide a sensitivity variant with combined_net_usd < ungated_net (−5.00):
      variant net = −10.00 → variant delta = −10 − (−5) = −5 < 0  ← sign flipped.
    """
    gated, ungated = _fixture_a()
    # ungated net = -5.00; a variant net of -10.00 gives delta = -5.00 (sign flipped)
    sensitivity = [("vol(20/70/90)", -10.00)]
    path = write_report(gated, ungated, _cfg(), tmp_path, sensitivity=sensitivity)
    text = path.read_text(encoding="utf-8")
    assert "DO NOT RECOMMEND" in text, (
        "Expected DO NOT RECOMMEND when a sensitivity variant flips the delta sign"
    )


def test_kill_trips_shown_for_gated(tmp_path):
    """Kill-switch trip dates must appear in the report when present."""
    gated, ungated = _fixture_a()
    gated.kill_trips.append("2026-04-12")
    path = write_report(gated, ungated, _cfg(), tmp_path, sensitivity=None)
    text = path.read_text(encoding="utf-8")
    assert "2026-04-12" in text


def test_paused_pct_shown(tmp_path):
    """Policy pause percentage for each strategy must appear in the report."""
    gated, ungated = _fixture_a()
    path = write_report(gated, ungated, _cfg(), tmp_path, sensitivity=None)
    text = path.read_text(encoding="utf-8")
    # Fixture A: paused_pct = {"S1": 35.0}
    assert "35.0%" in text, "Expected 35.0% paused for S1"


def test_caveat_paragraph_present(tmp_path):
    """The 3-month regime-sample caveat must be in the report."""
    gated, ungated = _fixture_a()
    path = write_report(gated, ungated, _cfg(), tmp_path, sensitivity=None)
    text = path.read_text(encoding="utf-8")
    # The caveat mentions "3 months" or "one regime sample"
    lower = text.lower()
    assert "3 month" in lower or "one regime sample" in lower, (
        "Caveat paragraph about 3-month limitation is missing"
    )
