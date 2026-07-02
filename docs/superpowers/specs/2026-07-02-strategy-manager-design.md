# Kronos Strategy Manager — design spec

**Date:** 2026-07-02 · **Status:** approved-to-build (user directive: "plan, build, deploy and test")
**Scope:** all three repos + AWS/Netlify deployment. Symbol scope v1: XAU_USD only.

## 1. Purpose

A regime-aware meta-controller ("Strategy Manager") that watches higher-timeframe market
structure and decides **when each child strategy is allowed to trade**, plus a platform tab to
observe and control it. It separates three concerns that are currently fused:

1. **What the market is** — the Regime Engine (pure analysis, persisted snapshots).
2. **What may trade now** — per-strategy gating policies evaluated by the Manager loop.
3. **What the user allows** — arm switches (OFF / PAPER / LIVE) per strategy + master mode.

Honesty constraints (from `xau-challenge-doctrine` and the 2026-07-02 S5 study): only
strategies with positive held-out expectancy get LIVE eligibility; the scalper slot is
**paper-only** pending the maker-fill re-audit; **no averaging/martingale anywhere** (proven to
fatten tails while faking win rate); a validated edge is not regime-paused unless the gating
itself back-tests better than always-on.

## 2. Architecture (fits the existing service model)

```
                       ┌──────────────────────────────────────────────┐
OANDA candles ────────►│ strategy_manager service (new, compose)      │
(fetch_candles)        │  1. RegimeEngine.compute() → RegimeSnapshot  │──► apis_regimesnapshot
                       │  2. read ManagedStrategy arm modes           │◄── GraphQL mutations (user)
                       │  3. policy(regime) → desired running state   │
                       │  4. flip UserStrategy.is_active accordingly  │──► apis_manageraction (audit)
                       └──────────────────────────────────────────────┘
                                        │ is_active respected on next entry attempt
        ┌────────────────┬──────────────┼──────────────────┬───────────────────┐
        ▼                ▼              ▼                  ▼                   │
 challenge_xau     s95_session_    s96_h1_momentum   s97_snap_scalper_m5      │
 (H4 trend, LIVE-  breakout        (paper until      (PAPER-ONLY, DRY_RUN     │
 eligible, exists) (new runner)    validated)        service)                 │
        └────────────── positions/exits handled by position_manager ──────────┘
```

- **Pause semantics (existing, verified):** `entry_manager.place_entry()` refuses new entries
  when `UserStrategy.is_active=False`; open positions keep their SL/TP/TIME_EXIT handling in
  `position_manager` regardless. The manager therefore never orphans a position.
- The manager **only manages strategies the user has armed**; master mode OFF (deploy default)
  means the loop computes and records regime but flips nothing.

## 3. Regime Engine (`strategies/regime/regime_engine.py`, pure functions)

Input: `fetch_candles` frames (1d×120, 4h×90d, 1h×30d, 15m×10d, 5m×3d, 1m×1d). Output
`RegimeSnapshot` dataclass persisted every ~60s:

- `d1_bias`, `h4_bias`, `m15_structure`, `m5_structure`, `m1_structure` — from
  `ict_engine.get_market_structure` / `get_htf_bias` (bullish/bearish/ranging; long/short/neutral).
- `vol_regime` — ATR(14, H1) percentile vs trailing 30 days: LOW (<25) / NORMAL / HIGH (>75) /
  EXTREME (>95).
- `trend_regime` — efficiency ratio (|net|/path) over 24×H1 and 30×M15: TRENDING (>0.35) /
  RANGING (<0.2) / MIXED.
- `session` — ASIA / LONDON / NY / OVERLAP / ROLLOVER (from UTC hour) + `market_closed`
  (existing `market_timing.is_market_closed_utc`).
- `details` JSON — raw numbers for the frontend (ATR value, ER values, swing levels).

Thresholds are constants in one place; the snapshot stores raw numbers so thresholds can be
tuned without invalidating history.

## 4. Child strategy roster & policies (v1)

| Slot | Module / service | Edge status | Arm ceiling | Gating policy (evaluated when armed) |
|---|---|---|---|---|
| Trend-follow | `kronos_challenge_xau` (exists, live) | validated (PF 1.83, every-year) | LIVE | Always-run; pause only on kill-switch or `market_closed`. No regime gate unless the gating study (task 4) beats always-on out-of-sample. |
| Session breakout | `s95_session_breakout` (new) | must pass held-out backtest | LIVE if test-positive, else PAPER | Run only 06:45–10:00 & 13:15–16:00 UTC and `vol_regime ∈ {NORMAL, HIGH}`. |
| Momentum | `s96_h1_momentum` (new; H1 Donchian continuation, ATR stop) | prior evidence weak; must pass | LIVE if test-positive, else PAPER | Run when `trend_regime=TRENDING` and `h4_bias ≠ neutral`. |
| Scalper | `s97_snap_scalper_m5` (new; M5 snap-fade + D1 ICT bias, hard stops) | edge unproven at honest costs; maker re-audit pending | **PAPER only in v1** | Run 03:00–09:00 UTC, `vol_regime ∈ {LOW, NORMAL}`, `d1_bias ≠ neutral`. |

New strategies implement the existing `backtest_strategies/base.py` contract (`NAME`, `CONFIG`,
`get_signal(w1m, w5m, w15m, now_utc)`) and run under `research_runner.py` as their own compose
services (the established pattern). PAPER = service env `DRY_RUN=true` (full pipeline, DB rows,
no broker orders) — measurable without risk.

Global guards in the manager: daily realized-loss kill-switch (default −$150, config), max
concurrent open positions across children (default 3), automatic re-enable next trading day.

## 5. Data model (Django owns; SQLAlchemy mirrors in both repos)

- `RegimeSnapshot` — `symbol`, `d1_bias`, `h4_bias`, `vol_regime`, `trend_regime`, `session`,
  `details` JSON. Index on (`symbol`, `created_at`).
- `ManagedStrategy` — FK `user_strategy` (the broker-bound unit whose `is_active` the manager
  flips; one ManagedStrategy per managed UserStrategy), `arm_mode` (`OFF|PAPER|LIVE`), `live_eligible` (Boolean,
  set from backtest verdicts; LIVE arm requests above eligibility are rejected), `policy_key`,
  `policy_params` JSON, `desired_active`, `last_reason`, `last_evaluated_at`.
- `ManagerConfig` — single row: `master_mode` (`OFF|ON`), `kill_switch_loss_usd`,
  `max_concurrent_positions`, `state` JSON (kill-switch trip date etc.).
- `ManagerAction` — FK `managed_strategy` (nullable), `action` (`START|PAUSE|KILL_SWITCH|INFO`),
  `reason` (text), `regime` JSON snapshot copy.

Migration `0007_strategy_manager` on branch `feat/strategy-manager` (off `feat/strategy-archive`,
which matches the live DB at 0006).

## 6. GraphQL API (auto-discovered; all `@user_authenticate`)

Queries: `strategy_manager_state.py` → `StrategyManagerState` (config, managed strategies with
status + P&L, latest regime snapshot, last 50 actions); `regime_history.py` (snapshots since N
hours, for charts).
Mutations (`mutation/user/`): `set_manager_mode.py` (ON/OFF), `arm_strategy.py`
(managedStrategyId, mode; enforces `live_eligible`), `update_manager_config.py` (kill-switch $,
max concurrent). Types in `apis/schema/types/` as `DjangoObjectType`s.

## 7. Frontend tab (`app/(main)/manager/`)

Sidebar entry "Strategy Manager" (constants.ts). Page = `page.tsx` + `_components/main.tsx`
(imperative `client.query/mutate` + `middleware(err)` + toast, per Archive pattern). Sections:

1. **Master bar** — manager ON/OFF switch, kill-switch status, config dialog.
2. **Regime panel** — D1/H4/M15 bias chips, vol & trend regime badges, session, raw numbers;
   mini history strip from `regime_history`.
3. **Strategy cards** (one per managed strategy) — name, slot, arm mode selector (OFF/PAPER/LIVE,
   LIVE disabled unless `live_eligible`), running/paused state with the manager's `last_reason`,
   today's P&L and open-position count.
4. **Action log** — table of recent ManagerActions.

Algorobos tokens (gold/ink/bg, mono labels with 0.28em tracking, 2px radius). Poll every 30s
(no websockets in v1).

## 8. Backtest validation (gate for `live_eligible`)

Data: existing `xau_m5_3y.csv` (2023→2026-06) + fresh OANDA fetches; costs per the S5-study
conventions (0.20-pt spread + $4.90/lot; conservative fills; hard stops). Train 2023–2024,
test 2025–2026 held out. Deliverables: per-strategy verdict (test expectancy > 0 → live-eligible)
and a **gating study**: manager-gated vs always-on for each child (adopt a gate only where the
gated version wins out-of-sample). The scalper stays paper regardless in v1.

## 9. Deployment (explicitly requested)

1. Box (`ubuntu@13.126.204.82`, `-F /dev/null`, key `algobet-ssh.pem`): scp KronosStrategies
   changes → `docker compose -p kronos build strategy_manager s95 s96 s97` → up -d. Manager
   starts with `master_mode=OFF`; paper runners with `DRY_RUN=true`. Existing services
   (challenge_xau, Telegram bot, collectors) untouched.
2. Backend on box: pull/copy branch, `manage.py migrate` (adds 0007), restart backend service
   (bind-mount deploy per Archive-feature precedent — verify on box).
3. Frontend: commit to `main`, push → Netlify ships live. Tab renders with manager OFF.
4. Verification: GraphQL `strategyManagerState` responds; tab loads; `docker compose -p kronos ps`
   healthy; no orders placed anywhere (master OFF); CHALLENGE_XAU still trading normally.

**Nothing trades that isn't trading today until the user arms it in the tab.**

## 10. Testing

- Unit (KronosStrategies `tests/`): regime classification on synthetic frames; policy evaluation
  truth table; manager flip logic against a temp SQLite DB; kill-switch trip/reset.
- Unit (backend `apis/tests`): model defaults, mutations (arm above eligibility rejected),
  query shape — on the in-memory SQLite harness.
- Backtests as §8. Frontend: `npm run build` + lint pass; manual smoke via GraphQL.

## 11. Out of scope (v1)

News-calendar guard, multi-symbol (XAG/BTC), websocket live updates, manager-driven position
sizing, strategy auto-discovery, averaging of any kind (doctrine-forbidden), editing gating
policies from the UI (params visible read-only; editable via config JSON only).
