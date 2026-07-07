# tests/test_entry_fill_price.py
"""Immediate broker-fill booking at entry (2026-07-07): get_position_fill
fetches the real openPrice right after the market order; dry-run and
timeouts fall back gracefully. No network — requests is monkeypatched."""
from __future__ import annotations

import os
import sys

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies")
)
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from shared import metaapi_client as mc  # noqa: E402


def _client():
    c = mc.MetaApiClient(account_id="acc-1", token="tok")
    c._trading_url_cache = "https://mt-client-api-v1.test.local"
    return c


class _Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


def test_dry_run_sentinel_short_circuits(monkeypatch):
    calls = []
    monkeypatch.setattr(mc.requests, "get",
                        lambda *a, **k: calls.append(1) or _Resp(200))
    assert _client().get_position_fill("dry-run") is None
    assert not calls, "sentinel must not hit the network"


def test_returns_open_price(monkeypatch):
    monkeypatch.setattr(mc.requests, "get",
                        lambda *a, **k: _Resp(200, {"openPrice": 4136.9}))
    assert _client().get_position_fill("23532569") == 4136.9


def test_retries_404_then_succeeds(monkeypatch):
    seq = [_Resp(404), _Resp(404), _Resp(200, {"openPrice": 4101.25})]
    monkeypatch.setattr(mc.requests, "get", lambda *a, **k: seq.pop(0))
    px = _client().get_position_fill("42", retries=3, delay=0.0)
    assert px == 4101.25


def test_gives_up_after_retries(monkeypatch):
    monkeypatch.setattr(mc.requests, "get", lambda *a, **k: _Resp(404))
    assert _client().get_position_fill("42", retries=2, delay=0.0) is None
