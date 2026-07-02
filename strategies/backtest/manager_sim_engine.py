"""Offline event-loop simulator for the Strategy Manager (spec 2026-07-02).
Imports PRODUCTION compute_regime + POLICIES — never copies them."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from strategy_manager.policies import POLICIES
from backtest_strategies import s95_session_breakout, s96_h1_momentum, \
    s97_snap_scalper_m5, kronos_session_breakout


@dataclass
class SimConfig:
    start: datetime
    end: datetime
    spread_pts: float = 0.30
    slippage_pts: float = 0.10
    lots: float = 0.02
    kill_switch_usd: float = 150.0
    max_concurrent: int = 3
    regime_cadence_min: int = 5
    gated: bool = True

    @property
    def entry_friction_pts(self) -> float:
        return self.spread_pts / 2 + self.slippage_pts

    def pts_to_usd(self, pts: float) -> float:
        return pts * self.lots * 100.0


@dataclass(frozen=True)
class StratSpec:
    name: str
    module: object
    policy_key: str
    policy_params: dict


STRAT_SPECS: list[StratSpec] = [
    StratSpec(s95_session_breakout.NAME, s95_session_breakout, "session_vol", {}),
    StratSpec(s96_h1_momentum.NAME, s96_h1_momentum, "trending", {}),
    StratSpec(s97_snap_scalper_m5.NAME, s97_snap_scalper_m5, "quiet_fade", {}),
    StratSpec(kronos_session_breakout.NAME, kronos_session_breakout, "session_vol", {}),
]


@dataclass
class GuardState:
    kill_tripped_date: str | None = None
    day_realized_usd: float = 0.0
    day: str = ""


def evaluate_gates(snap, now_utc: datetime, guard: GuardState,
                   open_count: int, cfg: SimConfig) -> dict[str, tuple[bool, str]]:
    """Mirror of strategy_manager.manager.evaluate_tick guard order."""
    if not cfg.gated:
        return {s.name: (True, "ungated") for s in STRAT_SPECS}

    if snap.market_closed:
        return {s.name: (False, "market closed") for s in STRAT_SPECS}

    today = now_utc.date().isoformat()
    if guard.kill_tripped_date == today:
        return {s.name: (False, "kill-switch tripped") for s in STRAT_SPECS}

    if open_count >= cfg.max_concurrent:
        return {s.name: (False, f"max concurrent {open_count}/{cfg.max_concurrent}")
                for s in STRAT_SPECS}

    out: dict[str, tuple[bool, str]] = {}
    for s in STRAT_SPECS:
        out[s.name] = POLICIES[s.policy_key](snap, s.policy_params, now_utc)
    return out
