# REPORT — Liquidity sweeps at S5 resolution (2026-09-18)

Folder: `lab/research_s5/sweeps/`. Pre-registration: `PROTOCOL.md`. Scripts: `sweeps_lib.py`,
`run_events.py`, `run_qa.py`, `run_qb.py`, `run_drift.py` (post-hoc, logged). Raw output:
`results/run_*.log`; tables `results/*.csv`; per-arm campaign files `results/campaign/`
(304 files: 12 real arms, 60 C-RAND replicate arms, 4 C-TOUCH arms × 2 costs) and
`results/campaign_opp/` (secondary target). Every number below is copied from those logs.

## The question
At 5-second resolution, what does a sweep of a known level look like (depth, time beyond,
reclaim speed), does the anatomy separate reversals from continuations (Q-A), and does a
sweep-and-reclaim entry carry any edge that a matched random-time control and a
touch-without-reclaim control cannot explain (Q-B)?

## The pre-registered rule (short form; full text in PROTOCOL.md)
- Levels: `PD` (previous completed UTC day H/L), `ASIA` (00–07 UTC range, valid 07–24),
  `H1` (previous completed clock hour H/L), `LON` (07–12 UTC range, valid 12–21).
- Sweep: mid crosses the level by ≥ 0.10 pt from a close inside; reclaim = first S5 close
  back inside; SWEEP(T) if the reclaim comes within T ∈ {30, 120, 300} s; re-arm at 0.50 pt
  back inside. Depth `D` = extreme − level; `E` = extreme.
- Trade: fill on the bar after the reclaim bar at the quote (short at bid / long at ask);
  stop `E ± 0.20` on the quote; target **2R** (primary) or the opposite side of the swept
  range (secondary); quote-level walk, stop before target, max hold 4 h; one open trade per
  arm; costs 0.45 / 0.80 on top of quote fills; TRAIN < 2025-12-01 ≤ TEST.
- Controls: **C-RAND** = same side, same R, same UTC hour, ±30 days, same count, 5 seeds;
  **C-TOUCH** = fade at the cross bar without waiting for the reclaim, stop = cross-bar
  extreme ± 0.20, 2R.
- Selection: the TEST candidate is the arm with the highest TRAIN PF at 0.80 (TRAIN n ≥ 80).
  Verdict = the five xau2y bars from `lab.tools.campaign_score`.

## Harness checks (from `run_qb.log`)
- Entry bar is the bar after the reclaim bar: gap p50 5 s, exactly 5 s in 99.6 % of events
  (the rest span quiet-minute gaps where no S5 candle printed).
- My resolver vs `lab.s5exit.resolve(mode="quote_s5", start_offset_s=0)` on 300 sampled
  trades: outcome agree 300/300, exit price agree 300/300.
- Level coverage of in-window bars: PD 100 %, H1 100 %, ASIA 67.8 % (valid 07–24 by
  design), LON 39.4 % (valid 12–21 by design). Median level range: PD 61.8 pts, H1 11.1,
  ASIA 33.4, LON 22.2.
- No trade fell across the 2025-12-25 23:00–23:15 spike (`bad_window` = 0 in every arm).

## Sweep anatomy (`run_events.log`)
59,194 in-window excursions (TRAIN 28,189 / TEST 31,005 — TEST is shorter but 2026 gold
moves more points per hour). Share reclaimed within T:

| level | excursions | ≤30 s | ≤120 s | ≤300 s | reclaimed within 4 h |
|---|---|---|---|---|---|
| PD | 6,233 | 0.692 | 0.828 | 0.885 | 6,075 |
| ASIA | 7,699 | 0.684 | 0.823 | 0.884 | 7,523 |
| H1 | 38,380 | 0.664 | 0.816 | 0.882 | 37,602 |
| LON | 6,882 | 0.702 | 0.841 | 0.899 | 6,732 |

SWEEP(300) anatomy (n = 52,368): depth p25/p50/p75/p95 = 0.22 / 0.45–0.51 / 1.04–1.21 /
3.4–4.3 pts by level type; depth in spread units p50 0.67–0.75, p90 3.1–3.8; time beyond
p50 5–10 s, p75 25–30 s, p90 85–95 s; 29–34 % of sweeps reclaim inside the cross bar itself.
By reclaim-speed bucket (pooled): 0 s n=15,836 depth p50 0.21 (0.30 spreads); 5–30 s
n=24,058 0.46 (0.70); 35–120 s n=8,712 1.32 (2.0); 125–300 s n=3,762 2.45 (3.7).

**Reading:** at S5 the typical "sweep" of a daily/session/hourly level on XAU is a poke of
about half a point — below one spread — that is back inside within 10 seconds. Only the
slowest ~7 % (125–300 s) reach the 2–3 pt depths a chart reader would call a stop hunt.

## Question A — does anatomy separate reversal from continuation? (`run_qa.log`)
Labels after the reclaim (mid, 4 h horizon): `REV1` = reaches level − 1×D before revisiting
E; `REV2` = 2×D; `REVF` = fixed 1.0 pt. Shares of resolved events: REV1 0.671, REV2 0.524,
REVF 0.548 (per level type within ±0.02 of these; UNRESOLVED ≤ 37 of 52,368).

Permutation tests (10,000 shuffles, difference of medians, REV − CONT), pooled:

| label | var | n_rev | n_cont | med REV | med CONT | diff | p |
|---|---|---|---|---|---|---|---|
| REV1 | log depth | 35,126 | 17,232 | −0.862 | −0.580 | **−0.282** | 0.0001 |
| REV1 | time beyond (s) | 35,126 | 17,232 | 5 | 10 | −5 | 0.031 |
| REV2 | log depth | 27,427 | 24,904 | −0.981 | −0.536 | **−0.445** | 0.0001 |
| REVF | log depth | 28,703 | 23,659 | −0.393 | −1.155 | **+0.762** | 0.0001 |
| REVF | time beyond (s) | 28,703 | 23,659 | 10 | 5 | +5 | 0.009 |

The depth effect is huge and significant in *both directions*: with a depth-scaled target,
"reversals" are the *shallow* sweeps (REV1 share by depth tercile 0.712 / 0.690 / 0.611);
with a fixed 1-pt target they are the *deep* ones (REVF 0.387 / 0.514 / 0.744). The same
flip appears by reclaim speed (REV1 0.695 / 0.703 / 0.593 / 0.544 vs REVF 0.473 / 0.529 /
0.651 / 0.746 across 0 s → 125–300 s). This is Trap 1 exactly: the label measures how far
the target is from the level relative to the stop, not what the sweep predicts. Holding
depth fixed (within terciles), duration separates the groups only in the deep tercile under
REV1 (35 s vs 50 s, p = 0.0002) and not at all under REVF (40 s vs 40 s, p = 1.0). REVF
share rises from 0.46–0.51 (TRAIN) to 0.60–0.64 (TEST) purely because 1.0 pt is a smaller
move in 2026 (Trap 8).

**Answer A:** the depth/duration distributions of "reversal" and "continuation" sweeps
differ enormously, but the direction of the difference is set by the label's geometry, so
no anatomy feature can be credited with predicting the outcome. Nothing here supports
"deeper / slower sweeps reverse more" as a property of the market.

## Question B — the tradeable rule vs controls (`run_qb.log`, `qb_summary.csv`)
Primary target 2R. `rand_test_pf` = mean over 5 seeds [min–max].

| level | T | cost | TRAIN n | TRAIN PF | TEST n | TEST PF | TEST pts | TEST WR % | C-RAND TRAIN PF | C-RAND TEST PF | C-TOUCH TRAIN PF | C-TOUCH TEST PF |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| PD | 30 | 0.45 | 1447 | 0.317 | 1497 | 0.504 | −1464.7 | 21.2 | 0.331 | 0.445 [0.418–0.469] | 0.189 | 0.353 |
| PD | 30 | 0.80 | 1447 | 0.226 | 1497 | 0.409 | −1988.6 | 20.9 | 0.237 | 0.359 [0.337–0.379] | 0.117 | 0.261 |
| PD | 120 | 0.45 | 1592 | 0.355 | 1460 | 0.601 | −1360.6 | 22.7 | 0.400 | 0.601 [0.530–0.641] | 0.189 | 0.353 |
| PD | 120 | 0.80 | 1592 | 0.263 | 1460 | 0.509 | −1871.6 | 22.4 | 0.297 | 0.505 [0.444–0.538] | 0.117 | 0.261 |
| PD | 300 | 0.45 | 1564 | 0.394 | 1338 | 0.609 | −1404.2 | 23.0 | 0.431 | 0.610 [0.568–0.730] | 0.189 | 0.353 |
| PD | 300 | 0.80 | 1564 | 0.300 | 1338 | 0.526 | −1872.5 | 22.8 | 0.330 | 0.523 [0.487–0.629] | 0.117 | 0.261 |
| ASIA | 30 | 0.45 | 1864 | 0.304 | 1800 | 0.475 | −1714.9 | 21.7 | 0.332 | 0.475 [0.443–0.539] | 0.172 | 0.343 |
| ASIA | 30 | 0.80 | 1864 | 0.214 | 1800 | 0.376 | −2344.9 | 21.4 | 0.235 | 0.376 [0.349–0.427] | 0.105 | 0.242 |
| ASIA | 120 | 0.45 | 2038 | 0.378 | 1823 | 0.543 | −1747.8 | 22.7 | 0.383 | 0.501 [0.473–0.520] | 0.172 | 0.343 |
| ASIA | 120 | 0.80 | 2038 | 0.280 | 1823 | 0.447 | −2385.8 | 22.5 | 0.281 | 0.411 [0.389–0.427] | 0.105 | 0.242 |
| ASIA | 300 | 0.45 | 2007 | 0.429 | 1681 | 0.555 | −1784.2 | 23.2 | 0.410 | 0.565 [0.517–0.609] | 0.172 | 0.343 |
| ASIA | 300 | 0.80 | 2007 | 0.327 | 1681 | 0.468 | −2372.6 | 23.0 | 0.310 | 0.475 [0.434–0.513] | 0.105 | 0.242 |
| H1 | 30 | 0.45 | 8930 | 0.275 | 9501 | 0.421 | −9730.8 | 19.7 | 0.291 | 0.422 [0.403–0.449] | 0.144 | 0.285 |
| H1 | 30 | 0.80 | 8930 | 0.191 | 9501 | 0.330 | −13056.1 | 19.5 | 0.201 | 0.330 [0.315–0.352] | 0.085 | 0.198 |
| H1 | 120 | 0.45 | 10398 | 0.335 | 9843 | 0.492 | −10182.4 | 21.1 | 0.337 | 0.490 [0.480–0.495] | 0.144 | 0.285 |
| H1 | 120 | 0.80 | 10398 | 0.244 | 9843 | 0.401 | −13627.4 | 20.9 | 0.243 | 0.399 [0.391–0.403] | 0.085 | 0.198 |
| H1 | 300 | 0.45 | 10497 | 0.381 | 9427 | 0.515 | −10513.6 | 21.7 | 0.378 | 0.547 [0.519–0.576] | 0.144 | 0.285 |
| H1 | 300 | 0.80 | 10497 | 0.286 | 9427 | 0.431 | −13813.1 | 21.6 | 0.282 | 0.456 [0.433–0.480] | 0.085 | 0.198 |
| LON | 30 | 0.45 | 1599 | 0.337 | 1603 | 0.497 | −1540.3 | 21.2 | 0.355 | 0.501 [0.448–0.532] | 0.229 | 0.398 |
| LON | 30 | 0.80 | 1599 | 0.244 | 1603 | 0.400 | −2101.4 | 21.1 | 0.256 | 0.402 [0.359–0.426] | 0.146 | 0.291 |
| LON | 120 | 0.45 | 1742 | 0.374 | 1620 | 0.562 | −1561.6 | 23.0 | 0.424 | 0.577 [0.535–0.619] | 0.229 | 0.398 |
| LON | 120 | 0.80 | 1742 | 0.280 | 1620 | 0.468 | −2128.6 | 22.9 | 0.319 | 0.480 [0.446–0.514] | 0.146 | 0.291 |
| LON | 300 | 0.45 | 1707 | 0.434 | 1526 | 0.593 | −1584.2 | 24.2 | 0.432 | 0.627 [0.607–0.667] | 0.229 | 0.398 |
| LON | 300 | 0.80 | 1707 | 0.336 | 1526 | 0.507 | −2118.3 | 24.1 | 0.332 | 0.535 [0.516–0.570] | 0.146 | 0.291 |

Events → trades: e.g. `sweep_LON_T300` 6,190 sweeps → 3,233 trades (2,902 skipped while a
trade was open, 55 impossible geometry); `sweep_H1_T300` 33,856 → 19,924 (13,736 / 196).
Full counts in `qb_skips.csv`.

### Pre-declared candidate and bars verdict (`campaign_score`, `qb_bars_campaign.csv`)
TRAIN ranking at 0.80 (n ≥ 80): LON T300 0.336, ASIA T300 0.327, PD T300 0.300, H1 T300
0.286, … H1 T30 0.191. **Candidate: `sweep_LON_T300`.**

| bar | value | pass |
|---|---|---|
| 1 TEST n ≥ 40 | 1,526 | yes |
| 2 TEST PF > 1 at 0.45 / 0.80 | 0.593 / 0.507 | **no / no** |
| 3 TRAIN PF > 0.9 | 0.434 | **no** |
| 4 ≥ 55 % TEST months positive, no month > 50 % | 0.0 % positive; net −1,584.2 | **no** |
| 5 regime independence | corr 0.26 (up months −2,188.4, down months −1,025.9) | yes (abs(corr) < 0.4, while losing in both) |

**Verdict: FAIL (2 of 6 bars).** All 12 primary arms and all 12 secondary-target arms FAIL
with the same two bars (n and abs(corr) < 0.4). Best TEST PF anywhere: secondary
`sweepopp_LON_T300` 0.743 at 0.45 / 0.664 at 0.80 (TRAIN 0.503). No arm is positive at
either cost in either split. C-TOUCH is worse still (TRAIN PF 0.085–0.229, TEST 0.198–0.398).

### Edge beyond the controls (mean pts/trade at 0.45, bootstrap 95 % CI; `qb_edge_vs_controls.csv`)

| level | T | split | n | real | C-RAND | real − RAND [CI] | C-TOUCH | real − TOUCH [CI] |
|---|---|---|---|---|---|---|---|---|
| PD | 300 | TRAIN | 1564 | −1.004 | −0.907 | −0.097 [−0.243, 0.059] | −0.874 | −0.130 [−0.277, 0.021] |
| PD | 300 | TEST | 1338 | −1.049 | −1.006 | −0.044 [−0.456, 0.385] | −0.927 | −0.123 [−0.525, 0.281] |
| ASIA | 300 | TRAIN | 2007 | −0.923 | −0.933 | +0.010 [−0.127, 0.147] | −0.881 | −0.043 [−0.173, 0.101] |
| ASIA | 300 | TEST | 1681 | −1.061 | −1.007 | −0.054 [−0.326, 0.222] | −0.838 | −0.223 [−0.491, 0.045] |
| H1 | 300 | TRAIN | 10497 | −0.952 | −0.942 | −0.010 [−0.063, 0.043] | −0.892 | −0.060 [−0.112, −0.007] |
| H1 | 300 | TEST | 9427 | −1.115 | −1.006 | −0.110 [−0.219, 0.005] | −0.918 | −0.197 [−0.300, −0.086] |
| LON | 300 | TRAIN | 1707 | −0.955 | −0.935 | −0.019 [−0.180, 0.136] | −0.826 | −0.128 [−0.285, 0.018] |
| LON | 300 | TEST | 1526 | −1.038 | −0.928 | −0.110 [−0.415, 0.210] | −0.799 | −0.239 [−0.534, 0.088] |

Across all 24 (arm × split) rows the real-minus-random difference ranges −0.113 to +0.126
pts/trade and every CI includes zero (the two H1 rows nearest the boundary are on the
*negative* side). Against the touch control the real rule is *worse* per trade in 24 of 24
rows (all six H1 rows and LON T30/T120 TRAIN have CIs excluding zero). The reclaim confirmation therefore adds nothing that the
control cannot explain; it slightly worsens the per-trade result while improving PF (touch
trades have smaller R, so smaller wins and losses).

### Where the loss comes from
Mean gross result (cost 0, quote fills) is negative in every speed bucket of every level
and split, −0.18 to −2.16 pts/trade, typically −0.45 to −0.55 (`qb_speed_buckets.csv`), i.e.
roughly one spread per trade for the fast sweeps and more for the slow, wide-stop ones. Median R is
0.9–1.1 pts for 0 s / 5–30 s sweeps and 1.7–5.7 for the slow ones; on the quote the short
stop (`ask ≥ E + 0.2`) fires when the mid comes within ~0.1 of the extreme while the 2R
target needs the mid to travel 2R + one spread. WR 14–32 % against a break-even near 27 %
gross and 33 %+ net. The random control, given the same R at the same hours, produces the
same numbers — the sweep is not what is losing; the geometry is.

### Reclaim speed and level type (Q4)
Gross PF at T=300 by speed bucket mostly rises toward the *slow* buckets (H1 TRAIN 0.404 /
0.474 / 0.669 / 0.753; not monotone everywhere: LON TRAIN 0.608 / 0.547 / 0.530 / 0.922,
n=180 in the last), but the improvement is exactly what a wider stop buys and no bucket reaches
PF 1 gross in either split (best: LON TRAIN 125–300 s 0.922, n=180; ASIA TEST 35–120 s
0.837, n=317; PD TEST 125–300 s 0.805, n=133). LON is the least-bad level type in both
splits (TEST PF 0.593–0.627 at 0.45 vs H1 0.42–0.55), but its random control is equally
"good" (LON T300 TEST C-RAND 0.627). **No level type and no reclaim speed carries an edge
beyond the controls.**

## Post-hoc: is there any directional information after a reclaim? (`run_drift.log`, exploratory)
Signed mid drift from the reclaim close, fade direction positive, vs matched random bars,
no stops, no costs (added after Q-B; logged in PROTOCOL.md):

| group | 300 s diff [CI] | 900 s diff [CI] | 3600 s diff [CI] |
|---|---|---|---|
| ALL (n=52,368) | −0.042 [−0.102, 0.020] | **−0.148 [−0.250, −0.047]** | −0.172 [−0.365, 0.034] |
| ASIA | **−0.242 [−0.408, −0.074]** | **−0.444 [−0.712, −0.163]** | −0.534 [−1.058, 0.043] |
| LON | **−0.268 [−0.488, −0.068]** | −0.293 [−0.628, 0.030] | −0.366 [−0.977, 0.226] |
| PD | −0.028 [−0.226, 0.170] | −0.053 [−0.384, 0.261] | **−0.860 [−1.524, −0.159]** |
| H1 | +0.038 [−0.024, 0.106] | −0.078 [−0.199, 0.037] | +0.048 [−0.194, 0.279] |
| TRAIN / TEST @900 s | | −0.075 [−0.156, 0.011] / **−0.213 [−0.378, −0.041]** | |

The sign is *against* the ICT expectation: after a sweep-and-reclaim of a session/day level,
price on average drifts back through the level (continuation), by 0.15–0.9 pts within
15–60 min, in both splits. The share of events with positive fade-direction drift is
0.48–0.52 everywhere, so this is a tail (large-break) effect, not a typical-event one, and
its size is below one round-trip cost. It is reported as a hypothesis, not a result.

## What this establishes and what it does not
Establishes (n in the thousands, TRAIN and TEST agree, controls matched by hour, ±30 d, R):
1. At S5 the sweeps of PD/Asia/H1/London levels on XAU are overwhelmingly sub-spread pokes
   reclaimed within seconds; the "stop hunt" shape (2–4 pts, 1–5 min) is ~7 % of them.
2. Sweep anatomy does not predict reversal vs continuation once the label's geometry is
   controlled; the apparent effects flip sign with the target definition.
3. The literal sweep-and-reclaim fade (stop 0.2 beyond the extreme, 2R or opposite side of
   range, quote-level, 4 h) loses about one spread per trade at every level type, every T,
   both costs, both splits, and is indistinguishable from a random entry with the same R.
4. Waiting for the reclaim is not better per trade than fading the touch; both lose.

Does not establish: anything about sweeps with a *minimum depth in ATR or spread units*
and a stop wide enough to survive quote noise (not in the pre-registered grid); anything
about the continuation drift as a trade (costs exceed the effect as measured); anything
about a bear market in gold (none in the window).

Comparisons made: 12 primary arms (cost pair judged jointly), 12 secondary arms, 45
permutation tests, 6 within-tercile tests, and 60 post-hoc drift cells. At 5 % per test,
2–3 spurious p < 0.05 were expected among the descriptive tests; the depth results are at
p = 0.0001 with n > 17,000 per group and are real, but geometric. Zero of 24 tradeable arms
passed any profitability bar, which is below the chance rate, not above it.

## Next hypotheses (concrete)
1. **Continuation, not reversal**: after a SWEEP(300) of ASIA or LON with depth ≥ 2
   spreads, enter *in the sweep direction* on a close back beyond the level (a second
   cross), stop at the reclaim-bar low/high, target 3R, max hold 60 min. The drift table
   says the mean move is 0.25–0.45 pts in 5–15 min for these levels — the test is whether
   conditioning on depth concentrates it above cost. Pre-register the depth cut; C-RAND as here.
2. **Anatomy-gated reversal with a survivable stop**: same fade rule but only the slow, deep
   bucket (time beyond 125–300 s, depth ≥ 3 spreads, n ≈ 3,700 pooled) with stop = E + 1
   spread and target = 1R. That is the only bucket whose gross PF approached 1 (0.80–0.92 in
   some cells); the question is whether a 1R target at that stop distance clears 0.80 cost.
3. **Level-quality gate**: restrict to levels that were *not* touched in the prior 4 h
   (untested liquidity) — the present universe re-arms at 0.5 pt, so most events are the
   second or third poke at a level already visited, which is not what "resting stops" means.
