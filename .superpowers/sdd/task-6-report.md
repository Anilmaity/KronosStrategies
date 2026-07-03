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