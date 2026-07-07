# tests/test_fill_reconciler.py
"""Broker-fill reconciliation (2026-07-07): deals -> Position true-up.
All offline: in-memory SQLite + hand-built deal groups; no network."""
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

import fill_reconciler as fr  # noqa: E402
from shared.models import Order, Position  # noqa: E402


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Position.metadata.create_all(engine)
    sess = sessionmaker(bind=engine)()
    yield sess
    sess.close()
    engine.dispose()


def _mk_trade(sess, *, side="SELL", booked_entry=4137.265, qty=0.0,
              realized=-0.38, broker_pid="23532569"):
    pos = Position(
        id=uuid.uuid4(), symbol="XAU_USD",
        quantity=Decimal(str(qty)),
        avg_buy_price=Decimal(str(booked_entry)) if side == "BUY" else Decimal("0"),
        avg_sell_price=Decimal("0") if side == "BUY" else Decimal(str(booked_entry)),
        realized_profit_loss=Decimal(str(realized)),
    )
    sess.add(pos)
    sess.flush()
    eo = Order(
        id=uuid.uuid4(), symbol="XAU_USD", condition="ENTRY", side=side,
        price=Decimal(str(booked_entry)), quantity=Decimal("0.20"),
        status="EXECUTED", broker_order_id=broker_pid, position_id=pos.id,
    )
    sess.add(eo)
    sess.commit()
    return pos


# The first live trade, exactly as MetaAPI reported it.
_FIRST_TRADE_DEALS = [
    {"positionId": "23532569", "entryType": "DEAL_ENTRY_IN",
     "type": "DEAL_TYPE_SELL", "price": 4136.9, "volume": 0.2,
     "commission": -1, "swap": 0, "profit": 0},
    {"positionId": "23532569", "entryType": "DEAL_ENTRY_OUT",
     "type": "DEAL_TYPE_BUY", "price": 4140.27, "volume": 0.2,
     "commission": 0, "swap": 0, "profit": -67.4},
]


def test_group_deals_first_live_trade():
    g = fr.group_deals(_FIRST_TRADE_DEALS)["23532569"]
    assert g["entry_px"] == pytest.approx(4136.9)
    assert g["entry_side"] == "SELL"
    assert g["out_vol"] == pytest.approx(0.2)
    assert g["total_usd"] == pytest.approx(-68.4)      # profit + commission


def test_apply_trues_up_closed_short(db):
    pos = _mk_trade(db)                                # DB thought -$38
    n = fr.apply_deals(db, fr.group_deals(_FIRST_TRADE_DEALS))
    db.commit()
    assert n == 1
    db.refresh(pos)
    assert float(pos.avg_sell_price) == pytest.approx(4136.9)   # real fill
    # realized in storage units (USD/100), commission included
    assert float(pos.realized_profit_loss) == pytest.approx(-0.68, abs=0.006)


def test_apply_is_idempotent(db):
    _mk_trade(db)
    grouped = fr.group_deals(_FIRST_TRADE_DEALS)
    assert fr.apply_deals(db, grouped) == 1
    db.commit()
    assert fr.apply_deals(db, grouped) == 0, "second pass must be a no-op"


def test_open_position_gets_entry_fix_only(db):
    pos = _mk_trade(db, side="BUY", booked_entry=4100.0, qty=0.20, realized=0.0)
    deals = [{"positionId": "23532569", "entryType": "DEAL_ENTRY_IN",
              "type": "DEAL_TYPE_BUY", "price": 4099.55, "volume": 0.2,
              "commission": -1, "swap": 0, "profit": 0}]
    assert fr.apply_deals(db, fr.group_deals(deals)) == 1
    db.commit()
    db.refresh(pos)
    assert float(pos.avg_buy_price) == pytest.approx(4099.55)
    assert float(pos.realized_profit_loss) == 0.0, \
        "open position's realized must stay untouched"


def test_unmatched_position_ids_ignored(db):
    _mk_trade(db, broker_pid="999999")
    assert fr.apply_deals(db, fr.group_deals(_FIRST_TRADE_DEALS)) == 0


def test_signal_price_audit_preserved(db):
    pos = _mk_trade(db)
    fr.apply_deals(db, fr.group_deals(_FIRST_TRADE_DEALS))
    db.commit()
    eo = db.query(Order).filter_by(position_id=pos.id, condition="ENTRY").one()
    # 4137.27 == the signal price at the column's 2dp precision
    assert float(eo.price) == pytest.approx(4137.27, abs=0.006), \
        "Order.price keeps the signal price for audit"
