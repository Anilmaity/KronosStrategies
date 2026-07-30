"""
opt15 Task 11 - regime loop efficiency (report point #15).

The Strategy Manager runs a 60s loop that fetched 6 OANDA frames
(~4,600 candles) and recomputed the whole regime every tick, even though
D1 structure moves ~1 bar/day. This suite guards the three efficiency wins:

  * FRAME_SPEC no longer fetches 5m/1m - they fed ONLY the display-only
    details.m5_structure/m1_structure, which no live code consumes. The keys
    stay present (empty string) for one release so external readers degrade
    gracefully instead of KeyError-ing.
  * D1 and H4 swing points are computed at most ONCE per tick each (they were
    computed twice - once for bias, once for details.swings_*). The fused
    _structure_and_swings helper is proven byte-for-byte equivalent to the
    get_market_structure + get_liquidity_targets pair it replaces.
  * manager.compute_regime_cached memoises on the last-closed-bar timestamps of
    (D1, H4, H1, M15) plus UTC hour: a cache hit skips compute_regime and only
    re-derives session/market_closed from the clock; an advanced H1 bar forces
    a recompute; and a hit presents exactly the vol/trend/bias a fresh
    recompute would (which is what makes memoising safe for the policies that
    read those fields).

Offline: synthetic DataFrames only - no network, no DB.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pandas as pd
import pytest

# Resolve imports against the strategy_manager tree (the live manager's root):
# manager.py plus its regime/shared/strategy/policies copies. Keeping a single
# tree on sys.path avoids the duplicated-tree ("regime" in both strategies/ and
# strategy_manager/) import collision.
_SM_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategy_manager")
)
if _SM_DIR not in sys.path:
    sys.path.insert(0, _SM_DIR)

import regime.regime_engine as regime_engine  # noqa: E402
from regime.regime_engine import FRAME_SPEC, compute_regime  # noqa: E402
from strategy.ict_engine import (  # noqa: E402
    get_liquidity_targets,
    get_market_structure,
)


# ──────────────────────────────────────────────────────────────────────────────
# Synthetic frame builders
# ──────────────────────────────────────────────────────────────────────────────

_START = pd.Timestamp("2026-06-01 00:00:00")
_NOW = datetime(2026, 6, 3, 10, 0, tzinfo=timezone.utc)  # Wednesday, LONDON


def _bars(mids: list[float], rng: float = 0.4, freq: str = "1h") -> pd.DataFrame:
    n = len(mids)
    times = pd.date_range(_START, periods=n, freq=freq)
    s = pd.Series(mids, dtype=float)
    return pd.DataFrame(
        {"time": times, "open": s, "high": s + rng / 2,
         "low": s - rng / 2, "close": s, "volume": 100.0}
    )


def _ramp(n: int, start: float = 100.0, step: float = 0.5, freq: str = "1h") -> pd.DataFrame:
    return _bars([start + i * step for i in range(n)], freq=freq)


def _zigzag_up(cycles: int = 8, start: float = 100.0, freq: str = "1h") -> pd.DataFrame:
    mids, lvl = [start], start
    for _ in range(cycles):
        for _ in range(5):
            lvl += 2.0
            mids.append(lvl)
        for _ in range(2):
            lvl -= 2.0
            mids.append(lvl)
    return _bars(mids, freq=freq)


def _zigzag_down(cycles: int = 8, start: float = 300.0, freq: str = "1h") -> pd.DataFrame:
    mids, lvl = [start], start
    for _ in range(cycles):
        for _ in range(5):
            lvl -= 2.0
            mids.append(lvl)
        for _ in range(2):
            lvl += 2.0
            mids.append(lvl)
    return _bars(mids, freq=freq)


def _zigzag_flat(cycles: int = 14, start: float = 100.0, freq: str = "1h") -> pd.DataFrame:
    mids, lvl = [start], start
    for _ in range(cycles):
        for _ in range(2):
            lvl += 2.0
            mids.append(lvl)
        for _ in range(2):
            lvl -= 2.0
            mids.append(lvl)
    return _bars(mids, freq=freq)


def _flat_vol_h1(n: int = 60, rng: float = 1.0) -> pd.DataFrame:
    times = pd.date_range(_START, periods=n, freq="1h")
    return pd.DataFrame(
        {"time": times, "open": 100.0, "high": 100.0 + rng / 2,
         "low": 100.0 - rng / 2, "close": 100.0, "volume": 100.0}
    )


def _make_frames() -> dict[str, pd.DataFrame]:
    """A realistic 4-frame regime input. D1 and H4 have DISTINCT lengths so the
    swing-call counter can prove each is computed exactly once."""
    return {
        "1d":  _zigzag_up(cycles=8, freq="1D"),      # 57 bars, bullish
        "4h":  _zigzag_flat(cycles=10, freq="4h"),   # 41 bars, ranging
        "1h":  _flat_vol_h1(n=60),                   # 60 bars
        "15m": _zigzag_flat(cycles=12, freq="15min"),  # 49 bars
    }


# ──────────────────────────────────────────────────────────────────────────────
# 1. FRAME_SPEC drops 5m/1m; details keys stay present but empty
# ──────────────────────────────────────────────────────────────────────────────

def test_frame_spec_drops_5m_and_1m():
    assert "5m" not in FRAME_SPEC
    assert "1m" not in FRAME_SPEC
    assert set(FRAME_SPEC) == {"1d", "4h", "1h", "15m"}


def test_details_m5_m1_keys_present_but_empty():
    snap = compute_regime(_make_frames(), _NOW)
    # Present for one release (graceful degradation), but no longer computed.
    assert snap.details["m5_structure"] == ""
    assert snap.details["m1_structure"] == ""
    # M15 is still classified for real (it feeds er_m15 / stays fetched).
    assert snap.details["m15_structure"] in {"bullish", "bearish", "ranging"}


def test_stray_5m_1m_frames_are_not_classified():
    """Even if a caller (e.g. the sim) still passes 5m/1m frames, compute_regime
    must not classify them - the keys stay empty."""
    frames = _make_frames()
    frames["5m"] = _zigzag_up(freq="5min")
    frames["1m"] = _zigzag_up(freq="1min")
    snap = compute_regime(frames, _NOW)
    assert snap.details["m5_structure"] == ""
    assert snap.details["m1_structure"] == ""


def test_details_still_json_serialisable():
    import json
    snap = compute_regime(_make_frames(), _NOW)
    json.dumps(snap.details)  # no NaN / Timestamp leakage


# ──────────────────────────────────────────────────────────────────────────────
# 2. Swing points computed once per frame; fused helper == ict_engine helpers
# ──────────────────────────────────────────────────────────────────────────────

def test_swing_points_computed_once_per_frame(monkeypatch):
    """D1 and H4 each drive exactly one _swing_points call (bias + swings are
    fused). H1 structure still goes through ict_engine.get_market_structure
    (its own _swing_points), so it is intentionally not counted here."""
    frames = _make_frames()
    seen_lengths: list[int] = []
    real = regime_engine._swing_points

    def spy(df, lookback=3):
        seen_lengths.append(len(df))
        return real(df, lookback)

    monkeypatch.setattr(regime_engine, "_swing_points", spy)
    compute_regime(frames, _NOW)

    # Exactly two computations, in D1-then-H4 order, one per distinct frame.
    assert seen_lengths == [len(frames["1d"]), len(frames["4h"])]


@pytest.mark.parametrize(
    "builder",
    [
        lambda: _zigzag_up(cycles=8),      # bullish
        lambda: _zigzag_down(cycles=8),    # bearish
        lambda: _zigzag_flat(cycles=14),   # ranging (equal swings)
        lambda: _ramp(50),                 # ranging (no swings at all)
        lambda: _ramp(8),                  # short frame (< length guard)
    ],
)
def test_structure_and_swings_matches_ict_helpers(builder):
    """The fused helper must be identical to calling get_market_structure and
    get_liquidity_targets separately - the whole point is one swing pass, not a
    behaviour change."""
    df = builder()
    struct, swings = regime_engine._structure_and_swings(df)
    assert struct == get_market_structure(df)
    assert swings == get_liquidity_targets(df)


def test_structure_and_swings_empty_frame():
    struct, swings = regime_engine._structure_and_swings(pd.DataFrame())
    assert struct == "ranging"
    assert swings == {"nearest_high": None, "nearest_low": None}


def test_swings_details_still_populated():
    """details.swings_d1/h4 must still carry the same content the old
    get_liquidity_targets path produced."""
    frames = _make_frames()
    snap = compute_regime(frames, _NOW)
    assert snap.details["swings_d1"] == get_liquidity_targets(frames["1d"])
    assert snap.details["swings_h4"] == get_liquidity_targets(frames["4h"])


# ──────────────────────────────────────────────────────────────────────────────
# 3. Manager-side memoisation of compute_regime
# ──────────────────────────────────────────────────────────────────────────────

import manager  # noqa: E402  (import after sys.path is set up)


def _count_compute(monkeypatch) -> dict:
    """Wrap manager.compute_regime with a call counter."""
    calls = {"n": 0}
    real = manager.compute_regime

    def counting(frames, now_utc, symbol="XAU_USD"):
        calls["n"] += 1
        return real(frames, now_utc, symbol=symbol)

    monkeypatch.setattr(manager, "compute_regime", counting)
    return calls


def test_memo_hit_skips_recompute(monkeypatch):
    calls = _count_compute(monkeypatch)
    cache: dict = {}
    frames = _make_frames()

    now1 = datetime(2026, 6, 3, 10, 0, tzinfo=timezone.utc)
    manager.compute_regime_cached(frames, now1, cache=cache)
    assert calls["n"] == 1

    # Same frames, same UTC hour, later minute -> cache HIT, no recompute.
    now2 = datetime(2026, 6, 3, 10, 45, tzinfo=timezone.utc)
    manager.compute_regime_cached(frames, now2, cache=cache)
    assert calls["n"] == 1


def test_memo_recomputes_when_h1_bar_advances(monkeypatch):
    calls = _count_compute(monkeypatch)
    cache: dict = {}
    frames = _make_frames()

    now = datetime(2026, 6, 3, 10, 0, tzinfo=timezone.utc)
    manager.compute_regime_cached(frames, now, cache=cache)
    assert calls["n"] == 1

    # Append one new closed H1 bar -> last-closed-bar timestamp advances ->
    # key changes -> recompute. UTC hour held constant to isolate the trigger.
    h1 = frames["1h"]
    new_row = h1.iloc[[-1]].copy()
    new_row["time"] = h1["time"].iloc[-1] + pd.Timedelta(hours=1)
    frames2 = dict(frames)
    frames2["1h"] = pd.concat([h1, new_row], ignore_index=True)

    now_same_hour = datetime(2026, 6, 3, 10, 50, tzinfo=timezone.utc)
    manager.compute_regime_cached(frames2, now_same_hour, cache=cache)
    assert calls["n"] == 2


def test_memo_hit_matches_fresh_recompute():
    """A cache hit must present exactly what compute_regime would return for the
    same frames+clock. vol/trend/bias are pure functions of the OHLC frames
    (whose last-closed-bar timestamps are in the key), and session/market_closed
    are re-derived from now_utc on the hit path - so a hit is behaviourally
    identical to a recompute. This is what makes memoising safe for the policies
    that read snap.vol_regime / trend_regime / *_bias."""
    frames = _make_frames()
    now = datetime(2026, 6, 3, 10, 0, tzinfo=timezone.utc)
    fresh = manager.compute_regime(frames, now, symbol="XAU_USD")

    cache: dict = {}
    manager.compute_regime_cached(frames, now, cache=cache)  # populate
    later = datetime(2026, 6, 3, 10, 59, tzinfo=timezone.utc)  # same key (hour 10)
    hit = manager.compute_regime_cached(frames, later, cache=cache)

    assert hit.vol_regime == fresh.vol_regime
    assert hit.trend_regime == fresh.trend_regime
    assert hit.d1_bias == fresh.d1_bias
    assert hit.h4_bias == fresh.h4_bias
    # Clock-derived fields re-derived from the hit's now_utc.
    assert hit.session == manager.session_for_hour(later.hour)
    assert hit.market_closed == manager.is_market_closed_utc(later)


def test_memo_refreshes_market_closed_across_weekend_boundary(monkeypatch):
    """Sanity: the hit path uses is_market_closed_utc(now), not the cached bool.
    A frozen tape over the weekend keeps the same last-bar timestamps, so within
    one hour the market_closed flag still reflects the live clock."""
    calls = _count_compute(monkeypatch)
    cache: dict = {}
    frames = _make_frames()

    # Saturday 12:00 (closed) then 12:30 (still closed) -> same key -> hit, and
    # market_closed stays True from the live clock, not a stale cached value.
    sat_a = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
    sat_b = datetime(2026, 6, 6, 12, 30, tzinfo=timezone.utc)
    snap_a = manager.compute_regime_cached(frames, sat_a, cache=cache)
    snap_b = manager.compute_regime_cached(frames, sat_b, cache=cache)
    assert calls["n"] == 1               # second call was a memo hit
    assert snap_a.market_closed is True
    assert snap_b.market_closed is True
