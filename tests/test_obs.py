# tests/test_obs.py
"""opt15 task12 - in-process metrics + Telegram-optional alerting (shared/obs.py).

Covers the dependency-free metrics core (count / observe / timer / flush_line /
flush_if_due) and the alert() helper (log-only by default, Telegram POST when
env is set, all POST errors swallowed). Also smoke-tests the entry_manager and
position_monitor probe wiring via direct calls.

Offline: no network. The Telegram POST is exercised by monkeypatching obs.requests.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies")
)
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

import pytest  # noqa: E402
from shared import obs  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_metrics():
    obs.reset()
    yield
    obs.reset()


# ---------------------------------------------------------------------------
# counters / observations aggregate
# ---------------------------------------------------------------------------
def test_count_aggregates():
    obs.count("a")
    obs.count("a")
    obs.count("b", 3)
    snap = obs.snapshot()
    assert snap["counters"]["a"] == 2
    assert snap["counters"]["b"] == 3


def test_observe_aggregates_count_sum_min_max_avg():
    for v in (2.0, 4.0, 6.0):
        obs.observe("d", v)
    rec = obs.snapshot()["observations"]["d"]
    assert rec["n"] == 3
    assert rec["sum"] == pytest.approx(12.0)
    assert rec["min"] == pytest.approx(2.0)
    assert rec["max"] == pytest.approx(6.0)
    assert rec["avg"] == pytest.approx(4.0)


def test_timer_records_observation():
    with obs.timer("t_sec"):
        pass
    rec = obs.snapshot()["observations"]["t_sec"]
    assert rec["n"] == 1
    assert rec["min"] >= 0.0


# ---------------------------------------------------------------------------
# flush_line -> valid ASCII METRICS json
# ---------------------------------------------------------------------------
def test_flush_line_is_valid_ascii_json():
    obs.count("hits", 5)
    obs.observe("lat", 1.5)
    line = obs.flush_line()
    assert line.startswith("METRICS ")
    assert line.isascii()
    body = json.loads(line[len("METRICS "):])
    assert body["counters"]["hits"] == 5
    assert body["observations"]["lat"]["n"] == 1


def test_flush_line_does_not_reset():
    obs.count("x")
    obs.flush_line()
    assert obs.snapshot()["counters"]["x"] == 1  # pure read, no drain


# ---------------------------------------------------------------------------
# flush_if_due: throttled emit + window reset
# ---------------------------------------------------------------------------
def test_flush_if_due_not_due_returns_false():
    obs.count("x")
    assert obs.flush_if_due(now=time.time()) is False
    assert obs.snapshot()["counters"]["x"] == 1  # not drained


def test_flush_if_due_when_due_logs_and_resets(caplog):
    obs.count("x", 7)
    due = time.time() + obs.OBS_FLUSH_SEC + 1
    with caplog.at_level(logging.INFO, logger="shared.obs"):
        flushed = obs.flush_if_due(now=due)
    assert flushed is True
    assert any("METRICS" in r.message for r in caplog.records)
    # window drained after a periodic flush
    assert obs.snapshot()["counters"] == {}


# ---------------------------------------------------------------------------
# alert(): log-only by default, Telegram POST when env set, errors swallowed
# ---------------------------------------------------------------------------
def test_alert_without_env_only_logs(monkeypatch, caplog):
    monkeypatch.delenv("ALERT_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ALERT_TELEGRAM_CHAT_ID", raising=False)

    class _Boom:
        def post(self, *a, **k):
            raise AssertionError("must not POST without env")

    monkeypatch.setattr(obs, "requests", _Boom())
    with caplog.at_level(logging.WARNING, logger="shared.obs"):
        obs.alert("disk almost full", level="WARN")
    assert any("ALERT" in r.message and "disk almost full" in r.message
               for r in caplog.records)


def test_alert_with_env_posts(monkeypatch):
    monkeypatch.setenv("ALERT_TELEGRAM_BOT_TOKEN", "TOK123")
    monkeypatch.setenv("ALERT_TELEGRAM_CHAT_ID", "CHAT9")
    captured = {}

    class _Fake:
        def post(self, url, json=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            captured["timeout"] = timeout

    monkeypatch.setattr(obs, "requests", _Fake())
    obs.alert("kill switch tripped", level="ERROR")
    assert "TOK123" in captured["url"]
    assert captured["json"]["chat_id"] == "CHAT9"
    assert "kill switch tripped" in captured["json"]["text"]
    assert captured["timeout"] == 2


def test_alert_swallows_post_exception(monkeypatch):
    monkeypatch.setenv("ALERT_TELEGRAM_BOT_TOKEN", "TOK")
    monkeypatch.setenv("ALERT_TELEGRAM_CHAT_ID", "CHAT")

    class _Fake:
        def post(self, *a, **k):
            raise RuntimeError("telegram down")

    monkeypatch.setattr(obs, "requests", _Fake())
    # must not raise
    obs.alert("something", level="ERROR")


# ---------------------------------------------------------------------------
# probe wiring smoke tests (direct calls)
# ---------------------------------------------------------------------------
def test_entry_manager_reject_counter_wired(monkeypatch):
    """_update_signal_status(status='REJECTED') increments a reject_<reason>
    counter keyed on the reason prefix. Uses a fake Session so no DB is needed."""
    from strategy import entry_manager as em

    class _NoRow:
        def query(self, *a, **k):
            return self

        def filter_by(self, *a, **k):
            return self

        def first(self):
            return None

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(em, "Session", lambda: _NoRow())
    obs.reset()
    em._update_signal_status(__import__("uuid").uuid4(), "REJECTED",
                             rejection_reason="sl_too_tight: 1.2pt < 1.5pt")
    assert obs.snapshot()["counters"].get("reject_sl_too_tight") == 1


def test_position_monitor_imports_obs():
    """position_monitor wires obs without cross-tree import gymnastics."""
    _pm = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "position_manager"))
    if _pm not in sys.path:
        sys.path.insert(0, _pm)
    import position_monitor as pm
    assert hasattr(pm, "obs")
