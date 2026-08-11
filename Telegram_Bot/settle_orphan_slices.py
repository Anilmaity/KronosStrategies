"""One-off repair: settle legs orphaned on a closed signal.

Until 2026-08-11 a signal ended by a CHANNEL REPLY was dropped from the ':open'
set without settling its legs, and stamped with db.close_signal (status only).
reconcile_broker can never revisit a signal once it leaves that set, so those
legs stayed broker_state='filled' with NULL realized_pnl permanently, and the
signal's aggregate realized_pnl stayed NULL — even though the broker had closed
and paid them (signal 11001: +25.24 / +25.36 / +24.72 unrecorded).

close_order now settles what it closes, so this only repairs the existing
orphans. For each non-terminal leg of a closed signal it asks MetaAPI for the
settled deal and, when the broker confirms the position is closed, writes the
leg's realized PnL and re-stamps the signal's aggregate.

Idempotent, and refuses to guess: a leg the broker does NOT report as closed is
left untouched and reported.

    python settle_orphan_slices.py              # dry run (default)
    python settle_orphan_slices.py --commit
"""
from __future__ import annotations

import argparse
import os
import sys

import psycopg2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _connect():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def _broker_client():
    from metaapi_orders import MetaApiClient
    tok, acct = os.getenv("META_API_TOKEN", ""), os.getenv("META_ACCOUNT_ID", "")
    if not tok or not acct:
        raise SystemExit("META_API_TOKEN / META_ACCOUNT_ID missing — cannot verify "
                         "against the broker, refusing to guess.")
    return MetaApiClient(tok, acct, dry_run=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="write (default: dry run)")
    args = ap.parse_args()

    client = _broker_client()
    conn = _connect()
    cur = conn.cursor()

    cur.execute("""
        SELECT o.msg_id, o.tp_index, o.ticket_id, o.volume, s.close_reason
          FROM tg_orders o JOIN tg_signals s ON s.msg_id = o.msg_id
         WHERE s.status LIKE 'closed%%'
           AND o.broker_state NOT IN ('closed', 'cancelled')
         ORDER BY o.msg_id, o.tp_index
    """)
    orphans = cur.fetchall()
    if not orphans:
        print("no orphaned legs — nothing to settle")
        return

    settled, unverified = 0, 0
    # msg_id -> {ticket: pnl} for the legs settled in THIS run, so the dry run can
    # project the same aggregate --commit would write instead of reporting the
    # pre-repair sum (which reads as a wrong total and hides the real outcome).
    fresh: dict[int, dict[str, float]] = {}
    for msg_id, idx, ticket, vol, reason in orphans:
        deal = client.get_position_realized_pnl(str(ticket))
        if not (deal and deal.get("closed")):
            unverified += 1
            print(f"  signal {msg_id} TP{idx} ticket={ticket}: broker does NOT "
                  f"report it closed — left untouched")
            continue
        pnl = deal["realized_pnl"]
        print(f"  signal {msg_id} TP{idx} vol={float(vol):.2f} ticket={ticket}: "
              f"settle at {pnl:+.2f} USD")
        if args.commit:
            cur.execute("""
                UPDATE tg_orders
                   SET broker_state = 'closed', realized_pnl = %s, closed_at = NOW()
                 WHERE ticket_id = %s
            """, (pnl, str(ticket)))
        settled += 1
        fresh.setdefault(msg_id, {})[str(ticket)] = pnl

    for msg_id in sorted(fresh):
        # Legs already closed before this run, plus the ones we settled here.
        # Excluding our own tickets keeps the arithmetic identical in dry-run and
        # commit mode (in commit they are already 'closed' in the DB).
        cur.execute("""
            SELECT COALESCE(SUM(realized_pnl), 0), COUNT(*) FILTER (WHERE realized_pnl IS NULL)
              FROM tg_orders
             WHERE msg_id = %s AND broker_state = 'closed'
               AND NOT (ticket_id = ANY(%s))
        """, (msg_id, list(fresh[msg_id])))
        prior, missing = cur.fetchone()
        total = float(prior) + sum(fresh[msg_id].values())
        if missing:
            print(f"  signal {msg_id}: {missing} closed leg(s) still have NULL pnl — "
                  f"aggregate left alone")
            continue
        print(f"  signal {msg_id}: realized_pnl -> {total:+.2f} USD")
        if args.commit:
            cur.execute("UPDATE tg_signals SET realized_pnl = %s WHERE msg_id = %s",
                        (total, msg_id))

    if args.commit:
        conn.commit()
        print(f"\nCOMMITTED: settled {settled} leg(s) across "
              f"{len(fresh)} signal(s); {unverified} unverified")
    else:
        conn.rollback()
        print(f"\nDRY RUN: would settle {settled} leg(s) across "
              f"{len(fresh)} signal(s); {unverified} unverified. "
              f"Re-run with --commit.")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
