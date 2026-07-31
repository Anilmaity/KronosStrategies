"""Task 6 (Manager Backtest plan): roster spec builder."""
from __future__ import annotations

import os
import sys

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies"))
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from audit_worker.roster import MODULES, build_specs  # noqa: E402


def _entry(name, policy="always_on", params=None):
    return {"name": name, "policy_key": policy, "policy_params": params or {}}


def test_builds_specs_for_full_roster():
    snap = [
        _entry("KRONOS_S93_FVG_SCALP"),
        _entry("KRONOS_S94_SWEEP_REVERSAL", "session_vol", {"w": 2}),
        _entry("KRONOS_S99_MSS_FVG"),
        _entry("KRONOS_S100_M3_COMBO"),
    ]
    specs, notes = build_specs(snap)
    assert [s.name for s in specs] == [e["name"] for e in snap]
    assert notes == []
    assert specs[1].policy_key == "session_vol"
    assert specs[1].policy_params == {"w": 2}
    assert specs[0].module is MODULES["KRONOS_S93_FVG_SCALP"]


def test_copy_slot_skipped_with_note():
    specs, notes = build_specs([_entry("Neymar Telegram Copy")])
    assert specs == []
    assert notes == ["skipped Neymar Telegram Copy: not replayable"]


def test_unknown_policy_falls_back():
    specs, notes = build_specs([_entry("KRONOS_S93_FVG_SCALP", "bogus")])
    assert len(specs) == 1
    assert specs[0].policy_key == "always_on"
    assert "unknown policy 'bogus'" in notes[0]


def test_empty_snapshot():
    specs, notes = build_specs([])
    assert specs == []
    assert notes == ["empty roster snapshot: nothing to simulate"]
