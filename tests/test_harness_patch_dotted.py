"""Cfg.patch with a dotted key ("package.module:ATTR") patches a DELEGATE module for the
duration of one replay and restores it afterwards -- so an arm that tunes e.g.
kronos_session_breakout._TP_MULT (used by the thin s95 wrapper) cannot leak into later arms
in the same process."""
from __future__ import annotations

import importlib
import os
import sys

import pytest

_STRAT_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "strategies"))
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from lab.harness import CACHE, Cfg, load_bars, replay   # noqa: E402

pytestmark = pytest.mark.skipif(
    not (CACHE / "is_XAU_USD_1m.parquet").exists(), reason="bars cache not present")
W = dict(start="2026-05-04", end="2026-05-16")


def test_dotted_patch_applies_and_restores():
    ksb = importlib.import_module("backtest_strategies.kronos_session_breakout")
    before = ksb._TP_MULT
    bars = load_bars()
    base = replay("s95_session_breakout", bars, cfg=Cfg(win_5m=300), **W)
    tuned = replay("s95_session_breakout", bars,
                   cfg=Cfg(win_5m=300, patch={"backtest_strategies.kronos_session_breakout:_TP_MULT": 2.0}), **W)
    assert ksb._TP_MULT == before                       # restored
    assert base["n"] > 0 and tuned["n"] > 0
    assert not tuned["trades"].tp.equals(base["trades"].tp)   # the patch took effect
    again = replay("s95_session_breakout", bars, cfg=Cfg(win_5m=300), **W)
    assert again["trades"].equals(base["trades"])       # no leak into the next arm


def test_dotted_patch_unknown_attr_raises():
    with pytest.raises(AttributeError):
        replay("s95_session_breakout", load_bars(),
               cfg=Cfg(win_5m=300, patch={"backtest_strategies.kronos_session_breakout:_NOPE": 1}), **W)
