"""Task 9 (Manager Backtest plan): worker job lifecycle (SQLite, offline)."""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies"))
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from audit_worker import worker  # noqa: E402
from backtest.manager_sim_engine import SimResult  # noqa: E402
from shared.models import ManagerBacktestRun  # noqa: E402


@pytest.fixture()
def db(monkeypatch, tmp_path):
    engine = create_engine("sqlite://",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    ManagerBacktestRun.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(worker, "PROGRESS_MIN_INTERVAL_SEC", 0.0)
    with Session() as s:
        yield s


def _mk_run(s, label="r", status="PENDING", created_shift_min=0, params=None):
    run = ManagerBacktestRun(
        label=label, status=status,
        period_start=datetime(2026, 6, 2).date(),
        period_end=datetime(2026, 6, 5).date(),
        params=params or {"roster_snapshot": [], "include_ungated": False},
        created_at=datetime(2026, 6, 10) + timedelta(minutes=created_shift_min),
    )
    s.add(run)
    s.commit()
    return run


def test_claim_oldest_pending(db):
    newer = _mk_run(db, "newer", created_shift_min=10)
    older = _mk_run(db, "older", created_shift_min=0)
    got = worker.claim_next(db)
    assert got.id == older.id and got.status == "RUNNING"
    assert got.started_at is not None
    assert worker.claim_next(db).id == newer.id
    assert worker.claim_next(db) is None


def test_stale_running_failed_on_startup(db):
    stale = _mk_run(db, status="RUNNING")
    done = _mk_run(db, status="DONE")
    assert worker.fail_stale_running(db) == 1
    db.refresh(stale)
    assert stale.status == "FAILED" and stale.error == "worker restarted"
    db.refresh(done)
    assert done.status == "DONE"


def _fake_frames():
    df = pd.DataFrame({"time": pd.to_datetime([], utc=True), "open": [],
                       "high": [], "low": [], "close": []})
    return {tf: df for tf in ("1m", "5m", "15m", "1h", "4h", "1d")}


def test_job_exception_lands_ascii_error(db, monkeypatch, tmp_path):
    from audit_worker import bars

    def boom(*a, **k):
        raise ValueError("boom → non-ascii")
    monkeypatch.setattr(bars, "ensure_frames", boom)

    run = _mk_run(db)
    claimed = worker.claim_next(db)
    worker.process_run(db, claimed, cache_dir=tmp_path)
    db.refresh(claimed)
    assert claimed.status == "FAILED"
    assert "boom" in claimed.error
    assert claimed.error.isascii()
    assert claimed.finished_at is not None


def test_done_path_writes_result(db, monkeypatch, tmp_path):
    from audit_worker import bars
    import backtest.manager_sim_engine as engine

    monkeypatch.setattr(bars, "ensure_frames", lambda *a, **k: tmp_path)
    monkeypatch.setattr(engine, "load_frames", lambda *a, **k: _fake_frames())
    monkeypatch.setattr(engine, "run_sim", lambda *a, **k: SimResult(
        trades=[], regime_rows=[], kill_trips=[], paused_pct={}))

    run = _mk_run(db)
    claimed = worker.claim_next(db)
    worker.process_run(db, claimed, cache_dir=tmp_path)
    db.refresh(claimed)
    assert claimed.status == "DONE", claimed.error
    assert claimed.progress_pct == 100.0
    assert claimed.result["summary"]["gated"]["trades"] == 0
    assert claimed.result["notes"] == ["empty roster snapshot: nothing to simulate"]
    assert Path(claimed.result["trades_csv"]).exists()


def test_cancel_mid_run(db, monkeypatch, tmp_path):
    from audit_worker import bars
    import backtest.manager_sim_engine as engine

    monkeypatch.setattr(bars, "ensure_frames", lambda *a, **k: tmp_path)
    monkeypatch.setattr(engine, "load_frames", lambda *a, **k: _fake_frames())

    run = _mk_run(db)
    claimed = worker.claim_next(db)

    def cancelling_run_sim(frames, cfg, specs=None, progress_cb=None):
        progress_cb(0.1)                     # RUNNING: passes
        (db.query(ManagerBacktestRun)
           .filter(ManagerBacktestRun.id == claimed.id)
           .update({"status": "CANCELLED"}))
        db.commit()
        progress_cb(0.5)                     # sees CANCELLED -> RunCancelled
        raise AssertionError("must not get past the cancelled progress write")

    monkeypatch.setattr(engine, "run_sim", cancelling_run_sim)
    worker.process_run(db, claimed, cache_dir=tmp_path)
    db.refresh(claimed)
    assert claimed.status == "CANCELLED"
    assert claimed.finished_at is not None
    assert claimed.result is None


def test_no_metaapi_import_static_and_runtime():
    # Static: no audit_worker source may import metaapi.
    pkg = Path(_STRAT_DIR) / "audit_worker"
    for py in pkg.glob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            assert not any("metaapi" in n for n in names), \
                f"{py.name} imports metaapi: {names}"

    # Runtime (clean subprocess): importing every audit_worker module must not
    # pull shared.metaapi_client in transitively.
    code = (
        "import sys; sys.path.insert(0, r'%s');"
        "import audit_worker.bars, audit_worker.roster, audit_worker.results,"
        "audit_worker.s5_resolve, audit_worker.live_deltas, audit_worker.worker;"
        "assert 'shared.metaapi_client' not in sys.modules, 'metaapi leaked'"
    ) % _STRAT_DIR
    proc = subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
