"""export_ground_truth.py — dump broker-verified live trades for parity runs.

Runs ON THE BOX (needs DB access). Emits the ground-truth CSV consumed by
``backtest.run_live_parity`` plus the ``--external-pnl`` companion CSV.

Why the joins look the way they do
----------------------------------
* **Times and prices come from ``broker_deals``, never from our own rows.**
  ``Order.price`` on an exit holds the stop *LEVEL*, not a fill, and
  ``Position.created_at`` is IST wall-clock mislabelled as UTC (+5:30). The
  broker archive carries real UTC fill times and real fill prices, so the
  window is filtered on ``deal_time`` and every price/time is taken from there.
* **Linkage is the ENTRY order's ticket** (``Order.broker_order_id`` ==
  MetaAPI ``positionId``) — the same edge ``fill_reconciler`` reconciles on.
* **The sliced-position guard is copied from ``fill_reconciler``.** A multi-TP
  copy trade is N broker positions mirrored as ONE row whose ENTRY order holds
  only slice 1's ticket. Trusting such a group restates a whole trade from a
  fraction of it (the 2026-08-11 accounting bug). Those rows are skipped and
  reported, never silently dropped.
* ``usd`` is broker truth: profit + ALL commissions + swap.

Outcome labels are taken from the exit ``Order.reason`` written by
``position_monitor`` and mapped onto the sim's vocabulary
(``TP`` | ``SL`` | ``TIME`` | ``TRAIL``) so ``outcome_agrees`` compares like
with like.

Usage (inside a container that has the strategies image + DB env):

    python export_ground_truth.py --start 2026-07-06 --end 2026-08-13 \
        --out /tmp/live_trades.csv --external-out /tmp/external_pnl.csv

No credentials are read or written; output contains only trade facts.
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402

from shared.models import (  # noqa: E402
    Session, Order, Position, Strategy, UserStrategy,
)
from shared import broker_deals as bd  # noqa: E402
from shared import models as _models  # noqa: E402

log = logging.getLogger("export_ground_truth")

# position_monitor writes Order.reason = the trigger label; the sim emits its
# own vocabulary. Map one onto the other so outcomes are comparable.
_OUTCOME = {
    "TARGET": "TP",
    "STOPLOSS": "SL",
    "TIME_EXIT": "TIME",
    "TRAIL": "TRAIL",
    "TRAILING_STOPLOSS_POINTS": "TRAIL",
    "TRAILING_STOPLOSS_SUM": "TRAIL",
}

_EPS_VOL = 0.005          # lot sizes are 2dp (same tolerance as fill_reconciler)

_CSV_COLS = ["strategy", "side", "entry_time", "entry_px", "exit_time",
             "exit_px", "outcome", "usd", "lots", "ticket", "account_id"]


def _parse_day(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def group_deals_from_archive(conn, start: datetime, end: datetime) -> dict:
    """position_id -> aggregated broker facts, over deals in [start, end).

    Mirrors fill_reconciler.group_deals but reads the persisted archive rather
    than a live MetaAPI response, and additionally keeps fill TIMES (which the
    reconciler has no use for but parity is measured on).
    """
    t = bd.broker_deals
    rows = conn.execute(
        select(t).where(t.c.deal_time >= start, t.c.deal_time < end)
    ).fetchall()

    out: dict[str, dict] = {}
    for d in rows:
        pid = str(d.position_id or "")
        if not pid:
            continue                     # balance operations carry no position
        g = out.setdefault(pid, {
            "account_id": d.account_id, "symbol": d.symbol,
            "entry_px": None, "entry_side": None, "entry_time": None,
            "in_vol": 0.0, "out_vol": 0.0, "out_pxvol": 0.0,
            "exit_time": None, "total_usd": 0.0,
        })
        g["total_usd"] += float(d.profit or 0) + float(d.commission or 0) \
            + float(d.swap or 0)

        if d.entry_type == "DEAL_ENTRY_IN":
            g["entry_px"] = float(d.price or 0)
            g["entry_side"] = "BUY" if d.deal_type == "DEAL_TYPE_BUY" else "SELL"
            g["entry_time"] = d.deal_time
            g["in_vol"] += float(d.volume or 0)
        elif d.entry_type == "DEAL_ENTRY_OUT":
            v = float(d.volume or 0)
            g["out_vol"] += v
            g["out_pxvol"] += v * float(d.price or 0)
            # A trade can close in slices; the LAST out-deal is the exit.
            if g["exit_time"] is None or d.deal_time > g["exit_time"]:
                g["exit_time"] = d.deal_time
    return out


def load_positions(sess, tickets: list[str]) -> dict[str, dict]:
    """ENTRY-order ticket -> {strategy, booked_vol, outcome, position_id}."""
    if not tickets:
        return {}

    entries = (
        # json_data is a generic JSON column (not JSONB), so ->> / .astext is
        # unavailable; pull the dict and read the tag in Python.
        sess.query(Order, Position, Strategy.name, Strategy.json_data)
        .join(Position, Order.position_id == Position.id)
        .join(UserStrategy, Position.user_strategy_id == UserStrategy.id)
        .join(Strategy, UserStrategy.strategy_id == Strategy.id)
        .filter(Order.condition == "ENTRY",
                Order.broker_order_id.in_(tickets))
        .all()
    )

    pos_ids = [row[1].id for row in entries]
    exits_by_pos: dict[object, str] = {}
    if pos_ids:
        for o in (sess.query(Order)
                  .filter(Order.position_id.in_(pos_ids),
                          Order.condition != "ENTRY")
                  .order_by(Order.created_at.asc())
                  .all()):
            # Last exit order wins (a position closes once; slices share a label)
            exits_by_pos[o.position_id] = str(o.reason or o.condition or "")

    out: dict[str, dict] = {}
    for order, pos, strat_name, json_data in entries:
        raw = exits_by_pos.get(pos.id, "")
        variation = (json_data or {}).get("variation") if isinstance(
            json_data, dict) else None
        out[str(order.broker_order_id)] = {
            # The sim identifies strategies by the module NAME constant
            # (KRONOS_S93_FVG_SCALP); the DB's Strategy.name is the human label
            # ("S93 FVG Scalp"). parity_harness matches on string equality, so
            # emit the variation tag the deploy scripts stamp into json_data.
            "strategy": variation or strat_name,
            "booked_vol": float(order.quantity or 0),
            "outcome": _OUTCOME.get(raw.upper(), raw.upper() or "UNKNOWN"),
            "entry_reason": str(order.reason or ""),
            "position_id": pos.id,
        }
    return out


def build_rows(grouped: dict, meta: dict, roster: list[str]) -> tuple:
    """Split broker-verified closed trades into (roster rows, external rows).

    Returns (rows, external, skipped) where `skipped` records every group that
    was excluded and why — silent truncation would read as clean coverage.
    """
    rows, external, skipped = [], [], []

    for ticket, g in sorted(grouped.items(),
                            key=lambda kv: (kv[1]["entry_time"] or
                                            datetime.max.replace(tzinfo=timezone.utc))):
        info = meta.get(ticket)

        # Still open, or the entry deal aged out of the window: not comparable.
        if g["out_vol"] <= 0 or not g["entry_px"] or g["entry_time"] is None:
            skipped.append((ticket, "no closing deal / entry outside window"))
            continue

        if info is None:
            # A real broker trade with no Position row: manual scalps and the
            # Telegram copy's extra slices land here. Real P&L the sim never
            # simulates, so it belongs in external_pnl, not in the sample.
            external.append({
                "utc_time": g["exit_time"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                "usd": round(g["total_usd"], 2),
                "note": f"unmatched broker position {ticket} "
                        f"({g['symbol']}, {g['out_vol']:g} lots)",
            })
            continue

        # Sliced-position guard (see module docstring).
        if info["booked_vol"] <= 0 or \
                abs(g["in_vol"] - info["booked_vol"]) > _EPS_VOL:
            skipped.append((
                ticket,
                f"sliced/partial: deals cover {g['in_vol']:.2f} lots but ENTRY "
                f"order booked {info['booked_vol']:.2f} ({info['strategy']})"))
            external.append({
                "utc_time": g["exit_time"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                "usd": round(g["total_usd"], 2),
                "note": f"sliced position {ticket} ({info['strategy']}) — "
                        f"excluded from parity sample, P&L still real",
            })
            continue

        # No exit Order => no outcome label to compare against the sim. In this
        # window that is the 2026-07-07 TEST_FILL_VALIDATION probe (0.01 lots,
        # 4 s, never closed by a trigger) — a real broker fill, but not a
        # strategy trade the sim can ever reproduce. Excluded, not hidden.
        if info["outcome"] == "UNKNOWN":
            skipped.append((
                ticket,
                f"no exit order / unmappable outcome "
                f"(entry reason={info['entry_reason'] or 'NONE'}, "
                f"{info['strategy']})"))
            external.append({
                "utc_time": g["exit_time"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                "usd": round(g["total_usd"], 2),
                "note": f"unlabelled position {ticket} "
                        f"({info['entry_reason'] or 'no reason'}) — "
                        f"excluded from parity sample",
            })
            continue

        record = {
            "strategy": info["strategy"],
            "side": g["entry_side"],
            "entry_time": g["entry_time"].astimezone(timezone.utc)
                           .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "entry_px": round(g["entry_px"], 5),
            "exit_time": g["exit_time"].astimezone(timezone.utc)
                          .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "exit_px": round(g["out_pxvol"] / g["out_vol"], 5),
            "outcome": info["outcome"],
            "usd": round(g["total_usd"], 2),
            "lots": round(g["in_vol"], 2),
            "ticket": ticket,
            "account_id": g["account_id"],
        }

        if roster and not any(k.upper() in record["strategy"].upper()
                              for k in roster):
            external.append({
                "utc_time": record["exit_time"],
                "usd": record["usd"],
                "note": f"{record['strategy']} (non-roster) position {ticket}",
            })
        else:
            rows.append(record)

    return rows, external, skipped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="UTC day, inclusive")
    ap.add_argument("--end", required=True, help="UTC day, exclusive")
    ap.add_argument("--out", required=True)
    ap.add_argument("--external-out", default=None)
    ap.add_argument("--roster", default="S93,S94,S99,S100",
                    help="comma-separated name substrings kept in the sample; "
                         "everything else is treated as external P&L")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    start, end = _parse_day(args.start), _parse_day(args.end)
    roster = [s.strip() for s in args.roster.split(",") if s.strip()]

    with _models.engine.connect() as conn:
        grouped = group_deals_from_archive(conn, start, end)
    log.info("broker_deals: %d distinct positions in [%s, %s)",
             len(grouped), args.start, args.end)

    sess = Session()
    try:
        meta = load_positions(sess, list(grouped))
    finally:
        sess.close()
    log.info("matched %d of them to Position rows", len(meta))

    rows, external, skipped = build_rows(grouped, meta, roster)

    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=_CSV_COLS)
        w.writeheader()
        w.writerows(rows)

    if args.external_out:
        with open(args.external_out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["utc_time", "usd", "note"])
            w.writeheader()
            w.writerows(sorted(external, key=lambda r: r["utc_time"]))

    per_strategy: dict[str, int] = defaultdict(int)
    per_account: dict[str, int] = defaultdict(int)
    for r in rows:
        per_strategy[r["strategy"]] += 1
        per_account[r["account_id"]] += 1

    print(f"\nwrote {len(rows)} roster trades -> {args.out}")
    for name, n in sorted(per_strategy.items(), key=lambda kv: -kv[1]):
        print(f"  {name:<34} {n:>4}")
    print("per account segment:")
    for acct, n in sorted(per_account.items(), key=lambda kv: -kv[1]):
        print(f"  {acct:<34} {n:>4}")
    print(f"\nexternal P&L rows: {len(external)}"
          f"  (sum {sum(r['usd'] for r in external):+.2f} USD)")
    print(f"skipped groups: {len(skipped)}")
    for ticket, why in skipped[:15]:
        print(f"  {ticket}: {why}")
    if len(skipped) > 15:
        print(f"  ... and {len(skipped) - 15} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
