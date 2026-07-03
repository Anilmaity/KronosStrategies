"""quiet_mr gating policy: window + vol + non-trending, bias-agnostic."""
from dataclasses import dataclass, field
from datetime import datetime, timezone

from strategy_manager.policies import POLICIES


@dataclass
class _Snap:
    d1_bias: str = "ranging"          # MUST be irrelevant (bias-agnostic)
    h4_bias: str = "neutral"
    vol_regime: str = "LOW"
    trend_regime: str = "MIXED"
    session: str = "ASIA"
    market_closed: bool = False
    details: dict = field(default_factory=dict)


def _at(hour, minute=0):
    return datetime(2026, 4, 1, hour, minute, tzinfo=timezone.utc)


def test_registered():
    assert "quiet_mr" in POLICIES


def test_active_in_window_low_vol_non_trending():
    ok, reason = POLICIES["quiet_mr"](_Snap(), {}, _at(5))
    assert ok and "quiet_mr" in reason


def test_bias_agnostic():
    for bias in ("bullish", "bearish", "ranging", "neutral"):
        ok, _ = POLICIES["quiet_mr"](_Snap(d1_bias=bias), {}, _at(5))
        assert ok, f"d1_bias={bias} must not gate quiet_mr"


def test_paused_outside_window():
    ok, reason = POLICIES["quiet_mr"](_Snap(), {}, _at(12))
    assert not ok and "outside" in reason


def test_paused_when_trending():
    ok, reason = POLICIES["quiet_mr"](_Snap(trend_regime="TRENDING"), {}, _at(5))
    assert not ok and "TRENDING" in reason


def test_paused_on_high_vol():
    ok, _ = POLICIES["quiet_mr"](_Snap(vol_regime="HIGH"), {}, _at(5))
    assert not ok


def test_param_overrides():
    ok, _ = POLICIES["quiet_mr"](_Snap(vol_regime="HIGH"),
                                 {"vol_regimes": ["HIGH"]}, _at(5))
    assert ok
