"""Offline event-loop simulator for the Strategy Manager (spec 2026-07-02).
Imports PRODUCTION compute_regime + POLICIES — never copies them."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime

import pandas as pd

from strategy_manager.policies import POLICIES
from backtest_strategies import s95_session_breakout, s96_h1_momentum, \
    s97_snap_scalper_m5, kronos_session_breakout
from backtest_strategies.base import Signal


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


# ── Position lifecycle ─────────────────────────────────────────────────────────


@dataclass
class SimPosition:
    """Open simulated position tracked by the event loop."""
    strategy: str
    side: str               # "BUY" | "SELL"
    entry_time: datetime
    entry_px: float
    sl: float               # current stop level (ratcheted for trailing)
    tp: float
    max_hold_min: float | None
    trailing: bool
    trail_dist: float       # abs(sig.entry_price - sig.stop_loss), fixed at open
    hwm: float              # BUY: running high-water mark; SELL: running low-water mark


@dataclass
class TradeRecord:
    """Completed trade produced by step_position."""
    strategy: str
    entry_time: datetime
    side: str
    entry_px: float
    sl: float
    tp: float
    exit_px: float
    exit_time: datetime
    outcome: str            # "TP" | "SL" | "TIME" | "TRAIL" | "OPEN"
    pnl_pts: float
    pnl_usd: float
    gate_reason: str        # captured at entry by event loop; empty when not available


def open_position(sig: Signal, strat_name: str, now: datetime,
                  cfg: SimConfig) -> SimPosition:
    """Apply entry friction and initialise a SimPosition.

    Entry friction (spread/2 + slippage) worsens the fill:
      BUY  → pays more  (entry_px = sig.entry_price + friction)
      SELL → receives less (entry_px = sig.entry_price - friction)

    SL/TP levels stay exactly as signalled.
    trail_dist is fixed at |sig.entry_price - sig.stop_loss| and never changes.
    hwm starts at entry_px (the worst fill, used as the initial water mark).
    """
    friction = cfg.entry_friction_pts
    if sig.side == "BUY":
        entry_px = sig.entry_price + friction
    else:  # SELL
        entry_px = sig.entry_price - friction

    return SimPosition(
        strategy=strat_name,
        side=sig.side,
        entry_time=now,
        entry_px=entry_px,
        sl=sig.stop_loss,
        tp=sig.take_profit,
        max_hold_min=sig.max_hold_min,
        trailing=sig.trailing,
        trail_dist=abs(sig.entry_price - sig.stop_loss),
        hwm=entry_px,
    )


def step_position(
    pos: SimPosition,
    bar: pd.Series,
    now: datetime,
    cfg: SimConfig,
) -> tuple[SimPosition | None, TradeRecord | None]:
    """Advance an open position by one 1-minute bar.

    Check order (prevents intra-bar look-ahead — ratchet from this bar's
    high/low is applied LAST and only takes effect on the NEXT bar):

      1. SL touch  — checked against the PRE-UPDATE stop level
      2. TP touch  — only for non-trailing positions; also pre-update
      3. TIME exit — when elapsed >= max_hold_min (bar close price)
      4. Trailing ratchet update — new sl/hwm applied to future bars only

    Exit friction worsens every exit by cfg.entry_friction_pts (same magnitude
    as entry friction):
      BUY exits:  exit_px = level - friction  (sell back at lower price)
      SELL exits: exit_px = level + friction  (buy back at higher price)

    Returns (updated_pos, None) when position stays open,
            (None, TradeRecord) when position is closed this bar.
    """
    friction = cfg.entry_friction_pts
    is_buy = pos.side == "BUY"

    # Snapshot stop level BEFORE any ratchet update (prevents look-ahead).
    pre_sl = pos.sl

    # ── 1. SL touch ───────────────────────────────────────────────────────────
    # Strict comparison (</>): a bar whose low/high exactly equals the stop is
    # NOT treated as a hit (avoids false exits on round-number wicks).
    sl_hit = (bar["low"] < pre_sl) if is_buy else (bar["high"] > pre_sl)

    if sl_hit:
        exit_px = (pre_sl - friction) if is_buy else (pre_sl + friction)
        outcome = "TRAIL" if pos.trailing else "SL"
        pnl_pts = (exit_px - pos.entry_px) if is_buy else (pos.entry_px - exit_px)
        return None, TradeRecord(
            strategy=pos.strategy,
            entry_time=pos.entry_time,
            side=pos.side,
            entry_px=pos.entry_px,
            sl=pre_sl,
            tp=pos.tp,
            exit_px=exit_px,
            exit_time=now,
            outcome=outcome,
            pnl_pts=pnl_pts,
            pnl_usd=cfg.pts_to_usd(pnl_pts),
            gate_reason="",
        )

    # ── 2. TP touch (static positions only) ───────────────────────────────────
    if not pos.trailing:
        tp_hit = (bar["high"] >= pos.tp) if is_buy else (bar["low"] <= pos.tp)
        if tp_hit:
            exit_px = (pos.tp - friction) if is_buy else (pos.tp + friction)
            pnl_pts = (exit_px - pos.entry_px) if is_buy else (pos.entry_px - exit_px)
            return None, TradeRecord(
                strategy=pos.strategy,
                entry_time=pos.entry_time,
                side=pos.side,
                entry_px=pos.entry_px,
                sl=pos.sl,
                tp=pos.tp,
                exit_px=exit_px,
                exit_time=now,
                outcome="TP",
                pnl_pts=pnl_pts,
                pnl_usd=cfg.pts_to_usd(pnl_pts),
                gate_reason="",
            )

    # ── 3. Time exit ──────────────────────────────────────────────────────────
    if pos.max_hold_min is not None:
        elapsed_min = (now - pos.entry_time).total_seconds() / 60.0
        if elapsed_min >= pos.max_hold_min:
            close = bar["close"]
            exit_px = (close - friction) if is_buy else (close + friction)
            pnl_pts = (exit_px - pos.entry_px) if is_buy else (pos.entry_px - exit_px)
            return None, TradeRecord(
                strategy=pos.strategy,
                entry_time=pos.entry_time,
                side=pos.side,
                entry_px=pos.entry_px,
                sl=pos.sl,
                tp=pos.tp,
                exit_px=exit_px,
                exit_time=now,
                outcome="TIME",
                pnl_pts=pnl_pts,
                pnl_usd=cfg.pts_to_usd(pnl_pts),
                gate_reason="",
            )

    # ── 4. Trailing ratchet update (applies to NEXT bar's SL check) ───────────
    if pos.trailing:
        if is_buy:
            new_hwm = max(pos.hwm, bar["high"])
            new_sl = max(pos.sl, new_hwm - pos.trail_dist)
        else:
            new_hwm = min(pos.hwm, bar["low"])
            new_sl = min(pos.sl, new_hwm + pos.trail_dist)
        pos = replace(pos, hwm=new_hwm, sl=new_sl)

    return pos, None
