"""Task 9 (Manager Backtest plan): worker job lifecycle (SQLite, offline)."""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
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
from backtest.manager_sim_engine import SimResult, TradeRecord  # noqa: E402
from shared.models import (  # noqa: E402
    CurrencyPair, ManagerBacktestRun, Order, Position, Strategy,
    StrategySignal, User, UserBroker, UserStrategy,
)


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


_IST_SKEW = timedelta(hours=5, minutes=30)


def _seed_live_strategy(s, name):
    user = User(email=f"{uuid.uuid4()}@t.local")
    cp = CurrencyPair(symbol="XAU_USD", name="XAU_USD")
    strat = Strategy(name=name, apis_currencypair=cp)
    ub = UserBroker(apis_user=user, api_key=str(uuid.uuid4()))
    us = UserStrategy(apis_strategy=strat, apis_userbroker=ub)
    s.add_all([user, cp, strat, ub, us])
    s.flush()
    return strat, us


def _seed_closed_position(s, us, realized, created_utc, lots=0.10):
    # Stored created_at is naive IST wall clock (get_kolkata_time convention),
    # same treatment tests/test_live_deltas.py applies.
    pos = Position(
        symbol="XAU_USD", quantity=Decimal(0),
        realized_profit_loss=Decimal(str(realized)),
        user_strategy_id=us.id,
        created_at=created_utc.replace(tzinfo=None) + _IST_SKEW,
    )
    s.add(pos)
    s.flush()
    # live_summary recovers sizing-invariant points via the ENTRY order's
    # lots -- every seeded live Position needs one for the join to match.
    s.add(Order(position_id=pos.id, condition="ENTRY",
                quantity=Decimal(str(lots))))
    s.flush()


def _seed_signal(s, strat, status, reason, when_utc):
    s.add(StrategySignal(
        symbol="XAU_USD", side="BUY", entry_price=Decimal("2000"),
        status=status, rejection_reason=reason, strategy_id=strat.id,
        signal_at=when_utc + _IST_SKEW))
    s.flush()


def test_comparing_phase_end_to_end_with_seeded_roster(db, monkeypatch, tmp_path):
    """Task 6 review finding: the comparing phase (sim_per_strategy ->
    live_summary -> infer_live_risk_usd -> add_matched_usd -> deltas ->
    reconcile -> assemble_v2) must be driven end-to-end with a non-empty
    roster and real seeded data -- the only prior worker test
    (test_done_path_writes_result) used an empty roster, which short-
    circuits every branch this subsystem added (risk_usd stays None,
    deltas stays {}, the reconciliation loop body never runs)."""
    from audit_worker import bars
    import backtest.manager_sim_engine as engine

    STRAT_NAME = "S93 FVG Scalp"   # resolves via roster._s_code -> s93_fvg_scalp

    # --- live side: Strategy/UserStrategy + 5 losers + 1 winner (closed) ---
    strat, us = _seed_live_strategy(db, STRAT_NAME)
    win = datetime(2026, 6, 3, 8, tzinfo=timezone.utc)
    for i in range(5):
        # -0.05 units @ 0.10 lots -> -0.5 pts, -$5.00 usd each: 5 individual
        # per-trade losses so sizing.infer_live_risk_usd's floor=5 can fire.
        _seed_closed_position(db, us, -0.05, win + timedelta(hours=i))
    _seed_closed_position(db, us, 0.08, win + timedelta(hours=5))   # 1 winner

    # --- live side: signal audit (PLACED + REJECTED) for reconciliation ---
    sig_at = datetime(2026, 6, 3, 9, 0)
    _seed_signal(db, strat, "PLACED", None, sig_at)
    _seed_signal(db, strat, "REJECTED", "entry_drift", sig_at)
    _seed_signal(db, strat, "REJECTED", "entry_drift", sig_at)

    # --- sim side: monkeypatch run_sim to return known TradeRecords ---
    monkeypatch.setattr(bars, "ensure_frames", lambda *a, **k: tmp_path)
    monkeypatch.setattr(engine, "load_frames", lambda *a, **k: _fake_frames())
    t0 = datetime(2026, 6, 3, 10, 0, tzinfo=timezone.utc)
    trades = [
        TradeRecord(strategy=STRAT_NAME, entry_time=t0, side="BUY",
                    entry_px=2000.0, sl=1997.0, tp=2006.0, exit_px=2006.0,
                    exit_time=t0 + timedelta(minutes=30), outcome="TP",
                    pnl_pts=6.0, pnl_usd=6.0 * 0.02 * 100, gate_reason=""),
        TradeRecord(strategy=STRAT_NAME, entry_time=t0 + timedelta(hours=1),
                    side="SELL", entry_px=2010.0, sl=2013.0, tp=2004.0,
                    exit_px=2013.0, exit_time=t0 + timedelta(hours=1, minutes=20),
                    outcome="SL", pnl_pts=-3.0, pnl_usd=-3.0 * 0.02 * 100,
                    gate_reason=""),
    ]
    monkeypatch.setattr(engine, "run_sim", lambda *a, **k: SimResult(
        trades=trades, regime_rows=[], kill_trips=[], paused_pct={}))

    run = _mk_run(db, params={
        "roster_snapshot": [{"name": STRAT_NAME, "policy_key": "always_on",
                             "policy_params": {}}],
        "include_ungated": False,
    })
    claimed = worker.claim_next(db)
    worker.process_run(db, claimed, cache_dir=tmp_path)
    db.refresh(claimed)

    assert claimed.status == "DONE", claimed.error
    result = claimed.result
    blk = result["per_strategy"][STRAT_NAME]

    # sim: the 2 known trades, points-only until risk is inferred
    assert blk["sim"]["points"]["trades"] == 2
    assert blk["sim"]["points"]["pnl_pts"] == pytest.approx(3.0)   # 6 - 3

    # live: 6 closed positions (5 losers + 1 winner), sizing-invariant points
    assert blk["live"]["points"]["trades"] == 6
    assert blk["live"]["usd"]["trades"] == 6

    # 5 individual -$5.00 live losses clear sizing.infer_live_risk_usd's
    # floor=5 -> risk_usd == 5.0 -> add_matched_usd attaches a usd block.
    assert result["live_risk_usd_inferred"] == pytest.approx(5.0)
    assert "usd" in blk["sim"]
    assert "matched-USD omitted" not in " ".join(result["notes"])

    # delta carries both sub-blocks since sim+live both priced usd
    assert blk["delta"] is not None
    assert "points" in blk["delta"] and "usd" in blk["delta"]

    # reconciliation attached from the StrategySignal audit
    recon = blk["reconciliation"]
    assert recon != "unavailable"
    assert recon["live_generated"] == 3
    assert recon["live_placed"] == 1
    assert recon["rejected"] == {"entry_drift": 2}
    assert recon["sim_trades"] == 2


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


def test_container_parity_build_config():
    """Final-review Critical #1 guard: the worker image must be built from the
    REPO ROOT and bake in strategy_manager/ — a ./strategies context cannot
    import strategy_manager.policies and every job would crash-loop."""
    import yaml
    repo = Path(_STRAT_DIR).parent
    compose = yaml.safe_load((repo / "compose.yml").read_text(encoding="utf-8"))
    build = compose["services"]["backtest_worker"]["build"]
    assert build["context"] == "."
    assert build["dockerfile"] == "backtest_worker/Dockerfile"

    dockerfile = (repo / "backtest_worker" / "Dockerfile").read_text(
        encoding="utf-8")
    assert "COPY strategies/ /app/" in dockerfile
    assert "COPY strategy_manager/ /app/strategy_manager/" in dockerfile
    reqs = (repo / "backtest_worker" / "requirements.txt").read_text(
        encoding="utf-8")
    assert "pyarrow" in reqs, "parquet cache needs pyarrow in the image"
