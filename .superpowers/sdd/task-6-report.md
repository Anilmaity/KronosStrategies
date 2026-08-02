# Task 6 report — wire points/matched-usd/reconciliation into the worker result

Plan: `docs/superpowers/plans/2026-08-02-manager-backtest-fidelity.md`, Task 6
(the integration task tying Tasks 3–5 together). Brief:
`.superpowers/sdd/task-6-brief.md`.

(This path previously held an unrelated stale report — "connection-pool
right-sizing" from `feat/optimization-15` — left uncommitted in the working
tree from an earlier session; overwritten below with this task's report, as
the brief's own filename collides with that older plan's Task 6.)

## What was implemented

### 1. `strategies/audit_worker/results.py`
- Removed `assemble()`. Split its body into:
  - `build_arms(gated, ungated, cfg) -> (summary, curves)` — the old
    `summary`/`equity_curve` construction, unchanged logic, extracted so the
    worker can build it before calling `assemble_v2`.
  - `assemble_v2(*, per_strategy, summary, curves, s5_report, notes,
    trades_csv, live_risk_usd_inferred, kill_trips, paused_pct,
    ungated=None) -> dict` — exact shape from the brief. `ungated` is
    accepted (call-site symmetry with the worker) but not embedded in the
    output; the worker folds it into `summary`/`curves` via `build_arms`
    beforehand. Adds the new top-level `live_risk_usd_inferred` key.

### 2. `strategies/audit_worker/live_deltas.py`
- `deltas(sim, live)` now diffs both sub-blocks per strategy:
  `delta.points` = `sim.points - live.points` on `pnl_pts`/`trades`/`win_rate`
  (always present when `live` is not None); `delta.usd` = same three fields
  on `sim.usd - live.usd`, added **only when both sides have priced a `usd`
  block** (sim only gets one after `add_matched_usd` ran; live always has
  one). `live=None` → `delta=None`, unchanged.

### 3. `strategies/audit_worker/worker.py` — `process_run`'s "comparing" phase
- Imports `sizing, reconcile` alongside the existing lazy `audit_worker`
  import (still inside the `try:`, still no `shared.metaapi_client` in the
  import graph).
- Builds `sim_map` (points), `live_map` (points+usd), infers `risk_usd` from
  this window's live losers via `sizing.infer_live_risk_usd`, conditionally
  calls `results.add_matched_usd(sim_map, gated.trades, risk_usd)`.
- Builds `per_strategy = live_deltas.deltas(sim_map, live_map)`, then runs
  `reconcile.reconcile(...)` with `sim_counts` keyed off `sim_map[...]
  ["points"]["trades"]` and attaches `blk["reconciliation"]` per name.
- Replaces the `run.result = results.assemble(...)` call with
  `results.build_arms(...)` + `results.assemble_v2(...)`, passing
  `live_risk_usd_inferred=risk_usd`.

**One deliberate deviation from the brief's literal code**, verified
necessary and tested: the brief's Step 4 snippet appends the
"matched-USD omitted" note unconditionally whenever `risk_usd is None`. I
changed the `else:` to `elif sim_map:`. Reason: `tests/test_mbt_worker.py`
(explicitly listed as a must-stay-green guard, and explicitly **not** in the
in-scope list of tests I'm allowed to edit) has `test_done_path_writes_result`
asserting `claimed.result["notes"] == ["empty roster snapshot: nothing to
simulate"]` for an empty-roster run — where `sim_map` is `{}` and
`risk_usd` is `None` (0 live losers < the `floor=5` default). The
unconditional `else` would append a second, spurious note ("nothing to
simulate" *and* "too few live losers") and break that assertion. Gating the
note on `sim_map` being non-empty is semantically correct too: there is
nothing to "omit" pricing for if the sim produced no strategies at all.
Verified: `test_mbt_worker.py` (all 7, incl. this one and the metaapi-import
guard) is green; the note still fires normally whenever `sim_map` is
non-empty and `risk_usd` is `None`.

## Tests

### TDD RED → GREEN evidence
1. `tests/test_mbt_result_assembly.py` (new) — wrote the brief's Step-1 test
   verbatim (`test_assemble_carries_points_and_reconciliation`) plus one more
   (`test_assemble_v2_ungated_kwarg_is_accepted_but_not_embedded`). Confirmed
   RED first (`audit_worker.results has no attribute 'assemble_v2'`), then
   implemented `assemble_v2`/`build_arms` → GREEN.
2. `tests/test_live_deltas.py::test_deltas_math_and_live_missing` — rewrote
   for the new `{points, usd}` sub-block shape; added a `D` case (sim has
   `points`-only, live has `points`+`usd`) to lock in the "usd only appears
   when both sides have one" guard. RED against the old `deltas()` (`KeyError:
   'points'`), GREEN after the `deltas()` rewrite.
3. `tests/test_mbt_results.py::test_assemble_passthrough_and_arms` renamed to
   `test_build_arms_and_assemble_v2`, now calls `results.build_arms` +
   `results.assemble_v2` and asserts the new key set including
   `live_risk_usd_inferred`. RED against removed `assemble()`, GREEN after.

### Full-suite run
```
.venv/Scripts/python.exe -m pytest tests/ -q --junitxml=junit_report.xml
```
JUnit summary: `tests="895" errors="0" failures="0" skipped="0"`, exit code 0
(276.6s). Console `-q`/`-v` output in this shell truncates the final summary
line due to a carriage-return progress quirk — confirmed via `--junitxml`
instead, which is authoritative.

Focused runs, all green:
- `tests/test_mbt_result_assembly.py tests/test_live_deltas.py
  tests/test_mbt_results.py` — 11 passed.
- `tests/test_mbt_worker.py` — 7 passed (incl.
  `test_no_metaapi_import_static_and_runtime`,
  `test_done_path_writes_result`).

## Files changed (staged per the brief's discipline — no `git add -A`)
- `strategies/audit_worker/worker.py`
- `strategies/audit_worker/results.py`
- `strategies/audit_worker/live_deltas.py`
- `tests/test_mbt_result_assembly.py` (new)
- `tests/test_live_deltas.py`
- `tests/test_mbt_results.py`

Left untouched/unstaged (pre-existing, unrelated dirty state found in the
working tree at session start, not part of this task):
`.claude/settings.json`,
`strategies/backtest/results/manager_sim/summary_20260702_235849.md`.

## Self-review
- Re-read the diff of all three source files after editing; confirmed no
  stray references to the removed `assemble()` remain (`git grep -n
  "results.assemble(" strategies tests` → only `assemble_v2` call sites).
- Confirmed `add_matched_usd`'s `sim_map.setdefault(name, {})` behavior can't
  silently create a `usd`-only entry with no `points` for a name absent from
  `sim_per_strategy`'s output, because `add_matched_usd` is only ever called
  with the `sim_map` that `sim_per_strategy` just built from the same
  `gated.trades` list — every strategy name in one is in the other.
- Confirmed the `elif sim_map:` deviation doesn't mask the real Task-6 goal:
  when strategies *did* trade in the sim but live had &lt;5 losers, the
  note still fires (only the "sim produced literally nothing" case is
  silenced).
- Confirmed `reconcile.get(name, "unavailable")` fallback is defensive only —
  `reconcile.reconcile` already returns `"unavailable"` for every name in
  `strategy_names` (all spec names) that has no `StrategySignal` rows, so
  `per_strategy` (keyed off `sim_map`, a subset of spec names) will always
  find a match unless a name mismatch bug exists elsewhere.

## Concerns
- The `elif sim_map:` deviation is a judgment call, not literally what the
  brief's code block shows — flagged prominently above with the specific
  test that forced it, so a reviewer can override if they'd rather update
  `test_mbt_worker.py` instead (which the brief's staging list does not
  permit me to touch).
- No behavioral test exercises `worker.py`'s new comparing-phase wiring
  end-to-end against a non-empty roster with real live Position/StrategySignal
  rows (i.e. a `test_done_path_writes_result`-style test with seeded DB data
  producing non-empty `sim_map`/`live_map`/`recon`). The existing worker
  tests only exercise the empty-roster and pre-comparing-phase-failure paths.
  Tasks 3–5's own unit tests (`test_live_points.py`, `test_sizing_matched_usd.py`,
  `test_reconcile.py`) cover the underlying functions individually; this
  task's brief did not ask for a new integration-level worker test beyond
  `test_mbt_result_assembly.py` (which tests `assemble_v2` directly, not the
  worker wiring), so I did not add one — flagging as a coverage gap if a
  reviewer wants deeper end-to-end confidence.

## Fix: comparing-phase integration test

Addresses the review finding above ("No behavioral test exercises `worker.py`'s
new comparing-phase wiring end-to-end...") by adding exactly that test, and it
found a real bug the gap let through.

### Covering test
`tests/test_mbt_worker.py::test_comparing_phase_end_to_end_with_seeded_roster`
(new, ~line 169). Seeds:
- A live `Strategy`/`UserStrategy` named `"S93 FVG Scalp"` (resolves via
  `roster._s_code` -> `s93_fvg_scalp`, same DB-display-name convention
  `tests/test_reconcile.py` uses).
- 6 closed `Position` rows (quantity=0, `realized_profit_loss` set) each with
  a matching ENTRY `Order` (lots=0.10): 5 losers at -0.05 units (-$5.00 each)
  and 1 winner at +0.08 units, all inside the run's window with the +5:30 IST
  skew `live_deltas`/`reconcile` both apply to stored timestamps.
- 3 `StrategySignal` rows (1 PLACED, 2 REJECTED "entry_drift") for the audit
  reconciliation.
- Monkeypatches `backtest.manager_sim_engine.run_sim` to return a
  `SimResult` with 2 known `TradeRecord`s (+6pt TP, -3pt SL) for the same
  strategy name, so `sim_map` is non-empty.

Drives `worker.process_run` through `claim_next`/`process_run` exactly like
the existing worker tests, then asserts on `run.result`: `per_strategy["S93
FVG Scalp"]` has `sim`/`live`/`delta` each with `points` and (once risk is
inferred) `usd` sub-blocks; `reconciliation` is the real dict (not
`"unavailable"`) with `live_generated=3`, `live_placed=1`,
`rejected={"entry_drift": 2}`, `sim_trades=2`; top-level
`live_risk_usd_inferred == 5.0`. Chose the "risk inferred" branch (5 seeded
per-trade losers) over the omission-note branch, per the brief's instruction
to pick one and assert it explicitly, since that's the branch
`add_matched_usd` needs to be exercised at all.

### Real bug found and fixed

Writing the test surfaced a genuine wiring bug in `worker.py`'s comparing
phase, of the same shape as the precedent (missing ×100 factor) that
motivated this finding — a live smoke run, not a test, would have been the
first thing to catch it.

**Bug:** `process_run` built the input to `sizing.infer_live_risk_usd`
(which needs a *list of individual live-trade losses* — see its docstring
"median absolute USD of live losers" and `tests/test_sizing_matched_usd.py`'s
fixture `[-36.0, -38.0, -40.0, -38.0, -37.0]`, five individual SL-hit sizes)
from `live_map.values()` — but `live_map` (`live_deltas.live_summary`'s
output) holds **one row per strategy name**, already summed across every
trade that strategy made in the window. With the roster's `specs` list
capped at the number of *replayable strategy names* (today: 4 — `roster.py`'s
`MODULES` dict; the Telegram copy slot isn't replayable so never appears),
`live_losses` could have at most 4 elements — below `infer_live_risk_usd`'s
default `floor=5` **unconditionally, regardless of how many live trades any
strategy made**. `add_matched_usd` could therefore never run in production:
`live_risk_usd_inferred` was permanently `None` and every result's `usd`
sub-blocks were permanently absent, no matter the data.

Confirmed empirically, not just by static reading: ran the new test against
the pre-fix `worker.py` (`git stash push -- strategies/audit_worker/worker.py`,
rerun, `git stash pop`) — it failed exactly at the risk-inference assertion
(`assert result["live_risk_usd_inferred"] == pytest.approx(5.0)` ->
`AssertionError: assert None == 5.0`), with everything else (sim/live
points, reconciliation) passing. That isolates the bug precisely to the
risk-inference input.

**Fix (minimal):**
- `strategies/audit_worker/live_deltas.py` — added
  `trade_losses_usd(session, strategy_names, start_utc, end_utc) -> list[float]`,
  a sibling to `live_summary` that runs the same Position/UserStrategy/Strategy
  join and window filter but returns one `realized_profit_loss * 100` value
  per closed Position, unaggregated.
- `strategies/audit_worker/worker.py` — the comparing phase now calls
  `live_deltas.trade_losses_usd(session, [s.name for s in specs], ...)`
  instead of deriving `live_losses` from `live_map.values()`.

No change to `live_summary`, `deltas`, `reconcile`, `sizing.py`, or
`results.py` — the bug was isolated to which list `infer_live_risk_usd` was
fed at the one call site.

### Command + output
```
.venv/Scripts/python.exe -m pytest tests/test_mbt_worker.py -q
```
```
........                                                                 [100%]
8 passed
```
(8 = the prior 7 worker tests + the new integration test.)

Full suite, foreground per instructions (`timeout=480000`):
```
.venv/Scripts/python.exe -m pytest tests/ -q --junitxml=junit_report_task6.xml
```
JUnit summary: `tests="896" errors="0" failures="0" skipped="0"` (277.8s) —
896 = the prior 895-test baseline + this one new test.

### Files changed
- `tests/test_mbt_worker.py` — new integration test + 3 small seeding helpers
  (`_seed_live_strategy`, `_seed_closed_position`, `_seed_signal`).
- `strategies/audit_worker/live_deltas.py` — new `trade_losses_usd` function
  (production bug fix).
- `strategies/audit_worker/worker.py` — comparing phase now calls
  `trade_losses_usd` instead of aggregating from `live_map` (production bug
  fix).
