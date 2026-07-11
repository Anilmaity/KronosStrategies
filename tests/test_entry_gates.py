# tests/test_entry_gates.py
"""Entry-quality gates added 2026-07-11 after the double kill-switch RCA
(2026-07-09/10): news blackout, min stop distance, entry-drift budget,
cross-strategy duplicate guard, and exit-time daily P&L attribution.

All offline: pure helpers + in-memory SQLite (same pattern as
test_risk_sizing.py).
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, time as dtime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies")
)
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from strategy import entry_manager as em  # noqa: E402


# ── _parse_utc_windows / _in_news_blackout ────────────────────────────────────

def test_parse_windows_single():
    wins = em._parse_utc_windows("12:25-12:45")
    assert wins == [(dtime(12, 25), dtime(12, 45))]


def test_parse_windows_multiple_and_garbage():
    wins = em._parse_utc_windows("12:25-12:45, nonsense ,13:55-14:05")
    assert wins == [(dtime(12, 25), dtime(12, 45)), (dtime(13, 55), dtime(14, 5))]


def test_parse_windows_empty():
    assert em._parse_utc_windows("") == []
    assert em._parse_utc_windows(None) == []


@pytest.mark.parametrize("hhmm,expected", [
    ((12, 24), False),   # one minute before
    ((12, 25), True),    # inclusive start
    ((12, 33), True),    # the candle that killed both days
    ((12, 45), True),    # inclusive end
    ((12, 46), False),   # one minute after
    ((1, 0), False),
])
def test_in_news_blackout(monkeypatch, hhmm, expected):
    monkeypatch.setattr(em, "_BLACKOUT_WINDOWS",
                        em._parse_utc_windows("12:25-12:45"))
    now = datetime(2026, 7, 10, hhmm[0], hhmm[1], tzinfo=timezone.utc)
    assert em._in_news_blackout(now) is expected


# ── _drift_budget_pts / _entry_drift_exceeded ─────────────────────────────────

def test_drift_budget_capped_by_fraction_of_stop(monkeypatch):
    monkeypatch.setattr(em, "MAX_ENTRY_DRIFT_PTS", 0.5)
    monkeypatch.setattr(em, "MAX_ENTRY_DRIFT_FRAC", 0.25)
    # 1.0pt stop -> 0.25pt budget (fraction binds)
    assert em._drift_budget_pts(4000.0, 3999.0) == pytest.approx(0.25)
    # 10pt stop -> 0.5pt budget (absolute cap binds)
    assert em._drift_budget_pts(4000.0, 3990.0) == pytest.approx(0.5)
    # no stop -> absolute cap
    assert em._drift_budget_pts(4000.0, None) == pytest.approx(0.5)


def test_drift_rejects_adverse_buy(monkeypatch):
    monkeypatch.setattr(em, "MAX_ENTRY_DRIFT_PTS", 0.5)
    monkeypatch.setattr(em, "MAX_ENTRY_DRIFT_FRAC", 0.25)
    # signal BUY @4000 with 4pt stop -> budget 0.5; market already at 4001.2
    monkeypatch.setattr(em, "fetch_latest_ltp", lambda symbol: 4001.2)
    exceeded, detail = em._entry_drift_exceeded("BUY", 4000.0, 3996.0, "XAU_USD")
    assert exceeded is True and "drift +1.20" in detail


def test_drift_allows_favorable_sell(monkeypatch):
    # SELL @4000, market rallied to 4001.5 -> we sell HIGHER than modelled
    monkeypatch.setattr(em, "fetch_latest_ltp", lambda symbol: 4001.5)
    exceeded, _ = em._entry_drift_exceeded("SELL", 4000.0, 4004.0, "XAU_USD")
    assert exceeded is False


def test_drift_rejects_adverse_sell(monkeypatch):
    monkeypatch.setattr(em, "MAX_ENTRY_DRIFT_PTS", 0.5)
    monkeypatch.setattr(em, "MAX_ENTRY_DRIFT_FRAC", 0.25)
    # SELL @4000, market already dropped to 3998.8 -> chasing 1.2pt
    monkeypatch.setattr(em, "fetch_latest_ltp", lambda symbol: 3998.8)
    exceeded, _ = em._entry_drift_exceeded("SELL", 4000.0, 4004.0, "XAU_USD")
    assert exceeded is True


def test_drift_fails_open_without_feed(monkeypatch):
    monkeypatch.setattr(em, "fetch_latest_ltp", lambda symbol: None)
    exceeded, detail = em._entry_drift_exceeded("BUY", 4000.0, 3996.0, "XAU_USD")
    assert exceeded is False and detail == "no_ltp"


# ── _duplicate_open_same_side ─────────────────────────────────────────────────

@pytest.fixture()
def db_factory(monkeypatch):
    engine = create_engine("sqlite://")
    em.Position.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(em, "Session", factory)
    yield factory
    engine.dispose()


def _mk_open_position(factory, ub_id, *, side="BUY", entry=4000.0, qty=0.10,
                      age_min=1.0):
    sess = factory()
    us = em.UserStrategy(id=uuid.uuid4(), name="t", deployed=True,
                         is_active=True, user_broker_id=ub_id)
    sess.add(us)
    pos = em.Position(
        id=uuid.uuid4(), symbol="XAU_USD",
        user_strategy_id=us.id,
        quantity=Decimal(str(qty)),
        avg_buy_price=Decimal(str(entry)) if side == "BUY" else Decimal("0"),
        avg_sell_price=Decimal("0") if side == "BUY" else Decimal(str(entry)),
        profit_loss=Decimal("0"),
        created_at=datetime.now(timezone.utc) - timedelta(minutes=age_min),
    )
    sess.add(pos)
    sess.commit()
    pid = pos.id
    sess.close()
    return pid


def test_dup_blocks_same_setup(db_factory, monkeypatch):
    monkeypatch.setattr(em, "DUP_GUARD_MIN", 15.0)
    monkeypatch.setattr(em, "DUP_GUARD_PROX_PTS", 2.0)
    ub = uuid.uuid4()
    _mk_open_position(db_factory, ub, side="BUY", entry=4108.0, age_min=1)
    # S99 firing the same FVG 8s after S93 (Jul-10 12:21 double-fill)
    assert em._duplicate_open_same_side(ub, "XAU_USD", "BUY", 4108.5) is True


def test_dup_allows_far_entry(db_factory, monkeypatch):
    monkeypatch.setattr(em, "DUP_GUARD_MIN", 15.0)
    monkeypatch.setattr(em, "DUP_GUARD_PROX_PTS", 2.0)
    ub = uuid.uuid4()
    _mk_open_position(db_factory, ub, side="BUY", entry=4100.0, age_min=1)
    assert em._duplicate_open_same_side(ub, "XAU_USD", "BUY", 4110.0) is False


def test_dup_allows_opposite_side(db_factory, monkeypatch):
    monkeypatch.setattr(em, "DUP_GUARD_MIN", 15.0)
    ub = uuid.uuid4()
    _mk_open_position(db_factory, ub, side="BUY", entry=4108.0, age_min=1)
    assert em._duplicate_open_same_side(ub, "XAU_USD", "SELL", 4108.0) is False


def test_dup_allows_stale_position(db_factory, monkeypatch):
    monkeypatch.setattr(em, "DUP_GUARD_MIN", 15.0)
    monkeypatch.setattr(em, "DUP_GUARD_PROX_PTS", 2.0)
    ub = uuid.uuid4()
    _mk_open_position(db_factory, ub, side="BUY", entry=4108.0, age_min=40)
    assert em._duplicate_open_same_side(ub, "XAU_USD", "BUY", 4108.0) is False


def test_dup_disabled_by_env(db_factory, monkeypatch):
    monkeypatch.setattr(em, "DUP_GUARD_MIN", 0.0)
    ub = uuid.uuid4()
    _mk_open_position(db_factory, ub, side="BUY", entry=4108.0, age_min=1)
    assert em._duplicate_open_same_side(ub, "XAU_USD", "BUY", 4108.0) is False


# ── _todays_realized_usd (exit-time attribution) ──────────────────────────────

def _mk_closed_position(sess, us_id, *, realized_units, exit_at):
    pos = em.Position(
        id=uuid.uuid4(), symbol="XAU_USD", user_strategy_id=us_id,
        quantity=Decimal("0"),
        avg_buy_price=Decimal("4000"), avg_sell_price=Decimal("0"),
        realized_profit_loss=Decimal(str(realized_units)),
    )
    sess.add(pos)
    sess.flush()
    sess.add(em.Order(
        id=uuid.uuid4(), symbol="XAU_USD", price=Decimal("4000"),
        condition="STOPLOSS", side="SELL", quantity=Decimal("0.1"),
        amount=Decimal("400"), order_type="MARKET", status="EXECUTED",
        position_id=pos.id, created_at=exit_at,
    ))
    return pos


def test_daily_pnl_keys_on_exit_time_not_modified(db_factory):
    """A position closed YESTERDAY must not count today, no matter how often
    the reconciler re-touches the row (the Jul-9 false-trip mechanism)."""
    sess = db_factory()
    us_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    today_start = datetime.combine(now.date(), dtime.min, tzinfo=timezone.utc)

    # closed today: -0.5 units = -$50
    _mk_closed_position(sess, us_id, realized_units=-0.5,
                        exit_at=today_start + timedelta(hours=1))
    # closed yesterday: -0.67 units — must NOT count regardless of modified_at
    _mk_closed_position(sess, us_id, realized_units=-0.67,
                        exit_at=today_start - timedelta(hours=2))
    sess.commit()

    assert em._todays_realized_usd(sess, [us_id]) == pytest.approx(-50.0)
    sess.close()


def test_daily_pnl_empty_ids(db_factory):
    sess = db_factory()
    assert em._todays_realized_usd(sess, []) == 0.0
    sess.close()
