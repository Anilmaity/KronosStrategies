"""s5_cache.py — S5 (5-second) OHLC + quote cache for the fidelity backtest.

Phase 1 of docs/superpowers/specs/2026-08-12-5s-backtest-fidelity-design.md.

The offline sim resolves exits on 1-minute bars, which forces it to *assume*
whether SL or TP came first inside a bar. S5 data replaces that assumption with
the observed sequence, and carries bid/ask so spread is measured rather than
hard-coded (live XAU spread measured 0.76 pt vs the sim's 0.30 pt constant).

Storage: monthly parquet partitions, so a multi-year backfill is resumable and
can be processed a month at a time — the production box has ~200 MB of free RAM
and must never hold a year of S5 in memory.

Network access is injected (`fetch_s5(..., getter=...)`) so every test runs
offline.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

S5_STEP_SEC = 5
S5_STEP = timedelta(seconds=S5_STEP_SEC)
PAGE_SIZE = 5000                 # OANDA hard cap per request (~6.94 h of S5)
MAX_MARKET_GAP_SEC = 300         # >5 min inside market hours is corruption
_MAX_PAGES = 20_000              # runaway-loop backstop
_POLITE_SLEEP_S = 0.15           # matches tsdb_reader's paging courtesy delay

COLUMNS = ["time", "o", "h", "l", "c", "bid_c", "ask_c", "volume"]

_BASE_DIR = Path(__file__).resolve().parent / "results" / "bars_cache" / "s5"

# OANDA rejects unknown price components with a 400; these markers let us fall
# back to two single-sided passes instead of failing the whole backfill.
_MBA_REJECT_MARKERS = ("400", "price", "invalid")


# ── paths ─────────────────────────────────────────────────────────────────────

def partition_path(symbol: str, ts: datetime, base: Path | str | None = None) -> Path:
    """Month-keyed partition for a timestamp: <base>/<SYMBOL>/YYYY-MM.parquet."""
    root = Path(base) if base is not None else _BASE_DIR
    return root / symbol / f"{ts:%Y-%m}.parquet"


# ── parsing ───────────────────────────────────────────────────────────────────

def _f(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def rows_from_candles(batch: list[dict]) -> list[dict]:
    """OANDA candle payloads -> row dicts. Incomplete candles are dropped.

    Tolerates missing `mid` or `bid`/`ask` blocks so the same parser serves the
    combined price=MBA response and the split M / BA fallback responses.
    """
    rows: list[dict] = []
    for c in batch or []:
        if not c.get("complete"):
            continue
        mid = c.get("mid") or {}
        bid = c.get("bid") or {}
        ask = c.get("ask") or {}
        rows.append({
            "time":   c.get("time"),
            "o":      _f(mid.get("o")),
            "h":      _f(mid.get("h")),
            "l":      _f(mid.get("l")),
            "c":      _f(mid.get("c")),
            "bid_c":  _f(bid.get("c")),
            "ask_c":  _f(ask.get("c")),
            "volume": c.get("volume", 0),
        })
    return rows


def frame_from_rows(rows: list[dict]) -> pd.DataFrame:
    """Row dicts -> typed DataFrame with tz-aware UTC `time`."""
    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    for col in ("o", "h", "l", "c", "bid_c", "ask_c", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
    return df[COLUMNS].reset_index(drop=True)


# ── merging ───────────────────────────────────────────────────────────────────

def merge_partition(existing: pd.DataFrame, fresh: pd.DataFrame) -> pd.DataFrame:
    """Concat, drop duplicate `time` keeping the FRESH row, sort ascending.

    Mirrors backfill_history_cache.merge_frames so a re-fetch of an overlapping
    window is idempotent.
    """
    if fresh is None or len(fresh) == 0:
        return (existing if existing is not None else
                pd.DataFrame(columns=COLUMNS)).reset_index(drop=True)
    if existing is None or len(existing) == 0:
        return fresh.reset_index(drop=True)
    out = pd.concat([existing, fresh], ignore_index=True)
    out = out.drop_duplicates(subset="time", keep="last")
    return out.sort_values("time").reset_index(drop=True)


# ── validation ────────────────────────────────────────────────────────────────

def _market_hours_gap_sec(df: pd.DataFrame) -> pd.Series:
    """Gap seconds between consecutive bars, NaN where the gap is legitimate.

    Excluded (same rule as backfill_history_cache._max_market_gap_minutes):
      * the daily 21:00-22:00 UTC break  -> previous bar's hour in {20, 21}
      * the Fri 21:00 -> Sun 22:00 close -> previous bar's weekday == Friday
    """
    t = df["time"]
    gaps = t.diff().dt.total_seconds()
    prev_hour = t.shift(1).dt.hour
    prev_dow = t.shift(1).dt.dayofweek
    legit = prev_hour.isin([20, 21]) | (prev_dow == 4)
    return gaps.where(~legit)


def validate_s5(df: pd.DataFrame, presorted: bool = True) -> list[str]:
    """Return a list of problems; empty means the frame is trustworthy.

    Returning problems (rather than raising) keeps the predicate testable; the
    CLI turns a non-empty list into a loud non-zero exit.
    """
    problems: list[str] = []
    if df is None or len(df) == 0:
        return problems          # emptiness is a coverage question, not corruption

    t = df["time"]

    dupes = int(t.duplicated().sum())
    if dupes:
        problems.append(f"{dupes} duplicate timestamp(s)")

    if not presorted and not t.is_monotonic_increasing:
        problems.append("time is not monotonic increasing")

    ordered = df if t.is_monotonic_increasing else df.sort_values("time")
    gaps = _market_hours_gap_sec(ordered).dropna()
    if len(gaps):
        worst = float(gaps.max())
        if worst > MAX_MARKET_GAP_SEC:
            problems.append(
                f"market-hours gap of {worst:.0f}s exceeds "
                f"{MAX_MARKET_GAP_SEC}s limit")
    return problems


def cached_months(symbol: str, base: Path | str | None = None) -> list[str]:
    """Sorted 'YYYY-MM' keys already on disk — lets a backfill skip whole months."""
    root = (Path(base) if base is not None else _BASE_DIR) / symbol
    if not root.exists():
        return []
    return sorted(p.stem for p in root.glob("*.parquet"))


def _months_in_range(start: datetime, end: datetime) -> list[str]:
    s = pd.Timestamp(start).tz_convert("UTC").tz_localize(None)
    e = pd.Timestamp(end).tz_convert("UTC").tz_localize(None)
    return [str(p) for p in pd.period_range(s, e, freq="M")]


def month_windows(start: datetime, end: datetime
                  ) -> list[tuple[datetime, datetime]]:
    """Split [start, end] into inclusive per-month windows.

    A multi-year backfill must never hold more than one month of S5 in memory
    (~380k rows), so the CLI fetches window by window.
    """
    if pd.Timestamp(end) < pd.Timestamp(start):
        return []

    windows: list[tuple[datetime, datetime]] = []
    cur = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    while cur <= end_ts:
        next_month = (cur + pd.offsets.MonthBegin(1)).normalize()
        if next_month <= cur:                      # cur is exactly a month start
            next_month = (cur + pd.Timedelta(days=1) +
                          pd.offsets.MonthBegin(1)).normalize()
        window_end = min(end_ts, next_month - S5_STEP)
        windows.append((cur.to_pydatetime(), window_end.to_pydatetime()))
        cur = next_month
    return windows


CLOSURE_GRACE = timedelta(days=3)   # weekend + holiday: a month may legitimately
                                    # have no bars for its last few days

MEMORY_FLOOR_MB = 400               # refuse to backfill below this much free RAM


def available_memory_mb() -> int | None:
    """MemAvailable in MiB, or None where /proc/meminfo does not exist."""
    try:
        with open("/proc/meminfo", "r") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def memory_is_sufficient(available_mb: int | None,
                         floor_mb: int = MEMORY_FLOOR_MB) -> bool:
    """False only when availability is KNOWN and below the floor.

    Guard added after 2026-08-12: a backfill run on the production box (227 MB
    available) thrashed it into unreachability and took the live API down.
    Unknown availability never blocks — that is the local-dev case.
    """
    if available_mb is None:
        return True
    return available_mb >= floor_mb


def window_is_cached(symbol: str, w_start: datetime, w_end: datetime,
                     base: Path | str | None = None,
                     grace: timedelta = CLOSURE_GRACE) -> bool:
    """True when the partition already covers this window, so it can be skipped.

    Completeness cannot be "last bar == w_end": a month ending on a Friday has no
    bars after the 21:00 UTC close, and one ending mid-weekend none for two days.
    A window counts as cached once its data reaches within `grace` of the end.
    """
    path = partition_path(symbol, w_start, base=base)
    if not path.exists():
        return False
    part = pd.read_parquet(path, columns=["time"])
    if len(part) == 0:
        return False
    last = pd.to_datetime(part["time"], utc=True).max()
    return last >= pd.Timestamp(w_end) - grace


def update_partition(symbol: str, df: pd.DataFrame,
                     base: Path | str | None = None) -> int:
    """Merge `df` into its month partition(s). Returns the number of NEW rows.

    A frame spanning a month boundary is split across partitions. Re-writing the
    same data is a no-op (0 new rows) because merge_partition dedupes on `time`.
    """
    if df is None or len(df) == 0:
        return 0

    new_rows = 0
    for _, part in df.groupby(df["time"].dt.strftime("%Y-%m"), sort=True):
        path = partition_path(symbol, part["time"].iloc[0], base=base)
        path.parent.mkdir(parents=True, exist_ok=True)

        existing = None
        if path.exists():
            existing = pd.read_parquet(path)
            existing["time"] = pd.to_datetime(existing["time"], utc=True)
        before = 0 if existing is None else len(existing)

        merged = merge_partition(existing, part)
        merged.to_parquet(path, index=False, compression="zstd")
        new_rows += len(merged) - before
    return new_rows


def load_s5(symbol: str, start: datetime, end: datetime,
            base: Path | str | None = None) -> pd.DataFrame:
    """Load cached S5 bars for [start, end] inclusive, spanning partitions.

    Missing partitions are skipped silently — `coverage_pct` is what reports how
    much of the window is actually present.
    """
    root = Path(base) if base is not None else _BASE_DIR
    parts: list[pd.DataFrame] = []
    for key in _months_in_range(start, end):
        path = root / symbol / f"{key}.parquet"
        if not path.exists():
            continue
        part = pd.read_parquet(path)
        part["time"] = pd.to_datetime(part["time"], utc=True)
        parts.append(part)

    if not parts:
        return pd.DataFrame(columns=COLUMNS)

    out = pd.concat(parts, ignore_index=True)
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    out = out[(out["time"] >= start_ts) & (out["time"] <= end_ts)]
    return out.sort_values("time").reset_index(drop=True)[COLUMNS]


def coverage_pct(df: pd.DataFrame, start: datetime, end: datetime) -> float:
    """Fraction of expected S5 slots in [start, end) that are present.

    Reported rather than enforced: thin-quote minutes legitimately lack bars, so
    coverage is a data-quality metric, not a pass/fail gate.
    """
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    expected = int((end_ts - start_ts).total_seconds() // S5_STEP_SEC)
    if expected <= 0 or df is None or len(df) == 0:
        return 0.0
    present = int(((df["time"] >= start_ts) & (df["time"] < end_ts)).sum())
    return present / expected


# ── fetching ──────────────────────────────────────────────────────────────────

def _candles_url(symbol: str) -> str:
    try:                                    # lazy: keeps this module import-safe
        from shared.tsdb_reader import _OANDA_BASE
        base = _OANDA_BASE
    except Exception:                       # pragma: no cover - env-dependent
        base = "https://api-fxpractice.oanda.com/v3"
    return f"{base}/instruments/{symbol}/candles"


def _default_getter(url: str, params: dict) -> dict:  # pragma: no cover - network
    from shared.tsdb_reader import _oanda_get
    return _oanda_get(url, params)


def _iso_z(ts: datetime) -> str:
    """OANDA-safe RFC3339 with no fractional seconds."""
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _looks_like_price_rejection(exc: Exception) -> bool:
    msg = str(exc).lower()
    return all(marker in msg for marker in ("400", "price")) or \
        ("invalid" in msg and "price" in msg)


def _merge_batches(m_batch: list[dict], ba_batch: list[dict]) -> list[dict]:
    """Graft bid/ask blocks onto mid candles by timestamp (split-pass fallback)."""
    quotes = {c.get("time"): c for c in (ba_batch or [])}
    merged: list[dict] = []
    for c in m_batch or []:
        q = quotes.get(c.get("time")) or {}
        row = dict(c)
        if "bid" in q:
            row["bid"] = q["bid"]
        if "ask" in q:
            row["ask"] = q["ask"]
        merged.append(row)
    return merged


def partition_sink(symbol: str, base: Path | str | None = None):
    """Sink that appends each streamed page straight into its month partition."""
    def _sink(df: pd.DataFrame) -> None:
        update_partition(symbol, df, base=base)
    return _sink


def stream_s5(
    symbol: str,
    start: datetime,
    end: datetime,
    sink,
    getter=None,
    page_size: int = PAGE_SIZE,
    sleep_s: float = 0.0,
) -> int:
    """Page OANDA S5 over [start, end], handing each page to `sink`. Returns rows.

    Peak memory is ONE page, by design. The 2026-08-12 incident came from
    accumulating a whole month (~450k candles as Python dicts, ~250 MB) before
    writing, which thrashed the 2 GB production box into unreachability. Nothing
    in this function may retain the full window.

    Tries the combined `price=MBA` form first and falls back to per-page M + BA
    requests if OANDA rejects it — the fallback stays page-local so the memory
    guarantee holds either way.

    Paging stops on the first page that yields no NEW timestamps, which also
    guards against a non-advancing cursor spinning forever.
    """
    url = _candles_url(symbol)
    get = getter if getter is not None else _default_getter
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)

    cur = start_ts.to_pydatetime()
    seen_last: pd.Timestamp | None = None
    split = False
    total = 0

    for _ in range(_MAX_PAGES):
        if pd.Timestamp(cur) > end_ts:
            break

        params = {
            "from":              _iso_z(cur),
            "count":             page_size,
            "granularity":       "S5",
            "price":             "MBA",
            "alignmentTimezone": "UTC",
            "dailyAlignment":    0,
        }

        body = None
        if not split:
            try:
                body = get(url, params)
            except Exception as exc:
                if not _looks_like_price_rejection(exc):
                    raise
                log.warning("[S5] price=MBA rejected (%s) — falling back to "
                            "per-page M + BA requests", str(exc)[:120])
                split = True

        if split:
            m_body = get(url, {**params, "price": "M"})
            ba_body = get(url, {**params, "price": "BA"})
            batch = _merge_batches((m_body or {}).get("candles", []),
                                   (ba_body or {}).get("candles", []))
        else:
            batch = (body or {}).get("candles", [])

        if not batch:
            break

        raw_last = pd.to_datetime(batch[-1]["time"], utc=True)
        df = frame_from_rows(rows_from_candles(batch))
        if len(df):
            df = df[df["time"] <= end_ts]
        if seen_last is not None and len(df):
            df = df[df["time"] > seen_last]

        if len(df) == 0:
            break            # window exhausted, or the cursor is not advancing

        seen_last = df["time"].iloc[-1]
        total += len(df)
        sink(df.reset_index(drop=True))

        cur = (raw_last + S5_STEP).to_pydatetime()
        if len(batch) < page_size:
            break            # caught up to the present
        if sleep_s:
            time.sleep(sleep_s)

    return total


def fetch_s5(
    symbol: str,
    start: datetime,
    end: datetime,
    getter=None,
    page_size: int = PAGE_SIZE,
    sleep_s: float = 0.0,
) -> pd.DataFrame:
    """Collect [start, end] into one frame. Convenience wrapper over stream_s5.

    Only safe for windows small enough to hold in memory — a month of S5 is
    ~380k rows. Long backfills must use stream_s5 with partition_sink.
    """
    chunks: list[pd.DataFrame] = []
    stream_s5(symbol, start, end, sink=chunks.append, getter=getter,
              page_size=page_size, sleep_s=sleep_s)
    if not chunks:
        return pd.DataFrame(columns=COLUMNS)
    return pd.concat(chunks, ignore_index=True)[COLUMNS]
