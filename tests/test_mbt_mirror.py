"""Task 4 (Manager Backtest tab plan): SQLAlchemy mirror of ManagerBacktestRun.

Verifies the mirror (a) round-trips a job row on SQLite, (b) exposes exactly the
Django model's columns, and (c) is byte-identical between the two full mirror
trees (strategies/shared vs strategy_manager/shared). position_manager's
trimmed mirror deliberately does not carry this class.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# The mirror does `from shared...`-style relative living inside strategies/,
# so put that dir on the path first (mirrors the sibling DB tests).
_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies")
)
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from shared.models import ManagerBacktestRun  # noqa: E402

REPO = Path(__file__).resolve().parents[1]

EXPECTED_COLUMNS = {
    "id", "created_at", "modified_at",
    "label", "status", "progress_pct", "phase",
    "period_start", "period_end", "params", "result", "error",
    "started_at", "finished_at", "requested_by_id",
}


def _engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def test_mirror_roundtrip_and_columns():
    engine = _engine()
    ManagerBacktestRun.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as s:
        s.add(ManagerBacktestRun(
            label="audit_2026-01-01_2026-07-01",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 7, 1),
            params={"lots": 0.02},
        ))
        s.commit()
        row = s.query(ManagerBacktestRun).one()
        assert row.status == "PENDING"
        assert row.progress_pct == 0.0
        assert row.params == {"lots": 0.02}
        assert row.result is None

    cols = {c.name for c in ManagerBacktestRun.__table__.columns}
    assert cols == EXPECTED_COLUMNS
    assert ManagerBacktestRun.__tablename__ == "apis_managerbacktestrun"


def _class_block(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    m = re.search(
        r"^class ManagerBacktestRun\(BaseModel\):.*?(?=^class |\Z)",
        text, re.M | re.S,
    )
    assert m, f"ManagerBacktestRun not found in {path}"
    return m.group(0)


def test_mirror_trees_identical():
    a = _class_block(REPO / "strategies" / "shared" / "models.py")
    b = _class_block(REPO / "strategy_manager" / "shared" / "models.py")
    assert a == b, "ManagerBacktestRun mirror diverged between trees"
