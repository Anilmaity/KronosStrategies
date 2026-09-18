# Protocol — execution / cost model from 24 months of S5 bid/ask (pre-registered 2026-09-18)

Written BEFORE any number in this folder was computed. This study is descriptive (it fits a
cost model; it does not select a strategy), so the xau2y bars are not the verdict — the
pre-declared validation error in §5 is.

## Question
What does execution actually cost on XAU_USD, as a function of UTC hour and stop distance,
measured from the S5 bid/ask cache — and does the campaign's flat 0.45 "base" / 0.80
"stress" round-trip cost convention deserve to survive?

## Data
- S5: `backtest/results/bars_cache/s5/XAU_USD/*.parquet` via `lab.tools.qa_s5_cache.load_s5`
  (loaded once; numpy arrays thereafter). Study window **2024-09-01 → 2026-09-17** (the
  brief's window; bars before/after are dropped). **2025-12-25 23:00–23:15 UTC excluded** from
  every statistic (QA feed spike).
- Trades: every Stage-1 arm in `lab/results/xau2y_stage1/*.trades.parquet` (15 strategies
  × 2 costs). Costs are additive per trade, so entries/levels are identical across the two
  cost files of a strategy (asserted, not assumed); each strategy is resolved once and
  both costs applied.
- Split: **TRAIN entry_time < 2025-12-01 ≤ TEST**. Model coefficients are fitted on TRAIN
  only; TEST is read once, for the pre-declared validation in §5.

## Definitions (fixed now)
- spread `s` = `ask_c − bid_c` per S5 bar. hour = UTC hour of the bar. weekday: Mon=0.
- Hour median `m_h` = median spread of all window bars in UTC hour h.
- **Widening event**: a maximal run of bars with `s > 3·m_h`, runs merged when the gap
  between consecutive qualifying bars ≤ 60 s. Duration = last − first + 5 s. Magnitude =
  max spread. Classified by the event's START time converted to America/New_York (the US
  release clock; 12:30/14:00/18:00 UTC in the topic statement are the EDT values of
  08:30/10:00/14:00 ET and shift by an hour under EST):
  - `scheduled`: Mon–Fri, start within [−1 min, +5 min] of 08:30, 10:00 or 14:00 ET;
  - `fomc`: a subset of 14:00 ET Wednesdays derived from the data alone — days whose 14:00
    ET event magnitude is in the top decile of all 14:00 ET weekday events AND a second
    event starts 14:25–14:45 ET (the press conference); the list is reported with its
    spacing so the reader can check it looks like an 8-meetings-a-year calendar;
  - `break`: start 20:55–22:10 UTC (daily close/reopen) or Sunday 21:55–23:00 UTC;
  - `unscheduled`: everything else.
- **Jump** `J_t = |c_t − c_{t−1}|` only for consecutive bars exactly 5 s apart (the S5 grid
  has holes where no tick printed; a jump across a hole is not a 5-second jump and is
  reported separately as "gap bars"). Excluded: the 2025-12-25 window.
- **Gap-through probability** `P_h(J > d)` for d ∈ {1, 2, 3, 5, 10} pts by hour.
- **Slippage per stop-out**, two estimators, both reported:
  1. analytic level-crossing: `E_h[J²] / (2·E_h[J])` — expected overshoot when a resting
     level is crossed by a 5-s jump and sits uniformly within it (size-biased crossing);
     plus `E_h[J − d | J > d]` for each d;
  2. observed: on every quote_S5 stop-out of the Stage-1 trades, overshoot = `sl − bid_c`
     (long) / `ask_c − sl` (short) at the triggering bar, by hour and stop bucket.
  Both are upper bounds on tick-level slippage (S5 closes are coarser than ticks) and
  exclude broker latency; that is stated, not fixed.
- **Quote-S5 resolution**: a vectorised replica of `lab/s5exit.resolve()` (mode `quote_s5`,
  `start_offset_s=60`, horizon = the strategy's `_MAX_HOLD_MIN` where it has one, else
  45 days with unresolved trades dropped and counted). It must reproduce the existing
  `lab/results/s5exit/{c03_fvg_fill,s14_ob_mit_bias}_c0.80.parquet` outcomes on ≥ 99.9 % of
  trades before any downstream number is trusted.
- **Spread at entry** `s_e` = spread of the fill bar (first S5 bar at/after entry_time+60 s).
  **Stop distance in spreads** `x = risk / s_e`. Buckets (fixed): [0,1.5), [1.5,2.5),
  [2.5,3.5), [3.5,5), [5,7), [7,10), [10,15), [15,25), [25,∞).
- **Geometry haircut** per trade `Δ = mid_M1_pts − quote_S5_pts` (same cost on both sides, so
  Δ is cost-free). PF haircut per bucket = `PF_quote / PF_mid − 1`.
- **Entry component** per trade: `ask_c − entry_px` (long) / `entry_px − bid_c` (short) at
  the fill bar: the half-spread plus the 5-s latency drift, measured, by hour.

## Pre-declared model (§4 deliverable)
Per-trade live cost relative to the mid-M1 harness at cost 0:
```
cost(h, d) = E(h)  +  G(x)·s_e  +  P(SL)·S(h, d)
  E(h)  entry component by hour (TRAIN mean)
  G(x)  geometry haircut in spread units by x-bucket (TRAIN pooled mean over all strategies)
  S(h,d) slippage per stop-out (estimator 1, by hour; d enters through E[J−d|J>d] is
         reported but the table uses the level-crossing value, which does not depend on d)
```
Null hypothesis for G, stated in advance: under a driftless random walk the quote trigger
shift (stop s/2 closer, target s/2 farther) costs exactly **s/2 per trade regardless of
stop distance** (P(TP) falls by (s/2)/(d+T); times the payoff span d+T). If the measured G
is ≈ 0.5 across buckets, the whole tight-stop "PF haircut" is arithmetic (a fixed points
cost on a small gross), not a geometry effect — and quote_S5 at cost c is the mid model at
cost c + s/2.

**Control** (methodology-traps §1/§4): for every Stage-1 trade a matched random entry —
same side, same hour-of-day and minute, same `risk` and `tp − entry` distances, entry day
drawn uniformly within ±30 days of the real trade — resolved on mid_S5 and quote_S5.
If the control's G(x) equals the strategies' G(x), the haircut is generic geometry and
one curve should fit all 15; if not, the residual is strategy-specific (e.g. entries that
sit at a level the quote crosses first).

## Validation (§5, TEST read once)
For each strategy at each cost: predicted quote_S5 net = mid_M1 net − Σ_trades G(x)·s_e,
compared with the actual quote_S5 net. Error metric `X = |pred − actual| / |actual quote net|`
(and in points). Pre-declared adequacy: pooled-TEST X ≤ 10 % and per-strategy median X ≤ 15 %;
strategies with |quote net| < 100 pts are reported but excluded from the median (a ratio on
a near-zero denominator is noise). PF: predicted PF from the per-trade adjusted pts.

## Decision rule for the flat costs (pre-declared)
Flat-cost equivalent for 07–16 UTC = mean over TRAIN Stage-1 trades entered 07–16 of
`E(h) + G(x)·s_e + P(SL)·S(h)` with the trade's own outcome for P(SL). Recommend retiring
0.45/0.80 if (a) the measured full round-trip cost at the median Stage-1 stop lies outside
[0.45, 0.80], or (b) the hour or stop-distance dependence moves any Stage-1 arm across a
bar (TEST PF > 1 at both costs) when the table replaces the flat cost. Otherwise keep the
pair and state what each one corresponds to.

## Comparisons
No bar-based selection is made. Model choice is one fit (bucket means on TRAIN). The
per-strategy residual table has 15 entries; at a 5 % false-positive rate one strategy's
residual is expected to look "significant" by chance and will not be interpreted alone.

## Outputs
`01_spread.py` → `results/spread_*.csv`, `results/events.csv`, `results/01_spread.txt`;
`02_jumps.py` → `results/jumps_*.csv`, `results/02_jumps.txt`;
`03_geometry.py` → `results/trades_quote_s5.parquet`, `results/geometry_*.csv`,
`results/03_geometry.txt`; `04_cost_model.py` → `results/cost_model.csv`,
`results/validation_test.csv`, `results/04_cost_model.txt`; `REPORT.md`.

## Changes recorded after the first run (2026-09-18, with reasons)
1. **FOMC derivation.** The "top decile of 14:00 ET event magnitude" filter is void: the feed
   caps the quoted spread at 5.00 / 10.00, 133 events tie at exactly 5.00, so the decile is a
   tie. Replaced by: `fomc` = a Wednesday event starting 13:59–14:01 ET; the 14:25–14:45 ET
   presser event is reported as a flag, not used as a filter. The derived day list is printed
   with its spacing (`results/01_spread.txt`).
2. **Entry component split.** `entry_px` in the Stage-1 trade files is the strategy's *nominal*
   entry, which for s95/s96/s97/s98 (and, with zero mean, s93/s94/s99/s100) is not the market
   mid at signal time. The pre-registered `ask_c − entry_px` therefore mixes a generic market
   term with a strategy-specific fill-fidelity term. It is now reported as
   `entry_mkt` (fill vs the market mid at the signal bar's close: half-spread + 5-s drift, by
   hour — this is what enters the cost table) plus `nominal_gap` (market mid vs nominal entry,
   per strategy, positive = the harness booked a better price than the market offered). Their
   sum is the pre-registered quantity, unchanged.
3. Nothing else moved. The validation metric, adequacy thresholds, buckets, split and the
   decision rule are as pre-registered; the model failed the adequacy test and that is reported
   as a FAIL. The alternatives discussed in REPORT.md §6 are labelled post-hoc and not adopted.
