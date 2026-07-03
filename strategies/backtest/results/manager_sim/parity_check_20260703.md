# Parity check — sim regime timeline vs live strategy_manager service

**Date:** 2026-07-03 (Task 6 Step 3 of plan 2026-07-02-manager-backtest-sim)
**Window compared:** 2026-07-02 10:00–14:59 UTC (60 five-minute marks, the overlap
between the live container's log retention and the simulated data range; the
sim side comes from a supplemental run 2026-06-26 → 2026-07-03 since the base
run's `--end 2026-07-02` is exclusive).

**Method:** live `[TICK]` lines from `kronos-strategy_manager-1` on the
algorobos box (`d1= h4= vol= trend= session=`) vs the sim regime timeline
(`manager_sim_20260703_0000_gated_regime.jsonl`), field-by-field at each
overlapping 5-minute mark.

**Result: 288/300 field-comparisons agree = 96.0% (spec bar: ≥95% → PASS).**

- d1_bias, h4_bias, trend_regime, session: **100% agreement** (60/60 each).
- vol_regime: 48/60 — the 12 mismatches are one contiguous block
  (10:00–10:55) where the box read LOW and the sim NORMAL. The H1 ATR
  percentile sat at the 25.0 LOW/NORMAL boundary during that hour; the live
  service's OANDA fetch window (rolling 30 days at fetch time) and the sim's
  fixed 760-bar slice differ by ~1.7 days of distribution, which flips the
  percentile at the boundary. Not systematic: agreement is exact before and
  after the block, and no other field ever diverged.

**Conclusion:** the simulator reproduces the production regime engine's
classifications within boundary jitter. Gating decisions driven by vol at an
exact percentile boundary carry ±1 bucket uncertainty — one more reason the
sensitivity grid (vol thresholds ±5) matters before a permanent verdict.

Scoring (Task 6 Step 4): backtest-expert evaluate_backtest.py on the gated
combined book (109 trades, WR 53.2, avg win 0.603%, avg loss 0.459%, max DD
6.43% of $5k, slippage-tested) → **54/100 "Refine"**, red flags: short test
window (0.25y actual; script minimum granularity 1y), 8 parameters
(curve-fitting risk). Consistent with the report's PROVISIONAL verdict and
paper-first recommendation. Reports: backtest_eval_2026-07-03_053602.{json,md}.
