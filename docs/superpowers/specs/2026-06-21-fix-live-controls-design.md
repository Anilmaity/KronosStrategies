# Fix Live Strategy Controls (Multiplier / Pause / Stop)

**Date:** 2026-06-21
**Status:** Approved (design) — pending spec review
**Scope:** Sub-project #1 of the Kronos platform build-out. The broader effort
(broker integration, Reports tab, AWS deployment hardening) is decomposed into
separate specs and built after this one.

## Problem

Three live-strategy controls in the dashboard are reported as not working:
multiplier, pause, and stop. Investigation across the three repos
(`Kronos` frontend, `Kronos_Backend`, `KronosStrategies` runners) shows the
backend mutations exist and the runner honors the relevant DB flags, so this is
a wiring/verification bugfix — not a from-scratch build.

### Root causes found

1. **Multiplier (frontend bug, confirmed).**
   `app/(main)/dashboard/_components/StrategyTableRow.tsx` calls a GraphQL
   mutation `SetUserStrategyQuantity(userStrategyId:, quantity:)` and reads
   `response.data.SetUserStrategyQuantity.Response`. That mutation no longer
   exists: the backend was renamed to `SetUserStrategyMultiplier(user_strategy_id,
   multiplier)` (GraphQL: `SetUserStrategyMultiplier(userStrategyId:, multiplier:)`).
   An orphaned `apis/schema/mutation/user/__pycache__/set_user_strategy_quantity.cpython-312.pyc`
   with no matching `.py` source confirms the rename. Every multiplier click
   therefore hits an undefined field and fails.
   - Runner side is correct: `strategies/strategy/entry_manager.py:103` computes
     `qty = float(strategy.entry_quantity) * int(us.multiplyer)`, reading the
     `multiplyer` column fresh at entry time. (Note the column is spelled
     `multiplyer` in the DB/ORM — keep that spelling; only the GraphQL
     name/arg is `multiplier`.)

2. **Pause (wiring looks correct — verify).**
   FE `ChangeStrategyStatus(userStrategyId:, status:)` matches the backend
   mutation, which sets `UserStrategy.is_active`. The runner filters
   `is_active=True, deployed=True` when building entry context
   (`entry_manager.py:96`), so pausing stops *new* entries. It does **not**
   close or stop managing open positions — that is the intended (current)
   behavior under "just make the existing buttons work." Must be reproduced
   against the real DB to confirm it actually flips and the runner respects it.

3. **Stop / Exit (backend bug + operational verify).**
   There is no button literally labeled "Stop"; the closest is "Exit Strategy",
   which is the stop control. It POSTs to a hardcoded Lambda
   (`EXIT_LAMBDA_URL`, ap-south-1) per open position. Two problems:
   - `exit_strategy.py:52-53` counts a `requests.exceptions.Timeout` as a
     **success** (`exited += 1`), masking real failures.
   - The toast always shows the returned message as success regardless of
     `failed` count. If the Lambda is down/undeployed, the user sees "success"
     while nothing closed. Lambda liveness must be verified.

## Decision

Approach **A + centralization from B**:
- Targeted fixes for the three controls.
- While touching the FE call sites, move the control mutations into one typed
  operations module so backend↔frontend name drift cannot silently recur.
- Rejected: a backend `SetUserStrategyQuantity` compatibility alias — it hides
  the mismatch and leaves the FE wrong.

## Design

### Frontend (`Kronos App/kronos_frontend`)

- Create a single module (e.g. `GraphQL/strategyControls.ts`) exporting the
  control mutations as named constants/functions:
  `CHANGE_STRATEGY_STATUS`, `SET_USER_STRATEGY_MULTIPLIER`, `EXIT_STRATEGY`,
  `DELETE_USER_STRATEGY`. Each defines the operation name, args, and the
  response field key in exactly one place.
- Refactor `StrategyTableRow.tsx` to use this module. Fix the multiplier path:
  call `SetUserStrategyMultiplier(userStrategyId:, multiplier:)` and read
  `response.data.SetUserStrategyMultiplier.Response`.
- Audit `StrategyBox.tsx` and `StrategyTable.tsx` (both matched the control
  grep) for the same drift and route them through the new module.
- Exit/Stop: surface real outcomes. When the backend reports any `failed`
  count, show an error/warning toast rather than `toast.success`.

### Backend (`Kronos_Backend`)

- `apis/schema/mutation/user/exit_strategy.py`: stop counting `Timeout` as
  success. A timeout is an unknown outcome — count it as failed (or a distinct
  "unknown" bucket) and reflect it in the returned message. Return accurate
  `exited` / `failed` counts.
- Delete the orphan `set_user_strategy_quantity.cpython-312.pyc`.
- Verify `EXIT_LAMBDA_URL` responds (single safe call); if it is dead, note it
  as a follow-up operational task (out of scope to redeploy the Lambda here
  unless it is a trivial fix).

### Runner (`KronosStrategies`)

- No code change expected. Used only to confirm pause/multiplier are honored
  live: `is_active`/`deployed` filter and `multiplyer` sizing in
  `entry_manager.py`.

## Testing

- **Backend unit tests** (`apis/tests.py` pattern): `SetUserStrategyMultiplier`
  (sets `multiplyer`, rejects `< 1`, scopes by user vs superuser); corrected
  `ExitStrategy` counting (timeout → failed/unknown, accurate message; "no
  position to exit" path).
- **Manual reproduction checklist** against a paper-trading `UserStrategy`:
  1. Set multiplier in UI → assert `UserStrategy.multiplyer` updates in DB and
     next entry sizes `entry_quantity * multiplyer`.
  2. Pause in UI → assert `is_active=False` in DB and runner opens no new entry;
     resume → entries resume.
  3. Exit/Stop in UI → assert open positions flatten when Lambda is live, and
     that a Lambda failure shows an error toast (not success).
- **Lambda check:** one safe verification call to `EXIT_LAMBDA_URL`.

## Out of scope

- Pause/Stop semantic redesign (e.g. pause also freezing position management,
  or stop auto-flattening). Current intended behavior is preserved.
- Redeploying or rewriting the exit Lambda beyond a trivial fix.
- Broker integration, Reports tab, AWS deployment (separate sub-projects).

## Success criteria

- Multiplier change in the UI persists to `UserStrategy.multiplyer` and affects
  the next entry's size.
- Pause flips `is_active` and verifiably stops new entries; resume re-enables.
- Exit/Stop reports truthful success/failure; timeouts are not shown as success.
- Control mutations live in one FE module; no remaining call site references the
  dead `SetUserStrategyQuantity`.
