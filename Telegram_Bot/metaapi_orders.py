"""
MetaAPI client for the Telegram signal trader.

Provides a MetaApiClient class so a single process can drive more than one
MetaAPI account (one Telegram listener fanning the same signal out to two
accounts — copy trading). Each instance owns its own token, resolved trading
host, and symbol-spec cache. The module-level functions remain as a
single-account convenience API delegating to a default client built from the
env (META_API_TOKEN / META_ACCOUNT_ID / DRY_RUN), so the single-account path is
unchanged.

Per client:
  - place_limit_order()        — pending limit order with SL + single TP
  - place_market_order_full()  — market order with SL + single TP
  - modify_position_sl()       — move SL on an open position
  - cancel_order()             — cancel a pending order
  - close_position()           — close an open position
  - get_open_positions()/get_pending_orders() — broker truth for reconciliation
  - get_symbol_price()         — current bid/ask for fill-mode decision
  - submit_signal_orders()     — one order per TP (multi-TP strategy)

Multi-TP strategy:
  The channel posts 3 TPs per signal. We submit 3 SEPARATE orders, each with
  the SAME entry+SL and a DIFFERENT TP. Volume is split equally. When any TP
  hits, only its slice closes; when SL hits, all three close at the same level.
  This sidesteps MetaAPI's one-TP-per-position constraint.

Env vars (for the default client):
  META_API_TOKEN, META_ACCOUNT_ID, DRY_RUN
"""
from __future__ import annotations

import logging
import os
import time
from typing import Literal

import requests
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("metaapi-orders")

_TOKEN = os.getenv("META_API_TOKEN", "")
_ACCOUNT = os.getenv("META_ACCOUNT_ID", "")
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

_PROVISION_URL = "https://mt-provisioning-api-v1.agiliumtrade.ai"
_TIMEOUT = 15

# MetaAPI's broker link blips for a few seconds at a time: the gateway returns
# 504/503/502 ("account is not connected to broker yet ... before retrying the
# request") or 429, and bare connection/read timeouts surface as requests
# exceptions. Without a retry a single blip aborts the whole signal (one failed
# slice cancels all TPs), which is how the 2026-06-04 signals were lost. Retry
# transient failures with backoff; a non-transient status (e.g. 400 bad order)
# returns immediately so the caller's raise_for_status surfaces the real error.
_RETRY_STATUSES = {429, 502, 503, 504}
_RETRY_ATTEMPTS = int(os.getenv("METAAPI_RETRY_ATTEMPTS", "3"))
_RETRY_BACKOFF = float(os.getenv("METAAPI_RETRY_BACKOFF", "1.5"))  # seconds, exponential


def _request(method: str, url: str, **kwargs) -> requests.Response:
    """HTTP request with bounded backoff retry on transient MetaAPI failures.

    Returns the final Response (the caller still calls raise_for_status); raises
    the last connection/timeout exception only if every attempt failed to reach
    the server. A non-transient HTTP status returns on the first try.
    """
    last_exc: Exception | None = None
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            resp = requests.request(method, url, **kwargs)
            if resp.status_code in _RETRY_STATUSES and attempt < _RETRY_ATTEMPTS:
                delay = _RETRY_BACKOFF * (2 ** (attempt - 1))
                log.warning("[MetaAPI] HTTP %s (attempt %d/%d) — retrying in %.1fs",
                            resp.status_code, attempt, _RETRY_ATTEMPTS, delay)
                time.sleep(delay)
                continue
            return resp
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exc = e
            if attempt < _RETRY_ATTEMPTS:
                delay = _RETRY_BACKOFF * (2 ** (attempt - 1))
                log.warning("[MetaAPI] %s (attempt %d/%d) — retrying in %.1fs",
                            type(e).__name__, attempt, _RETRY_ATTEMPTS, delay)
                time.sleep(delay)
                continue
            raise
    # Unreachable when attempts >= 1, but keeps type checkers happy.
    raise last_exc if last_exc else RuntimeError("retry loop exhausted")


_SYMBOL_MAP = {"XAUUSD": "XAUUSD", "XAU_USD": "XAUUSD"}

# Broker "stops level" — the minimum distance SL/TP may sit from the reference
# price. An order whose SL/TP is closer is rejected (TRADE_RETCODE_INVALID_STOPS),
# and in submit_signal_orders a single rejection aborts ALL three TP slices for
# that signal. We query it lazily per symbol; this is the fallback when the
# broker does not expose stopsLevel. Mirrors strategies/shared/metaapi_client.py.
_DEFAULT_MIN_STOP_DISTANCE = float(os.getenv("MIN_STOP_DISTANCE", "3.0"))  # XAUUSD: ~$3 ≈ 30 pips

Side = Literal["buy", "sell"]


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


def _apply_stops_floor(side: Side, ref_price: float, sl: float, tp: float,
                       min_distance: float) -> tuple[float, float, bool]:
    """Widen SL/TP *outward* so each sits at least `min_distance` from ref_price.

    ref_price is what the broker measures the stops level against: the current
    market price for a market order, or the open (entry) price for a pending
    limit. Only ever pushes SL/TP further away, so the signal's direction and
    is_malformed() invariants are preserved. Returns (sl, tp, was_adjusted).
    """
    adjusted = False
    if side == "buy":
        if (ref_price - sl) < min_distance:
            sl = round(ref_price - min_distance, 2); adjusted = True
        if (tp - ref_price) < min_distance:
            tp = round(ref_price + min_distance, 2); adjusted = True
    else:  # sell
        if (sl - ref_price) < min_distance:
            sl = round(ref_price + min_distance, 2); adjusted = True
        if (ref_price - tp) < min_distance:
            tp = round(ref_price - min_distance, 2); adjusted = True
    return sl, tp, adjusted


def _extract_ticket(resp: dict, payload: dict, prefer_position: bool) -> str | None:
    """Return the ticket id from a trade response, or log+None if absent.

    Without this the caller silently aborts the whole 3-slice batch and we never
    learn why the broker rejected the request (no HTTPError was raised, but the
    response carried no ticket — e.g. an error stringCode in a 2xx body).
    """
    if prefer_position:
        tid = resp.get("positionId") or resp.get("orderId")
    else:
        tid = resp.get("orderId") or resp.get("positionId")
    if not tid:
        log.error("[MetaAPI] trade returned no ticket id — payload=%s response=%s",
                  payload, resp)
    return tid


class MetaApiClient:
    """A single MetaAPI account. Holds its own token, trading host, spec cache."""

    def __init__(self, token: str, account: str, *, dry_run: bool = False,
                 label: str = "primary"):
        self._token = token
        self._account = account
        self.dry_run = dry_run
        self.label = label
        self._trading_url: str | None = None
        self._spec_cache: dict[str, dict] = {}

    @property
    def account(self) -> str:
        return self._account

    def _headers(self) -> dict:
        return {"auth-token": self._token, "Content-Type": "application/json"}

    def _resolve_trading_url(self) -> str:
        if self._trading_url:
            return self._trading_url
        try:
            resp = requests.get(
                f"{_PROVISION_URL}/users/current/accounts/{self._account}",
                headers=self._headers(), timeout=10,
            )
            resp.raise_for_status()
            region = resp.json().get("region", "new-york")
        except Exception:
            log.warning("[MetaAPI:%s] region lookup failed — defaulting to new-york", self.label)
            region = "new-york"
        self._trading_url = f"https://mt-client-api-v1.{region}.agiliumtrade.ai"
        log.info("[MetaAPI:%s] trading host: %s", self.label, self._trading_url)
        return self._trading_url

    def _trade(self, payload: dict) -> dict | None:
        if self.dry_run:
            log.info("[DRY:%s] MetaAPI trade payload=%s", self.label, payload)
            return {"positionId": "dry-run", "orderId": "dry-run", "stringCode": "DONE"}
        if not self._token or not self._account:
            log.error("[MetaAPI:%s] credentials not configured", self.label)
            return None
        try:
            url = f"{self._resolve_trading_url()}/users/current/accounts/{self._account}/trade"
            resp = _request("POST", url, headers=self._headers(), json=payload, timeout=_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            log.error("[MetaAPI:%s] HTTP %s: %s", self.label, e.response.status_code, e.response.text)
        except Exception:
            log.exception("[MetaAPI:%s] trade call failed", self.label)
        return None

    def get_symbol_price(self, symbol: str) -> dict | None:
        """Return {'bid': float, 'ask': float} or None on failure."""
        broker = _SYMBOL_MAP.get(symbol, symbol)
        if self.dry_run:
            return None  # caller will fall through to market-when-in-zone heuristic
        try:
            url = (f"{self._resolve_trading_url()}/users/current/accounts/{self._account}"
                   f"/symbols/{broker}/current-price")
            resp = _request("GET", url, headers=self._headers(), timeout=_TIMEOUT)
            resp.raise_for_status()
            d = resp.json()
            return {"bid": float(d["bid"]), "ask": float(d["ask"])}
        except Exception:
            log.exception("[MetaAPI:%s] price fetch failed for %s", self.label, broker)
            return None

    def _get_symbol_spec(self, broker_symbol: str) -> dict:
        """Fetch + cache the broker's spec. Returns {'stops_level_price', 'tick_size'}.

        Falls back to {_DEFAULT_MIN_STOP_DISTANCE, 0.01} on dry_run, missing creds,
        or any error so callers always get a usable minimum distance.
        """
        if broker_symbol in self._spec_cache:
            return self._spec_cache[broker_symbol]

        spec = {"stops_level_price": _DEFAULT_MIN_STOP_DISTANCE, "tick_size": 0.01}
        if self.dry_run or not self._token or not self._account:
            self._spec_cache[broker_symbol] = spec
            return spec

        try:
            url = (f"{self._resolve_trading_url()}/users/current/accounts/{self._account}"
                   f"/symbols/{broker_symbol}/specification")
            resp = _request("GET", url, headers=self._headers(), timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            tick_size = float(data.get("tickSize", 0.01))
            stops_level = int(data.get("stopsLevel", 0))
            stops_price = stops_level * tick_size if stops_level > 0 else _DEFAULT_MIN_STOP_DISTANCE
            spec = {"stops_level_price": stops_price, "tick_size": tick_size}
            log.info("[MetaAPI:%s] %s spec: tickSize=%s stopsLevel=%d -> min_distance=%.2f",
                     self.label, broker_symbol, tick_size, stops_level, stops_price)
        except Exception:
            log.exception("[MetaAPI:%s] _get_symbol_spec failed for %s — using fallback %.2f",
                          self.label, broker_symbol, _DEFAULT_MIN_STOP_DISTANCE)

        self._spec_cache[broker_symbol] = spec
        return spec

    def place_market_order_full(self, side: Side, symbol: str, volume: float,
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
        resp = self._trade(payload)
        if not resp:
            return None
        return _extract_ticket(resp, payload, prefer_position=True)

    def place_limit_order(self, side: Side, symbol: str, volume: float, entry: float,
                          sl: float, tp: float, current_price: float | None = None,
                          comment: str = "") -> tuple[str | None, str | None]:
        """Place a pending limit/stop order.

        Returns (ticket_id, retcode). retcode is the broker's stringCode when the
        order was rejected without a ticket (e.g. 'TRADE_RETCODE_INVALID_PRICE' when
        price has moved to the wrong side of the limit between our price fetch and
        the trade), letting the caller fall back to a market entry instead of
        aborting the whole signal.
        """
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
        resp = self._trade(payload)
        if not resp:
            return None, None
        tid = _extract_ticket(resp, payload, prefer_position=False)
        retcode = None if tid else (resp.get("stringCode") or resp.get("numericCode"))
        return tid, retcode

    def modify_position_sl(self, position_id: str, new_sl: float) -> bool:
        if position_id == "dry-run":
            log.info("[DRY:%s] modify_position_sl position=%s sl=%s", self.label, position_id, new_sl)
            return True
        payload = {"actionType": "POSITION_MODIFY", "positionId": position_id,
                   "stopLoss": round(new_sl, 2)}
        return self._trade(payload) is not None

    def cancel_order(self, order_id: str) -> bool:
        if order_id == "dry-run":
            log.info("[DRY:%s] cancel_order order=%s", self.label, order_id)
            return True
        payload = {"actionType": "ORDER_CANCEL", "orderId": order_id}
        return self._trade(payload) is not None

    def close_position(self, position_id: str) -> bool:
        if position_id == "dry-run":
            log.info("[DRY:%s] close_position position=%s", self.label, position_id)
            return True
        payload = {"actionType": "POSITION_CLOSE_ID", "positionId": position_id}
        return self._trade(payload) is not None

    def get_open_positions(self, symbol: str | None = None) -> list[dict] | None:
        """Return open broker positions (optionally filtered by symbol).

        Returns [] when the account genuinely holds no (matching) positions, and
        None when the broker could not be queried (missing creds or an API error).
        Callers MUST treat None as "unknown" and fail safe — never cancel an order
        on the assumption that no position exists when we simply couldn't check.
        dry_run returns [] since there is no live broker state to inspect.
        """
        if self.dry_run:
            return []
        if not self._token or not self._account:
            return None
        broker = _SYMBOL_MAP.get(symbol, symbol) if symbol else None
        try:
            url = f"{self._resolve_trading_url()}/users/current/accounts/{self._account}/positions"
            resp = _request("GET", url, headers=self._headers(), timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list):
                return None
            if broker:
                data = [p for p in data if p.get("symbol") == broker]
            return data
        except Exception:
            log.exception("[MetaAPI:%s] positions fetch failed", self.label)
            return None

    def get_pending_orders(self, symbol: str | None = None) -> list[dict] | None:
        """Return open *pending* orders (limits/stops not yet filled).

        Same contract as get_open_positions: [] when there are genuinely none, None
        when the broker could not be queried (missing creds / API error) so callers
        fail safe and never read a transient error as "the order vanished". dry_run
        returns [] — no live broker state to inspect.
        """
        if self.dry_run:
            return []
        if not self._token or not self._account:
            return None
        broker = _SYMBOL_MAP.get(symbol, symbol) if symbol else None
        try:
            url = f"{self._resolve_trading_url()}/users/current/accounts/{self._account}/orders"
            resp = _request("GET", url, headers=self._headers(), timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list):
                return None
            if broker:
                data = [o for o in data if o.get("symbol") == broker]
            return data
        except Exception:
            log.exception("[MetaAPI:%s] orders fetch failed", self.label)
            return None

    def get_position_realized_pnl(self, position_id: str) -> dict | None:
        """Broker-TRUE realized PnL + close price for a (closed) position.

        Queries the MetaAPI client-API history-deals endpoint for `position_id`
        and aggregates the realized components (profit + commission + swap) over
        all of the position's deals — the broker's actual settled figure, which
        replaces the stale last-live-snapshot approximation reconcile_broker used
        to record. Each TP slice is its own single-entry/single-exit position, so
        the full deal set for one position id is the whole realized PnL.

        Returns {"realized_pnl": float, "close_price": float | None,
                 "closed": bool} — `closed` is True once an exit (DEAL_ENTRY_OUT)
        deal exists. Returns None on dry_run, missing creds, empty/non-list
        history, or any API error, so the caller falls back to the snapshot and
        live trading is never blocked by this best-effort lookup.
        """
        if self.dry_run or not self._token or not self._account or not position_id:
            return None
        try:
            url = (f"{self._resolve_trading_url()}/users/current/accounts/{self._account}"
                   f"/history-deals/position/{position_id}")
            resp = _request("GET", url, headers=self._headers(), timeout=_TIMEOUT)
            resp.raise_for_status()
            deals = resp.json()
            if not isinstance(deals, list) or not deals:
                return None
            realized = 0.0
            for d in deals:
                for k in ("profit", "commission", "swap"):
                    v = d.get(k)
                    if v is not None:
                        realized += float(v)
            out_deals = [d for d in deals if str(d.get("entryType", "")).endswith("OUT")]
            ref = out_deals[-1] if out_deals else deals[-1]
            close_price = float(ref["price"]) if ref.get("price") is not None else None
            return {"realized_pnl": round(realized, 2), "close_price": close_price,
                    "closed": bool(out_deals)}
        except Exception:
            log.exception("[MetaAPI:%s] history-deals fetch failed for position %s",
                          self.label, position_id)
            return None

    def submit_signal_orders(self, side: Side, symbol: str, entry: float, sl: float,
                             tps: list[float], total_volume: float,
                             msg_id: int) -> list[dict]:
        """Submit one order per TP. Decide market vs limit based on current price.

        Returns list of dicts: {tp_index, tp, ticket_id, kind, volume, entry, sl}.
        """
        if not tps:
            return []
        broker = _SYMBOL_MAP.get(symbol, symbol)
        min_d = self._get_symbol_spec(broker)["stops_level_price"]
        px = self.get_symbol_price(symbol)
        cur = (px["ask"] if side == "buy" else px["bid"]) if px else None

        # Market vs limit. A pending limit's open price must sit at least the broker
        # stops-distance (min_d) on the correct side of the market, else the broker
        # rejects it with INVALID_PRICE. So when current price is already inside/past
        # the entry — or merely within min_d of it — enter at market instead.
        #   BUY_LIMIT  needs entry <= cur - min_d  -> else market
        #   SELL_LIMIT needs entry >= cur + min_d  -> else market
        use_market = False
        if cur is not None:
            if side == "buy" and entry > cur - min_d:
                use_market = True
            if side == "sell" and entry < cur + min_d:
                use_market = True

        # Reference the broker measures the stops level from: current market price
        # for a market order, the open (entry) price for a pending limit.
        ref_price = cur if (use_market and cur is not None) else entry

        vol_each = round(total_volume / len(tps), 2)
        if vol_each <= 0:
            log.warning("[%s:%s] volume per TP rounds to 0 (total=%.2f, splits=%d)",
                        msg_id, self.label, total_volume, len(tps))
            return []

        submitted: list[dict] = []
        for i, tp in enumerate(tps, start=1):
            # Widen SL/TP to the broker floor so a too-tight slice (commonly TP1)
            # isn't rejected — a rejection here aborts ALL slices for this signal.
            o_sl, o_tp, adjusted = _apply_stops_floor(side, ref_price, sl, tp, min_d)
            if adjusted:
                log.info("[%s:%s] stops widened to broker floor (%.2f): SL %.2f->%.2f TP%d %.2f->%.2f",
                         msg_id, self.label, min_d, sl, o_sl, i, tp, o_tp)
            comment = f"tg-{msg_id}-tp{i}"
            if use_market:
                tid = self.place_market_order_full(side, symbol, vol_each, o_sl, o_tp, comment)
                kind = "market"
            else:
                tid, retcode = self.place_limit_order(side, symbol, vol_each, entry, o_sl, o_tp, cur, comment)
                kind = "limit"
                # Price moved to the wrong side of the limit between our fetch and the
                # trade (the recurring INVALID_PRICE drop) — enter at market instead of
                # aborting the whole signal, as long as we're not chasing into the SL.
                if not tid and str(retcode) in ("TRADE_RETCODE_INVALID_PRICE", "10015"):
                    px2 = self.get_symbol_price(symbol)
                    cur2 = (px2["ask"] if side == "buy" else px2["bid"]) if px2 else cur
                    room_ok = cur2 is not None and (
                        (sl - cur2 >= min_d) if side == "sell" else (cur2 - sl >= min_d))
                    if room_ok:
                        o_sl, o_tp, adj2 = _apply_stops_floor(side, cur2, sl, tp, min_d)
                        log.warning("[%s:%s] TP%d limit rejected INVALID_PRICE (entry=%.2f cur=%.2f) "
                                    "— falling back to market", msg_id, self.label, i, entry, cur2)
                        tid = self.place_market_order_full(side, symbol, vol_each, o_sl, o_tp, comment)
                        kind = "market"
                        if tid:
                            use_market = True  # remaining slices go straight to market
                    else:
                        log.warning("[%s:%s] TP%d limit rejected INVALID_PRICE and market unsafe "
                                    "(cur=%s sl=%.2f) — not chasing", msg_id, self.label, i, cur2, sl)
            if not tid:
                log.error("[%s:%s] order placement failed for TP%d — aborting remaining slices",
                          msg_id, self.label, i)
                for prev in submitted:
                    if prev["kind"] == "limit":
                        self.cancel_order(prev["ticket_id"])
                    else:
                        self.close_position(prev["ticket_id"])
                return []
            submitted.append({"tp_index": i, "tp": o_tp, "ticket_id": tid, "kind": kind,
                              "volume": vol_each, "entry": entry, "sl": o_sl})
            log.info("[%s:%s] %s order placed | TP%d=%s vol=%.2f ticket=%s",
                     msg_id, self.label, kind, i, o_tp, vol_each, tid)
        return submitted


# ── Module-level default client + back-compatible single-account API ──────────
# The single-account path (and any external caller) keeps using these functions;
# they delegate to a default client built from the environment.
_default_client = MetaApiClient(_TOKEN, _ACCOUNT, dry_run=DRY_RUN, label="primary")


def get_symbol_price(symbol: str) -> dict | None:
    return _default_client.get_symbol_price(symbol)


def place_market_order_full(side: Side, symbol: str, volume: float,
                            sl: float, tp: float, comment: str = "") -> str | None:
    return _default_client.place_market_order_full(side, symbol, volume, sl, tp, comment)


def place_limit_order(side: Side, symbol: str, volume: float, entry: float,
                      sl: float, tp: float, current_price: float | None = None,
                      comment: str = "") -> tuple[str | None, str | None]:
    return _default_client.place_limit_order(side, symbol, volume, entry, sl, tp,
                                             current_price, comment)


def modify_position_sl(position_id: str, new_sl: float) -> bool:
    return _default_client.modify_position_sl(position_id, new_sl)


def cancel_order(order_id: str) -> bool:
    return _default_client.cancel_order(order_id)


def close_position(position_id: str) -> bool:
    return _default_client.close_position(position_id)


def get_open_positions(symbol: str | None = None) -> list[dict] | None:
    return _default_client.get_open_positions(symbol)


def get_pending_orders(symbol: str | None = None) -> list[dict] | None:
    return _default_client.get_pending_orders(symbol)


def submit_signal_orders(side: Side, symbol: str, entry: float, sl: float,
                         tps: list[float], total_volume: float,
                         msg_id: int) -> list[dict]:
    return _default_client.submit_signal_orders(side, symbol, entry, sl, tps,
                                                total_volume, msg_id)
