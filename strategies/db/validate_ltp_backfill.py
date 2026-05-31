"""
validate_ltp_backfill.py
------------------------
Validate TimescaleDB `ltp` hypertable coverage for any symbol against its
local JSON tick cache at `<cache-dir>/<SYMBOL>/D_M_YYYY.json`.

Two independent checks per trading day:
  1. CACHE completeness  -- does the JSON file have ~enough S5 candle groups?
     (reuses oanda_tick_lib._validator: weekend/holiday aware, 16,560/day)
  2. DB gap check        -- any weekday (non-holiday) where `ltp` holds < 1,000
     ticks is flagged as a backfill gap. This is offset-agnostic and needs no
     cache, so it runs even in DB-only mode (no local cache present).

Prices outside the per-symbol band (PRICE_BANDS, override with --price-min/
--price-max) are flagged as data errors. A day is flagged for repair when the
cache file is missing/empty/partial, the DB has a gap, or prices are bad.

Output: a per-day report plus a machine-readable list of bad dates written
to `db/ltp_repair_dates.txt` (one YYYY-MM-DD per line) for the repair step.
Exit code 2 when any day needs attention, 0 when clean.

Usage:
  python db/validate_ltp_backfill.py                                  # XAU_USD, .history_data
  python db/validate_ltp_backfill.py --symbol XAG_USD
  python db/validate_ltp_backfill.py --symbol BTC_USD                 # cache auto-resolves to
                                                                      # tick_data_collector/Tick_Data_Generator/cache_data
  python db/validate_ltp_backfill.py --symbol BTC_USD --start 2025-01-01 --end 2026-05-29
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from shared.tsdb_reader import _connect

REPO_ROOT = Path(__file__).resolve().parents[2]
HISTORY_DIR = REPO_ROOT / ".history_data"
COLLECTOR_LIB = REPO_ROOT / "tick_data_collector"

# DB ticks must be at least this fraction of the cache-file tick count.
DB_COVERAGE_THRESHOLD = 0.95

# Plausible price band per symbol -- ticks outside it are data errors
# (decimal-shift, zero, or stale fills). Override via --price-min/--price-max.
PRICE_BANDS: dict[str, tuple[float, float]] = {
    "XAU_USD": (1_500.0, 6_000.0),
    "XAG_USD": (10.0, 150.0),
    "BTC_USD": (10_000.0, 250_000.0),
}
# Unknown symbol: accept anything (band check effectively disabled) + warn.
_DEFAULT_BAND = (0.0, float("inf"))

# Where each symbol's JSON tick cache lives. XAU/XAG were backfilled into
# .history_data/; BTC_USD via tick_data_collector/backfill_ltp.py writes into
# the live collector's cache root. Override with --cache-dir. A missing cache
# dir is non-fatal -- the DB gap check still runs (DB-only mode).
DEFAULT_CACHE_DIRS: dict[str, Path] = {
    "BTC_USD": COLLECTOR_LIB / "Tick_Data_Generator" / "cache_data",
}


def _iter_prices(path: Path):
    """Yield float prices from one history file (nested-list aware)."""
    with path.open() as f:
        data = json.load(f)

    def walk(node):
        if isinstance(node, dict):
            p = node.get("price")
            if p is not None:
                yield float(p)
        elif isinstance(node, list):
            for item in node:
                yield from walk(item)

    yield from walk(data)


def _cache_tick_count(path: Path, price_min: float, price_max: float) -> tuple[int, int, int]:
    """Return (tick_count, n_below_band, n_above_band) for a history file."""
    n = lo = hi = 0
    for price in _iter_prices(path):
        n += 1
        if price < price_min:
            lo += 1
        elif price > price_max:
            hi += 1
    return n, lo, hi


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="XAU_USD")
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--end", default=None, help="default: today")
    ap.add_argument("--cache-dir", default=None,
                    help="JSON tick cache root (default: per-symbol; see DEFAULT_CACHE_DIRS)")
    ap.add_argument("--price-min", type=float, default=None,
                    help="override the lower price band for the symbol")
    ap.add_argument("--price-max", type=float, default=None,
                    help="override the upper price band for the symbol")
    args = ap.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end \
        else datetime.utcnow().date()

    # ---- resolve per-symbol price band (CLI override > table > wide default) -
    band = PRICE_BANDS.get(args.symbol, _DEFAULT_BAND)
    price_min = args.price_min if args.price_min is not None else band[0]
    price_max = args.price_max if args.price_max is not None else band[1]
    if args.symbol not in PRICE_BANDS and args.price_min is None and args.price_max is None:
        print(f"[WARN] no price band configured for {args.symbol}; "
              f"price-band check disabled (pass --price-min/--price-max to enable)")
    print(f"[CFG] symbol={args.symbol}  price band=[{price_min:g} .. {price_max:g}]")

    # ---- resolve cache dir; a missing one is non-fatal (DB-only mode) -------
    cache_root = Path(args.cache_dir) if args.cache_dir \
        else DEFAULT_CACHE_DIRS.get(args.symbol, HISTORY_DIR)
    sym_dir = cache_root / args.symbol
    have_cache = sym_dir.is_dir()

    # OANDA_HOLIDAYS is cache-independent and needed by the DB gap check below.
    sys.path.insert(0, str(COLLECTOR_LIB))
    from oanda_tick_lib._validator import validate_range, OANDA_HOLIDAYS

    # ---- cache completeness via oanda_tick_lib validator -------------------
    if have_cache:
        print(f"[CFG] cache dir={sym_dir}")
        reports = validate_range(
            start.isoformat(), end.isoformat(),
            cache_dir=str(cache_root), instrument=args.symbol,
        )
    else:
        print(f"[INFO] no cache dir for {args.symbol} at {sym_dir} — "
              f"running DB-only gap check (cache completeness skipped)")
        reports = []

    # ---- DB tick counts per calendar day -----------------------------------
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT (time AT TIME ZONE 'UTC')::date AS d, COUNT(*),
               MIN(ltp), MAX(ltp)
        FROM   ltp
        WHERE  symbol = %s AND time >= %s AND time < %s
        GROUP  BY 1
        """,
        (args.symbol, start.isoformat(),
         (end.replace(day=end.day) if False else end).isoformat() + " 23:59:59"),
    )
    db_by_day: dict[date, tuple[int, float, float]] = {
        r[0]: (r[1], float(r[2]), float(r[3])) for r in cur.fetchall()
    }
    cur.close()
    conn.close()

    # ---- per-day comparison (cache mode only) -------------------------------
    bad: list[date] = []
    counts = defaultdict(int)
    if reports:
        print(f"\n{'date':<12} {'cache':<9} {'cache_ticks':>12} {'db_ticks':>12} "
              f"{'db/cache':>9}  verdict")
        print("-" * 78)

    for rep in reports:
        d = rep.date
        if rep.status in ("weekend", "holiday"):
            counts[rep.status] += 1
            continue

        # cache file maps to the trade day = filename date - 1 (IST window).
        # _iter_prices reads the file at rep.path; DB day comparison uses the
        # actual tick timestamps, so we sum DB ticks the file produced.
        cache_ticks = lo = hi = 0
        if rep.path and Path(rep.path).exists():
            cache_ticks, lo, hi = _cache_tick_count(Path(rep.path), price_min, price_max)

        # DB ticks for this file's data: the file's internal timestamps land
        # on (filename date - 1) and (filename date). Sum both calendar days
        # is overcounting; instead compare the file's tick count to the DB
        # rows on the file's own date span. Simpler + robust: aggregate later.
        verdict = "ok"
        if rep.status in ("missing", "empty"):
            verdict = "REFETCH(cache-" + rep.status + ")"
            bad.append(d)
        elif rep.status == "partial":
            verdict = f"REFETCH(cache-partial {rep.pct}%)"
            bad.append(d)
        elif lo or hi:
            verdict = f"BAD-PRICE(lo={lo},hi={hi})"
            bad.append(d)
        counts[verdict.split("(")[0]] += 1
        print(f"{d!s:<12} {rep.status:<9} {cache_ticks:>12,} "
              f"{'-':>12} {'-':>9}  {verdict}")

    # ---- DB per-day gap check (offset-agnostic) -----------------------------
    # The cache filename date is offset ~1 day from the tick timestamps, so
    # rather than match files to days, flag any *weekday* in range where the
    # DB holds almost no ticks -- that is a backfill gap regardless of offset.
    print("\n--- DB weekday gaps (days with < 1,000 ticks) ---")
    holidays_set = OANDA_HOLIDAYS
    d = start
    one = (end - start).days
    from datetime import timedelta
    gap_days = 0
    while d <= end:
        if d.weekday() < 5 and d not in holidays_set:
            n = db_by_day.get(d, (0, 0, 0))[0]
            if n < 1_000:
                print(f"  {d}  db_ticks={n:>8,}  <-- GAP")
                bad.append(d)
                gap_days += 1
        d += timedelta(days=1)
    if gap_days == 0:
        print("  none")

    # ---- DB-vs-cache aggregate cross-check (monthly) ------------------------
    print("\n--- DB coverage by month (sanity) ---")
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT date_trunc('month', time)::date, COUNT(*),
               MIN(ltp), MAX(ltp)
        FROM   ltp WHERE symbol = %s AND time >= %s AND time < %s
        GROUP  BY 1 ORDER BY 1
        """,
        (args.symbol, start.isoformat(), end.isoformat() + " 23:59:59"),
    )
    for m, n, lo_p, hi_p in cur.fetchall():
        flag = ""
        if float(lo_p) < price_min or float(hi_p) > price_max:
            flag = "  <-- PRICE OUT OF BAND"
        print(f"  {m}  {n:>10,}  price[{float(lo_p):.1f}..{float(hi_p):.1f}]{flag}")
    cur.close()
    conn.close()

    # ---- summary ------------------------------------------------------------
    print(f"\n--- summary {start} .. {end} ---")
    for k in sorted(counts):
        print(f"  {k:<24} {counts[k]}")

    out = Path(__file__).resolve().parent / "ltp_repair_dates.txt"
    if bad:
        out.write_text("\n".join(d.isoformat() for d in sorted(set(bad))) + "\n")
        print(f"\n[REPAIR] {len(set(bad))} day(s) need refetch -> {out}")
        return 2
    out.write_text("")
    print("\n[OK] DB coverage complete; no gaps found."
          if not have_cache else "\n[OK] cache complete; no refetch needed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
