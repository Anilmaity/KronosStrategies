"""Task 5 (Manager Backtest fidelity plan): reconcile sim trades vs the
StrategySignal audit table."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies"))
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

import shared.models as m  # noqa: E402
from audit_worker.reconcile import reconcile  # noqa: E402

_IST = timedelta(hours=5, minutes=30)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    m.Position.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as s:
        yield s
    engine.dispose()


def _sig(session, strat, status, reason, when_utc):
    session.add(m.StrategySignal(
        symbol="XAU_USD", side="BUY", entry_price=2000, status=status,
        rejection_reason=reason, strategy_id=strat.id, signal_at=when_utc + _IST))
    session.flush()


def test_reconcile_groups_by_reason(db):
    s = m.Strategy(name="S93 FVG Scalp")
    db.add(s)
    db.flush()
    w = datetime(2026, 7, 10, 10, 0)
    _sig(db, s, "PLACED", None, w)
    _sig(db, s, "REJECTED", "entry_drift", w)
    _sig(db, s, "REJECTED", "entry_drift", w)
    _sig(db, s, "REJECTED", "sl_too_tight", w)
    out = reconcile(db, ["S93 FVG Scalp"],
                    datetime(2026, 7, 1), datetime(2026, 7, 31),
                    sim_counts={"S93 FVG Scalp": 3})
    blk = out["S93 FVG Scalp"]
    assert blk["live_generated"] == 4
    assert blk["live_placed"] == 1
    assert blk["rejected"] == {"entry_drift": 2, "sl_too_tight": 1}
    assert blk["sim_trades"] == 3


def test_reconcile_unavailable_when_empty(db):
    s = m.Strategy(name="S99 MSS FVG")
    db.add(s)
    db.flush()
    out = reconcile(db, ["S99 MSS FVG"],
                    datetime(2026, 7, 1), datetime(2026, 7, 31), sim_counts={})
    assert out["S99 MSS FVG"] == "unavailable"
