"""
db_utils.py
───────────
Shared helpers for all plot_chat scripts.

    connect_db()             – psycopg2 connection to TimescaleDB (local)
    fetch_ticks(days)        – pull raw ticks from the `ltp` hypertable
    build_candles(ticks, tf) – resample ticks → OHLCV DataFrame
    format_chart(chart)      – apply a dark-theme style to a lightweight_charts Chart
"""

import os
import sys
import psycopg2
import pandas as pd
from dotenv import load_dotenv

# ── Load .env (look next to this file, then two levels up) ───────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
for _env_path in [
    os.path.join(_HERE, ".env"),
    os.path.join(_HERE, "..", "..", "HFT_Data_Collector", ".env"),
    os.path.join(_HERE, "..", "..", "..", ".env"),
]:
    if os.path.exists(_env_path):
        load_dotenv(_env_path)
        break

IN_DOCKER = os.path.exists("/.dockerenv")



# ── DB config (always local when running debug scripts) ───────────────────────
if IN_DOCKER:
    DB_HOST = os.getenv("DB_HOST", "tsdb")
    DB_PORT = int(os.getenv("DB_PORT", "5432"))
else:
    DB_HOST = "127.0.0.1"
    DB_PORT = 5433  # host-mapped port for tsdb (5432 belongs to the 'db' service)
DB_NAME     = os.getenv("DB_NAME",     "hft")
DB_USER     = os.getenv("DB_USER",     "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")
SYMBOL      = os.getenv("SYMBOL",      "XAU_USD")

# Timeframe label → pandas offset alias
TF_ALIAS: dict[str, str] = {
    "1m":  "1min",
    "3m":  "3min",
    "15m": "15min",
    "1h":  "1h",
    "4h":  "4h",
    "1d":  "1D",
}

# How many days of tick history to fetch per timeframe (sensible defaults)
TF_DAYS: dict[str, int] = {
    "1m":  30,
    "3m":  30,
    "15m": 30,
    "1h":  30,
    "4h":  30,
    "1d":  30,
}


# ─────────────────────────────────────────────────────────────────────────────
def connect_db() -> "psycopg2.extensions.connection":
    """Return an open psycopg2 connection to TimescaleDB."""
    print(f"[DB] Connecting  {DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME}/",DB_PASSWORD)

    try:
        conn = psycopg2.connect(
            host=DB_HOST, database=DB_NAME,
            user=DB_USER, password=DB_PASSWORD, port=DB_PORT,
        )
        print(f"[DB] Connected  {DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME}")
        return conn
    except Exception as exc:
        print(f"[DB] FATAL – cannot connect: {exc}")
        sys.exit(1)


def fetch_ticks(days: int = 7, symbol: str = SYMBOL) -> pd.DataFrame:
    """
    Pull the last `days` calendar days of tick data for `symbol`.
    Returns a DataFrame with columns:
        time  (datetime64, UTC, tz-aware)
        price (float64)
    """
    conn   = connect_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT time, ltp AS price
        FROM   ltp
        WHERE  symbol = %s
          AND  time  >= NOW() - (%s || ' days')::INTERVAL
        ORDER  BY time ASC;
        """,
        (symbol, str(days)),
    )
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        print(f"[WARN] No tick data for symbol={symbol} in the last {days} days.")
        sys.exit(0)

    df = pd.DataFrame(rows, columns=["time", "price"])
    df["time"]  = pd.to_datetime(df["time"], utc=True)
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df = df.dropna(subset=["price"])

    print(
        f"[DB] Fetched {len(df):,} ticks  "
        f"({df['time'].iloc[0].strftime('%Y-%m-%d %H:%M')} UTC  →  "
        f"{df['time'].iloc[-1].strftime('%Y-%m-%d %H:%M')} UTC)"
    )
    return df


def build_candles(ticks: pd.DataFrame, tf: str) -> pd.DataFrame:
    """
    Resample tick data into OHLCV candles.

    Parameters
    ----------
    ticks : DataFrame[time (tz-aware UTC), price]
    tf    : "1m" | "3m" | "15m" | "1h" | "4h" | "1d"

    Returns
    -------
    DataFrame[time (tz-naive, datetime64[ns]), open, high, low, close, volume]
    lightweight_charts requires tz-naive datetime64[ns] in the time column.
    """
    alias = TF_ALIAS.get(tf)
    if alias is None:
        raise ValueError(f"Unknown timeframe '{tf}'. Valid: {list(TF_ALIAS)}")

    # Ensure price index is tz-aware and sorted
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

    # Keep UTC version (for logic, debugging, titles)
    # ohlcv["time_utc"] = ohlcv["time"]

    # Convert for chart (naive)
    ohlcv["time"] = (
        ohlcv["time"]
        .dt.tz_convert(None)
        .astype("datetime64[ns]")
    )

    # ── Ensure all OHLC values are plain Python float (not Decimal / object) ──
    for col in ["open", "high", "low", "close"]:
        ohlcv[col] = ohlcv[col].astype(float)
    ohlcv["volume"] = ohlcv["volume"].astype(float)

    if ohlcv.empty:
        print(f"[WARN] No candles built for {tf} — check that tick data exists.")
    else:
        print(
            f"[CANDLES] {tf}  →  {len(ohlcv):,} candles  "
            f"({ohlcv['time'].iloc[0]}  →  {ohlcv['time'].iloc[-1]})"
        )
    return ohlcv

def format_chart(chart):
    chart.layout(
        background_color="#DBDBDB",
        text_color="#000000",


    )

    chart.candle_style(
        up_color="#089981",
        down_color="#000000",
        border_up_color="#000000",
        border_down_color="#000000",
        wick_up_color="#000000",
        wick_down_color="#000000",
    )

    chart.grid(vert_enabled=False, horz_enabled=False)
    chart.legend(False)