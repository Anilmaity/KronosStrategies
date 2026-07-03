## Task 6 — Market-Realistic Fills + Fill-Realism Corrected Report

**Status:** DONE
**Date:** 2026-07-03
**Branch:** feat/strategy-manager

---

### FIX A — Engine: market-realistic fills (manager_sim_engine.py)

- Added `fill_price: float | None = None` parameter to `open_position()`.
  When `None`, original behaviour is preserved (existing tests unchanged).
  When provided, `entry_px = fill_price +/- friction` (BUY/SELL).
- In `run_sim`, the current 1m bar's `close` is passed as `fill_price` on every
  new entry.
- Phantom guard added in `run_sim`: if `bar_close + friction >= tp` (BUY) or
  `bar_close - friction <= tp` (SELL), the entry is silently dropped — no P&L
  booked, live would never open it.

### FIX A Tests (test_manager_sim.py)

Two new tests added in "Task 6" section (total tests: 36 pass, all green):

1. `test_fill_price_overrides_signal_entry` — fill_price=102 on BUY with
   sig.entry_price=100 => entry_px = 102.25. Verifies fill_price replaces
   sig.entry_price while friction still applies.

2. `test_phantom_beyond_tp_skipped_in_run_sim` — stubs a strategy that always
   returns BUY with tp=100.1 (well below bar close of ~3300). Verifies that
   run_sim emits zero trades (all phantom-skipped).

### FIX B — Correction script (manager_sim_correction.py)

New file at `strategies/backtest/manager_sim_correction.py`.
Reads committed CSV trades + 1m bars_cache parquet, applies fill-realism
correction, and outputs markdown tables for the AMENDED summary.

### FIX C — Amended summary

`strategies/backtest/results/manager_sim/summary_20260702_235849_AMENDED.md`
Created (original untouched). Contains:
- Fill-Realism Correction section with per-strategy/combined tables + phantom example
- Ex-S96 View
- Live Sizing Note
- REVISED VERDICT (PAPER mode recommended)

---

### Corrected headline numbers

| Metric | Value |
|--------|-------|
| Gated raw (0.02L) | +577.36 |
| Gated corrected (0.02L) | +133.36 |
| Gated corrected (0.01L live) | +66.68 |
| Phantom trades dropped (gated) | 3 |
| Phantom trades dropped (ungated) | 7 |
| Phantom example | 2026-07-01T14:00 SESSION_BREAKOUT BUY: bar_close=4099.245, entry_px=4099.495 >= TP=4098.980, old pnl=$102.34 DROPPED |

**S96 ungated anomaly note:** Corrected ungated swings from -$5,094 to +$4,122.
This is because S96's sig.entry_price is a computed momentum level systematically
offset from bar_close; for SELL signals, higher bar_close improves the corrected
SELL fill. The ex-S96 view (gated +$44 vs ungated -$89 corrected) is the reliable
gating-edge signal, unaffected by S96 fill anomaly.

Corrected gated of +$133.36 is in the vicinity of reviewer's estimate of $150-180
(within 12%). Within acceptable tolerance; proceeding.

---

### Test run

`pytest tests/test_manager_sim.py tests/test_manager_sim_report.py -q`
=> 36 passed, 0 failed. (Pre-task baseline was 34; +2 new tests = 36 total.
Spec estimated 38; the discrepancy is the pre-task count was 34, not 36.)
---

## Re-review fixes (final three blocking edits) — 2026-07-03, commit a08f7ce

### EDIT 1 — AMENDED report honesty (summary_20260702_235849_AMENDED.md)

- Ungated S96 corrected values (+$4,211.64 / +$2,105.82) replaced with
  "N/A — not computable from CSVs (TRAIL exits are fill-path-dependent;
  requires engine re-run)". Corrected ungated combined now presented ex-S96
  only and labeled as such.
- All "exact for TRAIL exits" and "real finding about S96's signal structure"
  claims deleted (AMENDED file + manager_sim_correction.py docstring and
  generated report text). True mechanism stated: S96's entry_price is the
  last closed H1 bar's close (up to ~1h stale); on crash days (2026-06-17:
  five consecutive BUYs at identical stale 4379.8 while market traded
  ~4264-4272) the CSV correction refills at market but keeps exits anchored
  to stops derived from the phantom entry, converting phantom losers into
  phantom winners — S96 corrected numbers are artifacts in BOTH directions.
- Gated S96 corrected (+$89.29) marked "uncertain sign, ±~$100 (same TRAIL
  limitation, smaller sample)". Corrected combined gated restated as
  including an S96 term of uncertain sign bounded ~±$100.
- Verdict now quotes the ex-S96 corrected pair as THE rubric-relevant
  comparison; added lines on (i) gated/ungated entry sets differing via
  gate-delayed detection and (ii) the ex-S96 delta being directional
  evidence, not expectancy proof. S96 contingency changed to: engine re-run
  with market fills + both phantom guards (CSV correction cannot produce it).

### EDIT 2 — engine beyond-SL guard (manager_sim_engine.py)

run_sim entry admission now mirrors the beyond-TP skip:
BUY skipped if fill (close + friction) <= sig.stop_loss; SELL skipped if
fill (close - friction) >= sig.stop_loss. Applies to trailing strategies
too (trail seeds hwm from the entry fill).
New test: test_phantom_beyond_sl_skipped_in_run_sim — BUY sl=98, tp=110,
entry_price=100 with detection-bar close 97.5 → entry skipped (no position,
no trade); compute_regime stubbed + get_signal call counter guards against
a vacuous pass.

### EDIT 3 — correction script beyond-SL guard + refreshed tables

_correct_trades drops (as phantoms) non-trailing trades whose corrected fill
is beyond the signal SL (trailing rows exempt: CSV `sl` is the ratcheted
stop). Exactly 4 such trades found, as the reviewer predicted:
gated 1 (S97 2026-04-22T05:17 SELL), ungated 3 (same S97 trade +
SESSION_BREAKOUT 2026-05-01T13:34 and 2026-06-11T13:46 SELLs).

Refreshed corrected numbers (0.02L):

| Metric | Old | New |
|--------|----:|----:|
| Gated combined corrected | +133.36 | +131.42 (incl. uncertain S96 term) |
| Gated phantoms | 3 | 4 |
| Ungated phantoms | 7 | 10 |
| Gated ex-S96 corrected | +44.07 | +42.13 |
| Ungated ex-S96 corrected | -89.11 | -99.10 |
| Ex-S96 corrected delta (G-U) | +133.18 | +141.23 |

Final ex-S96 corrected pair: **gated +$42.13 vs ungated -$99.10** (0.02L,
3 months). Gating edge survives: gated positive, ungated negative.

### Test run

`pytest tests/test_manager_sim.py tests/test_manager_sim_report.py -q`
=> 37 passed, 0 failed (36 baseline + 1 new beyond-SL test).

---

## Task 6b — per-process sensitivity variant driver (parallel runs)

### What was built

New `strategies/backtest/manager_sim_variant.py`: runs ONE sensitivity
variant per OS process so all 6 can run concurrently (vs run_sensitivity's
serial ~4.4 h in-process grid). setattr on regime_engine constants is safe
per-process; window variants go through `_shifted_specs` as before.

Least-invasive extraction in `manager_sim_report.py`: the 6 variant
definitions (4 threshold + 2 window) moved to a module-level
`SENSITIVITY_VARIANTS` dict (single source of truth; insertion order ==
historical run order). `run_sensitivity` now iterates that dict — labels,
thresholds, windows, order and output rows are byte-identical to before.
No engine/CLI files touched.

### CLI

Run mode: `python -m backtest.manager_sim_variant --variant NAME
--start 2026-04-01 --end 2026-07-02 --out PATH.json [--cache-dir DIR]
[--slice-rows JSON]` — one gated full run with default SimConfig (faithful
slice_rows, market fills), dumps JSON: variant, label, trades, net_usd,
max_dd_usd, wr, pf (null when infinite), per_strategy_net.

Collect mode: `--collect GLOB --base-gated CSV --base-ungated CSV
--out-md PATH` — loads the variant JSONs, reconstructs base gated/ungated
combined nets from the base run's trade CSVs (sum of pnl_usd), and APPENDS
a standalone sensitivity-grid markdown with the rubric-condition-4
(no-sign-flip) PASS/FAIL verdict. Flags a warning line if fewer/more than
6 variant files matched.

### Tests

`tests/test_manager_sim_variant.py` — 10 new tests: variant NAME->definition
mapping matches run_sensitivity's historical 6 (exact thresholds, windows,
labels, order); driver imports the report's dict by identity (no duplicated
numbers); unknown variant and incomplete --collect args exit non-zero; JSON
output shape on the tiny synthetic cache (shallow slice_rows via the
--slice-rows test hook) for both a threshold and a window variant;
collect-mode grid rendering with cond-4 PASS and sign-flip FAIL cases.

`pytest tests/test_manager_sim.py tests/test_manager_sim_report.py
tests/test_manager_sim_variant.py -q` => 47 passed (37 existing + 10 new).

---

## Task 6c — corrected-engine base run + sensitivity grid (2026-07-03, final)

### Corrected-engine base run (market fills, native — supersedes AMENDED CSV correction)

`--mode both`, faithful slice depths, 2026-04-01 → 2026-07-02 (exclusive).
Report: `results/manager_sim/summary_20260703_054509.md` (AUTHORITATIVE).

| Mode | Trades | WR% | PF | Net USD | Max DD $ |
|------|-------:|----:|---:|--------:|---------:|
| Gated | 105 | 49.5 | 1.10 | +131.42 | 464.26 |
| Ungated | 254 | 42.5 | 1.03 | +109.58 | 794.46 |

Engine-native gated net matches the CSV-corrected estimate exactly
(+$131.42), validating the correction methodology for gated. Ungated,
however, is +$109.58 native vs the CSV correction's negative estimate —
the path-dependent TRAIL/kill-switch terms the CSV pass could not fix
were material on the ungated book. S96 real numbers (the open question):
gated +$89.29 (10 trades), ungated +$208.22 (81 trades) — gating pauses
S96 98.2% of the time and REDUCES its return; its value in the gated book
is DD control, not P&L. One ungated kill-switch trip (2026-05-07).

### Sensitivity grid (6 parallel per-process variants, manager_sim_variant.py)

Artifact: `results/manager_sim/sensitivity_parallel.md` + `variant_*.json`.
Base delta (G-U) +$21.84. Variant nets: er_loose +$136.78, er_tight
+$192.53, vol_loose +$66.34, vol_tight +$172.73, win_minus30 +$23.03,
win_plus30 +$71.57. Variant deltas (V-U): 3 of 6 NEGATIVE (vol_loose
-$43.24, win_minus30 -$86.55, win_plus30 -$38.01).

**Rubric condition 4: FAIL.** No return plateau (+$23…+$193 around +$131).
Max DD is a plateau: every variant $400-524 vs ungated $794.

### Final verdict

Rubric (all-4-conditions) says DO NOT recommend master ON for live.
**RECOMMEND arm PAPER**: the return edge (+$22/3mo) is inside parameter
noise, but the drawdown reduction (~40-50%) is robust across every
perturbation. Re-evaluate after >=3 months of gated paper fills.

### Post-run fix

cp1252 UnicodeEncodeError in collect-mode `print(md)` (U+2212 in G−U/V−U
labels) — replaced with ASCII, same class as 49dca3f. File output was
unaffected (UTF-8); only the console echo crashed.
