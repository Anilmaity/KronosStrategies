"""Build manager-sim StratSpecs from a run's roster_snapshot.

The snapshot is captured server-side by the RunManagerBacktest mutation:
``[{"name": ..., "policy_key": ..., "policy_params": {...}}, ...]``. Only
strategies with a local replayable module are simulated — the Telegram copy
slot (external signals) and anything unknown produce a note instead of a spec.
"""
from __future__ import annotations

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


def build_specs(roster_snapshot: list[dict]) -> tuple[list[StratSpec], list[str]]:
    specs: list[StratSpec] = []
    notes: list[str] = []
    if not roster_snapshot:
        notes.append("empty roster snapshot: nothing to simulate")
        return specs, notes
    for entry in roster_snapshot:
        name = entry.get("name", "")
        module = MODULES.get(name)
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
