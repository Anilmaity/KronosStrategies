"""
strategies/research/htf_bias_study.py
-------------------------------------
opt15 Task 13 -- offline validation of a counter-HTF-bias entry filter for the
two reversal/trend children implicated in the July-2026 drawdown (S94 sweep
reversal, S99 MSS+FVG). Motivation: during 2-16 Jul 2026 S94 and S99 repeatedly
bought against a bearish D1/H4 and lost ~-$325 between them. Hypothesis: vetoing
(or half-sizing) entries whose side opposes an ALIGNED D1+H4 swing-structure
bias improves the test-period edge.

This is a RESEARCH-FIRST script. It replays the REAL ``get_signal`` of each
module over the 3-year OANDA M1 parquet (harvesting the baseline signal set
ONCE), then evaluates the veto / half-size arms as cheap filters over that
signal set -- the arms differ only in which signals survive / their size, so one
harvest + three filters is far cheaper than three full replays. It applies the
pre-registered decision rule and writes the full report (rule stated BEFORE the
results). It never wires anything into the live modules -- that is a separate,
conditional step gated on this study's verdict.

Run:
  E:/Projects/Kronos/KronosStrategies/.venv/Scripts/python.exe \
      strategies/research/htf_bias_study.py

ASCII-only output. Target runtime < 30 min on this machine.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_STRAT_DIR = os.path.dirname(_HERE)
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from research import replay_lib as rl  # noqa: E402
from backtest_strategies import s94_sweep_reversal as s94  # noqa: E402
from backtest_strategies import s99_mss_fvg as s99  # noqa: E402

DATA_DEFAULT = r"E:\Projects\Kronos\ClaudeTradingRD\m3_scalper\xau_m1_3y.parquet"
REPORT_DEFAULT = os.path.normpath(
    os.path.join(_STRAT_DIR, "..", "docs", "research",
                 "2026-07-30-htf-bias-filter.md")
)

# Replay period per the brief. Signals are harvested only for now_utc in
# [START, END); the module state is warmed from the parquet history that
# precedes START (>=1500 M5 bars are available before 2025-01, so S94's level
# machine is fully built on the first call).
START = pd.Timestamp("2025-01-01")
END = pd.Timestamp("2026-08-01")

STRATS = {
    "s94": {"mod": s94, "depth": 1500, "label": "S94 sweep-reversal (TREND)"},
    "s99": {"mod": s99, "depth": 160, "label": "S99 MSS+FVG (REVERSAL)"},
}
FRICTIONS = {"base": rl.BASE_FRICTION, "stress": rl.STRESS_FRICTION}


# ──────────────────────────────────────────────────────────────────────────────
# Signal harvest (one replay per strategy)
# ──────────────────────────────────────────────────────────────────────────────

def harvest(mod, depth: int, m5: pd.DataFrame, bias: rl.BiasEngine,
            m1_arrays, name: str, limit: int | None = None) -> list[dict]:
    """Replay ``mod.get_signal`` bar-by-bar over closed M5 bars in [START, END),
    matching the live window depth. Returns the harvested signal records with
    bias-at-entry and gross exit points already attached."""
    t_ns_m1, h_m1, l_m1, c_m1 = m1_arrays
    m5_time = m5["time"].to_numpy()            # naive datetime64[ns]
    o = m5["open"].to_numpy(float)
    hh = m5["high"].to_numpy(float)
    ll = m5["low"].to_numpy(float)
    cc = m5["close"].to_numpy(float)
    n = m5_time.shape[0]

    # first index whose CLOSE (label + 5min) is >= START
    five = np.timedelta64(5, "m")
    close_time = m5_time + five
    start_i = int(np.searchsorted(close_time, np.datetime64(START.to_datetime64()), "left"))
    end_i = int(np.searchsorted(close_time, np.datetime64(END.to_datetime64()), "left"))

    mod.reset_state()
    out: list[dict] = []
    t0 = time.perf_counter()
    steps = 0
    for i in range(start_i, end_i):
        lo = max(0, i - depth + 1)
        w5m = pd.DataFrame({
            "time": m5_time[lo:i + 1],
            "open": o[lo:i + 1], "high": hh[lo:i + 1],
            "low": ll[lo:i + 1], "close": cc[lo:i + 1],
        })
        now_naive = pd.Timestamp(m5_time[i]) + pd.Timedelta(minutes=5)
        now_tz = now_naive.tz_localize("UTC")
        sig = mod.get_signal(None, w5m, None, now_tz)
        steps += 1
        if steps % 20000 == 0:
            el = time.perf_counter() - t0
            print("  [%s] %d/%d M5 steps (%.0fs, %d signals)"
                  % (name, steps, end_i - start_i, el, len(out)))
        if sig is None:
            continue
        aligned, h4o = bias.bias_at(now_naive)
        ex = rl.simulate_exit(
            t_ns_m1, h_m1, l_m1, c_m1, now_naive, sig.side,
            float(sig.entry_price), float(sig.stop_loss),
            float(sig.take_profit), sig.max_hold_min,
        )
        out.append({
            "entry_time": now_naive,
            "year": now_naive.year,
            "side": sig.side,
            "entry": float(sig.entry_price),
            "sl": float(sig.stop_loss),
            "tp": float(sig.take_profit),
            "max_hold": sig.max_hold_min,
            "reason": sig.reason,
            "gross": ex.gross_pts,
            "exit_reason": ex.reason,
            "aligned": aligned,
            "h4": h4o,
        })
        if limit is not None and len(out) >= limit:
            break
    el = time.perf_counter() - t0
    print("  [%s] harvest done: %d signals from %d M5 steps in %.0fs"
          % (name, len(out), steps, el))
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Arms
# ──────────────────────────────────────────────────────────────────────────────

def _split(sigs: list[dict], which: str) -> list[dict]:
    if which == "all":
        return sigs
    yr = 2025 if which == "train" else 2026
    return [s for s in sigs if s["year"] == yr]


def arm_nets(sigs: list[dict], arm: str, friction: float) -> list[float]:
    """Net (post-friction) point series for one arm, in chronological order.

    baseline    : all trades, full size.
    veto_d1h4   : drop trades opposing an aligned D1+H4 bias.
    half_d1h4   : half-size those opposing trades (P&L + friction scale 0.5).
    veto_h4     : drop trades opposing the H4-only bias.
    half_h4     : half-size those opposing the H4-only bias.
    """
    nets: list[float] = []
    for s in sigs:
        if arm == "baseline":
            nets.append(rl.net_from_gross(s["gross"], friction))
        elif arm == "veto_d1h4":
            if not rl.opposes(s["side"], s["aligned"]):
                nets.append(rl.net_from_gross(s["gross"], friction))
        elif arm == "half_d1h4":
            size = 0.5 if rl.opposes(s["side"], s["aligned"]) else 1.0
            nets.append(rl.net_from_gross(s["gross"], friction, size=size))
        elif arm == "veto_h4":
            if not rl.opposes(s["side"], s["h4"]):
                nets.append(rl.net_from_gross(s["gross"], friction))
        elif arm == "half_h4":
            size = 0.5 if rl.opposes(s["side"], s["h4"]) else 1.0
            nets.append(rl.net_from_gross(s["gross"], friction, size=size))
        else:
            raise ValueError(arm)
    return nets


def arm_stats(sigs: list[dict], arm: str, which: str, friction: float) -> dict:
    return rl.trade_stats(arm_nets(_split(sigs, which), arm, friction))


# ──────────────────────────────────────────────────────────────────────────────
# Decision rule
# ──────────────────────────────────────────────────────────────────────────────

def evaluate_rule(sigs: list[dict]) -> dict:
    """Apply the pre-registered ship rule for the D1+H4 VETO arm:
      ship iff  test PF improves  AND  test avg-pts/trade improves
                AND veto removes < 40% of test trades
                AND stress-cost (0.80pt) test PF does not degrade.
    Returns a dict of the measured quantities + the four booleans + verdict."""
    base_t = arm_stats(sigs, "baseline", "test", rl.BASE_FRICTION)
    veto_t = arm_stats(sigs, "veto_d1h4", "test", rl.BASE_FRICTION)
    base_ts = arm_stats(sigs, "baseline", "test", rl.STRESS_FRICTION)
    veto_ts = arm_stats(sigs, "veto_d1h4", "test", rl.STRESS_FRICTION)

    n_base = base_t["n"]
    n_veto = veto_t["n"]
    removed_frac = (1.0 - n_veto / n_base) if n_base else 1.0

    pf_improves = n_veto > 0 and veto_t["pf"] > base_t["pf"]
    avg_improves = n_veto > 0 and veto_t["avg_pts"] > base_t["avg_pts"]
    removal_ok = removed_frac < 0.40
    stress_ok = n_veto > 0 and veto_ts["pf"] >= base_ts["pf"]
    ship = bool(pf_improves and avg_improves and removal_ok and stress_ok)
    return {
        "base_pf": base_t["pf"], "veto_pf": veto_t["pf"],
        "base_avg": base_t["avg_pts"], "veto_avg": veto_t["avg_pts"],
        "base_pf_stress": base_ts["pf"], "veto_pf_stress": veto_ts["pf"],
        "n_base": n_base, "n_veto": n_veto, "removed_frac": removed_frac,
        "pf_improves": pf_improves, "avg_improves": avg_improves,
        "removal_ok": removal_ok, "stress_ok": stress_ok, "ship": ship,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Reporting
# ──────────────────────────────────────────────────────────────────────────────

def _pf(x) -> str:
    if x != x:            # NaN
        return "n/a"
    if x == float("inf"):
        return "inf"
    return "%.2f" % x


def _row(name: str, st: dict) -> str:
    return "| %-11s | %4d | %6s | %+7.3f | %+8.1f | %5s |" % (
        name, st["n"], _pf(st["pf"]), st["avg_pts"], st["max_dd"],
        ("%.0f%%" % (100 * st["win_rate"])) if st["n"] else "n/a",
    )


_ARMS_REPORT = [
    ("baseline", "baseline"),
    ("veto_d1h4", "veto (D1+H4)"),
    ("half_d1h4", "half (D1+H4)"),
    ("veto_h4", "veto (H4-only)"),
    ("half_h4", "half (H4-only)"),
]


def strategy_section(key: str, sigs: list[dict]) -> str:
    cfg = STRATS[key]
    L = ["## %s -- %s" % (key.upper(), cfg["label"]), ""]
    L.append("Live window depth W5M=%d. Harvested %d baseline signals "
             "(%d in 2025 train, %d in 2026 test)." % (
                 cfg["depth"], len(sigs),
                 len(_split(sigs, "train")), len(_split(sigs, "test"))))
    L.append("")

    # bias distribution among baseline signals
    opp_al = sum(1 for s in sigs if rl.opposes(s["side"], s["aligned"]))
    opp_h4 = sum(1 for s in sigs if rl.opposes(s["side"], s["h4"]))
    tot = max(1, len(sigs))
    L.append("Of the baseline signals, %d (%.0f%%) oppose an aligned D1+H4 bias "
             "and %d (%.0f%%) oppose the H4-only bias." % (
                 opp_al, 100 * opp_al / tot, opp_h4, 100 * opp_h4 / tot))
    L.append("")

    for fname, fval in FRICTIONS.items():
        L.append("### Friction %s (%.2f pt/round-trip)" % (fname, fval))
        for which in ("train", "test", "all"):
            L.append("")
            L.append("*%s*" % {"train": "Train 2025", "test": "Test 2026",
                               "all": "All 2025-2026"}[which])
            L.append("")
            L.append("| arm         |    n |     PF | avg pts |   maxDD |   WR |")
            L.append("|-------------|------|--------|---------|---------|------|")
            for arm_key, arm_label in _ARMS_REPORT:
                st = arm_stats(sigs, arm_key, which, fval)
                L.append(_row(arm_label, st))
        L.append("")

    r = evaluate_rule(sigs)
    L.append("### Decision-rule evaluation (D1+H4 veto)")
    L.append("")
    L.append("| criterion | baseline | veto | pass? |")
    L.append("|-----------|----------|------|-------|")
    L.append("| test PF improves | %s | %s | %s |" % (
        _pf(r["base_pf"]), _pf(r["veto_pf"]), "YES" if r["pf_improves"] else "no"))
    L.append("| test avg-pts improves | %+.3f | %+.3f | %s |" % (
        r["base_avg"], r["veto_avg"], "YES" if r["avg_improves"] else "no"))
    L.append("| veto removes < 40%% of test trades | n=%d | n=%d (%.0f%% removed) | %s |" % (
        r["n_base"], r["n_veto"], 100 * r["removed_frac"],
        "YES" if r["removal_ok"] else "no"))
    L.append("| stress (0.80pt) test PF not degraded | %s | %s | %s |" % (
        _pf(r["base_pf_stress"]), _pf(r["veto_pf_stress"]),
        "YES" if r["stress_ok"] else "no"))
    L.append("")
    L.append("**Verdict for %s: %s**" % (
        key.upper(), "SHIP the D1+H4 veto" if r["ship"] else "DO NOT SHIP (report only)"))
    L.append("")

    # H4-only confirmation (the live-wireable bias)
    h4_test_base = arm_stats(sigs, "baseline", "test", rl.BASE_FRICTION)
    h4_test_veto = arm_stats(sigs, "veto_h4", "test", rl.BASE_FRICTION)
    h4_test_veto_s = arm_stats(sigs, "veto_h4", "test", rl.STRESS_FRICTION)
    h4_holds = (h4_test_veto["n"] > 0 and h4_test_veto["pf"] > h4_test_base["pf"]
                and h4_test_veto["avg_pts"] > h4_test_base["avg_pts"])
    L.append("H4-only confirmation (the bias a live runner can actually compute "
             "from resampled w15m -> H4; D1 depth is unreachable live): test PF "
             "%s -> %s, avg %+.3f -> %+.3f, stress PF %s. Edge holds H4-only: %s."
             % (_pf(h4_test_base["pf"]), _pf(h4_test_veto["pf"]),
                h4_test_base["avg_pts"], h4_test_veto["avg_pts"],
                _pf(h4_test_veto_s["pf"]), "YES" if h4_holds else "no"))
    L.append("")
    return "\n".join(L), r, h4_holds


HEADER = """# HTF-bias entry filter -- offline validation study (opt15 Task 13)

Date: 2026-07-30  |  Branch: feat/optimization-15  |  Author: Claude Code (opt15)

## Purpose

During the 2-16 July 2026 drawdown, S94 (sweep reversal) and S99 (MSS+FVG)
repeatedly opened LONGs into a bearish daily/4-hour structure and lost ~-$325
between them (see vault `40 Incidents` / the July-drawdown memory). The proposed
fix is a *direction-aware* entry filter: suppress (or shrink) entries whose side
opposes the higher-timeframe bias. This study measures whether that filter
actually improves the out-of-sample edge before any code is wired.

## Method

* **Data**: OANDA XAU_USD M1 mids, `ClaudeTradingRD/m3_scalper/xau_m1_3y.parquet`
  (2023-07..2026-07, ~1.07M bars, naive-UTC after load). Resampled to
  M5/M15/H1/H4/D1 with the house convention `label="left", closed="left"`
  (OANDA candles are left-labelled).
* **Replay**: the REAL `get_signal(w1m=None, w5m, w15m=None, now_utc)` of each
  module is called on every closed M5 bar in 2025-01..2026-07, with a trailing
  window matched to the live depth (S94 W5M=1500, S99 W5M=160) and module state
  carried across calls exactly as the live runner does (`reset_state()` once at
  the start; S94's incremental level machine warms from the 1500-bar window).
  `w1m=None` selects each module's documented M5-fallback probe (offline
  replays fill on the closed M5 bar). `w15m=None` (neither module consumes it).
* **Fill model**: entry at the signal's `entry_price`; exits simulated on the M1
  path with static SL/TP and the module's `max_hold_min` TIME backstop.
  Conservative: SL is checked before TP (a bar touching both is a stop-out);
  the entry M5 bar is excluded from the exit scan (no intra-entry-bar
  look-ahead). Friction is a per-round-trip point charge deducted once from
  gross: **0.45pt base, 0.80pt stress**.
* **Bias**: D1 and H4 swing structure via `ict_engine.get_market_structure`
  (lookback=3; HH+HL->bullish, LH+LL->bearish, else ranging) -- reused by
  import, so this is the SAME classifier the live regime engine uses
  (`_structure_and_swings`). Only fully-CLOSED HTF bars as of `now_utc` are
  read (a D1 bar closes at label+1d, an H4 bar at label+4h -> no peeking).
  Frame depth mirrors `regime_engine.FRAME_SPEC` (D1=120d, H4=90d).
  *Aligned D1+H4* means both frames are directional AND agree; ranging or
  disagreement never vetoes.
* **Arms** (evaluated as filters over the ONE harvested signal set): baseline;
  veto (drop trades opposing the aligned bias); half-size (opposing trades at
  0.5x P&L and friction); plus the H4-only variants (the live-wireable bias).
* **Splits**: train 2025, test 2026, and combined.

### Limitations (read before trusting the absolute numbers)

* This is a per-signal quality study. Execution-layer effects the live runner
  applies -- `cooldown_s`, `max_concurrent_positions`, entry-manager gates,
  broker fill drift -- are NOT modelled, so absolute trade counts and PF differ
  from live. The ARM COMPARISON (baseline vs veto vs half) is apples-to-apples
  because every arm sees the identical signal set, which is what the decision
  rule turns on.
* Offline fills use M5-granularity probes (the module's own offline-replay
  path); live fills use the fresher 1m probe. This is consistent across arms.
* The bias here is pure D1+H4 structure. The live regime engine's `h4_bias`
  additionally folds in H1 agreement; this study deliberately uses the plain
  D1+H4 framing of the task brief and separately reports an H4-only arm.

## Decision rule (PRE-REGISTERED -- fixed before results were seen)

Ship the counter-HTF-bias VETO for a strategy **only if ALL** hold on the
**test period (2026)**:

1. test-period Profit Factor **improves** (veto PF > baseline PF), AND
2. test-period average points/trade **improves** (veto avg > baseline avg), AND
3. the veto removes **< 40%** of test-period trades, AND
4. the stress-cost (0.80pt) test-period PF **does not degrade**
   (veto stress PF >= baseline stress PF).

If any criterion fails, DO NOT wire the gate -- the report is the deliverable.
If shipped, the gate is env-flagged **DEFAULT OFF** (`S94_HTF_VETO`,
`S99_HTF_VETO`), the edge is re-confirmed H4-only (live cannot see D1 depth),
and golden parity is proven with the flag off.

---
"""


def build_report(results: dict[str, list[dict]]) -> str:
    parts = [HEADER]
    verdicts = {}
    for key in ("s94", "s99"):
        sec, r, h4_holds = strategy_section(key, results[key])
        parts.append(sec)
        verdicts[key] = (r["ship"], h4_holds)
    parts.append("## Summary verdict")
    parts.append("")
    for key in ("s94", "s99"):
        ship, h4h = verdicts[key]
        parts.append("* **%s**: %s%s" % (
            key.upper(),
            "SHIP D1+H4 veto" if ship else "NO-SHIP (report only)",
            "" if not ship else (" -- H4-only edge %s"
                                 % ("confirmed, safe to wire" if h4h
                                    else "does NOT hold; wiring blocked")),
        ))
    parts.append("")
    parts.append("_Generated by strategies/research/htf_bias_study.py; rerun to "
                 "reproduce._")
    parts.append("")
    return "\n".join(parts)


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=DATA_DEFAULT)
    ap.add_argument("--out", default=REPORT_DEFAULT)
    ap.add_argument("--limit", type=int, default=None,
                    help="cap harvested signals per strategy (smoke test)")
    ap.add_argument("--only", default=None, help="s94 or s99 (smoke test)")
    args = ap.parse_args()

    print("[htf_bias_study] loading M1 parquet ...")
    t0 = time.perf_counter()
    m1 = rl.load_m1(args.data)
    print("  loaded %d M1 rows (%s .. %s) in %.0fs" % (
        len(m1), m1["time"].iloc[0], m1["time"].iloc[-1],
        time.perf_counter() - t0))

    print("[htf_bias_study] resampling frames ...")
    frames = rl.build_frames(m1)
    for k, v in frames.items():
        print("  %s: %d bars" % (k, len(v)))

    bias = rl.BiasEngine(frames["D1"], frames["H4"])
    m1_arrays = (
        m1["time"].to_numpy().astype("datetime64[ns]").astype("int64"),
        m1["high"].to_numpy(float), m1["low"].to_numpy(float),
        m1["close"].to_numpy(float),
    )

    keys = [args.only] if args.only else ["s94", "s99"]
    results: dict[str, list[dict]] = {}
    for key in keys:
        cfg = STRATS[key]
        print("[htf_bias_study] harvesting %s (W5M=%d) ..." % (key, cfg["depth"]))
        results[key] = harvest(cfg["mod"], cfg["depth"], frames["M5"], bias,
                               m1_arrays, key, limit=args.limit)

    if args.only or args.limit:
        # smoke run: print a compact summary, do not overwrite the report
        for key in keys:
            r = evaluate_rule(results[key])
            print("[SMOKE %s] base_pf=%s veto_pf=%s base_avg=%+.3f veto_avg=%+.3f "
                  "removed=%.0f%% ship=%s"
                  % (key, _pf(r["base_pf"]), _pf(r["veto_pf"]),
                     r["base_avg"], r["veto_avg"], 100 * r["removed_frac"],
                     r["ship"]))
        return 0

    report = build_report(results)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="ascii", errors="replace") as fh:
        fh.write(report)
    print("[htf_bias_study] wrote report -> %s" % args.out)
    for key in ("s94", "s99"):
        r = evaluate_rule(results[key])
        print("[VERDICT %s] ship=%s (base_pf=%s->veto_pf=%s, base_avg=%+.3f->"
              "veto_avg=%+.3f, removed=%.0f%%, stress %s->%s)"
              % (key, r["ship"], _pf(r["base_pf"]), _pf(r["veto_pf"]),
                 r["base_avg"], r["veto_avg"], 100 * r["removed_frac"],
                 _pf(r["base_pf_stress"]), _pf(r["veto_pf_stress"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
