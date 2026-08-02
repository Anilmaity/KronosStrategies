"""Result-JSON assembly for the Manager Backtest worker (spec `result` keys)."""
from __future__ import annotations

import pandas as pd

from audit_worker.sizing import matched_usd
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
    """Sizing-invariant points block per strategy, from sim trades.

    Same points shape as live_deltas.live_summary (usd block added in a
    later task).
    """
    acc: dict[str, dict] = {}
    for t in trades:
        a = acc.setdefault(t.strategy, {"pts": 0.0, "n": 0, "w": 0,
                                        "gw": 0.0, "gl": 0.0})
        a["pts"] += t.pnl_pts
        a["n"] += 1
        if t.pnl_pts > 0:
            a["w"] += 1
            a["gw"] += t.pnl_pts
        elif t.pnl_pts < 0:
            a["gl"] += -t.pnl_pts
    out: dict[str, dict] = {}
    for name, a in acc.items():
        out[name] = {"points": {
            "pnl_pts": round(a["pts"], 4),
            "trades": a["n"],
            "win_rate": _win_rate(a["w"], a["n"]),
            "profit_factor": (round(a["gw"] / a["gl"], 4)
                              if a["gl"] > 0 else None),
        }}
    return out


def add_matched_usd(sim_map: dict[str, dict], trades: list[TradeRecord],
                    risk_usd: float) -> dict[str, dict]:
    """Attach a `usd` block per strategy to `sim_map`, in place.

    Re-prices each sim trade at live's inferred per-trade risk budget
    (sizing.matched_usd, same clamp/round as entry_manager._risk_sized_qty)
    so sim and live dollars sit on the same economic basis. `risk_usd` must
    be a real number (None means the caller should skip calling this and
    omit the usd blocks instead). Returns `sim_map` for convenience.
    """
    acc: dict[str, dict] = {}
    for t in trades:
        a = acc.setdefault(t.strategy, {"usd": 0.0, "n": 0, "w": 0})
        sl_dist = abs(t.entry_px - t.sl)
        usd = matched_usd(t.pnl_pts, sl_dist, risk_usd)
        a["usd"] += usd
        a["n"] += 1
        if usd > 0:
            a["w"] += 1
    for name, a in acc.items():
        sim_map.setdefault(name, {})["usd"] = {
            "pnl_usd": round(a["usd"], 2),
            "trades": a["n"],
            "win_rate": _win_rate(a["w"], a["n"]),
        }
    return sim_map


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


def build_arms(gated: SimResult, ungated: SimResult | None,
               cfg: SimConfig) -> tuple[dict, dict]:
    """Build the `summary`/`equity_curve` blocks `assemble_v2` expects, from
    the gated (and optional ungated) SimResult arms. Lifted out of the old
    `assemble` so the worker can build them before calling `assemble_v2`."""
    summary = {"gated": arm_summary(gated.trades, cfg)}
    curves = {"gated": equity_curve(gated.trades)}
    if ungated is not None:
        summary["ungated"] = arm_summary(ungated.trades, cfg)
        curves["ungated"] = equity_curve(ungated.trades)
    return summary, curves


def assemble_v2(*, per_strategy: dict, summary: dict, curves: dict,
                s5_report: dict, notes: list[str], trades_csv: str,
                live_risk_usd_inferred: float | None, kill_trips: list,
                paused_pct: dict, ungated: SimResult | None = None) -> dict:
    """Assemble the Manager Backtest result JSON (points+usd+reconciliation
    per strategy, top-level matched-USD risk inference, extended notes).

    `ungated` is accepted for call-site symmetry with the sim arms but is not
    itself embedded — the caller folds it into `summary`/`curves` via
    `build_arms` before calling this.
    """
    return {
        "summary": summary,
        "per_strategy": per_strategy,
        "equity_curve": curves,
        "s5_resolution": s5_report,
        "trades_csv": trades_csv,
        "notes": notes,
        "live_risk_usd_inferred": live_risk_usd_inferred,
        "kill_trips": kill_trips,
        "paused_pct": paused_pct,
    }
