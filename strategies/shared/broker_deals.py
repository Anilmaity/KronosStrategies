"""broker_deals — append-only archive of raw MetaAPI history-deals.

Broker truth (orders, fills, P&L, commissions, swaps, balance operations) as
reported by the account itself, persisted continuously so history survives
past MetaAPI's retention and covers trades no bot placed (manual scalps show
up here with no matching Position row).

Written by fill_reconciler on every cycle. This table is OURS (not Django's):
created additively with checkfirst=True, append-only, keyed
(account_id, deal_id) — never mutated, safe to query for analytics.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import (
    Column, DateTime, Float, Index, JSON, MetaData, String, Table, func,
)

log = logging.getLogger(__name__)

metadata = MetaData()

broker_deals = Table(
    "broker_deals", metadata,
    Column("account_id", String(64), primary_key=True),   # MetaAPI account UUID
    Column("deal_id", String(64), primary_key=True),      # broker deal ticket
    Column("position_id", String(64)),
    Column("order_id", String(64)),
    Column("symbol", String(32)),
    Column("deal_type", String(40)),                      # DEAL_TYPE_BUY/SELL/BALANCE
    Column("entry_type", String(40)),                     # DEAL_ENTRY_IN/OUT
    Column("volume", Float),
    Column("price", Float),
    Column("profit", Float),
    Column("commission", Float),
    Column("swap", Float),
    Column("deal_time", DateTime(timezone=True)),
    Column("raw", JSON),                                  # full MetaAPI payload
    Column("inserted_at", DateTime(timezone=True), server_default=func.now()),
    Index("ix_broker_deals_position", "position_id"),
    Index("ix_broker_deals_time", "deal_time"),
)

_ensured: set[int] = set()   # engine ids we already ran create_all against


def ensure_table(engine) -> None:
    """Create the table/indexes if absent (additive; never alters)."""
    if id(engine) in _ensured:
        return
    metadata.create_all(engine, checkfirst=True)
    _ensured.add(id(engine))


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _row(account_id: str, d: dict) -> dict:
    return {
        "account_id": account_id,
        "deal_id": str(d["id"]),
        "position_id": str(d["positionId"]) if d.get("positionId") else None,
        "order_id": str(d["orderId"]) if d.get("orderId") else None,
        "symbol": d.get("symbol"),
        "deal_type": d.get("type"),
        "entry_type": d.get("entryType"),
        "volume": float(d["volume"]) if d.get("volume") is not None else None,
        "price": float(d["price"]) if d.get("price") is not None else None,
        "profit": float(d.get("profit") or 0.0),
        "commission": float(d.get("commission") or 0.0),
        "swap": float(d.get("swap") or 0.0),
        "deal_time": _parse_time(d.get("time")),
        "raw": d,
    }


def persist_deals(engine, account_id: str, deals: list[dict]) -> int:
    """Idempotent insert-ignore of MetaAPI deals. Returns newly stored rows.

    Deals are immutable at the broker, so conflicts on (account_id, deal_id)
    are silently skipped — re-persisting an overlapping lookback window every
    cycle is the intended usage.
    """
    ensure_table(engine)
    rows = [_row(account_id, d) for d in deals if d.get("id")]
    if not rows:
        return 0

    dialect = engine.dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:  # exotic dialect: plain insert, dupes will raise (caller isolates)
        from sqlalchemy import insert  # type: ignore[assignment]

    stmt = insert(broker_deals)
    if hasattr(stmt, "on_conflict_do_nothing"):
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["account_id", "deal_id"])

    with engine.begin() as conn:
        result = conn.execute(stmt, rows)
        return result.rowcount if result.rowcount and result.rowcount > 0 else 0
