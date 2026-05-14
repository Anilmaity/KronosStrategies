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

_PROVISION_URL = "https://mt-provisioning-api-v1.agiliumtrade.ai"
_TRADING_URL: str | None = None   # resolved lazily

# OANDA instrument → MetaAPI broker symbol
_SYMBOL_MAP = {
    "XAU_USD": "XAUUSD",
}

_TIMEOUT = 15  # seconds


def _headers() -> dict:
    return {"auth-token": _TOKEN, "Content-Type": "application/json"}


def _trading_url() -> str:
    """Resolve and cache the regional trading host for this account."""
    global _TRADING_URL
    if _TRADING_URL:
        return _TRADING_URL

    try:
        resp = requests.get(
            f"{_PROVISION_URL}/users/current/accounts/{_ACCOUNT}",
            headers=_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        region = resp.json().get("region", "new-york")
    except Exception:
        log.warning("[MetaAPI] Could not fetch account region — defaulting to new-york")
        region = "new-york"

    _TRADING_URL = f"https://mt-client-api-v1.{region}.agiliumtrade.ai"
    log.info("[MetaAPI] Resolved trading host: %s", _TRADING_URL)
    return _TRADING_URL


def place_market_order(
    side: str,
    symbol: str,
    volume: float,
    stop_loss: float,
    take_profit: float,
) -> str | None:
    """
    Place a MARKET order with SL and TP.

    Returns the MetaAPI positionId (broker ticket) on success, None on failure.
    On DRY_RUN returns the sentinel string 'dry-run'.
    """
    broker_symbol = _SYMBOL_MAP.get(symbol, symbol)
    action = "ORDER_TYPE_BUY" if side == "BUY" else "ORDER_TYPE_SELL"

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
