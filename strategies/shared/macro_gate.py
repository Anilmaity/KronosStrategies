"""
shared/macro_gate.py
--------------------
v15d production macro gate -- TNX (US 10Y yield) `|z_20d| >= 0.5`.

Drop a trading day when the prior-day TNX close is within 0.5 sigma of its
trailing 20-day mean -- i.e. the rates regime is "quiet". The v15d
optstack search (`backtest/reports/EXP_V15D_TNX_OPTSTACK_REPORT.md`,
TradingSkills) selected `T_baseline | TWThF | TNXz20_ge0.50` as the best
in-band (5-10 tpd) variant: 78.2% OOS win-day rate at 5.01 tpd, Wilson 95%
CI [67.8%, 85.9%].

USAGE (inside a strategy):

    from shared.macro_gate import tnx_gate_open
    if not tnx_gate_open(now_utc):
        return None       # skip this trade -- macro gate is closed

CACHE / REFRESH:
- Loads cached daily TNX bars from
  `KronosStrategies/strategies/shared/data/tnx_daily.parquet`.
- Falls back to the TradingSkills cache at
  `TradingSkills/backtest/data/macro__TNX_daily.parquet` if the local file
  is missing.
- On first call per process, the parquet is read once. After that the DF is
  kept in-memory; every call checks whether the in-memory df's last close
  is older than `now_utc.date() - 2` -- if so we try to refresh via
  yfinance (runs once per day in practice).
- If yfinance is unavailable / fetch fails / no data on disk -> we LOG A
  WARNING AND FAIL OPEN (return True). Rationale: it is better to take a
  trade and check later than to silently block every Kronos strategy
  because of a temporary network outage. Document any closed-day P&L
  delta vs the cached-gate result during reconciliation.

CLI:
    python -m shared.macro_gate refresh
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_LOCAL_DATA_DIR = os.path.join(_HERE, "data")
_LOCAL_PARQUET = os.path.join(_LOCAL_DATA_DIR, "tnx_daily.parquet")

# Optional fallback into the TradingSkills repo cache. Useful on workstations
# that already have the v15d backtest data downloaded; production containers
# will use the local parquet that ships with the image.
_FALLBACK_CANDIDATES = [
    os.path.normpath(os.path.join(
        _HERE, "..", "..", "..", "TradingSkills", "backtest", "data",
        "macro__TNX_daily.parquet",
    )),
    os.path.normpath(os.path.join(
        _HERE, "..", "..", "..", "..", "TradingSkills", "backtest", "data",
        "macro__TNX_daily.parquet",
    )),
]

_REFRESH_TICKER = "^TNX"
_REFRESH_LOOKBACK_DAYS = 200    # comfortably more than the 20-day rolling window
_REFRESH_STALENESS_DAYS = 2     # if last_close < today - 2 -> attempt refresh
_Z_WINDOW = 20
_Z_THRESHOLD = 0.5

# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------
_cache_df: Optional[pd.DataFrame] = None
_cache_loaded_at: Optional[datetime] = None
_cache_last_refresh_attempt: Optional[datetime] = None
_cache_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _read_parquet_any() -> Optional[pd.DataFrame]:
    """Read the TNX parquet from the first source that exists.
    Returns a normalised DataFrame with `time` (UTC tz-aware) and `close`.
    """
    candidates = [_LOCAL_PARQUET, *_FALLBACK_CANDIDATES]
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            df = pd.read_parquet(path)
            if "time" not in df.columns or "close" not in df.columns:
                continue
            df = df[["time", "close"]].copy()
            df["time"] = pd.to_datetime(df["time"], utc=True)
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            df = df.dropna(subset=["close"]).sort_values("time").reset_index(drop=True)
            log.info("[macro_gate] loaded TNX parquet: rows=%d src=%s "
                     "first=%s last=%s",
                     len(df), path, df["time"].iloc[0].date(),
                     df["time"].iloc[-1].date())
            return df
        except Exception:
            log.exception("[macro_gate] failed to read %s", path)
    return None


def _fetch_yfinance() -> Optional[pd.DataFrame]:
    """Re-fetch ^TNX daily bars covering the trailing ~200 days via yfinance.
    Returns a normalised DataFrame or None on any failure (fail-open).
    """
    try:
        import yfinance as yf
    except ImportError:
        log.warning("[macro_gate] yfinance not installed -- cannot refresh; "
                    "gate will use cached parquet only")
        return None
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=_REFRESH_LOOKBACK_DAYS)
    try:
        raw = yf.download(
            _REFRESH_TICKER,
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False,
        )
    except Exception as e:
        log.warning("[macro_gate] yfinance fetch error: %s", e)
        return None
    if raw is None or len(raw) == 0:
        log.warning("[macro_gate] yfinance returned empty TNX frame")
        return None
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [c[0] if isinstance(c, tuple) else c for c in raw.columns]
    raw = raw.reset_index().rename(columns={"Date": "time", "Datetime": "time"})
    raw.columns = [c.lower() if isinstance(c, str) else c for c in raw.columns]
    if "time" not in raw.columns or "close" not in raw.columns:
        log.warning("[macro_gate] yfinance frame missing time/close")
        return None
    raw["time"] = pd.to_datetime(raw["time"], utc=True)
    raw["close"] = pd.to_numeric(raw["close"], errors="coerce")
    raw = raw[["time", "close"]].dropna(subset=["close"]) \
        .sort_values("time").reset_index(drop=True)
    return raw if len(raw) else None


def _persist(df: pd.DataFrame) -> None:
    """Best-effort write of the refreshed parquet to the local cache."""
    try:
        os.makedirs(_LOCAL_DATA_DIR, exist_ok=True)
        df.to_parquet(_LOCAL_PARQUET, index=False)
        log.info("[macro_gate] saved TNX parquet rows=%d -> %s",
                 len(df), _LOCAL_PARQUET)
    except Exception:
        log.exception("[macro_gate] could not persist TNX parquet")


def _maybe_refresh(df: Optional[pd.DataFrame], now_utc: datetime) -> Optional[pd.DataFrame]:
    """Refresh the cached DF from yfinance at most once per process per day."""
    global _cache_last_refresh_attempt

    today = now_utc.date()
    if _cache_last_refresh_attempt is not None and \
            _cache_last_refresh_attempt.date() == today:
        return df

    last_close = None
    if df is not None and len(df) > 0:
        last_close = df["time"].iloc[-1].date()
    if last_close is not None and last_close >= (today - timedelta(days=_REFRESH_STALENESS_DAYS)):
        # fresh enough -- skip the network call
        return df

    log.info("[macro_gate] TNX cache stale (last_close=%s, today=%s); "
             "attempting yfinance refresh", last_close, today)
    _cache_last_refresh_attempt = now_utc
    new = _fetch_yfinance()
    if new is None or len(new) == 0:
        return df
    _persist(new)
    return new


def _ensure_loaded(now_utc: datetime) -> Optional[pd.DataFrame]:
    """Get the cached DF (loading from parquet on first call). Triggers a
    once-per-day refresh attempt if the cache is older than the staleness
    threshold.
    """
    global _cache_df, _cache_loaded_at
    with _cache_lock:
        if _cache_df is None:
            _cache_df = _read_parquet_any()
            _cache_loaded_at = now_utc
        # try to refresh stale caches (silent fail-open inside _maybe_refresh)
        refreshed = _maybe_refresh(_cache_df, now_utc)
        if refreshed is not None and refreshed is not _cache_df:
            _cache_df = refreshed
            _cache_loaded_at = now_utc
        return _cache_df


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def tnx_gate_open(now_utc: datetime) -> bool:
    """Return True if the TNX |z_20d| >= 0.5 gate is OPEN for the day of
    `now_utc` (UTC). Causal -- only uses TNX bars with date < today.

    Fail-open semantics: any failure to compute (no cache, fetch error,
    insufficient history) logs a WARNING and returns True. Better to take
    a trade than block the whole suite on a transient data outage.
    """
    if now_utc is None:
        return True
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    df = _ensure_loaded(now_utc)
    if df is None or len(df) < _Z_WINDOW + 2:
        log.warning("[macro_gate] TNX cache unavailable / too short -- "
                    "FAILING OPEN (returning True)")
        return True

    today = now_utc.date()
    # Use only bars strictly BEFORE today (causal: yesterday's close).
    past = df[df["time"].dt.date < today]
    if len(past) < _Z_WINDOW + 1:
        log.warning("[macro_gate] insufficient TNX history before %s "
                    "(have %d, need %d) -- FAIL OPEN",
                    today, len(past), _Z_WINDOW + 1)
        return True

    c = past["close"].astype(float)
    last_close = float(c.iloc[-1])
    window = c.iloc[-(_Z_WINDOW + 1):-1]   # the 20 days strictly BEFORE last_close
    if len(window) < _Z_WINDOW:
        log.warning("[macro_gate] short rolling window (%d/%d) -- FAIL OPEN",
                    len(window), _Z_WINDOW)
        return True
    mu = float(window.mean())
    sd = float(window.std(ddof=1))
    if sd <= 0 or not (sd == sd):  # nan check
        log.warning("[macro_gate] zero / nan std on TNX rolling window -- "
                    "FAIL OPEN")
        return True

    z = (last_close - mu) / sd
    is_open = abs(z) >= _Z_THRESHOLD
    log.debug("[macro_gate] day=%s last_TNX_close=%.3f mu20=%.3f sd20=%.3f "
              "z=%.3f thr=%.2f -> %s",
              today, last_close, mu, sd, z, _Z_THRESHOLD,
              "OPEN" if is_open else "CLOSED")
    return bool(is_open)


def refresh() -> None:
    """Force a yfinance re-fetch of ^TNX and overwrite the local parquet.
    Intended for the CLI (`python -m shared.macro_gate refresh`) or
    occasional manual cache rebuilds."""
    global _cache_df, _cache_loaded_at, _cache_last_refresh_attempt
    log.info("[macro_gate] manual refresh requested")
    df = _fetch_yfinance()
    if df is None or len(df) == 0:
        log.warning("[macro_gate] manual refresh failed -- no data fetched")
        return
    _persist(df)
    with _cache_lock:
        _cache_df = df
        _cache_loaded_at = datetime.now(timezone.utc)
        _cache_last_refresh_attempt = _cache_loaded_at


def _cli() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    if len(sys.argv) >= 2 and sys.argv[1] == "refresh":
        refresh()
        return 0
    # Default: print today's gate decision for ad-hoc verification.
    now = datetime.now(timezone.utc)
    decision = tnx_gate_open(now)
    print(f"TNX gate @ {now.isoformat()} -> {'OPEN' if decision else 'CLOSED'}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
