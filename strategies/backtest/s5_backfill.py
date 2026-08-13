"""s5_backfill.py — populate the S5 cache from OANDA, one month at a time.

Phase 1 of docs/superpowers/specs/2026-08-12-5s-backtest-fidelity-design.md.

Must run where OANDA_API_KEY is available (the algorobos box). Memory stays
bounded because each month window is fetched, validated, written and freed
before the next one starts — the box has ~200 MB of free RAM and the live
trading stack must not be disturbed.

Usage (from strategies/):
    python -m backtest.s5_backfill --start 2026-07-06 --end 2026-08-13
    python -m backtest.s5_backfill --start 2024-01-01 --end 2026-08-13
    python -m backtest.s5_backfill --start 2026-07-01 --end 2026-07-31 --force

Exits non-zero if any window fails validation, so a bad backfill is loud.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Load OANDA env BEFORE the first fetch. s5_cache imports tsdb_reader lazily
# (inside _default_getter), so loading here is early enough.
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _candidate in (_REPO_ROOT / "tick_data_collector" / ".env",
                   _REPO_ROOT / "strategies" / ".env",
                   _REPO_ROOT / ".env"):
    if _candidate.exists():
        load_dotenv(_candidate)
        break

from backtest import s5_cache  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("s5_backfill")


def _parse_day(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def backfill(symbol: str, start: datetime, end: datetime,
             base: str | None, force: bool, dry_run: bool,
             sleep_s: float, page_size: int,
             allow_low_memory: bool = False) -> int:
    windows = s5_cache.month_windows(start, end)
    if not windows:
        log.error("empty window: end %s precedes start %s", end, start)
        return 2

    avail = s5_cache.available_memory_mb()
    if not dry_run and not allow_low_memory and \
            not s5_cache.memory_is_sufficient(avail):
        log.error("REFUSING to backfill: only %s MB RAM available (floor %s MB). "
                  "On 2026-08-12 a backfill on the production box wedged it and "
                  "took the live API down. Run this on a dev machine, or pass "
                  "--allow-low-memory if you accept the risk.",
                  avail, s5_cache.MEMORY_FLOOR_MB)
        return 3

    log.info("=== S5 backfill %s | %s -> %s | %d month window(s) | "
             "page=%d | avail=%s MB ===",
             symbol, start.date(), end.date(), len(windows), page_size, avail)

    failures: list[str] = []
    for w_start, w_end in windows:
        key = f"{w_start:%Y-%m}"

        if not force and s5_cache.window_is_cached(symbol, w_start, w_end,
                                                  base=base):
            log.info("[%s] already cached — skip", key)
            continue

        if dry_run:
            log.info("[%s] DRY-RUN would fetch %s -> %s", key, w_start, w_end)
            continue

        # Stream straight to the partition: peak memory is one page, never the
        # window (2026-08-12 incident).
        rows = s5_cache.stream_s5(
            symbol, w_start, w_end,
            sink=s5_cache.partition_sink(symbol, base=base),
            page_size=page_size, sleep_s=sleep_s)

        if rows == 0:
            log.warning("[%s] OANDA returned no candles", key)
            failures.append(f"{key}: no candles")
            continue

        # Validate by reading the window back rather than holding it during the
        # fetch; a month frame is ~25 MB, an order of magnitude below the dict
        # accumulation that caused the incident.
        df = s5_cache.load_s5(symbol, w_start, w_end, base=base)
        problems = s5_cache.validate_s5(df)
        cov = s5_cache.coverage_pct(df, w_start, w_end)
        log.info("[%s] %d rows streamed | %s -> %s | wall-clock coverage %.1f%%",
                 key, rows, df["time"].iloc[0], df["time"].iloc[-1], cov * 100)

        if problems:
            for p in problems:
                log.error("[%s] VALIDATION: %s", key, p)
            failures.append(f"{key}: {'; '.join(problems)}")
        else:
            log.info("[%s] written to %s", key,
                     s5_cache.partition_path(symbol, w_start, base=base))
        del df

    if failures:
        log.error("=== %d window(s) FAILED ===", len(failures))
        for f in failures:
            log.error("  %s", f)
        return 1

    log.info("=== backfill complete: %s ===",
             ", ".join(s5_cache.cached_months(symbol, base=base)))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="XAU_USD")
    ap.add_argument("--start", required=True, type=_parse_day)
    ap.add_argument("--end", required=True, type=_parse_day)
    ap.add_argument("--base", default=None,
                    help="cache root (default: backtest/results/bars_cache/s5)")
    ap.add_argument("--force", action="store_true",
                    help="refetch months already cached")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.15,
                    help="courtesy delay between OANDA pages")
    ap.add_argument("--page-size", type=int, default=2000,
                    help="candles per OANDA request (max 5000); lower = less "
                         "peak memory per page")
    ap.add_argument("--allow-low-memory", action="store_true",
                    help="override the RAM floor (see the 2026-08-12 incident)")
    args = ap.parse_args()
    return backfill(args.symbol, args.start, args.end, args.base,
                    args.force, args.dry_run, args.sleep, args.page_size,
                    args.allow_low_memory)


if __name__ == "__main__":
    sys.exit(main())
