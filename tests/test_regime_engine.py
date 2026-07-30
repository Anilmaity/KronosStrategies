"""
Unit tests for strategies/regime/regime_engine.py (Strategy Manager §3).

All frames are synthetic DataFrames — no network, no DB. Import style
matches test_challenge_xau.py: put strategies/ on sys.path (the live
runner's root) and import the package flat.
"""
from __future__ import annotations

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

from regime.regime_engine import (  # noqa: E402
    ER_RANGING,
    ER_TRENDING,
    FRAME_SPEC,
    RegimeSnapshot,
    _classify_vol,
    _efficiency_ratio,
    _mid_rank_percentile,
    compute_regime,
    session_for_hour,
)


# ──────────────────────────────────────────────────────────────────────────────
# Synthetic frame builders
# ──────────────────────────────────────────────────────────────────────────────

_START = pd.Timestamp("2026-06-01 00:00:00")


def _bars_from_mid(mids: list[float], rng: float = 0.4, freq: str = "1h") -> pd.DataFrame:
    """OHLC bars centred on a mid-price path with a fixed high-low range."""
    n = len(mids)
    times = pd.date_range(_START, periods=n, freq=freq)
    mids_s = pd.Series(mids, dtype=float)
    return pd.DataFrame(
        {
            "time": times,
            "open": mids_s,
            "high": mids_s + rng / 2,
            "low": mids_s - rng / 2,
            "close": mids_s,
            "volume": 100.0,
        }
    )


def _zigzag_up(cycles: int = 8, start: float = 100.0, freq: str = "1h") -> pd.DataFrame:
    """Rising zigzag: +2/bar x5 then -2/bar x2 per cycle (net +6/cycle).

    Produces clean higher-high / higher-low swing structure for
    ict_engine.get_market_structure (lookback=3).
    """
    mids, level = [start], start
    for _ in range(cycles):
        for _ in range(5):
            level += 2.0
            mids.append(level)
        for _ in range(2):
            level -= 2.0
            mids.append(level)
    return _bars_from_mid(mids, freq=freq)


def _zigzag_flat(cycles: int = 14, start: float = 100.0, freq: str = "1h") -> pd.DataFrame:
    """Flat zigzag noise: +2/bar x2 then -2/bar x2 (net 0) — equal swing
    highs/lows -> 'ranging' structure, near-zero efficiency ratio."""
    mids, level = [start], start
    for _ in range(cycles):
        for _ in range(2):
            level += 2.0
            mids.append(level)
        for _ in range(2):
            level -= 2.0
            mids.append(level)
    return _bars_from_mid(mids, freq=freq)


def _ramp(n: int, start: float = 100.0, step: float = 0.5, freq: str = "1h") -> pd.DataFrame:
    """Monotonic ramp: efficiency ratio == 1.0 (pure trend, no swings)."""
    mids = [start + i * step for i in range(n)]
    return _bars_from_mid(mids, freq=freq)


def _flat_vol_h1(n: int = 200, rng: float = 1.0, tail_rng: float | None = None,
                 tail_n: int = 20) -> pd.DataFrame:
    """H1 bars with constant close and a controlled high-low range so
    TR == range exactly; optionally switch the last `tail_n` bars to
    `tail_rng` to steer the latest ATR into a chosen percentile bucket."""
    ranges = [rng] * n
    if tail_rng is not None:
        ranges[-tail_n:] = [tail_rng] * tail_n
    times = pd.date_range(_START, periods=n, freq="1h")
    r = pd.Series(ranges, dtype=float)
    return pd.DataFrame(
        {
            "time": times,
            "open": 100.0,
            "high": 100.0 + r / 2,
            "low": 100.0 - r / 2,
            "close": 100.0,
            "volume": 100.0,
        }
    )


_NOW = datetime(2026, 6, 3, 10, 0, tzinfo=timezone.utc)  # Wednesday, LONDON


def _frames(**overrides) -> dict[str, pd.DataFrame]:
    base = {
        "1d": _zigzag_flat(freq="1D"),
        "4h": _zigzag_flat(freq="4h"),
        "1h": _flat_vol_h1(),
        "15m": _zigzag_flat(freq="15min"),
        "5m": _zigzag_flat(freq="5min"),
        "1m": _zigzag_flat(freq="1min"),
    }
    base.update(overrides)
    return base


# ──────────────────────────────────────────────────────────────────────────────
# Directional bias
# ──────────────────────────────────────────────────────────────────────────────

def test_uptrend_d1_is_bullish():
    snap = compute_regime(_frames(**{"1d": _zigzag_up(freq="1D")}), _NOW)
    assert snap.d1_bias == "bullish"


def test_flat_noise_d1_is_ranging():
    snap = compute_regime(_frames(), _NOW)
    assert snap.d1_bias == "ranging"


def test_uptrend_h4_with_ranging_h1_is_long():
    # 4H bullish structure + H1 ranging (monotonic ramp -> no swings) -> long
    frames = _frames(**{"4h": _zigzag_up(freq="4h"), "1h": _ramp(200)})
    snap = compute_regime(frames, _NOW)
    assert snap.h4_bias == "long"


def test_flat_h4_is_neutral():
    snap = compute_regime(_frames(), _NOW)
    assert snap.h4_bias == "neutral"


def test_downtrend_d1_is_bearish():
    up = _zigzag_up(freq="1D")
    down = up.copy()
    for col in ("open", "high", "low", "close"):
        down[col] = 300.0 - up[col]
    # invert flips high/low roles
    down[["high", "low"]] = down[["low", "high"]].values
    snap = compute_regime(_frames(**{"1d": down}), _NOW)
    assert snap.d1_bias == "bearish"


# ──────────────────────────────────────────────────────────────────────────────
# Volatility regime (ATR percentile)
# ──────────────────────────────────────────────────────────────────────────────

def test_vol_constant_is_normal_midrank():
    snap = compute_regime(_frames(**{"1h": _flat_vol_h1(rng=2.0)}), _NOW)
    assert snap.vol_regime == "NORMAL"
    assert snap.details["atr_pct"] == pytest.approx(50.0)
    assert snap.details["atr_h1"] == pytest.approx(2.0)


def test_vol_spike_is_extreme():
    frames = _frames(**{"1h": _flat_vol_h1(n=320, rng=1.0, tail_rng=10.0)})
    snap = compute_regime(frames, _NOW)
    assert snap.vol_regime == "EXTREME"
    assert snap.details["atr_pct"] > 95.0
    assert snap.details["atr_h1"] == pytest.approx(10.0)


def test_vol_collapse_is_low():
    frames = _frames(**{"1h": _flat_vol_h1(n=320, rng=10.0, tail_rng=1.0)})
    snap = compute_regime(frames, _NOW)
    assert snap.vol_regime == "LOW"
    assert snap.details["atr_pct"] < 25.0


@pytest.mark.parametrize(
    "pct,expected",
    [
        (0.0, "LOW"),
        (24.99, "LOW"),
        (25.0, "NORMAL"),      # boundary: <25 is LOW, 25 itself is NORMAL
        (50.0, "NORMAL"),
        (75.0, "NORMAL"),      # boundary: >75 is HIGH, 75 itself is NORMAL
        (75.01, "HIGH"),
        (95.0, "HIGH"),        # boundary: >95 is EXTREME, 95 itself is HIGH
        (95.01, "EXTREME"),
        (100.0, "EXTREME"),
        (float("nan"), "NORMAL"),
    ],
)
def test_vol_percentile_edges(pct, expected):
    assert _classify_vol(pct) == expected


def test_mid_rank_percentile_constant_distribution():
    s = pd.Series([3.0] * 10)
    assert _mid_rank_percentile(s, 3.0) == pytest.approx(50.0)


def test_short_h1_frame_degrades_to_normal():
    frames = _frames(**{"1h": _flat_vol_h1(n=5)})
    snap = compute_regime(frames, _NOW)
    assert snap.vol_regime == "NORMAL"
    assert snap.details["atr_h1"] is None


# ──────────────────────────────────────────────────────────────────────────────
# Trend regime (efficiency ratio)
# ──────────────────────────────────────────────────────────────────────────────

def test_ramp_both_frames_is_trending():
    frames = _frames(**{"1h": _ramp(60), "15m": _ramp(60, freq="15min")})
    snap = compute_regime(frames, _NOW)
    assert snap.trend_regime == "TRENDING"
    assert snap.details["er_h1"] == pytest.approx(1.0)
    assert snap.details["er_m15"] == pytest.approx(1.0)


def test_flat_noise_both_frames_is_ranging():
    # default H1 fixture has constant closes (zero path -> ER undefined),
    # so give it real flat-zigzag noise for the trend-regime check
    snap = compute_regime(_frames(**{"1h": _zigzag_flat(freq="1h")}), _NOW)
    assert snap.trend_regime == "RANGING"
    assert snap.details["er_h1"] < ER_RANGING
    assert snap.details["er_m15"] < ER_RANGING


def test_disagreeing_frames_is_mixed():
    frames = _frames(**{"1h": _ramp(60)})  # H1 trends, M15 stays flat noise
    snap = compute_regime(frames, _NOW)
    assert snap.trend_regime == "MIXED"


def test_efficiency_ratio_short_history_is_nan():
    assert pd.isna(_efficiency_ratio(pd.Series([1.0, 2.0, 3.0]), 24))


def test_er_thresholds_are_sane_constants():
    assert 0 < ER_RANGING < ER_TRENDING < 1


# ──────────────────────────────────────────────────────────────────────────────
# Session mapping & market closed
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "hour,expected",
    [(0, "ASIA"), (6, "ASIA"),
     (7, "LONDON"), (12, "LONDON"),
     (13, "OVERLAP"), (16, "OVERLAP"),
     (17, "NY"), (20, "NY"),
     (21, "ROLLOVER"), (23, "ROLLOVER")],
)
def test_session_mapping(hour, expected):
    assert session_for_hour(hour) == expected


def test_every_hour_maps_to_a_session():
    for h in range(24):
        assert session_for_hour(h) in {"ASIA", "LONDON", "NY", "OVERLAP", "ROLLOVER"}


def test_snapshot_session_from_now_utc():
    snap = compute_regime(_frames(), datetime(2026, 6, 3, 14, 0, tzinfo=timezone.utc))
    assert snap.session == "OVERLAP"


def test_market_closed_weekend():
    saturday = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
    snap = compute_regime(_frames(), saturday)
    assert snap.market_closed is True


def test_market_open_midweek():
    snap = compute_regime(_frames(), _NOW)
    assert snap.market_closed is False


def test_market_closed_daily_maintenance():
    maint = datetime(2026, 6, 3, 21, 30, tzinfo=timezone.utc)  # Wed 21:30 UTC
    snap = compute_regime(_frames(), maint)
    assert snap.market_closed is True
    assert snap.session == "ROLLOVER"


# ──────────────────────────────────────────────────────────────────────────────
# Snapshot shape / degradation
# ──────────────────────────────────────────────────────────────────────────────

def test_snapshot_fields_and_details_keys():
    snap = compute_regime(_frames(), _NOW, symbol="XAU_USD")
    assert isinstance(snap, RegimeSnapshot)
    assert snap.symbol == "XAU_USD"
    assert snap.d1_bias in {"bullish", "bearish", "ranging"}
    assert snap.h4_bias in {"long", "short", "neutral"}
    assert snap.vol_regime in {"LOW", "NORMAL", "HIGH", "EXTREME"}
    assert snap.trend_regime in {"TRENDING", "RANGING", "MIXED"}
    for key in ("atr_h1", "atr_pct", "er_h1", "er_m15",
                "m15_structure", "m5_structure", "m1_structure",
                "swings_d1", "swings_h4", "hour_utc"):
        assert key in snap.details, f"missing details key {key}"


def test_details_are_json_serialisable():
    import json
    snap = compute_regime(_frames(), _NOW)
    json.dumps(snap.details)  # must not raise (no NaN/Timestamp leakage)


def test_empty_frames_degrade_safely():
    snap = compute_regime({}, _NOW)
    assert snap.d1_bias == "ranging"
    assert snap.h4_bias == "neutral"
    assert snap.vol_regime == "NORMAL"
    assert snap.trend_regime == "MIXED"
    assert snap.session == "LONDON"


def test_frame_spec_matches_design():
    # opt15 Task 11: 5m/1m dropped (fed only display-only details.m5/m1_structure).
    assert FRAME_SPEC == {"1d": 120, "4h": 90, "1h": 30, "15m": 10}
