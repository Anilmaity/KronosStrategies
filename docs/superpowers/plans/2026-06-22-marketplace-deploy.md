# Strategy Marketplace + Deploy (Sub-project B) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the broken deploy mutation and add a `/marketplace` page where a user browses active strategies and deploys one into one of their accounts (creating a live `UserStrategy` link).

**Architecture:** Repair `AddStrategy` (backend) to create a `deployed=True` `UserStrategy` scoped to the caller's account; reuse the existing `allStrategy` query for the listing; build a Next.js `/marketplace` page with an account-picker deploy flow, deployed badges, and a runner-caveat banner. No runner change (Sub-project C).

**Tech Stack:** Django 5 + graphene, `python manage.py test apis`, Next.js + `@apollo/client`, `tsc`/`next build`.

**Spec:** `docs/superpowers/specs/2026-06-22-marketplace-deploy-design.md`

**Repo paths (absolute):**
- Backend: `C:/Projects/PycharmProjects/personal/Kronos_Backend`
- Frontend: `C:/Projects/PycharmProjects/personal/Kronos/Kronos App/kronos_frontend`

**Backend test runner:** `./.venv/Scripts/python.exe manage.py test <dotted.path> -v 2` (run from the backend repo, branch `fix/pnl-short-positions`).
**Frontend branch:** `main`.

---

## File Structure

**Backend (`Kronos_Backend`)**
- Modify: `apis/schema/mutation/user/add_strategy.py` — repair the `mutate` body.
- Modify: `apis/tests.py` — deploy mutation tests.

**Frontend (`kronos_frontend`)**
- Create: `GraphQL/marketplaceControls.ts` — `DEPLOY_STRATEGY`, `GET_MARKETPLACE`, `GET_ACCOUNTS_WITH_DEPLOYMENTS`.
- Modify: `app/(main)/_components/constants.ts` — add "Marketplace" nav entry.
- Create: `app/(main)/marketplace/page.tsx` — route.
- Create: `app/(main)/marketplace/_components/MarketplaceManager.tsx` — listing + deploy UI.

`AddStrategy` is already auto-registered by `apis/schema/mutation/user/__init__.py`; `allStrategy` is an existing query. No schema-root edits.

---

## Task 1: Backend — repair the `AddStrategy` deploy mutation

**Files:**
- Modify: `apis/schema/mutation/user/add_strategy.py`
- Test: `apis/tests.py`

Current bug: `mutate` sets `broker_name=userbroker.broker.name`, but `UserStrategy` has no `broker_name` field and `UserBroker` has no `broker` relation, so it raises. It also does not scope the account to the caller nor set `deployed`.

- [ ] **Step 1: Write the failing tests**

Append to the END of `apis/tests.py`:
```python
# ───────────────────────────────────────────────────────────────────────────────
# AddStrategy deploy mutation (2026-06-22, Sub-project B)
# ───────────────────────────────────────────────────────────────────────────────

class AddStrategyDeployTests(TestCase):
    @staticmethod
    def _info(user):
        from types import SimpleNamespace
        return SimpleNamespace(context=SimpleNamespace(user=user))

    def _fresh_strategy(self):
        cp, _ = CurrencyPair.objects.get_or_create(
            symbol="XAU_USD", defaults={"name": "XAU_USD", "ltp": "4540.00"}
        )
        return Strategy.objects.create(
            name=f"Mkt {uuid.uuid4()}", currencypair=cp, is_active=True
        )

    def test_deploy_creates_live_userstrategy(self):
        from apis.schema.mutation.user.add_strategy import AddStrategy
        us = _mk_user_strategy()
        user = us.user_broker.user
        broker = us.user_broker
        strat = self._fresh_strategy()
        res = AddStrategy.mutate(
            None, self._info(user),
            strategy_id=str(strat.id), user_broker_id=str(broker.id), quantity=3,
        )
        self.assertEqual(res.Response, "Success")
        link = UserStrategy.objects.get(user_broker=broker, strategy=strat)
        self.assertTrue(link.deployed)
        self.assertTrue(link.is_active)
        self.assertEqual(link.multiplyer, 3)

    def test_duplicate_returns_already_exists(self):
        from apis.schema.mutation.user.add_strategy import AddStrategy
        us = _mk_user_strategy()
        user = us.user_broker.user
        broker = us.user_broker
        strat = self._fresh_strategy()
        AddStrategy.mutate(
            None, self._info(user),
            strategy_id=str(strat.id), user_broker_id=str(broker.id),
        )
        res = AddStrategy.mutate(
            None, self._info(user),
            strategy_id=str(strat.id), user_broker_id=str(broker.id),
        )
        self.assertIn("Already Exists", res.Response)
        self.assertEqual(
            UserStrategy.objects.filter(user_broker=broker, strategy=strat).count(), 1
        )

    def test_non_owner_account_rejected(self):
        from apis.schema.mutation.user.add_strategy import AddStrategy
        owner_us = _mk_user_strategy()
        broker = owner_us.user_broker
        other = _mk_user_strategy().user_broker.user
        strat = self._fresh_strategy()
        res = AddStrategy.mutate(
            None, self._info(other),
            strategy_id=str(strat.id), user_broker_id=str(broker.id),
        )
        self.assertIn("does not exist", res.Response)
        self.assertFalse(
            UserStrategy.objects.filter(user_broker=broker, strategy=strat).exists()
        )

    def test_missing_strategy(self):
        from apis.schema.mutation.user.add_strategy import AddStrategy
        us = _mk_user_strategy()
        user = us.user_broker.user
        res = AddStrategy.mutate(
            None, self._info(user),
            strategy_id=str(uuid.uuid4()), user_broker_id=str(us.user_broker.id),
        )
        self.assertIn("Strategy does not exist", res.Response)
```
(`uuid`, `CurrencyPair`, `Strategy`, `UserStrategy`, `_mk_user_strategy` are already imported/defined at the top of `apis/tests.py`.)

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe manage.py test apis.tests.AddStrategyDeployTests -v 2`
Expected: FAIL — `test_deploy_creates_live_userstrategy` errors on `userbroker.broker.name` (AttributeError), and the scoping/messages don't match.

- [ ] **Step 3: Rewrite the mutation**

Replace the ENTIRE contents of `apis/schema/mutation/user/add_strategy.py` with:
```python
import graphene

from apis.models import UserStrategy, Strategy, UserBroker
from apis.schema.utils import user_authenticate
from apis.schema.types.user_strategy_type import UserStrategyType


class AddStrategy(graphene.Mutation):
    UserStrategy = graphene.Field(UserStrategyType)
    Response = graphene.String()

    class Arguments:
        strategy_id = graphene.String(required=True)
        user_broker_id = graphene.String(required=True)
        quantity = graphene.Int(required=False)

    @user_authenticate
    def mutate(self, info, strategy_id, user_broker_id, quantity=1):
        try:
            if info.context.user.is_superuser:
                userbroker = UserBroker.objects.get(id=user_broker_id)
            else:
                userbroker = UserBroker.objects.get(
                    id=user_broker_id, user=info.context.user
                )
        except UserBroker.DoesNotExist:
            return AddStrategy(UserStrategy=None, Response="Account does not exist")

        try:
            strategy = Strategy.objects.get(id=strategy_id)
        except Strategy.DoesNotExist:
            return AddStrategy(UserStrategy=None, Response="Strategy does not exist")

        existing = UserStrategy.objects.filter(
            user_broker=userbroker, strategy=strategy
        ).first()
        if existing:
            return AddStrategy(
                UserStrategy=existing, Response="Strategy Already Exists"
            )

        userstrategy = UserStrategy.objects.create(
            user_broker=userbroker,
            strategy=strategy,
            name=strategy.name,
            multiplyer=quantity,
            is_active=True,
            deployed=True,
        )
        return AddStrategy(UserStrategy=userstrategy, Response="Success")
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/Scripts/python.exe manage.py test apis.tests.AddStrategyDeployTests -v 2`
Expected: PASS (4 tests).

- [ ] **Step 5: Confirm no stale `broker_name` reference and full suite green**

Run:
```
grep -rn "broker_name\|userbroker.broker\|\.broker\.name" apis/schema/mutation/user/add_strategy.py
./.venv/Scripts/python.exe manage.py test apis -v 1
```
Expected: grep returns nothing; full suite PASS.

- [ ] **Step 6: Commit**

```bash
cd "C:/Projects/PycharmProjects/personal/Kronos_Backend"
git add apis/schema/mutation/user/add_strategy.py apis/tests.py
git commit -m "fix(deploy): repair AddStrategy — remove broken broker_name, set deployed, scope to user

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Frontend — `marketplaceControls.ts` module

**Files:**
- Create: `GraphQL/marketplaceControls.ts`

- [ ] **Step 1: Create the module**

Create `C:/Projects/PycharmProjects/personal/Kronos/Kronos App/kronos_frontend/GraphQL/marketplaceControls.ts`:
```ts
import { gql } from "@apollo/client";

// Deploy a strategy into an account (creates a live UserStrategy link).
export const DEPLOY_STRATEGY = gql`
  mutation AddStrategy(
    $strategyId: String!
    $userBrokerId: String!
    $quantity: Int
  ) {
    AddStrategy(
      strategyId: $strategyId
      userBrokerId: $userBrokerId
      quantity: $quantity
    ) {
      Response
      UserStrategy {
        id
      }
    }
  }
`;

// All strategies (the page filters to isActive for the marketplace).
export const GET_MARKETPLACE = gql`
  query GetMarketplace {
    allStrategy {
      id
      name
      description
      capitalRequired
      symbol
      isActive
    }
  }
`;

// The user's accounts + which strategies are already deployed to each.
export const GET_ACCOUNTS_WITH_DEPLOYMENTS = gql`
  query GetAccountsWithDeployments {
    getuserdata {
      userbrokers {
        id
        label
        userstrategys {
          strategy {
            id
          }
        }
      }
    }
  }
`;
```

- [ ] **Step 2: Typecheck**

Run (from the frontend repo): `npx tsc --noEmit`
Expected: no new errors.

- [ ] **Step 3: Commit**

```bash
cd "C:/Projects/PycharmProjects/personal/Kronos/Kronos App/kronos_frontend"
git add GraphQL/marketplaceControls.ts
git commit -m "feat(marketplace): centralized marketplace query/mutation module

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Frontend — Marketplace nav + page

**Files:**
- Modify: `app/(main)/_components/constants.ts`
- Create: `app/(main)/marketplace/page.tsx`
- Create: `app/(main)/marketplace/_components/MarketplaceManager.tsx`

- [ ] **Step 1: Add the nav entry**

In `app/(main)/_components/constants.ts`, add this entry to `sidebarLinkArray` immediately AFTER the "Accounts" entry. `FaRectangleList` is already imported; reuse it:
```ts
  {
    title: "Marketplace",
    path: "/marketplace",
    icon: FaRectangleList,
  },
```

- [ ] **Step 2: Create the route**

Create `app/(main)/marketplace/page.tsx`:
```tsx
import MarketplaceManager from "./_components/MarketplaceManager";

export default function MarketplacePage() {
  return <MarketplaceManager />;
}
```

- [ ] **Step 3: Create the manager component**

Create `app/(main)/marketplace/_components/MarketplaceManager.tsx`:
```tsx
"use client";

import React, { useEffect, useState } from "react";
import { toast } from "sonner";

import { client } from "@/GraphQL/client";
import { middleware } from "@/GraphQL/middleware";
import { formatCapital } from "@/utils/FormatCapital";
import {
  DEPLOY_STRATEGY,
  GET_MARKETPLACE,
  GET_ACCOUNTS_WITH_DEPLOYMENTS,
} from "@/GraphQL/marketplaceControls";

interface Strategy {
  id: string;
  name: string;
  description: string;
  capitalRequired: string;
  symbol: string;
  isActive: boolean;
}

interface Account {
  id: string;
  label: string;
  deployedStrategyIds: string[];
}

const MarketplaceManager: React.FC = () => {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [openFor, setOpenFor] = useState<string | null>(null);
  const [pickedAccount, setPickedAccount] = useState<string>("");
  const [multiplier, setMultiplier] = useState<number>(1);

  const loadStrategies = () => {
    client
      .query({ query: GET_MARKETPLACE, fetchPolicy: "no-cache" })
      .then((res) => {
        const all: Strategy[] = res.data?.allStrategy ?? [];
        setStrategies(all.filter((s) => s.isActive));
      })
      .catch((err) => middleware(err));
  };

  const loadAccounts = () => {
    client
      .query({ query: GET_ACCOUNTS_WITH_DEPLOYMENTS, fetchPolicy: "no-cache" })
      .then((res) => {
        const brokers = res.data?.getuserdata?.userbrokers ?? [];
        setAccounts(
          brokers.map(
            (b: {
              id: string;
              label: string;
              userstrategys: { strategy: { id: string } }[];
            }) => ({
              id: b.id,
              label: b.label,
              deployedStrategyIds: (b.userstrategys ?? []).map(
                (us) => us.strategy?.id
              ),
            })
          )
        );
      })
      .catch((err) => middleware(err));
  };

  useEffect(() => {
    loadStrategies();
    loadAccounts();
  }, []);

  const openDeploy = (strategyId: string) => {
    setOpenFor(strategyId);
    setPickedAccount("");
    setMultiplier(1);
  };

  const handleDeploy = (strategyId: string) => {
    if (!pickedAccount) {
      toast.error("Pick an account");
      return;
    }
    client
      .mutate({
        mutation: DEPLOY_STRATEGY,
        variables: {
          strategyId,
          userBrokerId: pickedAccount,
          quantity: multiplier,
        },
        fetchPolicy: "no-cache",
      })
      .then((res) => {
        const resp = res.data.AddStrategy.Response;
        if (resp === "Success") {
          toast.success("Strategy deployed");
          setOpenFor(null);
          loadAccounts();
        } else {
          toast.error(resp);
        }
      })
      .catch((err) => middleware(err));
  };

  return (
    <div className="flex flex-col gap-6 w-full">
      <h1 className="text-xl font-semibold">Marketplace</h1>

      <div className="border border-myYellow1 rounded-md px-4 py-3 text-sm">
        Deployed strategies trade on the configured runner account until
        per-account trading is enabled. The account you choose here records the
        deployment but does not yet route trades.
      </div>

      {accounts.length === 0 && (
        <div className="text-sm opacity-70">
          No accounts yet — add one on the Accounts page first.
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 w-full">
        {strategies.length === 0 && (
          <div className="text-sm opacity-70">No strategies available.</div>
        )}
        {strategies.map((s) => (
          <div key={s.id} className="border rounded-md p-4 flex flex-col gap-2">
            <div className="font-semibold">{s.name}</div>
            <div className="text-sm opacity-80">{s.description || "—"}</div>
            <div className="text-sm">
              {s.symbol} · Capital {formatCapital(parseInt(s.capitalRequired))}
            </div>

            {openFor === s.id ? (
              <div className="flex flex-col gap-2 mt-2">
                <select
                  className="border rounded px-2 py-1"
                  value={pickedAccount}
                  onChange={(e) => setPickedAccount(e.target.value)}
                >
                  <option value="">Select account…</option>
                  {accounts.map((a) => {
                    const already = a.deployedStrategyIds.includes(s.id);
                    return (
                      <option key={a.id} value={a.id} disabled={already}>
                        {a.label || "(no label)"}
                        {already ? " — Deployed" : ""}
                      </option>
                    );
                  })}
                </select>
                <input
                  className="border rounded px-2 py-1"
                  type="number"
                  min={1}
                  value={multiplier}
                  onChange={(e) =>
                    setMultiplier(Math.max(1, Number(e.target.value)))
                  }
                />
                <div className="flex gap-2">
                  <button
                    className="bg-myGreen1 text-black rounded px-3 py-1"
                    onClick={() => handleDeploy(s.id)}
                  >
                    Confirm deploy
                  </button>
                  <button
                    className="border rounded px-3 py-1"
                    onClick={() => setOpenFor(null)}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <button
                className="border rounded px-3 py-1 self-start mt-2"
                onClick={() => openDeploy(s.id)}
              >
                Deploy
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
};

export default MarketplaceManager;
```

- [ ] **Step 4: Typecheck and build**

Run (from the frontend repo):
```
npx tsc --noEmit
npm run build
```
Expected: typecheck clean; `next build` succeeds and lists `/marketplace`.

- [ ] **Step 5: Commit**

```bash
cd "C:/Projects/PycharmProjects/personal/Kronos/Kronos App/kronos_frontend"
git add "app/(main)/_components/constants.ts" "app/(main)/marketplace"
git commit -m "feat(marketplace): /marketplace page — browse + deploy into an account

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: End-to-end verification (manual, against the running local stack)

**Files:** none (verification only). Requires the backend + frontend hot-reloaded and (from Sub-project A) the live DB migrated.

- [ ] **Step 1: Browse**

Log in, open the **Marketplace** tab. Confirm active strategies appear as cards (name, description, symbol, capital), and the runner-caveat banner is visible.

- [ ] **Step 2: Deploy**

Click **Deploy** on a strategy → pick one of your accounts → set a multiplier → Confirm. Expect a "Strategy deployed" toast.

- [ ] **Step 3: Confirm on the dashboard**

Open the Dashboard — the deployed strategy now appears under that account (it is `deployed=True`, `is_active=True`). Confirm the multiplier matches.

- [ ] **Step 4: Duplicate guard + badge**

Return to Marketplace, Deploy the same strategy again → the previously-used account is shown as "Deployed" and disabled in the picker. If forced, the backend returns "Strategy Already Exists" (error toast) and no second row is created.

- [ ] **Step 5: Record results**

Note pass/fail for browse, deploy, dashboard appearance, and the duplicate/badge behavior. If all pass, Sub-project B is complete and C (runner per-account trading) can begin.

---

## Self-Review notes (author)

- **Spec coverage:** deploy mutation fix incl. user-scoping + deployed flag + dup guard (Task 1); marketplace listing via reused `allStrategy` (Task 2 `GET_MARKETPLACE`); `/marketplace` page with account picker, deployed badges, multiplier, and caveat banner (Task 3); manual E2E (Task 4). All spec sections mapped.
- **Names pinned:** mutation GraphQL op `AddStrategy` (args `strategyId`, `userBrokerId`, `quantity`); query `allStrategy` (fields `id name description capitalRequired symbol isActive`); deployments via `getuserdata.userbrokers[].userstrategys[].strategy.id`. `UserStrategy` flags set: `is_active=True`, `deployed=True`, `multiplyer=quantity`.
- **No JS test runner:** frontend verified via `tsc --noEmit` + `next build` + the Task 4 manual checklist.
- **Caveat surfaced:** the warning banner (Task 3 Step 3) implements the spec's requirement that the per-account routing limitation is visible, not hidden.
