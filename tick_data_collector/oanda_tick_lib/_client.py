import logging
import time
from datetime import datetime, timedelta, timezone

import pytz
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ._exceptions import OandaAuthError, OandaAPIError

log = logging.getLogger(__name__)

_PRACTICE_URL = "https://api-fxpractice.oanda.com/v3"
_LIVE_URL      = "https://api-fxtrade.oanda.com/v3"

_DELTA_MAP: dict[str, timedelta] = {
    "S5":  timedelta(seconds=5),
    "M1":  timedelta(minutes=1),
    "M5":  timedelta(minutes=5),
    "M15": timedelta(minutes=15),
    "H1":  timedelta(hours=1),
    "H4":  timedelta(hours=4),
    "D":   timedelta(days=1),
}

_MAX_BATCH = 5000


class OandaClient:
    """
    Low-level OANDA REST client.

    Handles authentication, pagination, retries, and timeouts.
    All HTTP errors surface as OandaAuthError or OandaAPIError —
    never as raw requests exceptions.
    """

    def __init__(
        self,
        api_key: str,
        instrument: str,
        interval: str,
        practice: bool,
        timeout: int,
    ) -> None:
        if interval not in _DELTA_MAP:
            raise ValueError(
                f"Unknown interval {interval!r}. Valid: {sorted(_DELTA_MAP)}"
            )

        self.instrument = instrument
        self.interval   = interval
        self._base_url  = _PRACTICE_URL if practice else _LIVE_URL
        self._timeout   = timeout
        self._tz        = pytz.timezone("Asia/Kolkata")
        self._session   = self._build_session(api_key)

        if not practice:
            log.warning(
                "practice=False — requests will be sent to the LIVE OANDA endpoint (%s).",
                _LIVE_URL,
            )

    # ── public ──────────────────────────────────────────────────────────────

    def fetch_day(self, day: int, month: int, year: int) -> list[dict]:
        """
        Return all complete S5 candles for the 24-hour IST window that maps
        to the given calendar date.

        The window is  [midnight_IST(date) - 24 h,  midnight_IST(date))
        so that file  <day>_<month>_<year>.json  holds exactly one IST day,
        matching the existing cache convention.
        """
        step    = _DELTA_MAP[self.interval]
        end_dt  = self._tz.localize(datetime(year, month, day, 0, 0, 0))
        count   = int(timedelta(hours=24) / step)
        start_dt = end_dt - step * count

        log.debug(
            "fetch_day %d-%02d-%02d  window %s → %s  (%d candles)",
            year, month, day, start_dt, end_dt, count,
        )
        return self._paginate(start_dt, end_dt)

    def fetch_latest(self, n: int = 2) -> list[dict]:
        """
        Return the *n* most recent candles for this instrument / interval.

        The last entry may be incomplete (still-open bar); callers should
        filter on ``candle["complete"]`` if they only want closed bars.
        """
        url = f"{self._base_url}/instruments/{self.instrument}/candles"
        params = {
            "count":       n,
            "granularity": self.interval,
            "price":       "M",
        }

        body = self._get(url, params)
        raw = body.get("candles")
        if raw is None:
            raise OandaAPIError(200, f"Response missing 'candles' key: {str(body)[:200]}")

        result = []
        for c in raw:
            mid = c.get("mid", {})
            result.append({
                "time":     c["time"],
                "open":     mid.get("o", "0"),
                "high":     mid.get("h", "0"),
                "low":      mid.get("l", "0"),
                "close":    mid.get("c", "0"),
                "volume":   c.get("volume", 0),
                "complete": c.get("complete", False),
            })
        return result

    # ── internal ────────────────────────────────────────────────────────────

    @staticmethod
    def _build_session(api_key: str) -> requests.Session:
        session = requests.Session()
        session.headers.update({
            "Authorization":          f"Bearer {api_key}",
            "Accept-Datetime-Format": "RFC3339",
        })
        # Retry on transient server/rate-limit errors; 401/403 are NOT retried.
        retry = Retry(
            total=4,
            backoff_factor=1.5,          # waits: 0 s, 1.5 s, 3 s, 6 s
            status_forcelist={429, 500, 502, 503, 504},
            raise_on_status=False,       # let our code inspect the final response
        )
        session.mount("https://", HTTPAdapter(max_retries=retry))
        return session

    def _get(self, url: str, params: dict) -> dict:
        """Execute one GET request and return the parsed JSON body."""
        try:
            resp = self._session.get(url, params=params, timeout=self._timeout)
        except requests.exceptions.RetryError as exc:
            raise OandaAPIError(0, f"All retries exhausted: {exc}") from exc
        except requests.exceptions.Timeout as exc:
            raise OandaAPIError(0, f"Request timed out after {self._timeout}s") from exc
        except requests.exceptions.ConnectionError as exc:
            raise OandaAPIError(0, f"Connection error: {exc}") from exc

        if resp.status_code in (401, 403):
            raise OandaAuthError(
                f"HTTP {resp.status_code}: invalid or missing API key — "
                "check your credentials and practice/live setting."
            )
        if resp.status_code != 200:
            raise OandaAPIError(resp.status_code, resp.text[:400])

        try:
            return resp.json()
        except ValueError as exc:
            raise OandaAPIError(resp.status_code, f"Non-JSON response: {resp.text[:200]}") from exc

    def _paginate(self, start_dt: datetime, end_dt: datetime) -> list[dict]:
        url   = f"{self._base_url}/instruments/{self.instrument}/candles"
        step  = _DELTA_MAP[self.interval]
        ohlcs: list[dict] = []
        current = start_dt

        while current < end_dt:
            batch_end = min(current + step * _MAX_BATCH, end_dt)
            params = {
                "from":              current.astimezone(timezone.utc).isoformat(),
                "to":                batch_end.astimezone(timezone.utc).isoformat(),
                "granularity":       self.interval,
                "price":             "M",
                "smooth":            False,
                "includeFirst":      False,
                "alignmentTimezone": "UTC",
                "dailyAlignment":    0,
            }

            body = self._get(url, params)

            batch_candles = body.get("candles")
            if batch_candles is None:
                raise OandaAPIError(200, f"Response missing 'candles' key: {str(body)[:200]}")

            for candle in batch_candles:
                if not candle.get("complete"):
                    continue
                mid = candle.get("mid", {})
                ohlcs.append({
                    "time":   candle["time"],
                    "open":   mid["o"],
                    "high":   mid["h"],
                    "low":    mid["l"],
                    "close":  mid["c"],
                    "volume": candle["volume"],
                })

            log.debug(
                "  batch %s → %s  got %d candles (total so far: %d)",
                current.isoformat(), batch_end.isoformat(), len(batch_candles), len(ohlcs),
            )
            current = batch_end
            time.sleep(0.2)  # OANDA free-tier: ~100 req/s; 0.2 s keeps us well under

        return ohlcs
