"""
btc_research/data.py
--------------------
Efficient BTC_USD candle loader for edge research.

The `ltp` hypertable holds ~24.4M BTC_USD ticks (~1.3s resolution, 24h on
weekdays, closed Sat / partial Sun). Pulling raw ticks repeatedly is slow, so we
aggregate to 1-minute OHLCV *server-side* via TimescaleDB `time_bucket` + the
`first()/last()` ordered aggregates, cache the result to parquet, then derive all
higher timeframes locally.

Public API
----------
    build_cache(symbol="BTC_USD")        -> writes <cache>/<symbol>_1m.parquet
    load_1m(symbol="BTC_USD")            -> 1m OHLCV DataFrame (from cache)
    get_candles(tf, symbol="BTC_USD")    -> OHLCV at tf in {1m,5m,15m,30m,1h,4h,1d}
    add_time_features(df)                -> adds session / hour / weekday columns

DataFrame shape matches the rest of the repo:
    columns = [time (tz-naive UTC datetime64[ns]), open, high, low, close, volume]
"""
from __future__ import annotations

import os
import psycopg2
import pandas as pd
from dotenv import load_dotenv

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
load_dotenv(os.path.join(_ROOT, ".env"))

CACHE_DIR = os.path.join(_HERE, "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

_URL = os.environ["TIGERDATA_URL"]

# tf -> pandas offset alias for local resampling from the 1m base
_TF_ALIAS = {
    "1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min",
    "30m": "30min", "1h": "1h", "4h": "4h", "1d": "1D",
}


def _cache_path(symbol: str) -> str:
    return os.path.join(CACHE_DIR, f"{symbol}_1m.parquet")


def build_cache(symbol: str = "BTC_USD") -> pd.DataFrame:
    """Aggregate ticks -> 1m OHLCV server-side and cache to parquet."""
    print(f"[data] aggregating {symbol} ticks -> 1m candles (server-side)...")
    conn = psycopg2.connect(_URL)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT time_bucket('1 minute', time) AS t,
               first(ltp, time) AS open,
               max(ltp)         AS high,
               min(ltp)         AS low,
               last(ltp, time)  AS close,
               count(*)         AS volume
        FROM   ltp
        WHERE  symbol = %s
        GROUP  BY t
        ORDER  BY t
        """,
        (symbol,),
    )
    rows = cur.fetchall()
    conn.close()

    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert(None).astype("datetime64[ns]")
    for c in ("open", "high", "low", "close"):
        df[c] = df[c].astype(float)
    df["volume"] = df["volume"].astype(float)
    df = df.sort_values("time").reset_index(drop=True)

    path = _cache_path(symbol)
    df.to_parquet(path, index=False)
    print(f"[data] cached {len(df):,} 1m candles -> {path}")
    print(f"[data] span {df['time'].iloc[0]} -> {df['time'].iloc[-1]}")
    return df


def load_1m(symbol: str = "BTC_USD") -> pd.DataFrame:
    path = _cache_path(symbol)
    if not os.path.exists(path):
        return build_cache(symbol)
    return pd.read_parquet(path)


def get_candles(tf: str, symbol: str = "BTC_USD") -> pd.DataFrame:
    """Return OHLCV at `tf`, resampled locally from the cached 1m base."""
    alias = _TF_ALIAS.get(tf)
    if alias is None:
        raise ValueError(f"Unknown tf '{tf}'. Valid: {list(_TF_ALIAS)}")
    base = load_1m(symbol)
    if tf == "1m":
        return base.copy()

    s = base.set_index("time")
    ohlcv = pd.DataFrame({
        "open":   s["open"].resample(alias).first(),
        "high":   s["high"].resample(alias).max(),
        "low":    s["low"].resample(alias).min(),
        "close":  s["close"].resample(alias).last(),
        "volume": s["volume"].resample(alias).sum(),
    }).dropna(subset=["open", "close"]).reset_index()
    return ohlcv


# ── time / session features ──────────────────────────────────────────────────
# UTC session windows (BTC trades 24h on weekdays here).
#   Asia    : 00-07  (Tokyo/Sydney)
#   London  : 07-12  (London open + AM)
#   NY      : 12-21  (NY open through London close / NY PM)
#   Late    : 21-24  (post-NY / Asia pre-open)
def _session(hour: int) -> str:
    if 0 <= hour < 7:
        return "ASIA"
    if 7 <= hour < 12:
        return "LONDON"
    if 12 <= hour < 21:
        return "NY"
    return "LATE"


def add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    t = out["time"].dt
    out["hour"] = t.hour
    out["weekday"] = t.dayofweek          # 0=Mon .. 6=Sun
    out["date"] = out["time"].dt.normalize()
    out["session"] = out["hour"].map(_session)
    return out


if __name__ == "__main__":
    df = build_cache("BTC_USD")
    print("\n[data] sanity — candles per timeframe:")
    for tf in ("1m", "5m", "15m", "1h", "4h", "1d"):
        print(f"   {tf:4s} {len(get_candles(tf)):>8,}")
