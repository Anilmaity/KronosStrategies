# S5 research programme — shared brief (2026-09-18)

Six independent studies on the 24-month XAU_USD 5-second cache. Each study lives in its own
folder `lab/research_s5/<topic>/` and MUST contain, in this order of creation:
`PROTOCOL.md` (written before any number is computed), the scripts, `results/` (CSV/parquet
+ the raw script output), `REPORT.md`. Nothing outside your folder may be modified.

## Data (all under `KronosStrategies/strategies/`, run python from there: `../.venv/bin/python`)
- **S5**: `backtest/results/bars_cache/s5/XAU_USD/<YYYY-MM>.parquet`, columns
  `time` (UTC, tz-aware), `o h l c` (mid), `bid_c ask_c`, `volume`. 8,682,738 bars,
  2024-08-15 → 2026-09-18. Load with `from lab.tools.qa_s5_cache import load_s5` (556 MB in
  RAM — load ONCE, keep numpy arrays, never loop over 8.7 M rows in Python; vectorise or
  loop only over events). QA: `lab/QA_S5_2026-09-18.md` — integrity PASS; treat
  **2025-12-25 23:00–23:15 UTC as untradeable** (feed spike); spread median 0.58–0.66 by hour.
- **M1/M5/M15/H1/H4/D** (QA'd): `backtest/results/bars_cache_2y/is_XAU_USD_<tf>.parquet`
  (`time, open, high, low, close, volume`), 2024-08-15 → 2026-09-17, UTC-aligned days.
  Select in the harness with `LAB_BARS_CACHE=backtest/results/bars_cache_2y`.
- Harness (optional, for strategy-shaped tests): `lab/harness.py` (`replay`, `Cfg`,
  `load_bars`), `lab/sweep.py` (`Arm`, `run_campaign`), `lab/tools/campaign_score.py`
  (bars 1–5), `lab/s5exit.py` (`resolve(trade, s5, t5, mode, cost, max_hold_min,
  start_offset_s)` — walks S5 from entry, `quote_s5` = long stop/target on the BID, short on
  the ASK). Reuse; do not edit them.

## Protocol every study follows (pre-register in PROTOCOL.md, then do not move the goalposts)
- Window 2024-09-01 → 2026-09-17 (warm-up before). **TRAIN < 2025-12-01 ≤ TEST.** Selection
  decisions on TRAIN only; TEST is read once, for the pre-declared choice.
- Costs **0.45 and 0.80 pts** round trip, on top of quote-level execution where you model
  execution: entries fill at the NEXT S5 bar after the signal (long at ask, short at bid —
  no same-bar fills, no look-ahead); exits on the quote (long exits on bid, short on ask);
  stop checked before target within a bar. Points-primary, no lot sizing.
- Judge any tradeable claim by the xau2y bars (`lab/PROTOCOL_xau2y_2026-09-18.md`):
  n ≥ 40 TEST; TEST PF > 1 at both costs; TRAIN PF > 0.9; ≥ 55 % TEST months positive and no
  month > 50 % of TEST net; regime independence (positive in gold-up AND gold-down months, or
  |corr| < 0.4). Report a **control**: the same entry rule with the ICT condition removed or
  randomised in time (same count, same hours), so the concept is credited only with what
  the control cannot explain.
- State the number of comparisons you make and expect 5 % of single-bar passes by chance.
- Read `~/Projects/KronosVault/60 Concepts/Backtest Methodology Traps.md` before designing:
  geometric confounds (the C2-wick lesson: an entry that already sits beyond the level
  "predicts" delivery beyond it), exit-resolution flips, `label="left"` off-by-one, the
  control doing the work, units bugs, power before interpretation.
- Negative results are results. A concept that does not survive is reported as such, with
  the numbers. Never soften a fail. Every number in REPORT.md is copied from script output.

## Off limits
Nothing outside `lab/research_s5/<topic>/`. No edits to `lab/harness.py`, strategy
modules, `compose.yml`, anything on the box, the DB, or git (the coordinator commits).
No secrets. ≤ 2 worker processes per study (six studies share 14 cores / 38 GB).

## Deliverable
`REPORT.md`: the question, the pre-registered rule, the tables (TRAIN/TEST × cost, with the
control), the bars verdict, what it does and does not establish, and 1–3 concrete
next hypotheses. Final chat message: a 10-line summary with the headline numbers and the
file paths.
