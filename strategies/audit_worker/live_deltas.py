"""Live summary + sim-vs-live deltas for the Manager Backtest worker.

Read-only aggregation over realized Position rows (quantity == 0) joined
Position -> UserStrategy -> Strategy for the roster's strategy names.

Timezone note: Position.created_at is stored via get_kolkata_time as a naive
IST-wall-clock value (-5:30 vs UTC) — the known platform quirk from the July
2026 drawdown investigation. The UTC window bounds are therefore shifted
+5:30 before comparing against created_at.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from shared.models import Position, Strategy, UserStrategy

_IST_SKEW = timedelta(hours=5, minutes=30)


def _win_rate(wins: int, total: int) -> float:
    return round(100.0 * wins / total, 2) if total else 0.0


def live_summary(session, strategy_names: list[str], start_utc: datetime,
                 end_utc: datetime) -> dict[str, dict]:
    """Per-strategy realized aggregates for the window.

    Returns {name: {"pnl_usd": float, "trades": int, "win_rate": float}} —
    only names present in strategy_names appear; strategies with no realized
    positions in the window are simply absent.
    """
    lo = (start_utc.replace(tzinfo=None) + _IST_SKEW)
    hi = (end_utc.replace(tzinfo=None) + _IST_SKEW)

    rows = (
        session.query(Position.realized_profit_loss, Strategy.name)
        .join(UserStrategy, Position.user_strategy_id == UserStrategy.id)
        .join(Strategy, UserStrategy.strategy_id == Strategy.id)
        .filter(
            Strategy.name.in_(strategy_names),
            Position.quantity == 0,
            Position.created_at >= lo,
            Position.created_at <= hi,
        )
        .all()
    )

    out: dict[str, dict] = {}
    for realized, name in rows:
        realized = float(realized or 0.0)
        agg = out.setdefault(name, {"pnl_usd": 0.0, "trades": 0, "_wins": 0})
        agg["pnl_usd"] += realized
        agg["trades"] += 1
        if realized > 0:
            agg["_wins"] += 1
    for name, agg in out.items():
        agg["win_rate"] = _win_rate(agg.pop("_wins"), agg["trades"])
        agg["pnl_usd"] = round(agg["pnl_usd"], 2)
    return out


def deltas(sim: dict[str, dict], live: dict[str, dict]) -> dict[str, dict]:
    """Per-strategy sim / live / delta blocks. Strategies the sim knows but
    live never traded get live=None, delta=None."""
    out: dict[str, dict] = {}
    for name, s in sim.items():
        l = live.get(name)
        entry: dict = {"sim": s, "live": l, "delta": None}
        if l is not None:
            entry["delta"] = {
                "pnl_usd": round(s["pnl_usd"] - l["pnl_usd"], 2),
                "trades": s["trades"] - l["trades"],
                "win_rate": round(s["win_rate"] - l["win_rate"], 2),
            }
        out[name] = entry
    return out
