# REPORT — ICT time windows at 5-second resolution: killzones, macros, Silver Bullet

Study folder: `lab/research_s5/killzones/` · pre-registration: `PROTOCOL.md` (written first) ·
scripts: `common.py`, `test1_concentration.py`, `test2_silver_bullet.py`, `test3_macros.py` ·
raw outputs: `results/*.txt`, tables `results/*.csv`, trades `results/t2_trades.parquet`,
events `results/events.parquet`. Every number below is copied from those outputs.

Data: XAU_USD S5 cache (8,682,738 bars) on a 13,209,492-slot 5-s grid; M5/M15 from
`bars_cache_2y` (`label="left"` verified against the S5 grid: 99.93 % high match, 0.70 % if
the label were treated as the bar end). Window 2024-09-01 → 2026-09-17, **522 valid trading
days (TRAIN 320 < 2025-12-01 ≤ TEST 202)**. Clock: `America/New_York` with DST resolved per
timestamp (offset changes found at 2024-11-03, 2025-03-09, 2025-11-02, 2026-03-08 — the
four real transitions). Trading day 18:00 → 17:00 NY; the venue reopens at 18:04:55 NY in
both DST regimes; clock denominator 1 380 min.

## The question
Do the ICT windows concentrate gold's intraday delivery at 5-second resolution — and does
any of it survive as a tradeable edge beyond a same-hour control under the xau2y bars?

## Calibration (pipeline check, pre-declared tolerance 1.4×–2.0×)
| row | this study (S5, 2 y) | Session-Timing note (M1, 3 y) |
|---|---|---|
| 08:00–11:59 NY share of daily H/L | **26.3 % vs 17.4 % clock = 1.51×** (TRAIN 1.46×, TEST 1.59×) | 29.9 % vs 17.4 % = 1.72× |
| first 15 min after reopen, daily H/L | 5.46× | 6.02× |
| first 30 min after reopen, daily H/L | 4.10× | 4.08× |

Within tolerance; the reopen artefact reproduces almost exactly. The pipeline is trusted.

## Test 1 — concentration of delivery (descriptive, placement-permutation null)
Events per day: H, L, the largest 60-s (X60) and 300-s (X300) range windows, and every
burst (30-s range ≥ 1.5 pt; 236,542 episodes; relative 0.04 %-of-price variant 248,829).
Ratio = pooled share inside the window set ÷ its clock share. Null = the same windows with
independent uniformly random starts, one placement applied to all days (2 000 draws) — it
asks whether the ICT placement beats a random placement of the same total duration.
Bootstrap CI = day-resampled sampling error of the observed ratio.

**Primary (union of each set), ALL days:**

| set (clock share) | kind | share | ratio [95 % CI] | null mean ± sd | z | p |
|---|---|---|---|---|---|---|
| killzones (52.2 %) | H/L | 53.7 % | 1.030 [0.974, 1.085] | 1.001 ± 0.227 | 0.13 | 0.44 |
| | X60 | 76.1 % | 1.458 [1.388, 1.520] | 0.994 ± 0.405 | 1.14 | 0.16 |
| | X300 | 75.9 % | 1.454 [1.381, 1.526] | 1.000 ± 0.398 | 1.14 | 0.16 |
| | bursts | 61.8 % | **1.184 [1.166, 1.202]** | 0.999 ± 0.102 | 1.80 | **0.030** |
| | bursts (rel.) | 65.1 % | 1.247 [1.228, 1.268] | 0.994 ± 0.142 | 1.79 | 0.038 |
| macros (7.2 %) | H/L | 9.8 % | 1.348 [1.124, 1.580] | 1.005 ± 0.383 | 0.90 | 0.16 |
| | X60 | 14.2 % | 1.956 [1.586, 2.340] | 0.994 ± 0.701 | 1.37 | 0.11 |
| | X300 | 14.0 % | 1.930 [1.519, 2.353] | 1.006 ± 0.688 | 1.34 | 0.13 |
| | bursts | 7.8 % | 1.069 [1.035, 1.107] | 0.993 ± 0.137 | 0.56 | 0.28 |
| | bursts (rel.) | 8.5 % | 1.168 [1.137, 1.200] | 1.007 ± 0.193 | 0.83 | 0.20 |
| Silver Bullet (13.0 %) | H/L | 13.8 % | 1.057 [0.899, 1.223] | 0.998 ± 0.369 | 0.16 | 0.38 |
| | X60 | 19.4 % | 1.483 [1.234, 1.748] | 1.008 ± 0.658 | 0.72 | 0.26 |
| | X300 | 16.1 % | 1.234 [1.013, 1.483] | 1.025 ± 0.668 | 0.31 | 0.31 |
| | bursts | 14.5 % | 1.109 [1.086, 1.134] | 1.004 ± 0.151 | 0.70 | 0.25 |
| | bursts (rel.) | 15.4 % | 1.179 [1.152, 1.203] | 0.994 ± 0.207 | 0.90 | 0.20 |

TRAIN / TEST agree in sign on every row (full table in `results/t1_concentration.csv`):
killzone-union bursts 1.310 (p 0.058) / 1.103 (p 0.030), relative 1.326 (p 0.059) / 1.174
(p 0.023); macros X60 2.286 (p 0.10) / 1.435 (p 0.20); SB X60 1.581 (p 0.23) / 1.328 (p 0.30).
Excluding the first 30 min after the reopen changes nothing materially (killzones X60
1.504, macros X60 2.018, SB X60 1.530, calibration H/L 1.674).

**Reading.** The point estimates say the ICT windows *do* hold more of the day's largest
expansions than their clock share (killzones 1.46×, macros 1.96×, SB 1.48× on X60, with
bootstrap CIs excluding 1). But the placement null says a *randomly placed* set of windows
of the same length reaches the same concentration 11–26 % of the time, because the day's
delivery is concentrated in a few spots (08:00–10:30 NY holds 48 % of all X60 events, 4.1×
/ 3.6× / 3.3× for the 08, 09, 10 hours) that a random window of 60–240 min often overlaps.
Only the killzone-union share of bursts clears p < 0.05 — in both splits — and at 1.18× it
is small. Fifteen primary comparisons were declared; 1–2 passes at p < 0.05 are the chance
expectation; two were observed (the two burst definitions on the same set, i.e. one effect).

**Per window (exploratory, ALL days, ratio / placement p):** the sets are heterogeneous.
M_0950 is the standout — X60 **5.68×** (p 0.034), X300 6.35× (p 0.018), H/L 2.84× (p
0.038); M_1050 X60 3.04× (p 0.056); SB_10 X60 3.31× (p 0.098); KZ_NYAM X60 2.81× (p 0.056);
KZ_LondonClose X60 2.25×. The afternoon windows are *below* clock: M_1515 X60 0.088×, M_1310
0.62×, SB_14 0.84×, SB_03 0.31×, KZ_London 0.34× (the London killzone is where the day's
expansions are *least* likely to be). "The macros" and "the Silver Bullet" as sets are an
average of one live window (09:50–11:10 NY, which contains the 10:00 ET data releases) and
dead ones.

**Sub-minute delivery (descriptive).** The single largest 60-s window delivers a median
16.2 % (TRAIN) / 18.0 % (TEST) of the day's range (IQR 12–24 %; 7.1 pt / 17.6 pt); the
largest 300-s window 26–28 %. Burst episodes are churn: the gross sum of |burst moves| is
5.6× (TRAIN) to 11.7× (TEST) the day's range while their net is 0.39–0.48× of it. The
absolute 1.5-pt threshold is not scale-free — 178 bursts/day covering 4.8 % of the clock in
TRAIN vs 746/day and 38.9 % in TEST (median day range 43.6 → 100.4 pt); the concentration
*ratios* are shares and unaffected, the counts are not comparable across splits.

## Test 2 — Silver Bullet, tradeable (pre-registered rule, no sweeps)
Rule: in each SB hour, direction = sign of the M15 bar ending at the window start; first M5
FVG in that direction with gap ≥ 1.5 pt and third bar inside the window; limit at CE from
the first S5 bar after the third bar's close until window end (long fills on `ask_c ≤ CE`);
stop = far gap edge ∓ 0.2 pt; target 2R; exits on `bid_c`/`ask_c`, stop before target; time
exit 17:00 NY; costs 0.45 / 0.80 on top. Control A = identical rule in the adjacent hours
(02–03, 04–05, 09–10, 11–12, 13–14, 15–16 NY). Control B = identical rule in 60-min windows
at random starts not overlapping the SB hours (10 draws).

Window outcomes (SB primary): TRAIN 93 trades / 148 unfilled limits / 710 no qualifying FVG;
TEST 142 / 137 / 323. Median gap 2.85 pt, median R 1.62 pt.

| arm | TRAIN n | WR | PF 0.45 | PF 0.80 | mean R @0.80 | TEST n | WR | PF 0.45 | PF 0.80 | pts 0.45 | pts 0.80 | mean R @0.80 | TEST months + | gold-up / gold-down pts | bars |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **SB (primary)** | 93 | 12.9 % | 0.242 | 0.185 | −1.168 | 142 | 16.9 % | **0.439** | **0.362** | −168.5 | −218.2 | −0.951 | 0 % | −250.4 / −127.1 | **FAIL** |
| SB, \|disp\| ≥ ATR (variant) | 18 | 5.6 % | 0.108 | 0.085 | −1.359 | 21 | 9.5 % | 0.236 | 0.203 | −47.1 | −54.5 | −1.128 | 12 % | −58.6 / −35.0 | FAIL |
| Control A (adjacent hours) | 201 | 12.9 % | 0.216 | 0.160 | −1.191 | 261 | 21.1 % | 0.606 | 0.503 | −211.1 | −302.5 | −0.835 | 0 % | −465.6 / −176.6 | FAIL |
| Control B (10 shuffles), TEST | — | — | — | — | — | 104–131 | 10.7–21.6 % | 0.17–0.56 | mean 0.305, p95 0.411 | | | mean −1.008, p95 −0.887 | | | FAIL ×10 |

Per SB hour (TEST, PF @0.80): 03–04 0.154 (n 49), 10–11 0.642 (n 52), 14–15 0.206 (n 41).
SB vs Control A on mean R @0.80: TRAIN −1.168 vs −1.191 (Welch t 0.17, p 0.86); TEST −0.951
vs −0.835 (t −0.92, p 0.36). Every TEST month is negative for every arm. Outcome mix: 199
SL / 36 TP / 0 TIME.

**Bars verdict: FAIL on every bar** (TEST PF 0.36–0.44 < 1 at both costs, TRAIN PF 0.19 <
0.9, 0 % months positive). The SB hour is indistinguishable from its neighbours and from
random hours — the time condition explains nothing; the *rule* loses everywhere.

**Diagnostics (required by the methodology note for a WR this low; `results/t2_diagnostics.txt`).**
The harness is sound — sample trade paths show fills at CE with no look-ahead and stops
struck by the printed bid/ask. The mechanism is quote geometry at 5 s: after a limit fills
at CE the adverse quote is already a median **0.89 pt** into a median **1.62 pt** risk
(spread 0.64 at fill + the fill bar's overshoot past CE, median 0.18), leaving **0.75 pt** of
residual stop against a 3.2-pt target — and a 1.5-pt move inside 30 s occurs on 39 % of
the TEST clock. Median hold is **70 s**; 34 of 235 trades stop on the fill bar itself.
Within 60 min of the fill the mid reaches +2R in 65.5 % of trades and −1R in 91.5 %: the
target is usually visited, after the stop. Mid-resolved exits (SL 189 / TP 46) are nearly as
bad as quote-resolved (199 / 36), so this is not only the bid/ask effect. Random-time
market entries with the same stop/target distances win 22.6–26.4 % (break-even 33.3 %
pre-cost); the CE pullback entry wins 13–17 % — a pullback into a fresh M5 gap at 5-s
resolution is a fill into momentum against the position, worse than random.

## Test 3 — macro continuation / reversal of the 20-min pre-drift
Null = a window of the same duration at a random common start (2 000 draws).

| split | macro | n | P(net move continues drift) | null | z | p (two-sided) | bursts | P(burst continues drift) | null | z | p |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TRAIN | 09:50 | 320 | 0.531 | 0.494 ± 0.030 | 1.26 | 0.22 | 3 321 | 0.498 | 0.499 ± 0.016 | −0.07 | 0.93 |
| TRAIN | 10:50 | 320 | 0.503 | 0.493 ± 0.031 | 0.33 | 0.75 | 2 379 | 0.497 | 0.500 ± 0.015 | −0.17 | 0.84 |
| TRAIN | 13:10 | 319 | 0.505 | 0.495 ± 0.029 | 0.35 | 0.72 | 1 637 | 0.503 | 0.500 ± 0.013 | 0.28 | 0.76 |
| TRAIN | 15:15 | 311 | 0.524 | 0.494 ± 0.029 | 1.02 | 0.30 | 1 184 | 0.530 | 0.499 ± 0.013 | 2.42 | 0.015 |
| **TRAIN** | **pooled** | 1 270 | **0.516** | 0.495 ± 0.015 | **1.43** | 0.15 | 8 521 | 0.503 | 0.499 ± 0.006 | 0.60 | 0.55 |
| TEST | 09:50 | 202 | 0.480 | 0.491 ± 0.036 | −0.30 | 0.81 | 1 527 | 0.499 | 0.496 ± 0.011 | 0.27 | 0.79 |
| TEST | 10:50 | 202 | 0.470 | 0.494 ± 0.035 | −0.66 | 0.55 | 2 386 | 0.481 | 0.497 ± 0.011 | −1.42 | 0.16 |
| TEST | 13:10 | 202 | 0.480 | 0.490 ± 0.036 | −0.28 | 0.78 | 3 367 | 0.496 | 0.497 ± 0.009 | −0.09 | 0.92 |
| TEST | 15:15 | 198 | 0.480 | 0.490 ± 0.036 | −0.29 | 0.74 | 2 513 | 0.501 | 0.497 ± 0.009 | 0.53 | 0.60 |
| TEST | pooled | 804 | 0.478 | 0.491 ± 0.018 | −0.78 | 0.44 | 9 793 | 0.494 | 0.497 ± 0.005 | −0.55 | 0.59 |

Median signed macro move in units of |pre-drift| is 0.07 / −0.04 / −0.01 / 0.02 (ALL) — zero.
**Gate: TRAIN pooled net z = 1.43 < 2 → the tradeable rule was not run, as pre-registered.**
The single per-macro burst hit (15:15 TRAIN, z 2.42) is 1 of 10 per-macro comparisons and
is 0.501 on TEST. Macros neither continue nor reverse the pre-drift; they are a coin flip.

What the macros *do* deliver is range without direction: the 09:50–10:10 window's median
high-low is **11.94 pt vs 6.39 pt for a random 20-min window (1.87×, p 0.018; TRAIN 1.88×,
TEST 1.84×)**; 10:50 1.33× (p 0.14); 13:10 0.93×; 15:15 0.76× (p 0.81) — the two afternoon
macros are quieter than an average 30 minutes.

## Verdict
1. **Concentration — yes as description, weak as a specific claim.** The ICT windows hold
   1.5–2× their clock share of the day's largest 60-s / 300-s expansions, reproducing the
   Session-Timing finding at 5-s resolution (calibration 1.51× vs 1.72×). But against a
   random placement of the same-length windows only the killzone-union share of bursts is
   significant (1.18×, p 0.03, both splits), and the effect is really *one* window —
   09:50–11:10 NY (the 10:00 ET data half-hour), 3–6× — averaged with windows that are at
   or below clock (15:15 macro 0.09×, London killzone 0.34×, 03:00 SB 0.31× on X60).
2. **Tradeable — no.** The Silver Bullet rule fails every xau2y bar (TEST PF 0.36 at 0.80,
   WR 17 %) and is statistically identical to the same rule in the adjacent hours and in
   random hours: the time window is not doing any work; the entry geometry (a 0.75-pt
   residual quote-stop on a CE pullback) is what loses. The macros carry no directional
   information at the burst or net level (0.48–0.52 vs null 0.49) and the tradeable gate
   was not triggered.
3. Consistent with the Session-Timing note: **time-of-day buys opportunity (range), not
   accuracy (direction)** — now measured down to the second.

## What this does and does not establish
- Establishes: where the day's expansions fall on gold at 5-s resolution (08:00–10:30 NY,
  the reopen gap, and nothing much after 13:00), that the ICT macro/SB sets are internally
  heterogeneous, that a gap+0.2 stop cannot survive 5-s quote noise at 2025–26 volatility,
  and that neither SB entries nor macro drift have directional content the same-hour
  controls lack.
- Does not establish: anything about a differently-geometried SB rule (wider stop, swing
  target — not tested, deliberately); the London macros (02:33–03:00, 04:03–04:30, not in
  scope); regime behaviour outside a 2-year bull/topping market; the counts of bursts
  across splits (absolute and %-of-price thresholds both drift with the volatility regime).
- Comparisons made: 15 primary Test-1 + 2 Test-2 + 10 Test-3 = 27 (+ exploratory
  per-window rows, flagged). Expected chance passes at p < 0.05: 1–2. Observed: 2 (one effect).

## Next hypotheses (each would need its own pre-registration)
1. **09:50–11:10 NY as a range window, not a direction window.** It delivers 1.9× the range
   of a random 20-min window with zero drift-continuation. Test a direction-agnostic
   structure keyed to the first S5 burst after 10:00 ET (direction of the first ≥ 1×
   M5-ATR burst predicts the remaining window's delivery?), stop ≥ 1× M5 ATR so the 5-s
   noise cannot strike it, control = same rule at 12:00–13:20.
2. **The SB failure is stop geometry, not timing.** Re-run the identical SB entry with the
   stop at 1.0 × M5 ATR(14) beyond the gap (residual quote-stop ≫ 0.75 pt) and 2R; it must
   beat the random-time same-geometry control (23–26 % WR here), not just break even.
3. **Bursts inside 08:00–12:00 NY vs elsewhere: directional content.** The killzone burst
   concentration (1.18×) is the one placement-significant effect; measure whether a burst's
   sign predicts the next 5 minutes better inside that block than outside (same-hour
   control = 12:00–16:00), before building anything on it.
