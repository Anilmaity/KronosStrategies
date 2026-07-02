"""
manager_sim_report.py  (Task 5)
--------------------------------
Decision report generator for the Strategy Manager offline simulation
(spec 2026-07-02, section C).

Exports
-------
write_report(gated, ungated, cfg, out_dir, sensitivity) -> Path
    Write decision report files to *out_dir*.  Returns path to summary_<ts>.md.

run_sensitivity(frames, base_cfg) -> list[dict]
    6 full gated re-runs (4 threshold variants + 2 window variants).
    Returns list[dict] with keys: variant, combined_net_usd, n_trades, win_rate.

Max drawdown definition (stated once here, referenced in the report)
---------------------------------------------------------------------
Max DD is computed on the cumulative pnl_usd equity curve, ordered by
exit_time.  The running peak starts at 0 (zero initial equity).

    running_peak = max(0, max cumulative pnl seen so far)
    max_dd_usd   = max over all trades of (running_peak − cumulative_pnl)
    max_dd_pct   = (max_dd_usd / max(running_peak, 1)) × 100
                   → 100 % when peak stays at or below 0 and any loss occurs
                   → 0 % when there is no drawdown
"""
from __future__ import annotations

import csv
import math
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from backtest.manager_sim_engine import (
    SimResult,
    SimConfig,
    TradeRecord,
    STRAT_SPECS,
    StratSpec,
    run_sim,
)


# ─────────────────────────────────────────────────────────────────────────────
# Metric helpers
# ─────────────────────────────────────────────────────────────────────────────

def _net_usd(trades: list[TradeRecord]) -> float:
    return sum(t.pnl_usd for t in trades)


def _net_pts(trades: list[TradeRecord]) -> float:
    return sum(t.pnl_pts for t in trades)


def _win_rate(trades: list[TradeRecord]) -> float:
    if not trades:
        return 0.0
    return 100.0 * sum(1 for t in trades if t.pnl_usd > 0) / len(trades)


def _profit_factor(trades: list[TradeRecord]) -> float:
    gross_win  = sum(t.pnl_usd for t in trades if t.pnl_usd > 0)
    gross_loss = abs(sum(t.pnl_usd for t in trades if t.pnl_usd < 0))
    if gross_loss < 1e-9:
        return math.inf if gross_win > 1e-9 else 1.0
    return gross_win / gross_loss


def _compute_dd(trades: list[TradeRecord]) -> tuple[float, float]:
    """Return (max_dd_usd, max_dd_pct).

    See module-level docstring for the exact definition.
    """
    if not trades:
        return 0.0, 0.0
    sorted_trades = sorted(trades, key=lambda t: t.exit_time)
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in sorted_trades:
        cumulative += t.pnl_usd
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd
    if peak < 1e-9:
        dd_pct = 100.0 if max_dd > 1e-9 else 0.0
    else:
        dd_pct = 100.0 * max_dd / peak
    return max_dd, dd_pct


def _avg_hold_min(trades: list[TradeRecord]) -> float:
    if not trades:
        return 0.0
    return (
        sum((t.exit_time - t.entry_time).total_seconds() / 60.0 for t in trades)
        / len(trades)
    )


def _strat_trades(trades: list[TradeRecord], name: str) -> list[TradeRecord]:
    return [t for t in trades if t.strategy == name]


def _stats(trades: list[TradeRecord]) -> dict:
    dd_usd, dd_pct = _compute_dd(trades)
    return {
        "n":          len(trades),
        "wr":         _win_rate(trades),
        "pf":         _profit_factor(trades),
        "net_pts":    _net_pts(trades),
        "net_usd":    _net_usd(trades),
        "max_dd_usd": dd_usd,
        "max_dd_pct": dd_pct,
        "avg_hold":   _avg_hold_min(trades),
    }


def _pf_str(pf: float) -> str:
    return "inf" if math.isinf(pf) else f"{pf:.2f}"


def _month_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def _months_net(trades: list[TradeRecord]) -> dict[str, float]:
    """Return {month_key: net_usd}."""
    d: dict[str, float] = {}
    for t in trades:
        k = _month_key(t.exit_time)
        d[k] = d.get(k, 0.0) + t.pnl_usd
    return d


# ─────────────────────────────────────────────────────────────────────────────
# Rubric evaluation
# ─────────────────────────────────────────────────────────────────────────────

def _evaluate_rubric(
    gated_trades: list[TradeRecord],
    ungated_trades: list[TradeRecord],
    sensitivity: list[tuple[str, float]] | None,
) -> tuple[str, str]:
    """Return (verdict_line, detail_block).

    Decision rubric (spec section C):
      1. Gated combined net USD >= ungated combined net USD
      2. Gated max DD < ungated max DD  (ties at 0,0 pass)
      3. Gated >= ungated in >= 2 calendar months
      4. No sensitivity variant flips the sign of the combined delta
         (omitted / marked N/A when sensitivity is None)

    Returns a one-line verdict string and a multi-line detail block.
    """
    g_net = _net_usd(gated_trades)
    u_net = _net_usd(ungated_trades)
    g_dd, _  = _compute_dd(gated_trades)
    u_dd, __ = _compute_dd(ungated_trades)

    cond1 = g_net >= u_net

    # DD: gated strictly less, OR both exactly zero (no drawdown on either side)
    cond2 = (g_dd < u_dd) or (g_dd < 1e-9 and u_dd < 1e-9)

    # Month condition
    g_months = _months_net(gated_trades)
    u_months = _months_net(ungated_trades)
    all_months = sorted(set(list(g_months.keys()) + list(u_months.keys())))
    wins = sum(
        1 for m in all_months
        if g_months.get(m, 0.0) >= u_months.get(m, 0.0)
    )
    # Spec: "holds in >= 2 of 3 months". With fewer months present, require
    # min(2, n_months) months to win.
    required = min(2, len(all_months)) if all_months else 1
    cond3 = wins >= required

    # Sensitivity condition
    delta = g_net - u_net
    if sensitivity is not None:
        if abs(delta) < 1e-9:
            cond4: bool | None = True  # zero delta cannot flip
        else:
            # Flip = variant delta has opposite sign to base delta
            cond4 = all(
                (s_net - u_net) * delta >= 0
                for _, s_net in sensitivity
            )
        provisional = ""
    else:
        cond4 = None
        provisional = " (PROVISIONAL — sensitivity not run)"

    all_pass = cond1 and cond2 and cond3 and (cond4 is None or cond4)

    if all_pass:
        verdict = f"VERDICT: RECOMMEND master ON{provisional}"
    else:
        verdict = "VERDICT: DO NOT RECOMMEND master ON"

    # Detail lines
    detail_lines = [
        f"  1. Gated net ${g_net:+.2f} >= ungated net ${u_net:+.2f}: "
        + ("PASS" if cond1 else "FAIL"),

        f"  2. Gated max DD ${g_dd:.2f} < ungated max DD ${u_dd:.2f}: "
        + ("PASS" if cond2 else "FAIL"),

        f"  3. Edge in {wins}/{len(all_months)} months"
        + f" (need >= {required}): "
        + ("PASS" if cond3 else "FAIL"),

        f"  4. No sensitivity sign flip: "
        + ("N/A — not run" if cond4 is None else ("PASS" if cond4 else "FAIL")),
    ]
    return verdict, "\n".join(detail_lines)


# ─────────────────────────────────────────────────────────────────────────────
# CSV helpers
# ─────────────────────────────────────────────────────────────────────────────

_TRADE_FIELDS = [
    "strategy", "entry_time", "side", "entry_px", "sl", "tp",
    "exit_px", "exit_time", "outcome", "pnl_pts", "pnl_usd",
    "gated_reason_at_entry",
]


def _write_trades_csv(trades: list[TradeRecord], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=_TRADE_FIELDS)
        w.writeheader()
        for t in trades:
            w.writerow({
                "strategy":            t.strategy,
                "entry_time":          t.entry_time.isoformat(),
                "side":                t.side,
                "entry_px":            round(t.entry_px, 4),
                "sl":                  round(t.sl, 4),
                "tp":                  round(t.tp, 4),
                "exit_px":             round(t.exit_px, 4),
                "exit_time":           t.exit_time.isoformat(),
                "outcome":             t.outcome,
                "pnl_pts":             round(t.pnl_pts, 4),
                "pnl_usd":             round(t.pnl_usd, 4),
                "gated_reason_at_entry": t.gate_reason,
            })


def _write_regime_csv(regime_rows: list[dict], path: Path) -> None:
    if not regime_rows:
        path.write_text("time,d1_bias,h4_bias,vol_regime,trend_regime,session,market_closed\n",
                        encoding="utf-8")
        return
    # Use classification fields only (spec note: details fields can be stale on cache hits)
    primary_fields = [
        "time", "d1_bias", "h4_bias", "vol_regime",
        "trend_regime", "session", "market_closed",
    ]
    # Extend with any extra top-level keys (not nested details)
    extra = [k for k in regime_rows[0] if k not in primary_fields]
    all_fields = primary_fields + extra
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=all_fields, extrasaction="ignore")
        w.writeheader()
        for row in regime_rows:
            w.writerow(row)


# ─────────────────────────────────────────────────────────────────────────────
# Markdown report builder
# ─────────────────────────────────────────────────────────────────────────────

def _regime_distribution(
    regime_rows: list[dict],
) -> tuple[dict[str, float], dict[str, float], dict[str, float], float]:
    """Return (vol_pct, trend_pct, session_pct, flip_rate_per_day).

    flip_rate counts combined transitions across vol_regime + trend_regime +
    session, divided by the number of calendar days spanned.
    """
    n = len(regime_rows)
    if n == 0:
        return {}, {}, {}, 0.0

    vol_counts:     dict[str, int] = {}
    trend_counts:   dict[str, int] = {}
    session_counts: dict[str, int] = {}
    flips = 0
    prev_vol = prev_trend = prev_session = None

    for row in regime_rows:
        v  = row.get("vol_regime",   "UNKNOWN")
        tr = row.get("trend_regime", "UNKNOWN")
        s  = row.get("session",      "UNKNOWN")

        vol_counts[v]     = vol_counts.get(v, 0) + 1
        trend_counts[tr]  = trend_counts.get(tr, 0) + 1
        session_counts[s] = session_counts.get(s, 0) + 1

        if prev_vol is not None:
            if v  != prev_vol:     flips += 1
            if tr != prev_trend:   flips += 1
            if s  != prev_session: flips += 1
        prev_vol, prev_trend, prev_session = v, tr, s

    try:
        first_ts = pd.Timestamp(regime_rows[0]["time"])
        last_ts  = pd.Timestamp(regime_rows[-1]["time"])
        days = max(1.0, (last_ts - first_ts).total_seconds() / 86400.0)
    except Exception:
        days = 1.0

    flip_rate = flips / days

    vol_pct     = {k: 100.0 * cnt / n for k, cnt in vol_counts.items()}
    trend_pct   = {k: 100.0 * cnt / n for k, cnt in trend_counts.items()}
    session_pct = {k: 100.0 * cnt / n for k, cnt in session_counts.items()}

    return vol_pct, trend_pct, session_pct, flip_rate


def _build_markdown(
    gated:       SimResult,
    ungated:     SimResult,
    cfg:         SimConfig,
    sensitivity: list[tuple[str, float]] | None,
    ts:          str,
) -> str:
    lines: list[str] = []

    # ── Header ────────────────────────────────────────────────────────────────
    lines += [
        "# Strategy Manager Simulation — Decision Report",
        "",
        f"**Generated:** {ts} UTC",
        f"**Period:** {cfg.start.date()} to {cfg.end.date()}",
        (
            f"**Config:** spread={cfg.spread_pts:.2f} pts, "
            f"slippage={cfg.slippage_pts:.2f} pts, "
            f"lots={cfg.lots:.3f}, "
            f"kill_switch=${cfg.kill_switch_usd:.0f}, "
            f"max_concurrent={cfg.max_concurrent}, "
            f"regime_cadence={cfg.regime_cadence_min} min"
        ),
        "",
    ]

    # ── Decision Rubric ───────────────────────────────────────────────────────
    verdict, rubric_detail = _evaluate_rubric(
        gated.trades, ungated.trades, sensitivity
    )
    lines += [
        "## Decision Rubric",
        "",
        (
            "Recommend master ON iff: "
            "(1) gated >= ungated net USD combined; "
            "(2) gated max DD < ungated max DD; "
            "(3) edge holds (gated >= ungated) in >= 2 calendar months; "
            "(4) no sensitivity variant flips the sign of the combined gated-vs-ungated delta."
        ),
        "",
        "Evaluation:",
        "",
    ]
    lines += rubric_detail.split("\n")
    lines += [
        "",
        f"**{verdict}**",
        "",
    ]

    # ── Per-strategy table ────────────────────────────────────────────────────
    all_strats = sorted(
        set(t.strategy for t in gated.trades + ungated.trades)
    )

    lines += [
        "## Per-Strategy Performance",
        "",
        "Max DD computed on cumulative pnl_usd equity curve ordered by exit_time "
        "(running peak starts at 0; max_dd_pct = max_dd / peak × 100).",
        "",
        "| Strategy | Mode | Trades | WR% | PF | Net Pts | Net USD |"
        " Max DD $ | Max DD % | Avg Hold (min) |",
        "|----------|------|-------:|----:|----:|--------:|--------:|"
        "---------:|---------:|---------------:|",
    ]

    for sname in all_strats:
        g = _stats(_strat_trades(gated.trades, sname))
        u = _stats(_strat_trades(ungated.trades, sname))
        d_net_pts = g["net_pts"]  - u["net_pts"]
        d_net_usd = g["net_usd"]  - u["net_usd"]
        d_dd_usd  = g["max_dd_usd"] - u["max_dd_usd"]
        d_n       = g["n"] - u["n"]
        lines += [
            f"| {sname} | Gated    | {g['n']} | {g['wr']:.1f} | {_pf_str(g['pf'])} |"
            f" {g['net_pts']:+.2f} | {g['net_usd']:+.2f} |"
            f" {g['max_dd_usd']:.2f} | {g['max_dd_pct']:.1f}% | {g['avg_hold']:.1f} |",

            f"| {sname} | Ungated  | {u['n']} | {u['wr']:.1f} | {_pf_str(u['pf'])} |"
            f" {u['net_pts']:+.2f} | {u['net_usd']:+.2f} |"
            f" {u['max_dd_usd']:.2f} | {u['max_dd_pct']:.1f}% | {u['avg_hold']:.1f} |",

            f"| {sname} | **Delta** | {d_n:+d} | — | — |"
            f" {d_net_pts:+.2f} | {d_net_usd:+.2f} |"
            f" {d_dd_usd:+.2f} | — | — |",
        ]
    lines.append("")

    # ── Combined portfolio table ───────────────────────────────────────────────
    g_all = _stats(gated.trades)
    u_all = _stats(ungated.trades)
    d_net_pts = g_all["net_pts"]    - u_all["net_pts"]
    d_net_usd = g_all["net_usd"]    - u_all["net_usd"]
    d_dd_usd  = g_all["max_dd_usd"] - u_all["max_dd_usd"]
    d_n       = g_all["n"]          - u_all["n"]

    lines += [
        "## Combined Portfolio",
        "",
        "| Mode | Trades | WR% | PF | Net Pts | Net USD |"
        " Max DD $ | Max DD % | Avg Hold (min) |",
        "|------|-------:|----:|----:|--------:|--------:|"
        "---------:|---------:|---------------:|",

        f"| Gated    | {g_all['n']} | {g_all['wr']:.1f} | {_pf_str(g_all['pf'])} |"
        f" {g_all['net_pts']:+.2f} | {g_all['net_usd']:+.2f} |"
        f" {g_all['max_dd_usd']:.2f} | {g_all['max_dd_pct']:.1f}% | {g_all['avg_hold']:.1f} |",

        f"| Ungated  | {u_all['n']} | {u_all['wr']:.1f} | {_pf_str(u_all['pf'])} |"
        f" {u_all['net_pts']:+.2f} | {u_all['net_usd']:+.2f} |"
        f" {u_all['max_dd_usd']:.2f} | {u_all['max_dd_pct']:.1f}% | {u_all['avg_hold']:.1f} |",

        f"| **Delta (G-U)** | {d_n:+d} | — | — |"
        f" {d_net_pts:+.2f} | **{d_net_usd:+.2f}** |"
        f" {d_dd_usd:+.2f} | — | — |",
        "",
    ]

    # ── Kill-switch trips ─────────────────────────────────────────────────────
    lines += [
        "## Kill-Switch Trips",
        "",
        f"- **Gated:** {len(gated.kill_trips)} trip(s)"
        + (f"  — dates: {', '.join(gated.kill_trips)}" if gated.kill_trips else "  — no trips"),
        "- **Ungated:** N/A (kill-switch inactive in ungated mode)",
        "",
    ]

    # ── Policy pause ──────────────────────────────────────────────────────────
    lines += [
        "## Policy Pause Summary",
        "",
        "| Strategy | % Time Policy-Paused (Gated) |",
        "|----------|-----------------------------|",
    ]
    for sname, pct in sorted(gated.paused_pct.items()):
        lines.append(f"| {sname} | {pct:.1f}% |")
    lines.append("")

    # ── Regime distribution ───────────────────────────────────────────────────
    lines += ["## Regime Distribution", ""]

    if gated.regime_rows:
        vol_pct, trend_pct, session_pct, flip_rate = _regime_distribution(
            gated.regime_rows
        )

        lines += [
            "### Volatility Regime",
            "",
            "| Bucket | % Time |",
            "|--------|-------:|",
        ]
        for k in ("LOW", "NORMAL", "HIGH", "EXTREME"):
            if k in vol_pct:
                lines.append(f"| {k} | {vol_pct[k]:.1f}% |")

        lines += [
            "",
            "### Trend Regime",
            "",
            "| Bucket | % Time |",
            "|--------|-------:|",
        ]
        for k in ("TRENDING", "MIXED", "RANGING"):
            if k in trend_pct:
                lines.append(f"| {k} | {trend_pct[k]:.1f}% |")

        lines += [
            "",
            "### Session Distribution",
            "",
            "| Session | % Time |",
            "|---------|-------:|",
        ]
        for k in sorted(session_pct):
            lines.append(f"| {k} | {session_pct[k]:.1f}% |")

        flag = ""
        if flip_rate > 12:
            flag = "  **WARNING: >12/day average — HIGH CHURN**"
        lines += [
            "",
            f"**Regime flip rate (vol + trend + session):** {flip_rate:.1f} changes/day{flag}",
            "",
        ]
    else:
        lines += ["_(No regime rows — distribution not available.)_", ""]

    # ── Month-by-month ────────────────────────────────────────────────────────
    g_months = _months_net(gated.trades)
    u_months = _months_net(ungated.trades)
    all_months = sorted(set(list(g_months.keys()) + list(u_months.keys())))

    lines += [
        "## Month-by-Month Net USD (Gated vs Ungated)",
        "",
        "| Month | Strategy | Gated $ | Ungated $ | Delta $ |",
        "|-------|----------|--------:|----------:|--------:|",
    ]
    for m in all_months:
        for sname in all_strats:
            g_t = [t for t in gated.trades
                   if _month_key(t.exit_time) == m and t.strategy == sname]
            u_t = [t for t in ungated.trades
                   if _month_key(t.exit_time) == m and t.strategy == sname]
            gv = sum(t.pnl_usd for t in g_t)
            uv = sum(t.pnl_usd for t in u_t)
            lines.append(
                f"| {m} | {sname} | {gv:+.2f} | {uv:+.2f} | {gv-uv:+.2f} |"
            )
        # Combined row
        gc = g_months.get(m, 0.0)
        uc = u_months.get(m, 0.0)
        lines.append(
            f"| {m} | **COMBINED** | **{gc:+.2f}** | **{uc:+.2f}** | **{gc-uc:+.2f}** |"
        )
    lines.append("")

    # ── Sensitivity grid ──────────────────────────────────────────────────────
    lines += ["## Sensitivity Grid", ""]

    if sensitivity is None:
        lines += [
            "_(Not run — re-execute with `--sensitivity` flag for the full grid.)_",
            "",
        ]
    else:
        base_g_net = g_all["net_usd"]
        lines += [
            f"Base combined gated net USD: **{base_g_net:+.2f}**. "
            "Looking for a plateau (variants within ~10% of base = robust).",
            "",
            "| Variant | Combined Gated Net USD | Delta vs Base |",
            "|---------|----------------------:|---------------:|",
        ]
        for variant, net_usd in sensitivity:
            d = net_usd - base_g_net
            lines.append(f"| {variant} | {net_usd:+.2f} | {d:+.2f} |")
        lines.append("")

    # ── Caveat ────────────────────────────────────────────────────────────────
    lines += [
        "## Caveat: 3 Months Is One Regime Sample",
        "",
        (
            "This simulation covers approximately 3 months — one regime sample. "
            "The results support a decision to arm the Strategy Manager in PAPER mode "
            "or at small size, not a permanent verdict. "
            "Regime classification rules and policy thresholds should be re-evaluated "
            "after at least 6 months of gated live data before scaling. "
            "A single favourable backtest window does not prove the gating edge is durable; "
            "regime character shifts and strategy correlations change over time. "
            "Use this report as a cautious go/no-go gate, not a performance guarantee."
        ),
        "",
    ]

    return "\n".join(lines) + "\n"


# ─────────────────────────────────────────────────────────────────────────────
# Public API: write_report
# ─────────────────────────────────────────────────────────────────────────────

def write_report(
    gated:       SimResult,
    ungated:     SimResult,
    cfg:         SimConfig,
    out_dir:     Path,
    sensitivity: list[tuple[str, float]] | None,
) -> Path:
    """Write decision report files to *out_dir*.

    Writes:
      trades_gated_<ts>.csv
      trades_ungated_<ts>.csv
      regime_timeline_<ts>.csv
      summary_<ts>.md

    Returns the path to ``summary_<ts>.md``.

    Parameters
    ----------
    gated      : SimResult from a gated run (cfg.gated=True).
    ungated    : SimResult from an ungated run (cfg.gated=False).
    cfg        : SimConfig used for the *gated* run (metadata only).
    out_dir    : Output directory (created if it does not exist).
    sensitivity: list of (variant_name, combined_gated_net_usd) tuples from
                 run_sensitivity, or None if sensitivity was not run.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # Trade CSVs
    _write_trades_csv(gated.trades,   out_dir / f"trades_gated_{ts}.csv")
    _write_trades_csv(ungated.trades, out_dir / f"trades_ungated_{ts}.csv")

    # Regime timeline CSV (from gated run — ungated has the same regime rows)
    _write_regime_csv(gated.regime_rows, out_dir / f"regime_timeline_{ts}.csv")

    # Markdown summary
    md = _build_markdown(gated, ungated, cfg, sensitivity, ts)
    summary_path = out_dir / f"summary_{ts}.md"
    summary_path.write_text(md, encoding="utf-8")

    return summary_path


# ─────────────────────────────────────────────────────────────────────────────
# Sensitivity runner
# ─────────────────────────────────────────────────────────────────────────────

# Base session_vol windows (mirrors policies.SESSION_VOL_WINDOWS)
_BASE_SESSION_WINDOWS = [[6.75, 10.0], [13.25, 16.0]]

# ±30 min shift (0.5 h)
_SESSION_WINDOWS_MINUS_30 = [
    [w[0] - 0.5, w[1] - 0.5] for w in _BASE_SESSION_WINDOWS
]  # [[6.25, 9.5], [12.75, 15.5]]

_SESSION_WINDOWS_PLUS_30 = [
    [w[0] + 0.5, w[1] + 0.5] for w in _BASE_SESSION_WINDOWS
]  # [[7.25, 10.5], [13.75, 16.5]]


def _shifted_specs(windows: list[list[float]]) -> list[StratSpec]:
    """Return STRAT_SPECS with session_vol strategies using the given windows.

    Merges into each session_vol spec's existing policy_params so no other
    key (e.g. vol_regimes) is discarded.
    """
    return [
        replace(s, policy_params={**s.policy_params, "windows": windows})
        if s.policy_key == "session_vol"
        else s
        for s in STRAT_SPECS
    ]


def _sensitivity_stats(trades: list[TradeRecord]) -> dict:
    n = len(trades)
    net = sum(t.pnl_usd for t in trades)
    wr  = 100.0 * sum(1 for t in trades if t.pnl_usd > 0) / n if n > 0 else 0.0
    return {"combined_net_usd": net, "n_trades": n, "win_rate": wr}


def run_sensitivity(
    frames: dict,
    base_cfg: SimConfig,
) -> list[dict]:
    """Run 6 sensitivity variants, each a full gated re-run.

    Threshold variants (4)
    ~~~~~~~~~~~~~~~~~~~~~~
    Temporarily mutate ``strategy_manager.regime.regime_engine`` module
    constants with setattr, restore originals in a ``finally`` block.

    Window variants (2)
    ~~~~~~~~~~~~~~~~~~~
    Pass a modified spec list to run_sim (leaves regime_engine constants
    untouched).  Full re-run is used for correctness — window changes alter
    which entries are admitted, which in turn affects kill-switch and
    max-concurrent state, making a pure policy-replay non-trivial.
    (6 full re-runs ≈ a multi-hour overnight job at production depths;
    acceptable given the correctness trade-off.)

    Returns
    -------
    list[dict] with keys: variant, combined_net_usd, n_trades, win_rate.
    """
    import strategy_manager.regime.regime_engine as rem

    # Always gated for sensitivity
    gated_cfg = SimConfig(
        start=base_cfg.start,
        end=base_cfg.end,
        spread_pts=base_cfg.spread_pts,
        slippage_pts=base_cfg.slippage_pts,
        lots=base_cfg.lots,
        kill_switch_usd=base_cfg.kill_switch_usd,
        max_concurrent=base_cfg.max_concurrent,
        regime_cadence_min=base_cfg.regime_cadence_min,
        slice_rows=base_cfg.slice_rows,
        gated=True,
    )

    results: list[dict] = []

    # ── Threshold variants ────────────────────────────────────────────────────
    threshold_variants: list[tuple[str, dict]] = [
        ("vol(20/70/90)", {
            "VOL_PCTL_LOW": 20.0,
            "VOL_PCTL_HIGH": 70.0,
            "VOL_PCTL_EXTREME": 90.0,
        }),
        ("vol(30/80/97)", {
            "VOL_PCTL_LOW": 30.0,
            "VOL_PCTL_HIGH": 80.0,
            "VOL_PCTL_EXTREME": 97.0,
        }),
        ("er(0.30/0.15)", {
            "ER_TRENDING": 0.30,
            "ER_RANGING": 0.15,
        }),
        ("er(0.40/0.25)", {
            "ER_TRENDING": 0.40,
            "ER_RANGING": 0.25,
        }),
    ]

    for variant_name, attrs in threshold_variants:
        originals = {k: getattr(rem, k) for k in attrs}
        try:
            for k, v in attrs.items():
                setattr(rem, k, v)
            res = run_sim(frames, gated_cfg)
        finally:
            for k, v in originals.items():
                setattr(rem, k, v)
        row = {"variant": variant_name}
        row.update(_sensitivity_stats(res.trades))
        results.append(row)

    # ── Window variants ───────────────────────────────────────────────────────
    for variant_name, windows in [
        ("windows -30min", _SESSION_WINDOWS_MINUS_30),
        ("windows +30min", _SESSION_WINDOWS_PLUS_30),
    ]:
        specs = _shifted_specs(windows)
        res = run_sim(frames, gated_cfg, specs=specs)
        row = {"variant": variant_name}
        row.update(_sensitivity_stats(res.trades))
        results.append(row)

    return results
