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

    try:
        url = f"{_trading_url()}/users/current/accounts/{_ACCOUNT}/trade"
        resp = requests.post(url, headers=_headers(), json=payload, timeout=_TIMEOUT)
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
