# PROTOCOL — r2_new_edge: five leads from the S5 programme, pre-registered (2026-09-18 11:53 UTC)

Folder `lab/research_s5/r2_new_edge/`. Written before any hypothesis number was computed.
Deviations are appended in §9 with timestamps; nothing above §9 is rewritten.

One diagnostic was run before this protocol and is recorded here because it shaped H2:
the 775 Stage-1 `s96_h1_momentum` entries (`lab/results/xau2y_stage1/`) were compared with
the M1 bar at their entry time. 538 fire at minute :00 (nominal entry vs signal-bar open:
median +0.005 pt, mean −0.008); 237 fire off the hour (mean +3.69 pt, median +2.75 in the
harness's favour). Mechanism: after an in-hour exit the module re-fires on the same closed H1
bar and the harness books the stale H1 close. This is a fidelity defect, not an edge.

## 0. Common rules (all hypotheses)

- Window 2024-09-01 → 2026-09-17 (warm-up before). **TRAIN < 2025-12-01 ≤ TEST.** Every
  selection is made on TRAIN; TEST is read once, for the pre-declared choice.
- Data: S5 cache (`lab.tools.qa_s5_cache.load_s5`; skip any trade whose life crosses
  2025-12-25 23:00–23:15 UTC), xau2y M1/M15/H1/H4/D1 (`backtest/results/bars_cache_2y`).
- **Execution (quote-S5)**: fill at the *next* S5 bar after the signal bar, long at `ask_c`,
  short at `bid_c`; exits on the quote (long on bid, short on ask), stop before target within a
  bar; time exits on the quote close. Costs **0.45 base / 0.70 stress** on top (the post-
  execution-study convention for quote-S5); 0.80 is printed alongside for comparability with
  the six S5 studies but is not a judged cost.
- **Harness runs (H2, H5)** use `lab/harness.py` on the xau2y cache at **0.75 / 1.00** (mid-M1
  convention) and are then re-resolved on quote-S5 with `lab.s5exit.resolve` (mode
  `quote_s5`, `start_offset_s=60`) at 0.45 / 0.70. The quote-S5 numbers are the judged ones.
- **Bars** (`lab/PROTOCOL_xau2y_2026-09-18.md`): TEST n ≥ 40; TEST PF > 1 at both judged
  costs; TRAIN PF > 0.9 (base cost); ≥ 55 % TEST months positive and no month > 50 % of TEST
  net; regime (positive in gold-up and gold-down months, or |corr| < 0.4). Computed with the
  `bars()` function copied from `po3_common.py` (the campaign_score logic) with costs
  (0.45, 0.70). For H5 (changes to existing strategies) add bar 6 (plateau across the
  pre-declared neighbours) and bar 7 (beat the incumbent on TEST PF **and** points at stress
  cost, TRAIN-justified).
- **Controls**: named per hypothesis. A lead is credited only with what its control cannot
  explain. Geometry-matched random control (`C-RAND`) = for each real trade, 200 draws of an
  entry at the same side, same UTC clock-minute ±0, on a random trading day within ±30 days,
  with the same stop and target *distances* from the fill and the same time-exit clock time;
  fills and exits on the quote exactly as the real trade. Report the real book's percentile
  within the 200 null books on net points and PF, TRAIN and TEST separately.
- **Power**: for each arm report the TRAIN sd of pts/trade and the TEST MDE = 2·sd/√n_TEST.
- Comparisons are counted per hypothesis; at 5 % per single bar, the expected chance passes
  are stated in the report.
- A lead that passes the bars is checked for daily-R correlation against the current book
  (c03, s14, s93 from `lab/results/xau2y_stage1/*_c0.80.trades.parquet`); s95 is also
  reported for H1/H3 because it is the closest existing design (session ORB).

## 1. H1 — trade WITH the first London breach of the Asian range (continuation)

**Question.** The po3 study measured that the day closes on the breach side 61–68 % of the
time and that the fade sits at the 2–6th percentile of random. Does a *continuation* entry on
the first London breach of the Asian range, with the Asian-range midpoint as the stop and a
1× range projection as the target, carry an edge beyond geometry-matched random entries and
beyond the same rule outside London?

**Rule.**
- Trading days: UTC weekdays with S5 coverage (`po3_common.quality_ok`).
- Asian range `AR_lo/AR_hi` = S5 mid low/high over 00:00–06:59:55 UTC; `mid = (AR_lo+AR_hi)/2`;
  `rng = AR_hi − AR_lo`. Days with `rng < 2 × median spread of the day's Asian bars` are skipped
  (degenerate range).
- Breach: first S5 bar `b` in 07:00–09:59:55 UTC with mid `h > AR_hi` (UP → BUY) or `l < AR_lo`
  (DOWN → SELL). Only the first side per day is considered; the day is done afterwards.
- Hold gate `T ∈ {0, 30, 60}` s: confirmation bar `k = b + T/5`; the trade is taken only if every
  S5 close in `[b, k]` is beyond the level (UP: `c ≥ AR_hi`). If any close is back inside, no
  trade that day (the reclaimed poke is the Judas the po3 study already faded).
- Fill: bar `k+1`, long at ask / short at bid. Stop = `mid` (long: bid ≤ mid). Target =
  `AR_hi + M·rng` (long) / `AR_lo − M·rng` (short), `M ∈ {1.0, 0.5}`. Time exit 20:55 UTC on the
  quote. Skip if the fill is already beyond the target.
- **Grid**: 3 (T) × 2 (M) = 6 arms. **Selection**: T with the highest TRAIN PF at 0.70 for
  M = 1.0 (TRAIN n ≥ 80 required); M = 0.5 is the pre-declared secondary at the selected T.
- **Depth arm (secondary, pre-declared)**: T = 0, M = 1.0, plus breach depth at bar b
  (`h_b − AR_hi`) ≥ 2 × spread at bar b — the "deep sweep" continuation from the sweeps study.
- Judged arms: selected T×M=1.0 (primary), T×M=0.5, depth arm = 3 TEST reads (× 2 costs, joint).
- **Controls**: `C-PRE` = the same rule with London removed: range 00:00–03:59:55 UTC, first
  breach in 04:00–06:59:55 UTC, same T/M, time exit 20:55 (the po3 C1 shape). `C-RAND` as §0.
- Pre-declared splits of the primary arm: BUY/SELL; D1 bias with/against (prior close vs
  SMA20, `po3_common.d1_bias`); year.
- Prediction: TEST PF > 1 at 0.70 and above the C-RAND 95th percentile → the continuation is
  tradeable; otherwise the 61–68 % is a base rate the range-mid stop cannot monetise.

## 2. H2 — s96 H1 momentum with the nominal-entry defect removed; stop-entry variant

**Question.** With one signal per closed H1 bar and the entry booked at the market (the M1
close at signal time), is `s96_h1_momentum` an honest H1 momentum edge on 24 months under
quote-S5 execution (the execution study's model said TEST PF 1.34 → 1.19)? And does a
level-touch (stop-order-like) entry during the forming hour make the fill real without
destroying the edge?

**Module** `r2_s96_fixed.py` (in this folder), applied through `Cfg.patch={"get_signal": …}`
on `s96_h1_momentum` so the harness drives the real module with the replaced function:
- Arm **A (corrected)**: identical H1 resample, Donchian(24), EMA20/50 bias, ATR(14) stop
  3.0×ATR, TP 0.4R, computed from the closed H1 bar exactly as the module does; `entry_price`
  = the newest M1 close in `w1m`; SL/TP levels unchanged (anchored to the H1 close, as the
  live bot would place them); **at most one signal per closed H1 bar** (state = timestamp of
  the newest closed H1 bar; reset when time moves backwards, i.e. a new replay). Skip if the
  M1 close is already at/beyond the TP or at/beyond the SL.
- Arm **B (stop-entry)**: during the forming hour, the first M1 bar whose close crosses the
  Donchian(24) extreme of the prior 24 *closed* H1 bars, in the direction of the closed-H1
  EMA20/50 bias, fires at that M1 close; stop 3.0×ATR(14, closed H1) and TP 0.4R from the
  entry; one signal per H1 bar. This is the closest module-contract equivalent of a resting
  stop at the level (fill one M1 later than the touch, at the next S5 quote).
- Harness: xau2y cache, `win_15m=320` (Stage-1 value), costs 0.75 / 1.00 (mid-M1), then
  quote-S5 via `s5exit.resolve` (max_hold_min 20160 = 14 days; TIME exits counted) at
  0.45 / 0.70. Judged on quote-S5.
- Judged arms: A and B = 2 TEST reads (× 2 costs, joint). No parameter grid (the module's
  knobs are not touched — this is a fidelity question, not an optimisation).
- **Control**: C-RAND as §0 for arm A (same side, clock minute, stop and target distances,
  no time exit beyond 14 days). Because a 3×ATR stop against a 0.4R target has a random-walk
  win rate near 71 %, the control's PF is the geometry's PF; arm A is credited only with the
  excess over the control's 95th percentile.
- Pre-declared splits: BUY/SELL; year; hour-of-day block (00–06 / 07–15 / 16–23 UTC).
- Also reported (not judged): the Stage-1 s96 book's quote-S5 numbers with the off-hour
  entries counted — the size of the defect on the honest resolution.

## 3. H3 — 10:00 ET release-range straddle, direction-agnostic

**Question.** The killzones study found 09:50–11:10 NY to be the only window that
concentrates range (5.7–6.3× on 60-s/300-s expansions, p ≈ 0.02 both halves) with zero
directional content. Does a direction-agnostic breakout of the pre-10:00 ET range, taken at
the quote with the opposite edge as the stop, monetise that range beyond the same rule at a
non-release hour and beyond geometry-matched random entries?

**Rule.**
- Trading days: weekdays by the NY calendar (DST resolved per timestamp via
  `America/New_York`), with ≥ 50 % S5 coverage in 09:00–12:00 ET.
- Range `R_lo/R_hi` = S5 mid low/high over 09:45:00–09:59:55 ET (primary; 15 min);
  `rng = R_hi − R_lo`. Skip days with `rng < 2 × median spread of those bars`.
- Entry window 10:00:00–11:09:55 ET. Long at the first bar with `ask_c > R_hi` (fill at that
  ask), short at the first bar with `bid_c < R_lo` (fill at that bid); OCO — the first side to
  trigger is taken, the other is cancelled; if both trigger on the same bar, skip the day.
  One trade per day.
- Stop = opposite edge (long: bid ≤ R_lo). Target = `R_hi + M·rng` (long) / `R_lo − M·rng`
  (short), `M ∈ {1, 2}`. Time exit 12:00 ET on the quote. Skip if the fill is already beyond
  the target or beyond the stop (gap-through of the whole range).
- **Grid**: M ∈ {1, 2} (2 arms) — selection on TRAIN PF at 0.70; plus a pre-declared range
  variant 09:30–10:00 ET (30 min) at the selected M. Judged arms = 2 TEST reads (selected M,
  30-min variant) (× 2 costs, joint).
- **Controls**: `C-NOON` = identical rule on the same days at 12:00 ET (range 11:45–12:00,
  entries 12:00–13:10, time exit 14:00 ET). `C-RAND` as §0 (random day ±30 d, same clock
  minute of entry, same side and distances, time exit at the same clock time).
- Pre-declared splits of the primary arm: BUY/SELL; weekday; whether the breakout bar is the
  10:00:00–10:00:55 minute (release burst) or later.
- Note on lineage: `s95_session_breakout` trades a 30-min ORB at 12/13/14 UTC with an EMA240
  bias; this rule is bias-free, ET-anchored to the 10:00 release, 15-min range, stop at the
  opposite edge (s95: 2×OR). Daily-R correlation with s95 is reported.

## 4. H4 — H4 CRT with a multi-candle hold

**Question.** 40–45 % of the intraday CRT trades expired at the C3 close with the target
unreached. Does holding through C3–C5 (three candles) turn the H4 arms positive, and does it
beat a random C3' with the same extended hold?

**Rule.** `crt_lib` (imported, not edited) with the identical event detection, fill, stop
(C2 extreme ± 0.1·ATR) and target (C1 opposite extreme) for the four H4 arms (H4-UTC and
H4-NY, no-filter and D1 EMA20/50 bias); the only change is the exit horizon: the end of the
third candle after C2 (`end_utc` of candle i2+3) instead of the end of C3. Quote-S5 fills and
exits as before. Costs 0.45 / 0.70.
- **Pre-registered gate** (from the CRT report's own recommendation): the gross mid-M1
  expectancy on TRAIN with the 3-candle hold must be ≥ +2.0 pts/trade for an arm to be
  interpreted as a candidate; below that, the arm is reported as failing the gate and its
  quote-level numbers are descriptive only. All four arms are still run and scored (cheap).
- Judged arms: 4 (× 2 costs, joint).
- **Control**: `crt_lib.random_control` with the same extended horizon (K = 20 draws per
  trade, same slot, ±30 d, ATR-scaled transplanted geometry).

## 5. H5 — the TTrades higher-timeframe gate as an overlay on c03 / s14 (if time permits)

**Question.** The corpus's governing rule — no entry against the higher-timeframe candle;
"do not fade the current daily candle" — has not been tested as an overlay on the roster.
Does gating c03 / s14 signals by the daily candle improve them under the Stage-2 bars?

**Rule.** Wrapper `r2_htf_gate.py` applied via `Cfg.patch={"get_signal": …}` on
`c03_fvg_fill` and `s14_ob_mit_bias`; the wrapper calls the real `get_signal` and admits the
signal only if:
- **G1 (current daily candle)**: BUY only when the newest M1 close > the current UTC day's
  open (first M1 open at/after 00:00 UTC in `w1m`/`w15m`); SELL only when below. Neutral
  (equal) → reject.
- **G2 (previous daily candle)**: BUY only when the previous UTC day's close > its open;
  SELL only when below.
- Windows: `win_15m=200` for all H5 arms (48 h of M15, enough for G2), including the
  incumbent re-run — the incumbent at the same window is the bar-7 reference (control).
- Arms: per strategy {incumbent, G1, G2} × costs {0.75, 1.00} on mid-M1 = 12 harness arms.
  Judged: G1 and G2 per strategy = 4 (bars 1–7 at mid-M1; quote-S5 only for an arm that
  passes bar 7 at mid-M1). Neighbours for bar 6: none pre-declared (the gate has no numeric
  parameter); bar 6 is recorded as n/a.
- Prediction (honest prior): both gates remove trades from a profitable population and will
  likely fail bar 7 on points even if PF rises — the July-drawdown diagnosis said counter-HTF
  trades hurt, the xau2y Stage-2 said every subset lost points. This is the test of that.

## 6. Order of execution and budget

H1 → H3 → H2 → H4 → H5 (H5 only if wall-clock allows; ≤ 3 worker processes). Every script
writes its raw output to `results/*.txt` and per-trade books to `results/*.parquet`; the
report quotes only those files.

## 7. Comparisons declared

H1: 6 grid cells (TRAIN-selected), 3 judged TEST reads, 2 controls, 3 split families.
H2: 2 judged TEST reads, 1 control, 3 split families. H3: 2 grid cells, 2 judged TEST reads,
2 controls, 3 split families. H4: 4 judged arms, 1 control. H5: 4 judged arms, 1 reference.
Total judged TEST reads: 15. Expected single-bar chance passes at 5 %: ~0.75.

## 8. What would change a live decision

Nothing here ships. A PASS on H1/H3 is a candidate for a harness-compatible module and a
paper slot; a PASS on H2-A revives a retired non-ICT module for the operator's decision; a
bar-7 PASS on H5 is a Stage-2 change to a deployed strategy and would need the coordinator's
re-run. A FAIL with a working control closes the lead.

## 9. Deviations (append-only, timestamped)

(none yet)
- 2026-09-18 12:05 UTC (H1, after the grid, before the TEST read): only T = 0 had TRAIN
  n ≥ 80 at M = 1.0 (146; T = 30 → 63, T = 60 → 46), so the pre-registered floor made T = 0
  the only eligible cell. T = 30 / 60 are reported as record cells, not judged. No rule changed.
- 2026-09-18 12:20 UTC (H1, post-hoc): after the TEST read, a labelled diagnostic
  (`results/h1_diag_output.txt`) was run on the primary arm — monthly table, top trades, R-unit
  view, year × side, crash-fortnight exclusion. Descriptive only; no arm was added or changed.
- 2026-09-18 12:40 UTC (H2): the C-RAND horizon is the 14-day resolution horizon (no time
  exit in the strategy), as stated; `s5exit` semantics reproduced with a chunked walk and
  validated on a sample (result in `results/h2_output.txt`).
- 2026-09-18 12:27 UTC (H5): the 2-worker process pool died with BrokenProcessPool (a worker
  terminated abruptly; memory was not exhausted); the campaign was restarted with
  `workers=1` (in-process, sequential). Arms and rules unchanged.
- 2026-09-18 13:15 UTC (H5): the sequential run was split into two single-worker processes
  (c03 arms, s14 arms) to fit the wall-clock under machine load; `c03_incumbent` had finished
  and is reused (resume). Arms and rules unchanged.
