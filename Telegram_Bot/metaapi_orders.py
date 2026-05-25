"""
MetaAPI client extension for the Telegram signal trader.

Adds on top of Kronos App / Automation / utils / metaapi_client.py:
  - place_limit_order()        — pending limit order with SL + single TP
  - place_market_order_full()  — market order with SL + single TP
  - modify_position_sl()       — move SL on an open position
  - cancel_order()             — cancel a pending order
  - get_symbol_price()         — current bid/ask for fill-mode decision

Multi-TP strategy:
  The channel posts 3 TPs per signal. We submit 3 SEPARATE orders, each with
  the SAME entry+SL and a DIFFERENT TP. Volume is split equally. When any TP
  hits, only its slice closes; when SL hits, all three close at the same level.
  This sidesteps MetaAPI's one-TP-per-position constraint.

Env vars (same as Kronos App client):
  META_API_TOKEN, META_ACCOUNT_ID, DRY_RUN
"""
from __future__ import annotations

import logging
import os
from typing import Literal

import requests
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("metaapi-orders")

_TOKEN = os.getenv("META_API_TOKEN", "")
_ACCOUNT = os.getenv("META_ACCOUNT_ID", "")
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

_PROVISION_URL = "https://mt-provisioning-api-v1.agiliumtrade.ai"
_TRADING_URL: str | None = None
_TIMEOUT = 15

_SYMBOL_MAP = {"XAUUSD": "XAUUSD", "XAU_USD": "XAUUSD"}

Side = Literal["buy", "sell"]


def _headers() -> dict:
    return {"auth-token": _TOKEN, "Content-Type": "application/json"}


def _trading_url() -> str:
    global _TRADING_URL
    if _TRADING_URL:
        return _TRADING_URL
    try:
        resp = requests.get(
            f"{_PROVISION_URL}/users/current/accounts/{_ACCOUNT}",
            headers=_headers(), timeout=10,
        )
        resp.raise_for_status()
        region = resp.json().get("region", "new-york")
    except Exception:
        log.warning("[MetaAPI] region lookup failed — defaulting to new-york")
        region = "new-york"
    _TRADING_URL = f"https://mt-client-api-v1.{region}.agiliumtrade.ai"
    log.info("[MetaAPI] trading host: %s", _TRADING_URL)
    return _TRADING_URL


def _trade(payload: dict) -> dict | None:
    if DRY_RUN:
        log.info("[DRY] MetaAPI trade payload=%s", payload)
        return {"positionId": "dry-run", "orderId": "dry-run", "stringCode": "DONE"}
    if not _TOKEN or not _ACCOUNT:
        log.error("[MetaAPI] credentials not configured")
        return None
    try:
        url = f"{_trading_url()}/users/current/accounts/{_ACCOUNT}/trade"
        resp = requests.post(url, headers=_headers(), json=payload, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as e:
        log.error("[MetaAPI] HTTP %s: %s", e.response.status_code, e.response.text)
    except Exception:
        log.exception("[MetaAPI] trade call failed")
    return None


def get_symbol_price(symbol: str) -> dict | None:
    """Return {'bid': float, 'ask': float} or None on failure."""
    broker = _SYMBOL_MAP.get(symbol, symbol)
    if DRY_RUN:
        return None  # caller will fall through to market-when-in-zone heuristic
    try:
        url = f"{_trading_url()}/users/current/accounts/{_ACCOUNT}/symbols/{broker}/current-price"
        resp = requests.get(url, headers=_headers(), timeout=_TIMEOUT)
        resp.raise_for_status()
        d = resp.json()
        return {"bid": float(d["bid"]), "ask": float(d["ask"])}
    except Exception:
        log.exception("[MetaAPI] price fetch failed for %s", broker)
        return None


def _action_for(side: Side, *, is_limit: bool, entry: float, current: float) -> str:
    """Pick correct MetaAPI actionType.

    For BUY: LIMIT if entry < current (waiting for dip), STOP if entry > current.
    For SELL: LIMIT if entry > current, STOP if entry < current.
    """
    if not is_limit:
        return "ORDER_TYPE_BUY" if side == "buy" else "ORDER_TYPE_SELL"
    if side == "buy":
        return "ORDER_TYPE_BUY_LIMIT" if entry <= current else "ORDER_TYPE_BUY_STOP"
    return "ORDER_TYPE_SELL_LIMIT" if entry >= current else "ORDER_TYPE_SELL_STOP"


def place_market_order_full(side: Side, symbol: str, volume: float,
                            sl: float, tp: float, comment: str = "") -> str | None:
    broker = _SYMBOL_MAP.get(symbol, symbol)
    payload = {
        "actionType": "ORDER_TYPE_BUY" if side == "buy" else "ORDER_TYPE_SELL",
        "symbol": broker,
        "volume": round(volume, 2),
        "stopLoss": round(sl, 2),
        "takeProfit": round(tp, 2),
        "comment": comment[:31],  # MetaAPI cap
    }
    resp = _trade(payload)
    if not resp:
        return None
    return resp.get("positionId") or resp.get("orderId")


def place_limit_order(side: Side, symbol: str, volume: float, entry: float,
                      sl: float, tp: float, current_price: float | None = None,
                      comment: str = "") -> str | None:
    broker = _SYMBOL_MAP.get(symbol, symbol)
    ref = current_price if current_price is not None else entry
    action = _action_for(side, is_limit=True, entry=entry, current=ref)
    payload = {
        "actionType": action,
        "symbol": broker,
        "volume": round(volume, 2),
        "openPrice": round(entry, 2),
        "stopLoss": round(sl, 2),
        "takeProfit": round(tp, 2),
        "comment": comment[:31],
    }
    resp = _trade(payload)
    if not resp:
        return None
    return resp.get("orderId") or resp.get("positionId")


def modify_position_sl(position_id: str, new_sl: float) -> bool:
    if position_id == "dry-run":
        log.info("[DRY] modify_position_sl position=%s sl=%s", position_id, new_sl)
        return True
    payload = {"actionType": "POSITION_MODIFY", "positionId": position_id,
               "stopLoss": round(new_sl, 2)}
    return _trade(payload) is not None


def cancel_order(order_id: str) -> bool:
    if order_id == "dry-run":
        log.info("[DRY] cancel_order order=%s", order_id)
        return True
    payload = {"actionType": "ORDER_CANCEL", "orderId": order_id}
    return _trade(payload) is not None


def close_position(position_id: str) -> bool:
    if position_id == "dry-run":
        log.info("[DRY] close_position position=%s", position_id)
        return True
    payload = {"actionType": "POSITION_CLOSE_ID", "positionId": position_id}
    return _trade(payload) is not None


def submit_signal_orders(side: Side, symbol: str, entry: float, sl: float,
                         tps: list[float], total_volume: float,
                         msg_id: int) -> list[dict]:
    """Submit one order per TP. Decide market vs limit based on current price.

    Returns list of dicts: {tp, ticket_id, kind, volume, entry, sl, tp}.
    """
    if not tps:
        return []
    px = get_symbol_price(symbol)
    cur = (px["ask"] if side == "buy" else px["bid"]) if px else None

    # If current price is already inside/past the entry, use market; else limit.
    use_market = False
    if cur is not None:
        if side == "buy" and cur <= entry:
            use_market = True
        if side == "sell" and cur >= entry:
            use_market = True

    vol_each = round(total_volume / len(tps), 2)
    if vol_each <= 0:
        log.warning("[%s] volume per TP rounds to 0 (total=%.2f, splits=%d)",
                    msg_id, total_volume, len(tps))
        return []

    submitted: list[dict] = []
    for i, tp in enumerate(tps, start=1):
        comment = f"tg-{msg_id}-tp{i}"
        if use_market:
            tid = place_market_order_full(side, symbol, vol_each, sl, tp, comment)
            kind = "market"
        else:
            tid = place_limit_order(side, symbol, vol_each, entry, sl, tp, cur, comment)
            kind = "limit"
        if not tid:
            log.error("[%s] order placement failed for TP%d — aborting remaining slices", msg_id, i)
            for prev in submitted:
                if prev["kind"] == "limit":
                    cancel_order(prev["ticket_id"])
                else:
                    close_position(prev["ticket_id"])
            return []
        submitted.append({"tp_index": i, "tp": tp, "ticket_id": tid, "kind": kind,
                          "volume": vol_each, "entry": entry, "sl": sl})
        log.info("[%s] %s order placed | TP%d=%s vol=%.2f ticket=%s",
                 msg_id, kind, i, tp, vol_each, tid)
    return submitted
