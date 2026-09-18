# PROTOCOL — Liquidity sweeps at S5 resolution (pre-registered 2026-09-18)

Written before any number was computed. Anything changed after results exist is recorded
in the "Changes after pre-registration" section at the bottom, with the reason.

## Question
At 5-second resolution, what is the anatomy of a liquidity sweep of a well-known level
(how deep, how long beyond, how fast the reclaim), and does (a) the anatomy separate
sweeps that reverse from sweeps that continue, and (b) a sweep-and-reclaim entry carry
any edge that a random-time control and a level-touch-without-reclaim control cannot
explain?

## Data
- S5 cache `backtest/results/bars_cache/s5/XAU_USD/*.parquet` via
  `lab.tools.qa_s5_cache.load_s5` (loaded once; numpy arrays thereafter). Bar timestamp =
  bar OPEN time (OANDA convention); a bar's close is known at `time + 5 s`.
- Window: events with cross time in **2024-09-01 00:00 → 2026-09-17 23:59:59 UTC**
  (levels warm up on the days before). **TRAIN: cross time < 2025-12-01; TEST: ≥ 2025-12-01.**
- Untradeable: any trade whose hold window overlaps **2025-12-25 23:00–23:15 UTC** is
  dropped (count reported). Events inside that window are also dropped from Question A.
- Gold monthly regime series: `lab.tools.campaign_score.gold_monthly` on
  `backtest/results/bars_cache_2y` (read-only reuse).

## Levels (computed from completed data only, forward-filled per bar → no look-ahead)
| type | definition | valid while |
|---|---|---|
| `PD` | high / low (S5 mid h/l) of the most recent completed UTC calendar date with ≥ 5,760 bars (≥ 8 h of ticks; excludes the Sunday 22:00–24:00 stub) | the whole current UTC date |
| `ASIA` | high / low of 00:00–06:59:55 UTC of the current UTC date (≥ 1,000 bars required) | 07:00–23:59:55 the same date |
| `H1` | high / low of the most recent completed clock hour with ≥ 360 bars | the whole current clock hour |
| `LON` | high / low of 07:00–11:59:55 UTC of the current date (≥ 1,000 bars required) | 12:00–20:59:55 the same date ("London range for NY") |

Each type yields two levels (high, low) → two sides. High-level sweeps produce SHORT
trades; low-level sweeps produce LONG trades.

## Sweep event definition (one state machine per level type × side)
For a HIGH level `L` (mirror for lows):
1. **Cross bar** `i`: `h[i] ≥ L + 0.10`, the previous bar closed inside (`c[i-1] < L`) under
   the same level value (`L[i-1] == L[i]`), and the machine is ARMED.
2. **Reclaim bar** `j ≥ i`: first bar with `c[j] < L` (the cross bar itself may be the
   reclaim bar). `time_beyond = time[j] − time[i]` seconds (0 = reclaimed inside the cross
   bar). If `time_beyond ≤ T` the excursion is a **SWEEP(T)**; otherwise it is a **BREAK(T)**.
   Grid **T ∈ {30, 120, 300} s**. Because `time_beyond` is stored, the three T grids are
   nested subsets of one pass run with the search horizon at 4 h.
3. **Depth** `D = max(h[i..j]) − L` (points) and `D / spread[i]` (spread = `ask_c − bid_c`
   at the cross bar). **Extreme** `E = L + D`. **Reclaim speed** = `time_beyond` bucketed
   {0 s, 5–30 s, 35–120 s, 125–300 s}; also `D / max(time_beyond, 5)` pts per second.
4. **Re-arm**: after the reclaim bar, the machine re-arms at the first bar with
   `c ≤ L − 0.50` (a half-point back inside) so one excursion cannot fire twice on noise.
   A BREAK that never closes back inside before the level expires produces no further
   events on that level.
5. Events whose cross time is < 2024-09-01 or ≥ 2026-09-18 are discarded.

## Question A — descriptive (no trading)
Per level type and pooled, on SWEEP(300) events (T=300 is the widest set; the subsets are
reported by reclaim-speed bucket):
- **Reversal label** (after the reclaim bar, mid prices, 4 h horizon): `REV1` if
  `l ≤ L − 1·D` occurs before `h ≥ E` (revisit of the sweep extreme); `REV2` likewise with
  `L − 2·D`. `CONT` if `h ≥ E` occurs first. `UNRESOLVED` if neither within 4 h (excluded
  from the two-group comparison, counted).
- **Stated confound, in advance**: the depth-scaled target `k·D` is easier for small D, so
  a depth difference between REV and CONT groups is partly geometric (Trap 1). Therefore a
  **fixed-distance** label is also reported: `REVF` if `l ≤ L − 1.0` before `h ≥ E`; and
  the duration comparison is additionally shown within depth terciles.
- Test: two-sided **permutation test (10,000 shuffles, seed 0) on the difference of
  medians** of `log(D)` and `time_beyond` between REV and CONT, with n per group. Reported
  for REV1, REV2 and REVF; full window (this question makes no selection decision).
- Also reported: sweep vs break share per level type and T; depth / duration quantiles.

## Question B — tradeable rule (pre-declared)
- **Signal**: reclaim bar `j` of a SWEEP(T) event.
- **Entry**: the NEXT S5 bar `j+1`, at its `ask_c` (LONG after a low sweep) / `bid_c`
  (SHORT after a high sweep). No same-bar fills.
- **Stop**: `E + 0.20` for SHORT (tested `ask_c ≥ stop`); `E − 0.20` for LONG (`bid_c ≤ stop`).
  `R = |entry − stop|`. No minimum-stop floor (the rule is tested exactly as stated).
- **Target (primary, pre-declared): 2R** from entry. SHORT target hit when `ask_c ≤ tp`;
  LONG when `bid_c ≥ tp`. Secondary (exploratory, reported separately): target = the
  opposite side of the swept range (PD low for a PD-high sweep, etc.).
- **Exit walk**: quote-level, from bar `j+2` onward (the fill bar's close is the fill),
  stop checked before target within a bar, **max hold 4 h**; time exit at the quote
  (`bid_c` LONG / `ask_c` SHORT) of the last bar ≤ entry + 4 h.
- **Costs**: `pts = raw − cost`, cost ∈ {0.45, 0.80}, on top of quote-level fills.
- **Concurrency**: one open trade per arm; an event that fires while the arm's trade is
  open is skipped (counted).
- **Arms**: level type (4) × T (3) = **12 arms**, each at both costs (24 result files;
  the cost pair is one comparison). Secondary target adds 12 exploratory arms.
- **Selection on TRAIN only**: the pre-declared TEST candidate is the single arm with the
  highest TRAIN PF at 0.80 cost among arms with TRAIN n ≥ 80. The full 12-arm TEST table is
  printed for the descriptive Q4 answer; only the pre-declared arm carries the verdict.
- **Verdict**: the five xau2y bars via `lab.tools.campaign_score.score_campaign` (n ≥ 40
  TEST; TEST PF > 1 at 0.45 and 0.80; TRAIN PF > 0.9; ≥ 55 % TEST months positive and no
  month > 50 % of TEST net; regime independence). Output is quoted, never typed.

## Controls (both resolved with the identical exit walk, costs, and hold)
- **C-RAND** (matched random-time): for each real trade, a signal bar drawn uniformly from
  bars with the same UTC hour-of-day within ±30 calendar days of the real trade (Trap 4:
  regime-matched), same side, same `R` in points, target 2R, entry at the next bar's
  quote. Same count as the arm. **5 replicates** (seeds 0–4); the mean PF / pts across
  replicates and their range are reported; concurrency is not enforced on the control.
- **C-TOUCH** (level touch without reclaim): on the cross bar `i` itself (no wait for the
  reclaim), enter at bar `i+1` at the quote in the fade direction; stop = `h[i] + 0.20`
  (SHORT) / `l[i] − 0.20` (LONG) — the extreme known at that moment; target 2R; same walk.
  Same events universe (every cross, whether it later reclaims or not), same concurrency.
- Edge beyond control = real mean pts/trade − control mean pts/trade, with a bootstrap
  95 % CI (2,000 resamples, seed 0) on the difference.

## Comparisons stated up front
12 primary arms × 2 costs (judged jointly) + 12 secondary arms + 3 permutation tests × 3
labels × 5 groupings (4 types + pooled) ≈ 45 descriptive tests. At 5 % per bar, 1–2
spurious single-bar passes among the 12 arms and ~2 spurious p < 0.05 among the
descriptive tests are expected. Passing requires all five bars jointly on the one
pre-declared arm.

## Harness checks before any result is read
- Assert `entry index == reclaim index + 1` and exit walk starts at `entry index + 1`.
- Cross-check my vectorised resolver against `lab.s5exit.resolve(mode="quote_s5")` on
  ≥ 200 trades (must agree on outcome and exit price for all; the only permitted
  disagreement is the TIME exit price, mid vs quote, which is documented above).
- Sanity: WR near 0 % / 100 % or implausible expectancy → treat as harness fault.
- Report the join/coverage rate of each level (share of bars with a level defined).

## Compute
Single process; level arrays via pandas groupby; candidate crossings via boolean masks;
Python loop only over candidate events; resolver is a numpy slice per trade. Budget < 60 min.

## Changes after pre-registration
1. **Harness clarification (before any result was read, while coding `run_arm`)**: a fill
   that already sits beyond its own stop (`R ≤ 0`, e.g. the entry bar bounced back through
   the extreme) or a secondary-target level on the wrong side of the entry is an impossible
   trade and is skipped, counted as `bad_geom` (43–508 per arm, reported in `qb_skips.csv`).
2. **Post-hoc descriptive addition (after Q-B results)**: `run_drift.py` — signed mid drift
   after the reclaim bar (fade direction positive) at 60/300/900/3600 s vs matched random
   bars, no stops, no costs. Added because every Q-B arm tracked its random control and the
   Q4 question ("does the reclaim carry *any* directional information") needed a test free
   of stop geometry. Exploratory; it makes no tradeable claim and is labelled as such.
