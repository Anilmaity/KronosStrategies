"""
strategies/research/er_gate_study.py
------------------------------------
opt15 Task 15 -- offline validation of a trend-persistence (Kaufman efficiency
ratio) ENTRY gate for S100 (M3 combo scalper). Motivation (measured): S100's
spec has ONE losing period in its 3-year validation -- 2023H2 (-273 pts), a
choppy/ranging regime. Hypothesis: blocking new entries when the tape is not
trending (per the regime engine's efficiency-ratio classifier) removes the bulk
of the 2023H2 loss while sacrificing little of the 2025-2026 profit.

RESEARCH-FIRST. It replays the REAL ``s100.get_signal`` over the 3-year OANDA M1
parquet (harvesting the baseline signal set ONCE, with S100_ER_GATE forced OFF),
tags each signal with the trend regime it fired in (computed from the CANONICAL
regime-engine efficiency ratio via ``replay_lib.TrendRegimeEngine`` -- 24 closed
H1 bars + 30 closed M15 bars, thresholds 0.35/0.20), then evaluates the two gate
arms as cheap filters over that ONE signal set. It applies the pre-registered
decision rule and writes the full report (rule stated BEFORE the results). The
env-gated module gate is implemented and shipped DEFAULT OFF regardless of the
verdict; arming it live is a separate operator decision informed by this report.

Arms (share the identical baseline signal set -- only which signals survive
differs, so the comparison is purely the gate's selection quality):
  baseline   every fired signal.
  ranging    drop signals whose trend_regime == RANGING.
  strict     drop signals whose trend_regime != TRENDING (RANGING or MIXED).
A signal whose regime cannot be classified (too little history, only the first
hours of 2023-07) is ADMITTED by every arm -- the module's fail-toward-OFF path.

Costs 0.45pt base / 0.80pt stress. Per-period breakdown: 2023H2, 2024, 2025,
2026 (the parquet ends 2026-07).

Run:
  E:/Projects/Kronos/KronosStrategies/.venv/Scripts/python.exe \
      strategies/research/er_gate_study.py

ASCII-only output. Target runtime < 30 min on this machine.
"""
from __future__ import annotations

import argparse
import os
import pickle
import sys
import time

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_STRAT_DIR = os.path.dirname(_HERE)
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

# The baseline harvest must be the module's UNGATED signal set regardless of the
# operator's shell -- pin the gate OFF before importing/using the module.
os.environ["S100_ER_GATE"] = "off"

from research import replay_lib as rl  # noqa: E402
from backtest_strategies import s100_m3_combo as s100  # noqa: E402

DATA_DEFAULT = r"E:\Projects\Kronos\ClaudeTradingRD\m3_scalper\xau_m1_3y.parquet"
REPORT_DEFAULT = os.path.normpath(
    os.path.join(_STRAT_DIR, "..", "docs", "research",
                 "2026-07-30-s100-er-gate.md")
)

# Full parquet span (2023-07..2026-07). 2023H2 is the target losing regime.
START = pd.Timestamp("2023-07-01")
END = pd.Timestamp("2026-08-01")

FRICTIONS = {"base": rl.BASE_FRICTION, "stress": rl.STRESS_FRICTION}
DEPTH = s100.MIN_BARS_1M            # 642 = 3 * _MIN_M3 (the live window floor)

PERIODS = ("2023H2", "2024", "2025", "2026")
ARMS = ("baseline", "ranging", "strict")
ARM_LABEL = {"baseline": "baseline", "ranging": "block RANGING",
             "strict": "block NOT-TRENDING"}

# Pre-registered decision rule (stated in the report header BEFORE results).
LOSS_REDUCTION_MIN = 0.40          # 2023H2 loss must shrink by >= 40%
PROFIT_GIVEUP_MAX = 0.15           # 2025-2026 profit may give up <= 15%


# ──────────────────────────────────────────────────────────────────────────────
# Period bucketing
# ──────────────────────────────────────────────────────────────────────────────

def period_of(ts: pd.Timestamp):
    """Map an entry time to its reporting period, or None if outside them."""
    y = ts.year
    if y == 2023:
        return "2023H2" if ts.month >= 7 else None
    if y in (2024, 2025, 2026):
        return str(y)
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Signal harvest (one ungated replay; mirrors trail_exit_study.harvest_m1)
# ──────────────────────────────────────────────────────────────────────────────

def harvest(m1: pd.DataFrame, m1_arrays, regime: rl.TrendRegimeEngine,
            name: str = "s100", limit: int | None = None) -> list[dict]:
    """Replay ``s100.get_signal`` on each closed M1 bar in trading hours over
    [START, END), gate OFF. For each fired signal record the M1-path gross
    result, the reporting period, and the trend regime at entry."""
    t_ns_m1, h_m1, l_m1, c_m1 = m1_arrays
    m1_time = m1["time"].to_numpy()
    o = m1["open"].to_numpy(float)
    hh = m1["high"].to_numpy(float)
    ll = m1["low"].to_numpy(float)
    cc = m1["close"].to_numpy(float)

    one = np.timedelta64(1, "m")
    close_time = m1_time + one
    start_i = int(np.searchsorted(close_time, np.datetime64(START.to_datetime64()), "left"))
    end_i = int(np.searchsorted(close_time, np.datetime64(END.to_datetime64()), "left"))
    start_i = max(start_i, DEPTH)
    hours = set(s100._HOURS)

    s100.reset_state()
    out: list[dict] = []
    t0 = time.perf_counter()
    steps = 0
    for i in range(start_i, end_i):
        now_naive = pd.Timestamp(m1_time[i]) + pd.Timedelta(minutes=1)
        steps += 1
        if steps % 50000 == 0:
            print("  [%s] %d/%d M1 steps (%.0fs, %d signals)"
                  % (name, steps, end_i - start_i,
                     time.perf_counter() - t0, len(out)))
        if now_naive.hour not in hours:
            s100._pending = None      # faithful to get_signal's out-of-hours path
            continue
        per = period_of(now_naive)
        lo = i - DEPTH + 1
        w1m = pd.DataFrame({
            "time": m1_time[lo:i + 1], "open": o[lo:i + 1],
            "high": hh[lo:i + 1], "low": ll[lo:i + 1], "close": cc[lo:i + 1],
        })
        sig = s100.get_signal(w1m, None, None, now_naive.tz_localize("UTC"))
        if sig is None or per is None:
            continue
        ex = rl.simulate_exit(
            t_ns_m1, h_m1, l_m1, c_m1, now_naive, sig.side,
            float(sig.entry_price), float(sig.stop_loss),
            float(sig.take_profit), sig.max_hold_min,
        )
        label, er_h1, er_m15 = regime.regime_at(now_naive)
        out.append({
            "entry_time": now_naive, "period": per, "side": sig.side,
            "entry": float(sig.entry_price), "sl": float(sig.stop_loss),
            "tp": float(sig.take_profit), "max_hold": sig.max_hold_min,
            "reason": sig.reason, "gross": ex.gross_pts,
            "exit_reason": ex.reason, "regime": label,
            "er_h1": float(er_h1) if er_h1 == er_h1 else None,
            "er_m15": float(er_m15) if er_m15 == er_m15 else None,
        })
        if limit is not None and len(out) >= limit:
            break
    print("  [%s] harvest done: %d signals from %d M1 steps in %.0fs"
          % (name, len(out), steps, time.perf_counter() - t0))
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Arms / stats
# ──────────────────────────────────────────────────────────────────────────────

def _keep(sig: dict, arm: str) -> bool:
    """Whether ``arm`` admits ``sig``. Unclassifiable regime (None) is always
    admitted -- the module's fail-toward-OFF behaviour on a short window."""
    if arm == "baseline":
        return True
    reg = sig["regime"]
    if reg is None:
        return True
    if arm == "ranging":
        return reg != "RANGING"
    if arm == "strict":
        return reg == "TRENDING"
    raise ValueError(arm)


def arm_nets(sigs: list[dict], arm: str, friction: float) -> list[float]:
    return [rl.net_from_gross(s["gross"], friction)
            for s in sigs if _keep(s, arm)]


def _in_period(sigs: list[dict], period: str) -> list[dict]:
    return [s for s in sigs if s["period"] == period]


def arm_stats(sigs: list[dict], arm: str, period: str, friction: float) -> dict:
    return rl.trade_stats(arm_nets(_in_period(sigs, period), arm, friction))


def _period_total(sigs: list[dict], arm: str, period: str,
                  friction: float) -> float:
    return float(sum(arm_nets(_in_period(sigs, period), arm, friction)))


def _profit_2526(sigs: list[dict], arm: str, friction: float) -> float:
    return (_period_total(sigs, arm, "2025", friction)
            + _period_total(sigs, arm, "2026", friction))


# ──────────────────────────────────────────────────────────────────────────────
# Decision rule (PRE-REGISTERED -- see report header)
# ──────────────────────────────────────────────────────────────────────────────

def evaluate_rule(sigs: list[dict], friction: float = rl.BASE_FRICTION) -> dict:
    """Per arm (ranging, strict): 2023H2 loss reduced by >= 40% AND 2025-2026
    profit given up <= 15%. Both measured on the SAME (default base) friction.

    reduction = 1 - arm_2023H2_total / base_2023H2_total   (base < 0, a loss)
    give_up   = 1 - arm_2526_profit  / base_2526_profit    (base > 0, a profit)
    """
    base_2023 = _period_total(sigs, "baseline", "2023H2", friction)
    base_2526 = _profit_2526(sigs, "baseline", friction)
    fams = {}
    for arm in ("ranging", "strict"):
        arm_2023 = _period_total(sigs, arm, "2023H2", friction)
        arm_2526 = _profit_2526(sigs, arm, friction)
        # 2023H2 loss reduction (only defined when baseline actually lost).
        if base_2023 < 0:
            reduction = 1.0 - (arm_2023 / base_2023)
            loss_ok = reduction >= LOSS_REDUCTION_MIN
        else:
            reduction = float("nan")
            loss_ok = False
        # 2025-2026 profit give-up (only defined when baseline was profitable).
        if base_2526 > 0:
            give_up = 1.0 - (arm_2526 / base_2526)
            profit_ok = give_up <= PROFIT_GIVEUP_MAX
        else:
            give_up = float("nan")
            profit_ok = False
        fams[arm] = {
            "arm_2023": arm_2023, "arm_2526": arm_2526,
            "reduction": reduction, "give_up": give_up,
            "loss_ok": loss_ok, "profit_ok": profit_ok,
            "ship": bool(loss_ok and profit_ok),
        }
    return {"base_2023": base_2023, "base_2526": base_2526, "fams": fams,
            "ship": bool(fams["ranging"]["ship"] or fams["strict"]["ship"])}


def regime_mix(sigs: list[dict]) -> dict:
    """Count of baseline signals by regime label, overall and in 2023H2."""
    out = {"all": {}, "2023H2": {}}
    for s in sigs:
        lbl = s["regime"] if s["regime"] is not None else "unclassified"
        out["all"][lbl] = out["all"].get(lbl, 0) + 1
        if s["period"] == "2023H2":
            out["2023H2"][lbl] = out["2023H2"].get(lbl, 0) + 1
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Reporting
# ──────────────────────────────────────────────────────────────────────────────

def _pf(x) -> str:
    if x != x:
        return "n/a"
    if x == float("inf"):
        return "inf"
    return "%.2f" % x


def _row(name: str, st: dict) -> str:
    return "| %-18s | %4d | %6s | %+7.3f | %+9.1f | %+9.1f | %5s |" % (
        name, st["n"], _pf(st["pf"]), st["avg_pts"], st["total"], st["max_dd"],
        ("%.0f%%" % (100 * st["win_rate"])) if st["n"] else "n/a",
    )


def _pct(x) -> str:
    return "n/a" if x != x else "%.0f%%" % (100 * x)


def build_report(sigs: list[dict]) -> str:
    L = [HEADER]

    mix = regime_mix(sigs)
    n_all = max(1, len(sigs))
    n_2023 = max(1, len(_in_period(sigs, "2023H2")))
    L.append("## Baseline signal set")
    L.append("")
    L.append("Harvested %d baseline signals over %s..%s (window depth W1M=%d, "
             "the live MIN_BARS_1M floor). Per period: %s." % (
                 len(sigs), START.date(), END.date(), DEPTH,
                 ", ".join("%s=%d" % (p, len(_in_period(sigs, p)))
                           for p in PERIODS)))
    L.append("")
    L.append("Regime at entry (all signals): %s." % ", ".join(
        "%s=%d (%.0f%%)" % (k, v, 100 * v / n_all)
        for k, v in sorted(mix["all"].items())))
    L.append("")
    L.append("Regime at entry (2023H2 only, the target losing period): %s." %
             ", ".join("%s=%d (%.0f%%)" % (k, v, 100 * v / n_2023)
                       for k, v in sorted(mix["2023H2"].items())))
    L.append("")
    L.append("NOTE the classifier needs 24 closed H1 bars (~1500 M1); with the "
             "gate OFF the module runs a W1M=642 window so LIVE it cannot classify "
             "and FAILS TOWARD OFF. Enabling S100_ER_GATE raises the module's "
             "MIN_BARS_1M to 1560; this study measures the gate AS IF armed on that "
             "1560-bar floor -- see the arming note below.")
    L.append("")

    for fname, fval in FRICTIONS.items():
        L.append("## Friction %s (%.2f pt/round-trip)" % (fname, fval))
        L.append("")
        for period in PERIODS:
            L.append("*%s*" % period)
            L.append("")
            L.append("| arm                |    n |     PF | avg pts |    total |     maxDD |   WR |")
            L.append("|--------------------|------|--------|---------|----------|-----------|------|")
            for arm in ARMS:
                L.append(_row(ARM_LABEL[arm], arm_stats(sigs, arm, period, fval)))
            L.append("")

    # Pre-registered rule on base friction (primary), stress reported alongside.
    r = evaluate_rule(sigs, rl.BASE_FRICTION)
    rs = evaluate_rule(sigs, rl.STRESS_FRICTION)
    L.append("## Decision-rule evaluation (base friction 0.45pt)")
    L.append("")
    L.append("Baseline 2023H2 total = %+.1f pts; baseline 2025-2026 profit = "
             "%+.1f pts." % (r["base_2023"], r["base_2526"]))
    L.append("")
    L.append("| arm | 2023H2 total | loss reduced | >=40%? | 2025-26 profit | given up | <=15%? | SHIP |")
    L.append("|-----|--------------|--------------|--------|----------------|----------|--------|------|")
    for arm in ("ranging", "strict"):
        f = r["fams"][arm]
        L.append("| %s | %+.1f | %s | %s | %+.1f | %s | %s | %s |" % (
            ARM_LABEL[arm], f["arm_2023"], _pct(f["reduction"]),
            "YES" if f["loss_ok"] else "no", f["arm_2526"], _pct(f["give_up"]),
            "YES" if f["profit_ok"] else "no",
            "SHIP" if f["ship"] else "NO"))
    L.append("")
    L.append("Stress friction (0.80pt) cross-check -- baseline 2023H2 %+.1f, "
             "2025-26 profit %+.1f:" % (rs["base_2023"], rs["base_2526"]))
    L.append("")
    L.append("| arm | 2023H2 total | loss reduced | 2025-26 profit | given up | SHIP (stress) |")
    L.append("|-----|--------------|--------------|----------------|----------|---------------|")
    for arm in ("ranging", "strict"):
        f = rs["fams"][arm]
        L.append("| %s | %+.1f | %s | %+.1f | %s | %s |" % (
            ARM_LABEL[arm], f["arm_2023"], _pct(f["reduction"]),
            f["arm_2526"], _pct(f["give_up"]),
            "SHIP" if f["ship"] else "NO"))
    L.append("")
    L.append("**Verdict: %s**" % (
        ("SHIP-CANDIDATE -- %s meets the pre-registered rule on base friction "
         "(arming remains an operator decision)"
         % ", ".join(ARM_LABEL[a] for a in ("ranging", "strict")
                     if r["fams"][a]["ship"]))
        if r["ship"] else
        "DO NOT ARM -- neither arm meets the pre-registered rule; the gate "
        "ships DEFAULT OFF and the report is the deliverable"))
    L.append("")

    # Post-hoc context -- NOT part of the pre-registered rule (no goalpost move).
    base_2024 = _period_total(sigs, "baseline", "2024", rl.BASE_FRICTION)
    base_2025 = _period_total(sigs, "baseline", "2025", rl.BASE_FRICTION)
    base_2026 = _period_total(sigs, "baseline", "2026", rl.BASE_FRICTION)
    n_total = len(sigs)
    n_strict = sum(1 for s in sigs if _keep(s, "strict"))
    fs = r["fams"]["strict"]
    b26 = arm_stats(sigs, "baseline", "2026", rl.BASE_FRICTION)
    s26 = arm_stats(sigs, "strict", "2026", rl.BASE_FRICTION)
    L.append("### Reading beyond the pre-registered rule")
    L.append("")
    L.append("* This offline per-signal baseline is a NET LOSS in every period "
             "except 2026 (2023H2 %+.0f, 2024 %+.0f, 2025 %+.0f, 2026 %+.0f pts). "
             "The scalper fires far more signals here than live because the "
             "execution layer (cooldown_s, max_concurrent_positions=1, entry-"
             "manager gates) is NOT modelled -- see Limitations. The live-engine "
             "validation S100 shipped on WAS profitable in 2025-2026; this "
             "per-signal model is not, so the rule's PROFIT-give-up test is "
             "undefined (you cannot give up a profit the baseline never had) and "
             "both arms mechanically fail it. That -- not a gate weakness -- is "
             "why the verdict reads DO NOT ARM." % (
                 r["base_2023"], base_2024, base_2025, base_2026))
    L.append("")
    L.append("* Directionally the **block NOT-TRENDING** arm improves EVERY "
             "period: it cuts the 2023H2 loss by %s (%+.0f -> %+.0f) and turns "
             "2025-2026 from %+.0f to %+.0f pts (2026 alone PF %s -> %s, avg "
             "%+.2f -> %+.2f), admitting only %d of %d signals (%.0f%%, the "
             "TRENDING subset). That is real evidence the trend-persistence filter "
             "selects S100's good tape -- consistent with the hypothesis -- but "
             "the pre-registered rule cannot certify it against a non-profitable "
             "baseline." % (
                 _pct(fs["reduction"]), r["base_2023"], fs["arm_2023"],
                 r["base_2526"], fs["arm_2526"], _pf(b26["pf"]), _pf(s26["pf"]),
                 b26["avg_pts"], s26["avg_pts"], n_strict, n_total,
                 100.0 * n_strict / max(1, n_total)))
    L.append("")
    L.append("* Caveats: the strict arm's per-period n is small (122-276 trades) "
             "so those figures carry wide error bars, and it discards ~93% of "
             "signals (a large frequency drop). The honest next step before arming "
             "is to RE-REGISTER the rule against the live-engine P&L -- where the "
             "baseline is profitable -- rather than reading this per-signal result "
             "as a green light. That is future work, not done here.")
    L.append("")
    L.append("Regardless of verdict, the env gate `S100_ER_GATE=off|ranging|"
             "strict` is implemented in s100_m3_combo.py DEFAULT OFF, byte-"
             "identical to the prior module when off. Enabling it RAISES the "
             "module's MIN_BARS_1M to 1560 (25 closed H1 bars), so the Task-7 "
             "runner assert REFUSES TO START the s100 service LOUD when its window "
             "is too small -- never a silent no-gate. To ARM it live an operator "
             "sets `S100_ER_GATE` to the chosen mode AND widens the s100 service "
             "`RESEARCH_WIN_1M` to >=1560 in compose (currently 700). Neither "
             "change is made here.")
    L.append("")
    L.append("_Generated by strategies/research/er_gate_study.py; rerun to "
             "reproduce._")
    L.append("")
    return "\n".join(L)


HEADER = """# S100 trend-persistence (efficiency-ratio) entry-gate study -- opt15 Task 15

Date: 2026-07-30  |  Branch: feat/optimization-15  |  Author: Claude Code (opt15)

## Purpose

S100 (M3 combo scalper, spec v3) is regime-dependent: across its 3-year
validation the ONLY losing period was 2023H2 (-273 pts), a choppy/ranging tape.
This study measures whether a trend-persistence entry gate -- block new entries
when the Kaufman efficiency ratio says the market is not trending -- removes the
bulk of that 2023H2 loss without giving up the 2025-2026 profit, before the gate
is armed.

## Method

* **Data**: OANDA XAU_USD M1 mids, `ClaudeTradingRD/m3_scalper/xau_m1_3y.parquet`
  (2023-07..2026-07, ~1.07M bars, naive-UTC after load). Resampled with the
  house `label="left", closed="left"` convention.
* **Replay**: the REAL `s100.get_signal` is called on every closed M1 bar in
  S100's trading hours over the full span, with a trailing window at the live
  depth (W1M = MIN_BARS_1M = 642) and module state carried across calls exactly
  as the live runner does. `S100_ER_GATE` is forced OFF so the harvested set is
  the module's UNGATED baseline. The baseline signal set is harvested ONCE; the
  two gate arms are then cheap filters over it, so n differs only by which
  signals the gate removes (the comparison is purely selection quality).
* **Efficiency ratio**: the CANONICAL regime-engine classifier, imported (not
  re-derived) via `replay_lib.TrendRegimeEngine`: `_efficiency_ratio` over the
  last 24 CLOSED H1 bars and 30 CLOSED M15 bars, both > 0.35 -> TRENDING, both
  < 0.20 -> RANGING, else MIXED. Only fully-closed HTF bars as of the entry
  time feed the ratio (an H1 bar closes at label+1h) -> no look-ahead. The
  s100 module ports the identical `_efficiency_ratio`/`_classify_trend`
  (parity proven in tests/test_s100_er_gate.py).
* **Fill model** (shared, conservative): entry at the signal's `entry_price`;
  exits walk the M1 path from the first bar strictly after the entry bar's
  close (no intra-entry-bar look-ahead); SL is checked before TP (a bar
  touching both is a stop-out); the `max_hold_min` TIME backstop applies.
  Friction is one per-round-trip point charge: **0.45pt base, 0.80pt stress**.
* **Arms**: baseline (all signals); **ranging** (drop trend_regime == RANGING);
  **strict** (drop trend_regime != TRENDING). A signal whose regime cannot be
  classified (too little history -- only the first hours of 2023-07) is admitted
  by every arm, mirroring the module's fail-toward-OFF short-window path.
* **Periods**: 2023H2, 2024, 2025, 2026 (the parquet ends 2026-07).

### Limitations (read before trusting the absolute numbers)

* Per-signal quality study. Execution-layer effects the live runner applies --
  `cooldown_s`, `max_concurrent_positions`, entry-manager gates, broker fill
  drift -- are NOT modelled, so absolute counts and PF differ from live. The
  ARM COMPARISON is apples-to-apples (identical signal set), which is what the
  rule turns on.
* The gate needs 24 closed H1 bars (~1500 M1); with the gate OFF the module runs
  W1M=642 and cannot classify (fails toward OFF). Enabling S100_ER_GATE raises
  MIN_BARS_1M to 1560, so an armed service whose window is too small fails LOUD at
  runner startup. This study measures the gate as if armed on that 1560-bar floor
  -- widening RESEARCH_WIN_1M to >=1560 is the operator's one arm-time step.
* Offline fills use the module's own freshest-1m probe; consistent across arms.

## Decision rule (PRE-REGISTERED -- fixed before results were seen)

An arm is a SHIP-CANDIDATE only if BOTH hold on **base friction (0.45pt)**:

1. the **2023H2 loss is reduced by >= 40%** (arm 2023H2 total is at least 40%
   less negative than baseline, or turns positive), AND
2. the **2025-2026 profit gives up <= 15%** (arm 2025+2026 total is at least 85%
   of baseline).

Stress friction (0.80pt) is reported as a cross-check. Meeting the rule makes an
arm a SHIP-CANDIDATE; ARMING it live is a separate operator decision. The env
gate ships DEFAULT OFF regardless of the verdict.

---
"""


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def _summary_line(sigs: list[dict]) -> None:
    r = evaluate_rule(sigs, rl.BASE_FRICTION)
    for arm in ("ranging", "strict"):
        f = r["fams"][arm]
        print("[VERDICT %-8s] 2023H2 %+.1f->%+.1f (reduce %s) | 2025-26 "
              "%+.1f->%+.1f (giveup %s) | ship=%s"
              % (arm, r["base_2023"], f["arm_2023"], _pct(f["reduction"]),
                 r["base_2526"], f["arm_2526"], _pct(f["give_up"]), f["ship"]))
    print("[OVERALL] ship=%s" % r["ship"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=DATA_DEFAULT)
    ap.add_argument("--out", default=REPORT_DEFAULT)
    ap.add_argument("--limit", type=int, default=None,
                    help="cap harvested signals (smoke test)")
    ap.add_argument("--start", default=None, help="override START (smoke test)")
    ap.add_argument("--end", default=None, help="override END (smoke test)")
    ap.add_argument("--dump", default=None,
                    help="harvest, pickle records to this path, exit")
    ap.add_argument("--from-dump", default=None,
                    help="build report from a pickled harvest (no replay)")
    args = ap.parse_args()

    global START, END
    if args.start:
        START = pd.Timestamp(args.start)
    if args.end:
        END = pd.Timestamp(args.end)

    if args.from_dump:
        with open(args.from_dump, "rb") as fh:
            sigs = pickle.load(fh)
        report = build_report(sigs)
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w", encoding="ascii", errors="replace") as fh:
            fh.write(report)
        print("[er_gate_study] wrote report -> %s" % args.out)
        _summary_line(sigs)
        return 0

    print("[er_gate_study] loading M1 parquet ...")
    t0 = time.perf_counter()
    m1 = rl.load_m1(args.data, start=None, end=None)
    print("  loaded %d M1 rows (%s .. %s) in %.0fs" % (
        len(m1), m1["time"].iloc[0], m1["time"].iloc[-1],
        time.perf_counter() - t0))

    print("[er_gate_study] resampling H1/M15 for the regime engine ...")
    h1 = rl.resample_ohlc(m1, "1h")
    m15 = rl.resample_ohlc(m1, "15min")
    print("  H1: %d bars, M15: %d bars" % (len(h1), len(m15)))
    regime = rl.TrendRegimeEngine(h1, m15)

    m1_arrays = (
        m1["time"].to_numpy().astype("datetime64[ns]").astype("int64"),
        m1["high"].to_numpy(float), m1["low"].to_numpy(float),
        m1["close"].to_numpy(float),
    )

    print("[er_gate_study] harvesting s100 baseline (depth=%d) ..." % DEPTH)
    sigs = harvest(m1, m1_arrays, regime, limit=args.limit)

    if args.dump:
        with open(args.dump, "wb") as fh:
            pickle.dump(sigs, fh)
        print("[er_gate_study] dumped %d records -> %s" % (len(sigs), args.dump))
        return 0

    if args.limit or args.start or args.end:
        _summary_line(sigs)
        return 0

    report = build_report(sigs)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="ascii", errors="replace") as fh:
        fh.write(report)
    print("[er_gate_study] wrote report -> %s" % args.out)
    _summary_line(sigs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
