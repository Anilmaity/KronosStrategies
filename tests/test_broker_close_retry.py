# tests/test_broker_close_retry.py
"""Broker-close retry semantics for active exits (TIME_EXIT/TRAIL/CUSTOM).

Root cause (2026-07-23 audit of the Jul-14 "-0.52 TIME_EXIT" trades): the
monitor closed via the env-singleton MetaAPI client, whose region fallback
(new-york) did not match the account region -> HTTP 504 on every close.
The monitor then flattened the DB anyway, leaving the REAL broker position
running to its attached stop (-52pt each, trued later by fill_reconciler).

Fix under test:
  1. position_monitor resolves the per-broker client (client_for_broker,
     same working path the entry manager uses) for active closes.
  2. On unconfirmed close the position is NOT finalized: the trigger stays
     PENDING and the close is retried with backoff; only after
     _CLOSE_MAX_ATTEMPTS failures does it fall back to flatten-and-warn
     (broker SL/TP backstop remains attached).
"""
from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "position_manager")
))

import position_monitor as pm  # noqa: E402
from shared.metaapi_client import MetaApiClient  # noqa: E402


class _FakeEntryOrder:
    broker_order_id = "12345"


class _FakeQuery:
    def __init__(self, entry_order):
        self._entry = entry_order

    def filter_by(self, **kw):
        return self

    def first(self):
        return self._entry


class _FakeSession:
    def __init__(self, entry_order=_FakeEntryOrder()):
        self._entry = entry_order

    def query(self, model):
        return _FakeQuery(self._entry)


class _FakePosition:
    def __init__(self):
        self.id = uuid.uuid4()


class _StubClient:
    def __init__(self, ok):
        self.ok = ok
        self.calls = []

    def close_position_by_id(self, pid):
        self.calls.append(pid)
        return self.ok


def _reset_state():
    pm._close_attempts.clear()
    pm._close_next_try.clear()


def test_success_finalizes_via_per_broker_client(monkeypatch):
    _reset_state()
    stub = _StubClient(ok=True)
    seen = {}

    def fake_client_for_broker(sess, user_broker_id):
        seen["ub"] = user_broker_id
        return stub

    monkeypatch.setattr(pm, "client_for_broker", fake_client_for_broker)
    monkeypatch.setattr(pm, "_get_user_broker_id", lambda s, p: "ub-1")

    pos = _FakePosition()
    finalize, attempted = pm._attempt_broker_close(
        _FakeSession(), pos, "TIME_EXIT", now_ts=1000.0)
    assert finalize is True and attempted is True
    assert stub.calls == ["12345"]
    assert seen["ub"] == "ub-1"
    assert pos.id not in pm._close_attempts


def test_failure_keeps_position_open_and_backs_off(monkeypatch):
    _reset_state()
    stub = _StubClient(ok=False)
    monkeypatch.setattr(pm, "client_for_broker", lambda s, ub: stub)
    monkeypatch.setattr(pm, "_get_user_broker_id", lambda s, p: "ub-1")

    pos = _FakePosition()
    finalize, attempted = pm._attempt_broker_close(
        _FakeSession(), pos, "TIME_EXIT", now_ts=1000.0)
    assert finalize is False and attempted is True
    assert pm._close_attempts[pos.id] == 1

    # Within the backoff window: no new broker call, still not finalized.
    finalize, attempted = pm._attempt_broker_close(
        _FakeSession(), pos, "TIME_EXIT", now_ts=1000.0 + 1.0)
    assert finalize is False and attempted is False
    assert len(stub.calls) == 1

    # After the window: retried.
    finalize, attempted = pm._attempt_broker_close(
        _FakeSession(), pos, "TIME_EXIT",
        now_ts=1000.0 + pm._CLOSE_RETRY_SEC + 0.1)
    assert finalize is False and attempted is True
    assert len(stub.calls) == 2
    assert pm._close_attempts[pos.id] == 2


def test_gives_up_after_max_attempts(monkeypatch):
    _reset_state()
    stub = _StubClient(ok=False)
    monkeypatch.setattr(pm, "client_for_broker", lambda s, ub: stub)
    monkeypatch.setattr(pm, "_get_user_broker_id", lambda s, p: "ub-1")

    pos = _FakePosition()
    now = 1000.0
    for i in range(pm._CLOSE_MAX_ATTEMPTS - 1):
        finalize, attempted = pm._attempt_broker_close(
            _FakeSession(), pos, "TIME_EXIT", now_ts=now)
        assert finalize is False and attempted is True
        now += pm._CLOSE_RETRY_SEC + 0.1

    # Final attempt: give up -> finalize with the broker backstop warning.
    finalize, attempted = pm._attempt_broker_close(
        _FakeSession(), pos, "TIME_EXIT", now_ts=now)
    assert finalize is True and attempted is True
    assert pos.id not in pm._close_attempts        # state cleaned up


def test_no_broker_pid_finalizes_immediately(monkeypatch):
    _reset_state()
    monkeypatch.setattr(pm, "client_for_broker",
                        lambda s, ub: _StubClient(ok=False))
    monkeypatch.setattr(pm, "_get_user_broker_id", lambda s, p: "ub-1")

    pos = _FakePosition()
    finalize, attempted = pm._attempt_broker_close(
        _FakeSession(entry_order=None), pos, "TIME_EXIT", now_ts=1000.0)
    assert finalize is True and attempted is True


def test_falls_back_to_env_client_without_broker_creds(monkeypatch):
    _reset_state()
    calls = []
    monkeypatch.setattr(pm, "client_for_broker", lambda s, ub: None)
    monkeypatch.setattr(pm, "_get_user_broker_id", lambda s, p: "ub-1")
    monkeypatch.setattr(pm, "close_position_by_id",
                        lambda pid: calls.append(pid) or True)

    pos = _FakePosition()
    finalize, attempted = pm._attempt_broker_close(
        _FakeSession(), pos, "TIME_EXIT", now_ts=1000.0)
    assert finalize is True and attempted is True
    assert calls == ["12345"]


def test_metaapi_client_close_uses_per_account_url(monkeypatch):
    captured = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _Resp()

    import shared.metaapi_client as mc
    monkeypatch.setattr(mc.requests, "post", fake_post)
    monkeypatch.setattr(mc, "_DRY_RUN", False)

    client = MetaApiClient("acct-42", "tok-42")
    client._trading_url_cache = "https://mt-client-api-v1.london.agiliumtrade.ai"
    assert client.close_position_by_id("999") is True
    assert captured["url"] == (
        "https://mt-client-api-v1.london.agiliumtrade.ai"
        "/users/current/accounts/acct-42/trade"
    )
    assert captured["json"] == {"actionType": "POSITION_CLOSE_ID",
                                "positionId": "999"}
    assert captured["headers"]["auth-token"] == "tok-42"
