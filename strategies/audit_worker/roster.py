"""Build manager-sim StratSpecs from a run's roster_snapshot.

The snapshot is captured server-side by the RunManagerBacktest mutation:
``[{"name": ..., "policy_key": ..., "policy_params": {...}}, ...]``. Only
strategies with a local replayable module are simulated — the Telegram copy
slot (external signals) and anything unknown produce a note instead of a spec.
"""
from __future__ import annotations

import re

from backtest_strategies import (
    s93_fvg_scalp,
    s94_sweep_reversal,
    s99_mss_fvg,
    s100_m3_combo,
)
from backtest.manager_sim_engine import StratSpec
from strategy_manager.policies import POLICIES

MODULES = {
    m.NAME: m
    for m in (s93_fvg_scalp, s94_sweep_reversal, s99_mss_fvg, s100_m3_combo)
}


def _s_code(name: str) -> str | None:
    """Extract the sNN strategy code ('93', '100', ...) from either naming
    scheme. The DB Strategy rows carry display names ('S93 FVG Scalp') while
    the modules carry internal NAMEs ('KRONOS_S93_FVG_SCALP') — the roster
    snapshot arrives with the former, so exact-NAME matching alone finds
    nothing (caught by the first live smoke run, 2026-08-01)."""
    m = re.search(r"\bS(\d+)\b", name.upper().replace("_", " "))
    return m.group(1) if m else None


MODULES_BY_CODE = {_s_code(name): mod for name, mod in MODULES.items()}


def _resolve_module(name: str):
    if name in MODULES:
        return MODULES[name]
    code = _s_code(name)
    return MODULES_BY_CODE.get(code) if code else None


def build_specs(roster_snapshot: list[dict]) -> tuple[list[StratSpec], list[str]]:
    """Spec names keep the SNAPSHOT's names (the DB display names) — sim
    TradeRecords and live_deltas both key on them, so the sim/live join in
    the per-strategy table stays consistent."""
    specs: list[StratSpec] = []
    notes: list[str] = []
    if not roster_snapshot:
        notes.append("empty roster snapshot: nothing to simulate")
        return specs, notes
    for entry in roster_snapshot:
        name = entry.get("name", "")
        module = _resolve_module(name)
        if module is None:
            notes.append(f"skipped {name}: not replayable")
            continue
        policy_key = entry.get("policy_key") or "always_on"
        if policy_key not in POLICIES:
            notes.append(
                f"{name}: unknown policy '{policy_key}', fell back to always_on")
            policy_key = "always_on"
        specs.append(StratSpec(
            name=name, module=module, policy_key=policy_key,
            policy_params=entry.get("policy_params") or {},
        ))
    return specs, notes
