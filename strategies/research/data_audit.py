"""
data_audit.py
-------------
Audit tick data coverage for a symbol over a date range.

Session hours (UTC), weekdays only:
  London : 07:00 – 11:00
  NY     : 12:00 – 16:00

A "gap" is the interval between two consecutive ticks that are:
  - Both on the same calendar date (UTC)
  - Both on a weekday (Mon–Fri)
  - Both within the SAME named session block (London OR NY)
  - The gap duration > 60 seconds

The full gap duration (in minutes) is summed — not just the excess over 60s.
"""
from __future__ import annotations

import sys
import os
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Session definitions: list of (start_hour, end_hour) — both inclusive/exclusive
# The session block is [start_hour, end_hour) in UTC.
# ---------------------------------------------------------------------------
_SESSIONS: list[tuple[int, int]] = [
    (7, 11),   # London
    (12, 16),  # New York
]


def _session_block(ts: pd.Timestamp) -> int | None:
    """Return a session-block index (0=London, 1=NY) for a UTC timestamp, or None."""
    h = ts.hour + ts.minute / 60 + ts.second / 3600
    for idx, (start, end) in enumerate(_SESSIONS):
        if start <= h < end:
            return idx
    return None


def audit_ticks(
    symbol: str,
    start,
    end,
    *,
    ticks: pd.DataFrame | None = None,
    cache_dir: str | None = None,
) -> dict[str, Any]:
    """Audit tick data coverage for *symbol* over [start, end].

    Parameters
    ----------
    symbol : str
        Instrument identifier (e.g. "XAU_USD").
    start, end : str | datetime | pd.Timestamp
        UTC window boundaries (inclusive).
    ticks : pd.DataFrame, optional
        Pre-built DataFrame with columns ["time", "price"] (tz-aware UTC).
        If supplied, *symbol* / *cache_dir* are ignored for data loading.
    cache_dir : str, optional
        Override the default cache directory used by cache_loader.

    Returns
    -------
    dict with keys:
        rows            : int   — ticks in window
        first_tick      : pd.Timestamp | None
        last_tick       : pd.Timestamp | None
        duplicate_count : int
        gaps_minutes    : float — total session-gap minutes
        gaps_by_month   : dict[str, float] — {YYYY-MM: minutes}
        gap_list        : list[(gap_start, gap_end, minutes)]
    """
    # ------------------------------------------------------------------
    # 1. Load data
    # ------------------------------------------------------------------
    if ticks is None:
        # Import here to avoid hard dependency in tests
        _here = os.path.dirname(os.path.abspath(__file__))
        _repo_root = os.path.normpath(os.path.join(_here, "..", ".."))
        if _repo_root not in sys.path:
            sys.path.insert(0, _repo_root)
        from strategies.backtest.cache_loader import load_ticks
        df_all = load_ticks(symbol, cache_dir)
    else:
        df_all = ticks.copy()

    # ------------------------------------------------------------------
    # 2. Coerce boundaries and filter window
    # ------------------------------------------------------------------
    t_start = pd.Timestamp(start, tz="UTC") if not isinstance(start, pd.Timestamp) else start
    t_end   = pd.Timestamp(end,   tz="UTC") if not isinstance(end,   pd.Timestamp) else end

    if t_start.tzinfo is None:
        t_start = t_start.tz_localize("UTC")
    if t_end.tzinfo is None:
        t_end = t_end.tz_localize("UTC")

    if df_all.empty:
        return _empty_result()

    # Ensure time column is tz-aware UTC
    if df_all["time"].dt.tz is None:
        df_all["time"] = df_all["time"].dt.tz_localize("UTC")

    mask = (df_all["time"] >= t_start) & (df_all["time"] <= t_end)
    window = df_all.loc[mask].sort_values("time").reset_index(drop=True)

    if window.empty:
        return _empty_result()

    # ------------------------------------------------------------------
    # 3. Basic stats
    # ------------------------------------------------------------------
    rows            = len(window)
    first_tick      = window["time"].iloc[0]
    last_tick       = window["time"].iloc[-1]
    duplicate_count = rows - window["time"].nunique()

    # ------------------------------------------------------------------
    # 4. Gap detection
    # ------------------------------------------------------------------
    gap_list: list[tuple[pd.Timestamp, pd.Timestamp, float]] = []
    gaps_by_month: dict[str, float] = {}

    times = window["time"].values  # numpy datetime64[ns] array for speed
    times_ts = window["time"]      # pandas Series of Timestamps

    for i in range(1, rows):
        t_prev = times_ts.iloc[i - 1]
        t_curr = times_ts.iloc[i]

        gap_seconds = (t_curr - t_prev).total_seconds()
        if gap_seconds <= 60:
            continue  # not a qualifying gap

        # Both ticks must be on the same calendar date (UTC)
        date_prev = t_prev.date()
        date_curr = t_curr.date()
        if date_prev != date_curr:
            continue

        # Both ticks must be on a weekday (Monday=0 … Friday=4)
        if t_prev.weekday() >= 5:
            continue

        # Both ticks must be in the SAME session block
        block_prev = _session_block(t_prev)
        block_curr = _session_block(t_curr)
        if block_prev is None or block_curr is None or block_prev != block_curr:
            continue

        # Qualifying gap — record it
        gap_minutes = gap_seconds / 60.0
        gap_list.append((t_prev, t_curr, gap_minutes))

        month_key = t_prev.strftime("%Y-%m")
        gaps_by_month[month_key] = gaps_by_month.get(month_key, 0.0) + gap_minutes

    total_gaps_minutes = sum(g[2] for g in gap_list)

    return {
        "rows":            rows,
        "first_tick":      first_tick,
        "last_tick":       last_tick,
        "duplicate_count": duplicate_count,
        "gaps_minutes":    total_gaps_minutes,
        "gaps_by_month":   gaps_by_month,
        "gap_list":        gap_list,
    }


def _empty_result() -> dict[str, Any]:
    return {
        "rows":            0,
        "first_tick":      None,
        "last_tick":       None,
        "duplicate_count": 0,
        "gaps_minutes":    0.0,
        "gaps_by_month":   {},
        "gap_list":        [],
    }


# ---------------------------------------------------------------------------
# Step-3 CLI: run audit on real cache and report
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    START = "2025-11-22"
    END   = "2026-05-22"
    STOP_TRIGGER_MINUTES = 240.0  # 4 hours per calendar month

    print(f"\nXAU_USD Tick Data Audit  [{START} to {END}]")
    print("=" * 60)
    print("Loading cache (may take ~30–60 s) …")

    result = audit_ticks("XAU_USD", START, END)

    print(f"\n  rows            : {result['rows']:,}")
    print(f"  first_tick      : {result['first_tick']}")
    print(f"  last_tick       : {result['last_tick']}")
    print(f"  duplicate_count : {result['duplicate_count']:,}")
    print(f"  gaps_minutes    : {result['gaps_minutes']:.2f}  "
          f"({result['gaps_minutes']/60:.2f} h total)")

    print("\nPer-month session gap breakdown:")
    triggered_months: list[str] = []
    all_months = sorted(result["gaps_by_month"].keys())
    if all_months:
        for m in all_months:
            mins = result["gaps_by_month"][m]
            flag = "  *** STOP TRIGGER ***" if mins > STOP_TRIGGER_MINUTES else ""
            print(f"  {m}: {mins:7.2f} min  ({mins/60:.2f} h){flag}")
            if mins > STOP_TRIGGER_MINUTES:
                triggered_months.append(m)
    else:
        print("  (no qualifying session gaps found)")

    print(f"\nIndividual gap list ({len(result['gap_list'])} gaps):")
    if result["gap_list"]:
        for gs, ge, gm in result["gap_list"]:
            print(f"  {gs}  to  {ge}  :  {gm:.2f} min")
    else:
        print("  (none)")

    print("\n" + "=" * 60)
    if triggered_months:
        print(f"STOP-TRIGGER FIRED for months: {triggered_months}")
        print("=> Data quality concern — 90%-winrate claim may be invalidated.")
        sys.exit(1)
    else:
        print("STOP-TRIGGER: CLEAR — no calendar month exceeded 4 hours of "
              "session gaps.")
        print("Data coverage is acceptable for strategy development.")
