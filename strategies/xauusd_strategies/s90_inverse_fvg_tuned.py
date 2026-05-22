"""
s90_inverse_fvg_tuned.py
------------------------
Task 7: Confluence tuning for Family G -- Inverse-FVG Continuation (XAU/USD).

Base strategy: s90_inverse_fvg.py  (1756 IS signals, ~43% WR, ~6/day)
Goal: filter by confluence stack to lift winrate toward >=90% with >=+0.4R expectancy.

Method
------
1. Load IS 5m + 1h bars and run base detect_signals().
2. For each base signal, compute ALL 6 SEARCHABLE confluence booleans ONCE at the
   decision bar (entry_index - 1). Cache results in a list of dicts.
3. Exhaustive search over every "require-all-of-k" subset of SEARCHABLE for k=1,2,3.
   AND-combine cached booleans per stack (no recomputation per stack).
4. Wilson 95% LB computed for every stack with n>=10. Select best per contract rules.
5. Write JSON results to strategies/backtest/results/s90_inverse_fvg_tuned_20260522.json.

No-look-ahead guarantee
-----------------------
Confluence context is built at idx = signal.entry_index - 1 (the decision/retest bar),
which is the last closed bar before the entry bar's open. ONLY bars[0..idx] are read
by all detectors. HTF bars are filtered to time < bars[idx].time.
"""
from __future__ import annotations

import itertools
import json
import math
import os
from typing import Optional

import numpy as np
import pandas as pd

from strategies.research.exec_sim import Signal, simulate
from strategies.research.confluence import (
    SEARCHABLE,
    ConfluenceContext,
    DETECTORS,
)
from strategies.xauusd_strategies.s90_inverse_fvg import detect_signals

# ---------------------------------------------------------------------------
# Default chosen stack (filled in by run_backtest after search)
# Override via required_confluences kwarg in tuned_signals().
# ---------------------------------------------------------------------------
_DEFAULT_STACK: list[str] = []  # will be set by run_backtest(); empty = no filter

PARAMS = {
    "tf": "5m",
    "half_spread": 0.15,
    "slip": 0.05,
    "max_hold_bars": 48,
    "killzone_windows": ((7.0, 11.0), (12.0, 16.0)),
}


# ---------------------------------------------------------------------------
# Public API: tuned_signals()
# ---------------------------------------------------------------------------

def tuned_signals(
    bars: pd.DataFrame,
    htf_bars: Optional[pd.DataFrame],
    *,
    required_confluences: Optional[list[str]] = None,
) -> list[Signal]:
    """Return base inverse-FVG signals filtered by a confluence stack.

    Parameters
    ----------
    bars : pd.DataFrame
        5-minute entry-timeframe bars (tz-naive UTC).
    htf_bars : pd.DataFrame or None
        1-hour bars for htf_bias confluence. May be None (htf_bias returns False).
    required_confluences : list[str] or None
        Names from SEARCHABLE to require ALL of at the decision bar.
        If None, uses the module-level _DEFAULT_STACK (set by run_backtest).
        Pass [] to disable all filtering (returns all base signals).

    Returns
    -------
    list[Signal]
        Subset of detect_signals(bars, htf_bars) that pass all required confluences.

    No-look-ahead guarantee
    -----------------------
    Decision bar = signal.entry_index - 1. Only bars[0..decision_bar] are read.
    HTF bars visible: those with time < bars.iloc[decision_bar].time.
    """
    if required_confluences is None:
        stack = _DEFAULT_STACK
    else:
        stack = required_confluences

    base = detect_signals(bars, htf_bars)

    if not stack:
        return base

    filtered: list[Signal] = []
    kw = PARAMS["killzone_windows"]

    for sig in base:
        decision_idx = sig.entry_index - 1
        if decision_idx < 0 or decision_idx >= len(bars):
            continue

        ctx = ConfluenceContext(
            bars=bars,
            idx=decision_idx,
            direction=sig.direction,
            htf_bars=htf_bars,
            killzone_windows=kw,
        )

        if all(DETECTORS[name](ctx) for name in stack):
            filtered.append(sig)

    return filtered


# ---------------------------------------------------------------------------
# Wilson 95% confidence interval lower bound
# ---------------------------------------------------------------------------

def _wilson_lb(wins: int, n: int, z: float = 1.96) -> float:
    """Wilson score confidence interval lower bound (p_hat = wins/n)."""
    if n == 0:
        return 0.0
    p = wins / n
    center = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, center - half)


# ---------------------------------------------------------------------------
# Confluence cache builder
# ---------------------------------------------------------------------------

def _build_confluence_cache(
    bars: pd.DataFrame,
    htf_bars: Optional[pd.DataFrame],
    signals: list[Signal],
) -> list[dict[str, bool]]:
    """Compute all 6 SEARCHABLE confluences ONCE per signal at its decision bar.

    Returns a parallel list: cache[i] is the dict of bool results for signals[i].
    """
    kw = PARAMS["killzone_windows"]
    cache: list[dict[str, bool]] = []

    for sig in signals:
        decision_idx = sig.entry_index - 1
        if decision_idx < 0 or decision_idx >= len(bars):
            cache.append({name: False for name in SEARCHABLE})
            continue

        ctx = ConfluenceContext(
            bars=bars,
            idx=decision_idx,
            direction=sig.direction,
            htf_bars=htf_bars,
            killzone_windows=kw,
        )
        row = {}
        for name in SEARCHABLE:
            try:
                row[name] = bool(DETECTORS[name](ctx))
            except Exception:
                row[name] = False
        cache.append(row)

    return cache


# ---------------------------------------------------------------------------
# Metrics helpers
# ---------------------------------------------------------------------------

def _compute_metrics_from_results(
    results,
    bars: pd.DataFrame,
    stack: list[str],
    trading_days: int,
) -> dict:
    """Compute full metrics dict from a list of TradeResult objects."""
    rs = [r.r_multiple for r in results]
    n = len(rs)
    if n == 0:
        return _empty_stack_metrics(stack)

    wins_r = [r for r in rs if r > 0]
    losses_r = [r for r in rs if r <= 0]
    winrate = len(wins_r) / n
    wilson_lb = _wilson_lb(len(wins_r), n)
    expectancy = sum(rs) / n
    avg_win = sum(wins_r) / len(wins_r) if wins_r else 0.0
    avg_loss = sum(losses_r) / len(losses_r) if losses_r else 0.0
    gross_profit = sum(wins_r)
    gross_loss = abs(sum(losses_r))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Max drawdown in R
    cumr = np.cumsum(rs)
    peak = np.maximum.accumulate(cumr)
    dd = peak - cumr
    max_dd = float(dd.max()) if len(dd) > 0 else 0.0

    # Max consecutive losers
    max_consec = 0
    cur_consec = 0
    for r in rs:
        if r <= 0:
            cur_consec += 1
            max_consec = max(max_consec, cur_consec)
        else:
            cur_consec = 0

    trades_per_day = n / trading_days if trading_days > 0 else 0.0

    # By-hour
    by_hour: dict[str, dict] = {}
    for r in results:
        h = str(r.entry_time.hour)
        if h not in by_hour:
            by_hour[h] = {"trades": 0, "_wins": 0}
        by_hour[h]["trades"] += 1
        if r.r_multiple > 0:
            by_hour[h]["_wins"] += 1
    for h, hd in by_hour.items():
        hd["winrate"] = round(hd["_wins"] / hd["trades"], 4)
        del hd["_wins"]

    return {
        "stack": stack,
        "n_trades": n,
        "winrate": round(winrate, 4),
        "wilson_lb": round(wilson_lb, 4),
        "expectancy_R": round(expectancy, 4),
        "avg_win_R": round(avg_win, 4),
        "avg_loss_R": round(avg_loss, 4),
        "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else None,
        "max_consec_losers": max_consec,
        "max_dd_R": round(max_dd, 4),
        "trades_per_day": round(trades_per_day, 4),
        "by_hour": by_hour,
    }


def _empty_stack_metrics(stack: list[str]) -> dict:
    return {
        "stack": stack,
        "n_trades": 0,
        "winrate": 0.0,
        "wilson_lb": 0.0,
        "expectancy_R": 0.0,
        "avg_win_R": 0.0,
        "avg_loss_R": 0.0,
        "profit_factor": None,
        "max_consec_losers": 0,
        "max_dd_R": 0.0,
        "trades_per_day": 0.0,
        "by_hour": {},
    }


def _count_trading_days(bars: pd.DataFrame) -> int:
    dates = bars["time"].dt.date
    unique_dates = dates.unique()
    weekday_count = sum(1 for d in unique_dates if pd.Timestamp(d).weekday() < 5)
    return max(weekday_count, 1)


# ---------------------------------------------------------------------------
# Exhaustive stack search
# ---------------------------------------------------------------------------

def _search_stacks(
    signals: list[Signal],
    conf_cache: list[dict[str, bool]],
    sim_results,        # list[TradeResult] for all base signals
    trading_days: int,
    min_n: int = 50,
) -> list[dict]:
    """Search all subsets of SEARCHABLE of size k=1,2,3.

    For each subset, AND-combine cached booleans to filter signals,
    then compute metrics from pre-computed sim_results (no re-simulation).

    Returns list of metric dicts sorted by winrate desc.
    """
    all_stacks: list[dict] = []

    for k in range(1, 4):
        for combo in itertools.combinations(SEARCHABLE, k):
            stack = list(combo)

            # Filter signals by this stack using cached booleans
            kept_indices = [
                i for i, row in enumerate(conf_cache)
                if all(row[name] for name in stack)
            ]

            n = len(kept_indices)
            if n < min_n:
                continue

            # Gather pre-computed results for kept signals
            kept_results = [sim_results[i] for i in kept_indices]

            metrics = _compute_metrics_from_results(
                kept_results, None, stack, trading_days
            )
            all_stacks.append(metrics)

    # Also include the base (no filter) for reference
    base_metrics = _compute_metrics_from_results(
        sim_results, None, [], trading_days
    )
    all_stacks.insert(0, base_metrics)

    return all_stacks


# ---------------------------------------------------------------------------
# Selection logic
# ---------------------------------------------------------------------------

def _select_best(all_stacks: list[dict]) -> dict:
    """Select best stack per contract:
    1. Among stacks meeting WR>=0.90, Wilson_LB>=0.85, expectancy>=0.40, n>=50:
       pick highest trades_per_day.
    2. If none: pick highest winrate with expectancy>=0.40, n>=50.
    """
    qualifying = [
        s for s in all_stacks
        if s["n_trades"] >= 50
        and s["winrate"] >= 0.90
        and s["wilson_lb"] >= 0.85
        and s["expectancy_R"] >= 0.40
    ]
    if qualifying:
        return max(qualifying, key=lambda s: s["trades_per_day"])

    # Fallback: highest winrate with expectancy >= 0.40 and n >= 50
    fallback = [
        s for s in all_stacks
        if s["n_trades"] >= 50 and s["expectancy_R"] >= 0.40
    ]
    if fallback:
        return max(fallback, key=lambda s: s["winrate"])

    # Final fallback: highest winrate, any n >= 10
    candidates = [s for s in all_stacks if s["n_trades"] >= 10]
    if candidates:
        return max(candidates, key=lambda s: s["winrate"])

    return all_stacks[0]  # base


def _build_frontier(all_stacks: list[dict], tpd_thresholds: list[float]) -> list[dict]:
    """For each trades_per_day threshold, find best-winrate stack achieving >= that freq."""
    frontier = []
    for tpd in tpd_thresholds:
        candidates = [
            s for s in all_stacks
            if s["trades_per_day"] >= tpd and s["n_trades"] >= 10
        ]
        if candidates:
            best = max(candidates, key=lambda s: s["winrate"])
            frontier.append({
                "min_trades_per_day": tpd,
                "achieved_trades_per_day": best["trades_per_day"],
                "winrate": best["winrate"],
                "wilson_lb": best["wilson_lb"],
                "expectancy_R": best["expectancy_R"],
                "n_trades": best["n_trades"],
                "stack": best["stack"],
            })
        else:
            frontier.append({
                "min_trades_per_day": tpd,
                "achieved_trades_per_day": None,
                "winrate": None,
                "wilson_lb": None,
                "expectancy_R": None,
                "n_trades": None,
                "stack": None,
            })
    return frontier


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_backtest() -> dict:
    """Full IS backtest with confluence search. Writes JSON results."""
    from strategies.research.dataset import load_is_bars

    print("[s90_inverse_fvg_tuned] Loading IS bars...")
    bars = load_is_bars("5m")
    bars_1h = load_is_bars("1h")

    print("[s90_inverse_fvg_tuned] Detecting base signals...")
    signals = detect_signals(bars, bars_1h)
    base_n = len(signals)
    print(f"[s90_inverse_fvg_tuned] Base signals: {base_n}")

    print("[s90_inverse_fvg_tuned] Simulating all base signals...")
    sim_results = simulate(
        bars,
        signals,
        half_spread=PARAMS["half_spread"],
        slip=PARAMS["slip"],
        max_hold_bars=PARAMS["max_hold_bars"],
    )

    trading_days = _count_trading_days(bars)

    print("[s90_inverse_fvg_tuned] Building confluence cache...")
    conf_cache = _build_confluence_cache(bars, bars_1h, signals)

    print("[s90_inverse_fvg_tuned] Searching confluence stacks (k=1,2,3)...")
    all_stacks = _search_stacks(signals, conf_cache, sim_results, trading_days)
    print(f"[s90_inverse_fvg_tuned] Evaluated {len(all_stacks)} stacks (n>=50)")

    # Sort by winrate desc for top_candidates
    sorted_stacks = sorted(all_stacks, key=lambda s: s["winrate"], reverse=True)
    top_candidates = sorted_stacks[:10]

    # Select best stack
    chosen = _select_best(all_stacks)
    print(f"[s90_inverse_fvg_tuned] Chosen stack: {chosen['stack']}")
    print(f"  WR={chosen['winrate']:.3f}, WilsonLB={chosen['wilson_lb']:.3f}, "
          f"E[R]={chosen['expectancy_R']:.3f}, n={chosen['n_trades']}, "
          f"tpd={chosen['trades_per_day']:.2f}")

    # Build frequency frontier
    frontier = _build_frontier(all_stacks, tpd_thresholds=[1.0, 2.0, 3.0, 4.0, 5.0])

    # Determine if goals were met
    goals_met = {
        "winrate_90pct": chosen["winrate"] >= 0.90,
        "wilson_lb_85pct": chosen["wilson_lb"] >= 0.85,
        "expectancy_04R": chosen["expectancy_R"] >= 0.40,
        "n_ge_50": chosen["n_trades"] >= 50,
        "all_goals_met": (
            chosen["winrate"] >= 0.90
            and chosen["wilson_lb"] >= 0.85
            and chosen["expectancy_R"] >= 0.40
            and chosen["n_trades"] >= 50
        ),
    }

    # Update module-level default stack
    global _DEFAULT_STACK
    _DEFAULT_STACK = chosen["stack"]

    output = {
        "family": "G_inverse_fvg_tuned",
        "base_n": base_n,
        "chosen_stack": chosen["stack"],
        "params": PARAMS,
        "chosen_stack_metrics": chosen,
        "goals_met": goals_met,
        "frontier": frontier,
        "top_candidates": top_candidates,
        "searchable_detectors": SEARCHABLE,
        "notes": (
            "Exhaustive k=1,2,3 subset search over 6 SEARCHABLE detectors. "
            "Decision bar = entry_index-1 (last closed bar before entry). "
            "No look-ahead. DXY excluded (no data). IS only."
        ),
    }

    _write_json(output)
    return output


def _write_json(output: dict) -> None:
    out_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "backtest", "results"
    )
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "s90_inverse_fvg_tuned_20260522.json")

    def _default(obj):
        if isinstance(obj, float) and math.isnan(obj):
            return None
        if isinstance(obj, float) and math.isinf(obj):
            return None
        return str(obj)

    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=_default)
    print(f"[s90_inverse_fvg_tuned] Wrote results to {out_path}")


if __name__ == "__main__":
    run_backtest()
