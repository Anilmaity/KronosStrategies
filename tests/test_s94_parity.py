# tests/test_s94_parity.py
"""S94 incremental-detection parity gate (opt15 Task 10).

The optimization makes s94._detect INCREMENTAL: a persistent level/sweep
machine is carried across runner ticks and only bars newer than the last
processed timestamp are stepped, with a full-rebuild fallback when the window
origin changes. This must emit BYTE-IDENTICAL armed pendings to the old
"rebuild the whole window from born=0 on every closed bar" behavior.

Parity method (models the live runner exactly):
  * Reference trace  -- force a full rebuild on EVERY call by nulling the
    persistent detect state before each _detect (this is precisely what the
    old code did: levels rebuilt from scratch each call, _pending carried).
  * Incremental trace -- let the persistent state carry across calls (the new
    optimization). First call is a full rebuild; every subsequent call with a
    stable window origin processes only the new bar(s).

Both traces walk the SAME growing/sliding window sequence over a synthetic
~400-bar M5 frame that contains a prior-day level, session levels, confirmed
swing fractals, a day boundary crossed mid-stream, and one sweep+confirm
sequence. The armed pending (side/level/stop/tp/armed_after/expires_at) must
match at every step. No DB, no network.
"""
from __future__ import annotations

import copy
import os
import sys
from datetime import datetime, timezone

import pandas as pd
import pytest

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies")
)
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from backtest_strategies import s94_sweep_reversal as s94  # noqa: E402

# midnight-aligned so 15m buckets are index//3 and PD levels exist from day 1;
# 288 M5 bars == 24h, so the frame crosses a UTC day boundary at bar 288.
_T0 = pd.Timestamp("2026-06-04T00:00:00Z")
_LEVEL, _EXTREME = 2010.0, 2012.0
_STOP = 2012.2                       # extreme + 0.1 * penetration (pen = 2.0)
_TP = 1996.0                         # extreme - 2 * (2012 - 2004) break-bar leg


def _frame(n_tail=90):
    """Fast-frame geometry from test_s94_sweep_reversal, extended with a flat
    tail so the window can slide/grow past the confirmation bar (313)."""
    rows = []

    def add(o, h, l, c):
        rows.append(dict(time=_T0 + pd.Timedelta(minutes=5 * len(rows)),
                         open=float(o), high=float(h), low=float(l),
                         close=float(c), volume=1.0))

    for _ in range(300):
        add(2000, 2000.5, 1999.5, 2000)          # 0..299 flat (spans a UTC day)
    add(2000, 2010, 1999.5, 2005)                 # i=300 swing high 2010
    for _ in range(10):
        add(2004, 2006, 2003, 2004)               # 301..310 (swing confirms @310)
    add(2004, 2006, 2003, 2004)                   # i=311 filler
    add(2005, 2012, 2004, 2006)                   # i=312 sweep  (break, bucket 104)
    add(2006, 2011, 2005, 2008)                   # i=313 confirm (bucket 104)
    for _ in range(n_tail):
        add(2007, 2007.5, 2006.5, 2007)           # 314.. flat tail (no re-trigger)
    return pd.DataFrame(rows)


def _now(end_idx):
    """A now_utc consistent with the window's last bar (bar end_idx)."""
    return (datetime(2026, 6, 4, tzinfo=timezone.utc)
            + pd.Timedelta(minutes=5 * end_idx))


def _pending_key(p):
    """Comparable, order-stable snapshot of one armed pending."""
    return (
        p["side"], float(p["level"]), float(p["stop"]), float(p["tp"]),
        pd.Timestamp(p["armed_after"]).value, pd.Timestamp(p["expires_at"]).value,
    )


def _snapshot():
    return [_pending_key(p) for p in s94._pending]


def _win(frame, lo, hi):
    """Window = frame bars [lo, hi] inclusive, index reset (mirrors runner tail)."""
    return frame.iloc[lo:hi + 1].reset_index(drop=True)


@pytest.fixture(autouse=True)
def _reset():
    s94.reset_state()
    yield
    s94.reset_state()


def _reference_trace(frame, windows):
    """Old behavior: force a full rebuild of the level machine on EVERY call
    (state nulled first), with _pending carried across calls -- exactly what the
    pre-opt15 code did (levels rebuilt from born=0 each tick, pendings persist).
    `windows` is a list of (lo, hi) inclusive bar ranges."""
    s94.reset_state()
    out = []
    for lo, hi in windows:
        s94._state = None                     # force full rebuild (old semantics)
        s94._detect(_win(frame, lo, hi), _now(hi))
        out.append(_snapshot())
    return out


def _incremental_trace(frame, windows):
    """New behavior: persistent state carried; incremental when the origin is
    stable, full-rebuild fallback when it slides."""
    s94.reset_state()
    out = []
    for lo, hi in windows:
        s94._detect(_win(frame, lo, hi), _now(hi))
        out.append(_snapshot())
    return out


def test_incremental_matches_full_rebuild_single_bar_advance():
    # Grow the window one bar at a time across the day boundary (288), the swing
    # birth (310), the break (312) and the confirmation (313) -- each becomes the
    # window's LAST bar exactly once, the strongest incremental check.
    frame = _frame()
    endpoints = list(range(280, 320))
    windows = [(0, e) for e in endpoints]
    ref = _reference_trace(frame, windows)
    inc = _incremental_trace(frame, windows)
    assert inc == ref
    # sanity: the confirmation actually armed the validated pending at bar 313
    at_313 = inc[endpoints.index(313)]
    assert len(at_313) == 1
    side, level, stop, tp, _armed, _exp = at_313[0]
    assert side == -1
    assert level == _LEVEL
    assert stop == pytest.approx(_STOP)
    assert tp == pytest.approx(_TP)


def test_incremental_matches_full_rebuild_multi_bar_jumps():
    # Endpoints that SKIP bars -> each incremental call steps several new bars at
    # once (harvest only the last). Must still match a full rebuild per window.
    frame = _frame()
    endpoints = [285, 300, 309, 312, 313, 316, 319]
    windows = [(0, e) for e in endpoints]
    ref = _reference_trace(frame, windows)
    inc = _incremental_trace(frame, windows)
    assert inc == ref
    assert len(inc[endpoints.index(313)]) == 1     # confirm still armed once


def test_full_rebuild_fallback_on_origin_slide():
    # A sequence that GROWS an origin-0 window through the confirm, then SLIDES
    # the origin forward twice (drop leading bars). Each origin change forces the
    # incremental machine to fall back to a full rebuild; the incremental trace
    # must still match the force-full-rebuild reference at every step -- pending
    # carry-over included (both traces carry _pending across the slide).
    frame = _frame()
    windows = (
        [(0, e) for e in range(305, 314)]          # origin 0, incremental growth
        + [(5, 314), (5, 315), (5, 316)]           # origin -> 5 (fallback, then inc)
        + [(6, 317), (6, 318)]                      # origin -> 6 (fallback, then inc)
    )
    ref = _reference_trace(frame, windows)
    inc = _incremental_trace(frame, windows)
    assert inc == ref
    # the confirm armed exactly one pending at bar 313, carried across both
    # origin slides (never re-armed, never dropped by the fallback).
    assert len(inc[windows.index((0, 313))]) == 1
    assert len(inc[-1]) == 1


def test_dedup_same_bar_does_not_rearm():
    # Re-calling _detect with the SAME window (same last bar) must not re-arm.
    frame = _frame()
    s94.reset_state()
    for e in range(305, 314):
        s94._detect(_win(frame, 0, e), _now(e))
    n_after_confirm = len(s94._pending)
    assert n_after_confirm == 1
    s94._detect(_win(frame, 0, 313), _now(313))     # identical window again
    assert len(s94._pending) == 1                    # no duplicate arm


def test_level_ttl_is_env_tunable(monkeypatch):
    # S94_LEVEL_TTL_BARS overrides the default; reload picks it up.
    import importlib
    monkeypatch.setenv("S94_LEVEL_TTL_BARS", "512")
    mod = importlib.reload(s94)
    try:
        assert mod._LEVEL_TTL == 512
    finally:
        monkeypatch.delenv("S94_LEVEL_TTL_BARS", raising=False)
        importlib.reload(s94)
