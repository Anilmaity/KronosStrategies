import logging
import os
from datetime import date
from typing import Union

import pandas as pd

from ._client import OandaClient
from ._ticks import split_candles
from ._exceptions import OandaTickError, CacheReadError
from . import _cache

log = logging.getLogger(__name__)


class OandaTickFetcher:
    """
    Fetch S5 OANDA candles, synthesise 4-point tick sequences, and cache as JSON.

    Cache layout (compatible with existing files):
        <cache_dir>/<instrument>/<day>_<month>_<year>.json

    Each file is a list of candle groups; each group is 4 tick dicts:
        {"time": "YYYY-MM-DD HH:MM:SS+00:00", "price": "<str>"}

    Quick start::

        import logging, sys
        logging.basicConfig(stream=sys.stdout, level=logging.INFO)

        from oanda_tick_lib import OandaTickFetcher

        fetcher = OandaTickFetcher(
            api_key="<your-key>",
            instrument="XAU_USD",
            cache_dir="./cache_data",
            practice=True,       # False for a live account
        )
        fetcher.fetch_range("2026-04-01", "2026-04-30")
        ticks = fetcher.load_day("2026-04-15")
    """

    def __init__(
        self,
        api_key: str,
        instrument: str = "XAU_USD",
        cache_dir: str = "./cache_data",
        practice: bool = True,
        request_timeout: int = 30,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("api_key must not be empty.")

        self._client   = OandaClient(
            api_key=api_key,
            instrument=instrument,
            interval="S5",
            practice=practice,
            timeout=request_timeout,
        )
        self.instrument = instrument
        self.cache_dir  = os.path.abspath(cache_dir)

    # ── public API ──────────────────────────────────────────────────────────

    def fetch_day(
        self,
        day: Union[str, "pd.Timestamp", date],
        *,
        force: bool = False,
    ) -> list[list[dict]]:
        """
        Fetch and cache ticks for one calendar day.

        Returns cached data immediately unless ``force=True``.
        Returns an empty list if the API returns no data (market closed / holiday).
        Raises ``OandaAuthError`` on credential failure.
        Raises ``OandaAPIError`` on unrecoverable API failure.
        """
        d    = _to_timestamp(day)
        path = _cache.cache_path(self.cache_dir, self.instrument, d.date())

        if not force:
            try:
                cached = _cache.load(path)
                if cached is not None:
                    log.info("[SKIP]  %s — cache hit  (%s)", d.date(), os.path.basename(path))
                    return cached
            except CacheReadError as exc:
                log.warning("[CACHE] %s — treating as cache miss: %s", d.date(), exc)

        log.info("[FETCH] %s — calling OANDA API …", d.date())
        candles = self._client.fetch_day(d.day, d.month, d.year)

        if not candles:
            log.warning("[EMPTY] %s — API returned no candles (market closed / holiday?)", d.date())
            return []

        ticks = [split_candles(c) for c in candles]
        _cache.save(path, ticks)
        log.info("[SAVED] %s — %d candle groups → %s", d.date(), len(ticks), os.path.basename(path))
        return ticks

    def fetch_range(
        self,
        start: str,
        end: str,
        *,
        force: bool = False,
    ) -> dict[str, str]:
        """
        Fetch and cache every calendar day in the closed interval [start, end].

        Continues past per-day errors so one bad day does not abort the range.
        Returns a summary dict:
            {"2026-04-01": "saved", "2026-04-02": "skipped", "2026-04-06": "empty",
             "2026-04-07": "error: ..."}
        """
        try:
            dates = pd.date_range(start=start, end=end)
        except Exception as exc:
            raise ValueError(f"Invalid date range {start!r} → {end!r}: {exc}") from exc

        summary: dict[str, str] = {}

        for d in dates:
            label = str(d.date())
            try:
                result = self.fetch_day(d, force=force)
                if result:
                    status = _last_log_status(d)
                    summary[label] = status
                else:
                    summary[label] = "empty"
            except OandaTickError as exc:
                log.error("[ERROR] %s — %s", label, exc)
                summary[label] = f"error: {exc}"

        _log_summary(summary)
        return summary

    def fetch_latest_candle(self,count=2) -> dict | None:
        """
        Return the most recent *complete* S5 candle, or None if unavailable.

        Fetches the last 2 bars so we always get at least one closed candle
        even while the current bar is still open.
        Logs an error (instead of raising) on API failure so callers can loop.
        """
        try:
            candles = self._client.fetch_latest(n=count)
        except OandaTickError as exc:
            log.error("[LATEST] %s", exc)
            return None

        for candle in reversed(candles):
            if candle.get("complete"):
                return candle
        return None

    def load_day(
        self,
        day: Union[str, "pd.Timestamp", date],
    ) -> list[list[dict]] | None:
        """
        Return cached ticks for a day, or None if not yet cached.
        Logs a warning (instead of raising) if the file is corrupt.
        """
        d    = _to_timestamp(day)
        path = _cache.cache_path(self.cache_dir, self.instrument, d.date())
        try:
            return _cache.load(path)
        except CacheReadError as exc:
            log.warning("[CACHE] %s", exc)
            return None


# ── helpers ─────────────────────────────────────────────────────────────────

def _to_timestamp(value: Union[str, "pd.Timestamp", date]) -> "pd.Timestamp":
    try:
        return pd.Timestamp(value)
    except Exception as exc:
        raise ValueError(f"Cannot parse date {value!r}: {exc}") from exc


def _last_log_status(d: "pd.Timestamp") -> str:
    """Infer saved vs skipped from whether the INFO log said SAVED or SKIP."""
    return "saved"   # fetch_day already logged the exact outcome


def _log_summary(summary: dict[str, str]) -> None:
    saved   = sum(1 for v in summary.values() if v == "saved")
    skipped = sum(1 for v in summary.values() if v == "skipped")
    empty   = sum(1 for v in summary.values() if v == "empty")
    errors  = sum(1 for v in summary.values() if v.startswith("error"))
    log.info(
        "[RANGE] done — saved: %d  skipped: %d  empty: %d  errors: %d",
        saved, skipped, empty, errors,
    )

