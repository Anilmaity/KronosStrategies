# tests/test_s100_er_gate.py
"""S100 ER trend-persistence gate (opt15 Task 15) -- unit tests.

Covered (brief requirement 3), all offline / no network / no DB:

  * ER PARITY: the module's ported `_efficiency_ratio` / `_classify_trend` and
    the ER window/threshold constants match the CANONICAL regime engine
    (strategies/regime/regime_engine.py, loaded by explicit path so the check is
    against THAT file, immune to the duplicated strategy_manager/regime tree and
    import-cache ordering) on shared fixtures.
  * GATE OFF = GOLDEN PARITY: with S100_ER_GATE unset/off the gate is an identity
    pass-through -- it returns the exact signal object untouched, even on a
    ranging tape that an armed gate would block, so live behaviour is unchanged.
  * GATE ON: blocks a synthetic RANGING tape and admits a TRENDING one (both via
    the real regime computation), with the ranging-vs-strict distinction on MIXED
    tape, the fail-toward-OFF short-window path, and get_signal actually routing
    a detected signal through the gate.
  * EXTEND + ASSERT: MIN_BARS_1M stays the 642 base floor with the gate off and
    rises to the ER floor (25 closed H1 bars) when armed, so the Task-7 runner
    assert refuses to start an armed service whose window is too small.

Matches the suite convention: put strategies/ on sys.path and import flat.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
import types
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies")
)
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

import backtest_strategies.s100_m3_combo as s100  # noqa: E402
import research_runner as rr  # noqa: E402  (runner MIN_BARS assert helpers)
from backtest_strategies.base import Signal  # noqa: E402


def _load_regime_ref():
    """Load the canonical regime engine the brief names (strategies/regime/
    regime_engine.py) by explicit path, under a distinct module name so it never
    collides with the `regime` package binding test_regime_efficiency.py sets up
    against the duplicated strategy_manager/regime tree. Parity must be proven
    against THIS file -- the one the s100 gate ports from."""
    path = os.path.join(_STRAT_DIR, "regime", "regime_engine.py")
    spec = importlib.util.spec_from_file_location("_s100_regime_ref", path)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: the @dataclass in regime_engine resolves its own
    # module via sys.modules[cls.__module__] during class creation.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


REG = _load_regime_ref()

# A deterministic would-be entry; identity comparisons (`is`) prove the gate
# passes the SAME object through (golden parity) rather than a rebuilt copy.
FIXED_SIG = Signal(side="BUY", entry_price=4000.0, stop_loss=3999.0,
                   take_profit=4002.5, reason="S100_TEST_LONG", max_hold_min=72)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """Every test starts with the env unset (gate OFF) and fresh module state."""
    monkeypatch.delenv("S100_ER_GATE", raising=False)
    s100.reset_state()
    yield
    s100.reset_state()


# ──────────────────────────────────────────────────────────────────────────────
# Synthetic tapes (>= the armed floor so H1x24/M15x30 ratios are computable)
# ──────────────────────────────────────────────────────────────────────────────

def _w1m(closes, start="2026-01-05 00:00", spread=0.2) -> pd.DataFrame:
    """A contiguous 1-bar-per-minute w1m frame from a close series (tz-aware UTC,
    matching the live feed)."""
    c = np.asarray(closes, float)
    n = len(c)
    t = pd.date_range(start, periods=n, freq="1min", tz="UTC")
    o = np.concatenate(([c[0]], c[:-1]))
    return pd.DataFrame({"time": t, "open": o, "high": np.maximum(o, c) + spread,
                         "low": np.minimum(o, c) - spread, "close": c})


def _trend_closes(n=1600, drift=0.6, seed=3):
    """Strong monotonic drift, tiny noise -> ER ~1.0 -> TRENDING."""
    rng = np.random.default_rng(seed)
    return 4000 + np.cumsum(drift + rng.normal(0, 0.05, n))


def _chop_closes(n=1600, amp=18.0, period=100.0, seed=5):
    """Bounded oscillation -> near-zero net over a long path -> ER ~0 -> RANGING."""
    rng = np.random.default_rng(seed)
    i = np.arange(n)
    return 4000 + amp * np.sin(2 * np.pi * i / period) + rng.normal(0, 0.1, n)


def _mixed_closes(n=1600, drift=0.15, amp=8.0, period=70.0, seed=7):
    """Weak drift on an oscillation -> neither ER band -> MIXED."""
    rng = np.random.default_rng(seed)
    i = np.arange(n)
    return 4000 + drift * i + amp * np.sin(2 * np.pi * i / period) + rng.normal(0, 0.2, n)


def _in_hours_now():
    return datetime(2026, 1, 5, 13, 30, tzinfo=timezone.utc)   # hour 13 in _HOURS


# ──────────────────────────────────────────────────────────────────────────────
# 1) ER computation parity vs the canonical regime engine
# ──────────────────────────────────────────────────────────────────────────────

_CLOSE_CASES = {
    "ramp": pd.Series(4000 + 0.5 * np.arange(60)),               # trend
    "sine": pd.Series(4000 + 15 * np.sin(np.arange(60))),        # chop
    "flat": pd.Series([4000.0] * 60),                            # zero path -> NaN
    "short": pd.Series([4000.0, 4001.0, 4002.0]),                # < n+1 -> NaN
    "noisy": pd.Series(4000 + np.cumsum(np.random.default_rng(1).normal(0, 1, 60))),
}


@pytest.mark.parametrize("name", list(_CLOSE_CASES))
@pytest.mark.parametrize("n", [24, 30])
def test_efficiency_ratio_matches_regime_engine(name, n):
    cs = _CLOSE_CASES[name]
    a = s100._efficiency_ratio(cs, n)
    b = REG._efficiency_ratio(cs, n)
    if pd.isna(a) or pd.isna(b):
        assert pd.isna(a) and pd.isna(b)
    else:
        assert a == b


def test_er_windows_and_thresholds_match_regime_engine():
    assert (s100._ER_H1_BARS, s100._ER_M15_BARS) == \
           (REG.ER_H1_BARS, REG.ER_M15_BARS) == (24, 30)
    assert (s100._ER_TRENDING, s100._ER_RANGING) == \
           (REG.ER_TRENDING, REG.ER_RANGING) == (0.35, 0.20)


@pytest.mark.parametrize("h,m", [
    (0.50, 0.50),          # both > 0.35 -> TRENDING
    (0.40, 0.36),          # both > 0.35 -> TRENDING
    (0.10, 0.10),          # both < 0.20 -> RANGING
    (0.19, 0.19),          # both < 0.20 -> RANGING
    (0.50, 0.10),          # split -> MIXED
    (0.35, 0.35),          # boundary: NOT > 0.35 -> MIXED
    (0.20, 0.20),          # boundary: NOT < 0.20 -> MIXED
    (float("nan"), 0.50),  # NaN -> MIXED
    (0.50, float("nan")),  # NaN -> MIXED
])
def test_classify_trend_matches_regime_engine(h, m):
    assert s100._classify_trend(h, m) == REG._classify_trend(h, m)


# ──────────────────────────────────────────────────────────────────────────────
# 2) Env parsing
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("val,exp", [
    (None, "off"), ("off", "off"), ("ranging", "ranging"), ("strict", "strict"),
    ("RANGING", "ranging"), ("  strict ", "strict"), ("bogus", "off"), ("", "off"),
])
def test_gate_mode_parsing(monkeypatch, val, exp):
    if val is None:
        monkeypatch.delenv("S100_ER_GATE", raising=False)
    else:
        monkeypatch.setenv("S100_ER_GATE", val)
    assert s100._er_gate_mode() == exp


# ──────────────────────────────────────────────────────────────────────────────
# 3) MIN_BARS_1M extend + runner assert wiring
# ──────────────────────────────────────────────────────────────────────────────

def test_min_bars_default_is_base_floor():
    # Gate off (default) must be byte-identical to the pre-Task-15 contract that
    # test_runner_min_bars.py already guards: MIN_BARS_1M == 3 * _MIN_M3 == 642.
    assert s100.MIN_BARS_1M == s100._MIN_BARS_1M_BASE == 3 * s100._MIN_M3 == 642


def test_er_floor_is_derived_not_a_literal():
    assert s100._ER_MIN_BARS_1M == (s100._ER_H1_BARS + 2) * 60 == 1560


def test_required_floor_extends_only_when_armed():
    assert s100._required_min_bars_1m("off") == 642
    assert s100._required_min_bars_1m("ranging") == 1560
    assert s100._required_min_bars_1m("strict") == 1560


def test_armed_floor_is_enough_to_classify():
    # The extended floor must genuinely admit a valid 24-bar H1 ratio -- a window
    # of exactly _ER_MIN_BARS_1M contiguous bars classifies (never None).
    w = _w1m(_trend_closes(n=s100._ER_MIN_BARS_1M))
    assert s100._er_trend_regime(w) is not None


def test_runner_assert_flags_armed_service_at_base_window():
    # An armed s100 module declares MIN_BARS_1M=1560; the current compose window
    # for s100 (RESEARCH_WIN_1M=700) must trip the runner's LOUD startup assert.
    mod = types.SimpleNamespace(MIN_BARS_1M=s100._ER_MIN_BARS_1M)
    v = rr._min_bars_violations(mod, win_1m=700, win_5m=999, win_15m=999)
    assert len(v) == 1
    frame, need, have = v[0]
    assert frame == "1M" and need == 1560 and have == 700
    # A widened window clears it.
    assert rr._min_bars_violations(
        mod, win_1m=1560, win_5m=999, win_15m=999) == []


# ──────────────────────────────────────────────────────────────────────────────
# 4) Regime classification on real synthetic tapes
# ──────────────────────────────────────────────────────────────────────────────

def test_regime_classification_on_synthetic_tapes():
    assert s100._er_trend_regime(_w1m(_trend_closes())) == "TRENDING"
    assert s100._er_trend_regime(_w1m(_chop_closes())) == "RANGING"
    assert s100._er_trend_regime(_w1m(_mixed_closes())) == "MIXED"


def test_regime_none_on_short_window():
    # Too short for a 24-bar H1 ratio -> None (NOT MIXED: strict must fail toward
    # OFF, not block every entry, on a short window).
    assert s100._er_trend_regime(_w1m(_trend_closes(n=300))) is None


# ──────────────────────────────────────────────────────────────────────────────
# 5) Gate OFF = golden parity (identity pass-through)
# ──────────────────────────────────────────────────────────────────────────────

def test_gate_off_is_identity_passthrough():
    # A RANGING tape that an armed gate WOULD block: off returns the same object.
    w = _w1m(_chop_closes())
    assert s100._er_trend_regime(w) == "RANGING"
    assert s100._apply_er_gate(FIXED_SIG, w) is FIXED_SIG        # env unset -> off


def test_gate_off_explicit_value_passthrough(monkeypatch):
    monkeypatch.setenv("S100_ER_GATE", "off")
    assert s100._apply_er_gate(FIXED_SIG, _w1m(_chop_closes())) is FIXED_SIG


def test_gate_off_none_stays_none():
    assert s100._apply_er_gate(None, _w1m(_trend_closes())) is None


def test_get_signal_gate_off_ignores_regime(monkeypatch):
    # get_signal must return the detected signal untouched when the gate is off,
    # even on a RANGING tape (golden parity at the get_signal boundary).
    monkeypatch.setattr(s100, "_detect", lambda m3, now: FIXED_SIG)
    out = s100.get_signal(_w1m(_chop_closes()), None, None, _in_hours_now())
    assert out is FIXED_SIG


# ──────────────────────────────────────────────────────────────────────────────
# 6) Gate ON blocks ranging, admits trending
# ──────────────────────────────────────────────────────────────────────────────

def test_gate_ranging_blocks_ranging_admits_trend_and_mixed(monkeypatch):
    monkeypatch.setenv("S100_ER_GATE", "ranging")
    assert s100._apply_er_gate(FIXED_SIG, _w1m(_chop_closes())) is None      # RANGING blocked
    assert s100._apply_er_gate(FIXED_SIG, _w1m(_trend_closes())) is FIXED_SIG  # TRENDING admitted
    assert s100._apply_er_gate(FIXED_SIG, _w1m(_mixed_closes())) is FIXED_SIG  # MIXED admitted


def test_gate_strict_blocks_ranging_and_mixed_admits_trend(monkeypatch):
    monkeypatch.setenv("S100_ER_GATE", "strict")
    assert s100._apply_er_gate(FIXED_SIG, _w1m(_chop_closes())) is None       # RANGING blocked
    assert s100._apply_er_gate(FIXED_SIG, _w1m(_mixed_closes())) is None      # MIXED blocked
    assert s100._apply_er_gate(FIXED_SIG, _w1m(_trend_closes())) is FIXED_SIG  # TRENDING admitted


def test_gate_fails_toward_off_on_short_window(monkeypatch, caplog):
    monkeypatch.setenv("S100_ER_GATE", "strict")
    w = _w1m(_trend_closes(n=300))          # too short to classify
    with caplog.at_level(logging.WARNING):
        out = s100._apply_er_gate(FIXED_SIG, w)
    assert out is FIXED_SIG                 # admitted -- data hiccup never blocks
    assert s100._er_warned is True
    assert any("failing toward OFF" in r.getMessage() for r in caplog.records)


def test_get_signal_routes_detected_signal_through_gate(monkeypatch):
    # A detected signal on a RANGING tape is BLOCKED when armed and ADMITTED on a
    # TRENDING tape -- proving get_signal applies the gate, not just _apply_er_gate.
    monkeypatch.setattr(s100, "_detect", lambda m3, now: FIXED_SIG)

    monkeypatch.setenv("S100_ER_GATE", "strict")
    assert s100.get_signal(_w1m(_chop_closes()), None, None, _in_hours_now()) is None
    s100.reset_state()
    assert s100.get_signal(_w1m(_trend_closes()), None, None, _in_hours_now()) is FIXED_SIG
