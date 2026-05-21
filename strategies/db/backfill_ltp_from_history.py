"""
backfill_ltp_from_history.py
----------------------------
Backfill the TimescaleDB `ltp` hypertable from the local JSON tick cache at
`.history_data/<SYMBOL>/D_M_YYYY.json`.

Live `tick_data_collector` only stores a rolling ~6-month window. The local
cache spans ~16 months and is what the offline backtests use. After running
this, the live DB has the same coverage backtests do, so live runners can be
warmed from real history instead of only post-2025-11-19 data.

Each JSON file is a list of chunks; each chunk is a list of `{"time", "price"}`
dicts. Inserts use `ON CONFLICT (symbol, time) DO NOTHING` so the script is
idempotent and won't disturb the rolling-window rows.

Usage:
  python db/backfill_ltp_from_history.py              # dry run (counts files/rows)
  python db/backfill_ltp_from_history.py --commit     # actually insert
  python db/backfill_ltp_from_history.py --commit --symbol XAU_USD --before 2025-11-19
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time as _time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from psycopg2.extras import execute_values
from shared.tsdb_reader import _connect

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HISTORY_DIR = REPO_ROOT / ".history_data"
BATCH_SIZE = 50_000


def _parse_filename(name: str) -> datetime | None:
    """`D_M_YYYY.json` -> datetime(YYYY, M, D). Returns None for non-matching names."""
    if not name.endswith(".json"):
        return None
    stem = name[:-5]
    parts = stem.split("_")
    if len(parts) != 3:
        return None
    try:
        d, m, y = (int(p) for p in parts)
        return datetime(y, m, d)
    except ValueError:
        return None


def _iter_rows(path: Path, symbol: str):
    """Yield (time_str, symbol, price) tuples from one history file."""
    with path.open() as f:
        data = json.load(f)
    for chunk in data:
        items = chunk if isinstance(chunk, list) else [chunk]
        for tick in items:
            t = tick.get("time")
            p = tick.get("price")
            if t is None or p is None:
                continue
            yield (t, symbol, float(p))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true",
                    help="Actually insert. Without this, prints file/row counts only.")
    ap.add_argument("--symbol", default="XAU_USD")
    ap.add_argument("--history-dir", default=str(DEFAULT_HISTORY_DIR))
    ap.add_argument("--before", default=None,
                    help="Only ingest files with filename date strictly < this YYYY-MM-DD. "
                         "Default: auto-detect (oldest DB row for this symbol).")
    ap.add_argument("--after", default=None,
                    help="Only ingest files with filename date >= this YYYY-MM-DD.")
    args = ap.parse_args()

    hist_dir = Path(args.history_dir) / args.symbol
    if not hist_dir.is_dir():
        print(f"[ERR] history dir not found: {hist_dir}")
        return 1

    # Resolve --before from DB if not explicit.
    conn = _connect()
    if args.before is None:
        cur = conn.cursor()
        cur.execute("SELECT MIN(time) FROM ltp WHERE symbol=%s", (args.symbol,))
        (db_min,) = cur.fetchone()
        cur.close()
        if db_min is None:
            before_dt = None
            print(f"[INFO] DB has no rows for {args.symbol} — ingesting ALL history files.")
        else:
            before_dt = datetime(db_min.year, db_min.month, db_min.day)
            print(f"[INFO] DB min for {args.symbol} = {db_min} -> ingesting files dated < {before_dt.date()}")
    else:
        before_dt = datetime.strptime(args.before, "%Y-%m-%d")
        print(f"[INFO] --before override: files dated < {before_dt.date()}")

    after_dt = datetime.strptime(args.after, "%Y-%m-%d") if args.after else None
    if after_dt:
        print(f"[INFO] --after filter: files dated >= {after_dt.date()}")

    # Select files.
    selected: list[tuple[datetime, Path]] = []
    for entry in os.listdir(hist_dir):
        d = _parse_filename(entry)
        if d is None:
            continue
        if before_dt is not None and d >= before_dt:
            continue
        if after_dt is not None and d < after_dt:
            continue
        selected.append((d, hist_dir / entry))
    selected.sort(key=lambda x: x[0])

    if not selected:
        print("[DONE] No files match the date window. Nothing to do.")
        conn.close()
        return 0

    print(f"[INFO] {len(selected)} files selected: {selected[0][0].date()} ... {selected[-1][0].date()}")

    if not args.commit:
        # Cheap count pass on first/last 3 files for a size estimate.
        sample = selected[:3] + selected[-3:]
        n_sample = sum(sum(1 for _ in _iter_rows(p, args.symbol)) for _, p in sample)
        avg = n_sample / max(len(sample), 1)
        est = int(avg * len(selected))
        print(f"[DRY RUN] ~{avg:,.0f} ticks/file (sampled {len(sample)} files) -> "
              f"~{est:,} ticks total estimated.")
        print("[DRY RUN] Re-run with --commit to insert.")
        conn.close()
        return 0

    conn.rollback()  # release the implicit txn from the MIN(time) query
    cur = conn.cursor()
    sql = "INSERT INTO ltp (time, symbol, ltp) VALUES %s ON CONFLICT (symbol, time) DO NOTHING"

    t_start = _time.time()
    total_seen = total_inserted = 0
    for i, (d, path) in enumerate(selected, 1):
        batch: list[tuple] = []
        file_seen = 0
        file_inserted = 0
        for row in _iter_rows(path, args.symbol):
            batch.append(row)
            file_seen += 1
            if len(batch) >= BATCH_SIZE:
                execute_values(cur, sql, batch, page_size=10_000)
                file_inserted += cur.rowcount
                conn.commit()
                batch.clear()
        if batch:
            execute_values(cur, sql, batch, page_size=10_000)
            file_inserted += cur.rowcount
            conn.commit()
            batch.clear()

        total_seen += file_seen
        total_inserted += file_inserted
        elapsed = _time.time() - t_start
        rate = total_seen / elapsed if elapsed > 0 else 0
        print(f"  [{i:>3}/{len(selected)}] {d.date()}  seen={file_seen:>7,}  "
              f"inserted={file_inserted:>7,}  total_ins={total_inserted:>10,}  "
              f"({rate:,.0f} ticks/s)")

    cur.close()
    conn.close()
    print(f"[DONE] files={len(selected)}  ticks_seen={total_seen:,}  "
          f"ticks_inserted={total_inserted:,}  elapsed={_time.time()-t_start:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
