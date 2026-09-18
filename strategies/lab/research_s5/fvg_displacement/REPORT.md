# REPORT — S5 displacement quality vs M5 FVG respect (2026-09-18)

Protocol: `PROTOCOL.md` (pre-registered; two recorded deviations, both before any Q.A/Q.B
statistic). Scripts: `build_events.py`, `qa_features.py`, `qb_trades.py`,
`diag_exit_model.py` (post-hoc diagnostic, labelled). Raw outputs: `results/build_output.txt`,
`results/qa_output.txt`, `results/qb_output.txt`, `results/diag_output.txt`; tables in
`results/*.csv`; per-event data `results/events.parquet`. Every number below is copied from
those files. Compute: ~15 s end to end.

## The question
An M5 FVG gives you the gap size. The 5-second bars inside the displacement bar tell you how
the gap was made (one burst vs a grind). Does that S5-only information predict whether the
gap is respected (Q.A), and does it make a c03-like CE-limit trade profitable (Q.B), beyond
what gap size and killzone already explain?

## Sample
- **20,229 M5 FVGs** (gap ≥ 0.5 pt, three consecutive M5 bars) in 2024-09-01 → 2026-09-17;
  10,687 bullish, 9,542 bearish. TRAIN 11,060 (< 2025-12-01), TEST 9,169. 0 events dropped
  for the 2025-12-25 spike; 0 events with < 12 S5 bars in bar 2 (S5 coverage inside the
  displacement bar is complete: mean 59.4 of 60 slots).
- Gap size median 1.44 pt (IQR 0.86–2.62); in ATR units `gap_atr` median 0.43.
- Features (ATR-normalised where dimensional): `peak_vel` (median 0.28 ATR per 5 s),
  `burst30` (fastest 30 s covers a median 43 % of the bar-2 range), `vol_hhi` (0.021 — S5
  volume inside a displacement bar is nearly flat; 1/60 = 0.0167), `pullback_frac` (median
  42 % of S5 bars close against the impulse), `er2` (0.24), `peak_vel3`, `burst30_3`.

## Outcome labels and base rate
Primary label (near-edge touch, 8 h, mid S5):

| split | events | touched | resolved | respected | **base rate** |
|---|---|---|---|---|---|
| TRAIN | 11,060 | 10,086 (91.2 %) | 10,070 | 5,039 | **50.04 %** |
| TEST | 9,169 | 8,392 (91.5 %) | 8,378 | 4,453 | **53.15 %** |

Secondary label (`label_ce`, return must reach CE — the Q.B fill condition; deviation 2):
TRAIN 9,657 resolved, base rate **24.96 %**; TEST 8,058, **26.32 %**. The gap is
respected on a shallow first touch half the time; once price has come as deep as CE, it
trades through the far side three times in four.

TRAIN respect rate by `gap_atr` quintile: 0.530 / 0.499 / 0.488 / 0.490 / 0.496 (small gaps
are slightly *more* respected). Killzone 0.512 vs 0.493. Bullish 0.517 vs bearish 0.481.

## Question A — feature ranking (TRAIN, stratified on gap_atr quintile, 1,000 within-stratum label permutations)

| feature | dir | AUC | strat AUC | perm p | null p95 | lift (good half / base) | Δpp |
|---|---|---|---|---|---|---|---|
| **pullback_frac** | lower better | 0.5074 | **0.5103** | 0.040 | 0.5094 | 1.011 | +1.26 |
| er2 | higher | 0.4919 | 0.4973 | 0.674 | 0.5097 | 0.988 | −1.25 |
| vol_hhi | higher | 0.4937 | 0.4970 | 0.713 | 0.5090 | 0.998 | −0.18 |
| burst30 | higher | 0.4976 | 0.4951 | 0.794 | 0.5090 | 1.009 | +0.93 |
| peak_vel3 | higher | 0.4847 | 0.4883 | 0.972 | 0.5108 | 0.982 | −1.77 |
| peak_vel | higher | 0.4837 | 0.4865 | 0.991 | 0.5090 | 0.975 | −2.48 |
| burst30_3 | higher | 0.4807 | 0.4787 | 1.000 | 0.5095 | 0.965 | −3.56 |
| gap_atr (confound, unstratified AUC) | higher | 0.4872 | — | — | — | 0.980 | −2.05 |
| gap_size raw pts (confound) | higher | 0.5053 | 0.5170 | 0.002 | 0.5092 | 0.997 | −0.30 |

Bonferroni threshold over 7 features: p < 0.0071. **No S5 feature clears it.** The top feature
by the pre-registered statistic is `pullback_frac` (strat AUC 0.510, p 0.04 — a single-bar
pass of the kind 5 % of comparisons produce by chance). Five of seven features point the
*wrong* way: faster, burstier displacement (`peak_vel`, `peak_vel3`, `burst30_3`) is
associated with slightly *less* respect (AUC 0.48). `gap_size` in raw points ranks first
once stratified on `gap_atr` (0.517, p 0.002) — but within an ATR-normalised quintile, larger
raw points means later in the sample (gold ~2,500 → ~4,500), and the TEST base rate is 3 pp
higher than TRAIN; this is the time trend, not a gap property.

Power: the permutation null's 95th percentile is strat AUC 0.509–0.511, so a true effect of
AUC ≥ 0.515 would have been detected with n = 10,070. Everything measured lies inside
0.48–0.51.

**TEST read (once), threshold = TRAIN median:**

| feature | split | AUC | strat AUC | rate good half | rate bad half | lift | Δpp |
|---|---|---|---|---|---|---|---|
| pullback_frac | TRAIN | 0.5074 | 0.5103 | 0.5058 | 0.4932 | 1.011 | +1.26 |
| pullback_frac | **TEST** | **0.5003** | 0.5024 | 0.5329 | 0.5305 | 1.003 | **+0.24** |
| gap_atr | TRAIN | 0.4872 | 0.4980 | 0.4902 | 0.5106 | 0.980 | −2.05 |
| gap_atr | **TEST** | 0.4878 | 0.5004 | 0.5154 | 0.5410 | 0.970 | **−2.56** |

The top S5 feature's TEST AUC is 0.500 — exactly nothing. Gap size (ATR units) is the only
variable with a sign that replicates: larger gaps are ~2–2.5 pp *less* respected in both
halves (also the wrong way for the "bigger displacement = stronger gap" story).

Secondary label (`label_ce`): same ranking, same picture (`pullback_frac` strat AUC 0.508,
p 0.10; TEST AUC 0.496; `results/qa_train_ranking_ce.csv`, `qa_test_ce.csv`).

## Question B — c03-like CE-limit trade

Rule (as pre-registered): limit at CE from bar-3 close, fills on the quote from the next S5
bar, stop = far side ± 0.2, target 2R, quote-level exits, stop-first, max hold 8 h, costs
0.45 / 0.80. 17,457 of 20,229 gaps filled (TRAIN 9,492, TEST 7,965). Outcomes: TRAIN 8,535
SL / 953 TP / 4 TIME; TEST 6,945 / 1,017 / 3.

Threshold grid on TRAIN (arm A4, PF @0.80): keep best 33 % → PF 0.164 (n 3,248); 50 % →
0.157; 67 % → 0.151. **T\* = `pullback_frac` ≤ 0.3898** (best 33 %).

| arm | population | cost | TRAIN n | TRAIN PF | TRAIN net | TEST n | TEST PF | TEST net | TEST WR | +months | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A0 | all | 0.45 | 9,492 | 0.193 | −9,706 | 7,965 | 0.358 | −8,816 | 12.8 % | 0 % | FAIL |
| A0 | all | 0.80 | 9,492 | 0.132 | −13,028 | 7,965 | 0.282 | −11,604 | 12.8 % | 0 % | FAIL |
| A1 | killzone | 0.45 | 3,678 | 0.238 | −3,597 | 2,830 | 0.384 | −3,094 | 14.1 % | 0 % | FAIL |
| A1 | killzone | 0.80 | 3,678 | 0.165 | −4,884 | 2,830 | 0.304 | −4,084 | 14.1 % | 0 % | FAIL |
| A2 | gap_atr ≥ 0.475 | 0.45 | 4,664 | 0.292 | −4,797 | 2,875 | 0.541 | −3,220 | 20.8 % | 0 % | FAIL |
| A2 | gap_atr ≥ 0.475 | 0.80 | 4,664 | 0.212 | −6,430 | 2,875 | 0.459 | −4,227 | 20.8 % | 0 % | FAIL |
| A3 | KZ ∧ gap | 0.45 | 1,639 | 0.380 | −1,544 | 988 | 0.569 | −1,100 | 22.2 % | 0 % | FAIL |
| A3 | KZ ∧ gap | 0.80 | 1,639 | 0.284 | −2,118 | 988 | 0.487 | −1,446 | 22.2 % | 0 % | FAIL |
| A4 | feature T\* | 0.45 | 3,248 | 0.234 | −3,236 | 1,692 | 0.396 | −2,082 | 15.1 % | 0 % | FAIL |
| A4 | feature T\* | 0.80 | 3,248 | 0.164 | −4,373 | 1,692 | 0.323 | −2,674 | 15.1 % | 0 % | FAIL |
| A5 | KZ ∧ feature | 0.45 | 1,225 | 0.299 | −1,143 | 593 | 0.455 | −687 | 15.7 % | 0 % | FAIL |
| A5 | KZ ∧ feature | 0.80 | 1,225 | 0.214 | −1,571 | 593 | 0.377 | −894 | 15.7 % | 0 % | FAIL |
| A6 | KZ ∧ gap ∧ feature | 0.45 | 709 | 0.411 | −632 | 297 | 0.601 | −335 | 22.6 % | 10 % | FAIL |
| A6 | KZ ∧ gap ∧ feature | 0.80 | 709 | 0.310 | −880 | 297 | 0.522 | −439 | 22.6 % | 0 % | FAIL |

xau2y bars: every arm passes n ≥ 40 and the regime bar (losses in gold-up AND gold-down
months; |corr| ≤ 0.14) and fails PF > 1 at both costs, TRAIN PF > 0.9 and the monthly bar
(0–10 % of TEST months positive). **0 of 14 arm × cost cells pass; none is close.**

**Permutation control** (200 shuffles of `pullback_frac` across the parent population, same
count, TEST):

| arm | parent | cost | n | real PF | ctrl PF med / p95 | real net | ctrl net med / p95 | pct-rank PF / net |
|---|---|---|---|---|---|---|---|---|
| A4 | A0 | 0.45 | 1,692 | 0.396 | 0.360 / 0.402 | −2,082 | −2,490 / −2,286 | 0.895 / 1.000 |
| A4 | A0 | 0.80 | 1,692 | 0.323 | 0.284 / 0.318 | −2,674 | −3,281 / −3,077 | 0.955 / 1.000 |
| A5 | A1 | 0.45 | 593 | 0.455 | 0.374 / 0.449 | −687 | −888 / −750 | 0.960 / 0.995 |
| A5 | A1 | 0.80 | 593 | 0.377 | 0.296 / 0.357 | −894 | −1,163 / −1,019 | 0.980 / 1.000 |
| A6 | A3 | 0.45 | 297 | 0.601 | 0.575 / 0.709 | −335 | −417 / −270 | 0.590 / 0.785 |
| A6 | A3 | 0.80 | 297 | 0.522 | 0.492 / 0.609 | −439 | −549 / −400 | 0.615 / 0.880 |

Feature increment on TEST PF @0.80: A4 vs A0 +0.040, A5 vs A1 +0.073, A6 vs A3 +0.035. In
A4 and A5 the real filter sits at the 90–98th percentile of its random-subset control (it
loses ~20 % less per trade than a random subset of the same size); once gap size is also
conditioned on (A6) it is at the 59–62nd percentile — indistinguishable from random. Note
the control's own PF: a random 33 % subset of the killzone gaps has PF 0.30; the "improved"
arm has 0.38. The direction the feature moves the PF is the pre-declared one, but from a
population that loses 0.6–0.7 pt per trade on a 0.9-pt risk it is not a tradeable increment.

Sensitivity (one resting/open order per direction, `results/qb_arms_one_per_direction.csv`):
A0 TEST PF 0.298 / 0.231, A6 0.612 / 0.531 — the same picture with n roughly halved.

### Why the base arm is this bad — anatomy (A0, cost 0.45)

| R = \|CE − SL\| | n | TP % | SL on the fill bar | net | PF |
|---|---|---|---|---|---|
| < 0.6 | 3,939 | 2.1 % | 81.5 % | −3,688 | 0.01 |
| 0.6–0.8 | 3,467 | 5.9 % | 60.7 % | −3,532 | 0.05 |
| 0.8–1.0 | 2,387 | 9.6 % | 43.2 % | −2,589 | 0.11 |
| 1.0–1.5 | 3,420 | 14.2 % | 24.3 % | −3,924 | 0.20 |
| 1.5–2.5 | 2,591 | 20.6 % | 11.2 % | −3,013 | 0.38 |
| 2.5+ | 1,653 | 26.4 % | 5.0 % | −1,778 | 0.69 |

With min gap 0.5 pt and the stop 0.2 beyond the far side, the median gap (1.44 pt) gives
R = 0.92 pt against a 0.58–0.66 pt spread. A long fills when the ask reaches CE and stops
when the bid reaches gap_low − 0.2, i.e. when the mid falls a further R − 2·half-spread ≈
0.3 pt: for the smallest 40 % of gaps the stop is inside the spread and fires on the fill bar
itself. This is the pre-declared geometry (recorded in PROTOCOL deviations); it is the same
tight-stop/quote-trigger effect `lab/REPORT_s5exit_2026-09-18.md` documented for s97.

**Post-hoc diagnostic (not a pre-registered arm; `results/diag_exit_model.csv`)** — the same
A0 entries under other exit models, TEST @0.45 / @0.80:

| model | stop pad | TEST n | TP rate | TEST PF @0.45 | @0.80 |
|---|---|---|---|---|---|
| quote h/l ± half-spread (protocol) | 0.2 | 7,965 | 12.8 % | 0.358 | 0.282 |
| quote closes (lab/s5exit convention) | 0.2 | 7,904 | 11.4 % | 0.327 | 0.259 |
| **mid h/l, no spread at all** | 0.2 | 8,070 | **32.8 %** | 0.725 | 0.549 |
| quote h/l, stop pad 1.0 | 1.0 | 7,965 | 20.8 % | 0.475 | 0.394 |
| mid h/l, stop pad 1.0 | 1.0 | 8,070 | 33.7 % | 0.805 | 0.663 |

Even with the spread removed entirely, the TP rate on a 2R target is 32–34 % — the
random-walk break-even for 2R is 33.3 % — and the PF is < 1 at any cost. The CE entry has no
directional edge for a displacement feature to modulate; the quote geometry then turns a
zero-edge entry into a heavy loser.

## Verdict

**FAIL on both questions.** S5 displacement quality does not predict whether an M5 FVG holds:
the best of seven pre-declared features has TRAIN stratified AUC 0.510 (not significant after
Bonferroni) and TEST AUC 0.500; three of the "burst" features point the wrong way. Gap size
itself is a weak *negative* predictor of respect (−2 to −2.6 pp for the larger half, both
splits). The c03-like CE-limit trade fails every xau2y bar in all seven arms at both costs
(TEST PF 0.28–0.60, 0–10 % of months positive). The feature filter raises TEST PF by
0.035–0.073 and beats its random-subset control at the 90–98th percentile in the unconditioned
arms, but is indistinguishable from random once gap size and killzone are conditioned on
(A6: 59–62nd percentile). **Explicit statement: the S5 features add nothing beyond gap size and
killzone that a trader could use — and gap size and killzone add nothing tradeable either;
the entry has no edge at any exit model.**

### What this does and does not establish
- Does: at n ≈ 10k per split, S5-resolution displacement shape (velocity, burst share,
  volume concentration, pullback count, efficiency) carries no usable information about the
  first-return behaviour of an M5 FVG on XAU_USD. The label base rate (50 % on a touch, 25 %
  on a CE return) is the market's answer to "does an FVG hold": a coin flip on a touch, and
  3:1 against once price has come to CE.
- Does: a CE limit with a stop just beyond the gap is structurally untradeable on gold at the
  0.5-pt gap floor; the loss is mostly quote geometry (mid PF 0.55–0.73 → quote PF 0.28–0.36).
- Does not: say anything about c03 itself. c03 enters on a *reaction close* back outside the
  gap with a stop 0.1·ATR beyond the gap and a swing-based target — a different, wider
  geometry and a confirmation entry; its screen pass (`REPORT_xau2y`) is untouched here.
- Does not: rule out S5 features on *other* FVG questions (e.g. whether the gap gets touched
  at all, or the speed of the first return), nor on larger-gap subsets (gap ≥ 1 ATR is only
  the top 10.1 % here and was not split out).
- Comparisons made: 9 TRAIN + 4 TEST reads (Q.A), 3 TRAIN grid values, 14 TEST arm cells,
  6 control cells. One single-bar chance pass was expected; `pullback_frac` p 0.04 on TRAIN
  is exactly that, and it vanished on TEST.

### Next hypotheses (concrete)
1. **Touch probability, not respect.** 8.5–12.5 % of gaps are never returned to within 8 h.
   Test whether S5 features (or gap_atr) predict *no return* — the only outcome here that
   was not a coin flip — as a filter for continuation entries rather than fills.
2. **c03's actual geometry with the S5 features.** Re-run c03's reaction-close entries
   (stop 0.1·ATR beyond the gap, swing target) through `lab/s5exit.py`, attach the same
   seven features to the FVG each trade came from, and test `pullback_frac` ≤ TRAIN-33 % as
   a gate. Same 3-value grid, same control. That is the only place a marginal +0.04–0.07 PF
   effect could matter, because c03's base PF is 1.4, not 0.3.
3. **Spread-aware gap floor.** Any gap-based limit strategy on gold should express its
   minimum gap in spread units (≥ 4× spread ≈ 2.5 pt, the only bucket with PF > 0.6 here)
   before anything else is tested; re-run A0 with gap ≥ 2.5 pt and stop pad 1.0 as the base.
