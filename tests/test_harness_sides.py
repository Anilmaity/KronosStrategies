"""lab.harness Cfg.sides -- a modelable entry gate that admits only the listed sides.

Applied after get_signal() like block_hours, so a filtered signal still costs nothing
and never opens a trade; concurrency/cooldown state therefore differs from the unfiltered
run, which is why only invariants (not counts) are asserted."""
from __future__ import annotations

import os
import sys

import pytest

_STRAT_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "strategies"))
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from lab.harness import CACHE, Cfg, load_bars, replay   # noqa: E402

pytestmark = pytest.mark.skipif(
    not (CACHE / "is_XAU_USD_1m.parquet").exists(), reason="bars cache not present")
W = dict(start="2026-05-04", end="2026-05-09")


def test_default_admits_both_sides():
    assert Cfg().sides == ("BUY", "SELL")
    t = replay("s99_mss_fvg", load_bars(), cfg=Cfg(), **W)["trades"]
    assert set(t.side) == {"BUY", "SELL"}


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_single_side_admits_only_that_side(side):
    bars = load_bars()
    both = replay("s99_mss_fvg", bars, cfg=Cfg(), **W)
    one = replay("s99_mss_fvg", bars, cfg=Cfg(sides=(side,)), **W)
    assert one["n"] > 0
    assert set(one["trades"].side) == {side}
    assert one["n"] <= both["n"]


def test_unknown_side_is_rejected():
    with pytest.raises(ValueError, match="sides"):
        replay("s99_mss_fvg", load_bars(), cfg=Cfg(sides=("LONG",)), **W)
