"""tick_s5.py — build 5-second bars from the local 1-second tick cache.

Supports the 5s fidelity work (spec 2026-08-12) when OANDA is unreachable.

`.history_data/<SYMBOL>/D_M_YYYY.json` holds nested lists of {time, price} at
roughly 1-second resolution (4 samples per 5s bucket). That is FINER than
OANDA's S5 and matches live's 1-second position_manager loop, so where it
exists (2025-01-01 .. 2026-05-19) it is the better source for resolving which
of SL/TP came first inside a minute.

Two deliberate choices:
  * bars are labelled on the bucket's LEFT edge, matching every other frame in
    the codebase (a bar stamped T covers [T, T+5s));
  * empty buckets are DROPPED, never forward-filled — a fabricated flat bar
    would let walk_exit "observe" a price that never traded.

Days are streamed one file at a time so a multi-month build never holds more
than one day of ticks in memory.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

COLUMNS = ["time", "o", "h", "l", "c", "volume"]

# D_M_YYYY.json — day first (verified: the real cache contains 31_1_2026.json)
_NAME_RE = re.compile(r"^(\d{1,2})_(\d{1,2})_(\d{4})\.json$")


def parse_tick_date(name: str) -> date | None:
    """Filename -> date, or None when it is not a tick-day file."""
    m = _NAME_RE.match(Path(name).name)
    if not m:
        return None
    day, month, year = (int(g) for g in m.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def tick_files_in_range(cache_dir: str | Path, start: date,
                        end: date) -> list[Path]:
    """Tick files whose date falls in [start, end], sorted chronologically."""
    root = Path(cache_dir)
    dated: list[tuple[date, Path]] = []
    for p in root.glob("*.json"):
        d = parse_tick_date(p.name)
        if d is not None and start <= d <= end:
            dated.append((d, p))
    return [p for _, p in sorted(dated)]


def _flatten(payload):
    """Yield {time, price} dicts from arbitrarily nested lists."""
    if isinstance(payload, dict):
        if "time" in payload and "price" in payload:
            yield payload
        return
    if isinstance(payload, list):
        for item in payload:
            yield from _flatten(item)


def load_tick_day(path: str | Path) -> pd.DataFrame:
    """One day's ticks as [time, price], tz-aware UTC, sorted."""
    try:
        with open(path, "r") as fh:
            payload = json.load(fh)
    except Exception as exc:
        log.warning("[TICK] skipping %s: %s", path, exc)
        return pd.DataFrame(columns=["time", "price"])

    times: list[str] = []
    prices: list[float] = []
    for rec in _flatten(payload):
        t, p = rec.get("time"), rec.get("price")
        if t is None or p is None:
            continue
        times.append(t)
        prices.append(p)

    if not times:
        return pd.DataFrame(columns=["time", "price"])

    df = pd.DataFrame({
        "time": pd.to_datetime(times, utc=True, errors="coerce"),
        "price": pd.to_numeric(prices, errors="coerce"),
    }).dropna()
    return df.sort_values("time").reset_index(drop=True)


def resample_s5(ticks: pd.DataFrame) -> pd.DataFrame:
    """1-second ticks -> 5-second OHLC bars (left-labelled, gaps dropped)."""
    if ticks is None or len(ticks) == 0:
        return pd.DataFrame(columns=COLUMNS)

    s = ticks.set_index("time")["price"].sort_index()
    rs = s.resample("5s", label="left", closed="left")
    out = pd.DataFrame({
        "o": rs.first(), "h": rs.max(), "l": rs.min(), "c": rs.last(),
        "volume": rs.count(),
    }).dropna(subset=["o", "c"]).reset_index()

    out = out.rename(columns={"time": "time"})
    for col in ("o", "h", "l", "c", "volume"):
        out[col] = out[col].astype(float)
    return out[COLUMNS]


def build_s5(cache_dir: str | Path, start: date, end: date) -> pd.DataFrame:
    """5s bars for [start, end], streamed one day at a time."""
    frames: list[pd.DataFrame] = []
    files = tick_files_in_range(cache_dir, start, end)
    for path in files:
        bars = resample_s5(load_tick_day(path))
        if len(bars):
            frames.append(bars)

    if not frames:
        return pd.DataFrame(columns=COLUMNS)

    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(subset="time", keep="last")
    return out.sort_values("time").reset_index(drop=True)
