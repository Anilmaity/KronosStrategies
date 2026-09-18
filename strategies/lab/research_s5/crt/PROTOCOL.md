# PROTOCOL — Candle Range Theory (CRT / AMD) at intraday timeframes, honest execution

Pre-registered 2026-09-18, written before any number was computed. Folder:
`lab/research_s5/crt/`. Shared brief: `../BRIEF.md`. Nothing below moves after results exist;
any deviation is recorded in REPORT.md under "Deviations from protocol".

## Question
Does the three-candle CRT pattern (C1 sets a range, C2 sweeps one extreme and closes back
inside, C3 delivers to the opposite extreme) carry a tradeable edge on XAU_USD at H1 and H4
when the entry is the *quote at the C3 open* (never the C2 close) and every exit is resolved
on 5-second bid/ask quotes? The prior daily test (`bt_crt_daily.py`: WR 60 / PF 1.63 on 25
trades, 6-month sample; the 8-year run is not on record) could not answer the two things
that decide this intraday: (1) how much of the pattern's apparent delivery is candle
geometry (the C2-wick lesson), and (2) how much of it evaporates between a bar-level view
and quote-level execution.

## Data
- Candle formation: `backtest/results/bars_cache_2y/is_XAU_USD_1m.parquet` (QA'd M1, UTC),
  aggregated to the three candle grids below (o=first, h=max, l=min, c=last per grid slot).
  The UTC H1/H4 built this way are cross-checked against the cached `is_XAU_USD_1h/4h`
  files (h/l agreement is reported; if < 99.5 % agree the run stops and the cause is
  found before anything else is computed).
- Execution: the S5 cache (`backtest/results/bars_cache/s5/XAU_USD`, `bid_c`/`ask_c`),
  loaded once with `lab.tools.qa_s5_cache.load_s5`, kept as numpy arrays.
- Bias filter parents: UTC H4 for H1-UTC; UTC D1 (`is_XAU_USD_1d`) for H4-UTC; a daily
  series on the 17:00-ET grid built from M1 for H4-NY.
- Gold regime series: the D1 cache, calendar-month close-to-close (as `campaign_score`).
- Window 2024-09-01 → 2026-09-17 (warm-up before 2024-09-01 for ATR/EMA). **TRAIN: C3 open
  < 2025-12-01; TEST: C3 open ≥ 2025-12-01.** 2025-12-25 23:00–23:15 UTC is untradeable:
  any trade whose C3 overlaps it is dropped and counted.

## Candle grids (three, all judged)
1. **H1-UTC**: slots start on the UTC hour.
2. **H4-UTC**: slots start 00/04/08/12/16/20 UTC (the cache's `dailyAlignment=0` grid).
3. **H4-NY**: the ICT 4-hour grid anchored to the 17:00 ET daily open: slots start at
   17:00, 21:00, 01:00, 05:00, 09:00, 13:00 New York wall-clock time (DST-aware). The
   feed is closed 17:00–18:04 ET, so the 17:00 slot is a 3-hour candle that opens at 18:04
   ET; as C3 it is unfillable "at the open" and is skipped by the fill rule below.

A candle is *complete enough* to serve as C1 or C2 if it holds ≥ 50 % of its nominal
minutes (H1 ≥ 30, H4 ≥ 120). C1, C2, C3 must be three consecutive grid slots.

## Rule (fixed)
- ATR: Wilder ATR(14) on the grid's own candle series, value at C1 (as in the daily test).
- **Sweep-up → SHORT**: `C2.high > C1.high + 0.05·ATR` and `C2.close < C1.high`.
- **Sweep-down → LONG**: `C2.low < C1.low − 0.05·ATR` and `C2.close > C1.low`.
- If both extremes are swept in the same C2, the event is ambiguous and skipped (counted).
- **Entry**: the first S5 bar with `time ≥ C3 scheduled open`, filled at that bar's
  `ask_c` (long) / `bid_c` (short). The signal is complete at the C2 close (the S5 bar
  before it), so this is the next-bar fill the brief requires. If the first S5 bar of C3
  arrives more than 15 minutes after the scheduled open (daily break, weekend, holiday) the
  event is skipped as "no fill at open" (counted).
- **Stop**: short `C2.high + 0.1·ATR`; long `C2.low − 0.1·ATR`.
- **Target**: short `C1.low`; long `C1.high`.
- The fill must lie strictly between stop and target, otherwise skipped (counted).
- **Exits (quote_s5)**: walk S5 bars strictly after the entry bar and before the C3 close.
  Long: stop when `bid_c ≤ stop` (filled at the stop), target when `bid_c ≥ target`
  (filled at the target); short mirrors on `ask_c`. Stop checked before target within a
  bar. If neither fires, exit at the C3 close on the last S5 bar of C3: long at `bid_c`,
  short at `ask_c`. Max hold = one C3 candle. No re-entry, one trade per event.
- **Costs**: 0.45 and 0.80 pts subtracted from raw points on top of the quote-level fills.
- Points-primary. R = net pts / |entry − stop| is reported for comparability with the
  daily test; PF is in points.

## Pre-declared variant (the only one): HTF bias filter
EMA20 vs EMA50 of the parent-timeframe closes, evaluated on the last parent candle that
CLOSED at or before the C3 open. Long only if EMA20 > EMA50, short only if EMA20 < EMA50.
This is the `bias_filter` of the daily test, ported to the parent of the entry TF.

## Arms and comparisons
3 grids × {no filter, bias filter} × 2 costs = 12 arms; 6 base-cost arms carry a verdict
(the 0.80 twin feeds bar 2). There are no free parameters and no TRAIN-based selection —
every arm is pre-declared and read once on TEST. With 6 judged arms × 5 bars at a 5 %
false-positive rate per bar, ~1.5 single-bar passes by chance are expected across the
table; a full 5-bar pass by chance has probability ≈ 0.05^5 per arm if bars were
independent (they are not; treat any lone pass with suspicion).

Power (stated before running): if TEST has n ≈ 100 trades on a grid, the 95 % minimum
detectable win-rate difference against a control is ≈ ±10 pp; at n ≈ 40 it is ≈ ±15 pp.
Anything smaller is unresolvable here and will be reported as such.

## Controls
**(a) Random-C3 control (the concept removed).** For every real trade, K = 20 random
candles are drawn from the same grid, the same slot (same UTC hour for the UTC grids, same
NY wall-clock slot for H4-NY), within ±30 days of the real C3 (regime-matched, per the
traps note), that are fillable at the open under the same 15-minute rule, and whose own
preceding candle is NOT a sweep in either direction. The real trade's geometry is
transplanted in ATR units: stop distance and target distance are the real trade's,
rescaled by ATR'/ATR of the random candle, placed around the random candle's open quote in
the real trade's direction. Same exits, same costs, same C3-close time exit. Reported: the
distribution of control PF / net pts over the 20 draws, and the share of draws whose PF is
≥ the real PF (a permutation-style p-value), for TRAIN and TEST at both costs. The concept
is credited only with what the control cannot explain.

**(b) The C2-wick geometric control (the vault note).** The vault note does not give code,
so its metric is implemented as stated there: entry = C2 close (mid, at the C2 close
timestamp), target = "delivery beyond the C2 open" measured as **C3 closes beyond the C2
open in the reversal direction**, wick = the sweeping wick (C2 extreme minus the nearer of
C2 open/close, as a fraction of the C2 range), bucketed into quintiles within each grid.
Per quintile we report (i) that delivery rate, (ii) the cushion at entry — signed distance
from the C2 close to the C2 open in the trade direction, in R — and (iii) the share of
events whose entry already sits beyond the target (cushion > 0). Then the same events are
re-scored under the pre-registered rule (C3-open quote entry, C1-opposite-extreme target,
quote_s5 exits) per quintile. The artefact is "removed" if the Q1−Q5 delivery gap that
tracks the cushion under (b-i) is absent, or no longer tracks distance, under the honest
rule; the difference in the Q1−Q5 gap is the quantity reported. A third column moves only
the entry (C3-open quote) while keeping the C2-open target, so the report can say whether
the entry or the target definition carries the artefact.

## Resolution flip (the number the daily test could not produce)
The same events are resolved three ways at the same costs:
- `mid_m1`: entry at the M1 open of C3 (mid), exits on M1 mid high/low from that bar
  inclusive (stop before target), time exit at the last M1 close of C3. This is the
  bar-level view a bar backtester gives.
- `mid_s5`: entry at the mid close of the first S5 bar, exits on S5 mid h/l after it.
- `quote_s5`: the pre-registered rule.
Reported per grid: the fraction of trades whose outcome label (TP/SL/TIME) differs between
`mid_m1` and `quote_s5`, the crosstab, and the net-points gap at 0.45.

## Verdict (bars, computed by `lab.tools.campaign_score.score_campaign` on the arm files)
Per base-cost arm: TEST n ≥ 40; TEST PF > 1 at 0.45 and 0.80; TRAIN PF > 0.9; ≥ 55 % TEST
months positive and no month > 50 % of TEST net; regime independence (positive in gold-up
AND gold-down months, or |corr| < 0.4). All five jointly = PASS. Also reported: by year, by
side (long/short), by grid slot, and the control comparison. One sentence on whether the
daily result generalises, judged on whether ANY intraday grid passes the bars and on
whether the per-trade expectancy in R has the same sign as the daily test's +0.25 R.

## Compute budget
Vectorised numpy walks over the S5 window of each event; ≤ 1 worker process; well under
45 min.

## Outputs
- `crt_lib.py` (candles, events, walks, controls), `run_crt.py` (runs everything, writes
  `results/`), `results/run_output.txt` (raw stdout — REPORT.md copies from it),
  `results/trades_<grid>.parquet`, `results/arms/*.json + *.trades.parquet` (for
  `campaign_score`), `results/bars.csv`, `results/control_*.csv`, `results/wick_*.csv`,
  `results/flip_*.csv`, `REPORT.md`.
