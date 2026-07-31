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
from uuid import uuid4

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
_TRADING_URL: str | None = None   # resolved lazily

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
# opt15 Task 2: safe retries with verify-before-retry
#
# A MetaAPI market order that 504s or times out MUST NOT be blindly resent -- a
# timeout does not mean the order failed. Every retry first looks the order's
# clientId up via the positions/orders endpoint; only a positive "absent"
# confirmation permits a re-POST. A failed lookup returns the error unchanged
# (exactly the pre-opt15 single-attempt behavior). Closes are idempotent
# against a position id, so they just retry on timeout/5xx and treat an
# already-closed position (404) as success. Retries default OFF (0) until the
# broker's clientId dedup guarantee is verified; set META_ORDER_MAX_RETRIES>0
# to opt in.
# ---------------------------------------------------------------------------

def _order_max_retries() -> int:
    try:
        return max(0, int(os.getenv("META_ORDER_MAX_RETRIES", "0")))
    except ValueError:
        return 0


def _order_retry_base_sec() -> float:
    try:
        return max(0.0, float(os.getenv("META_ORDER_RETRY_BASE_SEC", "2")))
    except ValueError:
        return 2.0


def _retry_backoff_sec(attempt: int, base_sec: float) -> float:
    """Backoff before retry `attempt` (0-indexed). base 2 -> 2s, 5s, 12.5s..."""
    return base_sec * (2.5 ** attempt)


def _lookup_order_by_client_id(base_url: str, account_id: str, headers: dict,
                               client_id: str,
                               timeout: float = _TIMEOUT) -> tuple[str, str | None]:
    """Positively determine whether an order/position tagged `client_id` exists.

    Returns one of:
      ("found", position_id)  -- the order landed; treat as success
      ("absent", None)        -- confirmed not present; safe to re-POST
      ("failed", None)        -- lookup inconclusive; caller must NOT retry
    Any transport error or 5xx on the lookup is inconclusive ("failed"), so we
    never re-POST an order we could not prove is absent.
    """
    for endpoint in ("positions", "orders"):
        try:
            resp = requests.get(
                f"{base_url}/users/current/accounts/{account_id}/{endpoint}",
                headers=headers, timeout=timeout,
            )
            if resp.status_code >= 500:
                return "failed", None
            resp.raise_for_status()
            for item in (resp.json() or []):
                if item.get("clientId") == client_id:
                    pid = item.get("positionId") or item.get("id") or item.get("orderId")
                    return "found", (str(pid) if pid is not None else None)
        except Exception:
            return "failed", None
    return "absent", None


def _post_order_with_retry(base_url: str, account_id: str, headers: dict,
                           payload: dict, side: str, broker_symbol: str,
                           volume: float, stop_loss: float,
                           take_profit: float) -> str | None:
    """POST a market order, retrying safely on timeout/5xx (opt15 Task 2).

    Returns the broker positionId on success, None on failure. Preserves the
    single-attempt success/error contract: 4xx and unexpected responses return
    None without a retry, and META_ORDER_MAX_RETRIES=0 makes exactly one POST.
    """
    client_id = payload.get("clientId", "")
    max_retries = _order_max_retries()
    base_sec = _order_retry_base_sec()
    url = f"{base_url}/users/current/accounts/{account_id}/trade"

    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=_TIMEOUT)
            if resp.status_code >= 500:
                log.warning("[MetaAPI] order HTTP %s (clientId=%s) attempt %d/%d",
                            resp.status_code, client_id, attempt + 1, max_retries + 1)
            else:
                resp.raise_for_status()          # 4xx -> HTTPError (not retried)
                data = resp.json()
                pos_id = data.get("positionId") or data.get("orderId") or ""
                code = data.get("stringCode", "")
                if "DONE" not in code and pos_id == "":
                    log.warning("[MetaAPI] Unexpected trade response: %s", data)
                    return None
                log.info(
                    "[MetaAPI] Order placed | %s %s vol=%.2f SL=%.2f TP=%.2f | positionId=%s",
                    side, broker_symbol, volume, stop_loss, take_profit, pos_id,
                )
                return pos_id or None
        except requests.HTTPError as exc:
            sc = exc.response.status_code if exc.response is not None else "?"
            txt = exc.response.text if exc.response is not None else ""
            log.error("[MetaAPI] HTTP error %s (clientId=%s): %s", sc, client_id, txt)
            return None
        except (requests.Timeout, requests.ConnectionError) as exc:
            log.warning("[MetaAPI] order network error (clientId=%s) attempt %d/%d: %s",
                        client_id, attempt + 1, max_retries + 1, exc)
        except requests.RequestException as exc:
            log.warning("[MetaAPI] order request error (clientId=%s) attempt %d/%d: %s",
                        client_id, attempt + 1, max_retries + 1, exc)
        except Exception:
            log.exception("[MetaAPI] Failed to place order (clientId=%s)", client_id)
            return None

        # Only a transient failure (5xx / timeout / connection) reaches here.
        if attempt >= max_retries:
            log.error("[MetaAPI] order not placed after %d attempt(s) (clientId=%s) -- giving up",
                      attempt + 1, client_id)
            return None
        if not client_id:
            log.error("[MetaAPI] no clientId to verify -- not retrying")
            return None
        state, found_pid = _lookup_order_by_client_id(base_url, account_id, headers, client_id)
        if state == "found":
            log.info("[MetaAPI] verify-before-retry: clientId=%s already landed (positionId=%s)"
                     " -- success, no re-POST", client_id, found_pid)
            if found_pid:
                return found_pid
            log.error("[MetaAPI] clientId=%s landed but id unresolved -- RECONCILE MANUALLY",
                      client_id)
            return None
        if state == "failed":
            log.info("[MetaAPI] verify-before-retry: lookup FAILED for clientId=%s -- NOT retrying"
                     " (cannot confirm order is absent)", client_id)
            return None
        delay = _retry_backoff_sec(attempt, base_sec)
        log.info("[MetaAPI] verify-before-retry: clientId=%s confirmed ABSENT -- re-POST %d/%d"
                 " after %.1fs", client_id, attempt + 1, max_retries, delay)
        time.sleep(delay)

    return None


def _close_with_retry(base_url: str, account_id: str, headers: dict,
                      meta_position_id: str, log_ctx: str = "") -> bool:
    """Close a position by id, retrying on timeout/5xx (opt15 Task 2).

    Idempotent: a 404 (position not found / already closed) counts as success,
    matching how the monitor's _CLOSE_MAX_ATTEMPTS loop reasons about a flat
    position. Other 4xx is terminal (not retried). META_ORDER_MAX_RETRIES=0
    makes exactly one attempt. Returns True on close/already-closed, else False.
    """
    max_retries = _order_max_retries()
    base_sec = _order_retry_base_sec()
    url = f"{base_url}/users/current/accounts/{account_id}/trade"
    payload = {"actionType": "POSITION_CLOSE_ID", "positionId": meta_position_id}
    suffix = f" (account={log_ctx})" if log_ctx else ""

    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=_TIMEOUT)
            status = resp.status_code
            if status == 404:
                log.info("[MetaAPI] close positionId=%s -> 404 already closed%s -- success",
                         meta_position_id, suffix)
                return True
            if status >= 500:
                log.warning("[MetaAPI] close HTTP %s%s positionId=%s attempt %d/%d",
                            status, suffix, meta_position_id, attempt + 1, max_retries + 1)
            else:
                resp.raise_for_status()          # other 4xx -> HTTPError (terminal)
                log.info("[MetaAPI] Position closed%s | positionId=%s", suffix, meta_position_id)
                return True
        except requests.HTTPError as exc:
            sc = exc.response.status_code if exc.response is not None else "?"
            if sc == 404:
                log.info("[MetaAPI] close positionId=%s -> 404 already closed%s -- success",
                         meta_position_id, suffix)
                return True
            log.warning("[MetaAPI] close HTTP %s%s: %s", sc, suffix,
                        exc.response.text if exc.response is not None else "")
            return False
        except (requests.Timeout, requests.ConnectionError, requests.RequestException) as exc:
            log.warning("[MetaAPI] close network error%s positionId=%s attempt %d/%d: %s",
                        suffix, meta_position_id, attempt + 1, max_retries + 1, exc)
        except Exception:
            log.exception("[MetaAPI] Failed to close position %s%s", meta_position_id, suffix)
            return False

        if attempt >= max_retries:
            log.warning("[MetaAPI] close not confirmed after %d attempt(s)%s positionId=%s",
                        attempt + 1, suffix, meta_position_id)
            return False
        delay = _retry_backoff_sec(attempt, base_sec)
        time.sleep(delay)

    return False


def place_market_order(
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
        min_d = _get_symbol_spec(broker_symbol)["stops_level_price"]
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

    if not _TOKEN or not _ACCOUNT:
        log.error("[MetaAPI] META_API_TOKEN / META_ACCOUNT_ID not configured")
        return None

    # opt15 Task 2: tag the order so a retry can verify-before-resend.
    # With retries disabled the payload stays byte-identical to the old path.
    if _order_max_retries() > 0:
        payload["clientId"] = f"kr-{uuid4().hex[:16]}"

    return _post_order_with_retry(
        _trading_url(), _ACCOUNT, _headers(), payload,
        side, broker_symbol, volume, stop_loss, take_profit,
    )


# ---------------------------------------------------------------------------
# Per-account client (mirrors strategies/shared/metaapi_client.py) — the env
# singleton below resolves the region from the env token, which is
# client-scoped and cannot call the provisioning API, so it falls back to
# META_DEFAULT_REGION and 504s on accounts in other regions (the Jul-14
# TIME_EXIT failures). The per-broker creds resolve their own region the same
# way the entry manager's working order path does.
# ---------------------------------------------------------------------------

class MetaApiClient:
    """Per-account MetaAPI REST client (close path only — the monitor never
    opens positions)."""

    def __init__(self, account_id: str, token: str) -> None:
        self.account_id = account_id
        self.token = token
        self._trading_url_cache: str | None = None

    def _headers(self) -> dict:
        return {"auth-token": self.token, "Content-Type": "application/json"}

    def _trading_url(self) -> str:
        """Resolve and cache the regional trading host for this account.
        Precedence: META_REGION override -> provisioning lookup -> default."""
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
                log.warning("[MetaAPI] Could not fetch account region — defaulting to %s",
                            _DEFAULT_REGION)
                region = _DEFAULT_REGION

        self._trading_url_cache = f"https://mt-client-api-v1.{region}.{_CLIENT_DOMAIN}"
        log.info("[MetaAPI] Resolved trading host: %s (account %s)",
                 self._trading_url_cache, self.account_id)
        return self._trading_url_cache

    def close_position_by_id(self, meta_position_id: str) -> bool:
        """Close an open MetaAPI position on THIS account. False on any error."""
        if _DRY_RUN:
            log.info("[MetaAPI DRY_RUN] close_position_by_id positionId=%s", meta_position_id)
            return True
        if not meta_position_id or meta_position_id == "dry-run":
            return False

        return _close_with_retry(self._trading_url(), self.account_id,
                                 self._headers(), meta_position_id, self.account_id)


_CLIENT_CACHE: dict[str, "MetaApiClient"] = {}


def client_for_broker(session, user_broker_id) -> "MetaApiClient | None":
    """Return a MetaApiClient for the broker's stored creds, or None (-> caller
    falls back to the env-singleton close)."""
    from shared.models import UserBroker
    from shared.crypto import decrypt_token

    broker = session.query(UserBroker).filter_by(id=user_broker_id).first()
    if broker is None:
        log.warning("[MetaAPI] UserBroker %s not found", user_broker_id)
        return None
    acct = (getattr(broker, "meta_account_id", "") or "").strip()
    enc = getattr(broker, "meta_api_token_enc", "") or ""
    if not acct or not enc:
        log.warning("[MetaAPI] account %s has no per-account creds", user_broker_id)
        return None
    if acct in _CLIENT_CACHE:
        return _CLIENT_CACHE[acct]
    try:
        token = decrypt_token(enc)
    except Exception as e:
        log.error("[MetaAPI] decrypt failed for %s: %s", user_broker_id, e)
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

    return _close_with_retry(_trading_url(), _ACCOUNT, _headers(), meta_position_id, _ACCOUNT)
