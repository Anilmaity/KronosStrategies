"""
Completeness validation for the oanda_tick_lib tick cache.

The cache naming convention is:  <day>_<month>_<year>.json
Each file covers the 24-hour IST window ENDING at midnight on that date,
so file "7_4_2026.json" holds IST data for April 6 (not April 7).

Expected candles per trading day
─────────────────────────────────
  Full 24 h                              17 280 S5 candles
  Minus daily maintenance (2:30–3:30 IST)  − 720
  ──────────────────────────────────────────────────
  Normal trading day                     16 560

  Monday files cover the IST-Sunday window (market re-opens ~2:30 AM IST).
  Only ~21.5 h of trading is available, so the threshold is relaxed to 60 %.

Weekend / holiday rules
────────────────────────
  Saturday filename  →  "weekend"   (skip)
  Sunday   filename  →  "weekend"   (skip)
  Known holiday      →  "holiday"   (skip)
  All other days     →  compare candle count to expected
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

import pandas as pd

from . import _cache
from ._exceptions import CacheReadError

# ── Candle constants ─────────────────────────────────────────────────────────
FULL_DAY_CANDLES        = 17_280          # 24 h ÷ 5 s
MAINTENANCE_CANDLES     = 720             # 2:30–3:30 IST  (60 min ÷ 5 s)
TRADING_DAY_CANDLES     = FULL_DAY_CANDLES - MAINTENANCE_CANDLES   # 16 560

# Monday files include the weekend gap; relax the threshold
_NORMAL_THRESHOLD = 0.85                  # ≥ 85 % of 16 560 → ok
_MONDAY_THRESHOLD = 0.60                  # ≥ 60 % of 16 560 → ok  (~9 936 candles)

# ── Known OANDA holidays for XAU/USD ─────────────────────────────────────────
# Gold market is closed (or effectively halted) on these dates.
# Extend by passing extra_holidays to validate_range().
OANDA_HOLIDAYS: set[date] = {
    # 2024
    date(2024, 1, 1),    # New Year's Day
    date(2024, 3, 29),   # Good Friday
    date(2024, 12, 25),  # Christmas
    # 2025
    date(2025, 1, 1),
    date(2025, 4, 18),   # Good Friday
    date(2025, 12, 25),
    # 2026
    date(2026, 1, 1),
    date(2026, 4, 3),    # Good Friday
    date(2026, 12, 25),
    # 2027
    date(2027, 1, 1),
    date(2027, 3, 26),   # Good Friday
    date(2027, 12, 25),
}

Status = Literal["ok", "partial", "empty", "missing", "weekend", "holiday"]


@dataclass
class DayReport:
    date:     date
    status:   Status
    candles:  int          # actual candle groups in cache (0 if absent)
    expected: int          # expected candle groups (0 for weekend / holiday)
    path:     str | None   # absolute cache path (None for weekend / holiday)

    @property
    def pct(self) -> float | None:
        """Percentage of expected candles present; None when expected == 0."""
        if not self.expected:
            return None
        return round(100.0 * self.candles / self.expected, 1)

    def __str__(self) -> str:
        icon = _ICON[self.status]
        if self.status in ("weekend", "holiday"):
            return f"{self.date}  {icon}  {self.status}"
        pct = f"{self.pct:.1f}%" if self.pct is not None else "—"
        return (
            f"{self.date}  {icon}  {self.status:<8}  "
            f"{self.candles:>6,} / {self.expected:>6,}  ({pct})"
        )


_ICON: dict[Status, str] = {
    "ok":      "✓",
    "partial": "~",
    "empty":   "!",
    "missing": "✗",
    "weekend": "─",
    "holiday": "H",
}


# ── Core validation ───────────────────────────────────────────────────────────

def validate_day(
    d: date,
    cache_dir: str,
    instrument: str,
    holidays: set[date],
) -> DayReport:
    """Return a DayReport for a single calendar date."""

    # Weekday 5 = Saturday, 6 = Sunday
    if d.weekday() >= 5:
        return DayReport(date=d, status="weekend", candles=0, expected=0, path=None)

    if d in holidays:
        return DayReport(date=d, status="holiday", candles=0, expected=0, path=None)

    path = _cache.cache_path(cache_dir, instrument, d)

    try:
        data = _cache.load(path)
    except CacheReadError:
        data = None   # corrupted file → treat as missing so user re-fetches

    if data is None:
        return DayReport(date=d, status="missing", candles=0,
                         expected=TRADING_DAY_CANDLES, path=path)

    n = len(data)
    if n == 0:
        return DayReport(date=d, status="empty", candles=0,
                         expected=TRADING_DAY_CANDLES, path=path)

    # Monday files only contain ~21.5 h of trading (weekend re-open gap)
    threshold = _MONDAY_THRESHOLD if d.weekday() == 0 else _NORMAL_THRESHOLD
    status: Status = "ok" if n >= int(TRADING_DAY_CANDLES * threshold) else "partial"

    return DayReport(date=d, status=status, candles=n,
                     expected=TRADING_DAY_CANDLES, path=path)


def validate_range(
    start: str,
    end: str,
    cache_dir: str,
    instrument: str = "XAU_USD",
    extra_holidays: set[date] | None = None,
) -> list[DayReport]:
    """
    Validate every calendar day in [start, end].

    Parameters
    ----------
    start / end       : date strings understood by pd.date_range  (e.g. "2026-04-01")
    cache_dir         : root of the cache tree  (e.g. "./Tick_Data_Generator/cache_data")
    instrument        : OANDA instrument code   (default "XAU_USD")
    extra_holidays    : additional dates to treat as closed; merged with OANDA_HOLIDAYS
    """
    holidays = OANDA_HOLIDAYS | (extra_holidays or set())

    try:
        dates = pd.date_range(start=start, end=end)
    except Exception as exc:
        raise ValueError(f"Invalid date range {start!r} → {end!r}: {exc}") from exc

    return [validate_day(d.date(), cache_dir, instrument, holidays) for d in dates]
