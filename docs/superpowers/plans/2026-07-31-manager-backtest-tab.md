# Manager Backtest Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A dashboard tab that runs a historical audit of the Strategy Manager — replay the
current roster under production regime gating over a past window (M1 baseline, S5 for
ambiguous exit bars) and report summary deltas vs live.

**Architecture:** Frontend tab → GraphQL mutations insert a `ManagerBacktestRun` job row in
the shared Postgres → a new CPU-capped `backtest_worker` compose service (strategies image,
new `audit_worker` package) claims jobs FIFO, fetches bars from OANDA REST into a box-local
parquet cache, replays via the existing `manager_sim_engine.run_sim` with roster specs built
from the run's snapshot, resolves ambiguous exits on S5, computes live deltas, and writes a
result JSON back onto the row. Spec: `docs/superpowers/specs/2026-07-31-manager-backtest-tab-design.md`.

**Tech stack:** Django 5 + graphene (auto-discovery), SQLAlchemy mirror, pandas/pyarrow,
OANDA v20 REST, Next.js 14 + Apollo + lightweight-charts.

**Spec deviation (deliberate):** the spec's "stream the window in chunks" is dropped —
measured sizes (6 months of M1 ≈ 180k rows ≈ tens of MB) fit far inside the 700m cap, so
chunking is YAGNI. Progress/cancellation instead use a `progress_cb` hook added to
`run_sim`. The chunk-boundary equivalence test is replaced by a `progress_cb` parity test.
Note also: the spec's "fill realism becomes the engine default" needs NO task — `run_sim`
already passes the detection bar's close as `fill_price` with a phantom guard (added in the
July 2026 manager-sim Task 6); Task 7's parity test locks that behavior in place.

## Global Constraints (binding for every task)

1. **Inert for live trading**: no behavior change to any running service. The only
   duplicated-tree edit in this plan is the SQLAlchemy mirror addition (Task 4), applied
   byte-identically to `strategies/shared/models.py` and `strategy_manager/shared/models.py`.
   Do NOT touch `position_manager/shared/models.py` (trimmed mirror, allowlisted divergent).
2. **Worker never imports MetaAPI**: nothing under `strategies/audit_worker/` may import
   `shared.metaapi_client` (directly or transitively). Task 9 adds an import-guard test.
3. **Tests offline**: no network, no real DB. Strategies tests under `tests/`, run targeted:
   `E:/Projects/Kronos/KronosStrategies/.venv/Scripts/python.exe -m pytest tests/<file> -q`.
   Do NOT run the full strategies suite. Backend tests: `.venv\Scripts\python.exe manage.py
   test apis` (in-memory SQLite). OANDA calls are mocked in tests (mock `requests`).
4. **Commit discipline**: `git add` only explicit paths; NEVER `-A`/`-u`/`.`. One commit per
   task, message prefixed `mbt(taskN):`.
5. **ASCII-only** console/log output.
6. **LOCAL DEVELOPMENT ONLY** — no box deployment, no scp, no live-DB access. Deployment is
   the controller's job after the final review (spec "Rollout" section).
7. Branches: KronosStrategies `feat/manager-backtest` (exists, holds the spec);
   Kronos_Backend `feat/manager-backtest-api` off `feat/strategy-manager` @ `3236fa0`;
   kronos_frontend `feat/manager-backtest-tab` off `main`. Frontend is NEVER pushed
   (Netlify auto-ships on push).
8. Exact field/JSON names from the spec are binding: statuses
   `PENDING|RUNNING|DONE|FAILED|CANCELLED`; phases
   `fetching_bars|replaying|resolving_s5|comparing`; params keys `spread_pts, slippage_pts,
   lots, kill_switch_usd, max_concurrent, regime_cadence_min, include_ungated,
   roster_snapshot`; result keys `summary, per_strategy, equity_curve, s5_resolution,
   trades_csv, notes`.
9. Backend tree has a pre-existing modified `Kronos_Backend/settings.py` — never touch or
   commit it. Known makemigrations drift (see migration 0008's header): if `makemigrations`
   emits extra operations, hand-trim to only this plan's operations, as 0008 did.

---

### Task 1: Backend model + migration 0009

**Repo:** `E:\Projects\Kronos\Kronos_Backend` — create branch `feat/manager-backtest-api`
off `feat/strategy-manager`.
**Files:** Modify `apis/models.py` (append after `ManagerAction`); create
`apis/migrations/0009_manager_backtest_run.py` (via makemigrations); test in
`apis/tests.py` (append).

**Interfaces produced:** Django model `ManagerBacktestRun` — consumed by Tasks 2-3
(ORM) and Task 4 (mirrored columns). Table name `apis_managerbacktestrun`.

- [ ] **Step 1: model** — append to `apis/models.py`:

```python
class ManagerBacktestRun(BaseModel):
    """A queued/completed Strategy Manager historical-audit backtest (spec 2026-07-31)."""
    STATUS_CHOICES = [(s, s) for s in
                      ("PENDING", "RUNNING", "DONE", "FAILED", "CANCELLED")]
    label = models.CharField(max_length=120)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="PENDING")
    progress_pct = models.FloatField(default=0.0)
    phase = models.CharField(max_length=20, default="", blank=True)
    period_start = models.DateField()
    period_end = models.DateField()
    params = models.JSONField(default=dict, blank=True)
    result = models.JSONField(null=True, blank=True)
    error = models.TextField(default="", blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    requested_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                     blank=True, related_name="backtest_runs")

    class Meta:
        indexes = [models.Index(fields=["status", "created_at"],
                                name="idx_mbrun_status_created")]
```

- [ ] **Step 2: failing test** — append to `apis/tests.py`: `test_manager_backtest_run_defaults`
creates a run with only `label/period_start/period_end`, asserts `status == "PENDING"`,
`progress_pct == 0.0`, `params == {}`, `result is None`. Run
`manage.py test apis -k manager_backtest` → fails (no model).
- [ ] **Step 3: migrate** — `python manage.py makemigrations apis` → single migration; if
drift operations appear, trim to only `CreateModel` + `AddIndex` (constraint 9). Rename file
to `0009_manager_backtest_run.py` if needed (keep the `dependencies` on `0008_...`).
- [ ] **Step 4: run test** → PASS. Run the full `manage.py test apis` → all green.
- [ ] **Step 5: commit** — only `apis/models.py` + the migration + `apis/tests.py`:
`mbt(task1): ManagerBacktestRun model + migration 0009`.

### Task 2: Backend mutations

**Files:** Create `apis/schema/mutation/user/run_manager_backtest.py` and
`apis/schema/mutation/user/cancel_manager_backtest.py`; tests appended to `apis/tests.py`.
(Auto-discovery: filename → CamelCase class, so classes MUST be named `RunManagerBacktest`
and `CancelManagerBacktest`.)

**Interfaces produced:** GraphQL `runManagerBacktest(periodStart: Date!, periodEnd: Date!,
label: String, spreadPts: Float, slippagePts: Float, lots: Float, killSwitchUsd: Float,
maxConcurrent: Int, regimeCadenceMin: Int, includeUngated: Boolean) → {ok, runId, error}`
and `cancelManagerBacktest(runId: UUID!) → {ok, error}`. Consumed by Task 11.

- [ ] **Step 1: failing tests** — `test_run_manager_backtest_creates_pending` (executes the
mutation via `schema.execute` with an authenticated context, asserts a PENDING row with
captured `roster_snapshot`), `test_run_manager_backtest_rejects_bad_window` (start ≥ end;
end > today; window > 366 days → `ok=False`), `test_run_manager_backtest_queue_cap`
(3 existing non-terminal rows → `ok=False`), `test_cancel_manager_backtest`
(PENDING → CANCELLED; DONE → `ok=False`). Run → fail.
- [ ] **Step 2: implement `RunManagerBacktest`** — validation per the tests; params dict
assembled with spec keys, numeric defaults taken from the live `ManagerConfig` row when the
argument is omitted (fallbacks: spread 0.30, slippage 0.10, lots 0.02, kill 150.0,
max_concurrent 3, cadence 5, include_ungated False); `roster_snapshot` captured
server-side as
`[{"name": ms.strategy.name, "policy_key": ms.policy_key, "policy_params": ms.policy_params}]`
from active `ManagedStrategy` rows (models.py:410 — mirror the active-row filter used by
the manager state query in `apis/schema/query/`; read that file and reuse its queryset
filter verbatim); `label` defaults to `audit_<start>_<end>`; `requested_by=info.context.user`;
`@user_authenticate`.
- [ ] **Step 3: implement `CancelManagerBacktest`** — flip PENDING/RUNNING → CANCELLED
(single `UPDATE ... WHERE status IN (...)` via
`ManagerBacktestRun.objects.filter(id=run_id, status__in=["PENDING","RUNNING"]).update(status="CANCELLED")`,
rowcount 0 → `ok=False`).
- [ ] **Step 4: tests pass**; full `manage.py test apis` green.
- [ ] **Step 5: commit** — `mbt(task2): run/cancel manager-backtest mutations`.

### Task 3: Backend queries

**Files:** Create `apis/schema/query/manager_backtest_runs.py` (class
`ManagerBacktestRuns`) and `apis/schema/query/manager_backtest_run.py` (class
`ManagerBacktestRun` — graphene ObjectType name collision with the model is fine, the
schema module imports the model under an alias); tests in `apis/tests.py`.

**Interfaces produced:** `managerBacktestRuns → [RunSummaryType]` (id, label, status,
progressPct, phase, periodStart, periodEnd, createdAt — newest first, cap 100) and
`managerBacktestRun(runId: UUID!) → RunDetailType` (summary fields + params, result, error,
startedAt, finishedAt) with `params`/`result` exposed via `graphene.types.generic.GenericScalar`.
Consumed by Tasks 11-12.

- [ ] **Step 1: failing tests** — list returns newest-first with expected fields; detail
returns `result` JSON round-tripped intact; both `@user_authenticate`. Run → fail.
- [ ] **Step 2: implement** both files following `all_backtest_report.py`'s structure.
- [ ] **Step 3: tests pass**; full `manage.py test apis` green.
- [ ] **Step 4: commit** — `mbt(task3): manager-backtest queries`.

### Task 4: SQLAlchemy mirror (KronosStrategies)

**Repo:** `E:\Projects\Kronos\KronosStrategies`, branch `feat/manager-backtest` (exists).
**Files:** Modify `strategies/shared/models.py` and `strategy_manager/shared/models.py`
(byte-identical edits); test `tests/test_mbt_mirror.py`.

**Interfaces produced:** SQLAlchemy class `ManagerBacktestRun`
(`__tablename__ = "apis_managerbacktestrun"`) with columns exactly matching Task 1:
`id (UUID pk), created_at, modified_at, label, status, progress_pct, phase, period_start,
period_end, params (JSON), result (JSON), error, started_at, finished_at,
requested_by_id (UUID, nullable)`. Follow the file's existing column-type conventions for
UUID/JSON/datetime (copy the patterns of the adjacent `BacktestReport` mirror at
`strategies/shared/models.py:513`). Consumed by Tasks 8-9.

- [ ] **Step 1: failing test** — `tests/test_mbt_mirror.py`: create the table on in-memory
SQLite from the mirror metadata, insert a row with `status="PENDING"`, read it back;
assert column set equals the Task-1 field list; assert the two edited files' added class
blocks are byte-identical (read both files, extract `class ManagerBacktestRun` block,
compare strings).
- [ ] **Step 2: implement** in both trees. Run `tests/test_mbt_mirror.py` and
`tests/test_tree_sync.py` → both green (tree-sync compares whole files — identical edits
keep it green).
- [ ] **Step 3: commit** — `mbt(task4): SQLAlchemy mirror for ManagerBacktestRun (2 trees)`.

### Task 5: OANDA bars builder + parquet cache

**Files:** Create `strategies/audit_worker/__init__.py` (empty),
`strategies/audit_worker/bars.py`; test `tests/test_audit_bars.py`.

**Interfaces produced:**
- `fetch_candles(instrument: str, granularity: str, start_utc: datetime, end_utc: datetime) -> pd.DataFrame`
  — paged OANDA v20 `/v3/instruments/{instrument}/candles` fetch (`price="M"`, `count=5000`
  pages, `from` cursor advance; columns `time (tz-aware UTC), open, high, low, close,
  volume`; complete candles only). Reuse the token/host/env conventions from
  `strategies/shared/tsdb_reader.py` (read it first; same env var names, same 30s timeout,
  same retry-adapter pattern) but implement standalone in `bars.py` — do NOT edit
  tsdb_reader (duplicated tree).
- `build_audit_frames(cache_dir: Path, start_utc: datetime, end_utc: datetime) -> dict[str, pd.DataFrame]`
  — returns the six-key frames dict (`"1m","5m","15m","1h","4h","1d"`) shaped for
  `manager_sim_engine.run_sim`: M1 fetched from `start_utc - 7 days`; 5m/15m resampled from
  M1 via `research.replay_lib.resample_ohlc`; 1h/4h/1d fetched natively with warmup depth =
  `SimConfig.slice_rows` defaults (760/560/130 bars) + 10 margin before `start_utc`.
  Persist each granularity to `<cache_dir>/XAU_USD_<gran>.parquet`, incremental: on a later
  call, only fetch the missing head/tail ranges and merge (dedup on `time`).
- `ensure_s5(cache_dir: Path, start_utc: datetime, end_utc: datetime) -> pd.DataFrame` —
  S5 mid candles for the span, served from `<cache_dir>/XAU_USD_S5.parquet` when covered,
  else fetched, appended, deduped. Returns empty DataFrame (not an exception) when OANDA
  returns no data for the span.

- [ ] **Step 1: failing tests** — with `requests` mocked (canned candle JSON pages):
paging assembles frames in order; incremental call fetches only the gap (assert the mocked
URL ranges); resampled 5m bars match a hand-computed fixture; `ensure_s5` cache-hit does no
HTTP call; empty-span returns empty df. Run → fail.
- [ ] **Step 2: implement**; tests pass.
- [ ] **Step 3: commit** — `mbt(task5): audit_worker OANDA bars builder + parquet cache`.

### Task 6: Roster spec builder

**Files:** Create `strategies/audit_worker/roster.py`; test `tests/test_audit_roster.py`.

**Interfaces produced:**
- `MODULES: dict[str, module]` — `{m.NAME: m}` over `backtest_strategies.s93_fvg_scalp,
  s94_sweep_reversal, s99_mss_fvg, s100_m3_combo` (import at module top).
- `build_specs(roster_snapshot: list[dict]) -> tuple[list[StratSpec], list[str]]` — for each
  `{"name", "policy_key", "policy_params"}` entry: unknown `name` → note
  `"skipped <name>: not replayable"` (this covers the Telegram copy slot); `policy_key` not
  in `strategy_manager.manager.POLICIES` (import the same `POLICIES` the engine imports) →
  note + fall back to `"always_on"`; else `StratSpec(name, MODULES[name], policy_key,
  policy_params or {})`. Returns (specs, notes). Consumed by Task 9.

- [ ] **Step 1: failing tests** — happy path builds 4 specs; copy-slot name produces note +
no spec; bogus policy falls back with note; empty snapshot → ([], [note]). Run → fail.
- [ ] **Step 2: implement**; tests pass.
- [ ] **Step 3: commit** — `mbt(task6): roster spec builder`.

### Task 7: Engine progress hook + S5 ambiguity resolver

**Files:** Modify `strategies/backtest/manager_sim_engine.py` (`run_sim` signature only);
create `strategies/audit_worker/s5_resolve.py`; tests `tests/test_mbt_engine_hook.py`,
`tests/test_s5_resolve.py`.

**Interfaces produced:**
- `run_sim(frames, cfg, specs=None, progress_cb=None)` — `progress_cb: Callable[[float], None] | None`,
  invoked every 1000 processed M1 bars and once at loop end with `done_bars/total_bars`.
  Exceptions raised by the callback propagate out of `run_sim` (this is the cancellation
  path). `progress_cb=None` → byte-identical behavior to today.
- `resolve_ambiguous(trades: list[TradeRecord], m1: pd.DataFrame, s5_provider) ->
  tuple[list[TradeRecord], dict]` in `s5_resolve.py` — `s5_provider(start_utc, end_utc) ->
  pd.DataFrame` (Task 5's `ensure_s5` partial-applied). A trade is ambiguous when its exit
  M1 bar (row where `time == exit_time.floor("min")`) satisfies, for BUY:
  `bar.low <= trade.sl and bar.high >= trade.tp`; for SELL: `bar.high >= trade.sl and
  bar.low <= trade.tp`; or `outcome == "TIME"` and either level lies inside `[low, high]`.
  For each ambiguous trade fetch S5 for `[exit_time.floor("min"), +60s)` and walk S5 rows
  chronologically — first bar whose high/low touches TP or SL decides; if the M1 verdict
  flips, rewrite `exit_px` (=tp or sl), `outcome`, and recompute `pnl_pts`/`pnl_usd` with
  the SAME formula `step_position` uses (read it; apply identical exit-friction handling).
  Empty S5 → keep M1 verdict, count as unresolved. Returns
  `(trades_out, {"n_ambiguous": int, "n_flipped": int, "n_unresolved": int,
  "pnl_delta_pts": float})`. `OPEN`/`TRAIL` outcomes are never touched.

- [ ] **Step 1: failing hook test** — synthetic 2-day frames (reuse the fixture style of
`tests/test_htf_bias_study.py`); `run_sim(..., progress_cb=collector)` collects a
monotonically nondecreasing series ending at 1.0, and trade output is identical to
`progress_cb=None` (golden parity). A callback that raises `RuntimeError` after the first
call propagates. Run → fail (no param).
- [ ] **Step 2: implement the param** (thread a bar counter through the existing M1 loop);
hook test passes.
- [ ] **Step 3: failing resolver tests** — hand-built TradeRecords + M1 bar + canned S5:
BUY where S5 shows SL touched first while M1 said TP → flips, pnl recomputed; SELL
unambiguous bar → untouched; TIME mid-bar with TP inside range and S5 showing TP touch
before the minute closes → flips to TP; empty S5 → unresolved counted, trade unchanged.
- [ ] **Step 4: implement resolver**; tests pass. Also run `tests/test_htf_bias_study.py`
(neighbors the engine) → green.
- [ ] **Step 5: commit** — `mbt(task7): run_sim progress hook + S5 ambiguity resolver`.

### Task 8: Live deltas

**Files:** Create `strategies/audit_worker/live_deltas.py`; test
`tests/test_live_deltas.py`.

**Interfaces produced:**
- `live_summary(session, strategy_names: list[str], start_utc, end_utc) -> dict[str, dict]`
  — read-only SQLAlchemy aggregation over `Position` joined to its strategy name (follow
  the join path used by `strategies/db/` audit scripts; read one for the canonical
  join), realized positions only (`quantity == 0`), entry-window filter on `created_at`
  **shifted by the platform's known -5:30 storage skew** (add `timedelta(hours=5, minutes=30)`
  to the UTC window bounds; cite the July-2026 drawdown memory in a comment). Returns
  `{name: {"pnl_usd": float, "trades": int, "win_rate": float}}`.
- `deltas(sim: dict[str, dict], live: dict[str, dict]) -> dict[str, dict]` — per strategy:
  `{"sim": {...}, "live": {...} | None, "delta": {"pnl_usd": sim-live, "trades": ...,
  "win_rate": ...} | None}`. Consumed by Task 9.

- [ ] **Step 1: failing tests** — in-memory SQLite seeded with Strategy/UserStrategy/
Position rows (two strategies, wins+losses, one open position excluded); asserts
aggregates, the +5:30 window shift (a row at window-edge proves it), and `deltas` math
including a live-missing strategy → `live: None`. Run → fail.
- [ ] **Step 2: implement**; tests pass.
- [ ] **Step 3: commit** — `mbt(task8): live summary + deltas`.

### Task 9: Worker loop + result assembly

**Files:** Create `strategies/audit_worker/worker.py`,
`strategies/audit_worker/results.py`; tests `tests/test_mbt_worker.py`,
`tests/test_mbt_results.py`.

**Interfaces produced:**
- `results.assemble(gated: SimResult, ungated: SimResult | None, cfg: SimConfig,
  s5_report: dict, delta_map: dict, notes: list[str], trades_csv: str) -> dict` — the spec's
  `result` JSON: `summary` per arm from the trade lists (pnl_pts, pnl_usd via
  `cfg.pts_to_usd`, trades, win_rate, max_dd_pts from the running cum-pnl trough,
  profit_factor), `per_strategy` = `deltas()` output, `equity_curve` per arm =
  `[[iso_ts, cum_pts], ...]` by exit_time, stride-downsampled to <= 2000 points,
  `s5_resolution`, `trades_csv`, `notes`.
- `worker.main()` — loop: touch `/tmp/hb`; claim oldest PENDING
  (`UPDATE apis_managerbacktestrun SET status='RUNNING', started_at=now() WHERE id =
  (SELECT id FROM apis_managerbacktestrun WHERE status='PENDING' ORDER BY created_at
  LIMIT 1) AND status='PENDING' RETURNING id` — via SQLAlchemy text(); rowcount 0 → sleep
  10s, loop). On startup: mark any RUNNING row FAILED (`error="worker restarted"`).
  Per job: phase writes + `progress_pct` mapping — `fetching_bars` 0-10, `replaying` 10-80
  (gated 10-60 + ungated 60-80 when `include_ungated`, else 10-80), `resolving_s5` 80-90,
  `comparing` 90-100; progress writes throttled to >= 5s apart; the `progress_cb` also
  re-reads `status` each write and raises `RunCancelled(Exception)` when CANCELLED (caught
  → row already CANCELLED, just clean up). Trades CSV written to
  `<AUDIT_CACHE_DIR>/trades_<run_id>.csv` (all TradeRecord fields). Failure → FAILED +
  `error` = ASCII `repr(exc)[:4000]`. Env: DB URL exactly as other services build it
  (mirror `shared.models`' engine funcs), `AUDIT_CACHE_DIR` default `/app/audit_cache`.
- Import guard: `audit_worker` modules must not import `shared.metaapi_client`.

- [ ] **Step 1: failing lifecycle tests** — SQLite + mirror table: claim flips exactly one
PENDING (two PENDING → oldest); startup marks stale RUNNING → FAILED; a monkeypatched job
runner that raises → FAILED with ASCII error; CANCELLED mid-run via a progress_cb tick →
row stays CANCELLED, worker loops on. `test_no_metaapi_import`: import every
`audit_worker.*` module, assert `"shared.metaapi_client" not in sys.modules`.
- [ ] **Step 2: failing results tests** — two hand-built TradeRecord lists → exact summary
numbers (include a max-DD trough case), equity downsampling (2001st point dropped, ends on
final cum value), per_strategy passthrough, notes propagation.
- [ ] **Step 3: implement `results.py` then `worker.py`** (job body wires Tasks 5-8:
`build_audit_frames` → `build_specs` → `run_sim` gated (+ungated) → `resolve_ambiguous`
(gated arm only) → `live_summary`/`deltas` → `assemble` → DONE). Tests pass.
- [ ] **Step 4: run the neighboring suites** `tests/test_mbt_mirror.py tests/test_audit_bars.py
tests/test_audit_roster.py tests/test_s5_resolve.py tests/test_live_deltas.py` → green.
- [ ] **Step 5: commit** — `mbt(task9): backtest worker loop + result assembly`.

### Task 10: Compose service (additive)

**Files:** Modify `compose.yml` (append one service block; touch nothing else).

- [ ] **Step 1: append** (copy the env/anchor style of the `s100_m3_combo` block — same
image/build context `strategies/`, same DB env wiring):

```yaml
  backtest_worker:
    build: ./strategies
    command: python -m audit_worker.worker
    restart: unless-stopped
    cpus: "0.5"
    mem_limit: 700m
    volumes:
      - ./audit_cache:/app/audit_cache
    environment:
      # same DB/OANDA env passthrough keys as s100_m3_combo (copy them verbatim)
      AUDIT_CACHE_DIR: /app/audit_cache
    healthcheck:
      # copy the runners' heartbeat-file healthcheck block verbatim
```

- [ ] **Step 2: validate** — `docker compose config -q` (if docker unavailable locally,
`python -c "import yaml; yaml.safe_load(open('compose.yml'))"`).
- [ ] **Step 3: commit** — only `compose.yml`: `mbt(task10): backtest_worker service (additive)`.

### Task 11: Frontend — controls, tab, runs table, new-run card

**Repo:** `E:\Projects\Kronos\kronos_frontend` — branch `feat/manager-backtest-tab` off
`main`. DO NOT PUSH.
**Files:** Create `GraphQL/managerBacktestControls.ts`,
`app/(main)/manager-backtest/page.tsx`, `app/(main)/manager-backtest/_components/main.tsx`,
`app/(main)/manager-backtest/_components/NewRunCard.tsx`,
`app/(main)/manager-backtest/_components/RunsTable.tsx`; modify
`app/(main)/_components/constants.tsx` (sidebar entry "Manager Backtest", path
`/manager-backtest`, pick a lucide icon consistent with neighbors, e.g. `History`).

**Interfaces:** consumes Task 2/3 GraphQL ops — define
`RUN_MANAGER_BACKTEST, CANCEL_MANAGER_BACKTEST, MANAGER_BACKTEST_RUNS,
MANAGER_BACKTEST_RUN` gql documents in `managerBacktestControls.ts` matching the Task 2/3
schemas exactly. Produces `selectedRunId` state consumed by Task 12's results panel
(rendered inside `main.tsx`).

- [ ] **Step 1: controls file** with the four documents.
- [ ] **Step 2: page skeleton** — `page.tsx` mirrors `/manager/page.tsx`; `main.tsx` holds
state (`runs`, `selectedRunId`, `loading`) and the polling `useEffect` (10s while any run
PENDING/RUNNING else 60s — copy the `/manager` `setInterval` pattern, clean up on unmount).
- [ ] **Step 3: `RunsTable`** — columns label/period/status chip/progress bar+phase (running
rows)/created; row click sets `selectedRunId`; cancel button on PENDING/RUNNING rows with
`AlertDialog` confirm (copy `/manager` master-switch dialog usage). TV design tokens.
- [ ] **Step 4: `NewRunCard`** — two date inputs, collapsed "Advanced" section (spread,
slippage, lots, kill-switch, max-concurrent, cadence, include-ungated toggle) with
placeholder text showing server defaults, Run button → mutation → refetch; mutation `error`
rendered inline.
- [ ] **Step 5: verify** — `npm run lint` and `npm run build` pass.
- [ ] **Step 6: commit** — `mbt(task11): manager-backtest tab, runs table, new-run card`.

### Task 12: Frontend — results panel

**Files:** Create `app/(main)/manager-backtest/_components/ResultsPanel.tsx`,
`app/(main)/manager-backtest/_components/EquityChart.tsx`; modify
`app/(main)/manager-backtest/_components/main.tsx` (render panel when `selectedRunId`).

- [ ] **Step 1: `ResultsPanel`** — fetch `MANAGER_BACKTEST_RUN(selectedRunId)`;
DONE → per-arm summary cards (PnL pts + USD, trades, WR%, maxDD pts, PF), per-strategy
table with sim/live/Δ columns (live `None` renders "—"), S5 chip
(`"{n_ambiguous} ambiguous, {n_flipped} flipped, {pnl_delta_pts} pts"`), notes list;
FAILED → error block; RUNNING/PENDING → progress state. Re-poll detail every 10s while the
run is non-terminal.
- [ ] **Step 2: `EquityChart`** — lightweight-charts line series per arm from
`result.equity_curve` (copy the chart bootstrap from `app/(main)/chart/_components/main.tsx`),
gated always, ungated when present; TV tokens.
- [ ] **Step 3: verify** — `npm run lint`, `npm run build`.
- [ ] **Step 4: commit** — `mbt(task12): results panel + equity chart`.

---

## Execution notes for the controller

- Tasks 1-3 (backend) are sequential; Tasks 4-10 (strategies) sequential after 1; Tasks
  11-12 after 3. Single implementer at a time per SDD.
- Final whole-branch review covers all three branches (three review packages).
- Deployment (controller, post-review, per spec Rollout): backend migrate → strategies box
  sync + compose merge + `up -d --build backtest_worker` → smoke 1-week run → frontend push.
