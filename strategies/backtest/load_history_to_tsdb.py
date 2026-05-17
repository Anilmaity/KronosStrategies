"""
Load .history_data/<symbol>/*.json tick files into TimescaleDB `ltp` table.

Idempotent: relies on the (symbol, time) primary key — re-runs skip overlap.
"""
from __future__ import annotations

import glob
import json
import os
import sys
import time
from io import StringIO

import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cache_loader import _flatten

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, "..", "..", ".env"))

SYMBOL = "XAU_USD"
HIST_DIR = os.path.normpath(os.path.join(HERE, "..", "..", ".history_data", SYMBOL))


def connect():
    url = os.getenv("TIGERDATA_URL")
    if not url:
        raise RuntimeError("TIGERDATA_URL not set")
    return psycopg2.connect(url)


def load_file_rows(path: str) -> list[tuple[str, str, float]]:
    with open(path, "r") as fh:
        data = json.load(fh)
    rows = []
    seen = set()
    for rec in _flatten(data):
        t = rec.get("time")
        p = rec.get("price")
        if t is None or p is None:
            continue
        if t in seen:
            continue
        seen.add(t)
        rows.append((t, SYMBOL, float(p)))
    return rows


def copy_rows(conn, rows: list[tuple[str, str, float]]) -> int:
    if not rows:
        return 0
    cur = conn.cursor()
    cur.execute("CREATE TEMP TABLE _stage (time TIMESTAMPTZ, symbol TEXT, ltp DOUBLE PRECISION) ON COMMIT DROP")
    buf = StringIO()
    for t, s, p in rows:
        buf.write(f"{t}\t{s}\t{p}\n")
    buf.seek(0)
    cur.copy_from(buf, "_stage", columns=("time", "symbol", "ltp"))
    cur.execute(
        "INSERT INTO ltp (time, symbol, ltp) "
        "SELECT time, symbol, ltp FROM _stage "
        "ON CONFLICT (symbol, time) DO NOTHING"
    )
    inserted = cur.rowcount
    conn.commit()
    return inserted


def main():
    files = sorted(glob.glob(os.path.join(HIST_DIR, "*.json")))
    print(f"Found {len(files)} files in {HIST_DIR}")
    if not files:
        return

    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM ltp WHERE symbol=%s", (SYMBOL,))
    before = cur.fetchone()[0]
    print(f"ltp rows before: {before:,}")

    t0 = time.time()
    total_read = 0
    total_inserted = 0
    for i, fp in enumerate(files, 1):
        try:
            rows = load_file_rows(fp)
        except Exception as e:
            print(f"  [skip] {os.path.basename(fp)}: {e}")
            continue
        ins = copy_rows(conn, rows)
        total_read += len(rows)
        total_inserted += ins
        if i % 20 == 0 or i == len(files):
            elapsed = time.time() - t0
            rate = total_read / elapsed if elapsed > 0 else 0
            print(f"  [{i:3d}/{len(files)}] {os.path.basename(fp):20s} read={len(rows):6d} ins={ins:6d} | total read={total_read:,} ins={total_inserted:,} ({rate:,.0f} rec/s)")

    cur.execute("SELECT COUNT(*), MIN(time), MAX(time) FROM ltp WHERE symbol=%s", (SYMBOL,))
    cnt, mn, mx = cur.fetchone()
    print(f"\nDone in {time.time()-t0:.1f}s")
    print(f"ltp rows after:  {cnt:,}  (+{cnt-before:,})")
    print(f"Range: {mn} -> {mx}")
    conn.close()


if __name__ == "__main__":
    main()
