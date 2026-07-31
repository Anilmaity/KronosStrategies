# HTF-bias entry filter -- offline validation study (opt15 Task 13)

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

## S94 -- S94 sweep-reversal (TREND)

Live window depth W5M=1500. Harvested 1337 baseline signals (845 in 2025 train, 492 in 2026 test).

Of the baseline signals, 155 (12%) oppose an aligned D1+H4 bias and 385 (29%) oppose the H4-only bias.

### Friction base (0.45 pt/round-trip)

*Train 2025*

| arm         |    n |     PF | avg pts |   maxDD |   WR |
|-------------|------|--------|---------|---------|------|
| baseline    |  845 |   1.11 |  +0.239 |   -194.1 |   30% |
| veto (D1+H4) |  718 |   1.17 |  +0.380 |   -186.8 |   31% |
| half (D1+H4) |  845 |   1.14 |  +0.281 |   -179.0 |   30% |
| veto (H4-only) |  603 |   1.21 |  +0.463 |   -162.3 |   32% |
| half (H4-only) |  845 |   1.15 |  +0.285 |   -169.5 |   30% |

*Test 2026*

| arm         |    n |     PF | avg pts |   maxDD |   WR |
|-------------|------|--------|---------|---------|------|
| baseline    |  492 |   1.12 |  +0.554 |   -247.8 |   29% |
| veto (D1+H4) |  464 |   1.08 |  +0.358 |   -247.8 |   28% |
| half (D1+H4) |  492 |   1.10 |  +0.446 |   -247.8 |   29% |
| veto (H4-only) |  349 |   1.07 |  +0.341 |   -240.2 |   28% |
| half (H4-only) |  492 |   1.10 |  +0.398 |   -210.5 |   29% |

*All 2025-2026*

| arm         |    n |     PF | avg pts |   maxDD |   WR |
|-------------|------|--------|---------|---------|------|
| baseline    | 1337 |   1.11 |  +0.355 |   -247.8 |   29% |
| veto (D1+H4) | 1182 |   1.12 |  +0.371 |   -247.8 |   30% |
| half (D1+H4) | 1337 |   1.11 |  +0.342 |   -247.8 |   29% |
| veto (H4-only) |  952 |   1.13 |  +0.418 |   -240.2 |   30% |
| half (H4-only) | 1337 |   1.12 |  +0.326 |   -210.5 |   29% |

### Friction stress (0.80 pt/round-trip)

*Train 2025*

| arm         |    n |     PF | avg pts |   maxDD |   WR |
|-------------|------|--------|---------|---------|------|
| baseline    |  845 |   0.96 |  -0.111 |   -259.4 |   29% |
| veto (D1+H4) |  718 |   1.01 |  +0.030 |   -237.5 |   30% |
| half (D1+H4) |  845 |   0.98 |  -0.043 |   -247.6 |   29% |
| veto (H4-only) |  603 |   1.05 |  +0.113 |   -188.5 |   31% |
| half (H4-only) |  845 |   0.99 |  -0.015 |   -220.4 |   29% |

*Test 2026*

| arm         |    n |     PF | avg pts |   maxDD |   WR |
|-------------|------|--------|---------|---------|------|
| baseline    |  492 |   1.04 |  +0.204 |   -266.4 |   29% |
| veto (D1+H4) |  464 |   1.00 |  +0.008 |   -268.2 |   28% |
| half (D1+H4) |  492 |   1.02 |  +0.106 |   -266.4 |   29% |
| veto (H4-only) |  349 |   1.00 |  -0.009 |   -275.5 |   28% |
| half (H4-only) |  492 |   1.02 |  +0.099 |   -240.0 |   29% |

*All 2025-2026*

| arm         |    n |     PF | avg pts |   maxDD |   WR |
|-------------|------|--------|---------|---------|------|
| baseline    | 1337 |   1.00 |  +0.005 |   -266.4 |   29% |
| veto (D1+H4) | 1182 |   1.01 |  +0.021 |   -282.2 |   29% |
| half (D1+H4) | 1337 |   1.00 |  +0.012 |   -266.4 |   29% |
| veto (H4-only) |  952 |   1.02 |  +0.068 |   -275.5 |   30% |
| half (H4-only) | 1337 |   1.01 |  +0.027 |   -243.5 |   29% |

### Decision-rule evaluation (D1+H4 veto)

| criterion | baseline | veto | pass? |
|-----------|----------|------|-------|
| test PF improves | 1.12 | 1.08 | no |
| test avg-pts improves | +0.554 | +0.358 | no |
| veto removes < 40% of test trades | n=492 | n=464 (6% removed) | YES |
| stress (0.80pt) test PF not degraded | 1.04 | 1.00 | no |

**Verdict for S94: DO NOT SHIP (report only)**

H4-only confirmation (the bias a live runner can actually compute from resampled w15m -> H4; D1 depth is unreachable live): test PF 1.12 -> 1.07, avg +0.554 -> +0.341, stress PF 1.00. Edge holds H4-only: no.

## S99 -- S99 MSS+FVG (REVERSAL)

Live window depth W5M=160. Harvested 1856 baseline signals (1196 in 2025 train, 660 in 2026 test).

Of the baseline signals, 267 (14%) oppose an aligned D1+H4 bias and 569 (31%) oppose the H4-only bias.

### Friction base (0.45 pt/round-trip)

*Train 2025*

| arm         |    n |     PF | avg pts |   maxDD |   WR |
|-------------|------|--------|---------|---------|------|
| baseline    | 1196 |   1.10 |  +0.157 |   -145.0 |   51% |
| veto (D1+H4) |  989 |   1.02 |  +0.029 |   -117.3 |   51% |
| half (D1+H4) | 1196 |   1.06 |  +0.090 |   -126.0 |   51% |
| veto (H4-only) |  830 |   1.01 |  +0.016 |   -111.4 |   50% |
| half (H4-only) | 1196 |   1.06 |  +0.084 |   -114.4 |   51% |

*Test 2026*

| arm         |    n |     PF | avg pts |   maxDD |   WR |
|-------------|------|--------|---------|---------|------|
| baseline    |  660 |   1.09 |  +0.328 |   -278.5 |   48% |
| veto (D1+H4) |  600 |   1.05 |  +0.171 |   -278.5 |   48% |
| half (D1+H4) |  660 |   1.07 |  +0.242 |   -278.5 |   48% |
| veto (H4-only) |  457 |   1.05 |  +0.171 |   -203.3 |   47% |
| half (H4-only) |  660 |   1.08 |  +0.223 |   -240.9 |   48% |

*All 2025-2026*

| arm         |    n |     PF | avg pts |   maxDD |   WR |
|-------------|------|--------|---------|---------|------|
| baseline    | 1856 |   1.09 |  +0.218 |   -278.5 |   50% |
| veto (D1+H4) | 1589 |   1.04 |  +0.082 |   -278.5 |   50% |
| half (D1+H4) | 1856 |   1.07 |  +0.144 |   -278.5 |   50% |
| veto (H4-only) | 1287 |   1.03 |  +0.071 |   -203.3 |   49% |
| half (H4-only) | 1856 |   1.07 |  +0.134 |   -240.9 |   50% |

### Friction stress (0.80 pt/round-trip)

*Train 2025*

| arm         |    n |     PF | avg pts |   maxDD |   WR |
|-------------|------|--------|---------|---------|------|
| baseline    | 1196 |   0.89 |  -0.193 |   -459.2 |   48% |
| veto (D1+H4) |  989 |   0.82 |  -0.321 |   -371.6 |   49% |
| half (D1+H4) | 1196 |   0.86 |  -0.230 |   -412.1 |   48% |
| veto (H4-only) |  830 |   0.81 |  -0.334 |   -295.5 |   48% |
| half (H4-only) | 1196 |   0.86 |  -0.213 |   -374.1 |   48% |

*Test 2026*

| arm         |    n |     PF | avg pts |   maxDD |   WR |
|-------------|------|--------|---------|---------|------|
| baseline    |  660 |   0.99 |  -0.022 |   -317.4 |   48% |
| veto (D1+H4) |  600 |   0.95 |  -0.179 |   -317.4 |   48% |
| half (D1+H4) |  660 |   0.97 |  -0.092 |   -317.4 |   48% |
| veto (H4-only) |  457 |   0.95 |  -0.179 |   -258.5 |   47% |
| half (H4-only) |  660 |   0.98 |  -0.073 |   -274.2 |   48% |

*All 2025-2026*

| arm         |    n |     PF | avg pts |   maxDD |   WR |
|-------------|------|--------|---------|---------|------|
| baseline    | 1856 |   0.95 |  -0.132 |   -459.2 |   48% |
| veto (D1+H4) | 1589 |   0.89 |  -0.268 |   -491.2 |   49% |
| half (D1+H4) | 1856 |   0.92 |  -0.181 |   -412.1 |   48% |
| veto (H4-only) | 1287 |   0.88 |  -0.279 |   -414.4 |   48% |
| half (H4-only) | 1856 |   0.92 |  -0.163 |   -374.1 |   48% |

### Decision-rule evaluation (D1+H4 veto)

| criterion | baseline | veto | pass? |
|-----------|----------|------|-------|
| test PF improves | 1.09 | 1.05 | no |
| test avg-pts improves | +0.328 | +0.171 | no |
| veto removes < 40% of test trades | n=660 | n=600 (9% removed) | YES |
| stress (0.80pt) test PF not degraded | 0.99 | 0.95 | no |

**Verdict for S99: DO NOT SHIP (report only)**

H4-only confirmation (the bias a live runner can actually compute from resampled w15m -> H4; D1 depth is unreachable live): test PF 1.09 -> 1.05, avg +0.328 -> +0.171, stress PF 0.95. Edge holds H4-only: no.

## Summary verdict

* **S94**: NO-SHIP (report only)
* **S99**: NO-SHIP (report only)

_Generated by strategies/research/htf_bias_study.py; rerun to reproduce._
