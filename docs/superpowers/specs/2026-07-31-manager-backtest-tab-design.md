# Manager Backtest Tab — Design (2026-07-31)

## Purpose

A dashboard tab that runs a **historical audit** of the Strategy Manager: replay what the
manager *would* have done over a past window — current roster, production regime gates,
kill-switch, max-concurrent — and compare topline results against what live actually did.
Primary use: fidelity/drift auditing over long windows (6+ months) with S5 accuracy applied
surgically where it changes the answer.

Decisions fixed during brainstorming:
- **Purpose:** historical audit (not config sweeps, not pre-arm validation).
- **Compute:** a new throttled worker service on the `algorobos` box (kronos compose stack).
- **Window/accuracy:** 6+ month windows; M1 replay baseline; S5 re-replay only for
  ambiguous exit bars.
- **Live comparison:** summary deltas only (per-strategy PnL, trade counts, win rates) —
  no per-trade matching.

## Architecture (three repos, shared-DB seam)

```
kronos_frontend  /manager-backtest tab
      │ GraphQL (poll 10s active / 60s idle)
Kronos_Backend   RunManagerBacktest / CancelManagerBacktest mutations
                 ManagerBacktestRuns / ManagerBacktestRun queries
      │ writes/reads ManagerBacktestRun rows (Django ORM, migration 0009)
Postgres (shared) ─── ManagerBacktestRun table (job + result JSON)
      │ polled FIFO (SQLAlchemy mirror)
KronosStrategies backtest_worker service (cpus 0.5, mem ~700m)
                 ├─ bars: OANDA REST → box-local parquet cache (M1/M5/M15/H1/H4; S5 on demand)
                 ├─ engine: manager_sim gating loop + current-roster replay + S5 resolver
                 └─ live deltas: Position rows for the same window (read-only)
```

## Data model

Django `apis/models.py` (+ migration `0009`), mirrored **identically** in all three
SQLAlchemy copies (`strategies/shared/models.py`, `position_manager/shared/models.py`,
`strategy_manager/shared/models.py`).

`ManagerBacktestRun(BaseModel)`:
- `label` CharField (user-supplied or auto `audit_<start>_<end>`)
- `status` CharField: `PENDING | RUNNING | DONE | FAILED | CANCELLED`
- `progress_pct` FloatField (0-100), `phase` CharField:
  `fetching_bars | replaying | resolving_s5 | comparing`
- `period_start`, `period_end` DateField
- `params` JSONField: `spread_pts, slippage_pts, lots, kill_switch_usd, max_concurrent,
  regime_cadence_min, include_ungated (bool), roster_snapshot (list of strategy names)`
- `result` JSONField (null until DONE):
  - `summary` per arm: pnl_pts, pnl_usd, trades, win_rate, max_dd_pts, profit_factor
  - `per_strategy`: same stats keyed by strategy name, each with `live` and `delta` blocks
  - `equity_curve`: per arm, downsampled to <=2000 points `[ts, cum_pts]`
  - `s5_resolution`: `n_ambiguous, n_flipped, pnl_delta_pts`
  - `trades_csv`: box path of the full trade list (not stored in DB)
  - `notes`: e.g. "copy slot excluded (external signals not replayable)"
- `error` TextField, `started_at`, `finished_at`, `requested_by` FK User

No per-trade DB rows. Queue-depth cap enforced at mutation: max 3 non-terminal runs.

## Job lifecycle

1. Mutation inserts PENDING.
2. Worker single loop, one job at a time, FIFO by `created_at`; claims atomically
   (`UPDATE ... SET status='RUNNING', started_at=now() WHERE id=... AND status='PENDING'`).
3. Progress: worker updates `progress_pct`/`phase` per chunk (~30s cadence); between chunks
   it re-reads `status` and aborts cleanly if CANCELLED.
4. Terminal: DONE + `result`, or FAILED + `error` (ASCII, truncated to 4k).
5. Stale recovery: on startup the worker marks any RUNNING row FAILED
   (`error="worker restarted"`). No resume in v1.

## Worker service (`backtest_worker/` in KronosStrategies)

- Own Dockerfile + requirements (same pattern as other services); compose service in the
  kronos stack: `cpus: "0.5"`, `mem_limit: 700m`, `restart: unless-stopped`, healthcheck via
  heartbeat file like the runners.
- Imports: strategy modules, regime engine, replay/backtest code, SQLAlchemy mirror,
  OANDA fetch. **No MetaAPI client import anywhere on its path** — structurally cannot
  place orders.
- Bars cache: host-mounted volume (survives rebuilds). M1/M5/M15/H1/H4 for XAU_USD fetched
  incrementally from OANDA REST (5k-candle pages; ~36 requests per 6 months of M1).
  S5 fetched per ambiguous span only, cached alongside. v1 is **XAU_USD only**.
- Memory: the replay streams the window in chunks (e.g. 2-week slices with warmup carry);
  a chunk-boundary equivalence test proves slicing does not change results.

## Engine

Reuse `manager_sim_engine`'s gating loop — it already imports the **production**
`compute_regime` and `POLICIES` — with three changes:

1. **Current roster specs**: build STRAT_SPECS from the live modules (S93 s93_fvg_scalp,
   S94 s94_sweep_reversal, S99 s99_mss_fvg, S100 s100_m3_combo) via `replay_lib`-style
   window stepping, honoring each module's CONFIG windows and cooldowns exactly as
   `research_runner` does. The Telegram copy slot is excluded (external signals can't be
   replayed) and stated in `result.notes`. Roster comes from `params.roster_snapshot`,
   which the **mutation** captures server-side at submit time from the live manager config
   (UserStrategy rows) — the frontend never supplies it.
2. **Fill realism default**: entry fill = detection bar close +/- friction
   (spread+slippage from params), phantom guard (drop entries already beyond TP/SL at
   fill) — the `manager_sim_correction` semantics move into the engine path.
3. **S5 ambiguity resolver** (post-pass): for each closed sim trade whose exit M1 bar
   contains both TP and SL in its high-low range — or a mid-bar TIME_EXIT with TP or SL
   also in range — fetch S5 for that bar span (cached), walk S5 closes in order to decide
   which trigger fired first, adjust exit/PnL, and count flips. Report
   `n_ambiguous / n_flipped / pnl_delta_pts` in the result.

**Live deltas**: read Position rows (read-only) for the window and the roster's strategy
names via the SQLAlchemy mirror; aggregate per strategy: realized PnL (usd/pts), trade
count, win rate. Same aggregates from the sim's gated arm; report `sim / live / delta`.
Use the opt15 `market_timing` helpers for the window cut — `created_at` is stored at
-5:30 (known platform quirk).

## GraphQL API (auto-discovery conventions)

Mutations (`apis/schema/mutation/user/`):
- `run_manager_backtest.py` → `RunManagerBacktest(periodStart, periodEnd, label?,
  params?)`: validates dates (start < end, end <= today, window <= 12 months), queue depth
  (<=3 non-terminal), inserts PENDING, returns run id.
- `cancel_manager_backtest.py` → `CancelManagerBacktest(runId)`: PENDING/RUNNING →
  CANCELLED (worker honors between chunks).

Queries (`apis/schema/query/`):
- `manager_backtest_runs.py` → `ManagerBacktestRuns`: list, newest first (id, label,
  status, progressPct, phase, periodStart/End, createdAt).
- `manager_backtest_run.py` → `ManagerBacktestRun(runId)`: full detail incl. `result`.

All `@user_authenticate`.

## Frontend tab

- Sidebar entry **Manager Backtest** → `app/(main)/manager-backtest/page.tsx` +
  `_components/main.tsx`; ops in `GraphQL/managerBacktestControls.ts`.
- **New run card**: date-range picker; collapsed advanced params pre-filled from live
  manager config (spread/slippage/lots/kill-switch/max-concurrent/cadence,
  include-ungated toggle default off); Run button.
- **Runs table**: status chip, progress bar + phase while running; `setInterval` polling —
  10s while any run is PENDING/RUNNING, else 60s (the `/manager` pattern).
- **Results panel** (row click): per-arm summary cards (PnL pts/USD, trades, WR, maxDD,
  PF); per-strategy table with sim / live / delta columns; equity curve
  (lightweight-charts; gated arm + ungated if enabled); S5-resolution chip; FAILED shows
  `error`. Cancel via AlertDialog confirm. TV design tokens throughout.

## Testing (all offline)

- Engine: synthetic-frame roster replay with golden values; S5 resolver cases (TP-first
  flip, SL-first no-flip, mid-bar TIME_EXIT, no-S5-available fallback = keep M1 verdict +
  count as unresolved); chunk-boundary equivalence (one window vs same window in slices).
- Worker: lifecycle vs SQLite (claim, cancel mid-run, stale-RUNNING → FAILED on restart).
- Backend: Django tests for both mutations (validation, queue cap) + queries (SQLite).
- Frontend: `npm run lint` + `npm run build`.

## Rollout (order matters; no live-behavior change at any step)

1. Backend branch: model + migration 0009 + schema files → tests → box bind-mount copy →
   `manage.py migrate apis` (same flow as 0008).
2. Strategies branch: SQLAlchemy mirror additions (all three trees, identical) + engine +
   worker → tests → box file sync → compose merged **additively** (new service block
   only) → `docker compose -p kronos up -d --build backtest_worker`.
3. Frontend branch: tab → lint/build → push (Netlify ships it).
4. Smoke: 1-week window run end-to-end before any 6-month audit.

## Out of scope (v1)

- Symbols other than XAU_USD; the Telegram copy slot; per-trade live matching; sensitivity
  grids; resume of interrupted runs; retention/cleanup of old runs (revisit if the table
  grows); any change to live trading services.
