# PROTOCOL — ICT time windows at 5-second resolution (killzones, macros, Silver Bullet)

Pre-registered 2026-09-18, **before any number was computed**. Follows
`lab/research_s5/BRIEF.md`. Anything changed after results exist is recorded at the bottom
under "Deviations", with the reason.

## Question
1. Do the ICT time windows (killzones, the four NY "macros", the three Silver Bullet hours)
   concentrate gold's intraday *delivery* — the bars that form the day's high/low, the
   largest 60 s and 300 s range expansions, and sub-minute "bursts" — beyond their share
   of the clock, and beyond what a same-length window placed at random would capture?
2. Does the Silver Bullet entry (first M5 FVG in the direction of the prior 15 m
   displacement, entry at CE) survive as a tradeable edge against the identical rule in
   the adjacent non-SB hours and in time-shuffled windows, under the xau2y bars?
3. Do the macros deliver continuation or reversal of the 20 m pre-macro drift at the
   burst level, and — only if the descriptive effect is > 2σ on TRAIN — is that tradeable?

## Data
- S5: `backtest/results/bars_cache/s5/XAU_USD/*.parquet` via `lab.tools.qa_s5_cache.load_s5`
  (mid o/h/l/c, `bid_c`, `ask_c`). Loaded once, kept as numpy arrays, placed on a global
  5-second slot grid (slot = unix_seconds // 5). Missing slots are forward-filled with the
  previous close (h = l = c = prev close) so that rolling windows are time-based, and are
  flagged `has_bar = False`.
- M5 and M15: `backtest/results/bars_cache_2y/is_XAU_USD_5m.parquet`, `_15m.parquet`,
  `label="left"` (bar `time` = its OPEN; a bar's information exists at `time + 5/15 min`).
  Day bars: `is_XAU_USD_1d.parquet` for the gold-up/gold-down month labels.
- Study window: **2024-09-01 → 2026-09-17** (both S5 and M5 cover it). **TRAIN < 2025-12-01
  ≤ TEST** (TRAIN 15 mo, TEST 9.5 mo). Feed spike 2025-12-25 23:00–23:15 UTC is excluded
  from all event candidates and no trade may be open across it (that Christmas day fails
  the trading-day validity rule below anyway).

## Clock conventions (stated once, used everywhere)
- Local clock: **`America/New_York` with DST** (the Session-Timing note settled this
  empirically). Every S5/M5 timestamp is converted with `tz_convert("America/New_York")`
  per bar, so the UTC offset (−4 in summer, −5 in winter) is resolved per date, not by a
  fixed constant. The DST transitions inside the window (2024-11-03, 2025-03-09,
  2025-11-02, 2026-03-08) are printed by the script as a check.
- **Trading day**: 18:00 NY → 17:00 NY next day (the venue's daily break is 17:00–18:00
  NY). Day label = the NY calendar date of the 17:00 close. Trading-day minute
  `tdm ∈ [0, 1380)` = minutes since 18:00 NY. **Clock denominator = 1380 min (23 h)**, the
  same convention that gives the Session-Timing note's 17.4 % for 08:00–11:59.
- **Valid day**: first-to-last S5 bar span ≥ 20 h and no internal gap > 60 min. Only valid
  days enter every test (Sundays/holidays/early closes drop out).

## Windows (NY local, half-open [start, end))
| set | windows | minutes | clock share |
|---|---|---|---|
| Killzones (ICT taught convention, per the corpus `killzones.yaml`) | Asia 20:00–00:00 · London 02:00–05:00 · NY AM 07:00–10:00 · London Close 10:00–12:00 | 240/180/180/120, union 720 | 52.2 % |
| Macros (NY) | 09:50–10:10 · 10:50–11:10 · 13:10–13:40 · 15:15–15:45 | 20/20/30/30 = 100 | 7.2 % |
| Silver Bullet | 03:00–04:00 · 10:00–11:00 · 14:00–15:00 | 60 × 3 = 180 | 13.0 % |
| Calibration block (Session-Timing note) | 08:00–11:59 | 240 | 17.4 % |
| Reopen artefact rows (calibration only) | 18:00–18:15, 18:00–18:30 | 15 / 30 | 1.1 / 2.2 % |

Each set is reported per window and as the union.

## Test 1 — descriptive concentration (permutation null)
Events per valid trading day, each carrying the `tdm` of the S5 bar (or first slot) that
forms it:
- **H / L**: the S5 bar whose `h` is the day's high; whose `l` is the day's low (first
  occurrence if tied). Two events per day.
- **X60 / X300**: the 12-slot (60 s) and 60-slot (300 s) trailing windows with the largest
  `max(h) − min(l)` in the day; event time = the window's first slot. One each per day.
  Candidate windows may not straddle the daily break: windows whose first slot lies in the
  first 5 min after the day's first bar are excluded (the reopen gap is a jump, not
  delivery).
- **Bursts**: a 6-slot (30 s) trailing window whose range ≥ **1.5 pt** (absolute, as
  specified by the topic). Consecutive qualifying slots are merged into one *episode*;
  event time = the episode's first qualifying window's first slot; signed move = close at
  the episode's last slot − close at the slot before its first window. Same reopen
  exclusion. Because 1.5 pt is not scale-free across a 2 450 → 4 350 price span, a
  secondary threshold **0.04 % of price** (≈ 1.5 pt at ≈ 3 750) is reported as a robustness
  row, not as a separate claim.
- Also reported (descriptive only, no claim): share of the day's clock spent in burst
  episodes; Σ|burst signed move| / (day high − day low).

Statistic per (event type, window set): **share** = events inside / all events (pooled
over days, and, as a check, the mean of per-day shares); **concentration ratio** = share /
clock share. Reported for ALL, TRAIN, TEST.

**Null (pre-registered, 2 000 draws)**: for each draw, every window of the set gets an
independent uniformly random start in [0, 1380) minutes (same duration, wrapping within the
trading day; the same random placement is applied to *every* day so the null asks "is this
clock placement special?" — a per-day shuffle would only test non-uniformity, which is
trivially true). The draw's ratio uses the union clock-share of the shifted windows. p =
fraction of draws with ratio ≥ observed (one-sided, concentration), and the observed z =
(obs − mean_null)/sd_null is reported. Robustness rows: excluding the first 30 min after
the reopen; the relative burst threshold.

**Calibration**: the 08:00–11:59 block on H/L must reproduce the Session-Timing note's
1.72× (29.9 % vs 17.4 %) to within what 2 years vs 3 years of a different feed allow
(pre-declared tolerance: 1.4×–2.0×). If it does not, the pipeline is suspect and the study
stops until the discrepancy is explained.

## Test 2 — Silver Bullet, tradeable (one pre-declared rule, no sweeps)
Fired in each SB window `[t0, t0 + 60 min)`:
1. **Direction** = sign of the completed M15 bar ending at `t0` (the bar with `time =
   t0 − 15 min`: `close − open`). SB starts are on the hour, so this bar is exactly the
   prior 15 minutes. Zero displacement → no trade. (Robustness variant, counted as one extra
   comparison: |displacement| ≥ M15 ATR(20).)
2. **Signal** = the first M5 FVG in that direction whose third bar opens at or after `t0`
   and closes at or before `t0 + 60`. Bullish FVG: `low[i] > high[i−2]`, gap =
   `[high[i−2], low[i]]`; bearish: `high[i] < low[i−2]`, gap = `[high[i], low[i−2]]`. Gap
   size must be ≥ **1.5 pt** (so that 2R clears the 0.80 cost; declared here, not tuned).
   Signal time = `time[i] + 5 min` (the third bar's close). One trade per window at most.
3. **Entry** = limit at the gap's CE (midpoint), working from the first S5 bar strictly after
   the signal time until `t0 + 60`; fills when `ask_c ≤ CE` (long) / `bid_c ≥ CE` (short), at
   CE. Unfilled by window end → no trade (recorded as "no fill").
4. **Stop** = far gap edge ± 0.2 pt (long: `high[i−2] − 0.2`; short: `low[i−2] + 0.2`).
   R = |CE − stop| = gap/2 + 0.2.
5. **Target = 2R** (pre-declared; the nearest-prior-swing alternative is *not* used).
6. **Exits on the quote**: long stop when `bid_c ≤ stop`, target when `bid_c ≥ tp`; short on
   `ask_c`. Stop checked before target within a bar; the fill bar itself is checked for the
   stop. **Time exit** at the last S5 bar before 17:00 NY of that trading day, at the quote.
7. **Costs 0.45 and 0.80 pt** round trip subtracted from every trade on top of the quote
   exits. Points-primary, no sizing; R-multiples reported alongside.

**Controls** (identical rule, only the window start moves):
- **Control A — adjacent hours**: the same rule in `[t0 − 60, t0)` and `[t0 + 60, t0 + 120)`
  for each SB window (02–03, 04–05, 09–10, 11–12, 13–14, 15–16 NY). Same direction rule
  (the M15 bar ending at that window's own start).
- **Control B — time-shuffled**: per valid day and per SB slot, one 60-min window with a
  uniformly random start in the trading day (start not inside any SB window, window fully
  inside the day and ending ≥ 60 min before 17:00 NY), 10 independent draws → the
  distribution of the control's TEST PF / mean R; the SB rule is credited only with what
  exceeds the control's 95th percentile.

**Verdict bars (xau2y)**: TEST n ≥ 40; TEST PF > 1 at 0.45 AND 0.80; TRAIN PF > 0.9 at
0.45; ≥ 55 % of TEST months positive and no month > 50 % of TEST net; regime independence
(positive in gold-up AND gold-down months, or |corr| < 0.4); plus the control test: SB TEST
mean R at 0.80 must exceed Control A and Control B's 95th percentile.

**Power (computed before trades are simulated)**: with ~330 TRAIN / ~200 TEST valid days
and ≤ 3 trades/day, the SB book is at most ~600 TEST trades and realistically 150–300 after
"no FVG"/"no fill". With sd(R) ≈ 1.2 for a 2R book, SE(mean R) ≈ 1.2/√200 ≈ 0.085R, so the
minimum detectable effect at 2σ is ≈ **0.17R per trade**. Anything smaller is a null
result by construction, not evidence of a small edge.

## Test 3 — macro fade / continuation
For each macro `[t0, t1)` on each valid day:
- **Pre-drift** `d = c(t0) − c(t0 − 20 min)` (last S5 close at or before each instant).
- **Burst-level statistic**: among burst episodes (Test 1 definition) that start inside
  the macro, `cont = P(sign(burst move) = sign(d))`, pooled. **Net statistic**:
  `P(sign(c(t1) − c(t0)) = sign(d))`. Also the mean signed macro move in units of |d|.
- **Null**: the same two statistics for a window of the same duration placed at a random
  start (2 000 draws, common placement across days, start ≥ 20 min after the reopen and
  the window not straddling the break). z and one-sided p in the direction of the observed
  deviation from 0.5, per macro and pooled over the four.
- **Tradeable, pre-declared and conditional**: only if the pooled *net* statistic on TRAIN
  deviates from the null by > 2σ, one rule is run once on TEST: at `t0`, market entry at the
  next S5 bar (long at ask / short at bid) in the direction implied by TRAIN (continuation
  → with `d`; reversal → against `d`); stop = the opposite extreme of the 20-min pre-drift
  window ± 0.2 pt; target 2R; time exit at `t1` on the quote; costs 0.45/0.80; Control A =
  the same rule at `t0 − 30 min` and `t0 + 30 min`; judged by the same bars as Test 2. If
  the TRAIN effect is < 2σ, no trade is simulated and the section reports the numbers only.

## Comparisons declared
Test 1: 3 window sets × 5 event types (H, L, X60, X300, bursts) = 15 primary, + calibration
and robustness rows (not claims). Test 2: 1 primary rule judged jointly at two costs, +1
robustness variant. Test 3: 2 pooled statistics + 8 per-macro = 10, + ≤ 1 conditional trade
rule. **≈ 27 comparisons → 1–2 single-bar passes at p < 0.05 are expected by chance.** A
claim needs the joint bars, not one p-value.

## What this study will not do
No parameter sweeps on the SB rule (gap floor, target, hold are fixed above). No TEST
reads before the TRAIN tables are written to `results/`. No changes to windows after
seeing where the events fall. No edits outside `lab/research_s5/killzones/`.

## Deviations
(none at pre-registration)

### Deviations recorded after results existed (additions only; no rule, window, threshold or split changed)
1. Test 1: added a day-bootstrap 95 % CI (500 resamples) on each primary UNION ratio, so the
   estimate's sampling error is shown next to the placement null; and per-window placement
   p-values (500 draws, ALL split) labelled *exploratory*, not claims.
2. Test 2: after the primary arm returned a 13–17 % win rate, the diagnostics the
   Backtest-Methodology-Traps note requires were run (sample trade paths, quote geometry at
   fill, mid-resolved outcomes, MFE/MAE, a random-time same-geometry control). They are
   reported as diagnostics; the rule and its verdict are unchanged.
3. Test 3: added the range delivered inside each macro vs the placement null (descriptive).
