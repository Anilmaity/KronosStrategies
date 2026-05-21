"""
shared/event_gate.py
--------------------
v15e production macro-event gate -- the "E3" filter.

Per `TradingSkills/backtest/reports/EXP_V15E_FINAL_REPORT.md`, the new
deployable winner is:

    T_baseline | ANY weekday | drop=noSH | TNX_z20d_abs >= 0.50 | E3
    -> 78.3% OOS win-day @ 5.02 tpd, DD 94 (cf. v15d's DD 222).

The E3 filter skips any signal that fires within +/- window_hours of a
scheduled MAJOR US macro event -- FOMC, CPI, NFP, PCE, SPEECH (Powell /
Jackson Hole), or ECB rate decision. "Major" means importance >= 2 in the
TradingSkills `backtest/macro_events.py` calendar.

USAGE:
    from shared.event_gate import event_window_open
    if not event_window_open(now_utc, window_hours=2.0):
        return None       # skip -- inside +/-2h of a major event

CACHE:
- Event calendar is built once at module load (hand-encoded dates 2025-2026,
  same source as the TradingSkills macro_events module).
- No network / disk reads required. The list is short (~few hundred rows).

FAIL-OPEN:
- Any failure to build / query the calendar logs a WARNING and returns True
  (gate OPEN) -- it is better to take a trade and discover the issue at
  reconciliation than to silently block every Kronos strategy because a
  date-list module mis-imported.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

log = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# Tier-1 hard-coded FOMC dates (decision day = day 2 of each meeting)
# Source: Fed 2025 / 2026 calendar (publicly known schedule).
# Mirrors TradingSkills/backtest/macro_events.py
# ════════════════════════════════════════════════════════════════════════════
FOMC_DATES: list[dt.date] = [
    # 2025
    dt.date(2025, 1, 29),
    dt.date(2025, 3, 19),
    dt.date(2025, 5, 7),
    dt.date(2025, 6, 18),
    dt.date(2025, 7, 30),
    dt.date(2025, 9, 17),
    dt.date(2025, 10, 29),
    dt.date(2025, 12, 10),
    # 2026 (partial -- through May)
    dt.date(2026, 1, 28),
    dt.date(2026, 3, 18),
    dt.date(2026, 5, 6),
]


# Tier-1 hard-coded ECB decisions (8 per year, Thursdays)
ECB_DATES: list[dt.date] = [
    # 2025
    dt.date(2025, 1, 30),
    dt.date(2025, 3, 6),
    dt.date(2025, 4, 17),
    dt.date(2025, 6, 5),
    dt.date(2025, 7, 24),
    dt.date(2025, 9, 11),
    dt.date(2025, 10, 30),
    dt.date(2025, 12, 18),
    # 2026
    dt.date(2026, 1, 22),
    dt.date(2026, 3, 12),
    dt.date(2026, 4, 30),
]


# Known speech / set-piece events (Powell, Jackson Hole, etc.)
SPEECH_DATES: list[tuple[dt.date, int, str]] = [
    (dt.date(2025, 2, 11), 15, "Powell semi-annual testimony"),
    (dt.date(2025, 2, 12), 15, "Powell semi-annual testimony day 2"),
    (dt.date(2025, 7, 22), 14, "Powell testimony"),
    (dt.date(2025, 8, 22), 14, "Jackson Hole - Powell keynote"),
    (dt.date(2026, 2, 11), 15, "Powell semi-annual testimony"),
    (dt.date(2026, 2, 12), 15, "Powell semi-annual testimony day 2"),
]


# Override dict for known CPI / NFP exception dates (rule-of-thumb miss)
OVERRIDES_CPI: dict[int, dict[int, dt.date]] = {
    2025: {
        1:  dt.date(2025, 1, 15),
        2:  dt.date(2025, 2, 12),
        3:  dt.date(2025, 3, 12),
    },
    2026: {
        1:  dt.date(2026, 1, 14),
        2:  dt.date(2026, 2, 11),
        3:  dt.date(2026, 3, 11),
        4:  dt.date(2026, 4, 14),
        5:  dt.date(2026, 5, 13),
    },
}

OVERRIDES_NFP: dict[int, dict[int, dt.date]] = {
    2025: {
        1:  dt.date(2025, 1, 10),
        2:  dt.date(2025, 2, 7),
        3:  dt.date(2025, 3, 7),
    },
    2026: {
        1:  dt.date(2026, 1, 9),
        2:  dt.date(2026, 2, 6),
        3:  dt.date(2026, 3, 6),
        4:  dt.date(2026, 4, 3),
        5:  dt.date(2026, 5, 1),
    },
}


# ════════════════════════════════════════════════════════════════════════════
# Rule-of-thumb date builders
# ════════════════════════════════════════════════════════════════════════════
def _first_friday(year: int, month: int) -> dt.date:
    d = dt.date(year, month, 1)
    while d.weekday() != 4:  # Friday
        d += dt.timedelta(days=1)
    return d


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    d = dt.date(year, month, 1)
    while d.weekday() != weekday:
        d += dt.timedelta(days=1)
    return d + dt.timedelta(days=7 * (n - 1))


def _last_business_day(year: int, month: int) -> dt.date:
    if month == 12:
        d = dt.date(year + 1, 1, 1) - dt.timedelta(days=1)
    else:
        d = dt.date(year, month + 1, 1) - dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def _is_summer(d: dt.date) -> bool:
    if d.month < 3 or d.month > 11:
        return False
    if 4 <= d.month <= 10:
        return True
    return d.month == 3 and d.day >= 9 or d.month == 11 and d.day < 2


def _utc_hour_for_830_et(d: dt.date) -> int:
    """08:30 ET -> 12:30 UTC (summer) or 13:30 UTC (winter)."""
    return 12 if _is_summer(d) else 13


def _utc_hour_for_1400_et(d: dt.date) -> int:
    """14:00 ET -> 18:00 UTC (summer) or 19:00 UTC (winter)."""
    return 18 if _is_summer(d) else 19


# ════════════════════════════════════════════════════════════════════════════
# Build the master event table.  Only MAJOR events (importance >= 2):
#   FOMC=3, CPI=3, NFP=3, PCE=3, GDP=2, SPEECH=2, ECB=2.
# Skipped (importance 1, not used by E3): PPI, JOLTS, ADP, RETAIL, ISM.
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class _Event:
    time: pd.Timestamp     # UTC
    event_type: str
    importance: int


def _add(events: list[_Event], date: dt.date, hour: int, minute: int,
         event_type: str, importance: int) -> None:
    ts = pd.Timestamp(
        year=date.year, month=date.month, day=date.day,
        hour=hour, minute=minute, tz="UTC")
    events.append(_Event(time=ts, event_type=event_type, importance=importance))


def _build_event_table(year_from: int = 2025, year_to: int = 2026) -> pd.DataFrame:
    """Build the (time, event_type, importance) DataFrame for major events.

    Mirrors the TradingSkills `build_event_table(...)` but skips the
    importance==1 noise events because the E3 filter only uses major events
    (importance >= 2).
    """
    events: list[_Event] = []

    # FOMC decisions: 14:00 ET, importance 3
    for d in FOMC_DATES:
        if not (year_from <= d.year <= year_to):
            continue
        _add(events, d, _utc_hour_for_1400_et(d), 0, "FOMC", 3)

    # ECB: 12:15 UTC (rate) + 12:45 UTC (presser), importance 2
    for d in ECB_DATES:
        if not (year_from <= d.year <= year_to):
            continue
        _add(events, d, 12, 15, "ECB", 2)
        _add(events, d, 12, 45, "ECB", 2)

    # SPEECH (Powell / Jackson Hole), importance 2
    for d, h, _label in SPEECH_DATES:
        if not (year_from <= d.year <= year_to):
            continue
        _add(events, d, h, 0, "SPEECH", 2)

    # Monthly CPI / NFP / PCE / GDP (rule + override)
    for year in range(year_from, year_to + 1):
        last_m = 5 if year == 2026 else 12
        for m in range(1, last_m + 1):
            # CPI: 2nd Wed of month (or override). Importance 3.
            cpi_d = (OVERRIDES_CPI.get(year, {}).get(m)
                     or _nth_weekday(year, m, 2, 2))
            _add(events, cpi_d, _utc_hour_for_830_et(cpi_d), 30, "CPI", 3)

            # NFP: 1st Friday (or override). Importance 3.
            nfp_d = (OVERRIDES_NFP.get(year, {}).get(m)
                     or _first_friday(year, m))
            _add(events, nfp_d, _utc_hour_for_830_et(nfp_d), 30, "NFP", 3)

            # Core PCE: last business day. Importance 3.
            pce_d = _last_business_day(year, m)
            _add(events, pce_d, _utc_hour_for_830_et(pce_d), 30, "PCE", 3)

            # GDP: quarterly (advance / 2nd estimate). Importance 2.
            if m in (1, 4, 7, 10, 2, 5, 8, 11):
                gdp_d = _nth_weekday(year, m, 3, 4)  # 4th Thu
                _add(events, gdp_d, _utc_hour_for_830_et(gdp_d), 30, "GDP", 2)

    df = pd.DataFrame([
        dict(time=e.time, event_type=e.event_type, importance=e.importance)
        for e in events
    ])
    # Importance >= 2 already enforced above; filter defensively in case
    # someone adds a low-importance row.
    df = df[df["importance"] >= 2]
    df = df.drop_duplicates(subset=["time", "event_type"])
    df = df.sort_values("time").reset_index(drop=True)
    return df


# ════════════════════════════════════════════════════════════════════════════
# Module-level cache (built once on first call)
# ════════════════════════════════════════════════════════════════════════════
_cache_df: Optional[pd.DataFrame] = None
_cache_ts_ms: Optional["pd.Series"] = None      # int64 ms-since-epoch UTC
_cache_lock = threading.Lock()


def _ensure_cache() -> Optional[pd.DataFrame]:
    """Lazily build the event calendar on first use. Returns None on failure
    (which triggers fail-open behaviour in `event_window_open`)."""
    global _cache_df, _cache_ts_ms
    if _cache_df is not None:
        return _cache_df
    with _cache_lock:
        if _cache_df is not None:
            return _cache_df
        try:
            df = _build_event_table(2025, 2026)
            ts = (pd.to_datetime(df["time"], utc=True)
                  .values.astype("datetime64[ns]").astype("int64") // 1_000_000)
            _cache_df = df
            _cache_ts_ms = pd.Series(ts.astype("int64"))
            log.info("[event_gate] loaded major-event calendar rows=%d "
                     "first=%s last=%s",
                     len(df),
                     df["time"].iloc[0].isoformat() if len(df) else "-",
                     df["time"].iloc[-1].isoformat() if len(df) else "-")
            return _cache_df
        except Exception:
            log.exception("[event_gate] failed to build event calendar -- "
                          "FAIL OPEN")
            return None


# ════════════════════════════════════════════════════════════════════════════
# Public API
# ════════════════════════════════════════════════════════════════════════════
def event_window_open(now_utc, window_hours: float = 2.0) -> bool:
    """Return False if any major US macro event is within window_hours
    of now_utc (either side); True otherwise. Fails OPEN on data error.

    "Major" = importance >= 2 in the embedded calendar
    (FOMC, CPI, NFP, PCE, GDP, SPEECH, ECB).

    Parameters
    ----------
    now_utc : datetime
        Current time. If naive, assumed to be UTC.
    window_hours : float
        Half-width of the exclusion window in hours. Default 2.0 = E3.
    """
    if now_utc is None:
        return True
    try:
        if not isinstance(now_utc, pd.Timestamp):
            ts = pd.Timestamp(now_utc)
        else:
            ts = now_utc
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
    except Exception:
        log.exception("[event_gate] bad now_utc -- FAIL OPEN")
        return True

    df = _ensure_cache()
    if df is None or len(df) == 0 or _cache_ts_ms is None:
        return True   # fail-open

    try:
        now_ms = int(ts.value // 1_000_000)
        window_ms = int(window_hours * 3_600_000)
        # nearest event by |dt|
        diffs = (_cache_ts_ms - now_ms).abs()
        nearest_ms = int(diffs.min())
        is_open = nearest_ms > window_ms
        if not is_open:
            # Find the offending row for logging
            i = int(diffs.idxmin())
            log.debug("[event_gate] now=%s within %.1fh of %s (%s) -- CLOSED",
                      ts.isoformat(), window_hours,
                      df["event_type"].iloc[i],
                      df["time"].iloc[i].isoformat())
        return bool(is_open)
    except Exception:
        log.exception("[event_gate] query failed -- FAIL OPEN")
        return True


def _cli() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    now = datetime.now(timezone.utc)
    decision = event_window_open(now, window_hours=2.0)
    df = _ensure_cache()
    print(f"event_gate @ {now.isoformat()} window=+/-2h "
          f"-> {'OPEN' if decision else 'CLOSED'}")
    if df is not None:
        print(f"calendar rows = {len(df)}; "
              f"first={df['time'].iloc[0].isoformat()} "
              f"last={df['time'].iloc[-1].isoformat()}")
        print("\nBy type:")
        print(df["event_type"].value_counts().to_string())
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
