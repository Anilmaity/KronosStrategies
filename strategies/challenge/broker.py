"""
broker.py
---------
A lean MetaAPI REST client for the standalone challenge bot. Exposes exactly what
an H4 single-position trend-follower needs:

  - account_equity()       — live equity (for risk sizing + the drawdown guard)
  - open_position()        — the bot's current XAU position, or None
  - place_market(...)      — market order with an initial hard SL (no TP)
  - modify_sl(...)         — move the stop (chandelier trail)
  - close_position(...)    — flatten
  - position_realized_pnl()— broker-true realized PnL of a closed position

Kept separate from Telegram_Bot/metaapi_orders.py (which is multi-TP / copy-trade
specific) so the live neymar bot is untouched and this service depends only on
`strategies/`. Mirrors the proven region-resolve + transient-retry behaviour.

`dry_run=True` places no real orders — every mutating call is logged and returns a
synthetic ticket, so the loop can be exercised end-to-end without a broker.
"""
from __future__ import annotations

import logging
import time
from typing import Literal

import requests

log = logging.getLogger("challenge-broker")

_PROVISION_URL = "https://mt-provisioning-api-v1.agiliumtrade.ai"
_TIMEOUT = 15
_RETRY_STATUSES = {429, 502, 503, 504}
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF = 1.5

Side = Literal["buy", "sell"]
_SYMBOL_MAP = {"XAUUSD": "XAUUSD", "XAU_USD": "XAUUSD"}


def _request(method: str, url: str, **kwargs) -> requests.Response:
    """HTTP with bounded backoff on transient MetaAPI failures (429/5xx, timeouts)."""
    last_exc: Exception | None = None
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            resp = requests.request(method, url, **kwargs)
            if resp.status_code in _RETRY_STATUSES and attempt < _RETRY_ATTEMPTS:
                time.sleep(_RETRY_BACKOFF * (2 ** (attempt - 1)))
                continue
            return resp
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exc = e
            if attempt < _RETRY_ATTEMPTS:
                time.sleep(_RETRY_BACKOFF * (2 ** (attempt - 1)))
                continue
            raise
    raise last_exc if last_exc else RuntimeError("retry loop exhausted")


class ChallengeBroker:
    def __init__(self, token: str, account: str, *, dry_run: bool = False,
                 label: str = "challenge", region: str | None = None):
        self._token = token
        self._account = account
        self.dry_run = dry_run
        self.label = label
        self._region = (region or "").strip() or None   # explicit override; skips lookup
        self._trading_url: str | None = None

    def _headers(self) -> dict:
        return {"auth-token": self._token, "Content-Type": "application/json"}

    def _resolve_trading_url(self) -> str:
        if self._trading_url:
            return self._trading_url
        # An explicit region (CHALLENGE_META_REGION) wins — the provisioning lookup
        # 403s for some accounts/tokens, and guessing the wrong region's trading host
        # also 403s. Pin it to the account's real region (e.g. the integrated bot uses
        # london) to avoid both. Only auto-resolve when no override is given.
        if self._region:
            region = self._region
            log.info("[%s] using configured region: %s", self.label, region)
        else:
            region = "new-york"
            try:
                resp = requests.get(f"{_PROVISION_URL}/users/current/accounts/{self._account}",
                                    headers=self._headers(), timeout=10)
                resp.raise_for_status()
                region = resp.json().get("region", "new-york")
            except Exception:
                log.warning("[%s] region lookup failed — defaulting to new-york "
                            "(set CHALLENGE_META_REGION to pin it)", self.label)
        self._trading_url = f"https://mt-client-api-v1.{region}.agiliumtrade.ai"
        log.info("[%s] trading host: %s", self.label, self._trading_url)
        return self._trading_url

    def _base(self) -> str:
        return f"{self._resolve_trading_url()}/users/current/accounts/{self._account}"

    def _trade(self, payload: dict) -> dict | None:
        if self.dry_run:
            log.info("[DRY:%s] trade payload=%s", self.label, payload)
            return {"positionId": "dry-run", "orderId": "dry-run", "stringCode": "DONE"}
        if not self._token or not self._account:
            log.error("[%s] credentials not configured", self.label)
            return None
        try:
            resp = _request("POST", f"{self._base()}/trade", headers=self._headers(),
                            json=payload, timeout=_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            log.error("[%s] trade HTTP %s: %s", self.label, e.response.status_code, e.response.text)
        except Exception:
            log.exception("[%s] trade call failed", self.label)
        return None

    # ── reads ─────────────────────────────────────────────────────────────────
    def account_equity(self) -> float | None:
        """Live account equity, or None if it could not be read."""
        if not self._token or not self._account:
            return None
        try:
            resp = _request("GET", f"{self._base()}/account-information",
                            headers=self._headers(), timeout=_TIMEOUT)
            resp.raise_for_status()
            d = resp.json()
            return float(d["equity"])
        except Exception:
            log.exception("[%s] account-information fetch failed", self.label)
            return None

    def open_position(self, symbol: str = "XAU_USD") -> dict | None:
        """The current open position for `symbol`, or None when flat / unreadable.

        Returns the broker position dict (has 'id', 'type', 'openPrice', 'volume',
        'profit', ...). None is ambiguous (flat OR error) — the caller treats None
        as 'do not assume flat' and never opens a second position blindly.
        """
        if self.dry_run or not self._token or not self._account:
            return None
        broker = _SYMBOL_MAP.get(symbol, symbol)
        try:
            resp = _request("GET", f"{self._base()}/positions", headers=self._headers(),
                            timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list):
                return None
            for p in data:
                if p.get("symbol") == broker:
                    return p
            return None
        except Exception:
            log.exception("[%s] positions fetch failed", self.label)
            return None

    def position_realized_pnl(self, position_id: str) -> float | None:
        """Broker-true realized PnL (profit+commission+swap) for a closed position."""
        if self.dry_run or not self._token or not self._account or not position_id:
            return None
        try:
            resp = _request("GET", f"{self._base()}/history-deals/position/{position_id}",
                            headers=self._headers(), timeout=_TIMEOUT)
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
            return round(realized, 2)
        except Exception:
            log.exception("[%s] history-deals fetch failed for %s", self.label, position_id)
            return None

    # ── writes ────────────────────────────────────────────────────────────────
    def place_market(self, side: Side, symbol: str, volume: float, sl: float,
                     comment: str = "") -> str | None:
        """Market order with an initial hard SL and no TP (trend exit is the trail)."""
        broker = _SYMBOL_MAP.get(symbol, symbol)
        payload = {
            "actionType": "ORDER_TYPE_BUY" if side == "buy" else "ORDER_TYPE_SELL",
            "symbol": broker,
            "volume": round(volume, 2),
            "stopLoss": round(sl, 2),
            "comment": comment[:31],
        }
        resp = self._trade(payload)
        if not resp:
            return None
        tid = resp.get("positionId") or resp.get("orderId")
        if not tid:
            log.error("[%s] market order returned no ticket — resp=%s", self.label, resp)
        return tid

    def modify_sl(self, position_id: str, new_sl: float) -> bool:
        if position_id == "dry-run":
            log.info("[DRY:%s] modify_sl position=%s sl=%s", self.label, position_id, new_sl)
            return True
        payload = {"actionType": "POSITION_MODIFY", "positionId": position_id,
                   "stopLoss": round(new_sl, 2)}
        return self._trade(payload) is not None

    def close_position(self, position_id: str) -> bool:
        if position_id == "dry-run":
            log.info("[DRY:%s] close_position position=%s", self.label, position_id)
            return True
        payload = {"actionType": "POSITION_CLOSE_ID", "positionId": position_id}
        return self._trade(payload) is not None
