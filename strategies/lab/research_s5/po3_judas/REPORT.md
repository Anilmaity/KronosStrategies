# REPORT — Power of Three / Judas swing on XAU_USD at S5 (2026-09-18)

Study folder `lab/research_s5/po3_judas/`. Pre-registration: `PROTOCOL.md` (written first,
no deviations — §9 is empty). Scripts: `po3_common.py`, `run_descriptive.py`,
`run_tradeable.py`. Raw outputs every number below is copied from:
`results/descriptive.txt`, `results/tradeable.txt`, `results/a3_check.txt`; per-day tables
`results/days_A1|A2|A3.csv`; per-trade books `results/trades_A1|C1|A3|A1q.parquet`; C2 null
draws `results/c2_null_T60|300|900.csv`. Compute: 50 s + 3 s.

## The question

ICT's daily profile: Asia accumulates a range, London (07:00–10:00 UTC) manipulates by
sweeping one side of it — the *Judas swing* — and the day distributes the other way, so the
daily low of an up day (high of a down day) is made by that London sweep. Q1: does that
structure exist on gold beyond what a direction-preserving null predicts? Q2: is fading the
sweep on its reclaim tradeable beyond costs and two controls?

Sample: 527 trading days, 2024-09-02 → 2026-09-17 (S5 cache, 8,682,738 bars). TRAIN 321
days < 2025-12-01 ≤ TEST 206 days. Three session anchors, all pre-declared: **A1** UTC day
with fixed 07:00–10:00 UTC London (primary), **A2** NY day (17:00 ET, DST-aware) with the
same London window, **A3** UTC day with London = 08:00–11:00 Europe/London (the DST/4H-grid
question). Day direction = close vs the 07:00 price.

## Q1 — does the Judas structure exist?

### Where the daily extremes form (anchor A1, share of days)

| direction | LOW in Asia | London | post-London | NY late | post-break | | HIGH in Asia | London | post-London | NY late | post-break |
|---|---|---|---|---|---|---|---|---|---|---|---|
| UP (293) | **65.2** | 11.9 | 19.5 | 3.4 | 0.0 | | 13.0 | 3.1 | 32.8 | 27.3 | 23.9 |
| DOWN (234) | 17.5 | 2.1 | 38.0 | 29.9 | 12.4 | | **59.8** | 10.7 | 23.9 | 5.1 | 0.4 |

- **P(day low in London | up day) = 11.9 %**; unconditional 7.6 %; on down days 2.1 %.
- **P(day high in London | down day) = 10.7 %**; unconditional 6.5 %; on up days 3.1 %.
- The extreme opposite the close is in **Asia** on 65.2 % of up days and 59.8 % of down
  days: on gold the accumulation range *is* the day's extreme far more often than the
  manipulation is.
- "Judas proper" (extreme in London and opposite the close): **60 of 527 days = 11.4 %**.
  Anchors A2 / A3: 55 / 66 days (10.4 % / 12.5 %). Nothing hangs on the day anchor or on
  the DST question.
- Judas-extreme time, the 60 A1 days: median **82.8 min** after 07:00, IQR 40.7–121.3; the
  10-minute histogram is flat across the three hours (2–7 per bin), no cluster at the open.
- TRAIN vs TEST: P(low in London | up) 13.4 % → 9.3 %; P(high in London | down) 12.6 % →
  8.1 %; unconditional 9.0/8.1 % → 5.3/3.9 %. The London share of daily extremes is falling.

### Two nulls (the decisive one is B)

| anchor | statistic | observed | Null A (label shuffle) mean, 95 % band, p | Null B (within-day return shuffle) mean, 95 % band, p | excess over B |
|---|---|---|---|---|---|
| A1 | P(low in London \| UP) | 11.9 % (closes: 12.6 %) | 7.6 %, 5.5–9.6, **p < 0.0001** | 12.6 %, 8.9–15.7, p = 0.525 | **+0.0 pp** |
| A1 | P(high in London \| DOWN) | 10.7 % (closes: 10.3 %) | 6.4 %, 4.3–9.0, **p = 0.0002** | 17.8 %, 12.8–22.2, p = 0.995 | **−7.5 pp** |
| A2 | P(low in London \| UP) | 11.3 % (12.4 %) | 7.4 %, 5.5–9.3, p < 0.0001 | 12.0 %, 8.9–14.8, p = 0.435 | +0.4 pp |
| A2 | P(high in London \| DOWN) | 9.3 % (8.9 %) | 5.8 %, 3.8–8.1, p = 0.0024 | 17.6 %, 12.7–21.6, p = 1.000 | −8.7 pp |
| A3 | P(low in London \| UP) | 12.7 % (13.4 %) | 7.8 %, 5.7–9.7, p < 0.0001 | 12.6 %, 9.4–16.1, p = 0.385 | +0.8 pp |
| A3 | P(high in London \| DOWN) | 12.3 % (12.3 %) | 7.4 %, 4.8–10.1, p = 0.0004 | 17.4 %, 13.2–21.9, p = 1.000 | −5.1 pp |

Null A (the requested one: shuffle day directions across days, 5,000 perms) rejects
everywhere — the conditional rate is above the unconditional. Null B (200 perms/day) keeps
every day's Asian range, net direction and volatility and only permutes the order of the S5
returns from the London open to the close, so it reproduces the geometric fact that an up
day's low tends to be early. Against it the observed rate is **exactly the null** for lows
on up days (+0.0, +0.4, +0.8 pp, p ≈ 0.4–0.5) and **below the null** for highs on down days
(−7.5, −8.7, −5.1 pp, p ≥ 0.995). The "London makes the low of an up day" rate is the base
rate of a random ordering of that day's own moves; the London high of a down day happens
*less* often than that. This is the Traps §1 confound (an extreme that is early by
construction) in its descriptive form — Null A is the test that would have "confirmed" the
concept.

### The sweep itself, to the second (A1)

- 338 of 527 days (64.1 %) breach the Asian range in London (UP 190, DOWN 148; both sides 10).
  First breach at median 35.0 min after 07:00 (IQR 9.6–78.9).
- **Persistence: median 10 s beyond the level** (p10 5 s, p25 5 s, p75 45 s, p90 685 s);
  77.8 % of reclaimed sweeps are back inside within 60 s, 87.4 % within 300 s, 91.6 % within
  900 s. 95.3 % reclaim by 10:00, 98.5 % by 16:00 (5 days never do — those are breakouts).
- **Depth: median 0.53 pt** (IQR 0.15–1.77) against a London median spread of 0.58; only
  **47.3 %** of first breaches exceed the day's median spread ("real"), and among the
  sweeps that reclaim within 900 s only 41.6 %. Most "sweeps" at S5 are a one-bar poke of
  less than one spread.
- **The sweep side predicts continuation, not reversal.** First sweep DOWN (n = 148): the
  day closes UP vs 07:00 on **39.2 %** (reclaimed-by-10:00 subset, n = 140: 39.3 %); the
  day's low is in London on 26.4 %. First sweep UP (n = 190): closes DOWN on **31.6 %**
  (31.3 %); the day's high is in London on 17.9 %. A2/A3 the same within 2 pp.
- Asian range vs previous-day extreme in London: AR only 172, both with PD first 117, PD
  only 84, both with AR first 39, same bar 9, none 105. The first AR breach also exceeds the
  previous-day extreme on that side on 36.8 % of 337 days.

## Q2 — the pre-registered trade

Rule (PROTOCOL §5): first London breach of each side of the Asian range; reclaim (mid close
back inside) within T ∈ {60, 300, 900} s; fill at the next S5 bar's ask (long after a
sweep-down) / bid (short after a sweep-up); stop at the sweep extreme ∓ 0.2 pt; target T1 =
the opposite side of the Asian range (primary) or 2R (secondary); quote exits, stop before
target; time exit 16:00 UTC; costs 0.45 / 0.80. Events: 348 (343 reclaimed). One trade was
re-derived by hand from the raw parquet (2024-09-02 SELL: breach 08:44:25, reclaim 08:45:40,
fill 08:45:45 at bid 2504.70, stop 2505.955, hit 08:47:40) and matches the script exactly.

Geometry the rule produces (T = 900, T1): **median stop 1.36 pt, median target 26.09 pt, RR
17.8**; outcomes SL 302 / TP 8 / TIME 2 of 312. The pre-registered stop (sweep extreme +
0.2 with a median depth of 0.43) is about two spreads wide.

### All cells (bars at base cost; PF at 0.45 / 0.80; pts at 0.80)

| arm | T | target | TRAIN n | PF .45 | PF .80 | pts .80 | TEST n | PF .45 | PF .80 | pts .80 | TEST mo+ | corr | bars |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A1 | 60 | T1 | 169 | 0.176 | 0.139 | −270.0 | 95 | 0.555 | 0.488 | −145.4 | 0.1 | −0.39 | 2/6 FAIL |
| A1 | 60 | 2R | 169 | 0.344 | 0.229 | −192.1 | 95 | 0.849 | 0.697 | −59.5 | 0.5 | −0.21 | 2/6 FAIL |
| **A1** | **300** | **T1** | **193** | **0.187** | **0.149** | **−320.8** | **105** | **0.417** | **0.374** | **−232.3** | **0.1** | **−0.27** | **2/6 FAIL** |
| A1 | 300 | 2R | 193 | 0.400 | 0.276 | −213.3 | 105 | 0.811 | 0.687 | −80.7 | 0.5 | 0.19 | 2/6 FAIL |
| A1 | 900 | T1 | 201 | 0.171 | 0.137 | −354.0 | 111 | 0.305 | 0.279 | −357.7 | 0.1 | −0.36 | 2/6 FAIL |
| A1 | 900 | 2R | 201 | 0.396 | 0.280 | −232.0 | 111 | 0.584 | 0.510 | −183.7 | 0.3 | −0.06 | 2/6 FAIL |
| C1 | 60 | T1 | 205 | 0.335 | 0.267 | −274.5 | 123 | 0.455 | 0.396 | −205.8 | 0.2 | −0.01 | 2/6 FAIL |
| C1 | 300 | T1 | 239 | 0.341 | 0.276 | −337.5 | 137 | 0.536 | 0.470 | −213.6 | 0.3 | 0.11 | 2/6 FAIL |
| C1 | 900 | T1 | 242 | 0.329 | 0.268 | −352.4 | 144 | 0.603 | 0.533 | −207.1 | 0.3 | −0.02 | 2/6 FAIL |
| C1 | 60/300/900 | 2R | 205/239/242 | 0.225/0.250/0.287 | 0.154/0.178/0.208 | −281.5/−338.8/−331.2 | 123/137/144 | 0.451/0.613/0.628 | 0.358/0.501/0.520 | −171.6/−151.6/−161.8 | 0.1/0.3/0.2 | | 1–2/6 FAIL |

**Declared arm** (TRAIN selection, T1, PF at 0.80: T60 0.139, T300 0.149, T900 0.137 →
**T = 300**): TRAIN n 193, PF 0.187 / 0.149, −320.8 pts; **TEST n 105, PF 0.417 / 0.374,
−232.3 pts, WR 3.8 %, 1 of 10 TEST months positive**. Bars: n ✓, TEST PF ✗ ✗, TRAIN PF ✗,
monthly ✗, regime ✓ (both up- and down-month sums negative, −343.7 / −105.1, but |corr| =
0.27) → **2/6 FAIL**. Power: TRAIN sd 2.21 pts, TEST n 105 → MDE 0.43 pts/trade; TEST mean
−2.21 pts/trade, five MDEs below zero. The 2R secondary is also negative at every T on both
halves.

### Controls

- **C1 (same rule, no London; 00–04 range swept in 04–07 UTC)** loses too — TRAIN PF 0.276,
  TEST 0.470 at T = 300 — and loses *less* than the London arm on TRAIN (−337.5 on 239 vs
  −320.8 on 193 trades; per trade −1.41 vs −1.66).
- **C2 (time-shuffled, geometry-matched, 200 draws, ± 30 days)** — random entries at the same
  hour with the same stop and target distances:

| T | half | cost | real pts / PF | null pts mean (p5 – p95) | null PF mean | real at null percentile |
|---|---|---|---|---|---|---|
| 300 | TRAIN | 0.80 | −320.8 / 0.149 | −224.7 (−326.6 – −114.4) | 0.390 | **6 %** |
| 300 | TEST | 0.80 | −232.3 / 0.374 | −164.5 (−306.9 – 26.2) | 0.547 | 24 % |
| 300 | ALL | 0.80 | −553.2 / 0.261 | −389.3 (−553.2 – −201.0) | 0.464 | 5 % |
| 60 | TRAIN / TEST | 0.80 | −270.0 / −145.4 | −194.5 / −135.7 | 0.366 / 0.528 | 6 % / 50 % |
| 900 | TRAIN / TEST | 0.80 | −354.0 / −357.7 | −232.1 / −160.4 | 0.414 / 0.618 | **2 % / 2 %** |

At 0.45 the percentiles are identical (the cost is a constant shift). The random book loses
because a 1.4-pt stop against a 26-pt target with 0.6 of spread is a coin-flip stop; the
**real book sits at the 2–6th percentile of the random book on TRAIN and at T = 900 on
TEST** — the sweep-reclaim entry is measurably *worse* than a random entry at the same
geometry, which is what the descriptive already said (a sweep is followed by continuation
61–68 % of the time).

### Pre-declared splits of the declared arm (T = 300, T1; PF .80, pts at .80)

| split | TRAIN n / PF / pts | TEST n / PF / pts |
|---|---|---|
| all (PF .45 / .80) | 193 / 0.187 / 0.149 / −320.8 | 105 / 0.417 / 0.374 / −232.3 |
| 2024 · 2025 · 2026 | 55 / 0.195 / −75.7 · 138 / 0.134 / −245.1 · — | — · 9 / 0.0 / −24.8 · 96 / 0.401 / −207.6 |
| BUY · SELL | 81 / 0.172 / −141.1 · 112 / 0.130 / −179.7 | 52 / 0.502 / −103.2 · 53 / 0.211 / −129.1 |
| real sweep (depth > spread) · flicker | 62 / 0.163 / −136.8 · 131 / 0.138 / −184.1 | 54 / 0.288 / −184.4 · 51 / 0.572 / −47.9 |
| D1 bias with · against (98.7 % joined) | 96 / 0.167 / −160.4 · 93 / 0.136 / −153.3 | 45 / **0.0** / −136.7 · 60 / 0.592 / −95.6 |
| anchor A3 (DST-aware London; PF .45 / .80) | 184 / 0.396 / 0.320 / −241.8 | 103 / **1.174 / 1.053 / +18.4** |
| reclaim on the quote (PF .45 / .80) | 177 / 0.299 / 0.245 / −295.9 | 100 / 0.402 / 0.363 / −242.8 |
| 2R target (PF .45 / .80) | 193 / 0.400 / 0.276 / −213.3 | 105 / 0.811 / 0.687 / −80.7 |

Every year, both sides, real and flicker sweeps, with and against the D1 bias (the filter
makes it *worse*: 0 of 45 TEST winners) lose on both halves. The one positive cell, A3
TEST, is the expected spurious pass (≈ 27 cells, 1–2 expected): TRAIN PF 0.32, 3 of 10 TEST
months positive, and **one trade — 2026-02-04 SELL, +144.75 pts — is 7.9 × the TEST net**
(`results/a3_check.txt`; bars 4/6 FAIL).

## Verdict against the xau2y bars

**FAIL, 2/6 on the declared arm, and the concept does not beat either control.** No cell of
the 12 arms or ~15 subset rows passes; the only TEST PF > 1 is one trade in a robustness row
with TRAIN PF 0.32.

## What this does and does not establish

Established (527 days, S5):
1. The Judas *pattern* occurs on 10–12 % of gold days under any of three session anchors,
   and that rate is **exactly what a random re-ordering of each day's own moves produces**
   (Null B, +0.0 to +0.8 pp for lows on up days), and *below* it for highs on down days
   (−5 to −9 pp). The daily extreme opposite the close is in **Asia** on ~60–65 % of days.
2. A first London breach of the Asian range is followed by a close on the *same* side
   61–68 % of the time. On gold, the London "manipulation" is more often the start of the
   distribution than a false move.
3. At S5 the typical sweep is a **10-second, half-spread poke**: 78 % reclaimed within a
   minute, median depth 0.53 pt, 53 % smaller than the spread. The concept's "sweep" is,
   most of the time, not an event a trader could act on and not one that reverses.
4. Fading it with the pre-registered stop (extreme + 0.2 pt → median 1.36 pt) is a 3–4 %
   win-rate book that loses more than random entries with the same geometry (2–6th
   percentile of C2) and more per trade than the same rule without London (C1).

Not established: whether a *wider* stop (beyond the Asian-range mid, or ATR-scaled) rescues
the fade — the C2 null says the loss is stop geometry plus an adverse entry, so a wider stop
would at best converge on random; whether a reclaim required on M1/M5 closes instead of an
S5 close changes anything (the S5 reclaim fires on the first 5-second flicker, 78 % of
events; the "real sweep" split shows no improvement in that direction); anything about a
gold bear market (none in the window). Quote exits use the S5 close bid/ask (the shared
`s5exit` convention), which slightly understates intrabar stop hits — a bias in the rule's
favour, so the fail is if anything understated.

## Next hypotheses (pre-registered follow-ups, not tested here)

1. **Continuation, not reversal:** trade *with* the first London breach of the Asian range
   (long on a breach-up that holds ≥ 60 s on the quote, stop below the Asian-range mid,
   target 1× the Asian range), because 61–68 % of sweep days close on the sweep side and
   the fade sits at the 2nd percentile of random. Controls: C1-style pre-London breach and
   a C2 geometry match. Expect modest at best; the range-mid stop is what would make it
   different from this study.
2. **Asia makes the extreme:** the daily extreme opposite the close is in Asia on ~60–65 %
   of days; test whether the *Asian* extreme, once London has moved ≥ ¼ ADR away from it,
   is a valid session target (the AR extreme as a level, not as a sweep).
3. **Persistence gate:** the 8–12 % of sweeps that stay beyond the level > 300 s are the
   only ones that resemble a manipulation leg; their depth/duration vs day direction
   (n ≈ 40 here, underpowered) needs the 3-year M1 cache to reach n ≥ 100.
