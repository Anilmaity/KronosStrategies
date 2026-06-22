# Accounts with Credentials (Sub-project A) — Design Spec

**Date:** 2026-06-22
**Status:** Approved (design); pending implementation plan
**Author:** Anil + Claude

## Context

The user wants a feature set: **add trading accounts**, a **strategy marketplace**, and the ability to **deploy a strategy into a chosen account**, where deployment ultimately makes that account trade the strategy using its own MetaAPI credentials.

That is three subsystems. It is decomposed into three sequential sub-projects, each with its own spec → plan → implementation:

- **A — Accounts with credentials (this spec).** Securely store per-account MetaAPI credentials and provide a UI to manage accounts. Nothing trades yet.
- **B — Marketplace + deploy.** List available strategies; deploy one into an account (create the `UserStrategy` link; fix the currently-broken `AddStrategy`). Depends on A.
- **C — Runner per-account trading (live money).** Refactor `metaapi_client` from a single env-configured account to per-account credentials, and have `entry_manager` load each deployed strategy's decrypted token and trade on the correct account. Depends on A + B.

This spec covers **only Sub-project A**.

### Current state (as explored)

- **"Account" = `UserBroker`** (`apis/models.py`). Today it stores `api_key`, `margin_available`, `margin_used`, `status`, `is_active`, `last_updated`, `user`. It has **no MetaAPI credentials**.
- Trading credentials currently come from **environment variables** on each runner box: `META_API_TOKEN` and `META_ACCOUNT_ID` (read in `strategies/shared/metaapi_client.py`). Effectively one hardcoded account per box. (Changing this is Sub-project C, out of scope here.)
- `cryptography==42.0.5` is present in `Kronos_Backend/requirements.txt` but **commented out** and not installed in the venv.
- `UserBrokerType` (`apis/schema/types/user_broker_type.py`) currently exposes `marginAvailable`, `accountHolderName`, `name` (resolver added 2026-06-22), etc. It has no token field.

## Goal

Let a logged-in user create and manage trading accounts, each holding a MetaAPI **account ID** + **API token**. The token is **encrypted at rest** and **write-only** in the API (never returned; shown masked as `••••<last4>`). This is the secure foundation that Sub-projects B and C build on.

## Non-goals (explicitly out of scope for A)

- Deploying strategies / marketplace (Sub-project B).
- Any runner / live-trading change; the runner keeps using env vars (Sub-project C).
- Validating the MetaAPI credentials against MetaAPI (no live "test connection" call). A may store credentials that are syntactically present but unverified; verification is a later enhancement.

## Architecture

Three layers, one repo boundary each:

1. **Backend model + encryption** (`Kronos_Backend`): new fields on `UserBroker`, a Fernet-based crypto helper, a migration.
2. **Backend GraphQL API** (`Kronos_Backend`): a write-only token in the type; `AddAccount` / `UpdateAccount` mutations; reuse `DeleteUserBroker`.
3. **Frontend Accounts page** (`kronos_frontend`): a new `/accounts` route + nav tab; a centralized mutation module.

### 1. Data model + encryption

Add to the `UserBroker` model:

| Field | Type | Notes |
|---|---|---|
| `label` | `CharField(max_length=120, default="")` | Friendly name ("Primary Live", "Demo"). |
| `meta_account_id` | `CharField(max_length=120, default="")` | MetaAPI account UUID. Identifier, stored plaintext; used by the runner in Sub-project C. |
| `meta_api_token_enc` | `TextField(default="")` | Fernet ciphertext of the token. Never plaintext. |
| `meta_api_token_last4` | `CharField(max_length=4, default="")` | Last 4 chars of the token, for masked display. |

A migration adds all four with safe defaults so existing `UserBroker` rows are unaffected.

**Crypto helper** — `apis/crypto.py`:

```python
import os
from cryptography.fernet import Fernet

def _fernet() -> Fernet:
    key = os.getenv("FIELD_ENCRYPTION_KEY", "")
    if not key:
        raise RuntimeError("FIELD_ENCRYPTION_KEY is not set")
    return Fernet(key.encode())

def encrypt_token(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode()).decode()

def decrypt_token(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    return _fernet().decrypt(ciphertext.encode()).decode()
```

- `FIELD_ENCRYPTION_KEY` is a Fernet key (`Fernet.generate_key()`), provided via env per environment, never committed and never stored in the DB. For local dev, generate one and put it in `Kronos_Backend/.env` (uncommitted).
- Uncomment `cryptography==42.0.5` in `requirements.txt` and install it into the venv.
- The model itself stores only ciphertext; encryption/decryption happens in the mutation layer (and, later, the runner). Decryption is **never** triggered by a GraphQL read.

### 2. GraphQL API (token is write-only)

**`UserBrokerType`** — add read-only display fields; never expose the token:

- `label: String`
- `metaAccountId: String`
- `metaApiTokenLast4: String` (resolver returns `self.meta_api_token_last4`)
- `hasToken: Boolean` (resolver: `bool(self.meta_api_token_enc)`)

There is no field that returns the token or its ciphertext.

**Mutations** (each wrapped in `@user_authenticate` and scoped so a non-superuser only touches their own `UserBroker`):

- `AddAccount(label: String!, metaAccountId: String!, metaApiToken: String!)`
  - Creates a `UserBroker` for `info.context.user`.
  - `meta_api_token_enc = encrypt_token(metaApiToken)`, `meta_api_token_last4 = metaApiToken[-4:]`.
  - Returns `Response` + the created `UserBrokerType` (no token).
- `UpdateAccount(id: String!, label: String, metaAccountId: String, metaApiToken: String)`
  - Loads the user's `UserBroker` by id (404 → "Account does not exist").
  - Updates only provided fields. If `metaApiToken` is provided and non-empty, re-encrypt and update `last4`; if omitted/empty, the token is left unchanged.
  - Returns `Response` + the account (no token).
- **Delete** reuses the existing `DeleteUserBroker` mutation. Confirm during implementation that it is user-scoped; if it is not, tighten it (small, in-scope fix).

`api_key` keeps its existing `default=uuid4` behavior; `AddAccount` does not require it as input.

### 3. Frontend (Accounts page)

- **Nav:** add an "Accounts" entry to `app/(main)/_components/constants.ts` (the slot freed by removing Charts), pointing to `/accounts`, with an appropriate icon.
- **Route:** `app/(main)/accounts/page.tsx` rendering an accounts manager component under `app/(main)/accounts/_components/`.
- **Read query:** reuse the existing `getuserdata { userbrokers { ... } }` resolver (no new top-level query resolver needed), selecting only account fields: `id label metaAccountId metaApiTokenLast4 hasToken isActive status`.
- **List:** the user's accounts as rows/cards — `label`, `metaAccountId`, token shown as `••••{metaApiTokenLast4}` (or "no token set" when `hasToken` is false), `status`/`isActive`, with **Edit** and **Delete** actions.
- **Add Account form:** `label`, MetaAPI account ID, and token (an input of `type="password"`). On submit → `AddAccount`.
- **Edit form:** same fields prefilled (label, account id); the token field is **empty with placeholder "leave blank to keep current"** — submitting blank omits `metaApiToken` so the stored token is preserved.
- **Mutations** live in a new `GraphQL/accountControls.ts` module using Apollo variables (mirrors `GraphQL/strategyControls.ts`): `ADD_ACCOUNT`, `UPDATE_ACCOUNT`, `DELETE_USER_BROKER`. Handlers route through `client.mutate` with `fetchPolicy: "no-cache"`, toast on success/failure, and `middleware(err)` on catch — matching the existing dashboard control pattern.
- **Types:** extend the account/`UserExchangeSetProps` TypeScript interface in `types.ts` with `label`, `metaAccountId`, `metaApiTokenLast4`, `hasToken`.

## Data flow

```
Add/Edit account:
  Accounts page form
    → AddAccount / UpdateAccount mutation (token in the request, over HTTPS in prod)
      → backend encrypt_token(token) → store ciphertext + last4
      → return account WITHOUT token
    → list refetches; token shows ••••last4

Read accounts:
  getuserdata { userbrokers { ... } }  (existing resolver, new fields selected)
    → UserBrokerType returns label, metaAccountId, metaApiTokenLast4, hasToken
    → token ciphertext is never decrypted on a read path
```

## Error handling

- **Missing `FIELD_ENCRYPTION_KEY`:** `encrypt_token` raises `RuntimeError`; `AddAccount`/`UpdateAccount` catch it and return `Response="Server encryption key not configured"` rather than 500-ing. (Surfaced to the user as an error toast.)
- **Account not found / not owned:** mutation returns a clear `Response` and a null account; no information leak about other users' accounts.
- **Empty token on update:** treated as "leave unchanged", not as "clear the token".
- **Decrypt failure** (corrupt ciphertext / rotated key): only relevant to later sub-projects; A never decrypts on read, so it cannot fail a read. `decrypt_token` will propagate the Fernet error to its caller (the runner, in C).

## Testing

Backend (`apis/tests.py`, Django `TestCase`, SQLite):

- `crypto` round-trip: `decrypt_token(encrypt_token(x)) == x`; empty string handled; missing key raises.
- `AddAccount`: creates a `UserBroker` for the user; `meta_api_token_enc` is non-empty and `!=` plaintext; `meta_api_token_last4` equals the last 4; the returned type has no token field/value.
- `UpdateAccount`: changing `label` only leaves the token unchanged; providing a new token re-encrypts and updates `last4`; omitting the token preserves it.
- `UserBrokerType`: `hasToken` reflects presence; `metaApiTokenLast4` is exposed; there is no resolvable token field.
- Tests set `FIELD_ENCRYPTION_KEY` to a generated Fernet key in setup.

Frontend: `tsc --noEmit` + `next build` (repo's established verification; no JS test runner). Manual: add an account, see it listed with `••••last4`; edit label without retyping token; edit replacing the token; delete.

## Security notes

- Token is encrypted at rest (Fernet) and **write-only** over the API; a DB read (even of the now-internet-exposed DB) yields only ciphertext + last4.
- `FIELD_ENCRYPTION_KEY` must be set in each environment's env (local `.env`, and the prod box) and kept out of git.
- The `meta_account_id` is treated as a non-secret identifier (stored plaintext, returned by the API).

## Open follow-ups (not in A)

- Sub-project B: marketplace + deploy (fix `AddStrategy`, which references the nonexistent `userbroker.broker.name`).
- Sub-project C: runner reads per-account decrypted token; `metaapi_client` per-account refactor.
- Optional later: a "test connection" mutation that validates credentials against MetaAPI before saving.
