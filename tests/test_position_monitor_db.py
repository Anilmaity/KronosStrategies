# tests/test_position_monitor_db.py
"""opt15 task3 - position_monitor DB efficiency (offline, in-memory SQLite).

Pure hardening: observable trading behavior (which triggers fire, when, with
what realized P&L) must be preserved EXACTLY. These tests pin the four
efficiency requirements plus a behavior-parity anchor:

  1. N+1 kill        - one PENDING-trigger SELECT for ALL open positions.
  2. write throttle  - no pos.ltp/profit_loss UPDATE when the price is unchanged.
  3. CurrencyPair    - the ltp mirror is folded into the tick and throttled too.
  4. one session/tick- a single Session() per _check_triggers tick.
  parity             - a TARGET trigger still fires + realizes P&L as before, and
                       firing is never suppressed by the write throttle.

All state lives in one shared in-memory DB via StaticPool so the monitor's own
Session() (a fresh session per tick) sees the seeded rows.
"""
from __future__ import annotations

import os
import sys
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# position_monitor.py lives in position_manager/ and does `from shared...`, so
# put that dir on the path before importing it (mirrors the sibling tests).
_PM_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "position_manager")
)
if _PM_DIR not in sys.path:
    sys.path.insert(0, _PM_DIR)

import position_monitor as pm  # noqa: E402
from shared.models import (  # noqa: E402
    CurrencyPair, Order, Position, Trigger,
)

SYMBOL = pm.SYMBOL


# ---------------------------------------------------------------------------
# SQL statement counter (event listener on the engine)
# ---------------------------------------------------------------------------
class _SQLCounter:
    def __init__(self, engine):
        self.trigger_selects = 0
        self.position_updates = 0
        self.currency_updates = 0
        event.listen(engine, "before_cursor_execute", self._on)

    def reset(self):
        self.trigger_selects = 0
        self.position_updates = 0
        self.currency_updates = 0

    def _on(self, conn, cursor, statement, params, context, executemany):
        s = statement.lower()
        if s.lstrip().startswith("select") and "apis_trigger" in s:
            self.trigger_selects += 1
        elif s.lstrip().startswith("update") and "apis_position" in s:
            self.position_updates += 1
        elif s.lstrip().startswith("update") and "apis_currencypair" in s:
            self.currency_updates += 1


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def db(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Position.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(pm, "Session", Session)
    # Reset any module-level throttle / retry state between tests.
    if hasattr(pm, "_reset_write_throttle"):
        pm._reset_write_throttle()
    pm._close_attempts.clear()
    pm._close_next_try.clear()
    yield SimpleNamespace(engine=engine, Session=Session)
    engine.dispose()


@pytest.fixture()
def counter(db):
    return _SQLCounter(db.engine)


# ---------------------------------------------------------------------------
# Seed helpers (each opens + closes its own short session so no two sessions
# hold the shared StaticPool connection at once)
# ---------------------------------------------------------------------------
def _add_position(s, *, avg_buy=2000.0, avg_sell=0.0, qty=0.10):
    pos = Position(
        id=uuid.uuid4(), symbol=SYMBOL,
        quantity=Decimal(str(qty)),
        avg_buy_price=Decimal(str(avg_buy)),
        avg_sell_price=Decimal(str(avg_sell)),
        realized_profit_loss=Decimal("0"),
        profit_loss=Decimal("0"), ltp=Decimal("0"),
    )
    s.add(pos)
    s.flush()
    return pos


def _add_trigger(s, pos, *, trigger_type="TARGET", greater_than=True,
                 trigger_price=2010.0, qty=0.10, order_type="LIMIT",
                 side="SELL"):
    t = Trigger(
        id=uuid.uuid4(), symbol=SYMBOL, trigger_type=trigger_type,
        greater_than=greater_than, trigger_price=Decimal(str(trigger_price)),
        quantity=Decimal(str(qty)), order_type=order_type, side=side,
        status="PENDING", position_id=pos.id,
    )
    s.add(t)
    s.flush()
    return t


def _seed_currency(Session, ltp="0.00"):
    s = Session()
    s.add(CurrencyPair(id=uuid.uuid4(), symbol=SYMBOL, ltp=ltp))
    s.commit()
    s.close()


def _seed_three_positions(Session):
    """3 open positions; position[0] carries TWO pending triggers (TARGET far +
    STOPLOSS far) so grouping-by-position is exercised. None fire at 2005."""
    s = Session()
    p0 = _add_position(s, avg_buy=2000.0, qty=0.10)
    _add_trigger(s, p0, trigger_type="TARGET", greater_than=True,
                 trigger_price=2010.0)
    _add_trigger(s, p0, trigger_type="STOPLOSS", greater_than=False,
                 trigger_price=1990.0)
    p1 = _add_position(s, avg_buy=2001.0, qty=0.20)
    _add_trigger(s, p1, trigger_type="TARGET", greater_than=True,
                 trigger_price=2011.0)
    p2 = _add_position(s, avg_sell=2002.0, avg_buy=0.0, qty=0.30)
    _add_trigger(s, p2, trigger_type="STOPLOSS", greater_than=True,
                 trigger_price=2020.0)
    ids = [p0.id, p1.id, p2.id]
    s.commit()
    s.close()
    return ids


# ---------------------------------------------------------------------------
# (1) N+1 kill
# ---------------------------------------------------------------------------
def test_single_trigger_fetch_for_all_positions(db, counter):
    _seed_currency(db.Session)
    _seed_three_positions(db.Session)

    counter.reset()
    pm._check_triggers(2005.0)   # below every threshold -> nothing fires

    assert counter.trigger_selects == 1, (
        "expected exactly ONE PENDING-trigger SELECT for all open positions; "
        f"got {counter.trigger_selects} (N+1 not killed)"
    )


# ---------------------------------------------------------------------------
# (2)+(3) first tick writes both the position P&L mirror and the CurrencyPair
# ---------------------------------------------------------------------------
def test_pnl_and_currency_written_on_first_tick(db):
    _seed_currency(db.Session)
    ids = _seed_three_positions(db.Session)
    pm._reset_write_throttle()

    pm._check_triggers(2005.0)

    s = db.Session()
    try:
        pos0 = s.get(Position, ids[0])
        # avg_buy 2000, price 2005, 0.10 lots -> (5) * (0.10*100) = 50.00
        assert float(pos0.profit_loss) == pytest.approx(50.0)
        assert float(pos0.ltp) == pytest.approx(2005.0)
        cp = s.query(CurrencyPair).filter_by(symbol=SYMBOL).first()
        assert cp.ltp == str(2005.0)   # CurrencyPair mirror folded into the tick
    finally:
        s.close()


# ---------------------------------------------------------------------------
# (2)+(3) throttle: unchanged price => no UPDATE at all on the second tick
# ---------------------------------------------------------------------------
def test_pnl_write_throttled_when_price_unchanged(db, counter):
    _seed_currency(db.Session)
    _seed_three_positions(db.Session)
    pm._reset_write_throttle()

    pm._check_triggers(2005.0)    # first tick: writes the mirror
    counter.reset()
    pm._check_triggers(2005.0)    # same price, within window: must be a no-op

    assert counter.position_updates == 0, (
        "pos.ltp/profit_loss must NOT be rewritten when the price is unchanged"
    )
    assert counter.currency_updates == 0, (
        "CurrencyPair.ltp must NOT be rewritten when the price is unchanged"
    )


def test_pnl_write_resumes_when_price_changes(db, counter):
    _seed_currency(db.Session)
    _seed_three_positions(db.Session)
    pm._reset_write_throttle()

    pm._check_triggers(2005.0)
    counter.reset()
    pm._check_triggers(2006.0)    # price moved -> writes resume

    assert counter.position_updates >= 1
    assert counter.currency_updates >= 1


# ---------------------------------------------------------------------------
# (4) one Session per _check_triggers tick
# ---------------------------------------------------------------------------
def test_one_session_per_tick(db, monkeypatch):
    _seed_currency(db.Session)
    _seed_three_positions(db.Session)
    real = db.Session
    calls = {"n": 0}

    def _counting_session():
        calls["n"] += 1
        return real()

    monkeypatch.setattr(pm, "Session", _counting_session)
    pm._reset_write_throttle()
    pm._check_triggers(2005.0)

    assert calls["n"] == 1, f"expected 1 Session() per tick, got {calls['n']}"


# ---------------------------------------------------------------------------
# parity: a TARGET trigger fires and realizes P&L exactly as the original code
# ---------------------------------------------------------------------------
def test_target_fires_and_realizes_pnl(db):
    _seed_currency(db.Session)
    s = db.Session()
    pos = _add_position(s, avg_buy=2000.0, qty=0.10)
    _add_trigger(s, pos, trigger_type="TARGET", greater_than=True,
                 trigger_price=2010.0, qty=0.10)
    pid = pos.id
    s.commit()
    s.close()

    pm._reset_write_throttle()
    pm._check_triggers(2010.0)   # price hits the TARGET

    s = db.Session()
    try:
        pos = s.get(Position, pid)
        assert float(pos.quantity) == 0.0
        assert float(pos.profit_loss) == 0.0
        # realized is booked from the RAW trigger quantity (points x lots):
        # (2010-2000) * 0.10 = 1.00  (NOT x100 - matches trigger_logic units).
        assert float(pos.realized_profit_loss) == pytest.approx(1.0)

        trig = s.query(Trigger).filter_by(position_id=pid).first()
        assert trig.status == "TRIGGERED"

        order = s.query(Order).filter_by(
            position_id=pid, condition="TARGET").first()
        assert order is not None
        assert float(order.price) == pytest.approx(2010.0)
        # amount = round(price * close_qty, 2) = 2010 * 0.10 = 201.00
        assert float(order.amount) == pytest.approx(201.0)
    finally:
        s.close()


def test_other_pending_triggers_cancelled_on_close(db):
    _seed_currency(db.Session)
    s = db.Session()
    pos = _add_position(s, avg_buy=2000.0, qty=0.10)
    _add_trigger(s, pos, trigger_type="TARGET", greater_than=True,
                 trigger_price=2010.0, qty=0.10)
    _add_trigger(s, pos, trigger_type="STOPLOSS", greater_than=False,
                 trigger_price=1990.0, qty=0.10)
    pid = pos.id
    s.commit()
    s.close()

    pm._reset_write_throttle()
    pm._check_triggers(2010.0)   # TARGET fires

    s = db.Session()
    try:
        statuses = {
            t.trigger_type: t.status
            for t in s.query(Trigger).filter_by(position_id=pid).all()
        }
        assert statuses["TARGET"] == "TRIGGERED"
        assert statuses["STOPLOSS"] == "CANCELLED"
    finally:
        s.close()


# ---------------------------------------------------------------------------
# parity: firing is NEVER suppressed by the price-mirror throttle. A TIME_EXIT
# fires on wall-clock while the price is unchanged (mirror throttled).
# ---------------------------------------------------------------------------
def test_trigger_still_fires_when_pnl_write_throttled(db, monkeypatch):
    _seed_currency(db.Session)
    s = db.Session()
    pos = _add_position(s, avg_buy=2000.0, qty=0.10)
    # CUSTOM + order_type TIME_EXIT: trigger_price is a UNIX expiry epoch.
    _add_trigger(s, pos, trigger_type="CUSTOM", order_type="TIME_EXIT",
                 greater_than=False, trigger_price=4050.0, qty=0.10)
    pid = pos.id
    s.commit()
    s.close()

    # TIME_EXIT needs an active broker close; stub it to "confirmed".
    monkeypatch.setattr(pm, "_attempt_broker_close",
                        lambda sess, p, label, now_ts: (True, True))
    # Large heartbeat so an unchanged price is genuinely throttled between ticks.
    monkeypatch.setattr(pm, "MONITOR_PNL_WRITE_SEC", 3600.0)

    clock = {"t": 4000.0}
    monkeypatch.setattr(pm.time, "time", lambda: clock["t"])

    pm._reset_write_throttle()
    pm._check_triggers(2005.0)   # t=4000 < expiry 4050 -> no fire; mirror written

    s = db.Session()
    trig = s.query(Trigger).filter_by(position_id=pid).first()
    assert trig.status == "PENDING", "must not fire before expiry"
    s.close()

    clock["t"] = 4100.0          # past expiry; price UNCHANGED -> mirror throttled
    pm._check_triggers(2005.0)

    s = db.Session()
    try:
        trig = s.query(Trigger).filter_by(position_id=pid).first()
        assert trig.status == "TRIGGERED", (
            "TIME_EXIT must still fire on a throttled tick (live evaluation)"
        )
        pos = s.get(Position, pid)
        assert float(pos.quantity) == 0.0
    finally:
        s.close()
