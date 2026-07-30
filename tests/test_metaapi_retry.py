# tests/test_metaapi_retry.py
"""opt15 Task 2 - metaapi_client safe retries with verify-before-retry.

The single-attempt order path drops a whole signal on a transient 504, and the
July fidelity audit showed dropped signals carry the rare big winners. These
tests pin the safe-retry protocol:

  * place_market_order tags the FIRST attempt with a unique clientId.
  * On timeout/5xx it NEVER blind-resends: it first looks the clientId up via
    the positions/orders endpoint. Only a positive "absent" confirmation
    permits a re-POST; a failed lookup returns the error exactly like today.
  * 4xx is never retried.
  * close_position_by_id retries on timeout/5xx and treats an already-closed
    position (404) as idempotent success.
  * META_ORDER_MAX_RETRIES=0 restores the exact single-attempt behavior.

Mocks: monkeypatch requests.post / requests.get on the module (keeps the real
requests exception classes) and stub time.sleep so backoff does not slow tests.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import Mock

import pytest

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from strategies.shared import metaapi_client as mc  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class FakeResp:
    """Minimal stand-in for a requests.Response."""

    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = {} if json_data is None else json_data
        self.text = text

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise mc.requests.HTTPError(str(self.status_code), response=self)


def _client(monkeypatch):
    """A MetaApiClient with region resolution short-circuited and DRY_RUN off."""
    monkeypatch.setattr(mc, "_DRY_RUN", False)
    monkeypatch.setattr(mc.time, "sleep", lambda *a, **k: None)
    c = mc.MetaApiClient("acct-1", "tok-1")
    c._trading_url_cache = "https://mt-client-api-v1.test.agiliumtrade.ai"
    return c


def _patch_http(monkeypatch, post, get=None):
    post_mock = Mock(side_effect=post)
    monkeypatch.setattr(mc.requests, "post", post_mock)
    get_mock = Mock(side_effect=(get if get is not None else (lambda *a, **k: FakeResp(200, []))))
    monkeypatch.setattr(mc.requests, "get", get_mock)
    return post_mock, get_mock


# ---------------------------------------------------------------------------
# place_market_order
# ---------------------------------------------------------------------------

def test_success_first_try_no_lookup(monkeypatch):
    monkeypatch.setenv("META_ORDER_MAX_RETRIES", "2")
    c = _client(monkeypatch)

    def post(url, headers=None, json=None, timeout=None):
        return FakeResp(200, {"positionId": "POS-1", "stringCode": "TRADE_RETCODE_DONE"})

    post_mock, get_mock = _patch_http(monkeypatch, post)

    result = c.place_market_order(
        side="BUY", symbol="XAU_USD", volume=0.05,
        stop_loss=4490.0, take_profit=4520.0, entry_price=None)

    assert result == "POS-1"
    assert post_mock.call_count == 1
    assert get_mock.call_count == 0            # success -> no verification lookup


def test_first_attempt_carries_client_id(monkeypatch):
    monkeypatch.setenv("META_ORDER_MAX_RETRIES", "2")
    c = _client(monkeypatch)
    seen = {}

    def post(url, headers=None, json=None, timeout=None):
        seen["payload"] = json
        return FakeResp(200, {"positionId": "POS-1", "stringCode": "DONE"})

    _patch_http(monkeypatch, post)
    c.place_market_order(side="BUY", symbol="XAU_USD", volume=0.05,
                         stop_loss=4490.0, take_profit=4520.0)
    assert "clientId" in seen["payload"]
    assert seen["payload"]["clientId"].startswith("kr-")


def test_timeout_then_lookup_finds_order_no_repost(monkeypatch):
    monkeypatch.setenv("META_ORDER_MAX_RETRIES", "2")
    c = _client(monkeypatch)
    state = {"cid": None}

    def post(url, headers=None, json=None, timeout=None):
        state["cid"] = json.get("clientId")
        raise mc.requests.Timeout("boom")

    def get(url, headers=None, timeout=None):
        # positions endpoint echoes the just-sent clientId -> order DID land
        return FakeResp(200, [{"clientId": state["cid"], "id": "POS-9"}])

    post_mock, get_mock = _patch_http(monkeypatch, post, get)

    result = c.place_market_order(side="SELL", symbol="XAU_USD", volume=0.05,
                                  stop_loss=4520.0, take_profit=4490.0)

    assert result == "POS-9"                   # treated as success from the lookup
    assert post_mock.call_count == 1           # NO re-POST after a confirmed landing
    assert get_mock.call_count == 1            # positions lookup found it, short-circuit


def test_timeout_then_lookup_absent_reposts(monkeypatch):
    monkeypatch.setenv("META_ORDER_MAX_RETRIES", "2")
    c = _client(monkeypatch)
    state = {"n": 0}

    def post(url, headers=None, json=None, timeout=None):
        state["n"] += 1
        if state["n"] == 1:
            raise mc.requests.Timeout("boom")
        return FakeResp(200, {"positionId": "POS-2", "stringCode": "DONE"})

    def get(url, headers=None, timeout=None):
        return FakeResp(200, [])               # nothing on positions or orders -> absent

    post_mock, get_mock = _patch_http(monkeypatch, post, get)

    result = c.place_market_order(side="BUY", symbol="XAU_USD", volume=0.05,
                                  stop_loss=4490.0, take_profit=4520.0)

    assert result == "POS-2"
    assert post_mock.call_count == 2           # confirmed-absent -> exactly one re-POST
    assert get_mock.call_count == 2            # positions + orders both checked


def test_lookup_failure_does_not_retry(monkeypatch):
    monkeypatch.setenv("META_ORDER_MAX_RETRIES", "2")
    c = _client(monkeypatch)

    def post(url, headers=None, json=None, timeout=None):
        raise mc.requests.Timeout("boom")

    def get(url, headers=None, timeout=None):
        raise mc.requests.Timeout("lookup down")   # cannot confirm absence

    post_mock, get_mock = _patch_http(monkeypatch, post, get)

    result = c.place_market_order(side="BUY", symbol="XAU_USD", volume=0.05,
                                  stop_loss=4490.0, take_profit=4520.0)

    assert result is None                       # error returned as today
    assert post_mock.call_count == 1            # never blind-retried
    assert get_mock.call_count == 1


def test_lookup_5xx_does_not_retry(monkeypatch):
    monkeypatch.setenv("META_ORDER_MAX_RETRIES", "2")
    c = _client(monkeypatch)

    def post(url, headers=None, json=None, timeout=None):
        raise mc.requests.Timeout("boom")

    def get(url, headers=None, timeout=None):
        return FakeResp(503, [])                # server error -> lookup inconclusive

    post_mock, get_mock = _patch_http(monkeypatch, post, get)

    result = c.place_market_order(side="BUY", symbol="XAU_USD", volume=0.05,
                                  stop_loss=4490.0, take_profit=4520.0)

    assert result is None
    assert post_mock.call_count == 1


def test_server_5xx_triggers_verify_before_retry(monkeypatch):
    monkeypatch.setenv("META_ORDER_MAX_RETRIES", "2")
    c = _client(monkeypatch)
    state = {"n": 0}

    def post(url, headers=None, json=None, timeout=None):
        state["n"] += 1
        if state["n"] == 1:
            return FakeResp(504, text="gateway timeout")   # 5xx, not a raised exception
        return FakeResp(200, {"positionId": "POS-3", "stringCode": "DONE"})

    def get(url, headers=None, timeout=None):
        return FakeResp(200, [])               # absent -> allow re-POST

    post_mock, get_mock = _patch_http(monkeypatch, post, get)

    result = c.place_market_order(side="BUY", symbol="XAU_USD", volume=0.05,
                                  stop_loss=4490.0, take_profit=4520.0)

    assert result == "POS-3"
    assert post_mock.call_count == 2
    assert get_mock.call_count == 2


def test_4xx_never_retried(monkeypatch):
    monkeypatch.setenv("META_ORDER_MAX_RETRIES", "2")
    c = _client(monkeypatch)

    def post(url, headers=None, json=None, timeout=None):
        return FakeResp(400, text="bad request")

    post_mock, get_mock = _patch_http(monkeypatch, post)

    result = c.place_market_order(side="BUY", symbol="XAU_USD", volume=0.05,
                                  stop_loss=4490.0, take_profit=4520.0)

    assert result is None
    assert post_mock.call_count == 1            # 4xx -> no retry
    assert get_mock.call_count == 0             # 4xx -> no verification lookup


def test_retries_zero_single_attempt_no_client_id(monkeypatch):
    monkeypatch.setenv("META_ORDER_MAX_RETRIES", "0")
    c = _client(monkeypatch)
    seen = {}

    def post(url, headers=None, json=None, timeout=None):
        seen["payload"] = json
        raise mc.requests.Timeout("boom")

    post_mock, get_mock = _patch_http(monkeypatch, post)

    result = c.place_market_order(side="BUY", symbol="XAU_USD", volume=0.05,
                                  stop_loss=4490.0, take_profit=4520.0)

    assert result is None
    assert post_mock.call_count == 1            # exact old behavior: one attempt
    assert get_mock.call_count == 0             # no verification lookup
    assert "clientId" not in seen["payload"]    # retries disabled -> original payload


def test_max_retries_exhausted_returns_none(monkeypatch):
    monkeypatch.setenv("META_ORDER_MAX_RETRIES", "2")
    c = _client(monkeypatch)

    def post(url, headers=None, json=None, timeout=None):
        raise mc.requests.Timeout("boom")

    def get(url, headers=None, timeout=None):
        return FakeResp(200, [])               # always absent -> keep retrying

    post_mock, get_mock = _patch_http(monkeypatch, post, get)

    result = c.place_market_order(side="BUY", symbol="XAU_USD", volume=0.05,
                                  stop_loss=4490.0, take_profit=4520.0)

    assert result is None
    assert post_mock.call_count == 3            # 1 initial + 2 retries, then give up


# ---------------------------------------------------------------------------
# close_position_by_id
# ---------------------------------------------------------------------------

def test_close_success_first_try(monkeypatch):
    monkeypatch.setenv("META_ORDER_MAX_RETRIES", "2")
    c = _client(monkeypatch)

    def post(url, headers=None, json=None, timeout=None):
        return FakeResp(200)

    post_mock, _ = _patch_http(monkeypatch, post)
    assert c.close_position_by_id("999") is True
    assert post_mock.call_count == 1


def test_close_already_closed_404_is_success(monkeypatch):
    monkeypatch.setenv("META_ORDER_MAX_RETRIES", "2")
    c = _client(monkeypatch)

    def post(url, headers=None, json=None, timeout=None):
        return FakeResp(404, text="position not found")

    post_mock, _ = _patch_http(monkeypatch, post)
    # Idempotent: a repeated close of an already-flat position is a success,
    # matching how the monitor's _CLOSE_MAX_ATTEMPTS loop reasons.
    assert c.close_position_by_id("999") is True
    assert post_mock.call_count == 1            # 404 is terminal, not retried


def test_close_5xx_then_success_retries(monkeypatch):
    monkeypatch.setenv("META_ORDER_MAX_RETRIES", "2")
    c = _client(monkeypatch)
    state = {"n": 0}

    def post(url, headers=None, json=None, timeout=None):
        state["n"] += 1
        if state["n"] == 1:
            return FakeResp(502, text="bad gateway")
        return FakeResp(200)

    post_mock, _ = _patch_http(monkeypatch, post)
    assert c.close_position_by_id("999") is True
    assert post_mock.call_count == 2


def test_close_timeout_exhausts_returns_false(monkeypatch):
    monkeypatch.setenv("META_ORDER_MAX_RETRIES", "2")
    c = _client(monkeypatch)

    def post(url, headers=None, json=None, timeout=None):
        raise mc.requests.Timeout("boom")

    post_mock, _ = _patch_http(monkeypatch, post)
    assert c.close_position_by_id("999") is False
    assert post_mock.call_count == 3            # 1 + 2 retries, all failed


def test_close_other_4xx_not_retried(monkeypatch):
    monkeypatch.setenv("META_ORDER_MAX_RETRIES", "2")
    c = _client(monkeypatch)

    def post(url, headers=None, json=None, timeout=None):
        return FakeResp(400, text="bad request")

    post_mock, _ = _patch_http(monkeypatch, post)
    assert c.close_position_by_id("999") is False
    assert post_mock.call_count == 1            # non-404 4xx -> no retry


def test_close_retries_zero_single_attempt(monkeypatch):
    monkeypatch.setenv("META_ORDER_MAX_RETRIES", "0")
    c = _client(monkeypatch)

    def post(url, headers=None, json=None, timeout=None):
        raise mc.requests.Timeout("boom")

    post_mock, _ = _patch_http(monkeypatch, post)
    assert c.close_position_by_id("999") is False
    assert post_mock.call_count == 1


# ---------------------------------------------------------------------------
# module-level close_position_by_id (env-singleton fallback path)
# ---------------------------------------------------------------------------

def test_module_close_retries_on_5xx(monkeypatch):
    monkeypatch.setenv("META_ORDER_MAX_RETRIES", "2")
    monkeypatch.setattr(mc, "_DRY_RUN", False)
    monkeypatch.setattr(mc, "_ACCOUNT", "acct-env")
    monkeypatch.setattr(mc, "_TOKEN", "tok-env")
    monkeypatch.setattr(mc, "_TRADING_URL", "https://mt-client-api-v1.test.agiliumtrade.ai")
    monkeypatch.setattr(mc.time, "sleep", lambda *a, **k: None)
    state = {"n": 0}

    def post(url, headers=None, json=None, timeout=None):
        state["n"] += 1
        if state["n"] == 1:
            return FakeResp(503, text="unavailable")
        return FakeResp(200)

    post_mock, _ = _patch_http(monkeypatch, post)
    assert mc.close_position_by_id("777") is True
    assert post_mock.call_count == 2


# ---------------------------------------------------------------------------
# retry helper arithmetic (documents the 2s -> 5s schedule)
# ---------------------------------------------------------------------------

def test_backoff_schedule_default_base():
    # base 2 -> 2s then 5s, per the plan.
    assert mc._retry_backoff_sec(0, 2.0) == pytest.approx(2.0)
    assert mc._retry_backoff_sec(1, 2.0) == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# position_manager/shared copy (the synced duplicated tree) exposes the same
# retry protocol on its module-level place_market_order / close_position_by_id.
# ---------------------------------------------------------------------------

@pytest.fixture()
def mcp():
    # Load the position_manager/shared copy under a UNIQUE module name via
    # importlib so this test never populates the ambiguous top-level `shared`
    # package (which otherwise shadows strategies/shared for later test files).
    import importlib.util
    path = os.path.join(REPO_ROOT, "position_manager", "shared", "metaapi_client.py")
    spec = importlib.util.spec_from_file_location("pm_metaapi_client_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _patch_http_on(module, monkeypatch, post, get=None):
    post_mock = Mock(side_effect=post)
    monkeypatch.setattr(module.requests, "post", post_mock)
    get_mock = Mock(side_effect=(get if get is not None else (lambda *a, **k: FakeResp(200, []))))
    monkeypatch.setattr(module.requests, "get", get_mock)
    return post_mock, get_mock


def test_pm_copy_place_timeout_absent_reposts(mcp, monkeypatch):
    monkeypatch.setenv("META_ORDER_MAX_RETRIES", "2")
    monkeypatch.setattr(mcp, "_DRY_RUN", False)
    monkeypatch.setattr(mcp, "_ACCOUNT", "acct-env")
    monkeypatch.setattr(mcp, "_TOKEN", "tok-env")
    monkeypatch.setattr(mcp, "_TRADING_URL", "https://mt-client-api-v1.test.agiliumtrade.ai")
    monkeypatch.setattr(mcp.time, "sleep", lambda *a, **k: None)
    state = {"n": 0}

    def post(url, headers=None, json=None, timeout=None):
        state["n"] += 1
        if state["n"] == 1:
            raise mcp.requests.Timeout("boom")
        return FakeResp(200, {"positionId": "POS-PM", "stringCode": "DONE"})

    post_mock, get_mock = _patch_http_on(mcp, monkeypatch, post,
                                         lambda *a, **k: FakeResp(200, []))
    result = mcp.place_market_order(side="BUY", symbol="XAU_USD", volume=0.05,
                                    stop_loss=4490.0, take_profit=4520.0)
    assert result == "POS-PM"
    assert post_mock.call_count == 2
    assert get_mock.call_count == 2


def test_pm_copy_place_lookup_failure_no_retry(mcp, monkeypatch):
    monkeypatch.setenv("META_ORDER_MAX_RETRIES", "2")
    monkeypatch.setattr(mcp, "_DRY_RUN", False)
    monkeypatch.setattr(mcp, "_ACCOUNT", "acct-env")
    monkeypatch.setattr(mcp, "_TOKEN", "tok-env")
    monkeypatch.setattr(mcp, "_TRADING_URL", "https://mt-client-api-v1.test.agiliumtrade.ai")
    monkeypatch.setattr(mcp.time, "sleep", lambda *a, **k: None)

    def post(url, headers=None, json=None, timeout=None):
        raise mcp.requests.Timeout("boom")

    def get(url, headers=None, timeout=None):
        raise mcp.requests.Timeout("lookup down")

    post_mock, _ = _patch_http_on(mcp, monkeypatch, post, get)
    result = mcp.place_market_order(side="BUY", symbol="XAU_USD", volume=0.05,
                                    stop_loss=4490.0, take_profit=4520.0)
    assert result is None
    assert post_mock.call_count == 1


def test_pm_copy_close_404_idempotent_success(mcp, monkeypatch):
    monkeypatch.setenv("META_ORDER_MAX_RETRIES", "2")
    monkeypatch.setattr(mcp, "_DRY_RUN", False)
    monkeypatch.setattr(mcp, "_ACCOUNT", "acct-env")
    monkeypatch.setattr(mcp, "_TOKEN", "tok-env")
    monkeypatch.setattr(mcp, "_TRADING_URL", "https://mt-client-api-v1.test.agiliumtrade.ai")
    monkeypatch.setattr(mcp.time, "sleep", lambda *a, **k: None)

    def post(url, headers=None, json=None, timeout=None):
        return FakeResp(404, text="position not found")

    post_mock, _ = _patch_http_on(mcp, monkeypatch, post)
    assert mcp.close_position_by_id("777") is True
    assert post_mock.call_count == 1
