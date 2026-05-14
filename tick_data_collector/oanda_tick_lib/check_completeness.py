"""
check_completeness.py
──────────────────────
Audit the tick data cache for a date range and print a status report.

Legend
  ✓  ok       — candle count within expected range
  ~  partial  — fewer candles than threshold (gap in data)
  !  empty    — cache file exists but contains 0 candles
  ✗  missing  — no cache file for this date
  ─  weekend  — Saturday or Sunday (market closed, skipped)
  H  holiday  — known OANDA holiday (market closed, skipped)

Expected candles per trading day: 16,560
  = 17,280 (full day S5) − 720 (daily 2:30–3:30 IST maintenance window)

Run from the project root:
    python oanda_tick_lib/check_completeness.py
"""

import os
import sys

# Allow running as a plain script from the project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Ensure UTF-8 output on Windows terminals that default to cp1252
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from oanda_tick_lib._validator import (
    validate_range,
    TRADING_DAY_CANDLES,
    MAINTENANCE_CANDLES,
    OANDA_HOLIDAYS,
    Status,
    DayReport,
)

# ── Config — edit these ───────────────────────────────────────────────────────
INSTRUMENT  = "XAU_USD"
CACHE_DIR   = os.path.join(
    os.path.dirname(__file__), "..", "Tick_Data_Generator", "cache_data"
)
START       = "2026-04-01"
END         = "2026-04-30"

# Add any extra closed dates specific to your account / broker
EXTRA_HOLIDAYS: set = set()
# EXTRA_HOLIDAYS = {date(2026, 5, 25)}   # example

# ── Styling ───────────────────────────────────────────────────────────────────
_WIDTH = 62

_ROW_FMT = {
    "trading": "  {date:<14}  {icon:>1}  {status:<8}  {candles:>7,} / {expected:>6,}  ({pct:>6})",
    "skip":    "  {date:<14}  {icon:>1}  {status}",
}


def _fmt_row(r: DayReport) -> str:
    if r.status in ("weekend", "holiday"):
        return _ROW_FMT["skip"].format(
            date=str(r.date), icon=_icon(r.status), status=r.status
        )
    pct = f"{r.pct:.1f}%" if r.pct is not None else "—"
    return _ROW_FMT["trading"].format(
        date=str(r.date),
        icon=_icon(r.status),
        status=r.status,
        candles=r.candles,
        expected=r.expected,
        pct=pct,
    )


def _icon(status: Status) -> str:
    return {"ok": "✓", "partial": "~", "empty": "!", "missing": "✗",
            "weekend": "─", "holiday": "H"}[status]


def _hr() -> str:
    return "─" * _WIDTH


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    reports = validate_range(
        START, END,
        cache_dir=CACHE_DIR,
        instrument=INSTRUMENT,
        extra_holidays=EXTRA_HOLIDAYS,
    )

    counts: dict[Status, int] = {
        "ok": 0, "partial": 0, "empty": 0,
        "missing": 0, "weekend": 0, "holiday": 0,
    }
    for r in reports:
        counts[r.status] += 1

    # ── Header ────────────────────────────────────────────────────
    print()
    print(_hr())
    print(f"  Data Completeness  ·  {INSTRUMENT}  ·  {START} → {END}")
    print(f"  Expected / trading day: {TRADING_DAY_CANDLES:,}"
          f"  (17,280 − {MAINTENANCE_CANDLES} maint. window 2:30–3:30 IST)")
    print(_hr())
    print(f"  {'Date':<14}  {'':>1}  {'Status':<8}  {'Candles':>7}   {'Expected':>6}   {'%':>6}")
    print(_hr())

    # ── Rows ──────────────────────────────────────────────────────
    for r in reports:
        print(_fmt_row(r))

    # ── Summary ───────────────────────────────────────────────────
    print(_hr())
    trading = counts["ok"] + counts["partial"] + counts["empty"] + counts["missing"]
    complete = counts["ok"]
    problems = counts["partial"] + counts["empty"] + counts["missing"]

    print(
        f"  Trading days: {trading}   "
        f"✓ ok: {counts['ok']}   "
        f"~ partial: {counts['partial']}   "
        f"! empty: {counts['empty']}   "
        f"✗ missing: {counts['missing']}"
    )
    print(
        f"  Skipped:  ─ weekend: {counts['weekend']}   H holiday: {counts['holiday']}"
    )

    if problems == 0:
        print(f"\n  All {complete} trading day(s) complete.")
    else:
        print(f"\n  {problems} day(s) need attention:")
        for r in reports:
            if r.status in ("partial", "empty", "missing"):
                hint = f"  → fetch: fetcher.fetch_day('{r.date}', force=True)"
                print(f"    {r.date}  [{r.status}]  {r.candles:,} candles")
                print(hint)

    print(_hr())
    print()

    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
