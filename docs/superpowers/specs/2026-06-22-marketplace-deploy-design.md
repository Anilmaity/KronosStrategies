# Strategy Marketplace + Deploy (Sub-project B) — Design Spec

**Date:** 2026-06-22
**Status:** Approved (design); pending implementation plan
**Author:** Anil + Claude

## Context

Second of three sub-projects for the accounts/marketplace/deploy feature:

- **A — Accounts with credentials (done).** Encrypted MetaAPI creds on `UserBroker`; `/accounts` UI. Spec: `2026-06-22-accounts-credentials-design.md`.
- **B — Marketplace + deploy (this spec).** Browse available strategies; deploy one into an account (create the `UserStrategy` link; fix the broken `AddStrategy`).
- **C — Runner per-account trading (later).** Make the runner trade each deployed strategy on its account's decrypted token instead of the env-var account.

This spec covers **only Sub-project B**.

### Current state (as explored)

- **`Strategy`** (marketplace source): `name` (unique), `description`, `is_active`, `currencypair` (FK), `capital_required` (CharField), `json_data`, `params`. The only visibility flag is `is_active`.
- **`UserStrategy`** (a deployment): `name`, `strategy` (FK), `is_active` (default True), `multiplyer` (default 1), `user_broker` (FK), `deployed` (default False), `created_at`. There is **no `broker_name` field**.
- **`AddStrategy`** (`apis/schema/mutation/user/add_strategy.py`) is the deploy path and is **broken**: it sets `broker_name=userbroker.broker.name`, but `UserStrategy` has no `broker_name` field and `UserBroker` has no `broker` relation. It is `@user_authenticate`, args `strategy_id`, `user_broker_id`, `quantity=1`, and already guards "Already Exists" for an existing (strategy, broker) pair. It does **not** currently scope `user_broker` to the caller, and does **not** set `deployed`.
- **`allStrategy`** query (`apis/schema/query/all_strategy.py`) already returns `Strategy.objects.all().order_by("-created_at")` as `StrategyType` (exposes `name`, `description`, `capitalRequired`, `symbol`).
- The dashboard query (`getuserdata`) already returns the user's accounts → `userstrategys` → `strategy { id }`, so existing deployments per account are derivable on the frontend without a new resolver.
- Frontend conventions established in A: centralized GraphQL module per feature (e.g. `GraphQL/accountControls.ts`), `client.mutate(... fetchPolicy: "no-cache")`, `toast` on result, `middleware(err)` on catch, nav entries in `app/(main)/_components/constants.ts`.

## Goal

A logged-in user can browse a marketplace of active strategies and deploy a chosen strategy into one of their accounts. Deploying creates a live (`deployed=True`) `UserStrategy` link. Re-deploying the same strategy to the same account is prevented.

## Decisions (from brainstorming)

- **Deploy goes live immediately:** the deployment is created with `deployed=True` and `is_active=True`.
- **Known caveat (must be surfaced, not hidden):** until Sub-project C, the runner trades using the **env-var account**, not the account chosen here — and only if a runner process exists for that strategy. The UI shows a persistent warning banner stating this; the spec records it as the reason C is required.
- **Marketplace contents:** all `is_active=True` strategies, shown as cards; accounts where the strategy is already deployed are badged and disabled in the deploy picker.

## Non-goals (out of scope for B)

- Any runner / live-trading change (Sub-project C). B only writes the DB link + flags.
- A curated/featured marketplace flag — `is_active` is the filter.
- Editing strategy definitions; the marketplace is read-only over `Strategy`.

## Architecture

1. **Backend — deploy mutation fix** (`Kronos_Backend`): repair `AddStrategy` (remove broken field, set flags, scope to user).
2. **Backend — marketplace listing**: reuse the existing `allStrategy` query (no new resolver). The frontend filters `is_active`.
3. **Frontend — Marketplace page** (`kronos_frontend`): `/marketplace` route + nav tab; cards; account-picker deploy; deployed badges; warning banner; a `GraphQL/marketplaceControls.ts` module.

### 1. Backend — fix `AddStrategy`

Rewrite the `mutate` body of `AddStrategy`:

```python
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
        return AddStrategy(UserStrategy=existing, Response="Strategy Already Exists")

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

Changes vs current: removed `broker_name=userbroker.broker.name` (nonexistent); added user-scoping of the account; set `is_active=True` + `deployed=True`; clearer messages. Args and return shape unchanged (`UserStrategy`, `Response`).

### 2. Backend — marketplace listing

No backend change. The frontend uses the existing `allStrategy` query and filters to `is_active=True` client-side. `StrategyType` already exposes `id`, `name`, `description`, `capitalRequired`, `symbol`, `isActive`.

### 3. Frontend — Marketplace page

- **Nav:** add a "Marketplace" entry to `constants.ts` (after Accounts), path `/marketplace`.
- **Route:** `app/(main)/marketplace/page.tsx` → `MarketplaceManager` under `app/(main)/marketplace/_components/`.
- **Data:** query `allStrategy` (filter `isActive`), and the user's accounts + existing deployments via `getuserdata { userbrokers { id label userstrategys { strategy { id } } } }` (reuses the existing resolver; `label` from Sub-project A).
- **Cards:** one per active strategy — name, description, required capital (`formatCapital(parseInt(capitalRequired))`), symbol.
- **Deploy flow:** a "Deploy" button per card opens an inline panel/modal with an **account picker** (the user's accounts by `label`) and a **multiplier** input (default 1). On confirm → `AddStrategy(strategyId, userBrokerId, quantity)`. Accounts where this strategy is already deployed (its `strategy.id` appears in that account's `userstrategys`) are shown as "Deployed" and disabled in the picker.
- **Warning banner:** persistent text at the top of the page: *"Deployed strategies trade on the configured runner account until per-account trading is enabled. The account you choose here records the deployment but does not yet route trades."*
- **Toasts:** success → "Strategy deployed"; "Strategy Already Exists" → info/error toast; other → error.
- **Module:** `GraphQL/marketplaceControls.ts` exports three things and does NOT modify `accountControls.ts` (to avoid coupling):
  - `DEPLOY_STRATEGY` — the `AddStrategy` mutation (`strategyId`, `userBrokerId`, `quantity`) selecting `Response` + `UserStrategy { id }`.
  - `GET_MARKETPLACE` — the `allStrategy` query selecting `id name description capitalRequired symbol isActive`.
  - `GET_ACCOUNTS_WITH_DEPLOYMENTS` — `getuserdata { userbrokers { id label userstrategys { strategy { id } } } }` (its own query, used to populate the account picker and the deployed-badge set).

## Data flow

```
Browse:
  Marketplace page
    → allStrategy query → filter isActive → cards
    → getuserdata.userbrokers → accounts + their userstrategys.strategy.id (deployed set)

Deploy:
  card → Deploy → pick account + multiplier
    → AddStrategy(strategyId, userBrokerId, quantity)
      → backend: user-scoped account lookup; dup check; create UserStrategy(deployed=True)
      → Response "Success" | "Strategy Already Exists" | "Account/Strategy does not exist"
    → refetch deployments; toast
```

## Error handling

- **Account not owned / missing:** `AddStrategy` returns "Account does not exist", null `UserStrategy`; no leak of others' accounts.
- **Strategy missing:** returns "Strategy does not exist".
- **Already deployed:** returns "Strategy Already Exists" with the existing link; UI treats as a no-op info toast and keeps the badge.
- **No accounts yet:** the deploy picker shows "No accounts — add one first" linking to `/accounts`.

## Testing

Backend (`apis/tests.py`, Django `TestCase`, SQLite):

- `AddStrategy` success: creates a `UserStrategy` with `deployed=True`, `is_active=True`, `multiplyer=quantity`, linked to the given account+strategy; `Response == "Success"`.
- Duplicate deploy: second call for the same (account, strategy) returns "Strategy Already Exists" and does not create a second row.
- Non-owner account: deploying into another user's `UserBroker` returns "Account does not exist" and creates nothing.
- Missing strategy id: returns "Strategy does not exist".
- (Regression) confirm no reference to `broker_name` / `userbroker.broker` remains.

Frontend: `tsc --noEmit` + `next build` (lists `/marketplace`). Manual: marketplace lists active strategies; deploy into an account → appears on the dashboard as deployed; the same strategy is then badged "Deployed" for that account; warning banner visible.

## Security / safety notes

- Deploy is user-scoped: a user can only deploy into accounts they own.
- The go-live (`deployed=True`) behavior plus the runner caveat is surfaced in the UI banner so a user is not misled into thinking trades route to the chosen account before Sub-project C.

## Open follow-ups (Sub-project C)

- Runner reads each deployed `UserStrategy`'s account (`UserBroker.meta_account_id` + decrypted `meta_api_token_enc`) and trades there; `metaapi_client` per-account refactor; `entry_manager` integration. Only after C does the marketplace's account choice actually route trades.
