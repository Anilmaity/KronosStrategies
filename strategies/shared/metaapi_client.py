"""
metaapi_client.py
-----------------
Thin MetaAPI REST client for placing and closing MARKET orders.

Credentials read from env:
  META_API_TOKEN    – JWT auth token
  META_ACCOUNT_ID   – MT4/MT5 account UUID
  DRY_RUN           – 'true' to skip real broker calls (default: false)

The account's regional trading host is resolved once via the provisioning API
and cached for the lifetime of the process.
"""
from __future__ import annotations

import os
import time
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

_TOKEN      = os.getenv("META_API_TOKEN", "")
_ACCOUNT    = os.getenv("META_ACCOUNT_ID", "")
_DRY_RUN    = os.getenv("DRY_RUN", "false").lower() == "true"

# MetaAPI provisioning host. The single-label `agiliumtrade.ai` provisioning
# subdomain was retired (NXDOMAIN); the live one is the double-label domain.
# Only used when a management-scoped token is available — the trading token is
# client-scoped and gets 403 here, so region resolution normally relies on
# META_REGION / the default below.
_PROVISION_URL = os.getenv(
    "META_PROVISION_URL",
    "https://mt-provisioning-api-v1.agiliumtrade.agiliumtrade.ai",
)
# Client/trading hosts still serve on the single-label domain.
_CLIENT_DOMAIN   = os.getenv("META_CLIENT_DOMAIN", "agiliumtrade.ai").strip()
# Explicit region override — skips the provisioning lookup entirely.
_REGION_OVERRIDE = os.getenv("META_REGION", "").strip()
# Fallback region when neither override nor provisioning yields one.
_DEFAULT_REGION  = os.getenv("META_DEFAULT_REGION", "new-york").strip()
_TRADING_URL: str | None = None   # resolved lazily (module-level, used by close_position_by_id)

# OANDA instrument → MetaAPI broker symbol
_SYMBOL_MAP = {
    "XAU_USD": "XAUUSD",
}

_TIMEOUT = 15  # seconds

# Per-symbol specification cache: {broker_symbol: {"stops_level_price": float, "tick_size": float}}
_SPEC_CACHE: dict[str, dict] = {}

# Safety fallback when the broker doesn't expose a stops level: never let
# SL or TP sit closer than this many price units from entry.
_DEFAULT_MIN_STOP_DISTANCE = 3.0  # XAUUSD: 3 dollars ≈ 30 pips


def _headers() -> dict:
    return {"auth-token": _TOKEN, "Content-Type": "application/json"}


def _trading_url() -> str:
    """Resolve and cache the regional trading host for this account.

    Precedence:
      1. META_REGION env override — skips provisioning entirely (the trading
         token is client-scoped and cannot call the provisioning API).
      2. Provisioning API lookup — only succeeds with a management-scoped token.
      3. META_DEFAULT_REGION fallback (default 'new-york').
    """
    global _TRADING_URL
    if _TRADING_URL:
        return _TRADING_URL

    region = _REGION_OVERRIDE
    if region:
        log.info("[MetaAPI] Using META_REGION override: %s", region)
    else:
        try:
            resp = requests.get(
                f"{_PROVISION_URL}/users/current/accounts/{_ACCOUNT}",
                headers=_headers(),
                timeout=10,
            )
            resp.raise_for_status()
            region = resp.json().get("region") or _DEFAULT_REGION
        except Exception:
            log.warning("[MetaAPI] Could not fetch account region — defaulting to %s", _DEFAULT_REGION)
            region = _DEFAULT_REGION

    _TRADING_URL = f"https://mt-client-api-v1.{region}.{_CLIENT_DOMAIN}"
    log.info("[MetaAPI] Resolved trading host: %s", _TRADING_URL)
    return _TRADING_URL


def _get_symbol_spec(broker_symbol: str) -> dict:
    """Fetch and cache the broker's specification for a symbol.

    Returns dict with keys:
      stops_level_price: float — minimum SL/TP distance from market in price units
      tick_size:         float — broker tickSize (informational)
    Falls back to {_DEFAULT_MIN_STOP_DISTANCE, 0.01} on any error.
    """
    if broker_symbol in _SPEC_CACHE:
        return _SPEC_CACHE[broker_symbol]

    spec = {"stops_level_price": _DEFAULT_MIN_STOP_DISTANCE, "tick_size": 0.01}
    if _DRY_RUN or not _TOKEN or not _ACCOUNT:
        _SPEC_CACHE[broker_symbol] = spec
        return spec

    try:
        url = f"{_trading_url()}/users/current/accounts/{_ACCOUNT}/symbols/{broker_symbol}/specification"
        resp = requests.get(url, headers=_headers(), timeout=10)
        resp.raise_for_status()
        data = resp.json()
        tick_size   = float(data.get("tickSize", 0.01))
        stops_level = int(data.get("stopsLevel", 0))
        stops_price = stops_level * tick_size if stops_level > 0 else _DEFAULT_MIN_STOP_DISTANCE
        spec = {"stops_level_price": stops_price, "tick_size": tick_size}
        log.info("[MetaAPI] %s spec: tickSize=%s stopsLevel=%d → min_distance=%.2f",
                 broker_symbol, tick_size, stops_level, stops_price)
    except Exception:
        log.exception("[MetaAPI] _get_symbol_spec failed for %s — using fallback %.2f",
                      broker_symbol, _DEFAULT_MIN_STOP_DISTANCE)

    _SPEC_CACHE[broker_symbol] = spec
    return spec


def _apply_stops_floor(side: str, entry_price: float, stop_loss: float, take_profit: float,
                      min_distance: float) -> tuple[float, float, bool]:
    """Widen SL/TP outward so each sits at least `min_distance` from entry.
    Returns (new_sl, new_tp, was_adjusted)."""
    adjusted = False
    if side == "BUY":
        if (entry_price - stop_loss) < min_distance:
            stop_loss = round(entry_price - min_distance, 2); adjusted = True
        if (take_profit - entry_price) < min_distance:
            take_profit = round(entry_price + min_distance, 2); adjusted = True
    else:  # SELL
        if (stop_loss - entry_price) < min_distance:
            stop_loss = round(entry_price + min_distance, 2); adjusted = True
        if (entry_price - take_profit) < min_distance:
            take_profit = round(entry_price - min_distance, 2); adjusted = True
    return stop_loss, take_profit, adjusted


# ---------------------------------------------------------------------------
# Per-account client (used for strategies that have per-UserBroker creds)
# ---------------------------------------------------------------------------

class MetaApiClient:
    """Per-account MetaAPI REST client.

    Each instance holds its own account_id + token so that multiple
    broker accounts can be served from the same process without colliding
    on module-level env globals.
    """

    def __init__(self, account_id: str, token: str) -> None:
        self.account_id = account_id
        self.token = token
        self._trading_url_cache: str | None = None
        self._spec_cache: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Internal helpers (per-instance copies of the module functions)
    # ------------------------------------------------------------------

    def _headers(self) -> dict:
        return {"auth-token": self.token, "Content-Type": "application/json"}

    def _trading_url(self) -> str:
        """Resolve and cache the regional trading host for this account.

        Precedence:
          1. META_REGION env override — skips provisioning entirely (the trading
             token is client-scoped and cannot call the provisioning API).
          2. Provisioning API lookup — only succeeds with a management-scoped token.
          3. META_DEFAULT_REGION fallback (default 'new-york').
        """
        if self._trading_url_cache:
            return self._trading_url_cache

        region = _REGION_OVERRIDE
        if region:
            log.info("[MetaAPI] Using META_REGION override: %s", region)
        else:
            try:
                resp = requests.get(
                    f"{_PROVISION_URL}/users/current/accounts/{self.account_id}",
                    headers=self._headers(),
                    timeout=10,
                )
                resp.raise_for_status()
                region = resp.json().get("region") or _DEFAULT_REGION
            except Exception:
                log.warning("[MetaAPI] Could not fetch account region — defaulting to %s", _DEFAULT_REGION)
                region = _DEFAULT_REGION

        self._trading_url_cache = f"https://mt-client-api-v1.{region}.{_CLIENT_DOMAIN}"
        log.info("[MetaAPI] Resolved trading host: %s", self._trading_url_cache)
        return self._trading_url_cache

    def close_position_by_id(self, meta_position_id: str) -> bool:
        """Close an open MetaAPI position on THIS account. False on any error.
        Mirrored in position_manager/shared — the monitor's active exits
        (TIME_EXIT/TRAIL) close through this per-account path since the
        2026-07-23 fix (env-singleton closes hit the wrong region/account)."""
        if _DRY_RUN:
            log.info("[MetaAPI DRY_RUN] close_position_by_id positionId=%s", meta_position_id)
            return True
        if not meta_position_id or meta_position_id == "dry-run":
            return False

        payload = {"actionType": "POSITION_CLOSE_ID", "positionId": meta_position_id}
        try:
            url = f"{self._trading_url()}/users/current/accounts/{self.account_id}/trade"
            resp = requests.post(url, headers=self._headers(), json=payload, timeout=_TIMEOUT)
            resp.raise_for_status()
            log.info("[MetaAPI] Position closed | account=%s positionId=%s",
                     self.account_id, meta_position_id)
            return True
        except requests.HTTPError as exc:
            log.warning(
                "[MetaAPI] close_position HTTP %s (account=%s): %s",
                exc.response.status_code, self.account_id, exc.response.text,
            )
        except Exception:
            log.exception("[MetaAPI] Failed to close position %s (account=%s)",
                          meta_position_id, self.account_id)
        return False

    def _get_symbol_spec(self, broker_symbol: str) -> dict:
        """Fetch and cache the broker's specification for a symbol.

        Returns dict with keys:
          stops_level_price: float — minimum SL/TP distance from market in price units
          tick_size:         float — broker tickSize (informational)
        Falls back to {_DEFAULT_MIN_STOP_DISTANCE, 0.01} on any error.
        """
        if broker_symbol in self._spec_cache:
            return self._spec_cache[broker_symbol]

        spec = {"stops_level_price": _DEFAULT_MIN_STOP_DISTANCE, "tick_size": 0.01}
        if _DRY_RUN or not self.token or not self.account_id:
            self._spec_cache[broker_symbol] = spec
            return spec

        try:
            url = f"{self._trading_url()}/users/current/accounts/{self.account_id}/symbols/{broker_symbol}/specification"
            resp = requests.get(url, headers=self._headers(), timeout=10)
            resp.raise_for_status()
            data = resp.json()
            tick_size   = float(data.get("tickSize", 0.01))
            stops_level = int(data.get("stopsLevel", 0))
            stops_price = stops_level * tick_size if stops_level > 0 else _DEFAULT_MIN_STOP_DISTANCE
            spec = {"stops_level_price": stops_price, "tick_size": tick_size}
            log.info("[MetaAPI] %s spec: tickSize=%s stopsLevel=%d → min_distance=%.2f",
                     broker_symbol, tick_size, stops_level, stops_price)
        except Exception:
            log.exception("[MetaAPI] _get_symbol_spec failed for %s — using fallback %.2f",
                          broker_symbol, _DEFAULT_MIN_STOP_DISTANCE)

        self._spec_cache[broker_symbol] = spec
        return spec

    def place_market_order(
        self,
        side: str,
        symbol: str,
        volume: float,
        stop_loss: float,
        take_profit: float,
        entry_price: float | None = None,
    ) -> str | None:
        """
        Place a MARKET order with SL and TP.

        If `entry_price` is provided, SL/TP are widened to honour the broker's
        stops level (queried lazily from MetaAPI) so the order won't be rejected
        with TRADE_RETCODE_INVALID_STOPS.

        Returns the MetaAPI positionId (broker ticket) on success, None on failure.
        On DRY_RUN returns the sentinel string 'dry-run'.
        """
        broker_symbol = _SYMBOL_MAP.get(symbol, symbol)
        action = "ORDER_TYPE_BUY" if side == "BUY" else "ORDER_TYPE_SELL"

        # Honour the broker's stops level — widen SL/TP if too close to entry.
        if entry_price is not None:
            min_d = self._get_symbol_spec(broker_symbol)["stops_level_price"]
            new_sl, new_tp, adjusted = _apply_stops_floor(side, entry_price, stop_loss, take_profit, min_d)
            if adjusted:
                log.info("[MetaAPI] stops widened to broker floor (%.2f): SL %.2f→%.2f TP %.2f→%.2f",
                         min_d, stop_loss, new_sl, take_profit, new_tp)
            stop_loss, take_profit = new_sl, new_tp

        payload = {
            "actionType": action,
            "symbol": broker_symbol,
            "volume": round(volume, 2),
            "stopLoss": round(stop_loss, 2),
            "takeProfit": round(take_profit, 2),
        }

        if _DRY_RUN:
            log.info("[MetaAPI DRY_RUN] place_market_order payload=%s", payload)
            return "dry-run"

        if not self.token or not self.account_id:
            log.error("[MetaAPI] account_id / token not configured")
            return None

        try:
            url = f"{self._trading_url()}/users/current/accounts/{self.account_id}/trade"
            resp = requests.post(url, headers=self._headers(), json=payload, timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()

            # MetaAPI returns orderId and positionId (same value for market orders)
            pos_id = data.get("positionId") or data.get("orderId") or ""
            code   = data.get("stringCode", "")

            if "DONE" not in code and pos_id == "":
                log.warning("[MetaAPI] Unexpected trade response: %s", data)
                return None

            log.info(
                "[MetaAPI] Order placed | %s %s vol=%.2f SL=%.2f TP=%.2f | positionId=%s",
                side, broker_symbol, volume, stop_loss, take_profit, pos_id,
            )
            return pos_id or None

        except requests.HTTPError as exc:
            log.error("[MetaAPI] HTTP error %s: %s", exc.response.status_code, exc.response.text)
        except Exception:
            log.exception("[MetaAPI] Failed to place order")
        return None

    def get_position_fill(self, position_id: str, *, retries: int = 6,
                          delay: float = 0.5) -> float | None:
        """Real entry fill (openPrice) for a just-opened position, or None.

        Market fills land in well under a second; poll briefly so place_entry
        can book the Position at BROKER TRUTH instead of the signal price
        (2026-07-07: trade #1 booked 4137.27 vs real fill 4136.90). Returns
        None on the 'dry-run' sentinel, timeouts, or if the position already
        closed before we could read it — callers fall back to the signal
        price and the fill_reconciler trues it up within minutes.
        """
        if not position_id or not str(position_id).isdigit():
            return None                      # dry-run sentinel / no ticket
        url = (f"{self._trading_url()}/users/current/accounts/"
               f"{self.account_id}/positions/{position_id}")
        for attempt in range(retries):
            try:
                resp = requests.get(url, headers=self._headers(), timeout=10)
                if resp.status_code == 200:
                    px = (resp.json() or {}).get("openPrice")
                    if px:
                        return float(px)
                # 404: not registered yet (or already closed) — retry briefly
            except Exception as exc:
                log.warning("[MetaAPI] get_position_fill attempt %d: %s",
                            attempt + 1, exc)
            time.sleep(delay)
        log.warning("[MetaAPI] no fill price for position %s after %.1fs — "
                    "caller falls back to signal price (reconciler will fix)",
                    position_id, retries * delay)
        return None


# ---------------------------------------------------------------------------
# Per-broker client factory (looks up per-account creds from DB)
# ---------------------------------------------------------------------------

_CLIENT_CACHE: dict[str, "MetaApiClient"] = {}


def client_for_broker(session, user_broker_id) -> "MetaApiClient | None":
    """Return a MetaApiClient for the broker's stored creds, or None (→ refuse)."""
    # Lazy imports to avoid DB engine initialisation during module load and
    # potential circular imports when running under pytest.
    from shared.models import UserBroker
    from shared.crypto import decrypt_token

    broker = session.query(UserBroker).filter_by(id=user_broker_id).first()
    if broker is None:
        log.warning("[MetaAPI] UserBroker %s not found — refusing", user_broker_id)
        return None
    acct = (getattr(broker, "meta_account_id", "") or "").strip()
    enc = getattr(broker, "meta_api_token_enc", "") or ""
    if not acct or not enc:
        log.warning("[MetaAPI] account %s has no per-account creds — refusing", user_broker_id)
        return None
    if acct in _CLIENT_CACHE:
        return _CLIENT_CACHE[acct]
    try:
        token = decrypt_token(enc)
    except Exception as e:
        log.error("[MetaAPI] decrypt failed for %s: %s — refusing", user_broker_id, e)
        return None
    client = MetaApiClient(acct, token)
    _CLIENT_CACHE[acct] = client
    return client


def close_position_by_id(meta_position_id: str) -> bool:
    """
    Close an open MetaAPI position by its positionId.
    Best-effort: logs and returns False on any error.
    """
    if _DRY_RUN:
        log.info("[MetaAPI DRY_RUN] close_position_by_id positionId=%s", meta_position_id)
        return True

    if not meta_position_id or meta_position_id == "dry-run":
        return False

    payload = {"actionType": "POSITION_CLOSE_ID", "positionId": meta_position_id}

    try:
        url = f"{_trading_url()}/users/current/accounts/{_ACCOUNT}/trade"
        resp = requests.post(url, headers=_headers(), json=payload, timeout=_TIMEOUT)
        resp.raise_for_status()
        log.info("[MetaAPI] Position closed | positionId=%s", meta_position_id)
        return True
    except requests.HTTPError as exc:
        # 404 / position already closed is not fatal
        log.warning(
            "[MetaAPI] close_position HTTP %s (already closed?): %s",
            exc.response.status_code, exc.response.text,
        )
    except Exception:
        log.exception("[MetaAPI] Failed to close position %s", meta_position_id)
    return False
