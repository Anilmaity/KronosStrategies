"""Result-JSON assembly for the Manager Backtest worker (spec `result` keys)."""
from __future__ import annotations

import pandas as pd

from backtest.manager_sim_engine import SimConfig, SimResult, TradeRecord

EQUITY_MAX_POINTS = 2000


def _win_rate(wins: int, total: int) -> float:
    return round(100.0 * wins / total, 2) if total else 0.0


def arm_summary(trades: list[TradeRecord], cfg: SimConfig) -> dict:
    pnl_pts = sum(t.pnl_pts for t in trades)
    wins = sum(1 for t in trades if t.pnl_pts > 0)
    gross_win = sum(t.pnl_pts for t in trades if t.pnl_pts > 0)
    gross_loss = -sum(t.pnl_pts for t in trades if t.pnl_pts < 0)

    cum = peak = max_dd = 0.0
    for t in sorted(trades, key=lambda t: t.exit_time):
        cum += t.pnl_pts
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    return {
        "pnl_pts": round(pnl_pts, 4),
        "pnl_usd": round(cfg.pts_to_usd(pnl_pts), 2),
        "trades": len(trades),
        "win_rate": _win_rate(wins, len(trades)),
        "max_dd_pts": round(max_dd, 4),
        "profit_factor": (round(gross_win / gross_loss, 4)
                          if gross_loss > 0 else None),
    }


def sim_per_strategy(trades: list[TradeRecord], cfg: SimConfig) -> dict[str, dict]:
    """Same shape as live_deltas.live_summary, per strategy, from sim trades."""
    out: dict[str, dict] = {}
    for t in trades:
        agg = out.setdefault(t.strategy,
                             {"pnl_usd": 0.0, "trades": 0, "_wins": 0})
        agg["pnl_usd"] += t.pnl_usd
        agg["trades"] += 1
        if t.pnl_pts > 0:
            agg["_wins"] += 1
    for agg in out.values():
        agg["win_rate"] = _win_rate(agg.pop("_wins"), agg["trades"])
        agg["pnl_usd"] = round(agg["pnl_usd"], 2)
    return out


def equity_curve(trades: list[TradeRecord]) -> list[list]:
    """[[iso_exit_time, cum_pnl_pts], ...] stride-downsampled to
    <= EQUITY_MAX_POINTS, always keeping the final point."""
    pts: list[list] = []
    cum = 0.0
    for t in sorted(trades, key=lambda t: t.exit_time):
        cum += t.pnl_pts
        pts.append([pd.Timestamp(t.exit_time).isoformat(), round(cum, 4)])
    if len(pts) <= EQUITY_MAX_POINTS:
        return pts
    stride = -(-len(pts) // EQUITY_MAX_POINTS)   # ceil division
    sampled = pts[::stride]
    if sampled[-1] != pts[-1]:
        sampled.append(pts[-1])
    return sampled


def trades_frame(trades: list[TradeRecord]) -> pd.DataFrame:
    return pd.DataFrame([{
        "strategy": t.strategy, "side": t.side,
        "entry_time": pd.Timestamp(t.entry_time).isoformat(),
        "exit_time": pd.Timestamp(t.exit_time).isoformat(),
        "entry_px": t.entry_px, "sl": t.sl, "tp": t.tp, "exit_px": t.exit_px,
        "outcome": t.outcome, "pnl_pts": t.pnl_pts, "pnl_usd": t.pnl_usd,
        "gate_reason": t.gate_reason,
    } for t in trades])


def assemble(gated: SimResult, ungated: SimResult | None, cfg: SimConfig,
             s5_report: dict, per_strategy: dict, notes: list[str],
             trades_csv: str) -> dict:
    summary = {"gated": arm_summary(gated.trades, cfg)}
    curves = {"gated": equity_curve(gated.trades)}
    if ungated is not None:
        summary["ungated"] = arm_summary(ungated.trades, cfg)
        curves["ungated"] = equity_curve(ungated.trades)
    return {
        "summary": summary,
        "per_strategy": per_strategy,
        "equity_curve": curves,
        "s5_resolution": s5_report,
        "trades_csv": trades_csv,
        "notes": notes,
        "kill_trips": gated.kill_trips,
        "paused_pct": gated.paused_pct,
    }
