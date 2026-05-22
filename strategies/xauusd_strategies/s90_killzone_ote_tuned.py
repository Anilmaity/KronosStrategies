"""
s90_killzone_ote_tuned.py
--------------------------
Task 7 — Confluence Tuning for the Killzone OTE setup.

Goal: find the confluence stack from strategies.research.confluence.SEARCHABLE
that lifts in-sample winrate toward >= 90% (Wilson 95% LB >= 85%) while
keeping expectancy >= +0.4R, maximising trades/day.

PUBLIC API
----------
tuned_signals(bars, htf_bars, *, stack=CHOSEN_STACK) -> list[Signal]
    Generate base signals then filter to only those where every confluence in
    ``stack`` evaluates to True at the decision bar.

_apply_stack_filter(signals, bars, htf_bars, *, stack) -> list[Signal]
    Lower-level helper used by tuned_signals and tests.

wilson_lb(w, n, z=1.96) -> float
    Wilson 95% confidence lower bound for binomial proportion.

CHOSEN_STACK
    The stack selected by the IS search (set at module load time).

Run as __main__ to execute the full IS search and write results JSON.

NO LOOK-AHEAD
-------------
Confluence is evaluated at the DECISION bar = signal.entry_index - 1.
(The base generates entry_index = k+1; decision bar is k.)
ConfluenceContext is built with idx = signal.entry_index - 1.
Only bars[0..idx] and closed HTF bars are read inside each detector.
"""
from __future__ import annotations

import itertools
import json
import math
import os
from typing import Sequence

import pandas as pd

from strategies.research.confluence import (
    SEARCHABLE,
    ConfluenceContext,
    DETECTORS,
)
from strategies.research.exec_sim import Signal, simulate
from strategies.xauusd_strategies.s90_killzone_ote import (
    generate_signals,
    is_killzone,
    HALF_SPREAD,
    SLIP,
    MAX_HOLD_BARS,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Killzone windows for ConfluenceContext (fractional UTC hours, half-open)
_KZ_WINDOWS = ((8.0, 10.0), (13.5, 15.5))

# The chosen confluence stack — set by the IS search results.
#
# IS search result summary (2026-05-22):
#   Base: 52 signals, 46.2% WR, +0.442R expectancy
#   Best n>=30 stack: ["liquidity_sweep"] — n=42, WR=47.6%, E=0.555, tpd=0.147
#   Best WR overall:  ["htf_bias"] — n=26, WR=57.7%, E=0.706 (n<30, flagged below)
#   No stack meets the 90%/LB-85%/+0.4R gate at n>=30.
#
# CHOSEN: ["liquidity_sweep"] — best winrate among n>=30, exp>=0.40 stacks.
# NOTE: session_killzone is redundant here (all base signals already come from
#       killzone bars — it's baked into generate_signals). London-only filtering
#       (hours 8-10, 95.2% WR on 21 trades) is NOT reachable via SEARCHABLE.
CHOSEN_STACK: list[str] = ["liquidity_sweep"]


# ---------------------------------------------------------------------------
# Wilson 95% lower bound
# ---------------------------------------------------------------------------

def wilson_lb(w: int, n: int, z: float = 1.96) -> float:
    """Wilson 95% confidence interval lower bound for binomial proportion.

    Parameters
    ----------
    w : int   — number of wins
    n : int   — total number of trials
    z : float — z-score (default 1.96 for 95% CI)

    Returns 0.0 when n == 0.
    """
    if n == 0:
        return 0.0
    p = w / n
    z2 = z * z
    center = (p + z2 / (2 * n)) / (1 + z2 / n)
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / (1 + z2 / n)
    return max(0.0, center - half)


# ---------------------------------------------------------------------------
# Core filtering helper
# ---------------------------------------------------------------------------

def _apply_stack_filter(
    signals: list[Signal],
    bars: pd.DataFrame,
    htf_bars: pd.DataFrame | None,
    *,
    stack: list[str],
) -> list[Signal]:
    """Filter ``signals`` to those where every confluence in ``stack`` is True.

    Parameters
    ----------
    signals  : base signals (from generate_signals)
    bars     : 5M entry-timeframe bars (same object passed to generate_signals)
    htf_bars : 1H bars (same object passed to generate_signals)
    stack    : list of confluence names from SEARCHABLE

    Returns
    -------
    list[Signal] — subset where all stack confluences evaluate to True at the
    decision bar (signal.entry_index - 1).

    NO LOOK-AHEAD: idx = signal.entry_index - 1 (the decision bar, not the
    entry bar). ConfluenceContext only reads bars[0..idx].
    """
    if not stack:
        return list(signals)

    kept: list[Signal] = []
    for sig in signals:
        decision_idx = sig.entry_index - 1
        if decision_idx < 0 or decision_idx >= len(bars):
            continue  # degenerate — skip

        ctx = ConfluenceContext(
            bars=bars,
            idx=decision_idx,
            direction=sig.direction,
            htf_bars=htf_bars,
            killzone_windows=_KZ_WINDOWS,
        )
        if all(DETECTORS[name](ctx) for name in stack):
            kept.append(sig)

    return kept


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def tuned_signals(
    bars: pd.DataFrame,
    htf_bars: pd.DataFrame,
    *,
    stack: list[str] | None = None,
) -> list[Signal]:
    """Generate base Killzone OTE signals filtered by the chosen confluence stack.

    Parameters
    ----------
    bars     : 5M bars (tz-naive UTC, mid-price OHLCV)
    htf_bars : 1H bars (tz-naive UTC, mid-price OHLCV)
    stack    : confluence names to require (default: CHOSEN_STACK)

    Returns
    -------
    list[Signal] — a subset of generate_signals(bars, htf_bars) where every
    confluence in ``stack`` is True at the decision bar.
    """
    if stack is None:
        stack = CHOSEN_STACK

    base = generate_signals(bars, htf_bars)
    return _apply_stack_filter(base, bars, htf_bars, stack=stack)


# ---------------------------------------------------------------------------
# Metrics helpers
# ---------------------------------------------------------------------------

def _session_dates(bars_5m: pd.DataFrame) -> int:
    """Count unique calendar days that have at least one killzone bar."""
    dates = set()
    for _, row in bars_5m.iterrows():
        ts = pd.Timestamp(row["time"])
        if is_killzone(ts):
            dates.add(ts.date())
    return len(dates)


def _compute_metrics(results, bars_5m: pd.DataFrame) -> dict:
    """Compute full metrics dict from a list of TradeResult."""
    n = len(results)
    if n == 0:
        return {
            "n_trades": 0,
            "winrate": 0.0,
            "wilson_lb": 0.0,
            "expectancy_R": 0.0,
            "avg_win_R": 0.0,
            "avg_loss_R": 0.0,
            "profit_factor": 0.0,
            "max_consec_losers": 0,
            "max_dd_R": 0.0,
            "trades_per_day": 0.0,
            "by_hour": {},
        }

    rs = [t.r_multiple for t in results]
    wins   = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]

    w = len(wins)
    winrate    = w / n
    lb         = wilson_lb(w, n)
    expectancy = sum(rs) / n
    avg_win    = sum(wins)   / len(wins)   if wins   else 0.0
    avg_loss   = sum(losses) / len(losses) if losses else 0.0

    gross_win  = sum(wins)
    gross_loss = abs(sum(losses))
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")

    # Max consecutive losers
    max_consec = 0
    cur_consec = 0
    for r in rs:
        if r <= 0:
            cur_consec += 1
            max_consec = max(max_consec, cur_consec)
        else:
            cur_consec = 0

    # Max drawdown in R (peak-to-trough of cumulative R)
    cum_r = 0.0
    peak  = 0.0
    max_dd = 0.0
    for r in rs:
        cum_r += r
        if cum_r > peak:
            peak = cum_r
        dd = peak - cum_r
        if dd > max_dd:
            max_dd = dd

    # Trades per day
    n_days = _session_dates(bars_5m)
    tpd = n / n_days if n_days > 0 else 0.0

    # By-hour breakdown
    by_hour: dict[str, dict] = {}
    for t in results:
        h = str(pd.Timestamp(t.entry_time).hour)
        if h not in by_hour:
            by_hour[h] = {"trades": 0, "wins": 0}
        by_hour[h]["trades"] += 1
        if t.r_multiple > 0:
            by_hour[h]["wins"] += 1
    by_hour_out = {
        h: {"trades": v["trades"], "winrate": round(v["wins"] / v["trades"], 4)}
        for h, v in sorted(by_hour.items(), key=lambda kv: int(kv[0]))
    }

    return {
        "n_trades":         n,
        "winrate":          round(winrate, 4),
        "wilson_lb":        round(lb, 4),
        "expectancy_R":     round(expectancy, 4),
        "avg_win_R":        round(avg_win, 4),
        "avg_loss_R":       round(avg_loss, 4),
        "profit_factor":    round(pf, 4),
        "max_consec_losers": max_consec,
        "max_dd_R":         round(max_dd, 4),
        "trades_per_day":   round(tpd, 4),
        "by_hour":          by_hour_out,
    }


# ---------------------------------------------------------------------------
# IS Search
# ---------------------------------------------------------------------------

def run_is_search(
    bars_5m: pd.DataFrame,
    bars_1h: pd.DataFrame,
    *,
    verbose: bool = True,
) -> dict:
    """Run the full in-sample confluence stack search and return results dict.

    Algorithm
    ---------
    1. Generate all base signals once.
    2. For each base signal, evaluate all 6 SEARCHABLE confluences ONCE at
       its decision bar and cache the boolean vector.
    3. For k = 0, 1, 2, 3 enumerate every C(6,k) subset:
       - k=0: empty stack (baseline)
       - k=1..3: all combinations of k confluences
       AND-combine cached booleans (no detector re-calls) to get filtered signals.
    4. Run simulate() on each filtered signal set.
    5. Compute metrics and select the best stack.
    """
    if verbose:
        print("[tuned] Generating base signals...")
    base_signals = generate_signals(bars_5m, bars_1h)
    base_n = len(base_signals)
    if verbose:
        print(f"[tuned] Base signals: {base_n}")

    # --- Cache confluence booleans for every base signal ---
    if verbose:
        print("[tuned] Caching confluence booleans...")
    cached: list[dict[str, bool]] = []
    for sig in base_signals:
        decision_idx = sig.entry_index - 1
        ctx = ConfluenceContext(
            bars=bars_5m,
            idx=decision_idx,
            direction=sig.direction,
            htf_bars=bars_1h,
            killzone_windows=_KZ_WINDOWS,
        )
        row: dict[str, bool] = {}
        for name in SEARCHABLE:
            try:
                row[name] = bool(DETECTORS[name](ctx))
            except Exception:
                row[name] = False
        cached.append(row)

    if verbose:
        print("[tuned] Confluence cache built. Running stack search...")

    n_days = _session_dates(bars_5m)

    # --- Enumerate stacks: k = 0 (empty) through k = 3 ---
    all_candidates: list[dict] = []

    for k in range(4):  # k = 0, 1, 2, 3
        for stack in itertools.combinations(SEARCHABLE, k):
            stack_list = list(stack)

            # Filter signals using cached booleans (fast AND-combine)
            if k == 0:
                filtered_indices = list(range(base_n))
            else:
                filtered_indices = [
                    i for i, row in enumerate(cached)
                    if all(row[name] for name in stack_list)
                ]

            n_filtered = len(filtered_indices)
            if n_filtered == 0:
                all_candidates.append({
                    "stack":       stack_list,
                    "n_trades":    0,
                    "winrate":     0.0,
                    "wilson_lb":   0.0,
                    "expectancy_R": 0.0,
                    "trades_per_day": 0.0,
                    "_meets_gate": False,
                })
                continue

            filtered_signals = [base_signals[i] for i in filtered_indices]
            results = simulate(
                bars_5m, filtered_signals,
                half_spread=HALF_SPREAD, slip=SLIP, max_hold_bars=MAX_HOLD_BARS,
            )

            rs = [t.r_multiple for t in results]
            wins = [r for r in rs if r > 0]
            n_r = len(rs)
            w_r = len(wins)
            wr  = w_r / n_r if n_r > 0 else 0.0
            lb  = wilson_lb(w_r, n_r)
            exp = sum(rs) / n_r if n_r > 0 else 0.0
            tpd = n_r / n_days if n_days > 0 else 0.0

            meets_gate = (
                wr   >= 0.90
                and lb  >= 0.85
                and exp >= 0.40
                and n_r >= 30
            )

            all_candidates.append({
                "stack":          stack_list,
                "n_trades":       n_r,
                "winrate":        round(wr, 4),
                "wilson_lb":      round(lb, 4),
                "expectancy_R":   round(exp, 4),
                "trades_per_day": round(tpd, 4),
                "_meets_gate":    meets_gate,
            })

    if verbose:
        print(f"[tuned] Evaluated {len(all_candidates)} candidate stacks.")

    # --- Select best stack ---
    # Priority 1: meets all gates → pick highest trades_per_day
    gate_passers = [c for c in all_candidates if c["_meets_gate"]]
    if gate_passers:
        best = max(gate_passers, key=lambda c: c["trades_per_day"])
        gate_met = True
    else:
        # Priority 2: best winrate with exp >= 0.40 and n >= 30
        candidates_30 = [
            c for c in all_candidates
            if c["n_trades"] >= 30 and c["expectancy_R"] >= 0.40
        ]
        if candidates_30:
            best = max(candidates_30, key=lambda c: c["winrate"])
        else:
            # Fallback: best expectancy among n >= 5
            candidates_5 = [c for c in all_candidates if c["n_trades"] >= 5]
            if candidates_5:
                best = max(candidates_5, key=lambda c: c["expectancy_R"])
            else:
                # Last resort: empty stack (baseline)
                best = next(c for c in all_candidates if c["stack"] == [])
        gate_met = False

    # --- Full metrics for chosen stack ---
    chosen_stack = best["stack"]
    if chosen_stack:
        chosen_indices = [
            i for i, row in enumerate(cached)
            if all(row[name] for name in chosen_stack)
        ]
    else:
        chosen_indices = list(range(base_n))

    chosen_signals = [base_signals[i] for i in chosen_indices]
    chosen_results = simulate(
        bars_5m, chosen_signals,
        half_spread=HALF_SPREAD, slip=SLIP, max_hold_bars=MAX_HOLD_BARS,
    )
    chosen_metrics = _compute_metrics(chosen_results, bars_5m)

    # --- Top 10 candidates by winrate (for frontier table) ---
    # Sort by winrate desc, then wilson_lb desc, then n_trades desc
    sorted_candidates = sorted(
        all_candidates,
        key=lambda c: (c["winrate"], c["wilson_lb"], c["n_trades"]),
        reverse=True,
    )
    top_candidates = []
    for c in sorted_candidates[:20]:
        if c["n_trades"] == 0:
            continue
        top_candidates.append({
            "stack":          c["stack"],
            "n_trades":       c["n_trades"],
            "winrate":        c["winrate"],
            "wilson_lb":      c["wilson_lb"],
            "expectancy_R":   c["expectancy_R"],
            "trades_per_day": c["trades_per_day"],
            "meets_gate":     c["_meets_gate"],
        })
        if len(top_candidates) >= 10:
            break

    return {
        "base_n":          base_n,
        "gate_met":        gate_met,
        "chosen_stack":    chosen_stack,
        "params": {
            "tf":              "5m",
            "htf_tf":          "1h",
            "half_spread":     HALF_SPREAD,
            "slip":            SLIP,
            "session_windows": {"london": "08:00-10:00 UTC", "ny": "13:30-15:30 UTC"},
            "ote_zone":        "62%-79% Fibonacci retracement",
            "max_hold_bars":   MAX_HOLD_BARS,
        },
        **chosen_metrics,
        "top_candidates":  top_candidates,
    }


# ---------------------------------------------------------------------------
# Results path
# ---------------------------------------------------------------------------

RESULTS_PATH = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "backtest", "results", "s90_killzone_ote_tuned_20260522.json"
))


# ---------------------------------------------------------------------------
# __main__
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from strategies.research.dataset import load_is_bars

    print("[s90_killzone_ote_tuned] Loading IS bars...")
    bars_5m = load_is_bars("5m")
    bars_1h = load_is_bars("1h")
    print(f"[s90_killzone_ote_tuned] 5M: {len(bars_5m):,} bars | 1H: {len(bars_1h):,} bars")

    output = run_is_search(bars_5m, bars_1h, verbose=True)

    # Update module-level CHOSEN_STACK so subsequent imports use the IS result
    CHOSEN_STACK[:] = output["chosen_stack"]

    os.makedirs(os.path.dirname(RESULTS_PATH), exist_ok=True)
    with open(RESULTS_PATH, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n[s90_killzone_ote_tuned] Results -> {RESULTS_PATH}")
    print(f"  gate_met       : {output['gate_met']}")
    print(f"  chosen_stack   : {output['chosen_stack']}")
    print(f"  n_trades       : {output['n_trades']}")
    print(f"  winrate        : {output['winrate']:.1%}")
    print(f"  wilson_lb      : {output['wilson_lb']:.1%}")
    print(f"  expectancy_R   : {output['expectancy_R']:.3f}")
    print(f"  trades_per_day : {output['trades_per_day']:.4f}")

    print("\n--- Top Candidates (IS frontier) ---")
    for c in output["top_candidates"]:
        flag = "GATE" if c["meets_gate"] else "    "
        print(
            f"  {flag} {str(c['stack']):40s}  "
            f"n={c['n_trades']:3d}  WR={c['winrate']:.1%}  "
            f"LB={c['wilson_lb']:.1%}  E={c['expectancy_R']:.3f}  "
            f"tpd={c['trades_per_day']:.4f}"
        )
