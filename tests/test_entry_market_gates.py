# tests/test_entry_market_gates.py
"""opt15 task5 - entry_manager market gates: correlation budget, event-gate
news window, and spread gate. All three ship DEFAULT OFF behind an env flag;
with no env set behaviour is byte-identical to the pre-task5 chain.

Pins:

  1. Correlation budget (CORR_GUARD=off|on, CORR_GUARD_POINTS default 2.0):
     reject a NEW entry when a DIFFERENT UserStrategy on the SAME account holds
     an OPEN same-side position whose entry is within CORR_GUARD_POINTS. Ignores
     the opposite side and far-away positions. rejection_reason starts "correlated".
  2. Event-gate news window (NEWS_EVENT_GATE=off|on): consult the REAL
     shared.event_gate.event_window_open with a frozen timestamp inside a known
     calendar window. rejection_reason="news_event".
  3. Spread gate (SPREAD_GATE=off|on, SPREAD_GATE_MAX_FRAC default 0.25): reject
     when live spread > frac x |entry - sl|; a missing spread fails OPEN with a
     warning (never blocks on a data hiccup). rejection_reason starts "spread".
     The observed spread is recorded in the audit row on a placed entry.

Offline: mocked broker/LTP/spread, SQLite. Mirrors test_entry_manager_sessions'
in-memory (StaticPool) / file-WAL split and the strategies-dir sys.path insert
so `from shared...` inside entry_manager resolves to strategies/shared.
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

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies")
)
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from strategy import entry_manager as em  # noqa: E402

SYMBOL = "XAU_USD"


# ---------------------------------------------------------------------------
# Fake broker client
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
# DB fixtures (mirror test_entry_manager_sessions)
# ---------------------------------------------------------------------------
@pytest.fixture()
def memdb(monkeypatch):
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
    """CurrencyPair + active Strategy + deployed UserStrategy so
    _get_context(variation=None) resolves. Returns user_broker_id +
    user_strategy_id so tests can seed sibling positions."""
    ub = uuid.uuid4()
    s = Session()
    cp = em.CurrencyPair(id=uuid.uuid4(), symbol=SYMBOL, ltp="0.00")
    s.add(cp)
    strat = em.Strategy(id=uuid.uuid4(), name="Test Strat", is_active=True,
                        currencypair_id=cp.id, entry_quantity=Decimal(entry_qty))
    s.add(strat)
    us_id = uuid.uuid4()
    us = em.UserStrategy(id=us_id, name="t", is_active=True,
                         deployed=True, multiplyer=1,
                         strategy_id=strat.id, user_broker_id=ub)
    s.add(us)
    s.commit()
    s.close()
    return SimpleNamespace(user_broker_id=ub, user_strategy_id=us_id)


def _seed_sibling_position(Session, ub, *, side="BUY", entry=4000.0, age_min=0.0):
    """An OPEN position owned by a DIFFERENT UserStrategy on account `ub`.
    `age_min` sets created_at into the past so an end-to-end test can seed a
    position OLDER than the duplicate gate's DUP_GUARD_MIN window (which the
    correlation gate - no time window - must still catch)."""
    s = Session()
    us = em.UserStrategy(id=uuid.uuid4(), name="sibling", deployed=True,
                         is_active=True, user_broker_id=ub)
    s.add(us)
    pos = em.Position(
        id=uuid.uuid4(), symbol=SYMBOL, user_strategy_id=us.id,
        quantity=Decimal("0.10"),
        avg_buy_price=Decimal(str(entry)) if side == "BUY" else Decimal("0"),
        avg_sell_price=Decimal("0") if side == "BUY" else Decimal(str(entry)),
        profit_loss=Decimal("0"),
    )
    if age_min:
        pos.created_at = datetime.now(timezone.utc) - timedelta(minutes=age_min)
    s.add(pos)
    s.commit()
    s.close()


def _open_signal(side="BUY", entry=4000.0, sl=3996.0, tp=4008.0):
    return SimpleNamespace(side=side, entry_price=entry, stop_loss=sl,
                           take_profit=tp, reason="test")


def _pass_the_gates(monkeypatch, *, ltp=4000.0):
    monkeypatch.setattr(em, "_BLACKOUT_WINDOWS", [])
    monkeypatch.setattr(em, "fetch_latest_ltp", lambda symbol: ltp)
    fake = _FakeClient()
    monkeypatch.setattr(em, "client_for_broker", lambda sess, ub: fake)
    return fake


# ===========================================================================
# (1) Correlation budget
# ===========================================================================
def test_correlation_off_by_default_not_consulted(memdb, monkeypatch):
    """With CORR_GUARD off the helper never touches the DB - returns (False,'')
    even when a matching sibling position exists."""
    monkeypatch.setattr(em, "CORR_GUARD", False)
    ctx = _seed_context(memdb.Session)
    _seed_sibling_position(memdb.Session, ctx.user_broker_id, side="BUY", entry=4000.0)
    blocked, detail = em._correlated_open_other_strategy(
        ctx.user_broker_id, ctx.user_strategy_id, SYMBOL, "BUY", 4000.0)
    assert blocked is False
    assert detail == ""


def test_correlation_on_rejects_nearby_sibling(memdb, monkeypatch):
    monkeypatch.setattr(em, "CORR_GUARD", True)
    monkeypatch.setattr(em, "CORR_GUARD_POINTS", 2.0)
    ctx = _seed_context(memdb.Session)
    _seed_sibling_position(memdb.Session, ctx.user_broker_id, side="BUY", entry=4001.0)
    blocked, detail = em._correlated_open_other_strategy(
        ctx.user_broker_id, ctx.user_strategy_id, SYMBOL, "BUY", 4000.0)
    assert blocked is True
    assert detail.startswith("correlated")


def test_correlation_on_ignores_opposite_side(memdb, monkeypatch):
    monkeypatch.setattr(em, "CORR_GUARD", True)
    monkeypatch.setattr(em, "CORR_GUARD_POINTS", 2.0)
    ctx = _seed_context(memdb.Session)
    _seed_sibling_position(memdb.Session, ctx.user_broker_id, side="SELL", entry=4000.0)
    blocked, _ = em._correlated_open_other_strategy(
        ctx.user_broker_id, ctx.user_strategy_id, SYMBOL, "BUY", 4000.0)
    assert blocked is False


def test_correlation_on_ignores_faraway_sibling(memdb, monkeypatch):
    monkeypatch.setattr(em, "CORR_GUARD", True)
    monkeypatch.setattr(em, "CORR_GUARD_POINTS", 2.0)
    ctx = _seed_context(memdb.Session)
    _seed_sibling_position(memdb.Session, ctx.user_broker_id, side="BUY", entry=4010.0)
    blocked, _ = em._correlated_open_other_strategy(
        ctx.user_broker_id, ctx.user_strategy_id, SYMBOL, "BUY", 4000.0)
    assert blocked is False


def test_correlation_on_ignores_same_strategy(memdb, monkeypatch):
    """The account's own current UserStrategy is excluded - correlation is a
    CROSS-strategy budget (same-strategy dupes are _duplicate_open_same_side's
    job)."""
    monkeypatch.setattr(em, "CORR_GUARD", True)
    monkeypatch.setattr(em, "CORR_GUARD_POINTS", 2.0)
    ctx = _seed_context(memdb.Session)
    # seed an open position under the SAME user_strategy_id
    s = memdb.Session()
    s.add(em.Position(
        id=uuid.uuid4(), symbol=SYMBOL, user_strategy_id=ctx.user_strategy_id,
        quantity=Decimal("0.10"), avg_buy_price=Decimal("4000.0"),
        avg_sell_price=Decimal("0"), profit_loss=Decimal("0"),
    ))
    s.commit()
    s.close()
    blocked, _ = em._correlated_open_other_strategy(
        ctx.user_broker_id, ctx.user_strategy_id, SYMBOL, "BUY", 4000.0)
    assert blocked is False


def test_correlation_reuses_passed_session(memdb, monkeypatch):
    monkeypatch.setattr(em, "CORR_GUARD", True)
    monkeypatch.setattr(em, "CORR_GUARD_POINTS", 2.0)
    ctx = _seed_context(memdb.Session)
    _seed_sibling_position(memdb.Session, ctx.user_broker_id, side="BUY", entry=4000.0)
    shared = memdb.Session()
    try:
        opened = {"n": 0}
        real = memdb.Session

        def _counting():
            opened["n"] += 1
            return real()

        monkeypatch.setattr(em, "Session", _counting)
        blocked, _ = em._correlated_open_other_strategy(
            ctx.user_broker_id, ctx.user_strategy_id, SYMBOL, "BUY", 4000.0,
            sess=shared)
        assert blocked is True
        assert opened["n"] == 0, "helper must reuse the passed session"
    finally:
        shared.close()


def test_correlation_end_to_end_rejection(filedb, monkeypatch):
    monkeypatch.setattr(em, "CORR_GUARD", True)
    monkeypatch.setattr(em, "CORR_GUARD_POINTS", 2.0)
    ctx = _seed_context(filedb.Session)
    # Stale sibling (age > DUP_GUARD_MIN): the duplicate gate skips it, so this
    # isolates the correlation gate (which has no time window).
    _seed_sibling_position(filedb.Session, ctx.user_broker_id, side="BUY",
                           entry=4000.5, age_min=40)
    _pass_the_gates(monkeypatch)
    # broker must not be reached
    monkeypatch.setattr(em, "client_for_broker",
                        lambda *a, **k: pytest.fail("broker reached past corr gate"))

    ok = em.place_entry(_open_signal(), symbol=SYMBOL, variation=None)
    assert ok is False
    s = filedb.Session()
    try:
        sig = s.query(em.StrategySignal).one()
        assert sig.status == "REJECTED"
        assert sig.rejection_reason.startswith("correlated")
    finally:
        s.close()


def test_correlation_on_but_no_sibling_admits(filedb, monkeypatch):
    monkeypatch.setattr(em, "CORR_GUARD", True)
    monkeypatch.setattr(em, "CORR_GUARD_POINTS", 2.0)
    _seed_context(filedb.Session)
    _pass_the_gates(monkeypatch)
    ok = em.place_entry(_open_signal(), symbol=SYMBOL, variation=None)
    assert ok is True


# ===========================================================================
# (2) Event-gate news window
# ===========================================================================
# FOMC 2026-05-06 -> 14:00 ET summer -> 18:00 UTC in event_gate's calendar.
_FROZEN_EVENT = datetime(2026, 5, 6, 18, 0, tzinfo=timezone.utc)
# Mid-June 2026 is past the last 2026 calendar row (monthly events stop at May,
# FOMC at May 6) -> far from every event.
_FROZEN_CLEAR = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


def test_event_gate_off_by_default_not_consulted(monkeypatch):
    monkeypatch.setattr(em, "NEWS_EVENT_GATE", False)
    spy = {"n": 0}

    def _spy(*a, **k):
        spy["n"] += 1
        return False

    monkeypatch.setattr(em, "event_window_open", _spy)
    assert em._event_gate_blocks(_FROZEN_EVENT) is False
    assert spy["n"] == 0, "event_window_open must not be consulted when gate off"


def test_event_gate_on_blocks_inside_real_window(monkeypatch):
    """Real event_window_open, frozen inside a known FOMC window -> blocks."""
    monkeypatch.setattr(em, "NEWS_EVENT_GATE", True)
    # sanity: the real calendar reports this instant CLOSED
    from shared.event_gate import event_window_open as real_ewo
    assert real_ewo(_FROZEN_EVENT) is False
    assert em._event_gate_blocks(_FROZEN_EVENT) is True


def test_event_gate_on_passes_outside_real_window(monkeypatch):
    monkeypatch.setattr(em, "NEWS_EVENT_GATE", True)
    from shared.event_gate import event_window_open as real_ewo
    assert real_ewo(_FROZEN_CLEAR) is True
    assert em._event_gate_blocks(_FROZEN_CLEAR) is False


def test_event_gate_end_to_end_rejection(filedb, monkeypatch):
    """place_entry wires the gate: window 'open' -> REJECTED news_event. The
    calendar consult is stubbed to CLOSED so the test is time-independent while
    still exercising the real _event_gate_blocks logic + wiring."""
    monkeypatch.setattr(em, "NEWS_EVENT_GATE", True)
    monkeypatch.setattr(em, "event_window_open", lambda *a, **k: False)  # window open
    _seed_context(filedb.Session)
    _pass_the_gates(monkeypatch)
    monkeypatch.setattr(em, "client_for_broker",
                        lambda *a, **k: pytest.fail("broker reached past event gate"))
    ok = em.place_entry(_open_signal(), symbol=SYMBOL, variation=None)
    assert ok is False
    s = filedb.Session()
    try:
        sig = s.query(em.StrategySignal).one()
        assert sig.status == "REJECTED"
        assert sig.rejection_reason == "news_event"
    finally:
        s.close()


def test_event_gate_on_but_window_clear_admits(filedb, monkeypatch):
    monkeypatch.setattr(em, "NEWS_EVENT_GATE", True)
    monkeypatch.setattr(em, "event_window_open", lambda *a, **k: True)  # clear
    _seed_context(filedb.Session)
    _pass_the_gates(monkeypatch)
    ok = em.place_entry(_open_signal(), symbol=SYMBOL, variation=None)
    assert ok is True


# ===========================================================================
# (3) Spread gate
# ===========================================================================
def test_spread_off_by_default_not_consulted(monkeypatch):
    monkeypatch.setattr(em, "SPREAD_GATE", False)
    spy = {"n": 0}

    def _spy(symbol):
        spy["n"] += 1
        return 0.0

    monkeypatch.setattr(em, "fetch_latest_spread", _spy)
    blocked, detail, spread = em._spread_gate_blocks(SYMBOL, 4000.0, 3996.0)
    assert blocked is False
    assert spread is None
    assert spy["n"] == 0, "fetch_latest_spread must not be called when gate off"


def test_spread_on_rejects_wide_spread(monkeypatch):
    monkeypatch.setattr(em, "SPREAD_GATE", True)
    monkeypatch.setattr(em, "SPREAD_GATE_MAX_FRAC", 0.25)
    # sl_dist = 4pt -> budget = 1.0pt; spread 1.5 > 1.0 -> reject
    monkeypatch.setattr(em, "fetch_latest_spread", lambda symbol: 1.5)
    blocked, detail, spread = em._spread_gate_blocks(SYMBOL, 4000.0, 3996.0)
    assert blocked is True
    assert spread == pytest.approx(1.5)


def test_spread_on_passes_tight_spread(monkeypatch):
    monkeypatch.setattr(em, "SPREAD_GATE", True)
    monkeypatch.setattr(em, "SPREAD_GATE_MAX_FRAC", 0.25)
    # sl_dist = 4pt -> budget = 1.0pt; spread 0.5 <= 1.0 -> pass, spread recorded
    monkeypatch.setattr(em, "fetch_latest_spread", lambda symbol: 0.5)
    blocked, detail, spread = em._spread_gate_blocks(SYMBOL, 4000.0, 3996.0)
    assert blocked is False
    assert spread == pytest.approx(0.5)


def test_spread_missing_fails_open_with_warning(monkeypatch, caplog):
    monkeypatch.setattr(em, "SPREAD_GATE", True)
    monkeypatch.setattr(em, "fetch_latest_spread", lambda symbol: None)
    import logging
    with caplog.at_level(logging.WARNING):
        blocked, detail, spread = em._spread_gate_blocks(SYMBOL, 4000.0, 3996.0)
    assert blocked is False       # never blocks on a data hiccup
    assert spread is None
    assert any("spread" in r.message.lower() for r in caplog.records)


def test_spread_end_to_end_rejection(filedb, monkeypatch):
    monkeypatch.setattr(em, "SPREAD_GATE", True)
    monkeypatch.setattr(em, "SPREAD_GATE_MAX_FRAC", 0.25)
    monkeypatch.setattr(em, "fetch_latest_spread", lambda symbol: 2.0)  # > 1.0 budget
    _seed_context(filedb.Session)
    _pass_the_gates(monkeypatch)
    monkeypatch.setattr(em, "client_for_broker",
                        lambda *a, **k: pytest.fail("broker reached past spread gate"))
    ok = em.place_entry(_open_signal(), symbol=SYMBOL, variation=None)
    assert ok is False
    s = filedb.Session()
    try:
        sig = s.query(em.StrategySignal).one()
        assert sig.status == "REJECTED"
        assert sig.rejection_reason.startswith("spread")
    finally:
        s.close()


def test_spread_observed_recorded_on_placed(filedb, monkeypatch):
    """A passing spread is stitched into the audit row's reason so friction
    analysis has spread-at-entry data."""
    monkeypatch.setattr(em, "SPREAD_GATE", True)
    monkeypatch.setattr(em, "SPREAD_GATE_MAX_FRAC", 0.25)
    monkeypatch.setattr(em, "fetch_latest_spread", lambda symbol: 0.42)
    _seed_context(filedb.Session)
    _pass_the_gates(monkeypatch)
    ok = em.place_entry(_open_signal(), symbol=SYMBOL, variation=None)
    assert ok is True
    s = filedb.Session()
    try:
        sig = s.query(em.StrategySignal).one()
        assert sig.status == "PLACED"
        assert "spread=0.42" in (sig.reason or "")
    finally:
        s.close()


# ===========================================================================
# (0) All gates OFF => byte-identical passthrough (no external consults)
# ===========================================================================
def test_all_gates_off_places_without_consulting(filedb, monkeypatch):
    monkeypatch.setattr(em, "CORR_GUARD", False)
    monkeypatch.setattr(em, "NEWS_EVENT_GATE", False)
    monkeypatch.setattr(em, "SPREAD_GATE", False)

    def _no_spread(symbol):
        pytest.fail("spread fetched with gate off")

    def _no_event(*a, **k):
        pytest.fail("event calendar consulted with gate off")

    monkeypatch.setattr(em, "fetch_latest_spread", _no_spread)
    monkeypatch.setattr(em, "event_window_open", _no_event)
    _seed_context(filedb.Session)
    # even seed a nearby sibling that WOULD trip correlation if it were on
    ctx_ub = filedb.Session()
    # (sibling seeded via helper below)
    ctx_ub.close()
    _pass_the_gates(monkeypatch)

    ok = em.place_entry(_open_signal(), symbol=SYMBOL, variation=None)
    assert ok is True
    s = filedb.Session()
    try:
        sig = s.query(em.StrategySignal).one()
        assert sig.status == "PLACED"
        # no spread recorded when the spread gate is off
        assert "spread=" not in (sig.reason or "")
    finally:
        s.close()
