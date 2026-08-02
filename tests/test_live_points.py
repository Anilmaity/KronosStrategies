"""Task 3 (Manager Backtest fidelity fix): points blocks.

live_summary() must recover sizing-invariant points via the ENTRY Order's
lots (Order.condition == "ENTRY", quantity == lots) — NOT
Position.total_buy_quantity, which is 0 for shorts. sim_per_strategy() emits
the matching {"points": {...}} shape from TradeRecord.pnl_pts.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies"))
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from audit_worker.live_deltas import live_summary, _IST_SKEW  # noqa: E402
from shared.models import (  # noqa: E402
    CurrencyPair, Order, Position, Strategy, User, UserBroker, UserStrategy,
)

WIN_START = datetime(2026, 7, 1, tzinfo=timezone.utc)
WIN_END = datetime(2026, 7, 31, tzinfo=timezone.utc)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Position.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as s:
        yield s
    engine.dispose()


def _seed(s, name):
    user = User(email=f"{uuid.uuid4()}@t.local")
    cp = CurrencyPair(symbol="XAU_USD", name="XAU_USD")
    strat = Strategy(name=name, apis_currencypair=cp)
    ub = UserBroker(apis_user=user, api_key=str(uuid.uuid4()))
    us = UserStrategy(apis_strategy=strat, apis_userbroker=ub)
    s.add_all([user, cp, strat, ub, us])
    s.flush()
    return us


def _pos(s, us, realized, created_utc, lots=None, qty=0):
    """Seed a Position, plus (unless lots is None) its ENTRY Order."""
    pos = Position(
        symbol="XAU_USD", quantity=Decimal(qty),
        realized_profit_loss=Decimal(str(realized)),
        user_strategy_id=us.id,
        created_at=created_utc.replace(tzinfo=None) + _IST_SKEW,
    )
    s.add(pos)
    s.flush()
    if lots is not None:
        s.add(Order(position_id=pos.id, condition="ENTRY",
                    quantity=Decimal(str(lots))))
        s.flush()
    return pos


def test_live_points_recovered_from_entry_order(db):
    # 10-pt win at 0.10 lots -> realized units = 1.0 ; points = 1.0/0.10 = 10
    us = _seed(db, "S93 FVG Scalp")
    _pos(db, us, 1.0, WIN_START + timedelta(days=1), lots=0.10)

    out = live_summary(db, ["S93 FVG Scalp"], WIN_START, WIN_END)
    blk = out["S93 FVG Scalp"]
    assert round(blk["points"]["pnl_pts"], 4) == 10.0
    assert blk["usd"]["pnl_usd"] == 100.0        # 1.0 units * 100
    assert blk["points"]["trades"] == 1
    assert blk["usd"]["trades"] == 1


def test_points_are_sizing_invariant_across_lot_sizes(db):
    # Same 10-pt win, different lot sizes -> same recovered points, different
    # realized units/usd. This is the headline fidelity claim.
    us = _seed(db, "S94 Sweep Reversal")
    _pos(db, us, 1.0, WIN_START + timedelta(days=1), lots=0.10)   # 10 pts
    _pos(db, us, 5.0, WIN_START + timedelta(days=2), lots=0.50)   # 10 pts

    out = live_summary(db, ["S94 Sweep Reversal"], WIN_START, WIN_END)
    blk = out["S94 Sweep Reversal"]
    assert blk["points"]["trades"] == 2
    assert round(blk["points"]["pnl_pts"], 4) == 20.0  # 10 + 10, size-invariant
    assert blk["usd"]["pnl_usd"] == pytest.approx(600.0)  # (1.0+5.0)*100


def test_win_rate_and_profit_factor(db):
    us = _seed(db, "S99 MSS FVG")
    _pos(db, us, 1.0, WIN_START + timedelta(days=1), lots=0.10)   # +10 pts win
    _pos(db, us, -0.5, WIN_START + timedelta(days=2), lots=0.10)  # -5 pts loss

    out = live_summary(db, ["S99 MSS FVG"], WIN_START, WIN_END)
    blk = out["S99 MSS FVG"]
    assert blk["points"]["trades"] == 2
    assert blk["points"]["win_rate"] == pytest.approx(50.0)
    assert blk["points"]["profit_factor"] == pytest.approx(10.0 / 5.0)
    assert blk["usd"]["win_rate"] == pytest.approx(50.0)


def test_position_without_entry_order_is_excluded(db):
    # No ENTRY Order seeded -> the ENTRY-order INNER JOIN drops the row
    # entirely (cannot recover sizing-invariant points without it).
    us = _seed(db, "S100 ER Gate")
    _pos(db, us, 1.0, WIN_START + timedelta(days=1), lots=None)

    out = live_summary(db, ["S100 ER Gate"], WIN_START, WIN_END)
    assert out == {}


def test_zero_lots_entry_order_is_skipped(db):
    # ENTRY Order present but quantity 0 -> cannot divide, must skip (not
    # ZeroDivisionError, not counted).
    us = _seed(db, "S100 ER Gate")
    _pos(db, us, 1.0, WIN_START + timedelta(days=1), lots=0.0)
    _pos(db, us, 2.0, WIN_START + timedelta(days=2), lots=0.10)

    out = live_summary(db, ["S100 ER Gate"], WIN_START, WIN_END)
    blk = out["S100 ER Gate"]
    assert blk["points"]["trades"] == 1
    assert round(blk["points"]["pnl_pts"], 4) == 20.0
