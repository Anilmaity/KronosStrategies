"""S94 env knobs (2026-09-18): S94_SD_MULT and S94_SIDES.

Both are read at import (like S94_LEVEL_TTL_BARS) and ship at their historical defaults,
so an unset environment is byte-identical to the pre-flag module (the golden trace and the
full-window control replays are the gates for that). S94_SIDES applies AFTER _touch() has
consumed the pending -- exactly the harness's Cfg.sides semantics -- so every lab arm run
with Cfg.sides transfers 1:1 to a container running with the env set.
"""
from __future__ import annotations

import importlib
import os
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

_STRAT_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "strategies"))
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from backtest_strategies import s94_sweep_reversal as s94   # noqa: E402
from lab.harness import CACHE, Cfg, load_bars, replay        # noqa: E402

_T = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("S94_SD_MULT", "S94_SIDES"):
        monkeypatch.delenv(k, raising=False)
    importlib.reload(s94)
    s94.reset_state()
    yield
    for k in ("S94_SD_MULT", "S94_SIDES"):
        monkeypatch.delenv(k, raising=False)
    importlib.reload(s94)
    s94.reset_state()


def _reload_with(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return importlib.reload(s94)


def _arm(side: int):
    """A pending that the probe below retests immediately (no phantom-guard trip)."""
    lvl = 2010.0
    s94._pending.append({
        "side": side, "level": lvl,
        "stop": lvl + 2.0 if side < 0 else lvl - 2.0,
        "tp": lvl - 6.0 if side < 0 else lvl + 6.0,
        "armed_after": _T - timedelta(minutes=10), "expires_at": _T + timedelta(hours=1)})


def _probe(hi, lo):
    t = pd.Timestamp(_T - timedelta(minutes=1))
    return pd.DataFrame({"time": [t], "open": [hi], "high": [hi], "low": [lo], "close": [lo]})


def _w5m(n=400):
    t = pd.date_range(_T - timedelta(minutes=5 * n), periods=n, freq="5min", tz="UTC")
    return pd.DataFrame({"time": t, "open": 2000.0, "high": 2001.0, "low": 1999.0,
                         "close": 2000.0, "volume": 1.0})


def test_defaults_are_the_historical_constants():
    assert s94._SD_MULT == 2.0
    assert s94._SIDES == frozenset({"BUY", "SELL"})


def test_sd_mult_is_env_tunable(monkeypatch):
    mod = _reload_with(monkeypatch, S94_SD_MULT="2.5")
    assert mod._SD_MULT == 2.5


@pytest.mark.parametrize("raw,expect", [("BUY", {"BUY"}), ("SELL", {"SELL"}),
                                        ("buy, sell", {"BUY", "SELL"}), ("SELL,BUY", {"BUY", "SELL"})])
def test_sides_parse(monkeypatch, raw, expect):
    mod = _reload_with(monkeypatch, S94_SIDES=raw)
    assert mod._SIDES == frozenset(expect)


@pytest.mark.parametrize("raw", ["LONG", "", "BUY,LONG", "BOTH"])
def test_invalid_sides_fail_loud_at_import(monkeypatch, raw):
    monkeypatch.setenv("S94_SIDES", raw)
    with pytest.raises(ValueError, match="S94_SIDES"):
        importlib.reload(s94)


def test_default_fires_both_sides():
    _arm(-1)
    sig = s94.get_signal(_probe(2010.5, 2009.0), _w5m(), None, _T)
    assert sig is not None and sig.side == "SELL"
    _arm(+1)
    sig = s94.get_signal(_probe(2011.0, 2009.5), _w5m(), None, _T)
    assert sig is not None and sig.side == "BUY"


def test_buy_only_suppresses_sell_and_consumes_the_pending(monkeypatch):
    mod = _reload_with(monkeypatch, S94_SIDES="BUY")
    mod.reset_state()
    _arm(-1)
    assert mod.get_signal(_probe(2010.5, 2009.0), _w5m(), None, _T) is None
    assert mod._pending == []            # consumed, exactly as the harness Cfg.sides path
    _arm(+1)
    sig = mod.get_signal(_probe(2011.0, 2009.5), _w5m(), None, _T)
    assert sig is not None and sig.side == "BUY"


def test_sell_only_suppresses_buy(monkeypatch):
    mod = _reload_with(monkeypatch, S94_SIDES="SELL")
    mod.reset_state()
    _arm(+1)
    assert mod.get_signal(_probe(2011.0, 2009.5), _w5m(), None, _T) is None
    assert mod._pending == []


@pytest.mark.skipif(not (CACHE / "is_XAU_USD_1m.parquet").exists(), reason="bars cache not present")
def test_env_flag_replay_equals_harness_sides_gate():
    bars = load_bars()
    W = dict(start="2026-03-01", end="2026-04-01")
    via_env = replay("s94_sweep_reversal", bars, cfg=Cfg(env={"S94_SIDES": "BUY", "S94_SD_MULT": "3.0"}), **W)
    via_cfg = replay("s94_sweep_reversal", bars, cfg=Cfg(sides=("BUY",), patch={"_SD_MULT": 3.0}), **W)
    assert via_env["n"] > 0
    pd.testing.assert_frame_equal(via_env["trades"], via_cfg["trades"])
