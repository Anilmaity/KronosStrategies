# Fix Live Strategy Controls (Multiplier / Pause / Stop) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the dashboard's multiplier, pause, and stop/exit controls actually work, and centralize the frontend control mutations so backend↔frontend name drift can't silently recur.

**Architecture:** Three repos. Backend (`Kronos_Backend`, Django + Graphene) holds the GraphQL mutations. Frontend (`Kronos App/kronos_frontend`, Next.js + Apollo) calls them. The runner repo (`KronosStrategies`) only reads the resulting DB flags and needs no code change. The multiplier bug is a stale frontend mutation name; the stop/exit bug is a backend correctness issue (timeouts reported as success); pause is already wired and only needs verification.

**Tech Stack:** Django 5 + graphene, `python manage.py test apis`, Next.js + `@apollo/client`, `tsc`/`next build` for frontend verification (no JS test runner in this repo).

**Repo paths (absolute):**
- Backend: `C:/Projects/PycharmProjects/personal/Kronos_Backend`
- Frontend: `C:/Projects/PycharmProjects/personal/Kronos/Kronos App/kronos_frontend`

**Spec:** `docs/superpowers/specs/2026-06-21-fix-live-controls-design.md`

---

## File Structure

**Backend (`Kronos_Backend`)**
- Modify: `apis/schema/mutation/user/exit_strategy.py` — extract testable exit helpers; stop treating timeouts as success; add an `Ok` boolean to the mutation output.
- Modify: `apis/tests.py` — add tests for the exit helpers and the existing `SetUserStrategyMultiplier`.
- Delete: `apis/schema/mutation/user/__pycache__/set_user_strategy_quantity.cpython-312.pyc` — orphaned bytecode from the renamed mutation.

**Frontend (`kronos_frontend`)**
- Create: `GraphQL/strategyControls.ts` — single source of truth for the four control mutations.
- Modify: `app/(main)/dashboard/_components/StrategyTableRow.tsx` — use the module; fix the multiplier call; show truthful exit toasts.
- Modify: `app/(main)/dashboard/_components/StrategyBox.tsx` — same refactor (it duplicates the row's handlers).

`StrategyTable.tsx` contains only a query, no control mutations — no change.

---

## Task 1: Backend — make Exit/Stop report truthful success/failure

**Files:**
- Modify: `apis/schema/mutation/user/exit_strategy.py`
- Test: `apis/tests.py`

Current bug: `exit_strategy.py` counts `requests.exceptions.Timeout` as a success and the resolver always reports success regardless of failures. We split the exit loop into pure, testable helpers, treat timeouts as a distinct "pending" outcome (never counted as confirmed success), and expose an `Ok` flag.

- [ ] **Step 1: Write the failing tests**

Add to the end of `apis/tests.py`:

```python
# ───────────────────────────────────────────────────────────────────────────────
# ExitStrategy helpers — truthful success/failure (2026-06-21)
# ───────────────────────────────────────────────────────────────────────────────

from unittest.mock import MagicMock
import requests as _requests

from apis.schema.mutation.user.exit_strategy import (
    request_position_exits,
    build_exit_message,
)


class ExitStrategyHelperTests(TestCase):
    @staticmethod
    def _pos(pid="p1"):
        m = MagicMock()
        m.id = pid
        return m

    def test_all_confirmed(self):
        ok_resp = MagicMock()
        ok_resp.raise_for_status.return_value = None
        post = MagicMock(return_value=ok_resp)
        confirmed, pending, failed = request_position_exits(
            [self._pos(), self._pos()], post=post
        )
        self.assertEqual((confirmed, pending, failed), (2, 0, 0))
        self.assertEqual(post.call_count, 2)

    def test_timeout_is_pending_not_confirmed(self):
        post = MagicMock(side_effect=_requests.exceptions.Timeout())
        confirmed, pending, failed = request_position_exits([self._pos()], post=post)
        self.assertEqual((confirmed, pending, failed), (0, 1, 0))

    def test_connection_error_is_failed(self):
        post = MagicMock(side_effect=_requests.exceptions.ConnectionError())
        confirmed, pending, failed = request_position_exits([self._pos()], post=post)
        self.assertEqual((confirmed, pending, failed), (0, 0, 1))

    def test_http_error_is_failed(self):
        bad_resp = MagicMock()
        bad_resp.raise_for_status.side_effect = _requests.exceptions.HTTPError()
        post = MagicMock(return_value=bad_resp)
        confirmed, pending, failed = request_position_exits([self._pos()], post=post)
        self.assertEqual((confirmed, pending, failed), (0, 0, 1))

    def test_message_includes_pending_and_failed(self):
        self.assertEqual(build_exit_message(1, 0, 0), "Exit requested: 1 exited")
        msg = build_exit_message(1, 2, 3)
        self.assertIn("1 exited", msg)
        self.assertIn("2 pending confirmation", msg)
        self.assertIn("3 failed", msg)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `Kronos_Backend`):
```
python manage.py test apis.tests.ExitStrategyHelperTests -v 2
```
Expected: FAIL with `ImportError: cannot import name 'request_position_exits'` (helpers don't exist yet).

- [ ] **Step 3: Rewrite `exit_strategy.py` with the helpers and the fix**

Replace the entire contents of `apis/schema/mutation/user/exit_strategy.py` with:

```python
import requests
import graphene
from django.db.models import Q

from apis.models import UserStrategy
from apis.schema.utils import user_authenticate
from apis.schema.types.user_strategy_type import UserStrategyType


EXIT_LAMBDA_URL = "https://yo7uvfbmgdlzlux4vklm7gkdfm0akpav.lambda-url.ap-south-1.on.aws/"


def request_position_exits(positions, post=requests.post):
    """POST one exit request per position to the exit Lambda.

    Returns (confirmed, pending, failed):
      confirmed - Lambda returned 2xx
      pending   - request timed out; outcome unknown, do NOT claim success
      failed    - connection error or non-2xx response
    """
    confirmed = pending = failed = 0
    for position in positions:
        payload = {"position_id": str(position.id), "condition": "Platform Exit"}
        try:
            response = post(EXIT_LAMBDA_URL, json=payload, timeout=3)
            response.raise_for_status()
            confirmed += 1
        except requests.exceptions.Timeout:
            pending += 1
        except requests.exceptions.RequestException:
            failed += 1
    return confirmed, pending, failed


def build_exit_message(confirmed, pending, failed):
    parts = [f"{confirmed} exited"]
    if pending:
        parts.append(f"{pending} pending confirmation")
    if failed:
        parts.append(f"{failed} failed")
    return "Exit requested: " + ", ".join(parts)


class ExitStrategy(graphene.Mutation):
    Response = graphene.String()
    Ok = graphene.Boolean()
    UserStrategy = graphene.Field(UserStrategyType)

    class Arguments:
        strategy_id = graphene.String(required=True)
        broker_cred_id = graphene.String(required=True)

    @user_authenticate
    def mutate(self, info, strategy_id, broker_cred_id):
        try:
            if info.context.user.is_superuser:
                userstrategy = UserStrategy.objects.get(id=strategy_id)
            else:
                userstrategy = UserStrategy.objects.get(
                    user_broker__user=info.context.user, id=strategy_id
                )
        except UserStrategy.DoesNotExist:
            return ExitStrategy(
                Response="Strategy or UserBroker Does Not Exist",
                Ok=False,
                UserStrategy=None,
            )

        positions = userstrategy.position_set.filter(~Q(quantity=0))
        if not positions.exists():
            return ExitStrategy(
                Response="No Position to exit", Ok=True, UserStrategy=userstrategy
            )

        confirmed, pending, failed = request_position_exits(positions)
        return ExitStrategy(
            Response=build_exit_message(confirmed, pending, failed),
            Ok=(failed == 0),
            UserStrategy=userstrategy,
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```
python manage.py test apis.tests.ExitStrategyHelperTests -v 2
```
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
cd "C:/Projects/PycharmProjects/personal/Kronos_Backend"
git add apis/schema/mutation/user/exit_strategy.py apis/tests.py
git commit -m "fix(exit): stop reporting exit-request timeouts as success

Split exit loop into testable helpers; timeouts become a distinct
'pending' outcome and never count as confirmed. Add Ok flag so the FE
can show a truthful toast.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Backend — lock multiplier mutation behavior with a test

**Files:**
- Test: `apis/tests.py`

No backend code change — `SetUserStrategyMultiplier` already works; the multiplier bug is entirely frontend. This test pins its behavior so the FE fix in Task 5 has a verified contract. The `user_authenticate` decorator only checks `info.context.user.is_authenticated`, which is always `True` for a real Django `User`, so we can call `mutate` directly with a hand-built `info`.

- [ ] **Step 1: Write the failing test**

Add to the end of `apis/tests.py`:

```python
# ───────────────────────────────────────────────────────────────────────────────
# SetUserStrategyMultiplier — contract pinned for the FE fix (2026-06-21)
# ───────────────────────────────────────────────────────────────────────────────

from types import SimpleNamespace

from apis.schema.mutation.user.set_user_strategy_multiplier import (
    SetUserStrategyMultiplier,
)


class SetMultiplierTests(TestCase):
    @staticmethod
    def _info(user):
        return SimpleNamespace(context=SimpleNamespace(user=user))

    def test_sets_multiplier(self):
        us = _mk_user_strategy()
        user = us.user_broker.user
        res = SetUserStrategyMultiplier.mutate(
            None, self._info(user), user_strategy_id=str(us.id), multiplier=5
        )
        us.refresh_from_db()
        self.assertEqual(us.multiplyer, 5)
        self.assertEqual(res.Response, "Success")

    def test_rejects_below_one(self):
        us = _mk_user_strategy()
        user = us.user_broker.user
        res = SetUserStrategyMultiplier.mutate(
            None, self._info(user), user_strategy_id=str(us.id), multiplier=0
        )
        self.assertIn("Multiplier must be", res.Response)
        us.refresh_from_db()
        self.assertEqual(us.multiplyer, 1)  # unchanged
```

- [ ] **Step 2: Run the test to verify it passes (behavior already exists)**

Run:
```
python manage.py test apis.tests.SetMultiplierTests -v 2
```
Expected: PASS (2 tests). This is a characterization test — it documents existing backend behavior so the FE knows the exact mutation name (`SetUserStrategyMultiplier`) and arg (`multiplier`, Int, `>= 1`).

- [ ] **Step 3: Commit**

```bash
cd "C:/Projects/PycharmProjects/personal/Kronos_Backend"
git add apis/tests.py
git commit -m "test(multiplier): pin SetUserStrategyMultiplier contract

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Backend — remove orphaned bytecode

**Files:**
- Delete: `apis/schema/mutation/user/__pycache__/set_user_strategy_quantity.cpython-312.pyc`

This stale `.pyc` (no matching `.py`) is the fossil of the renamed mutation and is what confirmed the rename. Remove it so nobody imports a ghost.

- [ ] **Step 1: Delete the file**

```bash
cd "C:/Projects/PycharmProjects/personal/Kronos_Backend"
rm -f apis/schema/mutation/user/__pycache__/set_user_strategy_quantity.cpython-312.pyc
```

- [ ] **Step 2: Verify it's gone and nothing references the old name**

Run:
```
ls apis/schema/mutation/user/__pycache__/set_user_strategy_quantity.cpython-312.pyc
grep -rn "set_user_strategy_quantity\|SetUserStrategyQuantity" apis
```
Expected: `ls` reports "No such file"; `grep` returns nothing.

- [ ] **Step 3: Commit (only if the pyc was tracked by git)**

```bash
cd "C:/Projects/PycharmProjects/personal/Kronos_Backend"
git status --porcelain apis/schema/mutation/user/__pycache__/ || true
# If git shows the deletion as a change, commit it; otherwise the pyc was
# untracked and nothing to commit — skip.
git add -A apis/schema/mutation/user/__pycache__/
git commit -m "chore: drop orphaned set_user_strategy_quantity bytecode" || echo "nothing to commit (was untracked)"
```

---

## Task 4: Frontend — centralize the control mutations

**Files:**
- Create: `GraphQL/strategyControls.ts`

One module defines all four control mutations using Apollo variables (no string interpolation), so names/args live in exactly one place.

- [ ] **Step 1: Create the module**

Create `C:/Projects/PycharmProjects/personal/Kronos/Kronos App/kronos_frontend/GraphQL/strategyControls.ts`:

```ts
import { gql } from "@apollo/client";

// Pause / resume a user strategy. Sets UserStrategy.is_active.
export const CHANGE_STRATEGY_STATUS = gql`
  mutation ChangeStrategyStatus($userStrategyId: String!, $status: Boolean!) {
    ChangeStrategyStatus(userStrategyId: $userStrategyId, status: $status) {
      Response
    }
  }
`;

// Position-size multiplier for future entries. Sets UserStrategy.multiplyer.
export const SET_USER_STRATEGY_MULTIPLIER = gql`
  mutation SetUserStrategyMultiplier($userStrategyId: String!, $multiplier: Int!) {
    SetUserStrategyMultiplier(userStrategyId: $userStrategyId, multiplier: $multiplier) {
      Response
    }
  }
`;

// Stop / flatten: close all open positions for the strategy.
export const EXIT_STRATEGY = gql`
  mutation ExitStrategy($strategyId: String!, $brokerCredId: String!) {
    ExitStrategy(strategyId: $strategyId, brokerCredId: $brokerCredId) {
      Response
      Ok
    }
  }
`;

// Delete a user strategy.
export const DELETE_USER_STRATEGY = gql`
  mutation DeleteUserStrategy($id: String!) {
    DeleteUserStrategy(id: $id) {
      Response
    }
  }
`;
```

- [ ] **Step 2: Typecheck**

Run (from the frontend repo):
```
npx tsc --noEmit
```
Expected: no new errors introduced by this file. (Pre-existing repo errors, if any, are unrelated — note them but don't fix here.)

- [ ] **Step 3: Commit**

```bash
cd "C:/Projects/PycharmProjects/personal/Kronos/Kronos App/kronos_frontend"
git add GraphQL/strategyControls.ts
git commit -m "feat(fe): centralize strategy control mutations

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Frontend — fix StrategyTableRow.tsx (multiplier + truthful exit)

**Files:**
- Modify: `app/(main)/dashboard/_components/StrategyTableRow.tsx`

Route all four handlers through the module and Apollo variables. The key bug fix: the multiplier handler must call `SetUserStrategyMultiplier` with `multiplier` (not the dead `SetUserStrategyQuantity`/`quantity`). The exit handler must use the new `Ok` flag to choose toast severity.

- [ ] **Step 1: Replace the GraphQL import block**

Find (near the top, lines ~7–9):
```tsx
// GraphQL
import { gql } from "@apollo/client";
import { client } from "@/GraphQL/client";
```
Replace with:
```tsx
// GraphQL
import { client } from "@/GraphQL/client";
import {
  CHANGE_STRATEGY_STATUS,
  SET_USER_STRATEGY_MULTIPLIER,
  EXIT_STRATEGY,
  DELETE_USER_STRATEGY,
} from "@/GraphQL/strategyControls";
```

- [ ] **Step 2: Replace `handleStrategyMode`**

Find the whole `const handleStrategyMode = (...) => { ... };` block and replace with:
```tsx
  const handleStrategyMode = ({
    strategyId,
    status,
  }: {
    strategyId: string;
    status: boolean;
  }) => {
    client
      .mutate({
        mutation: CHANGE_STRATEGY_STATUS,
        variables: { userStrategyId: strategyId, status },
        fetchPolicy: "no-cache",
      })
      .then((response) => {
        if (response.data.ChangeStrategyStatus.Response === "Success") {
          toast.success("Success in Toggle Strategy");
          setStrategyChangeHappend(true);
        } else {
          toast.error("Something went wrong in Toggle Strategy");
        }
      })
      .catch((err) => {
        middleware(err);
      });
  };
```

- [ ] **Step 3: Replace `handleMultiplierChange` (the bug fix)**

Find the whole `const handleMultiplierChange = (...) => { ... };` block and replace with:
```tsx
  const handleMultiplierChange = (selectedQty: string) => {
    client
      .mutate({
        mutation: SET_USER_STRATEGY_MULTIPLIER,
        variables: { userStrategyId: data.id, multiplier: Number(selectedQty) },
        fetchPolicy: "no-cache",
      })
      .then((response) => {
        if (response.data.SetUserStrategyMultiplier.Response === "Success") {
          toast.success("Multiplier Changed Successfully");
          setStrategyChangeHappend(true);
        } else {
          toast.error("Something went wrong in changing multiplier");
        }
      })
      .catch((err) => {
        middleware(err);
      });
  };
```

- [ ] **Step 4: Replace `handleExitStrategy` (truthful toast)**

Find the whole `const handleExitStrategy = (...) => { ... };` block and replace with:
```tsx
  const handleExitStrategy = ({
    strategyId,
    brokerId,
  }: {
    strategyId: string;
    brokerId: string;
  }) => {
    client
      .mutate({
        mutation: EXIT_STRATEGY,
        variables: { strategyId, brokerCredId: brokerId },
        fetchPolicy: "no-cache",
      })
      .then((response) => {
        const { Response, Ok } = response.data.ExitStrategy;
        if (Ok) {
          toast.success(Response);
        } else {
          toast.error(Response);
        }
        setStrategyChangeHappend(true);
      })
      .catch((err) => {
        middleware(err);
      });
  };
```

- [ ] **Step 5: Replace the delete mutation inside `handleDeleteStrategy`**

Inside `handleDeleteStrategy`, find:
```tsx
    const mutation = gql`
    mutation{
      DeleteUserStrategy(id: "${strategyId}"){
        Response
      }
    }
    `;

    client
      .mutate({
        mutation: mutation,
        fetchPolicy: "no-cache",
      })
```
Replace with:
```tsx
    client
      .mutate({
        mutation: DELETE_USER_STRATEGY,
        variables: { id: strategyId },
        fetchPolicy: "no-cache",
      })
```

- [ ] **Step 6: Typecheck**

Run (from the frontend repo):
```
npx tsc --noEmit
```
Expected: no `gql` "declared but never used" error and no new errors from this file.

- [ ] **Step 7: Commit**

```bash
cd "C:/Projects/PycharmProjects/personal/Kronos/Kronos App/kronos_frontend"
git add "app/(main)/dashboard/_components/StrategyTableRow.tsx"
git commit -m "fix(fe): call SetUserStrategyMultiplier; truthful exit toast

Multiplier menu was calling the removed SetUserStrategyQuantity mutation.
Route all strategy controls through GraphQL/strategyControls.ts and show
exit success/failure from the new Ok flag.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Frontend — apply the same fix to StrategyBox.tsx

**Files:**
- Modify: `app/(main)/dashboard/_components/StrategyBox.tsx`

`StrategyBox.tsx` duplicates the row's handlers (confirmed: it also calls the dead `SetUserStrategyQuantity` at lines ~154–170 and toasts exit as success at ~203). Apply the identical refactor. The handler bodies are the same as Task 5 — repeat them here; do not assume Task 5 was read.

- [ ] **Step 1: Replace the GraphQL import block**

Find:
```tsx
import { gql } from "@apollo/client";
import { client } from "@/GraphQL/client";
```
Replace with:
```tsx
import { client } from "@/GraphQL/client";
import {
  CHANGE_STRATEGY_STATUS,
  SET_USER_STRATEGY_MULTIPLIER,
  EXIT_STRATEGY,
  DELETE_USER_STRATEGY,
} from "@/GraphQL/strategyControls";
```
(If `StrategyBox.tsx` imports `gql` on a combined line such as `import { gql } from "@apollo/client";` only, remove that import. If `gql` is imported alongside other names still in use, remove only `gql` from the destructured list.)

- [ ] **Step 2: Replace the status-toggle handler**

Find the handler that runs the `ChangeStrategyStatus` mutation (around lines 124–137) and replace its body so it uses:
```tsx
      .mutate({
        mutation: CHANGE_STRATEGY_STATUS,
        variables: { userStrategyId: strategyId, status },
        fetchPolicy: "no-cache",
      })
```
keeping the existing `.then(...)` that checks `response.data.ChangeStrategyStatus.Response === "Success"` and the existing `.catch((err) => middleware(err))`.

- [ ] **Step 3: Replace the multiplier handler (the bug fix)**

Find the handler that runs the `SetUserStrategyQuantity` mutation (around lines 154–170) and replace its entire body with:
```tsx
    client
      .mutate({
        mutation: SET_USER_STRATEGY_MULTIPLIER,
        variables: { userStrategyId: data.id, multiplier: Number(selectedQty) },
        fetchPolicy: "no-cache",
      })
      .then((response) => {
        if (response.data.SetUserStrategyMultiplier.Response === "Success") {
          toast.success("Multiplier Changed Successfully");
          setStrategyChangeHappend(true);
        } else {
          toast.error("Something went wrong in changing multiplier");
        }
      })
      .catch((err) => {
        middleware(err);
      });
```
(`selectedQty` is the existing parameter name of this handler — keep whatever the function already names it; it is the value passed from the multiplier menu item.)

- [ ] **Step 4: Replace the exit handler (truthful toast)**

Find the handler that runs the `ExitStrategy` mutation (around lines 189–203) and replace its body with:
```tsx
    client
      .mutate({
        mutation: EXIT_STRATEGY,
        variables: { strategyId, brokerCredId: brokerId },
        fetchPolicy: "no-cache",
      })
      .then((response) => {
        const { Response, Ok } = response.data.ExitStrategy;
        if (Ok) {
          toast.success(Response);
        } else {
          toast.error(Response);
        }
        setStrategyChangeHappend(true);
      })
      .catch((err) => {
        middleware(err);
      });
```
(Keep this handler `async` only if it was already; the body above does not require `await`.)

- [ ] **Step 5: Replace the delete mutation**

Find the `DeleteUserStrategy` inline `gql` (around lines 221–236) and switch it to:
```tsx
    client
      .mutate({
        mutation: DELETE_USER_STRATEGY,
        variables: { id: strategyId },
        fetchPolicy: "no-cache",
      })
```
keeping the existing `.then(...)` that checks `response.data.DeleteUserStrategy.Response === "Strategy Deleted Successfully"`.

- [ ] **Step 6: Typecheck and build**

Run (from the frontend repo):
```
npx tsc --noEmit
npm run build
```
Expected: typecheck clean for these files; `next build` succeeds.

- [ ] **Step 7: Verify no stale call sites remain**

Run (from the frontend repo):
```
grep -rn "SetUserStrategyQuantity" "app" "GraphQL" "components"
```
Expected: no matches.

- [ ] **Step 8: Commit**

```bash
cd "C:/Projects/PycharmProjects/personal/Kronos/Kronos App/kronos_frontend"
git add "app/(main)/dashboard/_components/StrategyBox.tsx"
git commit -m "fix(fe): StrategyBox uses centralized control mutations

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: End-to-end verification (manual, against a paper-trading UserStrategy)

**Files:** none (verification only).

The backend logic and FE wiring are unit/type checked; this task confirms the three controls behave correctly against the real running system before declaring done. Use a paper-trading `UserStrategy` (one with `enable_paper_trading`) so no real money is at risk.

- [ ] **Step 1: Multiplier round-trip**

In the dashboard, open a strategy's menu → Multiplier → pick e.g. `4x`. Then in the app DB confirm the value persisted:
```
# From Kronos_Backend with DB env loaded:
python manage.py shell -c "from apis.models import UserStrategy; us=UserStrategy.objects.get(id='<USER_STRATEGY_ID>'); print('multiplyer=', us.multiplyer)"
```
Expected: `multiplyer= 4`, and a success toast appeared (not an error). Confirm the row's displayed `Nx` updates after `setStrategyChangeHappend`.

- [ ] **Step 2: Pause / resume**

Pause the strategy in the UI. Confirm:
```
python manage.py shell -c "from apis.models import UserStrategy; us=UserStrategy.objects.get(id='<USER_STRATEGY_ID>'); print('is_active=', us.is_active)"
```
Expected: `is_active= False`, row shows "Paused". Confirm via runner logs that no new entry is opened while paused (`entry_manager.py` filters `is_active=True, deployed=True`). Resume → `is_active= True`, entries resume. If pause does NOT flip the flag, debug from here using superpowers:systematic-debugging (start: is the GraphQL request reaching the backend? does the resolver run? does `.save()` commit?).

- [ ] **Step 3: Exit / Stop + Lambda liveness**

First verify the exit Lambda is alive with a single safe call (a clearly non-existent position id so nothing real is touched):
```
curl -s -m 5 -X POST "https://yo7uvfbmgdlzlux4vklm7gkdfm0akpav.lambda-url.ap-south-1.on.aws/" \
  -H "Content-Type: application/json" \
  -d '{"position_id":"00000000-0000-0000-0000-000000000000","condition":"Liveness Check"}'
echo "HTTP exit: $?"
```
Expected: a response (any 2xx/4xx is "alive"); a hang/timeout means the Lambda is down — record that as a follow-up operational task (out of scope to redeploy here per the spec). Then, with a paper position open, click Exit Strategy in the UI and confirm: open positions flatten when the Lambda is live, and the toast reflects the real outcome (success only when nothing failed; error/`failed` text otherwise — never a false success on timeout).

- [ ] **Step 4: Record results**

Note the outcome of each control (pass / fail / Lambda-down) so the next sub-project (broker integration) starts from a known-good controls baseline. If everything passes, the live-controls sub-project is complete.

---

## Self-Review notes (author)

- **Spec coverage:** multiplier FE fix (Tasks 4–6), pause verification (Task 7 Step 2), exit/stop truthful reporting (Task 1) + Lambda check (Task 7 Step 3), centralization (Task 4), orphan pyc removal (Task 3), backend tests (Tasks 1–2), manual reproduction (Task 7). All spec sections mapped.
- **Names pinned:** GraphQL ops `ChangeStrategyStatus`, `SetUserStrategyMultiplier` (arg `multiplier: Int!`), `ExitStrategy` (`strategyId`,`brokerCredId`; now returns `Ok`), `DeleteUserStrategy` (`id`). DB column stays `multiplyer`. Helper names `request_position_exits` / `build_exit_message` consistent between `exit_strategy.py` and the tests.
- **No JS test runner** in the frontend repo — verification is `tsc --noEmit` + `next build` + the Task 7 manual checklist, which is the established pattern for this repo.
