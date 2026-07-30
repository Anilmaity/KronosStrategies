# tests/test_runner_min_bars.py
"""Runner MIN_BARS startup assert + compose hardening guard (opt15 Task 7).

Root cause guarded here (the CHALLENGE_XAU defect class, 2026-07-16): a runner
window smaller than the strategy's validated lookback makes get_signal() return
None on every tick -- a SILENT no-trade. CHALLENGE_XAU ran DAYS_15M=15 (~67
closed H4) below its 72-H4 minimum and never fired a live order. This suite
proves the runner now fails LOUD at startup (sys.exit(2)) instead, and that the
compose stack carries the 2GB-box memory budget + data-archive profiling.

Companion to tests/test_compose_window_coverage.py, which already guards the
weekend bar-arithmetic; this file guards the runtime assert and the compose
structural hardening (mem_limit / healthcheck / profiles) -- no duplication.
"""
from __future__ import annotations

import importlib
import os
import sys
import types

import pytest
import yaml

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies")
)
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

import research_runner as rr  # noqa: E402

COMPOSE = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "compose.yml")
)

# Live roster (2026-07-30): compose service name -> backtest_strategies module.
_LIVE = {
    "s93_fvg_scalp": "s93_fvg_scalp",
    "s94_sweep_reversal": "s94_sweep_reversal",
    "s99_mss_fvg": "s99_mss_fvg",
    "s100_m3_combo": "s100_m3_combo",
}

_TICK_COLLECTORS = (
    "tick_data_collector",
    "tick_data_collector_xagusd",
    "tick_data_collector_btcusd",
)
_RESEARCH_RUNNER_SERVICES = tuple(_LIVE)


def _load_compose() -> dict:
    with open(COMPOSE, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _svc_env(svc: dict) -> dict:
    env = svc.get("environment") or {}
    if isinstance(env, list):  # KEY=VAL list form
        env = dict(e.split("=", 1) for e in env)
    return env


# ---------------------------------------------------------------------------
# 1) The declared MIN_BARS_* constants derive from each module's own _MIN_*.
# ---------------------------------------------------------------------------

def test_live_modules_declare_min_bars_from_internal_constants():
    s93 = importlib.import_module("backtest_strategies.s93_fvg_scalp")
    s94 = importlib.import_module("backtest_strategies.s94_sweep_reversal")
    s99 = importlib.import_module("backtest_strategies.s99_mss_fvg")
    s100 = importlib.import_module("backtest_strategies.s100_m3_combo")

    # Values must equal the module's own internal requirement, never a second
    # hardcoded literal that could silently drift from the get_signal guard.
    assert s93.MIN_BARS_5M == s93._MIN_M5 == 18
    assert s94.MIN_BARS_5M == s94._MIN_M5 == 298
    assert s99.MIN_BARS_5M == s99._MIN_M5 == 58
    # s100 guards on w1m length: len(w1m) >= 3 * _MIN_M3 (its get_signal check).
    assert s100.MIN_BARS_1M == 3 * s100._MIN_M3 == 642


# ---------------------------------------------------------------------------
# 2) The runner assert: undersized -> exit(2); adequate -> passes.
# ---------------------------------------------------------------------------

def test_violations_flags_undersized_window():
    mod = types.SimpleNamespace(MIN_BARS_5M=100)
    v = rr._min_bars_violations(mod, win_1m=999, win_5m=80, win_15m=999)
    assert len(v) == 1
    frame, need, have = v[0]
    assert frame == "5M" and need == 100 and have == 80


def test_violations_empty_when_adequate():
    mod = types.SimpleNamespace(MIN_BARS_5M=100, MIN_BARS_1M=600)
    assert rr._min_bars_violations(mod, win_1m=700, win_5m=160, win_15m=100) == []


def test_violations_ignores_absent_constants():
    # A module without any MIN_BARS_* declared must never trip the assert.
    mod = types.SimpleNamespace()
    assert rr._min_bars_violations(mod, win_1m=1, win_5m=1, win_15m=1) == []


def test_violations_ignores_non_int_constants():
    mod = types.SimpleNamespace(MIN_BARS_5M="lots")
    assert rr._min_bars_violations(mod, win_1m=1, win_5m=1, win_15m=1) == []


def test_assert_exits_2_on_undersized(monkeypatch):
    monkeypatch.setenv("RESEARCH_WIN_5M", "10")
    mod = types.SimpleNamespace(MIN_BARS_5M=100)
    with pytest.raises(SystemExit) as ei:
        rr._assert_min_bars(mod)
    assert ei.value.code == 2


def test_assert_passes_on_adequate(monkeypatch):
    monkeypatch.setenv("RESEARCH_WIN_5M", "200")
    mod = types.SimpleNamespace(MIN_BARS_5M=100)
    rr._assert_min_bars(mod)  # must not raise


def test_assert_explicit_params_override_env(monkeypatch):
    monkeypatch.delenv("RESEARCH_WIN_5M", raising=False)
    mod = types.SimpleNamespace(MIN_BARS_5M=100)
    with pytest.raises(SystemExit):
        rr._assert_min_bars(mod, win_5m=10)


# ---------------------------------------------------------------------------
# 3) Real compose values pass the assert for all four live services.
# ---------------------------------------------------------------------------

def test_live_services_pass_assert_with_real_compose_env():
    doc = _load_compose()
    for svc_name, module_name in _LIVE.items():
        env = _svc_env(doc["services"][svc_name])
        mod = importlib.import_module(f"backtest_strategies.{module_name}")
        win_1m = int(env.get("RESEARCH_WIN_1M", "60"))
        win_5m = int(env.get("RESEARCH_WIN_5M", "80"))
        win_15m = int(env.get("RESEARCH_WIN_15M", "100"))
        v = rr._min_bars_violations(mod, win_1m, win_5m, win_15m)
        assert v == [], f"{svc_name} would silently no-trade: {v}"


# ---------------------------------------------------------------------------
# 4) Compose structural hardening: mem_limit / profiles / healthcheck.
# ---------------------------------------------------------------------------

def test_compose_yaml_loads():
    doc = _load_compose()
    assert isinstance(doc.get("services"), dict) and doc["services"]


def test_every_service_has_mem_limit():
    doc = _load_compose()
    missing = [n for n, svc in doc["services"].items() if "mem_limit" not in svc]
    assert not missing, f"services missing mem_limit: {missing}"


def test_tick_collectors_behind_data_archive_profile():
    doc = _load_compose()
    for name in _TICK_COLLECTORS:
        profiles = doc["services"][name].get("profiles") or []
        assert "data-archive" in profiles, f"{name} not behind data-archive profile"


def test_research_runner_services_have_heartbeat_healthcheck():
    doc = _load_compose()
    for name in _RESEARCH_RUNNER_SERVICES:
        hc = doc["services"][name].get("healthcheck")
        assert hc, f"{name} missing healthcheck"
        assert "/tmp/hb" in " ".join(hc["test"]), f"{name} healthcheck not heartbeat-based"
