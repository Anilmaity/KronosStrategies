# tests/test_risk_sizing.py
"""Phase-1 manager redesign (2026-07-06): risk-normalized sizing and the
no-add-to-loser guard in strategy/entry_manager.py.

All offline: pure sizing math + in-memory SQLite for the position query.
"""
from __future__ import annotations

import os
import sys
import uuid
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


# ── _risk_sized_qty ───────────────────────────────────────────────────────────

def test_sizing_disabled_falls_back_to_fixed(monkeypatch):
    monkeypatch.setattr(em, "RISK_PER_TRADE_USD", 0.0)
    qty, reason = em._risk_sized_qty(4000.0, 3980.0, "XAU_USD", 0.02)
    assert qty == 0.02 and reason == "fixed"


def test_sizing_floors_to_lot_step(monkeypatch):
    monkeypatch.setattr(em, "RISK_PER_TRADE_USD", 75.0)
    # 20-pt stop at $100/pt/lot: raw = 75/2000 = 0.0375 -> floors to 0.03
    qty, _ = em._risk_sized_qty(4000.0, 3980.0, "XAU_USD", 0.02)
    assert qty == 0.03
    # Realized risk never exceeds the budget: 0.03 * 20 * 100 = $60 <= $75.
    assert qty * 20 * 100 <= 75.0


def test_sizing_caps_at_max_lot(monkeypatch):
    monkeypatch.setattr(em, "RISK_PER_TRADE_USD", 75.0)
    monkeypatch.setattr(em, "MAX_LOT", 0.20)
    # 1-pt stop: raw = 0.75 lots -> capped to MAX_LOT
    qty, _ = em._risk_sized_qty(4000.0, 3999.0, "XAU_USD", 0.02)
    assert qty == 0.20


def test_sizing_rejects_freak_wide_stop(monkeypatch):
    monkeypatch.setattr(em, "RISK_PER_TRADE_USD", 75.0)
    # 120-pt stop: even MIN_LOT risks $120 > 1.5 x 75 = $112.5 -> reject
    qty, reason = em._risk_sized_qty(4000.0, 3880.0, "XAU_USD", 0.02)
    assert qty is None and "risk_too_wide" in reason


def test_sizing_min_lot_within_tolerance(monkeypatch):
    monkeypatch.setattr(em, "RISK_PER_TRADE_USD", 75.0)
    # 100-pt stop: MIN_LOT risks $100 <= $112.5 tolerance -> trades 0.01
    qty, _ = em._risk_sized_qty(4000.0, 3900.0, "XAU_USD", 0.02)
    assert qty == 0.01


def test_sizing_short_side_symmetric(monkeypatch):
    monkeypatch.setattr(em, "RISK_PER_TRADE_USD", 75.0)
    qty_long, _ = em._risk_sized_qty(4000.0, 3980.0, "XAU_USD", 0.02)
    qty_short, _ = em._risk_sized_qty(4000.0, 4020.0, "XAU_USD", 0.02)
    assert qty_long == qty_short


# ── _losing_open_same_side ────────────────────────────────────────────────────

@pytest.fixture()
def db_factory(monkeypatch):
    engine = create_engine("sqlite://")
    em.Position.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(em, "Session", factory)
    yield factory
    engine.dispose()


def _mk_position(factory, ub_id, *, side="BUY", pnl=-5.0, qty=0.02):
    sess = factory()
    us = em.UserStrategy(id=uuid.uuid4(), name="t", deployed=True,
                         is_active=True, user_broker_id=ub_id)
    sess.add(us)
    pos = em.Position(
        id=uuid.uuid4(), symbol="XAU_USD",
        user_strategy_id=us.id,
        quantity=Decimal(str(qty)),
        avg_buy_price=Decimal("4000") if side == "BUY" else Decimal("0"),
        avg_sell_price=Decimal("0") if side == "BUY" else Decimal("4000"),
        profit_loss=Decimal(str(pnl)),
    )
    sess.add(pos)
    sess.commit()
    sess.close()


def test_blocks_same_side_on_losing_open(db_factory):
    ub = uuid.uuid4()
    _mk_position(db_factory, ub, side="BUY", pnl=-5.0)
    assert em._losing_open_same_side(ub, "XAU_USD", "BUY") is True


def test_allows_opposite_side(db_factory):
    ub = uuid.uuid4()
    _mk_position(db_factory, ub, side="BUY", pnl=-5.0)
    assert em._losing_open_same_side(ub, "XAU_USD", "SELL") is False


def test_allows_when_open_position_is_winning(db_factory):
    ub = uuid.uuid4()
    _mk_position(db_factory, ub, side="BUY", pnl=+7.5)
    assert em._losing_open_same_side(ub, "XAU_USD", "BUY") is False


def test_ignores_closed_positions(db_factory):
    ub = uuid.uuid4()
    _mk_position(db_factory, ub, side="BUY", pnl=-5.0, qty=0.0)
    assert em._losing_open_same_side(ub, "XAU_USD", "BUY") is False


def test_ignores_other_accounts(db_factory):
    _mk_position(db_factory, uuid.uuid4(), side="BUY", pnl=-5.0)
    assert em._losing_open_same_side(uuid.uuid4(), "XAU_USD", "BUY") is False
