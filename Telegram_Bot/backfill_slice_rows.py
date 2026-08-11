"""One-off backfill: rebuild clubbed Telegram dashboard rows as per-slice rows.

Before 2026-08-11 each multi-TP signal was mirrored into ONE apis_position of
the summed volume, whose ENTRY order carried only slice 1's broker ticket. Two
consequences, both visible live:

  * the Orders tab showed a 0.12 trade that never existed instead of the four
    real 0.03 broker trades;
  * fill_reconciler, matching on that single ticket, restated the whole signal
    from slice 1 (signal 10985: the real -$80.52 was rewritten to -$19.65,
    which also under-fed the manager's kill-switch).

This script rebuilds those rows from broker truth: one apis_position per
tg_orders slice, each with its own volume, fill price, MetaAPI position id and
settled realized PnL (pulled from history-deals, falling back to the slice's
recorded figure). Old clubbed rows are backed up to JSON, then deleted.

Idempotent: a signal whose rows already match its slice count is left alone.

    python backfill_slice_rows.py              # dry run (default)
    python backfill_slice_rows.py --commit
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone

import psycopg2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from apis_persist import (CONTRACT_SIZE, CURRENCYPAIR_ID, SYMBOL,  # noqa: E402
                          USER_BROKER_ID, USER_STRATEGY_ID)

BACKUP_DIR = os.getenv("BACKFILL_BACKUP_DIR", "/tmp")


def _connect():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def _broker_client():
    """Live MetaAPI client for settled-deal lookups, or None if unavailable."""
    try:
        from metaapi_orders import MetaApiClient
        tok, acct = os.getenv("META_API_TOKEN", ""), os.getenv("META_ACCOUNT_ID", "")
        if not tok or not acct:
            return None
        return MetaApiClient(tok, acct, dry_run=False)
    except Exception as exc:                                  # pragma: no cover
        print(f"  ! broker client unavailable ({exc}) — using recorded PnL")
        return None


def load_signals(cur):
    """Signals that were mirrored to the dashboard, with their broker slices."""
    cur.execute("""
        SELECT s.msg_id, s.side, s.entry_mid, s.close_reason, s.realized_pnl
          FROM tg_signals s
         WHERE EXISTS (SELECT 1 FROM tg_orders o WHERE o.msg_id = s.msg_id)
         ORDER BY s.msg_id
    """)
    signals = []
    for msg_id, side, entry_mid, reason, tg_pnl in cur.fetchall():
        cur.execute("""
            SELECT tp_index, ticket_id, volume, realized_pnl, fill_price,
                   broker_state, closed_at, tp
              FROM tg_orders WHERE msg_id = %s ORDER BY tp_index
        """, (msg_id,))
        slices = [{
            "tp_index": r[0], "ticket": str(r[1]), "volume": float(r[2] or 0),
            "realized": float(r[3]) if r[3] is not None else None,
            "fill_price": float(r[4]) if r[4] is not None else None,
            "state": r[5], "closed_at": r[6], "tp": float(r[7] or 0),
        } for r in cur.fetchall()]
        signals.append({"msg_id": msg_id, "side": (side or "").lower(),
                        "entry_mid": float(entry_mid or 0), "reason": reason or "EXIT",
                        "tg_pnl": float(tg_pnl) if tg_pnl is not None else None,
                        "slices": slices})
    return signals


def existing_rows(cur, tickets):
    """apis_position rows reachable from any of this signal's slice tickets."""
    if not tickets:
        return []
    cur.execute("""
        SELECT DISTINCT p.id, p.quantity, p.realized_profit_loss, p.created_at
          FROM apis_order o JOIN apis_position p ON p.id = o.position_id
         WHERE o.condition = 'ENTRY' AND o.broker_order_id = ANY(%s)
           AND p.user_strategy_id = %s
         ORDER BY p.created_at
    """, (list(tickets), USER_STRATEGY_ID))
    return [{"id": str(r[0]), "quantity": float(r[1] or 0),
             "realized": float(r[2] or 0), "created_at": r[3]} for r in cur.fetchall()]


def settled(client, sl):
    """Broker-true (realized_usd, close_price, is_closed) for a slice.

    The broker outranks our recorded state: signal 11001's legs 2-4 are still
    'filled' in tg_orders (reconcile never settled them) while MetaAPI has them
    closed and paid. Trusting the stale state would mirror three phantom OPEN
    positions for trades that finished hours ago.
    """
    if client is not None:
        try:
            deal = client.get_position_realized_pnl(sl["ticket"])
            if deal and deal.get("closed"):
                return deal["realized_pnl"], deal.get("close_price"), True
        except Exception:
            pass
    return sl["realized"], None, sl["state"] == "closed"


def insert_slice(cur, sig, sl, realized_usd, close_px, is_closed, created_at):
    """One apis_position + ENTRY (+ close) order for a single broker slice."""
    is_long = sig["side"] == "buy"
    entry = sl["fill_price"] if sl["fill_price"] is not None else sig["entry_mid"]
    vol, pos_id = sl["volume"], str(uuid.uuid4())
    closed = is_closed and realized_usd is not None
    rpnl = round(realized_usd / CONTRACT_SIZE, 4) if closed else 0
    ltp = close_px if close_px is not None else (sl["tp"] if closed else entry)

    cur.execute("""
        INSERT INTO apis_position (
            id, created_at, modified_at, symbol,
            avg_buy_price, avg_sell_price, total_buy_quantity,
            quantity, profit_loss, profit_loss_percentage, ltp,
            realized_profit_loss, user_strategy_id, currencypair_id)
        VALUES (%s, %s, NOW(), %s, %s, %s, %s, %s, 0, 0, %s, %s, %s, %s)
    """, (pos_id, created_at, SYMBOL,
          entry if is_long else 0, 0 if is_long else entry,
          vol if is_long else 0,
          0 if closed else vol, round(ltp, 2), rpnl,
          USER_STRATEGY_ID, CURRENCYPAIR_ID))

    def _order(condition, side, price, reason, ticket):
        cur.execute("""
            INSERT INTO apis_order (
                id, created_at, modified_at, symbol, exchange, price, condition,
                side, quantity, amount, order_type, status, reason,
                broker_order_id, position_id, user_broker_id)
            VALUES (%s, %s, NOW(), %s, 'OANDA', %s, %s, %s, %s, %s,
                    'MARKET', 'EXECUTED', %s, %s, %s, %s)
        """, (str(uuid.uuid4()), created_at, SYMBOL, round(price, 2), condition,
              side, vol, round(price * vol, 2), reason, ticket, pos_id,
              USER_BROKER_ID))

    _order("ENTRY", "BUY" if is_long else "SELL", entry, "tg_entry", sl["ticket"])
    if closed:
        _order("TARGET" if rpnl >= 0 else "STOPLOSS",
               "SELL" if is_long else "BUY", ltp, sig["reason"], "")
    return pos_id, rpnl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="write (default: dry run)")
    args = ap.parse_args()

    client = _broker_client()
    conn = _connect()
    cur = conn.cursor()
    signals = load_signals(cur)
    backup, planned, skipped = [], 0, 0

    for sig in signals:
        tickets = [s["ticket"] for s in sig["slices"]]
        rows = existing_rows(cur, tickets)
        if not rows:
            continue                       # never mirrored — nothing to rebuild
        if len(rows) == len(sig["slices"]) and all(
                abs(r["quantity"]) < 1e-9 for r in rows):
            # already one row per slice
            skipped += 1
            continue

        created_at = rows[0]["created_at"] or datetime.now(timezone.utc)
        old_total = sum(r["realized"] for r in rows) * CONTRACT_SIZE
        truth = "n/a" if sig["tg_pnl"] is None else f"{sig['tg_pnl']:+.2f}"
        print(f"\nsignal {sig['msg_id']} ({sig['reason']}, {len(sig['slices'])} slices)")
        print(f"  old: {len(rows)} row(s), realized {old_total:+.2f} USD"
              f"  [bot's own record: {truth} USD]")

        for r in rows:
            cur.execute("SELECT id, condition, side, price, quantity, broker_order_id, "
                        "status, reason FROM apis_order WHERE position_id = %s", (r["id"],))
            backup.append({"position": r["id"], "quantity": r["quantity"],
                           "realized": r["realized"],
                           "orders": [list(map(str, o)) for o in cur.fetchall()]})

        new_total = 0.0
        for sl in sig["slices"]:
            realized_usd, close_px, is_closed = settled(client, sl)
            if args.commit:
                _, rpnl = insert_slice(cur, sig, sl, realized_usd, close_px,
                                       is_closed, created_at)
            else:
                rpnl = (round(realized_usd / CONTRACT_SIZE, 4)
                        if (is_closed and realized_usd is not None) else 0)
            new_total += rpnl * CONTRACT_SIZE
            print(f"    TP{sl['tp_index']} vol={sl['volume']:.2f} "
                  f"ticket={sl['ticket']} state={'closed' if is_closed else sl['state']} "
                  f"realized={0.0 if realized_usd is None else realized_usd:+.2f} USD")
        print(f"  new: {len(sig['slices'])} rows, realized {new_total:+.2f} USD")

        if args.commit:
            for r in rows:
                cur.execute("DELETE FROM apis_order WHERE position_id = %s", (r["id"],))
                cur.execute("DELETE FROM apis_position WHERE id = %s", (r["id"],))
        planned += 1

    if args.commit and backup:
        path = os.path.join(BACKUP_DIR, "apis_slice_backfill_backup.json")
        with open(path, "w") as fh:
            json.dump(backup, fh, indent=2, default=str)
        print(f"\nbacked up {len(backup)} old row(s) -> {path}")
        conn.commit()
        print(f"COMMITTED: rebuilt {planned} signal(s)")
    else:
        conn.rollback()
        print(f"\nDRY RUN: would rebuild {planned} signal(s) "
              f"({skipped} already per-slice). Re-run with --commit.")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
