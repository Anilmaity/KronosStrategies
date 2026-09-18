# Execution / cost model from 24 months of S5 bid/ask — result (2026-09-18)

Pre-registration: `PROTOCOL.md` (two recorded changes, both after the first run, both listed
there). Scripts `01_spread.py … 04_cost_model.py`; raw output `results/0N_*.txt`; every number
below is copied from those files. Data: 8,487,720 S5 bars 2024-09-01 → 2026-09-17 (the QA'd
cache minus the 2025-12-25 23:00–23:15 spike); trades: all 45,537 Stage-1 entries of the 15
xau2y strategies, re-resolved on the quote with a vectorised replica of `lab/s5exit.resolve()`
that reproduces the existing `results/s5exit/` outputs on 100.000 % (c03) and 99.975 % (s14,
the one difference is a reference TIME exit) of trades. TRAIN < 2025-12-01 ≤ TEST throughout.

## The question
What does execution cost on XAU_USD as a function of UTC hour and stop distance, measured —
and should the campaign's flat 0.45 "base" / 0.80 "stress" convention survive?

## 1. Spread

**By hour (all weekdays, `results/spread_hour.csv`)**: median 0.58–0.61 from 07 to 19 UTC,
0.64–0.67 in Asia (00–05), **0.73–0.77 at 21–23**. p95 0.96–1.04 in the core, 1.41–1.51 at
21–23; p99 2.2–4.8 in the core, 5.0–6.3 at 21–23. Weekday matters only at the edges: Friday
21:00 p95 2.25 vs 1.23–1.34 Mon–Thu; Monday 00–01 p95 1.35–1.43 vs 1.12–1.21 (the table by
hour × weekday is in `results/spread_hour_weekday.csv`).

**By month — it drifts, but with the price, not with time.** Monthly median: 0.39 (2024-09) →
0.53 (2025-01) → 0.77 (2025-05) → 0.57 (2025-07) → 0.99 (2026-01) → 0.54 (2026-09). In basis
points of the gold price it is 1.24–2.34 bp with no trend (slope −0.16 bp/yr, corr −0.37; in
points +0.13 pts/yr, corr +0.53 — the point series simply follows gold 2,570 → 5,000 → 4,370).
TRAIN months average median 0.572 (p95 0.780); TEST 0.730 (p95 1.168). **A flat cost in points
was therefore never one number: the same 0.45 was 1.2 spreads in 2024-09 and 0.45 spreads in
2026-01.**

**Daily break** (`results/spread_daily_break.csv`): the last five minutes before the 21:00 close
widen to p50 0.81 / p95 2.65 (21:55); the 22:04–22:10 reopen is p50 0.84–0.91, p95 1.8–2.1,
**p99 4–5**, back to p50 0.74 / p95 1.4 by 22:20. **Sunday open**: 22:05 p50 1.155, p95 3.96,
p99 5.00; below 0.90 median only from ~22:18; the whole 22:00–23:00 Sunday hour is p50 0.84 /
p95 2.06 vs 0.76 / 1.38 on weekdays.

**Top-1 % tail**: p99 1.57, p99.9 3.31, max 10.00 (the feed caps quotes at 5.00 / 10.00; 2026-01-30
19:02:50). 31.6 % of the tail bars are 21–23 UTC, 25.9 % are 07–16 UTC; by weekday Friday 29.3 %,
Monday 21.2 %, Thursday 19.0 %.

**Widening events** (spread > 3 × the hour's median; 43,923 bars = 0.517 % of all; runs merged
at ≤ 60 s → **1,817 events**, `results/events.csv`):

| class | events | dur p50 / p90 / max (s) | max-spread p50 | share of widened bars |
|---|---|---|---|---|
| unscheduled | 1,433 | 20 / 329 / 16,475 | 2.18 | 83.0 % |
| break (20:55–22:10, Sunday open) | 208 | 35 / 311 / 6,670 | 2.81 | 12.3 % |
| scheduled (08:30 / 10:00 / 14:00 ET ± [−1, +5] min) | 160 | **5** / 95 / 7,400 | 2.45 | 4.5 % |
| fomc (Wednesday 13:59–14:01 ET) | 16 | 15 / 120 / 160 | 2.99 | 0.2 % |

Scheduled releases are short: median 5 s, 90 % over within 95 s; 103 of the 160 are the 08:30 ET
slot (12:30 UTC in summer, 13:30 in winter — the topic's UTC times are the EDT values). The
data-derived FOMC calendar has 16 Wednesdays at 42/49-day spacing (2024-12-18, 2025-01-29,
03-19, 05-07, 06-18, 07-30, 09-17, 10-29, 12-10, 2026-01-28, 04-29, 06-17, 07-29, 09-16) plus
two 21-day extras (2025-04-09 tariff pause, 2025-10-08 minutes) — readable without an external
calendar. **In 07–16 UTC, 89.5 % of widened bars are unscheduled** (511 of 663 events); the
long events are all the 2026-01-29 → 02-02 gold crash (16,475 s = 4.6 h at 10.00 max) and
2025-11-28 09:43 (3.6 h at 5.00). A news blackout removes the short, frequent class, not the
bars that matter.

## 2. Slippage proxy: 5-second jumps (`results/jumps_hour.csv`, `jumps_slippage_hour.csv`)

98.40 % of consecutive bar pairs are exactly 5 s apart; the rest (holes where no tick printed)
are excluded from the jump statistics. |c_t − c_{t−1}| by hour: mean 0.17 (04 UTC) → 0.40 (13–14);
p99 1.19 → 2.46; p99.9 2.9 (04) → 5.0–6.0 (13–15).

**Gap-through probability per 5-s bar, P(J > d), ×10⁻³, and expected count per trading hour:**

| hour | P(J>1) | P(J>2) | P(J>3) | P(J>5) | P(J>10) | jumps > 3 pt per hour |
|---|---|---|---|---|---|---|
| 04 (quietest) | 15.0 | 2.6 | 0.87 | 0.14 | 0.000 | 0.6 |
| 07–11 | 24–31 | 4.4–5.7 | 1.4–2.1 | 0.29–0.55 | 0.005–0.07 | 1.0–1.5 |
| 12 | 48.3 | 9.1 | 2.8 | 0.63 | 0.07 | 2.0 |
| 13–14 (busiest) | 84–87 | 16–17 | 5.0–5.7 | 1.0 | 0.05 | 3.6–4.1 |
| 15 | 65.4 | 14.0 | 5.5 | 1.57 | 0.26 | 3.9 |
| 16–20 | 25–43 | 5.6–9.1 | 2.0–3.4 | 0.40–0.97 | 0.02–0.13 | 1.5–2.4 |
| 22–23 | 39 | 9.2–9.4 | 3.3 | 0.81–0.85 | 0.12–0.14 | 2.4 |

Given the jump clears d, the mean excess E[J − d | J > d] is 0.60–0.85 pt at d = 1, 1.0–1.4 at
d = 2, 1.1–1.9 at d = 3, 1.1–3.3 at d = 5 — it barely depends on d because the tail is heavy.

**Expected slippage per stop-out** (a resting level crossed by a 5-s jump, level uniform within
the crossing jump: E[J²]/2E[J]): 0.29 (04) – 0.60 (15) pts; **07–16 UTC pooled 0.470, but TRAIN
0.270 vs TEST 0.618** — it follows the jump size, and 5-s jumps doubled from 2025 to 2026
(07–16 monthly slip 0.14–0.19 through 2025-03, 0.49 in 2025-10, 0.83–0.91 in 2026-01→03,
0.38–0.50 since). Release minutes (08:30/10:00/14:00 ET ± 1 min) carry 1.07 vs 0.46 elsewhere,
but hold only 4.9 % of the core-hour jumps > 3 pt.

**Corroboration on real stop-outs** (`results/overshoot_hour.csv`, `overshoot_stopbucket.csv`):
on the 25,794 quote-S5 stop-outs the observed overshoot of the bid/ask close beyond the stop at
the triggering bar averages **0.387 in 07–16 UTC (TRAIN 0.264, TEST 0.517)** — the same number
as the analytic estimator — and grows with the stop: 0.27 (< 2 pt), 0.34 (2–3), 0.44 (3–5), 0.67
(5–10), 1.31 (10+): far stops are reached by fast moves. Both are S5-close proxies: upper bounds
on tick-level overshoot, with no broker latency; the daily-break gap (p50 1.6, p95 11.6, max
54 pts, n 407) and the weekend gap (p50 6.4, p90 29.6, max 90.0) are separate risks for any
position carried across them.

## 3. Quote-vs-mid trigger geometry (`results/03_geometry.txt`, `geometry_curve.csv`)

Same 45,537 entries, exits re-resolved on the quote. **1,993 of 21,051 mid-model TPs (9.47 %)
become stops on the quote**; those flips are 107.7 % of the whole haircut (13,457.6 of 12,501.5
pts), offset by 168 SL→TP flips (−1,668) and 105 TP→TIME (+567). mid_S5 vs mid_M1 agree on
99.77 % of outcomes (25,325.7 vs 25,006.5 pts) — the harness's minute resolution is not the
issue; the trigger side is. **The haircut is 0.275 pts per trade (TRAIN 0.280, TEST 0.269) and
it does not grow with the stop distance** — the PF haircut does, because a tight stop's gross
per trade is small:

| x = stop / spread at entry | n TRAIN | TP→SL share | G TRAIN (± se) | G TEST | G control | PF haircut @0.80 TRAIN | TEST |
|---|---|---|---|---|---|---|---|
| < 1.5 | 27 | 14.8 % | 0.447 ± 0.220 | 0.066 | 0.185 | −48 % | −7 % |
| 1.5–2.5 | 2,171 | 7.8 % | 0.411 ± 0.036 | 0.291 | 0.329 | **−24 %** | −18 % |
| 2.5–3.5 | 6,119 | 5.9 % | 0.431 ± 0.025 | 0.278 | 0.334 | −20 % | −13 % |
| 3.5–5 | 7,523 | 5.0 % | 0.465 ± 0.028 | 0.316 | 0.382 | −16 % | −11 % |
| 5–7 | 4,104 | 3.9 % | 0.543 ± 0.050 | 0.337 | 0.471 | −15 % | −9 % |
| 7–10 | 1,857 | 3.5 % | 0.603 ± 0.079 | 0.508 | 0.452 | −13 % | −10 % |
| 10–15 | 643 | 0.8 % | 0.248 ± 0.087 | 0.524 | 0.392 | −4 % | −8 % |
| 15–25 | 396 | 1.3 % | 0.509 ± 0.194 | 0.611 | 0.501 | −10 % | −7 % |
| 25+ | 854 | 0.6 % | 0.502 ± 0.176 | 0.842 | 0.625 | −5 % | −5 % |

G = Σ(mid − quote pts) / Σ spread at entry: the haircut in spread units. The pre-declared
random-walk null is G = 0.5 in every bucket (stop s/2 closer, target s/2 farther, cost exactly
s/2 per trade). Pooled: **TRAIN 0.467, TEST 0.364, control 0.389** — the trigger shift costs a
bit less than half a spread, and the tight-stop buckets pay *less* per trade in spread units
than the wide ones, not more. **The matched random-entry control (same side, time of day, stop
and target distances, day ± 30) reproduces the curve bucket by bucket** (0.33 / 0.33 / 0.38 /
0.47 / 0.45 / 0.39 / 0.50 / 0.63 vs the strategies' 0.34 / 0.36 / 0.40 / 0.43 / 0.54 / 0.45 / 0.58 /
0.69 on ALL): the haircut is generic geometry, not something the ICT entries do.

**Does one curve explain all 15?** Per-strategy G on TRAIN and the residual of the pooled curve:

| strategy | G own | G control | resid/trade TRAIN | TEST haircut actual → predicted | X @0.80 | quote PF → predicted |
|---|---|---|---|---|---|---|
| c03_fvg_fill | 0.36 | 0.43 | −0.068 | 327 → 287 | 3.3 % | 1.348 → 1.359 |
| s03_ob_mitigation | 0.50 | 0.36 | +0.019 | 713 → 816 | 6.1 % | 0.712 → 0.703 |
| s04_breaker_block | 0.47 | 0.38 | +0.010 | 678 → 1,351 | 21.8 % | 0.697 → 0.651 |
| s100_m3_combo | 0.41 | 0.27 | −0.037 | 801 → 895 | 8.2 % | 0.867 → 0.861 |
| s10_90min_fade | 0.44 | 0.47 | −0.013 | 1,168 → 1,600 | 9.0 % | 0.568 → 0.539 |
| s11_m90_fade_ny | 0.54 | 0.29 | +0.042 | 158 → 328 | 22.4 % | 0.667 → 0.603 |
| s12_m90_fade_bias | 0.45 | 0.43 | −0.008 | 645 → 713 | 5.7 % | 0.683 → 0.658 |
| s14_ob_mit_bias | 0.54 | 0.33 | +0.041 | 421 → 445 | 4.5 % | 1.210 → 1.198 |
| s93_fvg_scalp | 0.66 | 0.34 | +0.098 | 48 → 73 | 17.5 % | 1.175 → 1.142 |
| s94_sweep_reversal | **0.21** | 0.17 | **−0.165** | 30 → 264 | 32.4 % | 0.796 → 0.745 |
| s95_session_breakout | 0.54 | 0.57 | +0.035 | 106 → 146 | 5.6 % | 1.279 → 1.264 |
| s96_h1_momentum | 0.37 | 0.91 | −0.079 | 270 → 123 | 12.7 % | 1.264 → 1.309 |
| s97_snap_scalper_m5 | **0.82** | 0.37 | **+0.191** | 139 → 84 | 23.1 % | 1.601 → 1.916 |
| s98_zscore_mr_m15 | 0.74 | 0.00 | +0.191 (n 23) | −3 → 5 | 3.3 % | 0.192 → 0.184 |
| s99_mss_fvg | 0.45 | 0.28 | −0.014 | 368 → 342 | 2.7 % | 0.745 → 0.753 |

Eleven of fifteen sit within ±0.07 pts/trade of the one curve on TRAIN. The exceptions are
structural, not noise: s97 (30-min hold, 82 % TP rate, targets ≈ 1 pt: the target moving s/2
farther is a large fraction of its payoff) and s93 pay more; s94 (76 % SL rate, 1,200-min hold,
stops far from the price path's first crossing) pays less. One curve is the right *default*; a
strategy whose payoff is one spread wide needs its own number.

## 4. The cost model (`results/cost_model.csv`)

Per trade, in points, relative to the mid-M1 harness at cost 0:

```
cost(h, d) = entry(h) + G(d/s_h)·s_h + P(SL)·slip(h, d)
  entry(h)   fill at the next S5 bar vs the market mid at the signal close = half-spread + 5-s drift
             (measured on the Stage-1 fills: pooled 0.363 = 0.333 + 0.030; TRAIN 0.326, TEST 0.404)
  G(x)·s     the trigger-geometry haircut, TRAIN curve above × the hour's median spread
  slip(h,d)  E[J²]/2E[J] by hour × the stop-bucket ratio from the observed overshoots
```

TRAIN-fitted table (totals at P(SL) = 0.5; substitute the strategy's own stop rate):

| hour | spread p50 | entry | slip/stop | total <2 pt | 2–3 | 3–5 | 5–10 | 10+ |
|---|---|---|---|---|---|---|---|---|
| 0 | 0.67 | 0.36 | 0.28 | 0.71 | 0.77 | 0.84 | 0.73 | 0.93 |
| 1 | 0.66 | 0.34 | 0.28 | 0.69 | 0.74 | 0.82 | 0.71 | 0.91 |
| 2 | 0.65 | 0.28 | 0.23 | 0.60 | 0.65 | 0.72 | 0.60 | 0.79 |
| 3 | 0.64 | 0.37 | 0.19 | 0.69 | 0.73 | 0.80 | 0.67 | 0.85 |
| 4 | 0.64 | 0.41 | 0.18 | 0.71 | 0.76 | 0.82 | 0.69 | 0.87 |
| 5 | 0.64 | 0.35 | 0.22 | 0.67 | 0.72 | 0.79 | 0.66 | 0.85 |
| 6 | 0.61 | 0.30 | 0.22 | 0.61 | 0.66 | 0.72 | 0.61 | 0.79 |
| 7 | 0.59 | 0.31 | 0.23 | 0.62 | 0.66 | 0.72 | 0.62 | 0.79 |
| 8 | 0.59 | 0.33 | 0.24 | 0.65 | 0.69 | 0.75 | 0.65 | 0.82 |
| 9 | 0.58 | 0.32 | 0.22 | 0.62 | 0.66 | 0.72 | 0.61 | 0.78 |
| 10 | 0.58 | 0.32 | 0.20 | 0.62 | 0.65 | 0.71 | 0.60 | 0.77 |
| 11 | 0.58 | 0.32 | 0.22 | 0.62 | 0.66 | 0.72 | 0.61 | 0.78 |
| 12 | 0.60 | 0.34 | 0.30 | 0.68 | 0.72 | 0.79 | 0.70 | 0.89 |
| 13 | 0.61 | 0.34 | 0.33 | 0.68 | 0.73 | 0.81 | 0.73 | 0.92 |
| 14 | 0.60 | 0.34 | 0.33 | 0.68 | 0.73 | 0.80 | 0.72 | 0.91 |
| 15 | 0.59 | 0.31 | 0.27 | 0.63 | 0.67 | 0.74 | 0.65 | 0.82 |
| 16 | 0.58 | 0.29 | 0.24 | 0.60 | 0.64 | 0.70 | 0.60 | 0.77 |
| 17 | 0.59 | 0.31 | 0.22 | 0.62 | 0.65 | 0.72 | 0.61 | 0.78 |
| 18 | 0.59 | 0.32 | 0.25 | 0.64 | 0.68 | 0.74 | 0.65 | 0.82 |
| 19 | 0.58 | 0.32 | 0.21 | 0.62 | 0.65 | 0.72 | 0.61 | 0.77 |
| 20 | 0.64 | 0.35 | 0.21 | 0.66 | 0.71 | 0.78 | 0.65 | 0.84 |
| 21 | 0.73 | 0.39 | 0.14 | 0.73 | 0.75 | 0.84 | 0.93 | 0.88 |
| 22 | 0.77 | 0.41 | 0.44 | 0.84 | 0.89 | 1.01 | 1.19 | 1.16 |
| 23 | 0.73 | 0.39 | 0.25 | 0.76 | 0.79 | 0.89 | 1.01 | 0.96 |

The same table with TEST-era inputs (2025-12 → 2026-09) is the second block of the CSV: entry
0.40, slip/stop 0.59 in 07–16, totals 0.72 / 0.78 / 0.84 / 1.13 / 1.36 for the five stop buckets —
**the cost of a 5–10 pt stop rose 73 % between the two halves because 5-s jumps doubled while the
spread rose 28 %.** The hour effect inside 07–16 is small (±0.05 around the mean; 13–14 UTC are
the expensive hours through slippage, not spread); the stop-distance and regime effects are
what a flat number misses.

**A separate fidelity term, not in the table** (`results/nominal_gap_by_strategy.csv`): the
harness books the entry at the strategy's *nominal* `entry_price`, and for four modules that is
not the market at signal time. Positive = the harness got a price the market never offered:
**s97 +2.375 pts/trade (median 1.735), s96 +1.343**; s95 −1.068 and s98 −0.671 (the harness is
conservative for those). s93/s94/s99/s100 differ from the M1 close on 78–96 % of trades but with
zero mean (|gap| median 0.4–0.65). For s97 this is the whole edge: its targets are ~1 pt from a
nominal short entry that sits 2.4 pt above the market — the C2-wick confound in entry form (the
xau2y report's s97 disqualification is confirmed from a second direction).

## 5. Validation on TEST (pre-registered, read once) — **FAIL**

Predicted quote-S5 net = mid-M1 net − Σ G_TRAIN(x)·s_entry, per strategy (`results/validation_test.csv`):
the geometry haircut over all 21,843 TEST trades is **predicted 7,472.9 pts vs actual 5,868.4
(+27.3 %)**; pooled X = 54.4 % of |quote net| at 0.45 and 15.1 % at 0.80 (the pooled net is small
because winners and losers cancel, which the pre-declared metric did not anticipate); per-strategy
median X 13.3 % / 8.2 %, max 48.9 % (s94) / 32.4 %; |PF error| median 0.025, max 0.378 (s97).
Against the declared bars (pooled ≤ 10 %, median ≤ 15 %) that is a **FAIL at both costs**, and the
reason is visible in §3: G fell from 0.467 to 0.364 while the points per trade stayed at 0.27.
The haircut in *points* was stable; the spread-unit scaling I pre-registered was wrong.

Post-hoc, labelled, not adopted: a flat TRAIN-mean 0.280 pts/trade predicts the TEST haircut at
+4.2 %; and on TRAIN months alone G correlates −0.46 with the ratio (mean 5-s jump / spread) —
the fitted `G = 0.640 − 0.452·(J/s)` predicts TEST G 0.376 vs 0.366 actual. The mechanism is
plausible (when jumps are large relative to the spread, both levels are overshot by the same
jumps and the s/2 shift matters less) but it was found after the failure and needs its own
pre-registration.

## 6. Flat-cost equivalents and the verdict on 0.45 / 0.80 (`results/flat_equivalents.csv`)

All-in cost actually paid by the Stage-1 trades entered **07–16 UTC** (90.8 % of them), with the
observed stop overshoot and each trade's own outcome:

| | entry | geometry | stop slippage | **total** | P(SL) | median stop |
|---|---|---|---|---|---|---|
| TRAIN (2024-09 → 2025-11) | 0.324 | 0.277 | 0.146 | **0.748** | 0.554 | 2.18 |
| TEST (2025-12 → 2026-09) | 0.397 | 0.260 | 0.300 | **0.957** | 0.580 | 2.80 |

By stop bucket (07–16, observed slippage): TRAIN 0.68 (< 2) / 0.76 / 0.83 / 0.91 (5–10) / 0.73 (10+);
TEST 0.82 / 0.82 / 0.95 / 1.14 / 1.74.

Pre-declared rule (a): the measured all-in cost at the median Stage-1 stop is 0.75 (TRAIN) and
0.96 (TEST) — **outside [0.45, 0.80] on the TEST half**, and 0.45 is below the half-spread plus
geometry alone (0.33 + 0.28 = 0.61 pooled) in every month from 2024-12 on (2024-09 → 11, with a 0.39 spread, is the only stretch where 0.45 covered them). Rule (b): replacing the flat
costs by the hour × stop table changes **no** Stage-1 bars-1–3 verdict (`results/decision_rule_b.csv`:
the survivors c03 / s14 / s93 / s95 / s96 keep TEST PF 1.38 / 1.39 / 1.15 / 1.28 / 1.32 under the
TRAIN table and 1.33 / 1.39 / 1.11 / 1.23 / 1.28 under TEST-era inputs); adding the nominal-fill
gap flips exactly one, s97 (2.24 → 0.30), which is a fidelity defect, not a cost.

**Recommendation.** Retire 0.45 as a "base" — it has no physical counterpart for a mid-M1
harness. Keep two flat numbers only as a convenience, re-labelled by what they are:
- **mid-M1 harness: 0.75 base (TRAIN-era all-in) / 1.00 stress (TEST-era all-in, 5–10 pt stops
  1.14)**. 0.80 survives as "roughly the 2025 all-in cost", not as stress.
- **quote-S5 resolution (`s5exit`)**, which already carries the 0.27 geometry: **0.45 base / 0.70
  stress** (entry + slippage only). This is the one place 0.45 was accidentally right, and it
  is why the s14 floor decision made under quote_S5 + 0.80 (≈ mid at 1.07) was conservative.
- For anything cost-sensitive (stops < 3 pt, or a TP within 2 spreads), use the table, and
  scale its slippage column by the current month's mean 5-s jump (0.14–0.19 in 2025 H1, 0.30–0.68
  since 2025-10) — the quantity that moved most, and the easiest to monitor live.

## What this establishes, and what it does not
- Establishes: the spread is 1.2–2.3 bp of price and flat in those units; execution cost in
  points doubled with the gold price and with 5-s volatility; the quote-trigger haircut is
  ≈ 0.27 pts/trade, generic (the random control pays the same), largest in PF terms for stops
  under 2.5 spreads (−24 % at 0.80), and one curve fits 11 of 15 strategies to ±0.07 pts/trade.
- Does not establish: tick-level slippage (S5 closes bound it from above), broker latency,
  commission, or partial fills — none are in the cache. The slippage column is a proxy the
  live record must calibrate: the demo book (c03 / s14 since 09-18) should show a mean
  stop-out overshoot near 0.5 pt in the current regime; materially more is latency.
- Comparisons: one model fit, no bar-based selection; the 15-row residual table is expected to
  show one outlier by chance — s97 and s94 are explained structurally, not by that.

## Next hypotheses (each needs its own pre-registration)
1. **Geometry in points, scaled by jump/spread**: `Δ = a + b·s·(1 − c·J/s)` fitted on TRAIN
   months; the post-hoc fit predicts TEST G to 0.01. If it holds, the lab can price the trigger
   haircut from two live-observable numbers (spread, mean 5-s jump) instead of a flat 0.27.
2. **Fill-fidelity gate in the harness**: assert |entry_price − M1 close| ≤ 0.5 × spread at
   signal time, or fill at the M1 close and re-derive SL/TP from it. Re-run s96 (edge shrinks by
   1.3 pts/trade → TEST PF 1.34 → 1.19 under the model) and s97 (disqualified) before either is
   discussed again.
3. **Position-carry risk**: 407 daily-break gaps (p95 11.6 pt) and 106 weekend gaps (p90 29.6 pt)
   are outside every exit model; a rule "flat by 20:55 UTC unless stop ≥ 15 pt" can be tested on
   the c03 / s95 / s96 trade sets, which are the ones that hold overnight.
