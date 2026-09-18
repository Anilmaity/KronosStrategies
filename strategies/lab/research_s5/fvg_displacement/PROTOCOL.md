# PROTOCOL — Does S5 displacement quality predict whether an M5 FVG holds? (pre-registered 2026-09-18)

Written before any number was computed. Anything changed after results exist is recorded in
the "Deviations" section at the end, with the reason.

## Question
An M5 fair value gap tells you the gap size. The 5-second bars inside the displacement bar
tell you HOW the gap was made (one burst vs a grind). Does that S5-only information predict
(A) whether the gap is respected on the first return, and (B) whether a CE-limit trade on the
gap is profitable — beyond what gap size and killzone already explain?

## Data
- S5: `backtest/results/bars_cache/s5/XAU_USD/*.parquet` via `lab.tools.qa_s5_cache.load_s5`
  (mid o/h/l/c, bid_c/ask_c, volume). Loaded once; kept as numpy arrays.
- M5: `backtest/results/bars_cache_2y/is_XAU_USD_5m.parquet` (time = bar START, UTC).
  D1 from the same cache for gold monthly direction.
- Window: FVG bar-3 start ≥ 2024-09-01, and bar-3 close + 8 h ≤ end of M5 cache
  (2026-09-17 20:50). TRAIN: bar-3 close < 2025-12-01. TEST: ≥ 2025-12-01.
- Exclusion: any event whose [bar-1 start, bar-3 close + 8 h] overlaps 2025-12-25 23:00–23:15
  UTC is dropped (feed spike, QA_S5).

## Event definition (M5 FVG)
Bars b1 = i−2, b2 = i−1, b3 = i (consecutive M5 bars; the three must be consecutive on the
5-minute grid, i.e. no cache gap between them).
- Bullish: `b1.high < b3.low`; gap = [b1.high, b3.low], `gap_size = b3.low − b1.high`.
- Bearish: `b1.low > b3.high`; gap = [b3.high, b1.low], `gap_size = b1.low − b3.high`.
- Keep `gap_size ≥ 0.5` pt. `dir = +1` bullish, `−1` bearish.
- Signal time `t0 = b3.start + 5 min` (bar-3 close). All features use only bars b1–b3 (complete
  at t0). No dedupe: every qualifying triplet is one event (clustering is reported, see Q.B).
- `atr5` = mean(high − low) of the 20 M5 bars ending at b3 (inclusive), the c03 convention.
  `gap_atr = gap_size / atr5`. Absolute-point features are also expressed in ATR units because
  gold's level and volatility drift across the 24 months (Methodology Traps §8).

## S5 displacement features (computed at t0, from S5 bars with time in [b2.start, b2.start+5min))
A 60-slot 5-second grid per event; missing slots (no tick) carry the forward-filled close,
zero volume, and are flagged absent. The bar-2 "range" is `b2.high − b2.low` (M5).
Events with < 12 present S5 slots in bar 2 are dropped from Q.A (count reported).

| # | feature | definition | declared "quality" direction |
|---|---|---|---|
| 1 | `peak_vel` | max over slots of `dir·(c_k − c_{k−1})` / atr5 (pts per 5 s in the impulse direction, ATR units; c_{−1} = last S5 close before b2) | higher = better |
| 2 | `burst30` | max over slots k of `dir·(c_k − c_{k−6})` / bar-2 range, clipped to [0, 1] (share of bar-2 range covered by the fastest 30 s) | higher = better |
| 3 | `vol_hhi` | Herfindahl index Σ(v_k / Σv)² of S5 volume inside bar 2 (1 = one bar carried all volume; 1/60 = flat) | higher = better |
| 4 | `pullback_frac` | share of PRESENT S5 slots in bar 2 whose close moved against the impulse (`dir·Δc < 0`) | lower = better |
| 5 | `er2` | efficiency ratio of bar-2 S5 closes: `|c_last − c_first| / Σ|Δc|` | higher = better |
| 6 | `peak_vel3` | as `peak_vel` but over the 180 slots of b1–b3 | higher = better |
| 7 | `burst30_3` | as `burst30` over b1–b3, denominator = range of b1–b3 (`max(high) − min(low)`) | higher = better |

Confound / axis variables: `gap_atr` (and raw `gap_size`), `hour` of t0, `killzone` =
hour ∈ [7, 11) ∪ [12, 16) UTC, `dir`, `month`.

## Outcome labels (mid S5 h/l, walked from t0 for 8 h wall-clock)
Bullish (mirror for bearish):
- `touched`: first S5 bar with `low ≤ gap_high` (price returns into the gap; touching the near
  edge counts — a limit at the edge would fill).
- `respected`: after the touch, `high ≥ gap_high + gap_size` occurs BEFORE any bar with
  `low < gap_low` (leaves in the impulse direction by ≥ 1 gap size before trading through the
  far side). A bar that does both is resolved as filled-through (stop-first convention).
- `filled_through`: `low < gap_low` occurs first (touch and fill-through may be the same bar).
- `unresolved`: touched but neither within 8 h; `untouched`: no return within 8 h.

Primary binary label: `respected` vs `filled_through` among touched-and-resolved events.
Base rate = respected / (respected + filled_through), reported for TRAIN and TEST, plus the
touched and unresolved shares.

## Question A — feature ranking (TRAIN only, then one TEST read)
- Statistic: AUC of the feature for the primary label, oriented in the declared direction
  (AUC > 0.5 = the declared direction is right). Equivalent to a rank correlation.
- Confound control: stratified AUC = n-weighted mean of the AUC inside each `gap_atr` quintile
  (quintile edges from TRAIN). Also report the same for `gap_atr` itself (unstratified) and for
  `killzone`.
- Null: 1,000 label permutations WITHIN gap-atr quintile; p = share of permuted stratified AUCs
  ≥ observed. Bonferroni over 7 features: significant if p < 0.05/7.
- Lift: respect rate among events with the feature on the "good" side of its TRAIN median,
  divided by the overall respect rate; and the good-minus-bad-half rate difference (pp).
- Top feature = highest TRAIN stratified AUC. TEST is then read once for that feature and for
  `gap_atr`: AUC, stratified AUC, lift (TRAIN-median threshold). Comparisons: 7 features +
  gap_atr + killzone = 9 TRAIN statistics; 2 TEST reads.

## Question B — tradeable test (c03-like CE limit)
Entry rule for every event (independent, points-primary, no concurrency limit):
- A limit at `ce = (gap_low + gap_high)/2` is placed at t0. It can fill on S5 bars with time ≥
  t0 + 5 s (next bar after the signal, no same-bar fill). Quote series from S5: half-spread
  `hs = (ask_c − bid_c)/2` per bar; ask_low ≈ l + hs, bid_high ≈ h − hs.
  Long fills when `l + hs ≤ ce`; short when `h − hs ≥ ce`; fill price = ce. Unfilled after
  8 h from t0 → no trade. No fill if the far side is broken on the fill bar itself
  (stop-first: that bar is a stop-out and is taken as a loss — pre-declared conservative rule).
- Stop: long `sl = gap_low − 0.2`; short `sl = gap_high + 0.2`. `R = ce − sl` (long).
- Target: `tp = ce + 2R` (long). Exits on the quote — a long exits on the BID: stop if
  `bid_low ≈ l − hs ≤ sl`, target if `bid_high ≈ h − hs ≥ tp`; a short exits on the ASK:
  stop if `h + hs ≥ sl`, target if `l + hs ≤ tp`. Stop checked before target within a bar.
  Max hold 8 h from the fill → exit at the bar's bid_c (long) / ask_c (short).
- Points = directional exit − entry − cost, cost ∈ {0.45, 0.80}.

Arms (pre-declared; `T*` = feature threshold chosen below):
| arm | population |
|---|---|
| A0 | all events, all hours |
| A1 | killzone only |
| A2 | `gap_atr ≥ TRAIN median` |
| A3 | killzone ∧ gap-size |
| A4 | top-feature filter at T* |
| A5 | killzone ∧ feature |
| A6 | killzone ∧ gap-size ∧ feature |

Threshold grid for the top feature (declared now): TRAIN quantiles {0.33, 0.50, 0.67} of the
feature over all events, keeping the "good" side. T* = the grid value with the highest TRAIN PF
at 0.80 cost in arm A4 (tie → the one with more trades). Chosen once; then applied to TEST.

Control for A4–A6: the same population and the same threshold, but the feature values are
randomly permuted across the arm's parent population (A0 / A1 / A3) so a random subset of
the same count is selected; 200 permutations; report the control's median and 95th-percentile
TEST PF and net at both costs and the percentile rank of the real arm.

Judgement per arm on TEST: the xau2y bars — n ≥ 40; PF > 1 at 0.45 AND 0.80; TRAIN PF > 0.9;
≥ 55 % TEST months positive; no month > 50 % of TEST net; regime independence (positive in
gold-up AND gold-down months over the full window, or |corr(monthly pts, gold monthly %)| <
0.4). "S5 adds something" requires: A4 beats A0, A5 beats A1, A6 beats A3 on TEST PF at 0.80,
AND the real arm sits above the 95th percentile of its permutation control.

Secondary (sensitivity, not selection): the final table's arms re-run with at most one open or
resting order per direction at a time.

Comparisons: 3 threshold values (TRAIN), 7 arms × 2 costs = 14 TEST evaluations, + Q.A 9.
About 25 statistics; ~1 spurious single-bar pass is expected by chance; that is why the
verdict requires the joint bars and the permutation control.

## Deliverables
`build_events.py` → `results/events.parquet`; `qa_features.py` → `results/qa_*.csv`,
`results/qa_output.txt`; `qb_trades.py` → `results/qb_*.csv`, `results/qb_output.txt`;
`REPORT.md`. Every number in REPORT.md is copied from the results files.

## Deviations
(none yet)
- 2026-09-18, after the first build run (before any Q.A/Q.B statistic): (1) the spike exclusion
  span is [b1 start, t0 + 16 h], not + 8 h, so a trade's 8 h hold after an 8 h fill window
  cannot cross the feed spike (removed 0 events — the window is a holiday night). (2) The
  primary label resolves on a near-edge touch, which is not the Q.B fill condition (CE);
  a gap can be "respected" on a shallow first return and later stop out a CE trade. A
  SECONDARY label `label_ce` (identical rule, but the return must reach CE) is added and
  reported alongside; the primary label and its ranking stay as declared and the top
  feature for Q.B is still chosen on the primary label.
- Observed on the first build, recorded here because it shapes Q.B: with min gap 0.5 and the
  stop 0.2 beyond the far side, R for the median gap is ~0.9 pt against a ~0.6 pt spread, so
  the quote-level stop sits within the spread of CE for the smallest gaps. This is the
  pre-declared geometry, kept as declared; the report states it.
