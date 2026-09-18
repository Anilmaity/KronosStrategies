# PROTOCOL — Power of Three / Judas swing on XAU_USD at S5 (pre-registered 2026-09-18)

Written before any hypothesis number was computed. The only data looked at beforehand:
S5 bar counts per weekday by session window (for the data-quality thresholds in §2), the
S5 QA note, and the vault notes named in the brief. Anything changed after results exist
is recorded in §9 with the reason.

## 1. Question

ICT's daily profile: Asia **accumulates** a range; London **manipulates** by sweeping one
side of it (the *Judas swing*, a false move against the day's eventual direction); the day
then **distributes** the other way, so the daily low of an up day (high of a down day) is
formed by that London sweep.

Two questions, answered separately:

- **Q1 (descriptive):** on gold, is the daily extreme formed by a London sweep of the Asian
  range more often than a direction-preserving null predicts? How long does the false move
  persist, to the second?
- **Q2 (tradeable):** does fading a London sweep of the Asian range on its reclaim earn
  points beyond costs and beyond two controls, under the xau2y bars?

## 2. Data and sample

- S5 cache `backtest/results/bars_cache/s5/XAU_USD/` via `lab.tools.qa_s5_cache.load_s5`
  (loaded once; numpy arrays; loops only over days and events).
- Window **2024-09-01 → 2026-09-17** (UTC days). **TRAIN < 2025-12-01 ≤ TEST.**
- Trading days = Mon–Fri UTC days with ≥ 2,500 S5 bars in 00:00–07:00 UTC, ≥ 1,080 in
  07:00–10:00 and ≥ 3,900 in 10:00–21:00 (each ≈ 50 % of the weekday median: 4,988 /
  2,152 / 7,851). This removes the four full-holiday days and any partial day. Expected
  n ≈ 530.
- 2025-12-25 23:00–23:15 UTC is untradeable (QA feed spike); no rule here holds a
  position at that hour (all exits by 16:00 UTC), so no exclusion is needed for Q2; the
  UTC-day extreme of 2025-12-25 is not in the sample (holiday).
- Prices: mid `h/l/c` for levels and structure; `bid_c/ask_c` for fills and exits.

## 3. Definitions (fixed)

Session clock: the brief's fixed-UTC London window is the **primary** definition. The
DST/4H-grid question is handled as a declared robustness variant, not a search.

| symbol | definition |
|---|---|
| `p07` | mid open of the first S5 bar at or after 07:00 UTC |
| Asian range `AR_hi / AR_lo` | max mid high / min mid low over bars in **[00:00, 07:00) UTC** |
| London window | **[07:00, 10:00) UTC** (primary, anchor A1/A2); **Europe/London 08:00–11:00 local** (variant A3, = 07:00–10:00 UTC in BST, 08:00–11:00 UTC in GMT; in A3 the Asian range ends at London-local 08:00) |
| Day (anchor **A1**, UTC day) | [00:00, 24:00) UTC; close = mid close of the last S5 bar of the calendar day |
| Day (anchor **A2**, NY day) | [17:00 ET, 17:00 ET next day) with `America/New_York` DST; the UTC-day's Asian range and London window are unchanged; day high/low/close and the previous-day extremes are taken over this window (the day containing that London window is the NY day that started the previous evening) |
| Day direction | **up** if close > `p07`, **down** if close < `p07` (equal → excluded) |
| Previous-day extremes `PDH / PDL` | high/low of the previous trading day under the same anchor |
| First London sweep | the first S5 bar in the London window with `h > AR_hi` (sweep-up) or `l < AR_lo` (sweep-down); whichever comes first. Time = seconds after 07:00 |
| Reclaim | the first S5 bar after the breach whose mid **close** is back inside the range (`c < AR_hi` after a sweep-up; `c > AR_lo` after a sweep-down) |
| Sweep extreme / depth | max mid excursion beyond the level from the breach bar to the reclaim bar inclusive (if no reclaim by 16:00 UTC: to 16:00); depth in points |
| Duration beyond | seconds from the breach bar to the reclaim bar; `NaN` (labelled *no reclaim*) if none by 16:00 UTC |
| Extreme location | the day's low/high time-stamped to the S5 bar and bucketed: Asia (00–07), London (07–10), post-London (10–16), NY late (16–21), post-break (21–24 UTC); for A2 the pre-midnight bucket is "prev-evening" |
| Judas extreme time | minutes after 07:00 of the day's low on an up day / high on a down day, when that extreme lies in the London window |
| "Real" sweep | depth > 1 × the day's median S5 spread (`ask_c − bid_c`) in 07:00–10:00; else "flicker" — an S5-only distinction |

## 4. Q1 — descriptive statistics (per anchor A1, A2; A3 as robustness)

Per trading day, a row with: `AR_hi, AR_lo, AR_range, p07, close, direction, first-sweep
(time, side, depth, duration, reclaimed?), day-high time/price, day-low time/price,
extreme-location buckets, PDH/PDL, whether the first London breach also exceeded PDH/PDL,
which level (AR vs PD) was breached first in London`.

Reported:

1. **P(day low in London window | up day)** and **P(day low in London AND below AR_lo |
   up day)** — the Judas claim proper; mirrored for highs on down days. Also the
   unconditional rates and the extreme-location distribution by direction.
2. Distribution of the Judas-extreme time (minutes after 07:00): 10-minute histogram,
   median, IQR.
3. Sweep persistence: distribution of *duration beyond* (seconds) for reclaimed sweeps;
   share not reclaimed by 10:00 and by 16:00; depth distribution and the real/flicker share.
4. Of first London breaches: share that breach only the AR, share that also exceed the
   previous-day extreme, and which level is breached first.
5. **Null A (requested): label permutation.** Shuffle the day-direction labels across
   days 5,000 times; report the null distribution of item 1 and the observed percentile.
   This tests whether the conditional rate differs from the unconditional one.
6. **Null B (decisive): within-day return shuffle.** For each day, keep 00:00–07:00 as is,
   randomly permute the S5 close-to-close mid returns from 07:00 to the day's close (200
   permutations/day), and recompute item 1. This preserves each day's net direction,
   volatility and Asian range, and destroys only the intraday *ordering*. It is the null
   that removes the geometric confound (an up day's low is early by construction —
   Backtest Methodology Traps §1). The Judas structure is credited only with the excess
   of the observed rate over Null B. Reported with a two-sided p from the permutation
   distribution.

No selection happens in Q1; nothing in Q1 changes a Q2 rule.

## 5. Q2 — the pre-registered trade rule

- **Event:** the first London-window breach of each side of the Asian range (≤ 1 event
  per side per day; ≤ 2 per day). Primary anchor A1 (UTC day, fixed 07:00–10:00 UTC).
- **Reclaim condition:** the reclaim bar (§3) occurs within **T seconds** of the breach
  bar, T ∈ {60, 300, 900}. No reclaim within T → no trade (it is a breakout, not a sweep).
- **Entry:** at the **next S5 bar after the reclaim bar**: long at that bar's `ask_c`
  after a sweep-down, short at its `bid_c` after a sweep-up (no same-bar fill).
- **Stop:** sweep extreme ∓ 0.2 pt (long: extreme − 0.2; short: extreme + 0.2).
- **Target (pre-declared primary): T1 = the opposite side of the Asian range**
  (long → `AR_hi`, short → `AR_lo`). Secondary, reported not selected: **2R**.
- **Exits on the quote:** long stops when `bid_c ≤ sl`, targets when `bid_c ≥ tp`; short
  stops when `ask_c ≥ sl`, targets when `ask_c ≤ tp`; stop checked before target within
  a bar; **time exit at the first bar ≥ 16:00 UTC** on the same quote side.
- **Skip** when the entry quote is not strictly between stop and target (a tiny range).
- **Costs:** 0.45 and 0.80 pts round trip subtracted per trade. Points, no sizing.
- **Selection on TRAIN only:** the T with the highest TRAIN PF at 0.80 cost (T1 target)
  is the declared arm; TEST is read once for it. All other cells are reported as
  secondary.

### Controls
- **C1 — same rule, no London.** Range = [00:00, 04:00) UTC, sweep window =
  [04:00, 07:00) UTC (ends at London open, so no London participation), same T, same
  stop/target construction (target = opposite side of that range), same 16:00 exit.
- **C2 — time-shuffled, geometry-matched.** For each real trade: the same side, same
  time-of-day of entry, same stop distance and same target distance in points, placed on a
  random *other* trading day within ± 30 days (regime-matched, Traps §4), quote exits, same
  16:00 time exit. 200 draws of the whole book; report the null mean PF / pts and the
  percentile of the real book. C2 is the geometry control: what a random entry at that
  hour with that stop/target would earn.

### Pre-declared splits (reported, not selected on)
TRAIN/TEST × cost; by year (2024 tail, 2025, 2026); by side; by real vs flicker sweep;
optional filter **D1 bias** = trade only long when the prior UTC day's close is above the
20-day SMA of daily closes (from `bars_cache_2y/is_XAU_USD_1d.parquet`, closes up to and
including the prior day) and only short when below; robustness rows for anchor A3
(DST-aware London) and for the reclaim defined on the quote instead of the mid.

## 6. Judging (xau2y bars, unchanged)

n ≥ 40 TEST; TEST PF > 1 at 0.45 **and** 0.80; TRAIN PF > 0.9; ≥ 55 % TEST months positive
and no TEST month > 50 % of TEST net; regime independence (positive in gold-up and
gold-down months, or |corr| < 0.4, monthly, full window). Plus, for the concept to be
credited: the declared arm must beat **both** controls (C1 PF at the same cost, and the C2
null's 95th percentile of pts).

## 7. Comparisons and power

Q2 cells: T (3) × target (2) × cost (2) = **12** arms, of which one is the declared arm;
splits add year (3) × side (2) × depth (2) + D1 (1) + A3 (1) + quote-reclaim (1) ≈ 15
subset rows. ≈ **27 cells**; at 5 % per bar, **1–2 spurious single-bar passes are
expected**, which is why only the declared arm is judged and it must pass all bars
jointly plus both controls.

Power: before reading TEST, the minimum detectable effect is computed from TRAIN as
2 × sd(pts)/√n_TEST and reported; a TEST mean inside ± MDE is "not distinguishable
from zero", not evidence either way.

## 8. Traps checked by construction

- Geometry (Traps §1): C2 matches stop/target distance and hour; Null B preserves the
  day's direction.
- Exit resolution (§2): S5 quote exits, stop before target.
- Off-by-one (§3): breach at bar k, reclaim at bar j > k, fill at bar j+1's quote; the
  Asian range uses bars strictly before 07:00; a per-trade assertion checks
  `entry_idx > reclaim_idx > breach_idx`.
- Control specification (§4): C2 draws within ± 30 days.
- Units (§5): all points are mid or quote points on XAU_USD; spread in the same unit;
  denominators stated in every table.
- Power (§6): MDE reported before TEST is interpreted.
- Harness property (§7): join-resolution — the share of days with a computable Asian
  range, a computable previous day, and a D1 SMA value is reported next to every rate.
- Structure change (§8): none inside 2024-09 → 2026-09; thresholds in spread units for
  the depth split, in points for the stop pad (0.2 pt ≈ ⅓ spread, fixed by the brief).

## 9. Deviations log

(empty at pre-registration)
