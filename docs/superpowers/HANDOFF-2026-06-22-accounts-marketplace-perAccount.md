# Work Handoff — Accounts / Marketplace / Per-Account Trading (2026-06-22)

A single long session. This is the durable reference for everything built and what remains operationally.

## TL;DR

Built a three-part feature — **add trading accounts (with encrypted MetaAPI creds)**, a **strategy marketplace + deploy**, and **per-account trade routing** — plus several fixes found along the way. All *code* is committed, tested, and pushed. The remaining work is **operator-gated production steps** (a DB migration, an encryption key, deploys) that the automated safety layer (and lack of box access) prevents an agent from doing.

## Repos & branches (all pushed)

- **KronosStrategies** (this repo) — branch `research/btc-edge` → Bitbucket. Specs/plans under `docs/superpowers/`; runner code under `strategies/` + `position_manager/`.
- **Kronos_Backend** — branch `fix/pnl-short-positions` → GitHub. Django + Graphene.
- **Frontend** (`Kronos/Kronos App/kronos_frontend`) — branch `main` → Bitbucket → **Netlify auto-deploys on push**.

## Key infra facts (discovered this session)

- The **live app DB** is the Lightsail managed Postgres **`kronos-strategies-db`**, host `ls-c3002c4cc96130d24250133c280823179d61a1da.czomeckmiuze.ap-south-1.rds.amazonaws.com`, user `dbmasteruser`, database **`tsdb`**, port 5432, **publicly accessible (left ON per operator request)**. NOT the `onvya_database`/`kronos` DB that the `.env_aws` `DATABASE_URL` pointed at (that hosts other apps).
- Admin AWS key for that account (user `anil`, acct `086769945463`) lives in `KronosStrategies/.env_aws`. Lightsail DB master password is fetched via `aws lightsail get-relational-database-master-user-password --relational-database-name kronos-strategies-db`.
- The runner box is Lightsail instance **`algorobos`** (`13.126.204.82`); backend runs there (bind-mounted `manage.py runserver`).
- Prod Postgres is **v18.4** (matters for `pg_dump` — needs an 18.x client).

## What shipped (committed + pushed)

### Pre-feature fixes
- Live strategy controls: truthful Exit toast (`Ok` flag, timeouts ≠ success), `SetUserStrategyMultiplier` FE fix, centralized `GraphQL/strategyControls.ts`. (Backend + FE.)
- Dashboard data fixes: `formatCapital` NaN-guard (`$ NaNCr`→`$ 0`); broker `name`/`accountHolderName` resolver (`(undefined)` fix); fetch `strategy.capitalRequired` + broker fields; poll interval 1s→15s. Backend `UserBrokerType` got a `name`/`accountHolderName` resolver; `resolve_label` fixed (was returning nonexistent `unique_code`).
- Removed the Charts nav tab.

### Sub-project A — Accounts with credentials (Backend + FE)
- `UserBroker` gains `label`, `meta_account_id`, `meta_api_token_enc` (Fernet ciphertext), `meta_api_token_last4`. Migration **`0005`** (TRIMMED to only the 4 AddField ops — the auto-generated version bundled risky index renames + a broken `api_key` default; do NOT un-trim it).
- `apis/crypto.py` (Fernet, env `FIELD_ENCRYPTION_KEY`). `UserBrokerType` exposes `hasToken`, hides the ciphertext (token is write-only). `AddAccount` / `UpdateAccount` mutations (user-scoped, encrypt on save, `api_key` set explicitly because the model default is broken). `DeleteUserBroker` made user-scoped (was admin-only).
- FE: `/accounts` page + nav, `GraphQL/accountControls.ts`, masked `••••last4`, add/edit (blank token = keep)/delete (with confirm).
- Specs/plan: `docs/superpowers/specs/2026-06-22-accounts-credentials-design.md`, `docs/superpowers/plans/2026-06-22-accounts-credentials.md`. 35 backend tests pass.

### Sub-project B — Marketplace + Deploy (Backend + FE)
- Repaired `AddStrategy` (removed broken `broker_name=userbroker.broker.name`; sets `deployed=True`/`is_active=True`; user-scopes the account; keeps dup guard).
- FE: `/marketplace` page + nav, `GraphQL/marketplaceControls.ts`, cards, account-picker deploy, deployed badges, multiplier, runner-caveat banner.
- Specs/plan: `2026-06-22-marketplace-deploy-design.md` / `.md`. 39 backend tests pass.

### Sub-project C1 — Runner credential plumbing (KronosStrategies)
- `meta_account_id`/`meta_api_token_enc` columns on the SQLAlchemy `UserBroker` in **both** `strategies/shared/models.py` and `position_manager/shared/models.py`.
- `strategies/shared/crypto.py` + `position_manager/shared/crypto.py` (Fernet decrypt/encrypt, env `FIELD_ENCRYPTION_KEY`). `cryptography` added to both requirements.
- `strategies/db/migrate_env_creds_to_accounts.py` — dry-run-by-default, explicit target `UserBroker` ids, `--commit` to write the env creds (encrypted) into accounts. No trading change.
- Specs/plan: `2026-06-22-c1-credential-plumbing-design.md` / `.md`. 6 pytest tests.

### Sub-project C2 — Per-account entry (KronosStrategies) — live-money
- `strategies/shared/metaapi_client.py`: new `MetaApiClient(account_id, token)` class (per-account `place_market_order`/`_get_symbol_spec`/`_trading_url`/`_headers`, own region/spec cache) + `client_for_broker(session, user_broker_id)` (loads broker, decrypts token, caches by account_id; returns None → refuse). Module-level env `place_market_order` REMOVED. `close_position_by_id` + module helpers KEPT (C3 removes them).
- `entry_manager.place_entry` opens on the per-account client; **refuses** (REJECTED `no_account_credentials`) if the account has no usable creds — never falls back to the env account. `micro_scalper` needed no change (doesn't place orders directly).
- Specs/plan: `2026-06-22-c2-per-account-entry-design.md` / `.md`. 9 pytest tests (incl. C1 crypto).

## Decisions locked in

- Token: encrypted at rest + **write-only** in the API (masked `••••last4`).
- Deploy goes **live immediately** (`deployed=True`).
- Per-account rollout: **migrate the env creds into existing accounts first, then refuse to trade if an account has no creds** (no silent env fallback).
- C2 = full cutover of the entry path (env entry path removed).

## REMAINING — operator-gated production steps (in order)

1. **Apply migration `0005` to the live DB** (adds the 4 `UserBroker` columns). The deployed backend will break against the live DB until this is applied (the ORM selects the new columns). From `Kronos_Backend` with live DB env + the Lightsail password:
   `./.venv/Scripts/python.exe manage.py migrate apis`
2. **Generate + set `FIELD_ENCRYPTION_KEY`** (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`) — the **same value** on: the backend box, the `algorobos`/runner box, and `position_manager`. Without it, `AddAccount` and the runner refuse.
3. **Deploy backend** (the box's git pull/restart) so the Accounts/Marketplace mutations + resolvers exist. (Frontend already auto-deploys via Netlify; until the backend is deployed, the new FE pages call mutations the live backend lacks.)
4. **C1 migration**: from `strategies/`, `python -m db.migrate_env_creds_to_accounts <existing-broker-ids> --commit` to populate currently-live accounts with the env creds (so they don't start refusing once C2 deploys).
5. **C2 rollout**: deploy the runner with `DRY_RUN=true` first, confirm every active account resolves a client (no "refusing" in logs), then `DRY_RUN=false`.

## Pending feature requests (this session, not yet done — blocked on the above)

- **Add a new account** (MetaAPI account id `5216074f-3607-4285-967e-1383632f4815`; token provided in chat — keep it OUT of git; add it via the Accounts UI once steps 1–3 are done) and **deploy "Neymar Telegram Copy"** to it via the Marketplace. This is the first real use of A+B and needs the migration + encryption key + backend deploy first.

## Test status

Backend: 39 pass. Strategies (pytest): C1+C2 = 9 pass. Frontend: `tsc` + `next build` clean. All reviewed (subagent code reviews; caught + fixed a critical `api_key` uniqueness bug in A and a `label`/`unique_code` bug).

## Not started

- **Sub-project C3** — per-account close (`position_monitor.close_position_by_id` + remove the env globals). Design/plan/build the same way.
