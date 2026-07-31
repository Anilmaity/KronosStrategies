# Chandelier trailing-exit study -- S94 / S100 (opt15 Task 14)

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
  closed M1 bars (W1M=642 = MIN_BARS_1M) within its trading hours -- and
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
* **Arms**: baseline (static SL/TP/TIME); **chandelier k in {2.0,2.5,3.0}**
  (arm a: SL until +1R, then trail = HW - k*ATR, mirror for shorts, ratcheting,
  no fixed TP, TIME backstop retained); **time-replace k in {2.0,2.5,3.0}**
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

## S94 -- S94 sweep-reversal (TREND, tail-carried 29% WR)

Working TF M5, window depth 1500. Harvested 1337 baseline signals (845 in 2025 train, 492 in 2026 test). n is CONSTANT across arms -- trailing changes exits only, never which signals fire.

Baseline exit mix: SL=941, TIME=10, TP=386.
TIME flat-closes: 10, of which 7 were winners (70%) -- the 'trail don't cut' subset arm (b) targets.

### Friction base (0.45 pt/round-trip)

*Train 2025*

| arm                |    n |     PF | avg pts |     maxDD | top5 wins |   WR |
|--------------------|------|--------|---------|-----------|-----------|------|
| baseline           |  845 |   1.11 |  +0.239 |    -194.1 |    +204.0 |   30% |
| chandelier k=2.0   |  845 |   0.96 |  -0.070 |    -238.3 |    +205.9 |   30% |
| chandelier k=2.5   |  845 |   1.08 |  +0.176 |    -228.5 |    +315.5 |   27% |
| chandelier k=3.0   |  845 |   1.05 |  +0.103 |    -215.6 |    +345.7 |   23% |
| time-replace k=2.0 |  845 |   1.12 |  +0.257 |    -194.1 |    +198.4 |   30% |
| time-replace k=2.5 |  845 |   1.11 |  +0.247 |    -194.1 |    +194.2 |   30% |
| time-replace k=3.0 |  845 |   1.11 |  +0.237 |    -194.1 |    +190.1 |   30% |

*Test 2026*

| arm                |    n |     PF | avg pts |     maxDD | top5 wins |   WR |
|--------------------|------|--------|---------|-----------|-----------|------|
| baseline           |  492 |   1.12 |  +0.554 |    -247.8 |    +420.3 |   29% |
| chandelier k=2.0   |  492 |   1.39 |  +1.384 |    -159.0 |    +540.1 |   33% |
| chandelier k=2.5   |  492 |   1.31 |  +1.218 |    -238.0 |    +537.5 |   29% |
| chandelier k=3.0   |  492 |   1.33 |  +1.375 |    -298.3 |    +625.9 |   27% |
| time-replace k=2.0 |  492 |   1.13 |  +0.588 |    -246.4 |    +440.9 |   29% |
| time-replace k=2.5 |  492 |   1.12 |  +0.543 |    -249.9 |    +433.9 |   29% |
| time-replace k=3.0 |  492 |   1.11 |  +0.493 |    -253.3 |    +426.8 |   29% |

*All 2025-2026*

| arm                |    n |     PF | avg pts |     maxDD | top5 wins |   WR |
|--------------------|------|--------|---------|-----------|-----------|------|
| baseline           | 1337 |   1.11 |  +0.355 |    -247.8 |    +420.3 |   29% |
| chandelier k=2.0   | 1337 |   1.18 |  +0.465 |    -238.3 |    +540.1 |   31% |
| chandelier k=2.5   | 1337 |   1.20 |  +0.559 |    -238.0 |    +537.5 |   27% |
| chandelier k=3.0   | 1337 |   1.19 |  +0.571 |    -298.3 |    +630.2 |   24% |
| time-replace k=2.0 | 1337 |   1.12 |  +0.379 |    -246.4 |    +440.9 |   29% |
| time-replace k=2.5 | 1337 |   1.11 |  +0.356 |    -249.9 |    +433.9 |   29% |
| time-replace k=3.0 | 1337 |   1.11 |  +0.331 |    -253.3 |    +426.8 |   29% |

### Friction stress (0.80 pt/round-trip)

*Train 2025*

| arm                |    n |     PF | avg pts |     maxDD | top5 wins |   WR |
|--------------------|------|--------|---------|-----------|-----------|------|
| baseline           |  845 |   0.96 |  -0.111 |    -259.4 |    +202.2 |   29% |
| chandelier k=2.0   |  845 |   0.81 |  -0.420 |    -431.0 |    +204.1 |   28% |
| chandelier k=2.5   |  845 |   0.93 |  -0.174 |    -386.5 |    +313.8 |   25% |
| chandelier k=3.0   |  845 |   0.90 |  -0.247 |    -369.8 |    +343.9 |   22% |
| time-replace k=2.0 |  845 |   0.96 |  -0.093 |    -265.0 |    +196.6 |   30% |
| time-replace k=2.5 |  845 |   0.96 |  -0.103 |    -269.1 |    +192.5 |   30% |
| time-replace k=3.0 |  845 |   0.95 |  -0.113 |    -273.3 |    +188.3 |   30% |

*Test 2026*

| arm                |    n |     PF | avg pts |     maxDD | top5 wins |   WR |
|--------------------|------|--------|---------|-----------|-----------|------|
| baseline           |  492 |   1.04 |  +0.204 |    -266.4 |    +418.5 |   29% |
| chandelier k=2.0   |  492 |   1.28 |  +1.034 |    -212.0 |    +538.3 |   32% |
| chandelier k=2.5   |  492 |   1.21 |  +0.868 |    -262.8 |    +535.7 |   28% |
| chandelier k=3.0   |  492 |   1.23 |  +1.025 |    -365.5 |    +624.1 |   26% |
| time-replace k=2.0 |  492 |   1.05 |  +0.238 |    -265.0 |    +439.2 |   29% |
| time-replace k=2.5 |  492 |   1.04 |  +0.193 |    -268.4 |    +432.1 |   29% |
| time-replace k=3.0 |  492 |   1.03 |  +0.143 |    -271.9 |    +425.1 |   29% |

*All 2025-2026*

| arm                |    n |     PF | avg pts |     maxDD | top5 wins |   WR |
|--------------------|------|--------|---------|-----------|-----------|------|
| baseline           | 1337 |   1.00 |  +0.005 |    -266.4 |    +418.5 |   29% |
| chandelier k=2.0   | 1337 |   1.04 |  +0.115 |    -431.0 |    +538.3 |   29% |
| chandelier k=2.5   | 1337 |   1.07 |  +0.209 |    -386.5 |    +535.7 |   26% |
| chandelier k=3.0   | 1337 |   1.07 |  +0.221 |    -369.8 |    +628.5 |   23% |
| time-replace k=2.0 | 1337 |   1.01 |  +0.029 |    -265.0 |    +439.2 |   29% |
| time-replace k=2.5 | 1337 |   1.00 |  +0.006 |    -269.1 |    +432.1 |   29% |
| time-replace k=3.0 | 1337 |   0.99 |  -0.019 |    -273.3 |    +425.1 |   29% |

### Decision-rule evaluation (best-k chosen on TRAIN, judged on TEST)

**Chandelier (arm a) -- best k on train = 2.5**

| criterion | baseline | chandelier k=2.5 | pass? |
|-----------|----------|------|-------|
| test PF improves | 1.12 | 1.31 | YES |
| test avg-pts improves | +0.554 | +1.218 | YES |
| stress (0.80pt) test PF not degraded | 1.04 | 1.21 | YES |
| test tail-capture (top-5) improves | +420.3 | +537.5 | YES |

_Chandelier (arm a): PASS (all four criteria met)_

**Time-replace (arm b) -- best k on train = 2.0**

| criterion | baseline | time-replace k=2.0 | pass? |
|-----------|----------|------|-------|
| test PF improves | 1.12 | 1.13 | YES |
| test avg-pts improves | +0.554 | +0.588 | YES |
| stress (0.80pt) test PF not degraded | 1.04 | 1.05 | YES |
| test tail-capture (top-5) improves | +420.3 | +440.9 | YES |

_Time-replace (arm b): PASS (all four criteria met)_

**Verdict for S94: SHIP a trailing exit (see passing arm above)**

### Reading beyond the pre-registered rule

* The best chandelier arm (k=2.5) does not worsen test drawdown (-238.0 vs baseline -247.8).
* The time-replace PF gain is MARGINAL (+0.01 on test) -- a genuine pass but a small effect; the tail-capture jump is the real story.
* Arm (b) touches only the 10 TIME flat-closes (70% winners) -- a small slice of the 1337-trade book, so its book-level PF move is necessarily small even when it helps that subset.

## S100 -- S100 M3 combo scalper (spec v3)

Working TF M1, window depth 642. Harvested 6541 baseline signals (4198 in 2025 train, 2343 in 2026 test). n is CONSTANT across arms -- trailing changes exits only, never which signals fire.

Baseline exit mix: EOD=1, SL=4254, TIME=207, TP=2079.
TIME flat-closes: 207, of which 161 were winners (78%) -- the 'trail don't cut' subset arm (b) targets.

### Friction base (0.45 pt/round-trip)

*Train 2025*

| arm                |    n |     PF | avg pts |     maxDD | top5 wins |   WR |
|--------------------|------|--------|---------|-----------|-----------|------|
| baseline           | 4198 |   0.89 |  -0.176 |    -928.6 |    +127.4 |   34% |
| chandelier k=2.0   | 4198 |   0.93 |  -0.110 |    -706.1 |    +229.3 |   27% |
| chandelier k=2.5   | 4198 |   0.93 |  -0.115 |    -707.2 |    +232.9 |   25% |
| chandelier k=3.0   | 4198 |   0.96 |  -0.070 |    -675.2 |    +316.7 |   24% |
| time-replace k=2.0 | 4198 |   0.90 |  -0.173 |    -940.7 |    +127.4 |   33% |
| time-replace k=2.5 | 4198 |   0.89 |  -0.179 |    -980.7 |    +159.2 |   33% |
| time-replace k=3.0 | 4198 |   0.89 |  -0.190 |   -1013.3 |    +173.3 |   33% |

*Test 2026*

| arm                |    n |     PF | avg pts |     maxDD | top5 wins |   WR |
|--------------------|------|--------|---------|-----------|-----------|------|
| baseline           | 2343 |   1.03 |  +0.103 |    -485.6 |    +313.0 |   35% |
| chandelier k=2.0   | 2343 |   1.08 |  +0.239 |    -675.7 |    +735.7 |   29% |
| chandelier k=2.5   | 2343 |   1.05 |  +0.166 |    -938.5 |    +793.7 |   26% |
| chandelier k=3.0   | 2343 |   1.04 |  +0.137 |    -982.1 |    +737.7 |   24% |
| time-replace k=2.0 | 2343 |   1.04 |  +0.127 |    -514.8 |    +356.6 |   34% |
| time-replace k=2.5 | 2343 |   1.07 |  +0.220 |    -441.2 |    +444.5 |   34% |
| time-replace k=3.0 | 2343 |   1.05 |  +0.175 |    -466.3 |    +420.8 |   33% |

*All 2025-2026*

| arm                |    n |     PF | avg pts |     maxDD | top5 wins |   WR |
|--------------------|------|--------|---------|-----------|-----------|------|
| baseline           | 6541 |   0.97 |  -0.076 |    -930.7 |    +313.0 |   34% |
| chandelier k=2.0   | 6541 |   1.01 |  +0.015 |    -706.1 |    +735.7 |   27% |
| chandelier k=2.5   | 6541 |   0.99 |  -0.014 |    -938.5 |    +793.7 |   25% |
| chandelier k=3.0   | 6541 |   1.00 |  +0.004 |    -982.1 |    +737.7 |   24% |
| time-replace k=2.0 | 6541 |   0.97 |  -0.066 |    -942.4 |    +356.6 |   34% |
| time-replace k=2.5 | 6541 |   0.98 |  -0.036 |    -980.7 |    +444.5 |   33% |
| time-replace k=3.0 | 6541 |   0.97 |  -0.060 |   -1013.3 |    +420.8 |   33% |

### Friction stress (0.80 pt/round-trip)

*Train 2025*

| arm                |    n |     PF | avg pts |     maxDD | top5 wins |   WR |
|--------------------|------|--------|---------|-----------|-----------|------|
| baseline           | 4198 |   0.72 |  -0.526 |   -2288.1 |    +125.6 |   34% |
| chandelier k=2.0   | 4198 |   0.75 |  -0.460 |   -2056.0 |    +227.6 |   25% |
| chandelier k=2.5   | 4198 |   0.77 |  -0.465 |   -2037.6 |    +231.1 |   23% |
| chandelier k=3.0   | 4198 |   0.80 |  -0.420 |   -1896.1 |    +315.0 |   23% |
| time-replace k=2.0 | 4198 |   0.72 |  -0.523 |   -2293.0 |    +125.6 |   33% |
| time-replace k=2.5 | 4198 |   0.72 |  -0.529 |   -2321.4 |    +157.5 |   33% |
| time-replace k=3.0 | 4198 |   0.72 |  -0.540 |   -2364.8 |    +171.6 |   33% |

*Test 2026*

| arm                |    n |     PF | avg pts |     maxDD | top5 wins |   WR |
|--------------------|------|--------|---------|-----------|-----------|------|
| baseline           | 2343 |   0.93 |  -0.247 |    -932.5 |    +311.3 |   34% |
| chandelier k=2.0   | 2343 |   0.97 |  -0.111 |   -1154.5 |    +734.0 |   27% |
| chandelier k=2.5   | 2343 |   0.95 |  -0.184 |   -1418.4 |    +792.0 |   25% |
| chandelier k=3.0   | 2343 |   0.94 |  -0.213 |   -1461.9 |    +735.9 |   24% |
| time-replace k=2.0 | 2343 |   0.94 |  -0.223 |    -891.7 |    +354.8 |   34% |
| time-replace k=2.5 | 2343 |   0.96 |  -0.130 |    -660.9 |    +442.7 |   34% |
| time-replace k=3.0 | 2343 |   0.95 |  -0.175 |    -744.8 |    +419.0 |   33% |

*All 2025-2026*

| arm                |    n |     PF | avg pts |     maxDD | top5 wins |   WR |
|--------------------|------|--------|---------|-----------|-----------|------|
| baseline           | 6541 |   0.83 |  -0.426 |   -2956.8 |    +311.3 |   34% |
| chandelier k=2.0   | 6541 |   0.86 |  -0.335 |   -2534.8 |    +734.0 |   26% |
| chandelier k=2.5   | 6541 |   0.86 |  -0.364 |   -2765.3 |    +792.0 |   24% |
| chandelier k=3.0   | 6541 |   0.87 |  -0.346 |   -2640.2 |    +735.9 |   23% |
| time-replace k=2.0 | 6541 |   0.83 |  -0.416 |   -2898.6 |    +354.8 |   34% |
| time-replace k=2.5 | 6541 |   0.84 |  -0.386 |   -2676.6 |    +442.7 |   33% |
| time-replace k=3.0 | 6541 |   0.84 |  -0.410 |   -2814.0 |    +419.0 |   33% |

### Decision-rule evaluation (best-k chosen on TRAIN, judged on TEST)

**Chandelier (arm a) -- best k on train = 3.0**

| criterion | baseline | chandelier k=3.0 | pass? |
|-----------|----------|------|-------|
| test PF improves | 1.03 | 1.04 | YES |
| test avg-pts improves | +0.103 | +0.137 | YES |
| stress (0.80pt) test PF not degraded | 0.93 | 0.94 | YES |
| test tail-capture (top-5) improves | +313.0 | +737.7 | YES |

_Chandelier (arm a): PASS (all four criteria met)_

**Time-replace (arm b) -- best k on train = 2.0**

| criterion | baseline | time-replace k=2.0 | pass? |
|-----------|----------|------|-------|
| test PF improves | 1.03 | 1.04 | YES |
| test avg-pts improves | +0.103 | +0.127 | YES |
| stress (0.80pt) test PF not degraded | 0.93 | 0.94 | YES |
| test tail-capture (top-5) improves | +313.0 | +356.6 | YES |

_Time-replace (arm b): PASS (all four criteria met)_

**Verdict for S100: SHIP a trailing exit (see passing arm above)**

### Reading beyond the pre-registered rule

* The best chandelier arm (k=3.0) DEEPENS test drawdown (-982.1 vs baseline -485.6): it buys tail capture with a deeper equity dip. maxDD is intentionally NOT a ship criterion, so weigh it explicitly before wiring.
* The chandelier PF gain is MARGINAL (+0.01 on test) -- a genuine pass but a small effect; the tail-capture jump is the real story.
* The time-replace PF gain is MARGINAL (+0.01 on test) -- a genuine pass but a small effect; the tail-capture jump is the real story.
* Arm (b) touches only the 207 TIME flat-closes (78% winners) -- a small slice of the 6541-trade book, so its book-level PF move is necessarily small even when it helps that subset.

## Summary verdict

* **S94**: SHIP -- passing arm(s): chandelier k=2.5, time-replace k=2.0
* **S100**: SHIP -- passing arm(s): chandelier k=3.0, time-replace k=2.0

_Generated by strategies/research/trail_exit_study.py; rerun to reproduce._
