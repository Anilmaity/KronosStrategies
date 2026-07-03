# S98 Z-Score Mean Reversion — design spec

**Date:** 2026-07-03
**Status:** approved (brainstorm 2026-07-03)
**Replaces:** KRONOS_S97_SNAP_SCALPER (retired from roster — negative in both sim
modes at honest fills: gated −$43.32 / PF 0.65, ungated −$41.57 / PF 0.80; the
TP 0.5×ov / SL 0.9×ov payoff needs ~64% WR to break even before costs and the
strategy delivered 63% at best even after correcting for the sim cooldown bug).

## Why this concept

The 03:00–09:00 UTC scalper slot in the Strategy Manager roster needs a
replacement, chosen from the quant family (user decision). Per the
quant-strategy-design skill's empirical table (6-month XAUUSD cache), z-score
mean reversion is the only quant strategy with a positive edge (M15: 242
trades, PF 1.19, ExpR 0.13); ATR-squeeze (PF 0.89) and vol-filtered momentum
(PF 0.92) both lose. The "vol-regime" element of the concept is delegated to
the manager's gating policy — the strategy itself stays at 3 parameters.

Payoff structure is the inverse of S97's mistake: winners run ~2×std50 to the
mean, losers cut at ~1.5×std50 beyond entry, so the strategy does not need a
super-majority win rate to clear 0.5pt round-trip friction.

## 1. Strategy module

`strategies/backtest_strategies/s98_zscore_mr_m15.py` — standard contract:
`NAME = "KRONOS_S98_ZSCORE_MR"`, `CONFIG: StrategyConfig`,
`get_signal(w1m, w5m, w15m, now_utc) -> Signal | None`.

Evaluated on **closed M15 bars** from `w15m`:

- **Closed-bar guard:** drop the last row of `w15m` if its bar time + 15 min
  > `now_utc` (the forming bar must never enter the computation).
- **Z-score:** `z = (close − SMA50) / std50` on M15 closes (rolling 50).
- **Entry on the cross, not the state:** previous closed bar |z| < 2.0 AND
  current closed bar z ≥ +2.0 → SELL; z ≤ −2.0 → BUY. One signal per crossing
  bar; no refire while the series stays stretched.
- **Stop (hard, always):** price at z = ±3.5, i.e. `SMA50 ± 3.5 × std50`
  beyond the mean on the entry side.
- **Target:** `SMA50` at signal time (z = 0).
- **ADF prerequisite:** `statsmodels.tsa.stattools.adfuller` on the
  `close − SMA50` residuals over the lookback window; entry only if
  p < 0.05. Non-stationary residuals = trending market = no fade.
- **Session gate:** 03:00–09:00 UTC (CONFIG hours + in-function defense in
  depth, same pattern as S97).

**Parameters (exactly 3):** lookback = 50, entry_z = 2.0, stop_z = 3.5.
Fixed non-parameters: target z = 0, ADF p-threshold 0.05 (standard),
`max_hold_min = 240` (operational guard — reversion to SMA50 takes hours;
enforced by position_manager TIME_EXIT as usual), `cooldown_s = 900` (one
M15 bar), `max_concurrent_positions = 1`.

**Dependency:** `statsmodels` added to `strategies/requirements.txt` (runs
inside `get_signal` live, so it ships in the strategies image). Hand-rolled
ADF rejected — subtle statistical bugs are worse than the scipy pull.

## 2. Manager integration

S98 takes S97's roster slot:

- ManagedStrategy: slot `scalper`, arm = OFF (deploy default), policy:
  `session 03:00–09:00 UTC`, `vol_regime ∈ {LOW, NORMAL}`,
  `trend_regime ≠ TRENDING` (efficiency-ratio gate; bias-agnostic — MR trades
  both directions, so no d1_bias requirement, unlike S97).
- S97 retired from the roster (ManagedStrategy removed/deactivated in the
  deploy script; UserStrategy deployed=False). Box decommission (compose
  service swap s97 → s98, DRY_RUN=true) is a separate, later deploy step the
  user triggers — box compose is never overwritten, merged additively.
- Deploy script: idempotent `db/deploy_*.py` pattern, dry-run default,
  `--commit` to write.

## 3. Sim fidelity fix (prerequisite)

`manager_sim_engine.py` currently ignores `cooldown_s` — live
(`research_runner.py:133`) blocks entries within `cooldown_s` of the last
entry. Fix: engine enforces per-StratSpec cooldown from last entry time,
identical semantics to live. This changes ALL sim baselines (S97 ungated
moves from −$41.57 to ≈ −$12.30 under a 600s replay filter), so the base
3-month run is re-run as part of validation anyway.

## 4. Validation gauntlet

Rules (backtest-expert): OOS is touched ONCE; walk-forward selection only;
plateau, not spike.

1. **Standalone backtest** on the 18-month M15 cache
   (`bars_cache/is_XAU_USD_15m.parquet`, 2025-01 → 2026-07):
   train = 2025, OOS = 2026 (single shot).
2. **Parameter sweep on train only:** lookback, entry_z, stop_z each ±30% in
   5 steps; require a plateau around the chosen point (no isolated spike).
   Bonferroni-aware read of the grid.
3. **Acceptance to ship:** OOS PF ≥ 1.15 at 0.5pt round-trip friction,
   ≥ 60 OOS trades, plateau confirmed. Fail → S98 does not ship; the slot
   stays empty (no strategy beats a losing strategy).
4. **Manager-sim re-run** (with cooldown fix + S98 StratSpec, gated policy
   from §2): 3-month gated/ungated pair. Require S98 gated ≥ $0 and no
   degradation of the combined PAPER verdict.

## 5. Tests

- Crossing logic: fires only on the crossing bar; no refire while stretched.
- Closed-bar guard: forming M15 bar excluded (both directions, S96-lesson
  regression style).
- ADF gate: synthetic stationary AR(1) residuals → fires; random-walk /
  trending series → no signal.
- SL/TP arithmetic: BUY and SELL cases, floor/rounding, hard SL present on
  every signal.
- Sim cooldown enforcement: regression test reproducing the S97 Apr-14 churn
  pattern (5 entries in 19 min) → exactly 1 entry with cooldown 600s.
- Full sim suite stays green.

## Out of scope

- Live/box deployment of S98 (separate deploy step, user-triggered).
- Arming any strategy (user does this via /manager tab).
- Changing the manager verdict pipeline or other strategies' policies.
- Pairs/DXY variants (no DXY data in cache).
