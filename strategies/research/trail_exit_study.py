"""
strategies/research/trail_exit_study.py
---------------------------------------
opt15 Task 14 -- offline study of CHANDELIER TRAILING exits for the two
tail-carried children S94 (sweep reversal, 29% WR, P&L in a few big winners) and
S100 (M3 combo scalper). Motivation (measured): the M3 12-month forensics found
TIME-backstop flat-closes were 64% winners ("trail, don't cut") and S94's edge
lives in the tail. This quantifies whether a chandelier trail beats the static
SL/TP/TIME exits before any wiring is considered.

RESEARCH ONLY. It replays the REAL ``get_signal`` of each module over the 3-year
OANDA M1 parquet, harvesting the baseline signal set ONCE per strategy, then
evaluates every exit arm as a cheap re-simulation over the recorded signals'
M1 path (the arms share the identical signal set -- only the exit LOGIC differs,
so n is constant across arms and the comparison is purely exit quality). It
never edits a strategy module; wiring any winning config is a separate operator
decision (base.Signal.trailing + the monitor's TRAIL path already exist, but at a
different parameterization -- see the report).

Arms (per strategy, per friction, per split):
  baseline           static SL / TP / TIME flat-close (as deployed).
  chandelier k=2.0/2.5/3.0
                     arm (a): SL holds until +1R, then trail = HW - k*ATR
                     (mirror for shorts), ratcheting; no fixed TP; TIME retained.
  time_replace k=...  arm (b): baseline UNTIL max_hold, then a would-be TIME
                     flat-close becomes a chandelier trail (isolates the TIME
                     subset -- the "64% TIME winners" hypothesis).
ATR is ATR(14) on the strategy's working TF (M5 for S94, M3 for S100), FROZEN at
entry (documented choice; rolling is a follow-up). Costs 0.45pt / 0.80pt stress.
Train 2025 / test 2026.

Run:
  E:/Projects/Kronos/KronosStrategies/.venv/Scripts/python.exe \
      strategies/research/trail_exit_study.py

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

from research import replay_lib as rl  # noqa: E402
from backtest_strategies import s94_sweep_reversal as s94  # noqa: E402
from backtest_strategies import s100_m3_combo as s100  # noqa: E402
from backtest_strategies._shared_ta import atr_last  # noqa: E402

DATA_DEFAULT = r"E:\Projects\Kronos\ClaudeTradingRD\m3_scalper\xau_m1_3y.parquet"
REPORT_DEFAULT = os.path.normpath(
    os.path.join(_STRAT_DIR, "..", "docs", "research",
                 "2026-07-30-trailing-exits.md")
)

START = pd.Timestamp("2025-01-01")
END = pd.Timestamp("2026-08-01")

K_VALUES = (2.0, 2.5, 3.0)
FRICTIONS = {"base": rl.BASE_FRICTION, "stress": rl.STRESS_FRICTION}

# S94 works on M5 (window depth mirrors Task 13's harvest, 1500 bars -> full
# level universe warmed). S100 works on M3 resampled from an M1 window; its
# window depth is the documented MIN_BARS_1M contract (642 = 3 * _MIN_M3).
STRATS = {
    "s94": {"mod": s94, "tf": "M5", "depth": 1500,
            "label": "S94 sweep-reversal (TREND, tail-carried 29% WR)"},
    "s100": {"mod": s100, "tf": "M1", "depth": s100.MIN_BARS_1M,
             "label": "S100 M3 combo scalper (spec v3)"},
}


# ──────────────────────────────────────────────────────────────────────────────
# Exit-arm re-simulation over one signal's M1 path
# ──────────────────────────────────────────────────────────────────────────────

def _sim_all_arms(m1_arrays, sig: dict) -> dict:
    """Compute gross points for EVERY exit arm on one signal's M1 path. Returns
    a flat dict of arm-key -> gross plus the baseline exit reason. Friction is
    applied later so the sim runs once per signal, not once per (arm x split x
    friction)."""
    t_ns, h, l, c = m1_arrays
    args = (t_ns, h, l, c, sig["entry_time"], sig["side"],
            sig["entry"], sig["sl"])
    base = rl.simulate_exit(*args, sig["tp"], sig["max_hold"])
    out = {"base_gross": base.gross_pts, "base_reason": base.reason}
    atr = sig["atr_entry"]
    for k in K_VALUES:
        cha = rl.simulate_chandelier_exit(*args, atr, k, sig["max_hold"])
        out["cha_%.1f_gross" % k] = cha.gross_pts
        tr = rl.simulate_time_replace_exit(t_ns, h, l, c, sig["entry_time"],
                                           sig["side"], sig["entry"], sig["sl"],
                                           sig["tp"], atr, k, sig["max_hold"])
        out["tr_%.1f_gross" % k] = tr.gross_pts
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Signal harvest (one replay per strategy)
# ──────────────────────────────────────────────────────────────────────────────

def harvest_m5(mod, depth, m5, m1_arrays, name, limit=None) -> list[dict]:
    """S94: replay get_signal on each closed M5 bar in [START, END). ATR(14) is
    computed on the M5 window at entry (the working-TF chandelier width)."""
    t_ns_m1, h_m1, l_m1, c_m1 = m1_arrays
    m5_time = m5["time"].to_numpy()
    o = m5["open"].to_numpy(float)
    hh = m5["high"].to_numpy(float)
    ll = m5["low"].to_numpy(float)
    cc = m5["close"].to_numpy(float)

    five = np.timedelta64(5, "m")
    close_time = m5_time + five
    start_i = int(np.searchsorted(close_time, np.datetime64(START.to_datetime64()), "left"))
    end_i = int(np.searchsorted(close_time, np.datetime64(END.to_datetime64()), "left"))
    start_i = max(start_i, depth)

    mod.reset_state()
    out: list[dict] = []
    t0 = time.perf_counter()
    steps = 0
    for i in range(start_i, end_i):
        lo = max(0, i - depth + 1)
        w5m = pd.DataFrame({
            "time": m5_time[lo:i + 1], "open": o[lo:i + 1],
            "high": hh[lo:i + 1], "low": ll[lo:i + 1], "close": cc[lo:i + 1],
        })
        now_naive = pd.Timestamp(m5_time[i]) + pd.Timedelta(minutes=5)
        sig = mod.get_signal(None, w5m, None, now_naive.tz_localize("UTC"))
        steps += 1
        if steps % 20000 == 0:
            print("  [%s] %d/%d M5 steps (%.0fs, %d signals)"
                  % (name, steps, end_i - start_i, time.perf_counter() - t0, len(out)))
        if sig is None:
            continue
        atr_e = atr_last(hh[lo:i + 1], ll[lo:i + 1], cc[lo:i + 1], 14)
        if not (atr_e > 0):
            continue
        rec = {
            "entry_time": now_naive, "year": now_naive.year, "side": sig.side,
            "entry": float(sig.entry_price), "sl": float(sig.stop_loss),
            "tp": float(sig.take_profit), "max_hold": sig.max_hold_min,
            "reason": sig.reason, "atr_entry": float(atr_e),
        }
        rec.update(_sim_all_arms((t_ns_m1, h_m1, l_m1, c_m1), rec))
        out.append(rec)
        if limit is not None and len(out) >= limit:
            break
    print("  [%s] harvest done: %d signals from %d M5 steps in %.0fs"
          % (name, len(out), steps, time.perf_counter() - t0))
    return out


def harvest_m1(mod, depth, m1, m1_arrays, name, limit=None) -> list[dict]:
    """S100: replay get_signal on each closed M1 bar in trading hours in
    [START, END). ATR(14) is computed on the M3 resample at entry (the exact
    ATR the module uses internally). Out-of-hours steps mirror get_signal's
    early return (clear _pending) without the resample -- ~54% of steps."""
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
    start_i = max(start_i, depth)
    hours = set(mod._HOURS)

    mod.reset_state()
    out: list[dict] = []
    t0 = time.perf_counter()
    steps = 0
    for i in range(start_i, end_i):
        now_naive = pd.Timestamp(m1_time[i]) + pd.Timedelta(minutes=1)
        steps += 1
        if steps % 50000 == 0:
            print("  [%s] %d/%d M1 steps (%.0fs, %d signals)"
                  % (name, steps, end_i - start_i, time.perf_counter() - t0, len(out)))
        if now_naive.hour not in hours:
            mod._pending = None       # faithful to get_signal's out-of-hours path
            continue
        lo = i - depth + 1
        w1m = pd.DataFrame({
            "time": m1_time[lo:i + 1], "open": o[lo:i + 1],
            "high": hh[lo:i + 1], "low": ll[lo:i + 1], "close": cc[lo:i + 1],
        })
        sig = mod.get_signal(w1m, None, None, now_naive.tz_localize("UTC"))
        if sig is None:
            continue
        m3 = mod._resample_m3(w1m)
        if len(m3) < 15:
            continue
        atr_e = atr_last(m3["high"].to_numpy(float), m3["low"].to_numpy(float),
                         m3["close"].to_numpy(float), 14)
        if not (atr_e > 0):
            continue
        rec = {
            "entry_time": now_naive, "year": now_naive.year, "side": sig.side,
            "entry": float(sig.entry_price), "sl": float(sig.stop_loss),
            "tp": float(sig.take_profit), "max_hold": sig.max_hold_min,
            "reason": sig.reason, "atr_entry": float(atr_e),
        }
        rec.update(_sim_all_arms((t_ns_m1, h_m1, l_m1, c_m1), rec))
        out.append(rec)
        if limit is not None and len(out) >= limit:
            break
    print("  [%s] harvest done: %d signals from %d M1 steps in %.0fs"
          % (name, len(out), steps, time.perf_counter() - t0))
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Arms / stats
# ──────────────────────────────────────────────────────────────────────────────

ARM_KEYS = ["base"] + ["cha_%.1f" % k for k in K_VALUES] + ["tr_%.1f" % k for k in K_VALUES]
ARM_LABEL = {"base": "baseline"}
for _k in K_VALUES:
    ARM_LABEL["cha_%.1f" % _k] = "chandelier k=%.1f" % _k
    ARM_LABEL["tr_%.1f" % _k] = "time-replace k=%.1f" % _k


def _split(sigs, which):
    if which == "all":
        return sigs
    yr = 2025 if which == "train" else 2026
    return [s for s in sigs if s["year"] == yr]


def arm_stats(sigs, arm, which, friction) -> dict:
    key = "%s_gross" % arm
    nets = [rl.net_from_gross(s[key], friction) for s in _split(sigs, which)]
    return rl.trade_stats(nets)


# ──────────────────────────────────────────────────────────────────────────────
# Decision rule (PRE-REGISTERED -- see report header)
# ──────────────────────────────────────────────────────────────────────────────

def _best_k_on_train(sigs, family) -> float:
    best_k, best_pf = K_VALUES[0], -1e18
    for k in K_VALUES:
        pf = arm_stats(sigs, "%s_%.1f" % (family, k), "train", rl.BASE_FRICTION)["pf"]
        pf = -1e18 if pf != pf else pf          # NaN guard
        if pf > best_pf:
            best_pf, best_k = pf, k
    return best_k


def evaluate_rule(sigs) -> dict:
    """Per family (chandelier, time_replace): pick the best k on the TRAIN
    period (2025) by PF -- proper out-of-sample param selection -- then judge
    THAT k on TEST (2026) against the four pre-registered criteria."""
    base_t = arm_stats(sigs, "base", "test", rl.BASE_FRICTION)
    base_ts = arm_stats(sigs, "base", "test", rl.STRESS_FRICTION)
    fams = {}
    for fam in ("cha", "tr"):
        bk = _best_k_on_train(sigs, fam)
        arm = "%s_%.1f" % (fam, bk)
        test = arm_stats(sigs, arm, "test", rl.BASE_FRICTION)
        test_s = arm_stats(sigs, arm, "test", rl.STRESS_FRICTION)
        pf_ok = test["pf"] > base_t["pf"]
        avg_ok = test["avg_pts"] > base_t["avg_pts"]
        stress_ok = test_s["pf"] >= base_ts["pf"]
        tail_ok = test["tail5"] > base_t["tail5"]
        fams[fam] = {
            "best_k": bk, "test": test, "test_s": test_s,
            "pf_ok": pf_ok, "avg_ok": avg_ok, "stress_ok": stress_ok,
            "tail_ok": tail_ok,
            "ship": bool(pf_ok and avg_ok and stress_ok and tail_ok),
        }
    return {"base_t": base_t, "base_ts": base_ts, "fams": fams,
            "ship": bool(fams["cha"]["ship"] or fams["tr"]["ship"])}


def time_exit_forensics(sigs) -> dict:
    """Baseline exit-reason mix + the fraction of TIME flat-closes that were
    winners (grounds the 'trail don't cut' motivation on THIS dataset)."""
    reasons = {}
    time_win = time_tot = 0
    for s in sigs:
        r = s["base_reason"]
        reasons[r] = reasons.get(r, 0) + 1
        if r == "TIME":
            time_tot += 1
            if s["base_gross"] > 0:
                time_win += 1
    return {"reasons": reasons, "time_tot": time_tot, "time_win": time_win,
            "time_win_frac": (time_win / time_tot) if time_tot else float("nan")}


# ──────────────────────────────────────────────────────────────────────────────
# Reporting
# ──────────────────────────────────────────────────────────────────────────────

def _pf(x):
    if x != x:
        return "n/a"
    if x == float("inf"):
        return "inf"
    return "%.2f" % x


def _row(name, st):
    return "| %-18s | %4d | %6s | %+7.3f | %+9.1f | %+9.1f | %5s |" % (
        name, st["n"], _pf(st["pf"]), st["avg_pts"], st["max_dd"], st["tail5"],
        ("%.0f%%" % (100 * st["win_rate"])) if st["n"] else "n/a",
    )


def strategy_section(key, sigs) -> tuple[str, dict]:
    cfg = STRATS[key]
    L = ["## %s -- %s" % (key.upper(), cfg["label"]), ""]
    L.append("Working TF %s, window depth %d. Harvested %d baseline signals "
             "(%d in 2025 train, %d in 2026 test). n is CONSTANT across arms -- "
             "trailing changes exits only, never which signals fire." % (
                 cfg["tf"], cfg["depth"], len(sigs),
                 len(_split(sigs, "train")), len(_split(sigs, "test"))))
    L.append("")

    fx = time_exit_forensics(sigs)
    rmix = ", ".join("%s=%d" % (k, v) for k, v in sorted(fx["reasons"].items()))
    L.append("Baseline exit mix: %s." % rmix)
    if fx["time_tot"]:
        L.append("TIME flat-closes: %d, of which %d were winners (%.0f%%) -- the "
                 "'trail don't cut' subset arm (b) targets." % (
                     fx["time_tot"], fx["time_win"], 100 * fx["time_win_frac"]))
    else:
        L.append("TIME flat-closes: 0 in this harvest (arm (b) is a no-op here).")
    L.append("")

    for fname, fval in FRICTIONS.items():
        L.append("### Friction %s (%.2f pt/round-trip)" % (fname, fval))
        for which in ("train", "test", "all"):
            L.append("")
            L.append("*%s*" % {"train": "Train 2025", "test": "Test 2026",
                               "all": "All 2025-2026"}[which])
            L.append("")
            L.append("| arm                |    n |     PF | avg pts |     maxDD | top5 wins |   WR |")
            L.append("|--------------------|------|--------|---------|-----------|-----------|------|")
            for arm in ARM_KEYS:
                L.append(_row(ARM_LABEL[arm], arm_stats(sigs, arm, which, fval)))
        L.append("")

    r = evaluate_rule(sigs)
    b = r["base_t"]
    L.append("### Decision-rule evaluation (best-k chosen on TRAIN, judged on TEST)")
    L.append("")
    for fam, famlabel in (("cha", "Chandelier (arm a)"), ("tr", "Time-replace (arm b)")):
        f = r["fams"][fam]
        t = f["test"]
        L.append("**%s -- best k on train = %.1f**" % (famlabel, f["best_k"]))
        L.append("")
        L.append("| criterion | baseline | %s | pass? |" % ARM_LABEL["%s_%.1f" % (fam, f["best_k"])])
        L.append("|-----------|----------|------|-------|")
        L.append("| test PF improves | %s | %s | %s |" % (
            _pf(b["pf"]), _pf(t["pf"]), "YES" if f["pf_ok"] else "no"))
        L.append("| test avg-pts improves | %+.3f | %+.3f | %s |" % (
            b["avg_pts"], t["avg_pts"], "YES" if f["avg_ok"] else "no"))
        L.append("| stress (0.80pt) test PF not degraded | %s | %s | %s |" % (
            _pf(r["base_ts"]["pf"]), _pf(f["test_s"]["pf"]),
            "YES" if f["stress_ok"] else "no"))
        L.append("| test tail-capture (top-5) improves | %+.1f | %+.1f | %s |" % (
            b["tail5"], t["tail5"], "YES" if f["tail_ok"] else "no"))
        L.append("")
        L.append("_%s: %s_" % (famlabel,
                               "PASS (all four criteria met)" if f["ship"]
                               else "FAIL (at least one criterion not met)"))
        L.append("")
    L.append("**Verdict for %s: %s**" % (
        key.upper(),
        "SHIP a trailing exit (see passing arm above)" if r["ship"]
        else "DO NOT SHIP -- keep static exits (report only)"))
    L.append("")

    # Honest reading beyond the pre-registered rule (which deliberately does NOT
    # gate on maxDD or effect size). These do not change the verdict; they arm
    # the follow-up operator decision.
    L.append("### Reading beyond the pre-registered rule")
    L.append("")
    notes = []
    cha, tr = r["fams"]["cha"], r["fams"]["tr"]
    cha_dd, base_dd = cha["test"]["max_dd"], b["max_dd"]
    if cha_dd < base_dd * 1.10:            # >10% deeper (both negative)
        notes.append("The best chandelier arm (k=%.1f) DEEPENS test drawdown "
                     "(%.1f vs baseline %.1f): it buys tail capture with a deeper "
                     "equity dip. maxDD is intentionally NOT a ship criterion, so "
                     "weigh it explicitly before wiring." % (cha["best_k"], cha_dd, base_dd))
    else:
        notes.append("The best chandelier arm (k=%.1f) does not worsen test "
                     "drawdown (%.1f vs baseline %.1f)." % (cha["best_k"], cha_dd, base_dd))
    for fam, lbl in (("cha", "chandelier"), ("tr", "time-replace")):
        f = r["fams"][fam]
        pf, bpf = f["test"]["pf"], b["pf"]
        if f["ship"] and pf == pf and bpf == bpf and pf != float("inf") \
                and bpf != float("inf") and (pf - bpf) < 0.03:
            notes.append("The %s PF gain is MARGINAL (+%.2f on test) -- a genuine "
                         "pass but a small effect; the tail-capture jump is the "
                         "real story." % (lbl, pf - bpf))
    fx2 = time_exit_forensics(sigs)
    if fx2["time_tot"]:
        notes.append("Arm (b) touches only the %d TIME flat-closes (%.0f%% winners) "
                     "-- a small slice of the %d-trade book, so its book-level PF "
                     "move is necessarily small even when it helps that subset."
                     % (fx2["time_tot"], 100 * fx2["time_win_frac"], len(sigs)))
    for nline in notes:
        L.append("* " + nline)
    L.append("")
    return "\n".join(L), r


HEADER = """# Chandelier trailing-exit study -- S94 / S100 (opt15 Task 14)

Date: 2026-07-30  |  Branch: feat/optimization-15  |  Author: Claude Code (opt15)

## Purpose

S94 is a tail-carried edge (29% win rate; the P&L lives in a handful of big
winners) and the M3 12-month forensics found that TIME-backstop flat-closes were
64% winners -- "trail, don't cut". This study measures whether a CHANDELIER
trailing exit beats the deployed static SL/TP/TIME exits for S94 and S100 before
any wiring is considered. NO live code changes here regardless of the verdict --
the report IS the deliverable (arming is a follow-up operator decision).

## Method

* **Data**: OANDA XAU_USD M1 mids, `ClaudeTradingRD/m3_scalper/xau_m1_3y.parquet`
  (2023-07..2026-07). Resampled with the house `label="left", closed="left"`
  convention. Replay period 2025-01..2026-07 (train 2025, test 2026); module
  state is warmed from the parquet history preceding 2025-01.
* **Replay**: the REAL `get_signal` of each module is called with a trailing
  window matched to its live depth -- S94 on closed M5 bars (W5M=1500), S100 on
  closed M1 bars (W1M={depth} = MIN_BARS_1M) within its trading hours -- and
  module state carried across calls exactly as the live runner does. The
  baseline signal set is harvested ONCE per strategy; every exit arm is then a
  cheap re-simulation over the SAME signals' M1 path, so n is identical across
  arms and the comparison isolates exit quality.
* **Fill model** (shared, conservative): entry at the signal's `entry_price`;
  exits walk the M1 path from the first bar strictly after the entry bar's close
  (uniform `_scan_start` across every arm -- no intra-entry-bar look-ahead). SL
  is checked before TP (a bar touching both is a stop-out). The chandelier stop
  for bar i uses the high/low-water mark INCLUDING bar i and is checked only
  against bar i+1 (no intra-bar look-ahead), rounded to 2dp exactly as the live
  `Numeric(25,2)` trail column stores it. Friction is one per-round-trip point
  charge: **0.45pt base, 0.80pt stress**.
* **ATR**: ATR(14) on each strategy's working timeframe (M5 for S94, M3 for
  S100 -- the exact `_shared_ta.atr_last` S100 uses internally), **FROZEN at
  entry**. Frozen (not rolling) is chosen for determinism and a clean k
  comparison; the trail width `k*ATR` is constant for a trade's life. Rolling
  ATR is a documented follow-up.
* **Arms**: baseline (static SL/TP/TIME); **chandelier k in {{2.0,2.5,3.0}}**
  (arm a: SL until +1R, then trail = HW - k*ATR, mirror for shorts, ratcheting,
  no fixed TP, TIME backstop retained); **time-replace k in {{2.0,2.5,3.0}}**
  (arm b: baseline until max_hold, then a would-be TIME flat-close becomes a
  chandelier trail -- isolates the TIME subset).

### How this differs from the currently-wired live trail (not a bug)

`base.Signal.trailing` + the monitor's `TRAILING_STOPLOSS_POINTS` path already
exist, but the wired default trails at a FIXED distance == the initial risk R
from tick 1. This study measures an ATR-scaled distance that ACTIVATES only
after +1R (the classic chandelier). Wiring any winning config would require
adding the +1R-activation gate to the monitor -- a separate task.

### Limitations (read before trusting absolute numbers)

* Per-signal quality study: execution-layer effects (cooldown,
  max_concurrent_positions, entry-manager gates, broker fill drift) are NOT
  modelled, so absolute counts/PF differ from live. The ARM comparison is
  apples-to-apples (identical signal set) -- which is what the decision rule
  turns on.
* Offline fills use the module's own offline-replay probe (S94 M5-fallback;
  S100 the freshest 1m bar). Consistent across arms.
* S100's window is the MIN_BARS_1M floor (EMA200 warm-up on ~214 M3 bars);
  this affects all arms identically.

## Decision rule (PRE-REGISTERED -- fixed before results were seen)

For each family (chandelier, time-replace) the best k is chosen on the **train**
period (2025) by PF, then judged on the **test** period (2026). A family SHIPS
only if ALL FOUR hold on test:

1. test-period Profit Factor **improves** (arm PF > baseline PF), AND
2. test-period average points/trade **improves**, AND
3. the stress-cost (0.80pt) test PF **does not degrade** (arm >= baseline), AND
4. test-period **tail capture** (sum of top-5 winners) **improves** -- the
   trail-specific criterion; the hypothesis is fundamentally about the tail.

A strategy's verdict is SHIP if EITHER family passes. If neither passes, keep the
static exits -- the report is the deliverable. Wiring, even on a PASS, is a
separate operator decision (this task changes no strategy code).

---
""".format(depth=STRATS["s100"]["depth"])


def build_report(results) -> str:
    parts = [HEADER]
    verdicts = {}
    for key in ("s94", "s100"):
        sec, r = strategy_section(key, results[key])
        parts.append(sec)
        verdicts[key] = r
    parts.append("## Summary verdict")
    parts.append("")
    for key in ("s94", "s100"):
        r = verdicts[key]
        passing = [ARM_LABEL["%s_%.1f" % (fam, r["fams"][fam]["best_k"])]
                   for fam in ("cha", "tr") if r["fams"][fam]["ship"]]
        parts.append("* **%s**: %s%s" % (
            key.upper(),
            "SHIP" if r["ship"] else "NO-SHIP (report only)",
            (" -- passing arm(s): %s" % ", ".join(passing)) if passing else ""))
    parts.append("")
    parts.append("_Generated by strategies/research/trail_exit_study.py; rerun "
                 "to reproduce._")
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
    ap.add_argument("--only", default=None, help="s94 or s100 (smoke test)")
    ap.add_argument("--end", default=None, help="override END (smoke test)")
    ap.add_argument("--dump", default=None,
                    help="harvest --only strategy, pickle records to this path, "
                         "exit (for parallel harvest processes)")
    ap.add_argument("--from-dumps", default=None,
                    help="s94_pkl,s100_pkl -> build report from dumps (no "
                         "harvest); the fast path after parallel --dump runs")
    args = ap.parse_args()

    global END
    if args.end:
        END = pd.Timestamp(args.end)

    # Fast path: assemble the report from two pre-harvested dumps (parallel run).
    if args.from_dumps:
        p94, p100 = [p.strip() for p in args.from_dumps.split(",")]
        with open(p94, "rb") as fh:
            r94 = pickle.load(fh)
        with open(p100, "rb") as fh:
            r100 = pickle.load(fh)
        results = {"s94": r94, "s100": r100}
        report = build_report(results)
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w", encoding="ascii", errors="replace") as fh:
            fh.write(report)
        print("[trail_exit_study] wrote report -> %s" % args.out)
        for key in ("s94", "s100"):
            r = evaluate_rule(results[key])
            print("[VERDICT %s] ship=%s (cha bestk=%.1f ship=%s, tr bestk=%.1f "
                  "ship=%s)" % (key, r["ship"], r["fams"]["cha"]["best_k"],
                                r["fams"]["cha"]["ship"], r["fams"]["tr"]["best_k"],
                                r["fams"]["tr"]["ship"]))
        return 0

    print("[trail_exit_study] loading M1 parquet ...")
    t0 = time.perf_counter()
    m1 = rl.load_m1(args.data)
    print("  loaded %d M1 rows (%s .. %s) in %.0fs" % (
        len(m1), m1["time"].iloc[0], m1["time"].iloc[-1], time.perf_counter() - t0))

    print("[trail_exit_study] resampling M5 ...")
    m5 = rl.resample_ohlc(m1, "5min")
    print("  M5: %d bars" % len(m5))

    m1_arrays = (
        m1["time"].to_numpy().astype("datetime64[ns]").astype("int64"),
        m1["high"].to_numpy(float), m1["low"].to_numpy(float),
        m1["close"].to_numpy(float),
    )

    keys = [args.only] if args.only else ["s94", "s100"]
    results = {}
    for key in keys:
        cfg = STRATS[key]
        print("[trail_exit_study] harvesting %s (%s, depth=%d) ..."
              % (key, cfg["tf"], cfg["depth"]))
        if cfg["tf"] == "M5":
            results[key] = harvest_m5(cfg["mod"], cfg["depth"], m5, m1_arrays,
                                      key, limit=args.limit)
        else:
            results[key] = harvest_m1(cfg["mod"], cfg["depth"], m1, m1_arrays,
                                      key, limit=args.limit)

    if args.dump:
        key = keys[0]
        with open(args.dump, "wb") as fh:
            pickle.dump(results[key], fh)
        print("[trail_exit_study] dumped %d %s records -> %s"
              % (len(results[key]), key, args.dump))
        return 0

    if args.only or args.limit or args.end:
        for key in keys:
            r = evaluate_rule(results[key])
            fx = time_exit_forensics(results[key])
            print("[SMOKE %s] n=%d base_pf=%s | cha bestk=%.1f pf=%s ship=%s | "
                  "tr bestk=%.1f pf=%s ship=%s | TIME=%d (%.0f%% win)"
                  % (key, len(results[key]), _pf(r["base_t"]["pf"]),
                     r["fams"]["cha"]["best_k"], _pf(r["fams"]["cha"]["test"]["pf"]),
                     r["fams"]["cha"]["ship"],
                     r["fams"]["tr"]["best_k"], _pf(r["fams"]["tr"]["test"]["pf"]),
                     r["fams"]["tr"]["ship"], fx["time_tot"],
                     100 * fx["time_win_frac"] if fx["time_tot"] else 0.0))
        return 0

    report = build_report(results)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="ascii", errors="replace") as fh:
        fh.write(report)
    print("[trail_exit_study] wrote report -> %s" % args.out)
    for key in ("s94", "s100"):
        r = evaluate_rule(results[key])
        print("[VERDICT %s] ship=%s (cha bestk=%.1f ship=%s, tr bestk=%.1f ship=%s)"
              % (key, r["ship"], r["fams"]["cha"]["best_k"], r["fams"]["cha"]["ship"],
                 r["fams"]["tr"]["best_k"], r["fams"]["tr"]["ship"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
