"""
TigerData persistence for NeymarGoldTrader Telegram signals.

Mirrors the Redis state into Postgres so signals + per-TP orders + lifecycle
events survive container restarts and can be queried alongside other Kronos
strategies. All writes are best-effort: a DB outage logs and returns without
raising, so trading is never blocked by persistence problems.

Env:
  TIGERDATA_URL  — full postgres DSN (preferred)
  TSDB_HOST/TSDB_PORT/TSDB_NAME/TSDB_USER/TSDB_PASS — fallback when DSN absent
"""
from __future__ import annotations

import json
import logging
import os
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

_SCHEMA_PATH = Path(__file__).parent / "init_schema.sql"


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
        ddl = _SCHEMA_PATH.read_text(encoding="utf-8")
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
                """
                INSERT INTO tg_signals (
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
                    """
                    INSERT INTO tg_orders (
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
                "INSERT INTO tg_signal_updates (msg_id, type, payload) VALUES (%s, %s, %s)",
                (msg_id, kind, Json(payload)),
            )
    except Exception as e:
        log.error("[db] record_update msg_id=%s kind=%s failed: %s", msg_id, kind, e)


def update_sl(msg_id: int, new_sl: float) -> None:
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE tg_signals SET sl = %s WHERE msg_id = %s",
                (new_sl, msg_id),
            )
            cur.execute(
                "UPDATE tg_orders SET sl = %s WHERE msg_id = %s",
                (new_sl, msg_id),
            )
    except Exception as e:
        log.error("[db] update_sl msg_id=%s failed: %s", msg_id, e)
    record_update(msg_id, "sl_modify", {"sl": new_sl})


def load_open_signals() -> list[dict]:
    """Return every signal whose status is still open (not closed_*).

    Each dict has the same shape live_trader.place_order() produces, so it can
    be re-stored into the in-memory state store on boot.
    """
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT msg_id, instrument, side,
                       entry_low, entry_high, entry_mid, sl, tps,
                       risk_pts, total_volume, status, raw, dry_run,
                       posted_at, opened_at
                  FROM tg_signals
                 WHERE status NOT LIKE 'closed_%'
                 ORDER BY opened_at
                """
            )
            sig_rows = cur.fetchall()
            if not sig_rows:
                return []
            ids = [row[0] for row in sig_rows]
            cur.execute(
                """
                SELECT msg_id, ticket_id, tp_index, kind, volume, entry, sl, tp
                  FROM tg_orders
                 WHERE msg_id = ANY(%s)
                 ORDER BY msg_id, tp_index
                """,
                (ids,),
            )
            orders_by_msg: dict[int, list[dict]] = {}
            for (mid, tid, idx, kind, vol, entry, sl, tp) in cur.fetchall():
                orders_by_msg.setdefault(mid, []).append({
                    "tp_index": idx, "ticket_id": tid, "kind": kind,
                    "volume": float(vol), "entry": float(entry),
                    "sl": float(sl), "tp": float(tp),
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
                """
                UPDATE tg_signals
                   SET status = %s, close_reason = %s, closed_at = NOW()
                 WHERE msg_id = %s
                """,
                (f"closed_{reason}", reason, msg_id),
            )
    except Exception as e:
        log.error("[db] close_signal msg_id=%s failed: %s", msg_id, e)
    record_update(msg_id, "close", {"reason": reason})
