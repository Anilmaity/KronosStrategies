"""lab.harness honours CONFIG.session_start_hour/end_hour exactly as research_runner does:
the gate runs BEFORE get_signal() (so no signal is computed out of session), and a module
with None/None is unaffected."""
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


def test_module_session_hours_bound_entries():
    # s11 declares session 14..17 UTC; every entry must fall inside it
    r = replay("s11_m90_fade_ny", load_bars(), start="2026-05-04", end="2026-05-09", cfg=Cfg())
    assert r["n"] > 0
    hours = r["trades"].entry_time.dt.hour
    assert hours.between(14, 16).all(), sorted(hours.unique())


def test_none_session_is_unrestricted():
    # s99 gates hours inside get_signal (06..15) but declares None/None -> harness adds nothing
    r = replay("s99_mss_fvg", load_bars(), start="2026-05-04", end="2026-05-09", cfg=Cfg())
    assert r["n"] > 0 and r["trades"].entry_time.dt.hour.between(6, 15).all()
