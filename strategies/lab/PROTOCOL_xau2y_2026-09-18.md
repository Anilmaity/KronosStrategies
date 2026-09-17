# Protocol — XAU_USD 24-month ICT strategy screen (pre-registered 2026-09-18)

Written BEFORE any arm of this campaign ran. Anything below that changes after results
exist must be recorded as a change, with the reason, in the campaign log.

## Question
Which of the repo's ICT/SMC strategies (and which configuration of them) has a
demonstrable, cost-robust, regime-independent edge on XAU_USD over the last two years —
and is any of them good enough to trade?

## Data
- Cache: `backtest/results/bars_cache_2y/` (build + QA: `QA_REPORT.md` there). OANDA
  practice feed throughout; 2025-01-01 → 2026-08-12 is the production cache verbatim,
  2024-08-15 → 2025-01-01 is the same-source deep history. **2026-08-12 → 2026-09-18 is
  absent** (no OANDA key on this machine) — the window is therefore ~23.4 months, and any
  claim is about 2024-09 → 2026-08.
- Replay window: start **2024-09-01** (warm-up before it), end **2026-08-12**.
- Split, fixed now: **TRAIN 2024-09-01 → 2025-12-01** (15 mo), **TEST 2025-12-01 →
  2026-08-12** (8.4 mo). TEST is judged; TRAIN is context.
- Costs: **0.45 pts** base (the campaign convention), **0.80 pts** stress. Measured median
  XAU spread is 0.59, so 0.80 is ordinary operating cost, not a pessimistic scenario.
- Harness: `lab.harness.replay` — the REAL `get_signal()` of each module, real gate_rules,
  SL-before-TP within a bar, M1 exits. Points-primary; no USD, no lot sizing.

## Stage 1 — screen (every runnable module at its shipped configuration)
Arms: module × cost ∈ {0.45, 0.80}. Windows per module from `STRATEGY_INVENTORY`.

A module **passes the screen** only if ALL hold:
1. TEST n ≥ 40.
2. TEST PF > 1.0 at 0.45 **and** TEST PF > 1.0 at 0.80.
3. TRAIN PF > 0.9 at 0.45 (no train/test flip; TRAIN may be weaker, not inverted).
4. Monthly consistency: ≥ 55 % of TEST months positive, and no single TEST month > 50 %
   of TEST net points (an edge, not one event).
5. Regime independence: over the full window, positive net points in BOTH gold-up and
   gold-down calendar months (monthly close-to-close), OR |corr(monthly pts, gold %)| < 0.4.

"Near miss" = fails exactly one bar by a small margin (PF within 0.05 of the threshold, or
n within 10 of the floor) → eligible for Stage 2 only if the miss is the cost bar or the
regime bar, since those are what Stage 2 gates can address.

## Stage 2 — targeted arms (survivors + near misses only; pre-declared grids)
Only harness-level gates and at most ONE module constant per strategy; grids fixed here:
- `sides`: BUY-only, SELL-only.
- `block_hours` by session: block Asia (0–6), block London (7–11), block NY (12–20).
- `regime`: above_sma20, below_sma20 (daily, look-ahead safe).
- one module constant (`patch`): the strategy's TP multiple or its single documented
  tunable, at 3 values bracketing the shipped one.
Each Stage 2 arm is judged by the same five bars, PLUS
6. Plateau: the neighbouring grid values move the same direction (no lone spike).
7. It must beat the Stage 1 arm on TEST PF **and** TEST points at 0.80 cost.

## Stage 3 — book
For the survivors: daily-points correlation matrix, equal-risk book (1 R per trade = the
arm's median |entry − sl|), book PF / max DD / worst month at 0.80 cost. Nothing here is a
ship decision; it answers "do these edges add up".

## Multiple comparisons — stated up front
Stage 1 tests ~15 modules × 2 costs; Stage 2 adds ≤ 12 arms per survivor. With ~60 arms
and a 5 % false-positive rate per bar, 1–3 spurious single-bar passes are EXPECTED. That
is why passing requires all five (Stage 1) or seven (Stage 2) bars jointly, why TEST is
held out from every selection decision, and why the report will say "screen survivor",
never "proven".

## What this campaign cannot say
- Anything about 2026-08-12 → 2026-09-18 (no data).
- Anything about a sustained gold bear market (2024-09 → 2026-08 has none; 2025 is a
  straight-line rally, 2026 a topping range).
- Live fidelity: the harness models entries at the signal bar's close and exits on M1
  with static SL/TP; live entries are one bar late and pay real slippage. Every PF here is
  an upper bound.

## Outputs
- `lab/campaigns/xau2y_stage1.py`, `xau2y_stage2.py` — arms as declared.
- `lab/results/xau2y_stage1/`, `xau2y_stage2/` — per-arm json + trades parquet.
- `lab/tools/campaign_score.py` — computes bars 1–7 from the result files; the report
  quotes its output, not hand-typed numbers.
- `lab/REPORT_xau2y_2026-09-18.md` — ranking, survivors, near misses, book, caveats.
