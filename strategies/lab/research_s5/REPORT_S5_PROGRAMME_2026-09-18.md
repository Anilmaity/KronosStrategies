# S5 research programme — six studies on 24 months of 5-second XAU_USD (2026-09-18)

Six independent, pre-registered studies run in parallel (one agent each, isolated context)
on the QA'd S5 cache (`lab/QA_S5_2026-09-18.md`: 8.68 M bars 2024-08-15 → 2026-09-18), all
to the same rules (`BRIEF.md`): protocol written before any number, TRAIN < 2025-12-01 ≤
TEST, 0.45 / 0.80 costs on top of quote-level fills and exits, a matched control for every
tradeable claim, the xau2y bars, every number copied from script output. ~88 minutes of
wall-clock, ~1.0 M agent tokens. Each folder holds `PROTOCOL.md`, scripts, `results/`,
`REPORT.md`; this page is the integration, and quotes only the studies' own numbers.

## The one-table answer

| study | concept | tradeable arm, TEST PF @0.45 / 0.80 | control | verdict |
|---|---|---|---|---|
| `sweeps/` | sweep-and-reclaim of PD / Asia / H1 / London levels (52,368 events) | 0.593 / 0.507 (best of 24) | random entries 0.627; touch-without-reclaim better in 24/24 rows | **FAIL** |
| `fvg_displacement/` | S5 displacement quality inside an M5 FVG (20,229 gaps) | 0.60 / 0.52 (best of 14 cells) | feature filter at the 59–62nd pct of a random subset once gap size + killzone are held | **FAIL** — feature TEST AUC 0.500 |
| `killzones/` | killzones, macros, Silver Bullet (522 NY days) | Silver Bullet 0.44 / 0.36 | adjacent hours p 0.36; time-shuffled p95 0.41 | **FAIL** (range yes, direction no) |
| `crt/` | H1 / H4-UTC / H4-NY candle range theory (12 arms) | 0.75–0.90 both costs | random C3 loses the same; on H4-UTC 85 % of random draws beat the real sweeps | **FAIL** |
| `po3_judas/` | Asian range → London manipulation → distribution (527 days) | 0.417 / 0.374 | pre-London sweeps lose less; real book at the 2–6th pct of geometry-matched random books | **FAIL** |
| `execution/` | spread, gap-through, trigger geometry, cost model (45,537 Stage-1 trades) | — (descriptive) | matched random entries pay the identical geometry curve | **cost model delivered**; spread-unit fit failed TEST (−27 %), flat-points fit noted post-hoc |

**Zero of the five ICT concepts produced a tradeable edge beyond its control at 5-second
resolution, on 24 months, under honest execution.** In every case the control — random
entries with the same geometry, hours and count — lost the same amount, which means the
losses are the *geometry* (stop and target placed within one or two spreads of a
quote-level fill) and the concept neither adds nor subtracts. Comparisons made: ≈ 24 + 14 +
15 + 12 + 27 + (descriptive) tradeable cells; the 1–3 single-bar passes expected by chance
appeared (a p 0.04 feature on TRAIN that vanished on TEST; one +145-pt trade making an
A3 anchor "pass"; 4 of 33 post-hoc CRT slot cells > 1 vs 1.7 expected) and were reported as
such, not adopted.

## What the S5 resolution *did* establish (the descriptive results are the value)

1. **What a "sweep" is on gold.** Median depth 0.45–0.51 pt (~0.7 spreads), reclaimed in
   5–10 s; 29–34 % reclaim inside the crossing bar; only ~7 % reach the 2–3-pt "stop-hunt"
   shape. After a reclaim the mean drift is in the *continuation* direction (−0.15 pts at
   15 min, CI excludes 0), the opposite of the fade. The Judas study agrees from the other
   side: a London sweep-down day closes up only 39 % of the time. (`sweeps`, `po3_judas`)
2. **The Judas swing is a base rate.** P(day low in London | up day) = 11.9 % vs a
   within-day shuffle that preserves direction and range of 12.6 %; highs on down days sit
   7.5 pp *below* the null. The extreme opposite the close forms in **Asia** on 60–65 % of
   days. (`po3_judas`)
3. **Time windows concentrate range, not direction.** The 08:00–11:59 NY block holds 1.51×
   its clock share of daily extremes (vault note: 1.72×, reproduced within tolerance); the
   live window is **09:50–11:10 NY** (5.7–6.3× on 60-s / 300-s expansions, p ≈ 0.02 both
   halves) while the 15:15 macro, the London killzone and the 03–04 Silver Bullet are below
   clock. Bursts are churn: gross 6–12× the day's range, net 0.4×. Macros neither continue
   nor reverse the pre-drift (TRAIN z 1.43). (`killzones`)
4. **Displacement quality is not information.** Seven S5 features of the impulse bar; the
   best has TEST AUC 0.500. Larger gaps hold slightly *less* often (−2.6 pp TEST). The
   "respected" base rate is 50 / 53 % (a coin) and 25 % once price returns as deep as CE.
   (`fvg_displacement`)
5. **The C2-wick artefact generalises.** 47–57 % of intraday CRT "deliveries beyond the C2
   open" are trivial (entry already past the target); the honest C3-open quote entry with a
   C1-extreme target removes it and the edge with it. The daily CRT result (WR 60 on n 25)
   has a 95 % CI of 41–79 pp, containing the intraday 46–50. (`crt`)
6. **Execution, measured.** Spread 1.2–2.3 bp of price (flat in bp, so 0.39 → 0.99 → 0.54
   pts across the window; TRAIN months 0.57, TEST 0.73). Gap-through slippage per stop-out
   0.29–0.60 by hour and **doubled from 2025 to 2026** (0.27 → 0.62), confirmed on 25,794
   real quote stop-outs. Trigger geometry is a flat **0.275 pts/trade** across all 15
   strategies and their random controls — generic, not ICT-specific — and only *looks*
   worse for tight stops because their gross is small. Three modules book nominal entries
   the market never offered (s97 +2.4 pts/trade → its TEST PF 2.24 becomes 0.30; s96 +1.3;
   s95 −1.1); **c03 and s14 have zero nominal gap**. (`execution`)

## Decisions this implies

- **Retire 0.45 as a base cost.** Measured all-in cost 07–16 UTC is 0.75 (TRAIN) / 0.96
  (TEST). For the mid-M1 harness: **0.75 base / 1.00 stress**; for quote-S5 resolution,
  which already carries the 0.27 geometry: **0.45 / 0.70**. The hour × stop table
  (`execution/results/cost_model.csv`) changes no Stage-1 verdict — c03 and s14 pass at 1.00
  on mid-M1 by the s5exit numbers (their quote-S5 PF at 0.80 is 1.25 / 1.23 with the floor).
- **Quote-level fills are mandatory for any stop or target inside ~3 spreads.** Every
  failed arm here put its stop within 1–2 spreads of a quote fill; random-time entries at
  the same geometry beat the "concept" entries. This is the same lesson as s14's 3.0-pt
  floor and s97's disqualification, now with the mechanism measured.
- **Gap-limit / CE-pullback entries need a spread-denominated floor (≥ ~2.5 pt gap) before
  they are worth testing again**; the Silver Bullet and c03-like CE arms both died on
  residual stops of < 1 pt.
- **Next hypotheses worth a pre-registration** (from the studies' own lists): trade *with*
  the London breach of the Asian range (range-mid stop, 1× range target); continuation
  after a deep (≥ 2-spread) Asia/London sweep; a direction-agnostic 09:50–11:10 NY range
  structure; multi-candle hold for H4 CRT; s96's nominal-entry term folded into its arm.

## What this does not say

Nothing about a gold bear market (none in the window). Nothing about the roster's live
fidelity beyond what `execution` measured on the harness trades. Nothing about ICT concepts
as *discretionary* context — every test here is a mechanical rule with a fixed geometry, and
each report is explicit that the geometry, not the concept, decided the outcome. A concept
can be true and untradeable at these stop distances; the studies' next-hypothesis lists are
the honest way to find out.
