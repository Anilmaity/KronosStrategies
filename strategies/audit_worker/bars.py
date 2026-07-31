"""OANDA REST -> parquet bars cache for the Manager Backtest worker.

Builds the six ``is_XAU_USD_{tf}.parquet`` files ``manager_sim_engine.load_frames``
expects, in a worker-local cache dir: M1 is fetched from OANDA (paged, mid
prices), the five higher timeframes are resampled from that M1 via
``research.replay_lib.resample_ohlc``. S5 candles are fetched on demand for the
ambiguity resolver and appended to their own parquet.

Standalone by design: reuses ``shared/tsdb_reader.py``'s env/host/retry
conventions but does NOT import it (tsdb_reader lives in duplicated trees;
audit_worker is single-tree worker-only code).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger("audit_worker.bars")

_OANDA_API_KEY = os.getenv("OANDA_API_KEY", "").strip()
_OANDA_PRACTICE = os.getenv("OANDA_PRACTICE", "true").strip().lower() not in (
    "false", "0", "no")
_OANDA_BASE = ("https://api-fxpractice.oanda.com/v3" if _OANDA_PRACTICE
               else "https://api-fxtrade.oanda.com/v3")
_HTTP_TIMEOUT = int(os.getenv("OANDA_HTTP_TIMEOUT", "30"))
_MAX_BATCH = 4500          # OANDA hard cap is 5000 candles/request
INSTRUMENT = "XAU_USD"

# load_frames warms up max(FRAME_SPEC)+5 = 125 days before the sim start; give
# it 130 so the D1 frame always has full regime-lookback depth.
WARMUP_DAYS = 130

# Minimum cached S5 bars inside a span before the cache may satisfy it without
# a fetch (a full market minute holds 12; half covers thin-market minutes).
S5_COVERAGE_MIN = 6

_TF_RULES = {"5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h", "1d": "1d"}

_session = requests.Session()
_session.headers.update({"Authorization": f"Bearer {_OANDA_API_KEY}"})
_session.mount("https://", HTTPAdapter(max_retries=Retry(
    total=3, backoff_factor=0.5,
    status_forcelist={429, 500, 502, 503, 504},
    allowed_methods={"GET"},
)))


def _rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_candles(granularity: str, start_utc: datetime,
                  end_utc: datetime) -> pd.DataFrame:
    """Paged fetch of complete mid candles in [start_utc, end_utc].

    Returns columns time (tz-aware UTC), open, high, low, close, volume,
    sorted ascending, deduped. Empty DataFrame when OANDA has nothing.
    """
    from audit_worker import heartbeat

    rows: list[dict] = []
    cursor = start_utc
    while cursor < end_utc:
        heartbeat.touch()   # multi-month cold fetches outlast the healthcheck
        resp = _session.get(
            f"{_OANDA_BASE}/instruments/{INSTRUMENT}/candles",
            params={"granularity": granularity, "price": "M",
                    "from": _rfc3339(cursor), "count": _MAX_BATCH},
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        candles = [c for c in resp.json().get("candles", []) if c.get("complete")]
        if not candles:
            break
        for c in candles:
            t = pd.Timestamp(c["time"]).tz_convert("UTC")
            if t > end_utc:
                break
            rows.append({
                "time": t,
                "open": float(c["mid"]["o"]), "high": float(c["mid"]["h"]),
                "low": float(c["mid"]["l"]), "close": float(c["mid"]["c"]),
                "volume": int(c.get("volume", 0)),
            })
        last = pd.Timestamp(candles[-1]["time"]).tz_convert("UTC")
        if last <= cursor or last >= end_utc:
            break
        cursor = last.to_pydatetime()
    if not rows:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows).drop_duplicates(subset="time").sort_values("time")
    return df.reset_index(drop=True)


def _m1_path(cache_dir: Path) -> Path:
    return Path(cache_dir) / f"is_{INSTRUMENT}_1m.parquet"


def ensure_frames(cache_dir: Path, period_start_utc: datetime,
                  period_end_utc: datetime) -> Path:
    """Guarantee the six is_XAU_USD_{tf}.parquet files cover
    [period_start - WARMUP_DAYS, period_end]; fetch only the missing M1 head/
    tail, then rebuild the resampled TF parquets. Returns cache_dir."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    need_start = pd.Timestamp(period_start_utc).tz_convert("UTC") - pd.Timedelta(
        days=WARMUP_DAYS)
    need_end = pd.Timestamp(period_end_utc).tz_convert("UTC")

    m1_path = _m1_path(cache_dir)
    if m1_path.exists():
        m1 = pd.read_parquet(m1_path)
        m1["time"] = pd.to_datetime(m1["time"], utc=True)
    else:
        m1 = pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

    pieces = [m1]
    # Head gap. Tolerance 3 days: when need_start lands inside a weekend gap
    # the earliest existing candle can sit ~2 days later than requested; a
    # 1-day tolerance would refetch an empty head page on every call.
    if m1.empty or m1["time"].min() > need_start + pd.Timedelta(days=3):
        head_end = (m1["time"].min().to_pydatetime() if not m1.empty
                    else need_end.to_pydatetime())
        pieces.append(fetch_candles("M1", need_start.to_pydatetime(), head_end))
    # Tail gap. Tolerance 2 min: the newest complete M1 candle lags "now".
    if not m1.empty and m1["time"].max() < need_end - pd.Timedelta(minutes=2):
        pieces.append(fetch_candles(
            "M1", m1["time"].max().to_pydatetime(), need_end.to_pydatetime()))

    m1 = (pd.concat(pieces, ignore_index=True)
          .drop_duplicates(subset="time").sort_values("time")
          .reset_index(drop=True))
    if m1.empty:
        raise RuntimeError("no M1 candles available for the requested window")
    m1.to_parquet(m1_path, index=False)

    # Resample the higher TFs from naive-UTC M1 (house left/left convention).
    from research.replay_lib import resample_ohlc  # single-tree research code
    m1_naive = m1.copy()
    m1_naive["time"] = m1_naive["time"].dt.tz_localize(None)
    for tf, rule in _TF_RULES.items():
        frame = resample_ohlc(m1_naive[["time", "open", "high", "low", "close"]],
                              rule)
        frame.to_parquet(Path(cache_dir) / f"is_{INSTRUMENT}_{tf}.parquet",
                         index=False)
    return cache_dir


def ensure_s5(cache_dir: Path, start_utc: datetime,
              end_utc: datetime) -> pd.DataFrame:
    """S5 mid candles for the HALF-OPEN span [start_utc, end_utc), cached in
    one growing parquet. Returns the span slice; empty DataFrame (never an
    exception) when OANDA has no data for the span.

    Coverage rule: the cache satisfies a span only when it already holds at
    least S5_COVERAGE_MIN bars inside it. A single boundary bar left behind by
    an adjacent minute's fetch must NOT count as coverage — that would starve
    the resolver of the other ~11 bars and silently undercount flips."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"is_{INSTRUMENT}_S5.parquet"
    start_ts = pd.Timestamp(start_utc).tz_convert("UTC")
    end_ts = pd.Timestamp(end_utc).tz_convert("UTC")

    if path.exists():
        s5 = pd.read_parquet(path)
        s5["time"] = pd.to_datetime(s5["time"], utc=True)
        have = s5[(s5["time"] >= start_ts) & (s5["time"] < end_ts)]
        if len(have) >= S5_COVERAGE_MIN:
            return have.reset_index(drop=True)
    else:
        s5 = pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

    fetched = fetch_candles("S5", start_ts.to_pydatetime(), end_ts.to_pydatetime())
    if not fetched.empty:
        s5 = (pd.concat([s5, fetched], ignore_index=True)
              .drop_duplicates(subset="time").sort_values("time")
              .reset_index(drop=True))
        s5.to_parquet(path, index=False)
    out = s5[(s5["time"] >= start_ts) & (s5["time"] < end_ts)]
    return out.reset_index(drop=True)
