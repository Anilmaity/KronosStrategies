"""
tsdb_reader.py
--------------
Market-data helpers for the Automation strategies.

Candles are now fetched LIVE from the OANDA REST API (mid prices) instead of
TimescaleDB, so the strategies no longer depend on the tick DB or the tick
collectors. A short in-process TTL cache keeps OANDA request volume low even
though the runners poll every few seconds.

Public API:
    fetch_candles(tf, days, symbol)  -> OHLCV DataFrame        (unchanged)
    fetch_latest_ltp(symbol)         -> float | None           (unchanged)
    fetch_latest_bidask(symbol)      -> tuple[float, float] | None
    fetch_latest_spread(symbol)      -> float | None

`_connect()` is retained only for the offline DB utility scripts that still
import it (backfill/validate); the live trading path never calls it.
"""

import logging
import os
import time
import threading
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv

log = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
for _env_path in [
    os.path.join(_HERE, ".env"),
    os.path.join(_HERE, "..", ".env"),
    os.path.join(_HERE, "..", "..", ".env"),
]:
    if os.path.exists(_env_path):
        load_dotenv(_env_path)
        break

# ── OANDA config ─────────────────────────────────────────────────────────────
_OANDA_API_KEY  = os.getenv("OANDA_API_KEY", "").strip()
_OANDA_PRACTICE = os.getenv("OANDA_PRACTICE", "true").strip().lower() not in ("false", "0", "no")
_OANDA_BASE     = "https://api-fxpractice.oanda.com/v3" if _OANDA_PRACTICE else "https://api-fxtrade.oanda.com/v3"
_CANDLE_TTL     = int(os.getenv("OANDA_CANDLE_TTL_SEC", "20"))   # cache candle fetches this long
_HTTP_TIMEOUT   = int(os.getenv("OANDA_HTTP_TIMEOUT", "30"))
_MAX_BATCH      = 4500                                            # OANDA hard cap is 5000 candles/request

# OANDA market-data timeframe -> granularity
_TF_GRAN = {
    "1m":  "M1",
    "5m":  "M5",
    "15m": "M15",
    "1h":  "H1",
    "4h":  "H4",
    "1d":  "D",
}
_GRAN_DELTA = {
    "S5":  timedelta(seconds=5),
    "M1":  timedelta(minutes=1),
    "M5":  timedelta(minutes=5),
    "M15": timedelta(minutes=15),
    "H1":  timedelta(hours=1),
    "H4":  timedelta(hours=4),
    "D":   timedelta(days=1),
}

_session = requests.Session()
_session.headers.update({
    "Authorization":          f"Bearer {_OANDA_API_KEY}",
    "Accept-Datetime-Format": "RFC3339",
})
# Retry transient server/rate-limit errors on GETs (all OANDA reads are GET);
# mirrors tick_data_collector/oanda_tick_lib/_client.py. 401/403 are not retried.
# raise_on_status=False lets _oanda_get() inspect the final response as it does today.
_retry = Retry(
    total=3,
    backoff_factor=0.5,                              # waits: 0 s, 0.5 s, 1 s, 2 s
    status_forcelist={429, 500, 502, 503, 504},
    allowed_methods={"GET"},
    raise_on_status=False,
)
_session.mount("https://", HTTPAdapter(max_retries=_retry))

_cache_lock = threading.Lock()
_candle_cache: dict = {}   # (symbol, gran, days) -> (cached_at_epoch, DataFrame)


def _cache_fresh(gran: str, cached_at: float, now: float) -> bool:
    """True while a cached candle frame may still be served.

    Plain TTL for every granularity, plus an extra bar-boundary check for the
    fast frames so a freshly-closed bar is never masked by a still-live TTL:
      * M1 -> invalidate once a new UTC minute boundary has passed
      * S5 -> invalidate once a new 5-second boundary has passed
    """
    if now - cached_at >= _CANDLE_TTL:
        return False
    if gran == "M1" and int(now / 60) > int(cached_at / 60):
        return False
    if gran == "S5" and int(now / 5) > int(cached_at / 5):
        return False
    return True

# ── legacy DB DSN (offline scripts only) ─────────────────────────────────────
_TIGERDATA_URL = os.getenv("TIGERDATA_URL", "").strip()
IN_DOCKER = os.path.exists("/.dockerenv")
if IN_DOCKER:
    _TSDB_HOST = os.getenv("TSDB_HOST") or os.getenv("DB_HOST") or "tsdb"
    _TSDB_PORT = int(os.getenv("TSDB_PORT") or os.getenv("DB_PORT") or "5432")
else:
    _TSDB_HOST = "127.0.0.1"
    _TSDB_PORT = 5433
_TSDB_NAME = os.getenv("TSDB_NAME") or os.getenv("DB_NAME") or "hft"
_TSDB_USER = os.getenv("TSDB_USER") or os.getenv("DB_USER") or "postgres"
_TSDB_PASS = os.getenv("TSDB_PASS") or os.getenv("DB_PASSWORD") or "postgres"


def _connect():
    """Legacy TimescaleDB connection — used only by offline backfill/validate scripts."""
    import psycopg2
    if _TIGERDATA_URL:
        return psycopg2.connect(_TIGERDATA_URL)
    return psycopg2.connect(
        host=_TSDB_HOST, port=_TSDB_PORT,
        database=_TSDB_NAME, user=_TSDB_USER, password=_TSDB_PASS,
    )


# ── OANDA fetch ──────────────────────────────────────────────────────────────
def _oanda_get(url: str, params: dict) -> dict:
    resp = _session.get(url, params=params, timeout=_HTTP_TIMEOUT)
    if resp.status_code in (401, 403):
        raise RuntimeError(f"OANDA auth HTTP {resp.status_code} — check OANDA_API_KEY / practice flag")
    if resp.status_code != 200:
        raise RuntimeError(f"OANDA HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def _fetch_oanda_ohlc(instrument: str, granularity: str, days: int) -> list:
    """Return a list of (time, o, h, l, c, volume) tuples for complete candles.

    Pages forward with `from` + `count` (never sends `to`, which OANDA rejects
    when it lands on/after "now"). Stops when a short batch signals we've caught
    up to the present.
    """
    step  = _GRAN_DELTA[granularity]
    start = datetime.now(timezone.utc) - timedelta(days=max(days, 1))
    url   = f"{_OANDA_BASE}/instruments/{instrument}/candles"

    rows: list = []
    seen: set = set()
    cur = start
    for _ in range(60):   # safety cap on pages
        params = {
            "from":              cur.isoformat().replace("+00:00", "Z"),
            "count":             _MAX_BATCH,
            "granularity":       granularity,
            "price":             "M",
            "alignmentTimezone": "UTC",
            "dailyAlignment":    0,
        }
        body = _oanda_get(url, params)
        batch = body.get("candles", [])
        if not batch:
            break
        for c in batch:
            ts = c["time"]
            if not c.get("complete") or ts in seen:
                continue
            seen.add(ts)
            mid = c.get("mid", {})
            rows.append((ts, mid.get("o"), mid.get("h"), mid.get("l"), mid.get("c"), c.get("volume", 0)))
        if len(batch) < _MAX_BATCH:
            break   # caught up to the present
        last_ts = pd.to_datetime(batch[-1]["time"], utc=True).to_pydatetime()
        cur = last_ts + step
        time.sleep(0.15)   # stay polite to OANDA when paginating
    return rows


def fetch_candles(tf: str, days: int = 7, symbol: str = "XAU_USD") -> pd.DataFrame:
    """Return OHLCV DataFrame (live OANDA candles). Returns empty DF on no data/error."""
    gran = _TF_GRAN.get(tf)
    if gran is None:
        raise ValueError(f"Unknown timeframe '{tf}'. Valid: {list(_TF_GRAN)}")

    empty = pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    key = (symbol, gran, int(days))
    now = time.time()

    with _cache_lock:
        hit = _candle_cache.get(key)
        if hit and _cache_fresh(gran, hit[0], now):
            return hit[1].copy()

    try:
        rows = _fetch_oanda_ohlc(symbol, gran, days)
    except Exception as exc:
        log.warning("[OANDA] fetch_candles error (%s %s %sd): %s", symbol, gran, days, exc)
        with _cache_lock:                       # serve last good data if we have it
            hit = _candle_cache.get(key)
        return hit[1].copy() if hit else empty

    if not rows:
        return empty

    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert(None).astype("datetime64[ns]")
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").astype(float)
    df = df.dropna(subset=["open", "close"]).reset_index(drop=True)

    with _cache_lock:
        _candle_cache[key] = (now, df)
    return df.copy()


def fetch_latest_ltp(symbol: str = "XAU_USD") -> float | None:
    """Return the most recent mid price from OANDA (latest S5 candle close), or None on error."""
    url = f"{_OANDA_BASE}/instruments/{symbol}/candles"
    params = {"count": 1, "granularity": "S5", "price": "M"}
    try:
        body = _oanda_get(url, params)
        candles = body.get("candles", [])
        if not candles:
            return None
        return float(candles[-1]["mid"]["c"])
    except Exception as exc:
        log.warning("[OANDA] fetch_latest_ltp error (%s): %s", symbol, exc)
        return None


def fetch_latest_bidask(symbol: str = "XAU_USD") -> tuple[float, float] | None:
    """Return (bid_close, ask_close) from the latest OANDA S5 candle, or None on error.

    Mirrors fetch_latest_ltp but requests price="BA" so both sides are returned.
    Uncached today (same as fetch_latest_ltp); kept as a thin single-call helper so
    a short TTL cache can wrap it later without changing callers.
    """
    url = f"{_OANDA_BASE}/instruments/{symbol}/candles"
    params = {"count": 1, "granularity": "S5", "price": "BA"}
    try:
        body = _oanda_get(url, params)
        candles = body.get("candles", [])
        if not candles:
            return None
        last = candles[-1]
        return float(last["bid"]["c"]), float(last["ask"]["c"])
    except Exception as exc:
        log.warning("[OANDA] fetch_latest_bidask error (%s): %s", symbol, exc)
        return None


def fetch_latest_spread(symbol: str = "XAU_USD") -> float | None:
    """Return the latest ask-minus-bid spread (price units), or None on error."""
    ba = fetch_latest_bidask(symbol)
    if ba is None:
        return None
    bid, ask = ba
    return ask - bid
