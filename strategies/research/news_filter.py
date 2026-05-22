"""
strategies/research/news_filter.py
------------------------------------
Standalone ForexFactory news-event filter for XAU/USD research strategies.

Purpose
-------
Block new trade entries within ±N minutes of a high-impact (red-folder) USD
news event.  All timestamps are tz-aware UTC.

Relationship to shared/event_gate.py
-------------------------------------
``shared/event_gate.py`` (the E3 filter) uses a hand-coded, hard-wired
calendar of *major* macro events (FOMC, CPI, NFP, PCE, ECB, SPEECH) with
hour-scale windows (default ±2 h).  It is a *production* gate baked into the
live runner.

``news_filter.py`` is different in three ways:

1. **Data source** – events come from a user-supplied ForexFactory CSV (scraped
   once, then cached as a fixture), not a hard-coded list.  That lets research
   notebooks use the full ForexFactory calendar including medium-impact events,
   seasonal variations, and years outside the hard-coded 2025-2026 range.

2. **Granularity** – minute-scale windows (default ±15 min) rather than
   hour-scale windows, which is appropriate for entry-timing research.

3. **Purpose** – pure research / backtesting utility.  It is intentionally
   *not* a live gate (no fail-open semantics, no thread-safe caching, no
   network refresh).  The live E3 gate in ``event_gate.py`` handles production.

ForexFactory CSV format
-----------------------
Expected columns (header names are case-insensitive):

    datetime_utc   : ISO-8601 string, UTC (e.g. "2026-01-10 13:30:00")
    currency       : 3-letter currency code (e.g. "USD", "EUR")
    impact         : Impact level string, one of "High", "Medium", "Low", "Holiday"
    event_name     : Free-text event description (not filtered on)

Example header line::

    datetime_utc,currency,impact,event_name

The CSV is assumed to carry all times in UTC.  If your ForexFactory export uses
a different timezone, convert before calling ``load_forexfactory_csv``.
"""
from __future__ import annotations

from typing import Iterable, Union

import pandas as pd

# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------
_EventsInput = Union[
    list[pd.Timestamp],
    pd.Series,
    pd.DataFrame,   # must have a 'time' column of tz-aware UTC Timestamps
    Iterable[pd.Timestamp],
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_forexfactory_csv(
    path: str,
    *,
    impacts: tuple[str, ...] = ("High",),
    currencies: tuple[str, ...] = ("USD",),
) -> list[pd.Timestamp]:
    """Parse a ForexFactory-style CSV and return tz-aware UTC timestamps of
    events matching the given impact levels and currencies.

    Expected CSV columns (header names normalised to lower-case internally):

        datetime_utc  – ISO-8601 event datetime assumed to be UTC
        currency      – 3-letter code, e.g. "USD", "EUR"
        impact        – "High", "Medium", "Low", or "Holiday"
        event_name    – descriptive label (not filtered on)

    Parameters
    ----------
    path : str
        Absolute or relative path to the ForexFactory CSV file.
    impacts : tuple of str
        Impact levels to INCLUDE.  Default ``("High",)`` → red-folder only.
    currencies : tuple of str
        Currencies to INCLUDE.  Default ``("USD",)``.

    Returns
    -------
    list of pd.Timestamp
        Sorted list of tz-aware UTC timestamps for matching events.

    Raises
    ------
    FileNotFoundError
        If ``path`` does not exist.
    ValueError
        If the CSV does not contain the required columns.
    """
    df = pd.read_csv(path)

    # Normalise column names to lower-case and strip whitespace.
    df.columns = [c.strip().lower() for c in df.columns]

    required = {"datetime_utc", "currency", "impact"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"ForexFactory CSV is missing required columns: {missing}. "
            f"Found columns: {list(df.columns)}"
        )

    # Normalise string columns.
    df["currency"] = df["currency"].str.strip().str.upper()
    df["impact"] = df["impact"].str.strip()

    # Filter by currency and impact.
    mask = (
        df["currency"].isin([c.upper() for c in currencies])
        & df["impact"].isin(impacts)
    )
    filtered = df[mask].copy()

    # Parse datetime column to tz-aware UTC Timestamps.
    filtered["datetime_utc"] = pd.to_datetime(
        filtered["datetime_utc"], utc=True
    )

    timestamps: list[pd.Timestamp] = (
        filtered["datetime_utc"].sort_values().tolist()
    )
    return timestamps


def is_news_window(
    ts,
    events: _EventsInput,
    *,
    lookback_min: int = 15,
    lookahead_min: int = 15,
) -> bool:
    """Return True if ``ts`` falls within the news window of ANY event.

    The window around each event is::

        [event_time - lookback_min,  event_time + lookahead_min]  (inclusive)

    Parameters
    ----------
    ts : pd.Timestamp (tz-aware UTC)
        The candidate timestamp to test.  Must be tz-aware; a naive timestamp
        raises ``ValueError`` (consistent with other research modules in this
        repo that guard tz-naive input).
    events : list / iterable of tz-aware UTC pd.Timestamp,
             OR a pd.DataFrame with a ``'time'`` column of tz-aware UTC
             Timestamps (as returned by wrapping the load result in a
             DataFrame), OR a pd.Series.
        The event times to check against.  Empty input always returns False.
    lookback_min : int
        Minutes *before* an event that are considered inside the window.
        Default 15.
    lookahead_min : int
        Minutes *after* an event that are considered inside the window.
        Default 15.

    Returns
    -------
    bool
        True  → ``ts`` is within the blackout window of at least one event.
        False → ``ts`` is outside every event window (or ``events`` is empty).

    Raises
    ------
    ValueError
        If ``ts`` is tz-naive.
    """
    # --- tz-aware guard ---------------------------------------------------
    if not isinstance(ts, pd.Timestamp):
        ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        raise ValueError(
            "is_news_window requires a tz-aware UTC timestamp; "
            f"got tz-naive ts={ts!r}. "
            "Localise with pd.Timestamp(..., tz='UTC') or "
            ".tz_localize('UTC')."
        )

    # --- normalise events to a list of Timestamps -------------------------
    if isinstance(events, pd.DataFrame):
        if "time" not in events.columns:
            raise ValueError(
                "When passing a DataFrame to is_news_window, it must have "
                "a 'time' column of tz-aware UTC Timestamps."
            )
        event_list: list[pd.Timestamp] = events["time"].tolist()
    elif isinstance(events, pd.Series):
        event_list = events.tolist()
    else:
        event_list = list(events)

    if not event_list:
        return False

    # --- tz-naive guard on event timestamps --------------------------------
    for event_ts in event_list:
        if not isinstance(event_ts, pd.Timestamp):
            event_ts = pd.Timestamp(event_ts)
        if event_ts.tzinfo is None:
            raise ValueError(
                "is_news_window requires event times to be tz-aware UTC; "
                f"got tz-naive event_ts={event_ts!r}. "
                "Localise with pd.Timestamp(..., tz='UTC') or "
                ".tz_localize('UTC')."
            )

    # --- check window membership ------------------------------------------
    lb = pd.Timedelta(minutes=lookback_min)
    la = pd.Timedelta(minutes=lookahead_min)

    for event_ts in event_list:
        if (event_ts - lb) <= ts <= (event_ts + la):
            return True

    return False
