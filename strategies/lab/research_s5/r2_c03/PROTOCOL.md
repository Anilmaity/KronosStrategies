# r2_c03 — can `c03_fvg_fill` be made better? Pre-registration (2026-09-18, written before any arm ran)

Mandate: c03 is live on the demo book since 2026-09-18 05:00 UTC and is the project's best
strategy (24-mo TEST PF 1.57/1.46 mid-M1 @0.45/0.80; 1.25 under live triggers @0.80). Make it
better or show it cannot be, under CONTEXT.md §6.

## Fixed for every hypothesis below

- **Data**: `backtest/results/bars_cache_2y` (M1/M5/M15/D, 2024-08-15 → 2026-09-17) for the
  harness; the QA'd S5 cache (2024-08-15 → 2026-09-18) for quote-level exits.
- **Window / split**: replay 2024-09-01 → 2026-09-17; TRAIN < 2025-12-01 ≤ TEST.
- **Engine**: `lab.harness.replay` driving the real `get_signal()` (shipped module or a variant
  module in this folder that imports the shipped code path) with the real gate rules; harness
  windows win_1m 700 / win_5m 160 / win_15m 100 as in Stage 1. Never a filter of saved trades.
- **Costs**: the harness is run once per arm (cost is a constant subtracted at exit, so the trade
  list is identical at every cost — asserted in the scorer against the Stage-1 pair) and reported
  at **mid-M1 0.75 / 1.00**; every arm's exits are then re-resolved on the S5 quote (long stop/target
  on the bid, short on the ask, stop before target, walk starts 60 s after the signal bar's open,
  the vectorised replica of `lab/s5exit.resolve` from `research_s5/execution/03_geometry.py`, copied
  into this folder unchanged in logic) and reported at **quote-S5 0.45 / 0.70**.
- **Decision metric**: **quote-S5 at 0.70** (the stress cost of the live-trigger model).
- **Bars**: 1–5 of `PROTOCOL_xau2y_2026-09-18.md` on the mid-M1 trades (TEST n ≥ 40; TEST PF > 1 at
  0.75 and 1.00; TRAIN PF > 0.9; ≥ 55 % TEST months positive and no month > 50 % of TEST net;
  regime independence), plus Stage-2 **bar 6** (plateau: the neighbouring grid points move the same
  way — no lone spike) and **bar 7** (beat the incumbent at quote-S5 0.70 on **TEST PF AND TEST
  points**).
- **TRAIN-justified pick**: inside a grid the arm carried to TEST is the one with the best TRAIN
  quote-S5 PF at 0.70 (tie → TRAIN points). Its TEST is read once. Other grid points' TEST numbers
  are reported (they exist) but never used to choose.
- **Incumbent / control**: the shipped `c03_fvg_fill` at floor 1.5, re-run in this campaign (must
  reproduce the Stage-1 trade list exactly), plus the variant module at its shipped defaults
  (must reproduce the same list — the equivalence control, H0). A hypothesis that adds a filter has
  a matched control declared in its own section.
- **Comparisons declared**: 25 harness arms in total (listed per hypothesis). No arm is added
  after seeing results without a timestamped deviation note here.
- **Workers**: ≤ 3 processes.

## H0 — equivalence control (2 arms)
`base` = shipped `c03_fvg_fill`; `v_default` = `c03r2` (this folder's variant module) at shipped
constants. Pass iff the two trade lists are identical (entry_time, side, entry_px, sl, tp, exit)
and identical to `lab/results/xau2y_stage1/c03_fvg_fill_c0.80.trades.parquet` (entries/exits).
If H0 fails, nothing below is run until it is fixed and the fix is recorded here.

## H1 — minimum stop floor (the s14 template), 4 arms
Question: under live triggers c03's < 2 pt stops kept +82 of +177 mid pts and its 2–3 pt stops
+127 of +275 (REPORT_s5exit). A floor rejects the signal the way live does (`sl_too_tight`), freeing
the single slot and the 600 s cooldown for a later signal — so it is *not* a subset filter and the
outcome is not knowable from the saved list.
Rule: `Cfg.min_sl_dist_pts` ∈ {2.0, 2.5, 3.0, 3.5}; incumbent 1.5.
Prediction (recorded): PF rises, points fall; bar 7 likely fails on points unless slot-freeing
compensates. Judged by bars 6–7 with the TRAIN-justified pick.

## H2 — stop buffer beyond the gap, 3 arms
Question: the shipped stop is `zone_low − 0.10·ATR5` — inside the "one or two spreads" band the S5
programme identified as geometry losses when ATR5 is small. Bracket it.
Rule: `_SL_BUF_ATR` ∈ {0.0, 0.25, 0.50} (shipped 0.10). Nothing else changes (TP unchanged, so R:R
falls with a wider stop and the ≥ 1:1 gate removes some signals — that is the rule as it would ship).
Prediction: 0.25 helps under quote-S5 if the < 3-spread stops are the loss; 0.50 likely costs
points through the 1:1 gate.

## H3 — target structure, 4 arms
Question: TP = 20-bar M5 swing high + 0.5 (fixed). Bracket the lookback and the minimum R:R.
Rule: `_TP_LOOKBACK` ∈ {10, 40} (shipped 20); `_MIN_RR` ∈ {1.5, 2.0} (shipped 1.0). One knob per
arm. Prediction: none held — these are plateau checks; a lone spike at any value is not adopted.

## H4 — TTrades higher-timeframe gate as an overlay, 6 arms (2 real + 4 controls)
Question: "no entry without a higher-timeframe reason." The daily-bias engine from the corpus
(`daily-bias-framework`): the previous daily candle decides the draw.
Rules (daily bars = the cache's UTC-midnight D frame; a bar is *closed* once now ≥ time + 1 day —
the harness's own look-ahead-safe convention; the alignment differs from TTrades' 17:00 NY day and
this is a declared limitation):
- `prev_candle`: bias BULL if yesterday's close > open, BEAR if <, else none. Signals admitted only
  when side matches (BUY↔BULL).
- `pdhl_engine`: with y = newest closed day, y1 = the day before: close[y] > high[y1] → BULL;
  close[y] < low[y1] → BEAR; else high[y] > high[y1] (swept PDH, failed to close above) → BEAR;
  else low[y] < low[y1] → BULL; else (inside day) → none (no entry). Checked in that order.
- Controls: the same gate with the daily bias series **randomly permuted across days** (seeds 1, 2)
  for each rule — same marginal admit rate, no information. The concept is credited only with what
  the permuted gate cannot explain: the real arm must beat both of its own controls on TRAIN quote-S5
  PF and points before its TEST is read.
Prediction: Stage-2 already showed side/regime subsets raise PF and lose points; a daily gate is
another subset. It passes only if the admitted half carries more than half the points.

## H5 — trading window, 3 arms
Question: the killzones (07–11, 13–18 UTC) are the module's own; "time buys opportunity, not
accuracy" (Session Timing on Gold) predicts that adding hours adds trades at similar per-trade edge.
Rule: `_KZ` ∈ {((7,20),) "all session", ((7,11),(12,18)) "add 12 UTC", ((8,11),(13,16)) "narrow"}.
(`CONFIG.session_end_hour` 20 caps the first.) Prediction: "all session" adds points at similar PF
→ the only arm that can pass bar 7; "narrow" is the S5-programme prediction's negative control.

## H6 — the H1 bias definition, 3 arms
Question: `_h1_ema_slope` samples every 4th 15m close from the window start, so its "H1" series is
phase-locked to the window length and its newest sample is 3 bars (45 min) stale. Is the shipped
quirk load-bearing?
Rule: `_BIAS_MODE` ∈ {`h1_resampled` (true H1 closes from the last bar backwards: every 4th bar
ending at the newest 15m bar; same EMA20, same 4-bar slope), `ema15_21` (`base.htf_bias`: 15m close
vs EMA21 — s14's filter), `none` (no bias: take the most recent unfilled FVG of either type — the
concept-removed control)}. Prediction: `none` loses (the bias is doing work, as s03→s14 showed);
`h1_resampled` ≈ shipped (a plateau); `ema15_21` unknown.

## H7 — entry-drift live gate and live parity (descriptive, no harness arms)
- For every incumbent signal, the live runner sees the closed M1 bar 5–25 s after its close
  (5 s poll, 20 s candle-cache TTL) and fetches the LTP for the drift gate. Model: LTP = S5 close at
  entry bar open + 60 s + δ, δ ∈ {5, 15, 30} s; reject iff (LTP − entry)·dir > min(0.5, 0.25·stop).
  Report the rejected share by δ and the quote-S5 PF of rejected vs admitted (a *measurement of the
  live gate's selection*, explicitly not a strategy change and not judged by bars).
- Live parity: every `apis_strategysignal` row for 'Concept C03_FVG_FILL' on 2026-09-18 vs the
  variant module driven bar-by-bar over the same M1/M5/M15 bars (today's bars derived from the S5
  cache, earlier bars from the 2y cache) with the runner's windows (60/80/100). Report: signals
  matched by minute and side, level differences, and the two positions' fills vs the harness's.
  Also the May–June 2026 live record (395 signals) summarised by rejection reason.

## Appendix — deviations (append only, with timestamps)
- 2026-09-18 12:05 UTC (before any arm ran): arm count corrected from 23 to 25 (2+4+3+4+6+3+3);
  the per-hypothesis lists were already as above.
- 2026-09-18 12:40 UTC — **H8 added (deviation, recorded before any H8 number)**. While running H7b
  (live parity) the harness was found to hand `get_signal` an M5 window whose last bar is the
  bar *in progress* (`j5 = searchsorted(t5, t1[i], "right")` includes the M5 bar opening at or
  before the current M1 bar; verified: at M1 bar 07:11 the window ends with the 07:10 bar, which
  closes 07:15; same for M15, up to 14 min). c03's trigger reads that bar's close
  (`last5.close > zone_high`) and its high/low (target, ATR). Live (`fetch_candles` returns only
  `complete` candles) sees that bar only from the 5th minute. The leaky campaign (25 arms, 3 had
  finished: H0_base, H0_v_default, H1_minsl2.0 — kept in results/ as the "as-screened" control)
  is paused; every decision is now taken under a closed-bar harness.
  **H8 — closed-bar alignment.** `harness_fixed.py` = a copy of `lab/harness.py` with one change:
  `j5 = searchsorted(t5, t1[i] − 4 min, "right")`, `j15 = searchsorted(t15, t1[i] − 14 min, "right")`
  (an M5/M15 bar enters the window only once its close ≤ the current M1 bar's close, exactly the
  runner's `complete` filter). Everything else identical. Arms: `F_base` (shipped c03 under the
  fixed harness). Bars 1–5 + quote-S5 as above; the leaky `H0_base` is the comparison, not the
  incumbent — the incumbent for bar 7 becomes `F_base`. Prediction (recorded): the parity run
  says live fires ~4 min later at different prices; the edge will shrink; whether it survives is
  the question. If `F_base` still passes bars 1–5 under quote-S5, H1–H6 are re-run under the
  fixed harness (same grids, same rules; arm labels prefixed `F_`); if it fails, H1–H6 are moot and
  the report says so. Also: H7b's parity is re-run with the closed-bar windows to confirm the
  diagnosis (live's 07:11–07:13 and 07:27 signals should reappear).
- 2026-09-18 12:42 UTC — correction to the line above: when the leaky campaign was killed **no**
  arm had finished (results/ held only the log); "3 had finished" was written from the expected
  progress, not the directory. Nothing from the leaky campaign exists. If leaky numbers are wanted
  as a comparison they will be re-run explicitly as `L_base` (one arm) and labelled as such.
- 2026-09-18 13:55 UTC — the leaky campaign's spawned workers survived the driver's kill and
  completed three unprefixed arms (`H0_base`, `H0_v_default`, `H1_minsl2.0`; leaky harness) before
  they were found and killed; `H0_base` equals `L_H0_base` exactly (n 2161, same points). They are
  kept, labelled leaky in the report, and no further leaky arm is run. All later arms run under
  `harness_fixed` with the `F_` prefix and ≤ 3 workers.
- 2026-09-18 13:55 UTC — **H8 result read: F_base fails bars 1–5** (mid-M1 TEST PF 0.956 / 0.910 at
  0.75 / 1.00; quote-S5 @0.70 TRAIN 0.822 / TEST 0.918). Per H8's rule H1–H6 are moot as
  *improvements*. Declared next (before running): (a) **H8b** — `s14_ob_mit_bias` (Stage-1 config,
  floor 1.5, and the shipped floor 3.0) under `harness_fixed`, because it is the other live
  strategy screened by the same harness and shares the `w5m.iloc[-1]` pattern — 2 arms, reported
  as-is; (b) **H9 — closed-bar rescue grid**: the H1/H2/H5/H6 arms are run under `harness_fixed`
  (13 arms) as a *pre-registered rescue*, not an improvement: with 13 comparisons on a base at
  PF 0.93 a lone passing cell is expected by chance; a cell is credited only if it passes bars 1–5
  under quote-S5 at 0.70 on TRAIN and TEST, its neighbours agree (bar 6), and it is TRAIN-chosen.
  H3 (target lookback / min R:R) and H4 (daily gate) are dropped from the rescue: H4's premise was
  an overlay on a working strategy, H3 reshapes the payoff of a strategy with no entry edge.
- 2026-09-18 12:38 UTC (DB clock) — the UTC times written on the deviation lines above ("12:05",
  "12:40", "12:42", "13:55") were estimated from the process listing's local clock and are about
  one hour too late; the true order and sequence (each note before its numbers) are unchanged.
  From here on times are taken from `select now()` on the prod DB.
- 2026-09-18 12:38 UTC — **H8c declared before running**: minute-level live parity for
  `s14_ob_mit_bias` (live name 'Research OB_MIT_BIAS') on 2026-09-18 for the signals inside the
  S5 cache's coverage (≤ 10:30 UTC: the 09:26–09:28 rows), same method as H7b, both alignments.
- 2026-09-18 ~13:45 UTC — all H8b/H8c/H9 numbers read (results/SCORE.csv, SCORE.txt,
  parity_s14_2026-09-18.txt). No H9 cell passes bar 2 (all quote-S5 @0.70 TEST PF < 1; all mid-M1
  TEST PF < 1 at 0.75). Nothing is adopted. Campaign closed: 20 harness arms run in total
  (3 leaky: H0_base, H0_v_default, H1_minsl2.0, plus L_H0_base = 4 files; 17 closed-bar: F_H0 ×2,
  F_H1 ×4, F_H2 ×3, F_H5 ×3, F_H6 ×3, F_S14 ×2). H3 and H4 were not run (declared dropped above).
