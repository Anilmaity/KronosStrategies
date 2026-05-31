"""
backfill_ltp.py
───────────────
One-off, idempotent historical backfill of OANDA S5 ticks into the shared
TigerData ``ltp`` hypertable.

It reuses the *exact* same fetch + synthetic-tick path as the live collector
(``oanda_tick_lib.OandaTickFetcher.fetch_day`` → ``split_candles``), so the
historical rows it writes are indistinguishable from rows the live
``main.py`` writer produces — same 4-point split, same UTC timestamps. Rows go
in via ``execute_values`` + ``ON CONFLICT DO NOTHING`` (the same pattern as
``main.py:insert_ticks``), so the run is fully idempotent and resumable: a
re-run skips already-present rows, and the OANDA day cache makes re-fetch cheap.

Defaults target the BTC_USD deep-history backfill described in
``docs/superpowers/specs/2026-05-31-btc-tick-data-design.md``.

Usage (run from the repo root)::

    python tick_data_collector/backfill_ltp.py                       # BTC_USD, 2025-01-01 → today
    python tick_data_collector/backfill_ltp.py --instrument XAG_USD  # any instrument
    python tick_data_collector/backfill_ltp.py --start 2025-06-01 --end 2025-06-30

Connection target comes from ``TIGERDATA_URL`` in ``.env`` (via the shared
``connect_db`` helper). OANDA credentials come from ``OANDA_API_KEY``.
"""

import argparse
import logging
import os
import sys
from datetime import datetime, date

import pandas as pd
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

# Make `oanda_tick_lib` / `utils` importable regardless of the CWD the user
# launches from (running as a script auto-adds this dir, but be explicit).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from oanda_tick_lib import OandaTickFetcher  # noqa: E402
from utils.db_utils import connect_db        # noqa: E402

# Windows consoles default to cp1252 and choke on the arrow / box-drawing chars
# used in the log messages below; force UTF-8 so the long run logs cleanly.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("backfill_ltp")

# Quiet the very chatty per-batch DEBUG/INFO lines from the fetch library; we do
# our own per-day progress logging below.
logging.getLogger("oanda_tick_lib").setLevel(logging.WARNING)

INSERT_SQL = "INSERT INTO ltp (time, ltp, symbol) VALUES %s ON CONFLICT DO NOTHING"

# Default cache root matches the live collector's layout; a per-instrument
# subfolder is created underneath. The cache is gitignored (see .gitignore).
DEFAULT_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "Tick_Data_Generator", "cache_data"
)


# ── DB insert (mirrors main.py:insert_ticks, batched for backfill volume) ──────
def insert_ticks(conn, ticks: list[dict], symbol: str, page_size: int = 10_000) -> int:
    """Bulk-insert flattened ticks for one day. Returns rows *attempted*.

    ``ON CONFLICT DO NOTHING`` makes this idempotent; the number of rows that
    were genuinely new is reported at the end via a before/after row count, so
    we do not rely on the (per-page, unreliable) ``cur.rowcount`` here.
    """
    rows = [
        (
            datetime.fromisoformat(t["time"].replace("Z", "+00:00")),
            float(t["price"]),
            symbol,
        )
        for t in ticks
    ]
    if not rows:
        return 0

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, INSERT_SQL, rows, page_size=page_size)
    conn.commit()
    return len(rows)


def symbol_count(conn, symbol: str) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ltp WHERE symbol = %s", (symbol,))
        return cur.fetchone()[0]


# ── CLI ────────────────────────────────────────────────────────────────────────
def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill OANDA S5 ticks into the ltp hypertable.")
    p.add_argument("--instrument", default="BTC_USD", help="OANDA instrument (default: BTC_USD)")
    p.add_argument("--start", default="2025-01-01", help="First calendar day, YYYY-MM-DD (default: 2025-01-01)")
    p.add_argument(
        "--end",
        default=date.today().isoformat(),
        help="Last calendar day, YYYY-MM-DD inclusive (default: today)",
    )
    p.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR, help="OANDA candle cache root")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    load_dotenv()  # populate OANDA_API_KEY / TIGERDATA_URL into os.environ

    api_key = os.getenv("OANDA_API_KEY")
    if not api_key:
        log.error("Set OANDA_API_KEY in environment or .env")
        return 1

    # BTC_USD is served by the same OANDA practice account as XAU/XAG.
    practice = os.getenv("OANDA_PRACTICE", "true").lower() != "false"

    try:
        days = pd.date_range(start=args.start, end=args.end)
    except Exception as exc:
        log.error("Invalid date range %s → %s: %s", args.start, args.end, exc)
        return 1
    if len(days) == 0:
        log.error("Empty date range %s → %s", args.start, args.end)
        return 1

    fetcher = OandaTickFetcher(
        api_key=api_key,
        instrument=args.instrument,
        cache_dir=args.cache_dir,
        practice=practice,
        request_timeout=30,
    )

    conn = connect_db()
    start_count = symbol_count(conn, args.instrument)

    log.info(
        "Backfill %s | %s → %s | %d calendar days | practice=%s | starting rows=%d",
        args.instrument, args.start, args.end, len(days), practice, start_count,
    )

    total_attempted = 0
    n_data = n_empty = n_error = 0

    for d in days:
        label = str(d.date())
        try:
            groups = fetcher.fetch_day(d)  # list[list[dict]] — cached or freshly fetched
        except Exception as exc:
            log.error("[ERR ] %s — fetch failed: %s", label, exc)
            n_error += 1
            continue

        if not groups:
            n_empty += 1
            log.info("[----] %s — no candles (market closed / holiday)", label)
            continue

        ticks = [tick for group in groups for tick in group]
        try:
            attempted = insert_ticks(conn, ticks, args.instrument)
        except psycopg2.Error as exc:
            log.error("[ERR ] %s — DB insert failed: %s", label, exc)
            try:
                conn.rollback()
            except Exception:
                pass
            if conn.closed:
                log.warning("Reconnecting DB…")
                conn = connect_db()
            n_error += 1
            continue

        total_attempted += attempted
        n_data += 1
        log.info(
            "[OK  ] %s — %d candle groups → %d tick rows (cumulative attempted: %d)",
            label, len(groups), attempted, total_attempted,
        )

    end_count = symbol_count(conn, args.instrument)
    conn.close()

    log.info("─" * 70)
    log.info(
        "DONE %s | days: %d with data / %d empty / %d error",
        args.instrument, n_data, n_empty, n_error,
    )
    log.info("Tick rows attempted (incl. dedup re-runs): %d", total_attempted)
    log.info(
        "Net new rows in ltp for %s: %d  (%d → %d)",
        args.instrument, end_count - start_count, start_count, end_count,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
