"""
dataset.py
----------
Frozen in-sample / out-of-sample split + shared pre-built bar cache for the
XAU/USD 90%-winrate research (docs/plans/2026-05-22-xauusd-90pct-winrate.md).

Backtest source is the local mid-price tick cache under ``.history_data/<symbol>/``
(see cache_loader). To keep the 3-month OOS window genuinely held-out, Phase B/C
discovery must read ONLY in-sample bars. This module:

  * defines the frozen IS/OOS boundary (constants below),
  * builds OHLC bars ONCE for all timeframes and caches them to parquet (pickle
    fallback), so the 7 parallel Phase-B subagents read identical bars instead of
    each re-loading ~1.3 GB of ticks,
  * exposes ``load_is_bars(tf)`` / ``load_oos_bars(tf)``.

All bar ``time`` columns are tz-naive datetime64[ns] (UTC wall-clock), matching
bar_builder / cache_loader. The IS/OOS constants are therefore tz-naive too.

NOTE on cache file dating: cache files are named ``D_M_YYYY.json`` and are IST-window
labelled (a file holds roughly UTC D-1 18:30 -> D 18:30). We load a generous date
range of files and then slice bars precisely by timestamp, so no OOS bar can leak
into the IS set regardless of file labelling.
"""
from __future__ import annotations

import os
import glob
import json
import datetime
from typing import Iterable

import pandas as pd

from strategies.backtest.cache_loader import _flatten  # reuse tested tick parser
from strategies.research.bar_builder import build_bars, VALID_TFS

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(_HERE, "..", ".."))
CACHE_DIR = os.path.join(REPO, ".history_data")
BARS_CACHE_DIR = os.path.join(REPO, "strategies", "backtest", "results", "bars_cache")

# --- Frozen split (tz-naive UTC, inclusive) ---------------------------------
# Full cache spans 2025-01-01 -> 2026-05-19. Hold out the last ~3 months as OOS.
IS_START = pd.Timestamp("2025-01-01 00:00:00")
IS_END = pd.Timestamp("2026-02-18 23:59:59")
OOS_START = pd.Timestamp("2026-02-19 00:00:00")
OOS_END = pd.Timestamp("2026-05-19 23:59:59")

DEFAULT_SPREAD = 0.30  # full spread (cents) -> bar_builder assumed_spread


# --- pure helpers (unit-tested) ---------------------------------------------
def slice_is(bars: pd.DataFrame) -> pd.DataFrame:
    """Return only bars whose time is within [IS_START, IS_END]."""
    m = (bars["time"] >= IS_START) & (bars["time"] <= IS_END)
    return bars.loc[m].reset_index(drop=True)


def slice_oos(bars: pd.DataFrame) -> pd.DataFrame:
    """Return only bars whose time is within [OOS_START, OOS_END]."""
    m = (bars["time"] >= OOS_START) & (bars["time"] <= OOS_END)
    return bars.loc[m].reset_index(drop=True)


def _parse_file_date(path: str) -> datetime.date:
    d, m, y = os.path.basename(path)[:-5].split("_")
    return datetime.date(int(y), int(m), int(d))


def day_files_in_range(symbol: str, lo: datetime.date, hi: datetime.date,
                       cache_dir: str = CACHE_DIR) -> list[str]:
    """Day-file paths whose label date is within [lo, hi], sorted ascending."""
    out = []
    for f in glob.glob(os.path.join(cache_dir, symbol, "*.json")):
        try:
            fd = _parse_file_date(f)
        except ValueError:
            continue
        if lo <= fd <= hi:
            out.append((fd, f))
    return [f for _, f in sorted(out)]


def _load_ticks_from_files(files: Iterable[str]) -> pd.DataFrame:
    parts = []
    for fp in files:
        try:
            with open(fp, "r") as fh:
                data = json.load(fh)
        except Exception as exc:  # pragma: no cover - corrupt file guard
            print(f"[dataset] skip {os.path.basename(fp)}: {exc}")
            continue
        times, prices = [], []
        for rec in _flatten(data):
            t, p = rec.get("time"), rec.get("price")
            if t is None or p is None:
                continue
            times.append(t)
            prices.append(p)
        if not times:
            continue
        df = pd.DataFrame({
            "time": pd.to_datetime(times, utc=True, errors="coerce"),
            "price": pd.to_numeric(prices, errors="coerce"),
        }).dropna(subset=["time", "price"])
        parts.append(df)
    if not parts:
        return pd.DataFrame(columns=["time", "price"])
    out = pd.concat(parts, ignore_index=True).sort_values("time").reset_index(drop=True)
    return out


# --- parquet/pickle persistence ---------------------------------------------
def _path(split: str, symbol: str, tf: str, ext: str) -> str:
    return os.path.join(BARS_CACHE_DIR, f"{split}_{symbol}_{tf}.{ext}")


def _save(bars: pd.DataFrame, split: str, symbol: str, tf: str) -> str:
    os.makedirs(BARS_CACHE_DIR, exist_ok=True)
    try:
        p = _path(split, symbol, tf, "parquet")
        bars.to_parquet(p, index=False)
        return p
    except Exception:  # pyarrow missing -> pickle fallback
        p = _path(split, symbol, tf, "pkl")
        bars.to_pickle(p)
        return p


def _read(split: str, symbol: str, tf: str) -> pd.DataFrame:
    pq = _path(split, symbol, tf, "parquet")
    pk = _path(split, symbol, tf, "pkl")
    if os.path.exists(pq):
        return pd.read_parquet(pq)
    if os.path.exists(pk):
        return pd.read_pickle(pk)
    raise FileNotFoundError(
        f"No cached {split} bars for {symbol} {tf}. Run dataset.build_and_cache() first."
    )


# --- build + load ------------------------------------------------------------
def _build_split(split: str, symbol: str, tfs: Iterable[str], spread: float):
    if split == "is":
        files = day_files_in_range(symbol, datetime.date(2024, 12, 31),
                                    datetime.date(2026, 2, 19))
        slicer = slice_is
    else:
        files = day_files_in_range(symbol, datetime.date(2026, 2, 18),
                                   datetime.date(2026, 5, 31))
        slicer = slice_oos
    print(f"[dataset] {split.upper()} {symbol}: loading {len(files)} day-files...")
    ticks = _load_ticks_from_files(files)
    print(f"[dataset]   loaded {len(ticks):,} ticks")
    written = {}
    for tf in tfs:
        bars = slicer(build_bars(ticks, tf, assumed_spread=spread))
        path = _save(bars, split, symbol, tf)
        written[tf] = (len(bars), path)
        print(f"[dataset]   {tf}: {len(bars):,} bars -> {os.path.basename(path)}")
    return written


def build_and_cache(symbol: str = "XAU_USD", tfs: Iterable[str] = VALID_TFS,
                    spread: float = DEFAULT_SPREAD, splits=("is",)):
    out = {}
    for split in splits:
        out[split] = _build_split(split, symbol, tfs, spread)
    return out


def load_is_bars(tf: str, symbol: str = "XAU_USD") -> pd.DataFrame:
    return _read("is", symbol, tf)


def load_oos_bars(tf: str, symbol: str = "XAU_USD") -> pd.DataFrame:
    return _read("oos", symbol, tf)


if __name__ == "__main__":
    import sys
    splits = ("is", "oos") if "--oos" in sys.argv else ("is",)
    res = build_and_cache(splits=splits)
    print("\nDONE:", {s: {tf: n for tf, (n, _) in d.items()} for s, d in res.items()})
