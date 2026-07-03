# S96 in-place rewrite — pure M5 EMA 9/21 crossover

**Date:** 2026-07-03
**Status:** Approved (user, 2026-07-03)
**Supersedes:** the S96 entry/exit logic defined in
`2026-07-02-strategy-manager-design.md` §4 (H1 Donchian(24) momentum
continuation). The manager architecture in that spec is unchanged.

## Goal

Replace the momentum child strategy's logic in place: the H1 Donchian(24)
close-through continuation with EMA20/50 agreement becomes a **pure M5
EMA 9/21 crossover** system — every cross is an entry, both directions,
no higher-timeframe filter inside the strategy. Chop protection comes
solely from the Strategy Manager's existing TRENDING + H4-bias gating
policy, which this change does not touch.

## Identity — what stays

- File stays `strategies/backtest_strategies/s96_h1_momentum.py`.
- `NAME` stays `KRONOS_S96_H1_MOMENTUM`. This is the strategy's DB
  identity, keyed in `entry_manager.py`'s `_VARIATION_STRATEGY_NAME` map,
  the `deploy_manager.py` roster, the ManagedStrategy `momentum` slot, and
  the compose service env (`RESEARCH_STRATEGY: s96_h1_momentum`). Keeping
  it means zero DB migration and zero compose changes.
- The "H1" in the name becomes historical. The module docstring,
  `CONFIG.description`, and the `deploy_manager.py` description text are
  updated to describe the new logic. Renaming the strategy is an explicit
  non-goal (optional future cleanup).

## Entry logic — what changes

Delete all H1 resampling, Donchian, and EMA20/50 bias code. The strategy
evaluates the last closed M5 bar from `w5m` directly (same convention as
S97 `s97_snap_scalper_m5.py`):

- Compute EMA9 and EMA21 on M5 closes.
- A signal fires only on the cross **event**, evaluated on the last bar
  `i`: `ema9[i-1] <= ema21[i-1] and ema9[i] > ema21[i]` → BUY;
  the mirror (`>=` then `<`) → SELL.
- No H1/HTF filter. No session window (`session_start_hour` /
  `session_end_hour` stay `None` — runs around the clock, as now).
- Warmup guard: return `None` when `w5m` is missing or has fewer than
  `_MIN_M5 = 120` bars (EMA21 + ATR14 stabilization).
- `cooldown_s`: 3600 → **300** (one M5 bar). A cross is an event, not a
  state, so it cannot re-fire every bar; the cooldown is a backstop only.
- `max_concurrent_positions` stays 1.

## Exits — rescaled to M5

Same chandelier design as today, on a faster clock:

- Initial stop = entry ∓ **1.5 × ATR(14, M5)** (`_K_ATR` unchanged, ATR
  timeframe changes from H1 to M5).
- `trailing=True` — position_monitor ratchets the chandelier trail exactly
  as today; no position_manager changes.
- `take_profit` stays a far **30 × ATR** broker backstop (`_FAR_ATR`),
  never a realistic cap.
- `max_hold_min`: 2880 → **480** (8 hours).
- Reason strings become `S96_M5_CROSS_LONG` / `S96_M5_CROSS_SHORT`.

## Safety & validation

- This change **voids the S96 backtest verdict** (test-period PF 1.445 on
  the Donchian logic does not transfer). As part of this change the
  ManagedStrategy momentum row's `live_eligible` flips back to **False**
  (via `deploy_manager.py`); arm stays OFF, the compose service stays
  DRY_RUN.
- Re-validation through the backtest harness is a **required follow-up
  before arming live**. No one arms this strategy on the old verdict.
- Manager gating policy (`strategy_manager/policies.py`, TRENDING +
  `h4_bias != neutral`) is unchanged.

## Testing

Rewrite the S96 block in `tests/test_s95_s96_s97.py` for the new contract:

1. Cross-up on synthetic M5 data → trailing BUY with correct SL/TP/hold.
2. Cross-down → trailing SELL.
3. Trend continuation with no cross on the last bar → `None`.
4. Flat/chop with no cross → `None`.
5. Insufficient history (`< _MIN_M5`, and `w5m=None`) → `None`.
6. SL distance tracks M5 ATR.
7. Config contract: `NAME` unchanged, `cooldown_s == 300`, sessions `None`,
   `max_concurrent_positions == 1`.

`test_manager_sim.py` and `test_deploy_manager.py` must keep passing
untouched (name and slot don't change). Full suite: `pytest tests/ -q`.

## Deploy (later — not part of this change)

Box images are baked: shipping means scp + `docker compose -p kronos build
s96_h1_momentum` on the algorobos box, following the box-drift rules
(merge additively, never overwrite box compose.yml / entry_manager.py).
No urgency and no live risk: the service is DRY_RUN and the manager master
switch is OFF.

## Decision log

- Entry change requested by user 2026-07-03: "make it M5 ema crossover".
- Structure: **pure** M5 crossover (user chose over H1-bias+M5-trigger and
  M5+Donchian hybrids).
- EMA pair: **9/21** (recommended default, accepted).
- Exits: **ATR trail rescaled to M5** (recommended default, accepted).
- Ship as: **in-place rewrite of s96** (user chose over a new s99 file).
