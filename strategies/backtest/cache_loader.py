"""
cache_loader.py
---------------
Load cached XAUUSD tick JSON files from
`Extras_Script/Tick_Data_Generator/cache_data/<symbol>/` and resample to OHLC.

Each file is one day, layout: nested arrays of {"time": "...UTC...", "price": float}.
Returns DataFrames in the same shape as utils.tsdb_reader.fetch_candles.
"""
from __future__ import annotations

import json
import os
import glob
from typing import Iterable

import pandas as pd

_TF_ALIAS = {
    "1m":  "1min",
    "5m":  "5min",
    "15m": "15min",
    "1h":  "1h",
}

_DEFAULT_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..", "Extras_Script", "Tick_Data_Generator", "cache_data",
))


def _flatten(payload) -> Iterable[dict]:
    """Walk arbitrarily nested lists, yielding {time, price} dicts."""
    if isinstance(payload, dict):
        if "time" in payload and "price" in payload:
            yield payload
        return
    if isinstance(payload, list):
        for item in payload:
            yield from _flatten(item)


def load_ticks(symbol: str = "XAU_USD", cache_dir: str | None = None) -> pd.DataFrame:
    """Load every cached tick for a symbol into a single DataFrame [time, price] sorted asc."""
    cache_dir = cache_dir or _DEFAULT_DIR
    folder = os.path.join(cache_dir, symbol)
    files = sorted(glob.glob(os.path.join(folder, "*.json")))
    if not files:
        return pd.DataFrame(columns=["time", "price"])

    rows: list[dict] = []
    for fp in files:
        try:
            with open(fp, "r") as fh:
                data = json.load(fh)
        except Exception as exc:
            print(f"[cache_loader] skipping {os.path.basename(fp)}: {exc}")
            continue
        rows.extend(_flatten(data))

    if not rows:
        return pd.DataFrame(columns=["time", "price"])

    df = pd.DataFrame(rows)
    df["time"]  = pd.to_datetime(df["time"], utc=True, errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df = df.dropna(subset=["time", "price"]).sort_values("time").reset_index(drop=True)
    return df


def resample(ticks: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Resample tick DataFrame into an OHLCV bar DataFrame at the given timeframe."""
    alias = _TF_ALIAS.get(tf)
    if alias is None:
        raise ValueError(f"Unknown timeframe '{tf}'. Valid: {list(_TF_ALIAS)}")
    if ticks.empty:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

    series = ticks.set_index("time")["price"].sort_index()
    rs = series.resample(alias)
    ohlcv = pd.DataFrame({
        "open":   rs.first(),
        "high":   rs.max(),
        "low":    rs.min(),
        "close":  rs.last(),
        "volume": rs.count(),
    }).dropna(subset=["open", "close"]).reset_index()

    # Match tsdb_reader format: tz-naive datetime64[ns]
    ohlcv["time"] = ohlcv["time"].dt.tz_convert(None).astype("datetime64[ns]")
    for col in ("open", "high", "low", "close"):
        ohlcv[col] = ohlcv[col].astype(float)
    ohlcv["volume"] = ohlcv["volume"].astype(float)
    return ohlcv


def load_candles(tf: str, symbol: str = "XAU_USD", cache_dir: str | None = None) -> pd.DataFrame:
    return resample(load_ticks(symbol, cache_dir), tf)
