"""
test_research_runner_heartbeat.py
---------------------------------
The generic research_runner loop polls every few seconds and `continue`s silently
on every quiet reason (no new bar, out of session, cooldown, no signal), so from
the outside a healthy idle runner is indistinguishable from a dead one. It emits a
time-throttled heartbeat instead. These tests pin the pure throttle decision.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from strategies.research_runner import _should_heartbeat

_T0 = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def test_heartbeat_fires_on_first_call_when_never_logged():
    assert _should_heartbeat(_T0, None, 900) is True


def test_heartbeat_suppressed_within_the_interval():
    last = _T0
    assert _should_heartbeat(_T0 + timedelta(seconds=300), last, 900) is False


def test_heartbeat_fires_once_the_interval_has_elapsed():
    last = _T0
    assert _should_heartbeat(_T0 + timedelta(seconds=900), last, 900) is True
    assert _should_heartbeat(_T0 + timedelta(seconds=1200), last, 900) is True
