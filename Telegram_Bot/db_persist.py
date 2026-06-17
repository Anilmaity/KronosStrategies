"""
TigerData persistence for NeymarGoldTrader Telegram signals.

Mirrors the Redis state into Postgres so signals + per-TP orders + lifecycle
events survive container restarts and can be queried alongside other Kronos
strategies. All writes are best-effort: a DB outage logs and returns without
raising, so trading is never blocked by persistence problems.

Env:
  TIGERDATA_URL  — full postgres DSN (preferred)
  TSDB_HOST/TSDB_PORT/TSDB_NAME/TSDB_USER/TSDB_PASS — fallback when DSN absent
  TG_DB_PREFIX   — table-name prefix (default "tg"). A second trader copy that
                   mirrors the same channel to a different account sets this to
                   its own value (e.g. "tg_neymar2") so it gets isolated
                   tg_*-shaped tables and never clobbers the primary instance's
                   rows (msg_id collides because both copies see the same channel
                   message ids).
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from psycopg2.extras import Json

log = logging.getLogger("tg-db")

_TIGERDATA_URL = os.getenv("TIGERDATA_URL")
_TSDB_HOST = os.getenv("TSDB_HOST", "tsdb")
_TSDB_PORT = int(os.getenv("TSDB_PORT", "5432"))
_TSDB_NAME = os.getenv("TSDB_NAME", "hft")
_TSDB_USER = os.getenv("TSDB_USER", "postgres")
_TSDB_PASS = os.getenv("TSDB_PASS", "postgres")

# Table namespace. The schema files / SQL below are written against the canonical
# "tg_" names; a non-default prefix rewrites both the DDL and every query so a
# parallel instance is fully isolated. Validate strictly — the prefix is
# interpolated straight into SQL identifiers (cannot be a bound parameter).
_PREFIX = (os.getenv("TG_DB_PREFIX", "tg") or "tg").strip()
if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*", _PREFIX):
    raise ValueError(f"invalid TG_DB_PREFIX: {_PREFIX!r} (must match [A-Za-z][A-Za-z0-9_]*)")

_T_SIGNALS = f"{_PREFIX}_signals"
_T_ORDERS = f"{_PREFIX}_orders"
_T_UPDATES = f"{_PREFIX}_signal_updates"

_SCHEMA_PATH = Path(__file__).parent / "init_schema.sql"


def _render_ddl(raw: str, prefix: str) -> str:
    """Rewrite the canonical tg_* DDL for the configured prefix.

    The default prefix ("tg") returns the DDL byte-for-byte so existing
    deployments are untouched. Any other prefix remaps the three base tables
    and their indexes/foreign keys, all of which begin with the literal "tg_".
    """
    if prefix == "tg":
        return raw
    return raw.replace("tg_", f"{prefix}_")


def _connect():
    if _TIGERDATA_URL:
        return psycopg2.connect(_TIGERDATA_URL)
    return psycopg2.connect(
        host=_TSDB_HOST, port=_TSDB_PORT,
        dbname=_TSDB_NAME, user=_TSDB_USER, password=_TSDB_PASS,
    )


def init_schema() -> bool:
    """Apply init_schema.sql. Returns True on success, False on failure."""
    if not _SCHEMA_PATH.exists():
        log.warning("[db] schema file missing: %s", _SCHEMA_PATH)
        return False
    try:
        ddl = _render_ddl(_SCHEMA_PATH.read_text(encoding="utf-8"), _PREFIX)
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(ddl)
        log.info("[db] schema initialised")
        return True
    except Exception as e:
        log.error("[db] schema init failed: %s", e)
        return False


def insert_signal(pos: dict, channel: str) -> None:
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {_T_SIGNALS} (
                    msg_id, channel, instrument, side,
                    entry_low, entry_high, entry_mid, sl, tps,
                    risk_pts, total_volume, status, raw, dry_run,
                    posted_at, opened_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (msg_id) DO UPDATE SET
                    status       = EXCLUDED.status,
                    total_volume = EXCLUDED.total_volume,
                    opened_at    = EXCLUDED.opened_at
                """,
                (
                    pos["msg_id"], channel, pos["instrument"], pos["side"],
                    pos.get("entry_lo"), pos.get("entry_hi"), pos.get("entry_mid"),
                    pos.get("sl"), pos.get("tps") or [],
                    pos.get("risk_pts"), pos.get("total_volume"),
                    pos.get("status", "submitted"), pos.get("raw"),
                    bool(pos.get("dry_run", False)),
                    pos.get("posted_at"),
                    pos.get("opened_at") or datetime.now(timezone.utc),
                ),
            )
            for o in pos.get("orders", []):
                cur.execute(
                    f"""
                    INSERT INTO {_T_ORDERS} (
                        ticket_id, msg_id, tp_index, kind,
                        volume, entry, sl, tp
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (ticket_id) DO NOTHING
                    """,
                    (
                        str(o["ticket_id"]), pos["msg_id"], o["tp_index"], o["kind"],
                        o["volume"], o["entry"], o["sl"], o["tp"],
                    ),
                )
    except Exception as e:
        log.error("[db] insert_signal msg_id=%s failed: %s", pos.get("msg_id"), e)


def record_update(msg_id: int, kind: str, payload: dict) -> None:
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {_T_UPDATES} (msg_id, type, payload) VALUES (%s, %s, %s)",
                (msg_id, kind, Json(payload)),
            )
    except Exception as e:
        log.error("[db] record_update msg_id=%s kind=%s failed: %s", msg_id, kind, e)


def update_sl(msg_id: int, new_sl: float) -> None:
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE {_T_SIGNALS} SET sl = %s WHERE msg_id = %s",
                (new_sl, msg_id),
            )
            cur.execute(
                f"UPDATE {_T_ORDERS} SET sl = %s WHERE msg_id = %s",
                (new_sl, msg_id),
            )
    except Exception as e:
        log.error("[db] update_sl msg_id=%s failed: %s", msg_id, e)
    record_update(msg_id, "sl_modify", {"sl": new_sl})


def record_fill(msg_id: int, tp_index: int, ticket_id: str, fill_price: float) -> None:
    """Mark a slice as filled at the broker (pending limit -> live position).

    Flips kind to 'market' so the close path closes the position instead of
    trying to cancel an order that no longer exists, and stamps the fill price.
    """
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {_T_ORDERS}
                   SET broker_state = 'filled', fill_price = %s, kind = 'market'
                 WHERE ticket_id = %s
                """,
                (round(fill_price, 3), str(ticket_id)),
            )
    except Exception as e:
        log.error("[db] record_fill msg_id=%s tp%s failed: %s", msg_id, tp_index, e)
    record_update(msg_id, "fill", {"tp_index": tp_index, "fill_price": fill_price})


def record_slice_close(msg_id: int, tp_index: int, ticket_id: str,
                       reason: str, pnl: float | None) -> None:
    """Record a single slice leaving the broker (closed if it had filled, or
    cancelled if it was still pending). pnl is the last live snapshot before the
    position vanished (None for a never-filled cancel)."""
    state = "cancelled" if reason == "cancelled" else "closed"
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {_T_ORDERS}
                   SET broker_state = %s, realized_pnl = %s, closed_at = NOW()
                 WHERE ticket_id = %s
                """,
                (state, pnl, str(ticket_id)),
            )
    except Exception as e:
        log.error("[db] record_slice_close msg_id=%s tp%s failed: %s", msg_id, tp_index, e)
    record_update(msg_id, "slice_close",
                  {"tp_index": tp_index, "reason": reason, "pnl": pnl})


def conclude_signal(msg_id: int, reason: str, realized_pnl: float | None) -> None:
    """Close a signal from broker truth, stamping the aggregate realized PnL."""
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {_T_SIGNALS}
                   SET status = %s, close_reason = %s, realized_pnl = %s, closed_at = NOW()
                 WHERE msg_id = %s
                """,
                (f"closed_{reason}", reason, realized_pnl, msg_id),
            )
    except Exception as e:
        log.error("[db] conclude_signal msg_id=%s failed: %s", msg_id, e)
    record_update(msg_id, "close", {"reason": reason, "realized_pnl": realized_pnl, "source": "broker"})


def load_open_signals() -> list[dict]:
    """Return every signal whose status is still open (not closed_*).

    Each dict has the same shape live_trader.place_order() produces, so it can
    be re-stored into the in-memory state store on boot.
    """
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT msg_id, instrument, side,
                       entry_low, entry_high, entry_mid, sl, tps,
                       risk_pts, total_volume, status, raw, dry_run,
                       posted_at, opened_at
                  FROM {_T_SIGNALS}
                 WHERE status NOT LIKE 'closed_%'
                 ORDER BY opened_at
                """
            )
            sig_rows = cur.fetchall()
            if not sig_rows:
                return []
            ids = [row[0] for row in sig_rows]
            cur.execute(
                f"""
                SELECT msg_id, ticket_id, tp_index, kind, volume, entry, sl, tp,
                       broker_state, fill_price
                  FROM {_T_ORDERS}
                 WHERE msg_id = ANY(%s)
                 ORDER BY msg_id, tp_index
                """,
                (ids,),
            )
            orders_by_msg: dict[int, list[dict]] = {}
            for (mid, tid, idx, kind, vol, entry, sl, tp, bstate, fprice) in cur.fetchall():
                orders_by_msg.setdefault(mid, []).append({
                    "tp_index": idx, "ticket_id": tid, "kind": kind,
                    "volume": float(vol), "entry": float(entry),
                    "sl": float(sl), "tp": float(tp),
                    "broker_state": bstate or ("filled" if kind == "market" else "pending"),
                    "fill_price": float(fprice) if fprice is not None else None,
                })

        positions: list[dict] = []
        for (mid, instr, side, e_lo, e_hi, e_mid, sl, tps,
             risk_pts, total_vol, status, raw, dry, posted_at, opened_at) in sig_rows:
            positions.append({
                "msg_id": mid,
                "instrument": instr,
                "side": side,
                "entry_lo": float(e_lo) if e_lo is not None else None,
                "entry_hi": float(e_hi) if e_hi is not None else None,
                "entry_mid": float(e_mid) if e_mid is not None else None,
                "sl": float(sl) if sl is not None else None,
                "tps": [float(x) for x in (tps or [])],
                "risk_pts": float(risk_pts) if risk_pts is not None else None,
                "total_volume": float(total_vol) if total_vol is not None else None,
                "status": status,
                "raw": raw,
                "dry_run": bool(dry),
                "posted_at": posted_at.isoformat() if posted_at else None,
                "opened_at": opened_at.isoformat() if opened_at else None,
                "orders": orders_by_msg.get(mid, []),
            })
        return positions
    except Exception as e:
        log.error("[db] load_open_signals failed: %s", e)
        return []


def close_signal(msg_id: int, reason: str) -> None:
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {_T_SIGNALS}
                   SET status = %s, close_reason = %s, closed_at = NOW()
                 WHERE msg_id = %s
                """,
                (f"closed_{reason}", reason, msg_id),
            )
    except Exception as e:
        log.error("[db] close_signal msg_id=%s failed: %s", msg_id, e)
    record_update(msg_id, "close", {"reason": reason})
