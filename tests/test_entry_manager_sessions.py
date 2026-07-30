# tests/test_entry_manager_sessions.py
"""opt15 task4 - entry_manager session consolidation + drift fail-mode + SQL
duplicate-age filter. All offline (mocked MetaAPI/LTP, SQLite).

Pins the three requirements of Task 4:

  1. session consolidation - place_entry threads ONE session through
     context/gates/persist (one commit at the end of persist). Only the two
     audit helpers (_log_signal_fired / _update_signal_status) may keep their
     own short sessions so the FIRED/REJECTED row survives a rollback. A gated
     dry place_entry therefore opens <= 4 sessions total (was ~9).
  2. drift fail-mode - ENTRY_DRIFT_FAIL_MODE=open (default, current behavior)
     admits an entry when no live price is available; =closed rejects it with
     rejection_reason="entry_drift_noprice".
  3. duplicate-age filter moved into SQL - the SQL cutoff reproduces the exact
     result of the old Python-side age filter (parity on boundary rows).

The consolidated session stays open across the two audit sessions, so the
place_entry tests run against a file-backed SQLite in WAL mode: every Session()
gets its own real connection (as in production Postgres), and WAL lets the audit
writer commit while the consolidated reader holds its snapshot. The pure-helper
and parity tests only ever hold one session at a time, so they use a plain
in-memory StaticPool DB.
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# entry_manager.py lives in strategies/ and does `from shared...` /
# `from strategy...`, so put that dir on the path first (mirrors test_entry_gates).
_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies")
)
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from strategy import entry_manager as em  # noqa: E402

SYMBOL = "XAU_USD"


# ---------------------------------------------------------------------------
# Fake broker client - place_entry only needs place_market_order (returns a
# broker position id) and get_position_fill (None -> book the signal price).
# ---------------------------------------------------------------------------
class _FakeClient:
    def __init__(self):
        self.orders = 0

    def place_market_order(self, **kwargs):
        self.orders += 1
        return "POS-TEST-1"

    def get_position_fill(self, position_id, **kwargs):
        return None


# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def memdb(monkeypatch):
    """Shared in-memory DB (StaticPool). One session open at a time only."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    em.Position.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(em, "Session", Session)
    yield SimpleNamespace(engine=engine, Session=Session)
    engine.dispose()


@pytest.fixture()
def filedb(monkeypatch):
    """File-backed SQLite in WAL mode: every Session() gets its own connection,
    so the consolidated reader and the short audit writers coexist (as they do
    against Postgres in production)."""
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def _wal(dbapi_conn, _rec):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    em.Position.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(em, "Session", Session)
    yield SimpleNamespace(engine=engine, Session=Session)
    engine.dispose()
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------
def _seed_context(Session, *, entry_qty="0.10"):
    """One CurrencyPair + active Strategy + deployed UserStrategy so
    _get_context(variation=None) resolves. Returns the user_broker_id."""
    ub = uuid.uuid4()
    s = Session()
    cp = em.CurrencyPair(id=uuid.uuid4(), symbol=SYMBOL, ltp="0.00")
    s.add(cp)
    strat = em.Strategy(id=uuid.uuid4(), name="Test Strat", is_active=True,
                        currencypair_id=cp.id, entry_quantity=Decimal(entry_qty))
    s.add(strat)
    us = em.UserStrategy(id=uuid.uuid4(), name="t", is_active=True,
                         deployed=True, multiplyer=1,
                         strategy_id=strat.id, user_broker_id=ub)
    s.add(us)
    s.commit()
    s.close()
    return SimpleNamespace(user_broker_id=ub)


def _open_signal():
    # 4pt stop (> MIN_SL_DIST 1.5), no trailing / max_hold.
    return SimpleNamespace(side="BUY", entry_price=4000.0, stop_loss=3996.0,
                           take_profit=4008.0, reason="test")


def _pass_the_gates(monkeypatch, *, ltp=4000.0):
    """Neutralise gate randomness so place_entry reaches persist: empty news
    window, a live price equal to the signal (zero drift), a fake broker."""
    monkeypatch.setattr(em, "_BLACKOUT_WINDOWS", [])
    monkeypatch.setattr(em, "fetch_latest_ltp", lambda symbol: ltp)
    fake = _FakeClient()
    monkeypatch.setattr(em, "client_for_broker", lambda sess, ub: fake)
    return fake


# ---------------------------------------------------------------------------
# (1) session consolidation
# ---------------------------------------------------------------------------
def test_place_entry_consolidates_sessions(filedb, monkeypatch):
    _seed_context(filedb.Session)
    _pass_the_gates(monkeypatch)

    real = filedb.Session
    calls = {"n": 0}

    def _counting():
        calls["n"] += 1
        return real()

    monkeypatch.setattr(em, "Session", _counting)

    ok = em.place_entry(_open_signal(), symbol=SYMBOL, variation=None,
                        max_concurrent=1)

    assert ok is True, "gated dry place_entry should place"
    assert calls["n"] <= 4, (
        f"place_entry opened {calls['n']} sessions; expected <= 4 after "
        "consolidating context/gates/persist onto one session"
    )


def test_place_entry_persists_one_position(filedb, monkeypatch):
    _seed_context(filedb.Session)
    _pass_the_gates(monkeypatch)

    ok = em.place_entry(_open_signal(), symbol=SYMBOL, variation=None,
                        max_concurrent=1)
    assert ok is True

    s = filedb.Session()
    try:
        positions = s.query(em.Position).all()
        assert len(positions) == 1
        assert float(positions[0].quantity) == pytest.approx(0.10)
        # ENTRY order + SL + TP triggers written on the same committed session.
        assert s.query(em.Order).filter_by(condition="ENTRY").count() == 1
        assert s.query(em.Trigger).count() == 2
        sig = s.query(em.StrategySignal).one()
        assert sig.status == "PLACED"
        assert sig.position_id == positions[0].id
    finally:
        s.close()


# ---------------------------------------------------------------------------
# (2) drift fail-mode
# ---------------------------------------------------------------------------
def test_drift_fail_open_admits_missing_price(monkeypatch):
    """Default 'open' keeps the exact current behavior: (False, 'no_ltp')."""
    monkeypatch.setattr(em, "ENTRY_DRIFT_FAIL_MODE", "open")
    monkeypatch.setattr(em, "fetch_latest_ltp", lambda symbol: None)
    exceeded, detail = em._entry_drift_exceeded("BUY", 4000.0, 3996.0, SYMBOL)
    assert exceeded is False
    assert detail == "no_ltp"


def test_drift_fail_closed_rejects_missing_price(monkeypatch):
    monkeypatch.setattr(em, "ENTRY_DRIFT_FAIL_MODE", "closed")
    monkeypatch.setattr(em, "fetch_latest_ltp", lambda symbol: None)
    exceeded, detail = em._entry_drift_exceeded("BUY", 4000.0, 3996.0, SYMBOL)
    assert exceeded is True
    assert detail == "entry_drift_noprice"


def test_drift_fail_closed_still_admits_when_price_ok(monkeypatch):
    """closed only bites on MISSING price; a good price with no adverse drift
    still admits (no behavior change to the normal path)."""
    monkeypatch.setattr(em, "ENTRY_DRIFT_FAIL_MODE", "closed")
    monkeypatch.setattr(em, "fetch_latest_ltp", lambda symbol: 4000.0)
    exceeded, _ = em._entry_drift_exceeded("BUY", 4000.0, 3996.0, SYMBOL)
    assert exceeded is False


def test_place_entry_drift_closed_rejection_reason(filedb, monkeypatch):
    """End-to-end: closed mode + no price -> REJECTED with the exact
    rejection_reason the brief specifies."""
    _seed_context(filedb.Session)
    monkeypatch.setattr(em, "_BLACKOUT_WINDOWS", [])
    monkeypatch.setattr(em, "ENTRY_DRIFT_FAIL_MODE", "closed")
    monkeypatch.setattr(em, "fetch_latest_ltp", lambda symbol: None)
    # broker must never be reached (drift gate precedes it)
    monkeypatch.setattr(em, "client_for_broker",
                        lambda *a, **k: pytest.fail("broker reached past drift gate"))

    ok = em.place_entry(_open_signal(), symbol=SYMBOL, variation=None)
    assert ok is False

    s = filedb.Session()
    try:
        sig = s.query(em.StrategySignal).one()
        assert sig.status == "REJECTED"
        assert sig.rejection_reason == "entry_drift_noprice"
    finally:
        s.close()


def test_place_entry_drift_open_admits_missing_price(filedb, monkeypatch):
    """Parity: with default 'open', a missing price does NOT block the entry."""
    _seed_context(filedb.Session)
    monkeypatch.setattr(em, "_BLACKOUT_WINDOWS", [])
    monkeypatch.setattr(em, "ENTRY_DRIFT_FAIL_MODE", "open")
    monkeypatch.setattr(em, "fetch_latest_ltp", lambda symbol: None)
    monkeypatch.setattr(em, "client_for_broker", lambda sess, ub: _FakeClient())

    ok = em.place_entry(_open_signal(), symbol=SYMBOL, variation=None)
    assert ok is True


# ---------------------------------------------------------------------------
# (3) duplicate-age filter parity: SQL cutoff == old Python-side age filter
# ---------------------------------------------------------------------------
def _legacy_duplicate_open_same_side(user_broker_id, symbol, side, entry_price):
    """VERBATIM copy of the pre-opt15 Python-side implementation: fetch every
    open same-broker/symbol position, then filter the age in Python. The new
    SQL-side filter must return identical results to this."""
    if em.DUP_GUARD_MIN <= 0:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=em.DUP_GUARD_MIN)
    sess = em.Session()
    try:
        rows = (
            sess.query(em.Position)
            .join(em.UserStrategy,
                  em.Position.user_strategy_id == em.UserStrategy.id)
            .filter(
                em.UserStrategy.user_broker_id == user_broker_id,
                em.Position.symbol == symbol,
                em.Position.quantity > 0,
            )
            .all()
        )
        for p in rows:
            created = p.created_at
            if created is None:
                continue
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created < cutoff:
                continue
            p_side = "BUY" if float(p.avg_buy_price or 0) > 0 else "SELL"
            if p_side != side:
                continue
            p_entry = float(p.avg_buy_price if p_side == "BUY"
                            else p.avg_sell_price)
            if abs(p_entry - float(entry_price)) <= em.DUP_GUARD_PROX_PTS:
                return True
        return False
    finally:
        sess.close()


def _seed_position(Session, ub, *, side="BUY", entry=4108.0, age_min=1.0):
    s = Session()
    us = em.UserStrategy(id=uuid.uuid4(), name="t", deployed=True,
                         is_active=True, user_broker_id=ub)
    s.add(us)
    s.add(em.Position(
        id=uuid.uuid4(), symbol=SYMBOL, user_strategy_id=us.id,
        quantity=Decimal("0.10"),
        avg_buy_price=Decimal(str(entry)) if side == "BUY" else Decimal("0"),
        avg_sell_price=Decimal("0") if side == "BUY" else Decimal(str(entry)),
        profit_loss=Decimal("0"),
        created_at=datetime.now(timezone.utc) - timedelta(minutes=age_min),
    ))
    s.commit()
    s.close()


def test_dup_sql_filter_parity_inside_and_outside(memdb, monkeypatch):
    monkeypatch.setattr(em, "DUP_GUARD_MIN", 15.0)
    monkeypatch.setattr(em, "DUP_GUARD_PROX_PTS", 2.0)
    ub = uuid.uuid4()
    # boundary rows: one INSIDE the 15-min cutoff, one OUTSIDE it.
    _seed_position(memdb.Session, ub, side="BUY", entry=4108.0, age_min=1)
    _seed_position(memdb.Session, ub, side="BUY", entry=4108.2, age_min=40)

    for probe in (4108.5, 4120.0):
        new = em._duplicate_open_same_side(ub, SYMBOL, "BUY", probe)
        old = _legacy_duplicate_open_same_side(ub, SYMBOL, "BUY", probe)
        assert new == old, f"SQL vs Python age-filter disagree at probe={probe}"
    # the near probe matches only the INSIDE row -> True; far probe -> False.
    assert em._duplicate_open_same_side(ub, SYMBOL, "BUY", 4108.5) is True
    assert em._duplicate_open_same_side(ub, SYMBOL, "BUY", 4120.0) is False


def test_dup_sql_filter_parity_only_stale(memdb, monkeypatch):
    """Only an OUTSIDE-cutoff position exists: both filters agree it's stale."""
    monkeypatch.setattr(em, "DUP_GUARD_MIN", 15.0)
    monkeypatch.setattr(em, "DUP_GUARD_PROX_PTS", 2.0)
    ub = uuid.uuid4()
    _seed_position(memdb.Session, ub, side="BUY", entry=4108.0, age_min=40)

    new = em._duplicate_open_same_side(ub, SYMBOL, "BUY", 4108.0)
    old = _legacy_duplicate_open_same_side(ub, SYMBOL, "BUY", 4108.0)
    assert new == old is False


def test_dup_sql_filter_passes_session_through(memdb, monkeypatch):
    """When place_entry threads its consolidated session in, the helper reuses
    it (no new Session()) yet returns the same verdict."""
    monkeypatch.setattr(em, "DUP_GUARD_MIN", 15.0)
    monkeypatch.setattr(em, "DUP_GUARD_PROX_PTS", 2.0)
    ub = uuid.uuid4()
    _seed_position(memdb.Session, ub, side="BUY", entry=4108.0, age_min=1)

    shared = memdb.Session()
    try:
        opened = {"n": 0}
        real = memdb.Session

        def _counting():
            opened["n"] += 1
            return real()

        monkeypatch.setattr(em, "Session", _counting)
        verdict = em._duplicate_open_same_side(ub, SYMBOL, "BUY", 4108.5,
                                               sess=shared)
        assert verdict is True
        assert opened["n"] == 0, "helper must reuse the passed session"
    finally:
        shared.close()
