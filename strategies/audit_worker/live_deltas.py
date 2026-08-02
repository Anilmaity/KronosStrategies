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

from shared.models import Order, Position, Strategy, UserStrategy

_IST_SKEW = timedelta(hours=5, minutes=30)

# Position.realized_profit_loss is stored in PnL UNITS (points x lots), not
# dollars — same conversion entry_manager._todays_realized_usd applies. The
# first DONE smoke run (2026-08-01) reported live "-$1.87" vs sim "-$51.73"
# because this factor was missing.
_USD_PER_PNL_UNIT = 100.0


def _win_rate(wins: int, total: int) -> float:
    return round(100.0 * wins / total, 2) if total else 0.0


def _pf(gross_win: float, gross_loss: float) -> float | None:
    return round(gross_win / gross_loss, 4) if gross_loss > 0 else None


def live_summary(session, strategy_names: list[str], start_utc: datetime,
                 end_utc: datetime) -> dict[str, dict]:
    """Per-strategy realized aggregates for the window, sizing-invariant.

    realized_profit_loss is stored in PnL UNITS (points x lots) — dividing by
    the position's ENTRY-order lots recovers points, which is what makes the
    sim/live comparison sizing-invariant. total_buy_quantity is NOT used
    here: it is 0 for short positions.

    Returns {name: {"points": {pnl_pts, trades, win_rate, profit_factor},
                    "usd": {pnl_usd, trades, win_rate}}} — only names
    present in strategy_names appear; strategies with no realized positions
    (or none with a resolvable ENTRY-order lots) in the window are absent.
    """
    lo = (start_utc.replace(tzinfo=None) + _IST_SKEW)
    hi = (end_utc.replace(tzinfo=None) + _IST_SKEW)

    rows = (
        session.query(Position.id, Position.realized_profit_loss,
                      Strategy.name, Order.quantity)
        .join(UserStrategy, Position.user_strategy_id == UserStrategy.id)
        .join(Strategy, UserStrategy.strategy_id == Strategy.id)
        .join(Order, (Order.position_id == Position.id) &
                     (Order.condition == "ENTRY"))
        .filter(
            Strategy.name.in_(strategy_names),
            Position.quantity == 0,
            Position.created_at >= lo,
            Position.created_at <= hi,
        )
        .all()
    )

    acc: dict[str, dict] = {}
    for _pid, realized, name, lots in rows:
        realized = float(realized or 0.0)
        lots = float(lots or 0.0)
        if lots <= 0:
            continue  # cannot recover points; skip (noted upstream)
        pts = realized / lots
        a = acc.setdefault(name, {"pts": 0.0, "usd": 0.0, "n": 0, "w": 0,
                                  "gw": 0.0, "gl": 0.0})
        a["pts"] += pts
        a["usd"] += realized * _USD_PER_PNL_UNIT
        a["n"] += 1
        if pts > 0:
            a["w"] += 1
            a["gw"] += pts
        elif pts < 0:
            a["gl"] += -pts

    out: dict[str, dict] = {}
    for name, a in acc.items():
        out[name] = {
            "points": {
                "pnl_pts": round(a["pts"], 4),
                "trades": a["n"],
                "win_rate": _win_rate(a["w"], a["n"]),
                "profit_factor": _pf(a["gw"], a["gl"]),
            },
            "usd": {
                "pnl_usd": round(a["usd"], 2),
                "trades": a["n"],
                "win_rate": _win_rate(a["w"], a["n"]),
            },
        }
    return out


def trade_losses_usd(session, strategy_names: list[str], start_utc: datetime,
                     end_utc: datetime) -> list[float]:
    """Individual closed-trade USD P&L for the given strategies/window, one
    value per Position -- NOT aggregated per strategy.

    `sizing.infer_live_risk_usd` derives a *per-trade* risk budget (see its
    docstring and `tests/test_sizing_matched_usd.py`, whose fixture is a list
    of individual SL-hit sizes like [-36, -38, -40, ...]) and only fires once
    it sees >= `floor` losing samples. Feeding it `live_summary`'s per-
    strategy net `usd.pnl_usd` instead collapses every trade a strategy made
    in the window into a single sample -- with a small roster (today: 4
    replayable strategies) the floor of 5 can then never be reached no
    matter how many live trades exist, silently disabling matched-USD.
    """
    lo = (start_utc.replace(tzinfo=None) + _IST_SKEW)
    hi = (end_utc.replace(tzinfo=None) + _IST_SKEW)

    rows = (
        session.query(Position.realized_profit_loss)
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
    return [float(r[0] or 0.0) * _USD_PER_PNL_UNIT for r in rows]


_ZERO_SIM_POINTS: dict = {
    "points": {"pnl_pts": 0.0, "trades": 0, "win_rate": 0.0, "profit_factor": None},
}


def deltas(sim: dict[str, dict], live: dict[str, dict],
          all_names: list[str] | None = None) -> dict[str, dict]:
    """Per-strategy sim / live / delta blocks, each covering the sizing-
    invariant `points` sub-block and, when both sides have priced one, the
    matched-`usd` sub-block. Strategies the sim knows but live never traded
    get live=None, delta=None.

    `all_names`, when given (the full roster, not just names the sim
    happened to produce a trade for), guarantees every roster strategy gets
    an entry -- a strategy absent from `sim` (zero sim trades in the window)
    still appears, with a zeroed `sim.points` block, rather than being
    silently dropped. These are the biggest sim/live gaps and must be
    visible, not discarded."""
    names = list(sim.keys())
    if all_names is not None:
        seen = set(names)
        for name in all_names:
            if name not in seen:
                names.append(name)
                seen.add(name)

    out: dict[str, dict] = {}
    for name in names:
        s = sim.get(name, _ZERO_SIM_POINTS)
        l = live.get(name)
        entry: dict = {"sim": s, "live": l, "delta": None}
        if l is not None:
            sp, lp = s["points"], l["points"]
            delta: dict = {"points": {
                "pnl_pts": round(sp["pnl_pts"] - lp["pnl_pts"], 4),
                "trades": sp["trades"] - lp["trades"],
                "win_rate": round(sp["win_rate"] - lp["win_rate"], 2),
            }}
            if "usd" in s and "usd" in l:
                su, lu = s["usd"], l["usd"]
                delta["usd"] = {
                    "pnl_usd": round(su["pnl_usd"] - lu["pnl_usd"], 2),
                    "trades": su["trades"] - lu["trades"],
                    "win_rate": round(su["win_rate"] - lu["win_rate"], 2),
                }
            entry["delta"] = delta
        out[name] = entry
    return out
