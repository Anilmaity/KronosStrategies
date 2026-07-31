# Optimization-15 Implementation Plan (2026-07-30)

Executes the 15-point research report `E:\Projects\Kronos\shared\OPTIMIZATION_15_POINTS_2026-07-30.md`.
Branch: `feat/optimization-15` (KronosStrategies), `feat/db-indexes` (Kronos_Backend, Task 16 only).
LOCAL DEVELOPMENT ONLY — no box deployment, no scp, no live DB/broker access. All tests offline.

## Global Constraints (binding for every task)

1. **Safety-first defaults**: every NEW trading-behavior gate ships default-OFF behind an env
   flag. Pure hardening (retries, cache invalidation, query batching, pooling) must preserve
   observable trading behavior exactly.
2. **Never blind-retry a MetaAPI market order.** A timeout does not mean the order failed.
   Any retry of `place_market_order` must first verify via a client-id lookup that the
   original did not land. If verification is impossible, do not retry — log and reject.
3. **Tests are offline**: no network, no real DB. Mock `requests` / use in-memory SQLite for
   SQLAlchemy paths. New tests go under `tests/` (pytest.ini scopes collection there).
   Run targeted: `E:/Projects/Kronos/KronosStrategies/.venv/Scripts/python.exe -m pytest tests/<file> -q`.
   Do NOT run the full suite (it is slow and some legacy tests hit the network).
4. **Commit discipline**: the working tree contains unrelated uncommitted files. `git add`
   ONLY the explicit paths you created/modified. NEVER `git add -A`, `-u`, or `.`.
   One commit per task (or a few logical commits), message prefixed `opt15(taskN):`.
5. **ASCII-only** console/log output (Windows cp1252 chokes on Unicode).
6. **Parity proof for refactors**: any behavior-preserving refactor of signal logic must
   include a test that runs old-vs-new (or golden values) on synthetic data proving
   identical outputs.
7. **Duplicated trees stay in sync**: `strategies/shared/`, `position_manager/shared/`,
   `strategy_manager/shared/` (and `strategy_manager/regime` vs `strategies/regime`,
   `strategy_manager/strategy` vs `strategies/strategy`) are copies consumed by separate
   Docker build contexts. When you edit a file that exists in more than one tree, apply the
   identical edit to every copy.
8. **In-container imports are `shared.*`** (WORKDIR /app) — preserve existing import style;
   do not introduce `strategies.shared.*` imports in service code.
9. Do not touch any `.env`, secrets, or compose service blocks outside your task's scope.
10. Interpreter: `E:/Projects/Kronos/KronosStrategies/.venv/Scripts/python.exe`.

---

## Task 1: tsdb_reader hardening — bar-boundary cache, retry adapter, bid/ask fetch

**Report points #1, #4, #14 (data half). Files:** `strategies/shared/tsdb_reader.py` (and
identical copies `position_manager/shared/tsdb_reader.py`, `strategy_manager/shared/tsdb_reader.py`),
new `tests/test_tsdb_reader_hardening.py`.

Requirements:
1. **Bar-boundary cache invalidation**: the candle cache (`_CANDLE_TTL = 20`) currently
   serves 1m frames up to 20s stale. Change cache validity so that for granularity `M1` the
   cached entry is also invalidated when a new UTC minute boundary has passed since the
   entry was stored (i.e. `int(now/60) > int(cached_at/60)`). For `S5`, invalidate on 5s
   boundaries. Other granularities keep plain TTL. Make TTL env-tunable:
   `OANDA_CANDLE_TTL_SEC` (default 20, existing behavior).
2. **Retry adapter**: mount `urllib3.util.retry.Retry(total=3, backoff_factor=0.5,
   status_forcelist={429,500,502,503,504}, allowed_methods={"GET"})` on the module
   `requests.Session` — mirror the pattern already used in
   `tick_data_collector/oanda_tick_lib/_client.py:130-136`. GET-only; keep the existing
   30s timeout and the stale-cache-on-error fallback.
3. **Bid/ask**: add `fetch_latest_bidask(symbol) -> tuple[float, float] | None` using the
   OANDA candles endpoint with `price="BA"` at S5 granularity (mirror `fetch_latest_ltp`,
   returning latest bid close and ask close). Add a thin
   `fetch_latest_spread(symbol) -> float | None` returning `ask - bid`. No caching beyond
   what `fetch_latest_ltp` does today (uncached), but structure so a TTL can be added later.
4. Keep every existing public signature unchanged (`fetch_candles`, `fetch_latest_ltp`).
5. Sync all three tree copies byte-identically.

Tests (mock `requests.Session.get`): cache serves within TTL; M1 cache invalidated across a
minute boundary even inside TTL; retry adapter mounted (assert adapter config on session);
`fetch_latest_bidask` parses a mocked OANDA BA response; spread = ask-bid; error path
returns None without raising.

## Task 2: metaapi_client safe retries with verify-before-retry

**Report point #4. Files:** `strategies/shared/metaapi_client.py` (+ identical copy check:
`position_manager/shared/metaapi_client.py` if it exists — sync), new
`tests/test_metaapi_retry.py`.

Requirements:
1. `place_market_order` currently makes one attempt; a transient 5xx/timeout permanently
   drops the signal. Add bounded retry (max 2 retries, 2s then 5s backoff) with this
   protocol: generate a unique `clientId` (e.g. `f"kr-{uuid4().hex[:16]}"`) sent with the
   order payload on the FIRST attempt. On timeout/5xx, before any retry, call the
   positions/orders endpoint and check whether a position/order with that `clientId`
   exists; if found, treat as success (return its data); if lookup itself fails, DO NOT
   retry — return the error as today. Only re-POST when the lookup positively confirms the
   order is absent. 4xx responses are never retried.
2. `close_position_by_id`: add the same bounded retry on 5xx/timeout (closes are idempotent
   against a position id — a repeated close of an already-closed position must be treated
   as success, matching how the monitor's `_CLOSE_MAX_ATTEMPTS` loop reasons).
3. All retry parameters env-tunable: `META_ORDER_MAX_RETRIES` (default 2),
   `META_ORDER_RETRY_BASE_SEC` (default 2). Setting retries to 0 restores exact current
   behavior.
4. Log each retry decision at INFO with the clientId (ASCII).

Tests (mock `requests`): success first try (no lookup); timeout then lookup finds order →
success without re-POST; timeout then lookup confirms absent → re-POST happens; lookup
fails → no retry, error returned; 400 → no retry; retries=0 env → single attempt.

## Task 3: position_monitor DB efficiency

**Report point #3. Files:** `position_manager/position_monitor.py`, new
`tests/test_position_monitor_db.py`.

Requirements:
1. **Kill the N+1**: fetch all PENDING triggers for all open positions in ONE query
   (`Trigger.position_id.in_(ids)`), group in Python.
2. **Throttle write amplification**: update `pos.ltp`/`pos.profit_loss` only when the
   price actually changed since the last written value OR at most once per
   `MONITOR_PNL_WRITE_SEC` (env, default 5) — not every position every second. Trigger
   fires must still evaluate against the live price every tick.
3. **CurrencyPair LTP update**: throttle the per-second unconditional SELECT+UPDATE to the
   same `MONITOR_PNL_WRITE_SEC` cadence.
4. **One session per tick**: a single `Session()` per `_check_triggers` tick handling all
   reads/writes with one commit (plus the separate broker-close path unchanged).
5. Trigger-decision logic itself (`trigger_logic.py`) must NOT change.

Tests: use in-memory SQLite with the SQLAlchemy models (pattern: create tables from
`Base.metadata`), seed 3 positions with PENDING triggers, monkeypatch the LTP source;
assert one trigger-fetch query total (count via event listener or session spy), assert
P&L writes throttled (no UPDATE when price unchanged), assert a TARGET trigger still
fires and realizes P&L exactly as before (behavior parity).

## Task 4: entry_manager session consolidation + drift gate fail-closed option

**Report points #3, #4. Files:** `strategies/strategy/entry_manager.py` (+ sync
`strategy_manager/strategy/entry_manager.py` if present), new
`tests/test_entry_manager_sessions.py`.

Requirements:
1. Consolidate `place_entry`'s ~10 sequential `Session()` opens into ONE session passed
   through the gate helpers (context/gates/persist), one commit at the end of persist;
   `_log_signal_fired`/`_update_signal_status` may keep their own short sessions so the
   audit row survives a later rollback. No gate ordering or semantics change.
2. `_entry_drift_exceeded` currently fails OPEN when no price is available. Add env
   `ENTRY_DRIFT_FAIL_MODE` = `open` (default, current behavior) | `closed`. In `closed`
   mode, missing price rejects with `rejection_reason="entry_drift_noprice"`.
3. Python-side age filtering in `_duplicate_open_same_side` moves into the SQL query
   (`created_at >=` cutoff) — note `Order/Position.created_at` is stored at -5:30 (naive
   IST); compute the cutoff the same way the current Python filter does, do not "fix"
   the timezone semantics.

Tests: in-memory SQLite; spy Session creation count during a gated `place_entry` dry path
(mock MetaAPI + LTP): assert <= 4 sessions total; drift fail-mode closed rejects on
missing price and open admits (parity); duplicate-gate SQL filter returns identical
results to the old Python filter on seeded boundary rows (one inside, one outside cutoff).

## Task 5: entry_manager market gates — correlation budget, event-gate news, spread gate

**Report points #9, #13, #14. Files:** `strategies/strategy/entry_manager.py`, new
`tests/test_entry_market_gates.py`. Depends on Task 1 (`fetch_latest_spread`).

Three new gates, inserted in the existing gate chain (after news blackout, before broker
call), each recording its distinct `rejection_reason` in StrategySignal, each env-gated
and DEFAULT OFF:

1. **Correlation budget** (`CORR_GUARD=off|on`, `CORR_GUARD_POINTS` default 2.0): reject a
   NEW entry when any OPEN position from a DIFFERENT UserStrategy on the same account is
   same-side AND its entry price is within `CORR_GUARD_POINTS` of the new entry price.
   (The existing `_duplicate_open_same_side` only checks the same strategy —
   this extends across strategies.) rejection_reason=`correlated`.
2. **Event-gate news window** (`NEWS_EVENT_GATE=off|on`): consult
   `shared.event_gate.event_window_open(now_utc)` (already exists, fail-open, hardcoded
   2025-2026 macro calendar). When on and window open → rejection_reason=`news_event`.
   The static `NEWS_BLACKOUT_UTC` window stays as-is (both can be active).
3. **Spread gate** (`SPREAD_GATE=off|on`, `SPREAD_GATE_MAX_FRAC` default 0.25): fetch
   `fetch_latest_spread(symbol)`; reject when `spread > SPREAD_GATE_MAX_FRAC *
   |entry - sl|`. Missing spread data → fail-open with one WARN log (never blocks trading
   on a data hiccup). rejection_reason=`spread`.

Also: record the observed spread (when fetched) into the StrategySignal audit text/details
so future friction analysis has real spread-at-entry data.

Tests: each gate off by default (no behavior change with unset env — assert the gate
helper is not consulted or passes through); each gate on: one rejecting case + one passing
case; spread missing → passes with warning; correlation ignores different-side and
far-away positions; event gate consults the real `event_window_open` with a frozen
timestamp inside a known calendar window (pick one date from `event_gate.py`'s table).

## Task 6: connection-pool right-sizing + duplicated-tree sync guard

**Report points #3, #8. Files:** every `shared/models.py` copy (strategies/,
position_manager/, strategy_manager/), new `tests/test_tree_sync.py`.

Requirements:
1. Engine config: `pool_size=int(os.getenv("DB_POOL_SIZE", "5"))`,
   `max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "5"))`, `pool_pre_ping=True`,
   `pool_recycle=1800`. (Current: 60/10, no pre-ping — ~560 potential connections across
   8 processes vs one managed Postgres.)
2. `tests/test_tree_sync.py`: assert byte-equality of every file that exists in more than
   one of the duplicated trees (`shared/models.py`, `shared/tsdb_reader.py`,
   `shared/metaapi_client.py`, `shared/market_timing.py`, `shared/event_gate.py` if
   copied, `regime/regime_engine.py`, `strategy/entry_manager.py` where copies exist).
   Discover pairs dynamically: for each filename under `strategies/shared/`,
   `strategies/regime/`, `strategies/strategy/`, if the same relative file exists under
   `position_manager/` or `strategy_manager/`, compare. Known allowed divergence: the
   `strategies/strategy/ict_engine.py` copy adds `EntrySignal.from_signal` — either sync
   the copies for real (preferred: add the classmethod to the manager copy) or expose an
   explicit allowlist with a comment explaining why.

Tests: the sync test itself passing IS the deliverable proof; plus one test asserting the
engine kwargs come from env with the new defaults.

## Task 7: compose hardening + runner MIN_BARS assert

**Report points #2, #4, #8. Files:** `compose.yml`, `strategies/research_runner.py`, new
`tests/test_runner_min_bars.py`. (`tests/test_compose_window_coverage.py` already guards
window arithmetic — extend, don't duplicate.)

Requirements:
1. **compose.yml (LOCAL ONLY — additive, keep every existing service block and comment):**
   (a) add `mem_limit` to each service: 384m for strategy runners/position_manager/
   strategy_manager/fill_reconciler, 256m for tick collectors, matching the 2GB box
   budget; (b) add a basic `healthcheck` to long-running python services (process-alive
   style: `CMD-SHELL python -c "import os,sys;sys.exit(0)"` is useless — instead use a
   heartbeat-file pattern: each service touches `/tmp/hb` every loop (add the touch in
   `research_runner.py` and note in comments which services still need it), healthcheck
   `test: ["CMD-SHELL", "find /tmp/hb -mmin -5 | grep -q hb"]`, interval 60s; only wire
   the healthcheck for services whose loop you actually instrument in this task:
   research-runner-based services); (c) add `profiles: ["data-archive"]` to the three
   `tick_data_collector` services with a tombstone comment (`# opt15: no live-path reader
   of ltp — collectors moved behind the data-archive profile; start explicitly with
   --profile data-archive if the archive is wanted`) so `docker compose up -d` no longer
   starts them by default.
2. **research_runner.py**: (a) touch `/tmp/hb` each loop iteration (cheap, guarded
   try/except for non-Linux); (b) MIN_BARS assert — at startup, if the loaded strategy
   module exposes `MIN_BARS_1M`/`MIN_BARS_5M`/`MIN_BARS_15M` (ints), compare against the
   configured `WIN_1M`/`WIN_5M`/`WIN_15M`; if any window is smaller, log a clear FATAL
   line and `sys.exit(2)` (an undersized window means silent no-trade — the CHALLENGE_XAU
   defect class). (c) Declare the constants in the four live modules:
   s93 `MIN_BARS_5M=18`, s94 `MIN_BARS_5M=298`, s99 `MIN_BARS_5M=58`,
   s100 `MIN_BARS_1M=642` (derive exact values from each module's existing `_MIN_*`
   constants — import/reuse them, do not hardcode twice).

Tests: runner assert exits for undersized window (monkeypatch env + module), passes for
adequate; compose parse test: yaml loads, every non-profile service has mem_limit, tick
collectors carry the data-archive profile.

## Task 8: shared TA consolidation (behavior-preserving)

**Report point #8. Files:** new `strategies/backtest_strategies/_shared_ta.py`,
refactor `s93_fvg_scalp.py`, `s99_mss_fvg.py`, `s100_m3_combo.py` (and s94's touch
helper), new `tests/test_shared_ta_parity.py`.

Requirements:
1. Extract into `_shared_ta.py`: (a) `atr_last(h, l, c, n)` — the byte-identical `_atr`
   from s93/s99/s100; (b) `fvg_at(h, l, k)` returning ("bull"|"bear"|None, gap, prox,
   dist) — the triplicated 3-bar FVG geometry; (c) `ensure_utc_index(df)` — the repeated
   tz-localize idiom; (d) a `PendingRetrace` dataclass + `check_touch(...)` implementing
   the shared pending-retrace/phantom-guard state machine (parameterized by prox/sl/side/
   expiry) matching the s93/s99/s100 single-pending semantics exactly.
2. Refactor s93/s99/s100 to consume these helpers. s94 keeps its list-based multi-pending
   machine but adopts `ensure_utc_index`. NO parameter or behavior change — this is
   pure consolidation.
3. Fix (behavior-neutral) housekeeping while touching the files: s99's stale hours comment
   ("1..15 UTC" vs actual 06-15); standardize `round(x, 2)` on ALL price levels stored in
   pendings/Signals across the three modules ONLY IF a parity test proves no signal
   changes on the synthetic fixtures — otherwise leave rounding as-is and note it.
4. **Parity test is the gate**: build synthetic 1m/5m OHLC fixtures that exercise: bull FVG
   formed then retraced (fires), FVG formed then SL-pierced first (phantom-cancel), FVG
   below ATR threshold (no signal), session-hour exclusion, cooldown. Capture golden
   Signal tuples (side, entry, sl, tp, reason) from the PRE-refactor code (import from a
   git stash copy or vendor the old function into the test as `_legacy_get_signal`), then
   assert the refactored module reproduces them exactly for s93, s99, s100.

## Task 9: S93 SOFT structure veto + 1.5xATR gap cap (validated spec)

**Report point #6. Files:** `strategies/backtest_strategies/s93_fvg_scalp.py`, new
`tests/test_s93_soft_veto.py`. Depends on Task 8. Reference implementation:
`E:\Projects\Kronos\ClaudeTradingRD\m3_scalper\s93_struct_validate.py` (the harness that
validated this spec — read it and match its exact structure computation) and
`E:\Projects\Kronos\ClaudeTradingRD\fvg_ict.py`.

The validated spec (2026-07-23, test n=388 PF 1.31 vs base 1.26, stress-0.80pt PF 1.20):
1. **SOFT M15 swing-structure veto**: compute M15 swing structure from `w15m` (currently
   ignored by s93): swing = extreme of +/-3 bars confirmed 3 bars later; consider the last
   two confirmed swing highs AND last two confirmed swing lows within a 60-bar window;
   HH&HL => +1 (bullish), LH&LL => -1 (bearish), else 0 (ranging). Map to the M5 decision
   bar by closed-bar time. Veto an FVG entry ONLY when structure opposes the trade side
   (structure=-1 vetoes bull FVGs, +1 vetoes bear FVGs; 0 never vetoes).
2. **Gap cap**: reject FVGs with `gap > 1.5 * ATR` (the 52pt news gap was ~17x ATR).
3. Both default ON (this is the validated shipping config) but env-escapable:
   `S93_SOFT_VETO=on|off`, `S93_GAP_CAP_ATR` (default 1.5, `0` disables).
4. Match the harness's swing/structure arithmetic EXACTLY — parameter values live in
   `s93_struct_validate.py`; cross-check lookback/confirm/window constants there before
   coding. Record in the module docstring: validation evidence + date.

Tests: synthetic M15 fixtures producing known structure sequences (+1, -1, 0); veto
blocks counter-structure bull/bear cases; ranging (0) does not veto; gap cap rejects an
oversized FVG that previously fired; `S93_SOFT_VETO=off` reproduces Task 8's golden
signals exactly (regression guard).

## Task 10: S94 detection optimization + level-universe parity

**Report point #10. Files:** `strategies/backtest_strategies/s94_sweep_reversal.py`, new
`tests/test_s94_parity.py`.

Requirements:
1. `_detect` rebuilds its O(n x levels) python state machine from `born=0` over the whole
   window on every new closed M5 bar. Make it incremental: persist the level/sweep state
   between calls (module state alongside `_pending`, reset via existing `reset_state()`),
   processing only bars newer than the last processed timestamp. Full-rebuild fallback
   when the window's first timestamp changes (window slid past state origin) or on any
   inconsistency.
2. `_LEVEL_TTL` becomes env-tunable `S94_LEVEL_TTL_BARS` (default 1440, unchanged) and
   `MIN_BARS_5M` (Task 7) documents the real requirement; add a WARN log when the supplied
   window is shorter than `_LEVEL_TTL` (level universe truncated vs backtest — the
   module's own comment concedes this divergence).
3. **Parity is the gate**: fixture window (synthetic ~400 M5 bars with a prior-day level, a
   session level, and swing fractals; at least one sweep+confirm sequence). Assert
   incremental path emits identical pending entries (level, stop, tp, expiry) as a
   forced full rebuild across 3 successive window slides.

## Task 11: regime loop efficiency

**Report point #15. Files:** `strategy_manager/regime/regime_engine.py` AND
`strategies/regime/regime_engine.py` (keep byte-identical), `strategy_manager/manager.py`,
new `tests/test_regime_efficiency.py`.

Requirements:
1. Drop `5m` and `1m` from `FRAME_SPEC` and remove `m5_structure`/`m1_structure` from
   `details` (display-only, consumed by nothing — confirmed). Keep the `details` keys
   present with value `""` for one release so any external reader degrades gracefully.
2. Compute `_swing_points(D1)` and `_swing_points(H4)` once per tick and reuse for both
   bias and `details.swings_*` (each is currently computed twice).
3. Memoize `compute_regime` on the last-closed-bar timestamps of (D1, H4, H1, M15) plus
   UTC hour — the exact pattern already implemented in
   `strategies/backtest/manager_sim_engine.py:451-479`; on memo hit return the previous
   snapshot with only `market_closed`/`session` refreshed from the clock. (D1 structure is
   currently recomputed 1440x/day for ~1 new bar.)
4. Manager-side candle TTL: pass/emit `OANDA_CANDLE_TTL_SEC=90` in the strategy_manager
   compose env (local compose.yml, additive) so its 60s loop actually hits cache.
5. `market_closed` and session logic unchanged; snapshot persistence schema unchanged.

Tests: fetch-spec no longer contains 5m/1m; swing function called at most once per frame
per tick (spy/counter); memo hit returns without recompute when frames unchanged (counter
static) and recomputes when an H1 bar timestamp advances; details keys still present.

## Task 12: metrics + alerting

**Report point #5. Files:** new `strategies/shared/obs.py` (+ copies where consumed:
position_manager/shared/, strategy_manager/shared/), wire-in edits to
`strategies/strategy/entry_manager.py`, `position_manager/position_monitor.py`,
`strategies/shared/tsdb_reader.py`; new `tests/test_obs.py`.

Requirements:
1. `obs.py`: dependency-free in-process metrics — `count(name)`, `observe(name, value)`
   (keeps count/sum/min/max), `timer(name)` context manager, and a `flush_line()` that
   returns one ASCII `METRICS {json}` line; each service logs it every
   `OBS_FLUSH_SEC` (default 300). No external services, no new dependencies.
2. Wire minimal probes: entry_manager — signal->order placement duration, per-trade fill
   drift (broker fill vs signal price: value already computed around the existing "booked
   at broker fill" log — record it as `observe("fill_drift_pts", ...)`), gate rejection
   counts by reason; position_monitor — tick duration, trigger fires by type, broker-close
   attempts/failures; tsdb_reader — OANDA call duration + error count.
3. `alert(msg, level)` in obs.py: logs `ALERT {level} {msg}`, and when env
   `ALERT_TELEGRAM_BOT_TOKEN` + `ALERT_TELEGRAM_CHAT_ID` are set, POSTs to the Telegram
   sendMessage API (2s timeout, swallow errors — alerting must never break trading).
   Wire alert() at: the RECONCILE-MANUALLY flatten in position_monitor, kill-switch trip
   (manager) if reachable without cross-tree import gymnastics — otherwise leave a TODO,
   and repeated OANDA failure (>=5 consecutive) in tsdb_reader.
4. Default state (no env set): identical behavior except the periodic METRICS log line.

Tests: counters/observations aggregate correctly; flush emits valid JSON (ASCII); alert
without env only logs; alert with env POSTs (mock requests) and swallows a mocked
exception; entry/monitor wiring smoke-tested via direct function calls.

## Task 13: counter-HTF-bias filter — offline validation, then opt-in gate

**Report point #12. Files:** new `strategies/research/htf_bias_study.py`, report
`docs/research/2026-07-30-htf-bias-filter.md`, conditional edits to
`strategies/backtest_strategies/s94_sweep_reversal.py` + `s99_mss_fvg.py`, tests.
Data: `E:\Projects\Kronos\ClaudeTradingRD\m3_scalper\xau_m1_3y.parquet` (OANDA M1
2023-07..2026-07, 1.07M bars).

Requirements:
1. Build the study script: load parquet; resample M1->M5/M15/H1/H4/D1; replay s94 and s99
   `get_signal` over 2025-01..2026-07 with a rolling window matched to each module's live
   window depth; simulate fills with the house cost model (0.45pt friction; stress 0.80pt),
   static SL/TP/time exits per module params. For each signal record the D1+H4 bias at
   entry (bias = swing-structure direction from `ict_engine.get_market_structure`
   semantics — reuse `_kronos_indicators.htf_bias_from_window` or port the regime
   `get_htf_bias`). Compare: baseline vs "veto entries whose side opposes an ALIGNED
   D1+H4 bias" (both directional and agreeing; neutral/ranging never vetoes) vs
   "half-size instead of veto". Train = 2025, test = 2026. Report PF, n, avg pts, maxDD
   per arm, per strategy.
2. Decision rule (write it in the report BEFORE running): ship the veto for a strategy
   only if test-period PF improves AND avg-pts/trade improves AND the veto removes <40%
   of trades AND stress-cost PF does not degrade. Otherwise DO NOT wire the gate — the
   report is the deliverable.
3. If shipped: implement inside the strategy module (consume the already-fetched `w15m`?
   NO — bias needs H4/D1: derive H4/D1 by resampling the module's `w5m`/`w15m` if depth
   allows, else fetch via a new optional runner-provided frame; keep it self-contained by
   resampling `w15m` (100 bars = 25h) to H4 — insufficient for D1, so use H4-only bias in
   live and note the study measured D1+H4; re-run the study H4-only to confirm the edge
   holds before wiring). Env `S94_HTF_VETO=off|on`, `S99_HTF_VETO=off|on`, DEFAULT OFF
   regardless of study outcome (arming is an operator decision).
4. Runtime budget: the replay must finish in <30 min on this machine; chunk/vectorize
   accordingly. ASCII progress output.

Tests: unit-test the bias computation on synthetic H4/D1 fixtures (known HH/HL, LH/LL,
mixed); if gate shipped: veto blocks opposing-side entry, neutral passes, env off = Task 8
golden parity.

## Task 14: trailing-exit study (chandelier) for S94 / S100

**Report point #7. Files:** new `strategies/research/trail_exit_study.py`, report
`docs/research/2026-07-30-trailing-exits.md`. NO live code change in this task —
research only (any wiring is a follow-up operator decision; `Signal.trailing` and the
monitor's TRAIL path already exist).

Requirements:
1. Using the same parquet + replay scaffolding as Task 13 (share code via a small
   `strategies/research/replay_lib.py` — Task 13 and 14 coordinate through it; whichever
   task lands second refactors to share), evaluate for S94 and S100: baseline static
   exits vs (a) chandelier trail (activate after +1R, trail = high-water - k*ATR,
   k in {2.0, 2.5, 3.0}) vs (b) trail replacing the TIME backstop only (position past
   max_hold trails instead of flat-closing — the "64% of TIME exits were winners"
   hypothesis). Costs 0.45pt, stress 0.80pt. Train 2025 / test 2026.
2. Report per arm: PF, n, avg pts, maxDD, tail capture (sum of top-5 winners), and an
   explicit ship/no-ship verdict per strategy per the same decision-rule style as Task 13
   (rule written before results).
3. Runtime <30 min; ASCII output.

## Task 15: S100 trend-persistence (ER) gate — opt-in + validation

**Report point #11. Files:** `strategies/backtest_strategies/s100_m3_combo.py`, study
extension in `strategies/research/er_gate_study.py`, report
`docs/research/2026-07-30-s100-er-gate.md`, tests.

Requirements:
1. Study (parquet, replay_lib): replay S100 2023-07..2026-07 (its 2023H2 losing regime is
   the target); compute Kaufman ER exactly as the regime engine does (`_efficiency_ratio`,
   24 H1 bars and 30 M15 bars, thresholds 0.35/0.20) resampled from M1. Arms: baseline vs
   "block new entries when trend_regime==RANGING" vs "block when NOT TRENDING". Success
   rule (pre-registered): 2023H2 loss reduced by >=40% while 2025-2026 profit gives up
   <=15%.
2. Implement in s100 as env-gated `S100_ER_GATE=off|ranging|strict` (DEFAULT OFF): compute
   ER inside the module from its own M3/M1 data resampled to M15/H1 equivalents (the
   module already holds `w1m` deep enough for M15x30; H1x24 needs 1440 M1 bars — extend
   MIN_BARS_1M accordingly ONLY when the gate is enabled, assert at runtime).
3. Tests: ER computation matches regime engine values on a shared fixture; gate off =
   golden parity; gate on blocks a synthetic ranging tape and admits a trending one.

## Task 16: Kronos_Backend index migration

**Report point #3. Repo: `E:\Projects\Kronos\Kronos_Backend` — branch `feat/db-indexes`
off current HEAD.** Files: `apis/models.py`, new migration, tests via
`python manage.py test apis` (repo venv: check `.venv/Scripts/python.exe` there; else use
the KronosStrategies venv only if Django is importable — otherwise document).

Requirements:
1. Add `Meta.indexes` (Django `models.Index`) for: Trigger(position, status);
   Position(symbol, quantity); Order(condition, created_at); Order(broker_order_id).
   Use explicit index names <=30 chars (Postgres limit safety): e.g.
   `idx_trigger_pos_status`.
2. `python manage.py makemigrations apis` -> a single new migration; review the SQL
   (`sqlmigrate`) and paste it into the commit message body. DO NOT run `migrate` against
   any real DB — tests run on in-memory SQLite only.
3. Run the existing test suite for the `apis` app; all green (or document pre-existing
   failures verbatim).
4. Commit ONLY models.py + the migration on `feat/db-indexes` (tree has unrelated
   uncommitted files — settings.py is modified; do not touch or commit it).

## Task 17: documentation corrections

**Report point #2 (doc drift). Files:** `E:\Projects\Kronos\shared\CLAUDE.md`,
`E:\Projects\Kronos\KronosStrategies\docs\superpowers\plans\2026-07-30-optimization-15-plan.md`
(append outcome), NO vault edits (the controller updates the vault after merge).

Requirements:
1. In `shared/CLAUDE.md`: correct the data-flow claim — `position_manager` reads latest
   LTP via OANDA REST S5 (`shared/tsdb_reader.fetch_latest_ltp`), NOT from the
   TimescaleDB `ltp` hypertable; the `ltp` hypertable is written by `tick_data_collector`
   and read only by offline backfill/validation scripts. Note the collectors are behind
   the `data-archive` compose profile as of opt15 (local compose; box unchanged until
   next deploy). Also fix the stale KronosStrategies branch note (`feat/strategy-manager`,
   not `fix/tg-copy-fidelity`).
2. Add one line to the CLAUDE.md cross-cutting notes pointing at
   `shared/OPTIMIZATION_15_POINTS_2026-07-30.md` and this plan for the opt15 change log.
3. Keep edits surgical — do not rewrite sections.

---

## Outcome (2026-07-31)

All 17 tasks complete. Base branch `feat/strategy-manager` @ 3f7d2ce; work landed on
`feat/optimization-15` (KronosStrategies) plus `feat/db-indexes` (Kronos_Backend, Task 16 only).

- **Tasks 1-12** (platform hardening + gates): tsdb_reader cache/retry/bid-ask, MetaAPI
  verify-before-retry, position_monitor N+1 fix + write throttling, entry_manager session
  consolidation + drift-fail-mode + correlation/news/spread gates, connection-pool
  right-sizing + duplicated-tree sync guard, compose mem_limits/healthchecks/data-archive
  profile, shared TA consolidation, S93 SOFT structure veto + 1.5x-ATR gap cap (shipped ON),
  S94 incremental detection parity, regime loop efficiency, metrics/alerting (`obs.py`).
  All reviewed clean; only minor non-blocking notes recorded per task in `progress.md`.
- **Task 13** (HTF-bias study): NO-SHIP — S94/S99 HTF veto fails the pre-registered OOS
  profit rule. Research only, no wiring.
- **Task 14** (chandelier trailing-exit study): S94 chandelier k=2.5 SHIP-strong
  (test PF 1.31, stress PF 1.21); S100 marginal, time-replace arm preferred. Research
  only — no live wiring in this task.
- **Task 15** (S100 ER trend-persistence gate): DO-NOT-ARM — the pre-registered profit
  test is undefined against an unprofitable offline 2025-26 baseline, though the strict
  arm does cut the 2023H2 loss ~92%. Shipped wired but DEFAULT OFF (`S100_ER_GATE=off`).
- **Task 16** (Kronos_Backend index migration): `Trigger(position,status)`,
  `Position(symbol,quantity)`, `Order(condition,created_at)`, `Order(broker_order_id)`
  indexes added on `feat/db-indexes`, commit `3236fa0`; tests green on in-memory SQLite.
- **Task 17** (this task): documentation corrections — see `shared/CLAUDE.md` (data-flow
  diagram fix + cross-cutting pointer) and this section.

**Deploy (2026-07-31):** shipped to the `algorobos` box — 7 services rebuilt (`-p kronos`),
all healthy; in-container asserts confirm S93 soft veto ON, gap cap 1.5x ATR, MetaAPI
retries=0 (verify-before-retry client-id dedup path kept conservative pending prod
observation). Compose merged additively (46 lines added / 0 removed). Every new gate from
Tasks 5, 9, and 15 ships DEFAULT OFF except the validated S93 veto/gap-cap, per this plan's
safety-first-defaults constraint.
