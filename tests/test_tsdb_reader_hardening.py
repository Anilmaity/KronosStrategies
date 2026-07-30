# tests/test_tsdb_reader_hardening.py
"""opt15(task1): tsdb_reader hardening.

Covers the three additions to the OANDA market-data layer:
  * bar-boundary cache invalidation (M1 refetch across a UTC minute edge),
  * a mounted urllib3 Retry adapter on the module requests.Session,
  * fetch_latest_bidask / fetch_latest_spread (price="BA").

No network: the module _session.get is monkeypatched and the cache clock
(time.time) is driven by the test.
"""
from __future__ import annotations

import os
import sys

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies")
)
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

import pytest  # noqa: E402
from shared import tsdb_reader as tr  # noqa: E402


class _Resp:
    def __init__(self, body, status=200):
        self._body = body
        self.status_code = status
        self.text = ""

    def json(self):
        return self._body


def _mid_candles(n=1):
    out = []
    for i in range(n):
        out.append({
            "time": "2026-07-30T00:%02d:00.000000000Z" % i,
            "complete": True,
            "volume": 10,
            "mid": {"o": "2400.0", "h": "2401.0", "l": "2399.0", "c": "2400.5"},
        })
    return {"candles": out}


def _ba_candles():
    return {"candles": [{
        "time": "2026-07-30T00:00:00.000000000Z",
        "complete": True,
        "volume": 10,
        "bid": {"o": "2399.8", "h": "2400.6", "l": "2399.4", "c": "2400.10"},
        "ask": {"o": "2400.1", "h": "2400.9", "l": "2399.7", "c": "2400.40"},
    }]}


@pytest.fixture(autouse=True)
def _clean_cache():
    with tr._cache_lock:
        tr._candle_cache.clear()
    yield
    with tr._cache_lock:
        tr._candle_cache.clear()


def _install_clock(monkeypatch, start):
    holder = {"t": float(start)}
    monkeypatch.setattr(tr.time, "time", lambda: holder["t"])
    return holder


def _install_get(monkeypatch, body):
    calls = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        calls["n"] += 1
        return _Resp(body)

    monkeypatch.setattr(tr._session, "get", fake_get)
    return calls


# -- cache freshness ---------------------------------------------------------
def test_cache_serves_within_ttl_same_minute(monkeypatch):
    clock = _install_clock(monkeypatch, 100.0)   # int(100/60) == 1
    calls = _install_get(monkeypatch, _mid_candles())
    df1 = tr.fetch_candles("1m", days=1)
    clock["t"] = 105.0                            # same minute, within TTL
    df2 = tr.fetch_candles("1m", days=1)
    assert calls["n"] == 1                        # served from cache
    assert len(df1) == len(df2) == 1


def test_m1_cache_invalidated_across_minute_boundary(monkeypatch):
    clock = _install_clock(monkeypatch, 119.0)   # int(119/60) == 1
    calls = _install_get(monkeypatch, _mid_candles())
    tr.fetch_candles("1m", days=1)
    clock["t"] = 121.0                            # int==2: boundary crossed, still < 20s TTL
    tr.fetch_candles("1m", days=1)
    assert calls["n"] == 2                        # refetched at the new bar


def test_non_m1_frame_keeps_plain_ttl_across_minute_boundary(monkeypatch):
    clock = _install_clock(monkeypatch, 119.0)
    calls = _install_get(monkeypatch, _mid_candles())
    tr.fetch_candles("5m", days=1)
    clock["t"] = 121.0                            # boundary crossed but M5 ignores it
    tr.fetch_candles("5m", days=1)
    assert calls["n"] == 1                        # still cached (plain TTL)


def test_ttl_expiry_refetches(monkeypatch):
    clock = _install_clock(monkeypatch, 100.0)
    calls = _install_get(monkeypatch, _mid_candles())
    tr.fetch_candles("5m", days=1)
    clock["t"] = 100.0 + tr._CANDLE_TTL + 1       # past TTL
    tr.fetch_candles("5m", days=1)
    assert calls["n"] == 2


# -- retry adapter -----------------------------------------------------------
def test_retry_adapter_mounted():
    adapter = tr._session.get_adapter("https://api-fxpractice.oanda.com/v3")
    retry = adapter.max_retries
    assert retry.total == 3
    assert retry.backoff_factor == 0.5
    assert set(retry.status_forcelist) == {429, 500, 502, 503, 504}
    assert set(retry.allowed_methods) == {"GET"}


# -- bid/ask -----------------------------------------------------------------
def test_fetch_latest_bidask_parses_ba(monkeypatch):
    _install_get(monkeypatch, _ba_candles())
    ba = tr.fetch_latest_bidask("XAU_USD")
    assert ba == (2400.10, 2400.40)


def test_fetch_latest_spread_is_ask_minus_bid(monkeypatch):
    _install_get(monkeypatch, _ba_candles())
    spread = tr.fetch_latest_spread("XAU_USD")
    assert spread == pytest.approx(0.30, abs=1e-9)


def test_bidask_error_returns_none(monkeypatch):
    def boom(url, params=None, timeout=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(tr._session, "get", boom)
    assert tr.fetch_latest_bidask("XAU_USD") is None
    assert tr.fetch_latest_spread("XAU_USD") is None


def test_bidask_empty_candles_returns_none(monkeypatch):
    _install_get(monkeypatch, {"candles": []})
    assert tr.fetch_latest_bidask("XAU_USD") is None
    assert tr.fetch_latest_spread("XAU_USD") is None
