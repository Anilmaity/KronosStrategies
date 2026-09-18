# Protocol — the TTrades "no entry without a higher-timeframe reason" rule as an overlay on the book

Pre-registered 2026-09-18, before any number in this folder existed. Deviations are appended
at the bottom with timestamps; nothing above the deviations line is rewritten after results.

## 0. Question

The TTrades method's governing rule (`overtrading-gate`, `structure-requires-htf-context`,
`no-fading-the-daily-candle`, spec §2.1): **no entry without a higher-timeframe reason; a
lower-timeframe shift against the HTF is a retracement into an HTF PD array and is expected
to fail.** Has never been tested on this project's strategies. Two questions, in order:

1. **Does trading against the HTF bias "fail" on gold, and by how much?** On the 15 Stage-1
   modules' own entries (45,537 trades), does the against-bias subset lose (PF < 1) while the
   aligned subset keeps or improves the module's edge — under each of several mechanical
   definitions of "HTF reason" drawn from the corpus?
2. **Is the rule a usable overlay for the four survivors** (c03, s14, s95, s93) under the
   project's Stage-2 bars (6–7) at the new costs under quote-S5 resolution — and does the
   corpus's definition beat a "dumb" HTF filter (daily SMA20 regime) and a random gate with
   the same admitted fraction?

## 1. Why applying the gate to a saved trade list is legitimate here (and its one limit)

Every gate below is a pure function of `(entry_time, side, entry_px)` and of higher-timeframe
bars that **closed before `entry_time`**. It never touches the strategy's internal state
(`_pending`, cooldown, dedup memory), never changes a stop or a target, and never changes
which bar `get_signal()` sees — so the S100 `_pending` lesson (a filtered CSV flipping a sign
because the *strategy's* state changed under the filter) does not apply: the labelled trade
list is exactly the set of trades the strategy generated, each with a label computed from
information available before its fill. Filtering it is the same as inserting the gate in
the harness *after* `get_signal()` (where `sides` / `regime` / `block_hours` already sit)
**except for one thing**: the harness's `max_concurrent` / `cooldown` re-admission — a
trade the gate blocks frees a slot, so a re-run could admit a later signal the saved list
never contained. Those re-admitted trades would be *aligned* trades by construction (only
aligned trades are admitted), so the filter under-counts the gated arm's admitted set, never
over-counts it. **Appendix check (pre-declared):** for the four survivors, the primary gate
(B1) is also run *inside* a copy of the harness (`harness_gate.py`, adds one `Cfg` field, no
other change) and the re-run's admitted trades are compared with the filtered list (count,
points, PF). Agreement within the re-admitted remainder is the expected result; a sign
change between the two would void the filter approach for that module.

## 2. Data

- Trades: `lab/results/xau2y_stage1/<module>_c0.80.trades.parquet` (15 modules; the harness
  charges `cost_pts` once at entry, so `raw = pts + 0.80`, re-costed exactly at any cost).
  Window 2024-09-01 → 2026-09-17, TRAIN < 2025-12-01 ≤ TEST.
- HTF bars: NY-anchored daily and weekly candles built **from the QA'd M1 cache**
  (`backtest/results/bars_cache_2y/is_XAU_USD_1m.parquet`), trading day = 18:00 NY → 17:00 NY
  on the DST-aware `America/New_York` wall clock (settled in `Session Timing on Gold`;
  venue break 17:00–18:00, zero bars in NY hour 17 — verified on this cache). Weekly = the
  trading days Sun 18:00 NY → Fri 17:00 NY. A reference day with < 600 M1 bars (holiday /
  feed hole: 2025-07-03, 2025-12-08, 2025-12-23) is **invalid as a reference candle**
  (bias → none; ranges skip it).
- Dumb control bars: the cache's UTC-midnight daily (`is_XAU_USD_1d.parquet`), exactly as
  `lab.harness.RegimeGate` consumes it (bar closed once `now >= time + 1 day`).
- S5 quotes for the live-trigger resolution: `backtest/results/bars_cache/s5/XAU_USD/*.parquet`
  (`lab/QA_S5_2026-09-18.md`).

## 3. The gates — each stated with what it consumes and when that information exists

All gates return one of `aligned` / `against` / `none` for a trade. `none` = the method says
"no bias → stand aside" (`daily-bias-framework`, spec §2.8); the gated arm admits `aligned`
only. Timestamps are left-labelled bar starts; a bar's information exists at its close.

| id | corpus definition | computation | available from | look-ahead assertion |
|---|---|---|---|---|
| **B1 PCE-D** | previous-candle engine on the daily (`daily-bias-framework`, spec §2.3) | last closed valid NY daily candle i vs i−1: took high only & closed above prev high → bullish; took high only & closed back inside → bearish; took low only & closed below → bearish; took low only & closed inside → bullish; both sides → none; inside bar → last resolved side-taking (trend), none if 3 consecutive inside bars (range-bound) | close of candle i (17:00 NY) | `entry_time ≥ utc_end_close(i)` and candle i ≠ the trade's own trading day |
| **B2 PD-3d** | premium/discount of the dealing range (`premium-discount-equilibrium`; range = `previous-day-lookback-three-days`) | EQ = (max high, min low) midpoint of the last 3 closed valid NY days; long aligned iff `entry_px < EQ` (discount), short aligned iff `entry_px > EQ` (premium); `entry_px == EQ` → none | close of day i | as B1 |
| **B3 DAY** | do not fade the current daily candle (`no-fading-the-daily-candle`; its own `measurable`: "expectancy of daily-aligned vs daily-opposing entries on the same signal set") | today's NY open O = open of the trading day's first M1 bar; long aligned iff `entry_px > O`, short aligned iff `entry_px < O`; equal → none | close of the day's first M1 bar | `entry_time ≥ first_bar_time + 1 min` |
| **B4 PCE-W** | the same engine on the weekly (fractal, `daily-bias-framework`) | B1's rule on NY-anchored weekly candles, last closed week | Friday 17:00 NY | `entry_time ≥ week_end_close` |
| **B5 STRUCT-D** | daily market structure — last body-close break of a 3-bar fractal swing (pillar-01 BOS/CHoCH; `swing-point`) | on closed NY daily candles: bullish from the first close above the most recent confirmed swing high until a close below the most recent confirmed swing low, and mirror; none before the first break | close of the breaking daily candle (swing confirmation needs the right-hand candle closed) | as B1 |
| **B6 STACK** | the method's stacking: daily bias **and** do not fade the developing candle (spec §2.1 items 1 + "do not fade") | aligned iff B1 aligned and B3 aligned; against iff B1 against or B3 against; else none | max of the two | both |

Two-sided readings, declared now so the complement is not "discovered": B2's inverted reading
(`premium-discount`, spec §8.3: "the upper half must be respected for price to trade higher")
is the exact complement of the classic reading, so one partition reports both. B3's complement
is the "premium/discount vs the daily open" reading (`deep-premium-deep-discount`,
trading-knowledge `htf_bias.md`: below the daily open = discount = buy) — momentum vs
mean-reversion on the same partition; the TTrades reading (momentum) is primary.

Robustness axes (bar 6 plateau where one exists), fixed now, three values each, primary in
bold: B2 lookback **3** / 2 / 5 days; B3 deadband **0** / 0.05 / 0.10 × ADR20 (mean of the
last 20 valid daily ranges; inside the deadband → none); B1 anchor **NY 18:00** / UTC 00:00
(the cache's daily; binary, reported as a sensitivity, not a plateau); B5 swing width
**3-bar** / 5-bar. B4, B6 have no axis.

## 4. Controls

- **C1 random gate, same admitted fraction (primary null)** — day-block sign flip: for each
  trading day, with probability ½ swap `aligned` ↔ `against` for every trade of that day
  (`none` stays). Preserves the admitted fraction in expectation and the within-day
  clustering of outcomes (trades on one day share the price path; a trade-level shuffle
  would understate the null variance). 2,000 flips per cell. Reported: two-sided p-value of
  ΔR, and the 2.5–97.5 % band of the admitted set's PF and points.
- **C1b trade-level permutation within calendar month** (secondary; exact admitted count) —
  reported beside C1 so a disagreement between the two nulls is visible as clustering.
- **C2 the dumb HTF filter** — `Cfg.regime` semantics exactly: newest closed UTC daily close
  > SMA20 → bullish regime; long aligned in bullish, short aligned in bearish. Judged with
  the same statistics. The corpus gate is credited only with what C2 cannot explain: its ΔR
  must (a) exceed C2's ΔR and (b) keep its sign and Holm-significance **within each C2
  class** (2 × 2 partition), and (c) the arm `C2-aligned ∧ Bk-aligned` must beat
  `C2-aligned` alone on TEST PF at stress cost.

## 5. Statistics, costs, split

- Per trade: `R = pts / risk` (risk = |entry − sl|). Primary statistic per cell:
  **ΔR = mean R(aligned) − mean R(against)**. Also PF and net points of aligned / against /
  baseline (all trades), admitted fraction, n per class — each on ALL / TRAIN / TEST.
- Costs: mid-M1 (harness) **0.75 base / 1.00 stress**; quote-S5 **0.45 / 0.70**. Quote-S5
  raw points come from a vectorised re-implementation of `lab.s5exit.resolve` semantics
  (walk from `entry_time + 60 s`; long stop/target on the bid, short on the ask; stop before
  target within a bar; horizon = the module's own max hold; TIME at the last bar's mid
  close), **validated to exact agreement with `lab.s5exit.resolve` on every c03 and s14 trade**
  before use. Horizons (min): s93 120, s95 180, s100 72, s97 30, s98 240, s94 1200, s99 480;
  no-time-exit modules use their longest harness hold + 10 %: c03 7200, s14 1000, s03 3700,
  s04 1100, s10 5700, s11 1500, s12 300, s96 23500.
- Split: TRAIN < 2025-12-01 ≤ TEST. **Nomination is made on TRAIN**: for each survivor, the
  gate (if any) with the largest TRAIN ΔR at stress cost among those with C1 p < 0.05 on
  TRAIN and C2-conditional sign agreement is nominated; TEST is then read for bars 6–7.
  All cells' TEST numbers are still tabulated (the mandate asks for them) — the nomination
  rule is what keeps the read honest, and it is written here before any number exists.
- Multiple comparisons: the confirmatory family is **6 gates × 4 survivors = 24 cells** at
  quote-S5 0.70 on TEST; Holm-corrected within the family. The 15-module mid-M1 sweep
  (6 × 15 = 90 cells × 2 costs) is exploratory and reported with raw p-values flagged at the
  Holm level for its own family. The pooled-book test (§6) is 6 cells, Holm within.

## 6. Decision rules — written before any number

**Q1 — "against the HTF fails":** for a definition Bk the corpus claim is *supported* on the
book if, on the pooled trades of all 15 modules (R units, day-block null with day = block
across modules), (i) ΔR > 0 with Holm p < 0.05, (ii) PF(against) < 1.0 at stress cost on
TEST while PF(aligned) ≥ PF(baseline), and (iii) ΔR(Bk) > ΔR(C2) and (i) holds within both
C2 classes. *Refuted* if ΔR ≤ 0 or the C1 band contains the aligned PF. *Explained by the
dumb filter* if (i)–(ii) hold but (iii) fails. "By how much" = ΔR with its C1 95 % band, and
PF(against) vs PF(aligned) at both costs, reported for every definition whether it wins or
loses. The same table is produced for survivors-only and for non-survivors-only (the
corpus's mechanism predicts the *failing* modules' against-trades are the worst).

**Q2 — usable overlay for a survivor:** the nominated gate passes only if the admitted arm
clears bars 1–5 (`lab/PROTOCOL_xau2y_2026-09-18.md`) at quote-S5 0.45 and 0.70, bar 6 on
its robustness axis (neighbours move the same way; a lone spike fails), and **bar 7: beats
the incumbent Stage-1 arm on TEST PF AND TEST points at the stress cost.** A filter can only
beat on points if the trades it removes are net negative — that is exactly the corpus's
claim, so bar 7 is the claim's own test. An arm with higher PF and fewer points is reported
as "quality-improving, profit-reducing — not adopted" (the S94 long-only lesson).

**Sanity floors** (harness fault, not finding): any class with WR < 10 % or > 90 %, |mean R|
> 1.0, or PF > 3 on n ≥ 100 is diagnosed before being reported; the join-resolution rate
("gate never evaluated" ≠ "gate passed") is reported per gate — `none` from *missing data*
is tabulated separately from `none` from *no bias*.

## 7. What this study cannot say

Nothing about entries the strategies did not generate (the gate cannot add trades); nothing
about the method's own entries (C2–C4, CISD, POI — the conjunction test already covered
those on its own events); nothing about a gold bear market (none in the window); nothing
about the confirmed bias (daily C2/C3 **plus** hourly CISD, spec §2.4) — pre-declared out of
scope because it resolves at or near the day's close and the conjunction test found the
stacked day-layer gates leave ~3 tradeable days a year.

## 8. Outputs

`htf_bias.py` (gate builders + assertions), `s5quote.py` (vectorised quote resolver +
validation), `run_overlay.py` (labels, statistics, nulls, tables → `results/*.csv`),
`harness_gate.py` + `rerun_check.py` (appendix re-run for the survivors), `REPORT.md`.

---
## Deviations (append-only, timestamped)

- **2026-09-18 17:45 UTC — D1 (added after the first pooled read, before any per-module
  interpretation was written).** The pooled-book ΔR of §6 Q1 as pre-registered pools raw R across
  modules, so it confounds *within-module* alignment with *between-module composition*: modules
  that are profitable and ~90 % aligned by construction (c03, s95, s96 under B3) lift the pooled
  aligned mean while the against pool is dominated by the losing modules — a Simpson effect the
  protocol did not anticipate. Added, reported beside (not instead of) the original: (a) a
  **module-demeaned** pooled ΔR (each trade's R minus its module's mean R over the same split
  and cost model) with the same day-block null; (b) a **module-level sign test** (how many of 14
  modules — s98 excluded, n=39 — have ΔR > 0). Q1's criterion (i) is read on (a); the original
  raw pooled numbers stay in the tables.
