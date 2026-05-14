"""
tsdb_reader.py
--------------
Higher-level TimescaleDB helpers for the Automation strategy.
Wraps db_utils so callers get DataFrames instead of sys.exit on empty data.
"""

import os
import sys
import pandas as pd
from dotenv import load_dotenv

_HERE = os.path.dirname(os.path.abspath(__file__))
for _env_path in [
    os.path.join(_HERE, ".env"),
    os.path.join(_HERE, "..", ".env"),
    os.path.join(_HERE, "..", "..", ".env"),
]:
    if os.path.exists(_env_path):
        load_dotenv(_env_path)
        break

IN_DOCKER = os.path.exists("/.dockerenv")

# Prefer TIGERDATA_URL (DSN) when present, then TSDB_*, then DB_*, then defaults.
_TIGERDATA_URL = os.getenv("TIGERDATA_URL", "").strip()

if IN_DOCKER:
    _TSDB_HOST = os.getenv("TSDB_HOST") or os.getenv("DB_HOST") or "tsdb"
    _TSDB_PORT = int(os.getenv("TSDB_PORT") or os.getenv("DB_PORT") or "5432")
else:
    _TSDB_HOST = "127.0.0.1"
    _TSDB_PORT = 5433

_TSDB_NAME = os.getenv("TSDB_NAME") or os.getenv("DB_NAME") or "hft"
_TSDB_USER = os.getenv("TSDB_USER") or os.getenv("DB_USER") or "postgres"
_TSDB_PASS = os.getenv("TSDB_PASS") or os.getenv("DB_PASSWORD") or "postgres"

_TF_ALIAS = {
    "1m":  "1min",
    "5m":  "5min",
    "15m": "15min",
    "1h":  "1h",
    "4h":  "4h",
    "1d":  "1D",
}


def _connect():
    import psycopg2
    if _TIGERDATA_URL:
        return psycopg2.connect(_TIGERDATA_URL)
    return psycopg2.connect(
        host=_TSDB_HOST, port=_TSDB_PORT,
        database=_TSDB_NAME, user=_TSDB_USER, password=_TSDB_PASS,
    )


def fetch_candles(tf: str, days: int = 7, symbol: str = "XAU_USD") -> pd.DataFrame:
    """Return OHLCV DataFrame for the given timeframe. Returns empty DF on no data."""
    alias = _TF_ALIAS.get(tf)
    if alias is None:
        raise ValueError(f"Unknown timeframe '{tf}'. Valid: {list(_TF_ALIAS)}")

    empty = pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT time, ltp AS price
            FROM   ltp
            WHERE  symbol = %s
              AND  time  >= NOW() - (%s || ' days')::INTERVAL
            ORDER  BY time ASC
            """,
            (symbol, str(days)),
        )
        rows = cur.fetchall()
        conn.close()
    except Exception as exc:
        print(f"[TSDB] fetch_candles error: {exc}")
        return empty

    if not rows:
        return empty

    ticks = pd.DataFrame(rows, columns=["time", "price"])
    ticks["time"] = pd.to_datetime(ticks["time"], utc=True)
    ticks["price"] = pd.to_numeric(ticks["price"], errors="coerce")
    ticks = ticks.dropna(subset=["price"])

    series = ticks.set_index("time")["price"].sort_index()
    resampled = series.resample(alias)
    ohlcv = pd.DataFrame({
        "open":   resampled.first(),
        "high":   resampled.max(),
        "low":    resampled.min(),
        "close":  resampled.last(),
        "volume": resampled.count(),
    }).dropna(subset=["open", "close"])

    ohlcv = ohlcv.reset_index()
    ohlcv["time"] = ohlcv["time"].dt.tz_convert(None).astype("datetime64[ns]")
    for col in ["open", "high", "low", "close"]:
        ohlcv[col] = ohlcv[col].astype(float)
    ohlcv["volume"] = ohlcv["volume"].astype(float)

    return ohlcv


def fetch_latest_ltp(symbol: str = "XAU_USD") -> float | None:
    """Return the most recent tick price from TimescaleDB, or None on error."""
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT ltp FROM ltp WHERE symbol = %s ORDER BY time DESC LIMIT 1",
            (symbol,),
        )
        row = cur.fetchone()
        conn.close()
        return float(row[0]) if row else None
    except Exception as exc:
        print(f"[TSDB] fetch_latest_ltp error: {exc}")
        return None
