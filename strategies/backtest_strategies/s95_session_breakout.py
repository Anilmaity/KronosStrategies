"""
Kronos S95 -- Session Opening-Range Breakout (manager session slot)
-------------------------------------------------------------------
Rewritten 2026-07-06: now a thin delegate of kronos_session_breakout (the
validated bias-filtered 30-min ORB), so the manager's PAPER session slot
trades the SAME design as the live SESSION_BREAKOUT bot and paper-vs-live
fidelity is directly measurable.

Why the old design was dropped: the original S95 (London/NY 15-min OR,
2-12pt width filter, 8pt risk cap, TP 1.5R) fired ZERO signals in its first
paper week, and in the 2026-07-06 optimization sweep every 15-min-OR variant
that hit 70%+ WR on train FAILED the held-out 2026 half. The 30-min-OR
geometry below is the one that held:

  sessions [1,7,12,13,14] UTC, OR = first 30 min, EMA240+48-slope bias gate,
  entry at the boundary on the 1m touch, SL = 2.0xOR, TP = 0.8xOR,
  180-min time exit. Train WR 68.6% PF 1.20 / test WR 72.3% PF 1.57
  (cost 0.45pt; still profitable both halves at 0.80pt stress).

NAME keeps the historical "KRONOS_S95_SESSION_BREAKOUT" identifier -- it is
the strategy's DB identity (entry_manager variation map, deploy_manager
roster, ManagedStrategy session slot, compose service env) and must not
change. Signals are re-tagged S95_* so live audit rows distinguish the two
deployments.
"""
from __future__ import annotations

from datetime import datetime

from backtest_strategies import kronos_session_breakout as _orb
from backtest_strategies.base import Signal, StrategyConfig

NAME = "KRONOS_S95_SESSION_BREAKOUT"  # historical DB identity -- do not change
CONFIG = StrategyConfig(
    name=NAME,
    description="Session ORB (delegate of kronos_session_breakout): 30-min OR, "
                "sessions [1,7,12,13,14] UTC, EMA240 bias, SL 2.0xOR, TP 0.8xOR, "
                "180-min time exit. High-WR geometry validated 2026-07-06.",
    cooldown_s=1800,           # >= OR length; per-session dedup is stricter
    session_start_hour=None,   # five discrete hours -> gated inside get_signal
    session_end_hour=None,
    max_concurrent_positions=1,
)


def reset_state() -> None:
    """Clear the shared per-session dedup memory (used by tests)."""
    _orb.reset_state()


def get_signal(w1m, w5m, w15m, now_utc: datetime) -> Signal | None:
    sig = _orb.get_signal(w1m, w5m, w15m, now_utc)
    if sig is None:
        return None
    return Signal(
        side=sig.side,
        entry_price=sig.entry_price,
        stop_loss=sig.stop_loss,
        take_profit=sig.take_profit,
        reason=sig.reason.replace("SESSION_BREAKOUT", "S95_ORB"),
        max_hold_min=sig.max_hold_min,
    )
