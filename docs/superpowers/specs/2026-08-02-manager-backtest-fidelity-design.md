# Manager Backtest tab — sim-vs-live fidelity fix

Date: 2026-08-02  |  Branch: `feat/mbt-fidelity` (off `feat/strategy-manager`)  |  Author: Claude Code

## Problem

The Manager Backtest tab (the `audit_worker` package shipped 2026-08-01) reports a
sim-vs-live PnL comparison that diverges far more than real fidelity warrants. Operator
observation: last month (July 2026) live closed negative almost every week, against a belief
that "the backtest was positive almost every week." Investigation shows the headline gap is
dominated by **measurement artifacts in the tab itself**, on top of a real (already-documented)
component that the tab cannot and should not try to erase.

### Root cause (evidence, 2026-08-02 code read)

1. **Sizing-base mismatch (dominant USD distortion).** The worker builds the sim with a flat
   `lots=0.02` (`worker.py:141`) and `SimConfig.pts_to_usd = pts × lots × 100`
   (`manager_sim_engine.py:43`) — a constant ~$2/pt for every trade. Live sizes each leg by
   **risk** when `RISK_PER_TRADE_USD > 0` (`entry_manager.py:291-307`):
   `lots = RISK_USD / (SL_pts × 100)` clamped to `[MIN_LOT 0.01, MAX_LOT 0.20]`, so a typical
   2–4pt stop lands near 0.10–0.12 lots ≈ $10–12/pt. On *identical* trades the sim USD is
   ~5–6× smaller than live USD. `live_summary` converts correctly (`realized_pl × 100`,
   `live_deltas.py:23`); the defect is that the two sides sit on different sizing bases. This
   is the "lots-vs-multiplyer sizing caveat in USD deltas" tracked-but-unfixed follow-up.

2. **Sim ignores live entry-quality gates (trade-count distortion).** `evaluate_gates`
   (`manager_sim_engine.py:69-92`) models only regime policy + kill-switch + max_concurrent.
   Live additionally rejects via `place_entry`: `entry_drift`, `sl_too_tight` (MIN_SL_DIST
   1.5pt), `no_add_to_loser`, `news_blackout` (12:25–12:45 UTC), `risk_too_wide`, dup,
   soft-brake. The July 2026 fidelity audit measured 81/163 signals rejected — the sim takes
   roughly 2× the trades live places.

3. **Irreducible component (NOT a bug — do not "fix").** The July audit established live
   *execution* is fine and live *generation* matches the sim, but sub-point M5 FVG/sweep
   levels are noisy (real-time vs finalized OANDA candles) and flip 1.5R outcomes (±$700 path
   noise over 11 days), because the strategy class has no robust edge (12-month sim PF 0.974,
   20/55 weeks positive). Over a short window this dominates; a small-window USD comparison is
   not a reliable signal by itself.

### Framing note (why "positive every week" is the wrong baseline)

No *faithful* backtest in the repo shows weekly-positive returns. The faithful 12-month
manager sim is negative in every mode; the most recent per-signal upgrade study
(`docs/research/2026-07-30-trailing-exits.md`) shows S94 all-period PF ~1.11 (1.00 stressed)
and S100 ~0.97 (0.83 stressed), and it *explicitly excludes the execution layer*. Live being
negative most weeks is **consistent** with the faithful evidence. The purpose of this fix is
not to make the sim print positive — it is to make the tab an apples-to-apples comparator so
the *residual* difference (component 3) is what the operator actually sees.

## Goals

- Make the tab's per-strategy sim-vs-live comparison **sizing-invariant** (points-primary) and
  additionally show a **matched-USD** view on a common economic basis.
- **Explain** the trade-count gap: model the two cleanly-replayable live gates in the sim and
  reconcile the residual against the `StrategySignal` audit table.
- Change nothing about the strategies themselves; the current live roster is backtested as-is.

## Non-goals

- No strategy logic changes; no loosening of live entry-quality gates (July audit: net
  protective — do not loosen).
- No trailing-exit preview (S94/S100 chandelier is validated-but-unshipped; out of scope).
- No attempt to model non-replayable gates (entry_drift, risk_too_wide) inside the sim — those
  are surfaced via reconciliation only.
- No backend GraphQL/migration change (result is a JSON blob).

## Approach

Extend the existing `audit_worker` in place (Approach A). Sizing/points/reconciliation live in
the comparison layer; the two deterministic gates live in the sim engine (they change which
trades the sim takes and cannot be post-processed).

## Components

### 1. Points + matched-USD  (`results.py`, `live_deltas.py`)

Each per-strategy entry gains parallel `points` and `usd` sub-blocks on `sim`, `live`, `delta`.

- **Points (headline).** Sim: sum `TradeRecord.pnl_pts`. Live: recover per-position points as
  `realized_profit_loss / lots`, where `lots` is the ENTRY `Order.quantity` for that position
  (`Order.condition == "ENTRY"`, joined on `position_id`). This is used instead of
  `total_buy_quantity` because that column is 0 for shorts and `quantity` zeroes on close — the
  ENTRY order is the only lot source valid for both sides. PF/WR/pnl_pts on both sides.
  Sizing-invariant — this is the fidelity verdict.
- **Matched-USD (secondary).** Infer the live risk budget from the window's live SL-exit
  losers: risk-sizing makes each loser cost ≈ `$R`, so `R̂ = median(|usd|)` over live losing
  trades. Re-price the sim's trades through the same `R̂ / (SL_dist × 100)` clamp
  (`[MIN_LOT, MAX_LOT]`) live uses; live USD stays actual. Emit top-level
  `live_risk_usd_inferred`. Fallback: if too few live losers to infer (< configurable floor,
  default 5), use a `risk_per_trade_usd` run-param if provided, else omit matched-USD and add a
  `notes` line. (User decision 2026-08-02: inferred budget is acceptable; run-param is the
  fallback, not the primary source.)

### 2. Deterministic entry-quality gates in the sim  (`manager_sim_engine.py`)

In the entry branch (currently `manager_sim_engine.py:546-588`), after `gate_ok`/cooldown and
around `get_signal`, add two gates behind `SimConfig` flags (default on for the tab; offline
research runs may disable):

- `sl_too_tight`: skip when `|entry_price − stop_loss| < MIN_SL_DIST_PTS`.
- `news_blackout`: skip signals whose `now` (UTC) falls in a `NEWS_BLACKOUT_UTC` window.

Constants come from a single shared source so sim and live cannot drift: extract the literals
(`MIN_SL_DIST_PTS`, `NEWS_BLACKOUT_UTC`) from `entry_manager` into a small dependency-free
constants module that both `entry_manager` and the worker import. This keeps the worker clear
of `shared.metaapi_client` (which `entry_manager` transitively pulls in) — enforced by
`tests/test_mbt_worker.py`. Rejections increment a per-strategy counter exposed for the
reconciliation block.

### 3. Reconciliation vs `StrategySignal`  (new `audit_worker/reconcile.py`)

Called in the worker "comparing" phase. Per strategy over the window (IST-skew applied to
`created_at` exactly as `live_deltas.live_summary` does):

- `live_generated` = count of `StrategySignal` rows (join `strategy_id → Strategy.name`).
- `live_placed` = count `status == "PLACED"`.
- `rejected` = `{rejection_reason: count}` for `status == "REJECTED"`.
- `sim_trades` = count of sim trades for that strategy.

(Status values per `models.py`: `FIRED` → `PLACED` | `REJECTED`. Window filter uses
`StrategySignal.signal_at` with the same IST-skew handling `live_summary` applies to
`Position.created_at`.)

Emit a `reconciliation` block per strategy. If no `StrategySignal` rows exist for the window
(e.g. a window before the 2026-07-11 audit table went live), mark reconciliation
`"unavailable"` rather than reporting zeros.

### 4. Surfacing  (result JSON + `kronos_frontend`)

Worker `results.assemble` writes: `per_strategy[name]` with `sim/live/delta` each carrying
`points` and `usd` sub-blocks; a `reconciliation` block per strategy; top-level
`live_risk_usd_inferred` and extended `notes`. No GraphQL migration.

Frontend Backtest tab (`kronos_frontend`, display-only): points table as the primary verdict;
matched-USD as a secondary column/toggle; a per-strategy reconciliation row (generated →
rejected-by-reason → placed, vs sim_trades). Existing summary/equity-curve views unchanged.

## Data flow

Unchanged through `run_sim` → `s5_resolve`. The "comparing" phase additionally: derives points
on both sides, infers `R̂` and computes matched-USD, and runs reconciliation — all folded into
`results.assemble`. `worker.py` gains one call into `reconcile.py`; no new phase.

## Result JSON (added keys, illustrative)

```
per_strategy[name] = {
  "sim":   {"points": {pnl_pts, trades, win_rate, profit_factor}, "usd": {pnl_usd, ...}},
  "live":  {"points": {pnl_pts, trades, win_rate, profit_factor}, "usd": {pnl_usd, ...}},
  "delta": {"points": {pnl_pts, trades, win_rate}, "usd": {pnl_usd, ...}},
  "reconciliation": {"live_generated": G, "live_placed": M,
                      "rejected": {reason: count}, "sim_trades": N} | "unavailable"
}
top-level: "live_risk_usd_inferred": R̂ | null, "notes": [...]
```

## Error handling / edge cases

- Empty window / no replayable roster → existing notes path.
- Live closed position with no ENTRY order or `Order.quantity == 0` → skip that row for points,
  add note (should not occur for a normally-closed position).
- Fewer live losers than the inference floor → matched-USD omitted or run-param fallback, noted.
- `StrategySignal` rows absent for window → reconciliation `"unavailable"`.
- IST-skew (+5:30) applied consistently to every `created_at` comparison (Position and
  StrategySignal).

## Testing (extends the existing 35-test suite)

- Points-recovery: synthetic Position rows → `realized/total_buy_quantity` reproduces pnl_pts
  within rounding.
- Risk inference: synthetic live losers → `R̂` = their median absolute USD; floor fallback path.
- Gate mirroring parity: signals that live `place_entry` rejects for `sl_too_tight` /
  `news_blackout` are also skipped by the sim (shared constants).
- Reconciliation aggregation: mixed ACCEPTED/REJECTED StrategySignal rows → correct grouped
  counts; `"unavailable"` when none.
- Worker parity: full `process_run` over a fixed fixture window emits the new keys with stable
  values; `test_mbt_worker.py` still proves no metaapi import.

## Deploy (box) & rollout

1. Branch off `feat/strategy-manager`; implement + green suite locally.
2. Strategies sync to box + `docker compose -p kronos up -d --build backtest_worker`
   (repo-root build context — see manager-backtest-tab memory).
3. Frontend push → Netlify.
4. Run **July 2026** through the tab; read the **points verdict + reconciliation** as the
   trustworthy result. Backend untouched.

## Risks / tracked follow-ups

- `R̂` inference is only as good as the live sample; small windows → noisy budget (mitigated by
  points-primary being the headline and the inference floor).
- Shared-constants extraction must not pull a metaapi import into the worker (guard test).
- Reconciliation depends on the `StrategySignal` audit being complete for the window.
- Points recovery assumes single-fill positions (`total_buy_quantity` == entry size); partial
  fills/scale-ins would need per-order aggregation (not present in the current roster).
