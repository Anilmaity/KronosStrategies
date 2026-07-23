# tests/test_broker_deals_archive.py
"""broker_deals archive — append-only store of raw MetaAPI history-deals.

Written by fill_reconciler on every cycle (additive, failure-isolated) so the
account's true order/P&L history survives in our own DB — including balance
operations and positions no bot placed (e.g. manual mobile scalps), which the
reconciler itself matches to nothing and would otherwise drop.
"""
from __future__ import annotations

import os
import sys

import pytest
from sqlalchemy import create_engine, select

sys.path.insert(0, os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies")
))

from shared import broker_deals as bd  # noqa: E402


DEALS = [
    {
        "id": "d-1", "positionId": "p-1", "orderId": "o-1", "symbol": "XAUUSD",
        "type": "DEAL_TYPE_BUY", "entryType": "DEAL_ENTRY_IN",
        "volume": 0.2, "price": 4130.05, "profit": 0.0, "commission": -0.2,
        "swap": 0.0, "time": "2026-07-07T08:43:21.000Z",
    },
    {
        "id": "d-2", "positionId": "p-1", "orderId": "o-2", "symbol": "XAUUSD",
        "type": "DEAL_TYPE_SELL", "entryType": "DEAL_ENTRY_OUT",
        "volume": 0.2, "price": 4130.87, "profit": 16.4, "commission": -0.2,
        "swap": 0.0, "time": "2026-07-07T08:44:02.000Z",
    },
    {   # balance operation (deposit) — no positionId
        "id": "d-3", "type": "DEAL_TYPE_BALANCE",
        "profit": 5000.0, "time": "2026-07-06T10:00:00.000Z",
    },
]


@pytest.fixture()
def engine():
    eng = create_engine("sqlite://")
    bd.ensure_table(eng)
    yield eng
    eng.dispose()


def test_persist_inserts_and_maps_fields(engine):
    n = bd.persist_deals(engine, "acct-1", DEALS)
    assert n == 3
    with engine.connect() as conn:
        rows = conn.execute(
            select(bd.broker_deals).order_by(bd.broker_deals.c.deal_id)
        ).mappings().all()
    assert len(rows) == 3
    r = rows[0]
    assert r["account_id"] == "acct-1"
    assert r["position_id"] == "p-1"
    assert r["deal_type"] == "DEAL_TYPE_BUY"
    assert r["entry_type"] == "DEAL_ENTRY_IN"
    assert r["price"] == pytest.approx(4130.05)
    assert r["deal_time"] is not None
    assert r["raw"]["id"] == "d-1"          # full payload preserved
    bal = rows[2]
    assert bal["position_id"] is None
    assert bal["profit"] == pytest.approx(5000.0)


def test_persist_is_idempotent(engine):
    assert bd.persist_deals(engine, "acct-1", DEALS) == 3
    assert bd.persist_deals(engine, "acct-1", DEALS) == 0      # re-run: no dupes
    assert bd.persist_deals(engine, "acct-1", DEALS[:1]) == 0  # partial overlap
    with engine.connect() as conn:
        n = conn.execute(select(bd.broker_deals)).fetchall()
    assert len(n) == 3


def test_same_deal_id_on_other_account_is_distinct(engine):
    bd.persist_deals(engine, "acct-1", DEALS[:1])
    assert bd.persist_deals(engine, "acct-2", DEALS[:1]) == 1  # PK is (account, deal)


def test_deals_without_id_are_skipped(engine):
    assert bd.persist_deals(engine, "acct-1", [{"positionId": "p-9"}]) == 0


def test_persist_empty_is_noop(engine):
    assert bd.persist_deals(engine, "acct-1", []) == 0
