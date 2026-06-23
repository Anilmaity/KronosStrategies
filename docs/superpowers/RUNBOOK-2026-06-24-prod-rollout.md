# Prod Rollout Runbook — Accounts / Marketplace / Per-Account Trading (2026-06-24)

Turnkey execution guide for the operator-gated production steps. Companion to
`HANDOFF-2026-06-22-accounts-marketplace-perAccount.md`. All **code** is
verified and pushed; the steps below are the **production** actions a Claude
Code session could **not** run autonomously (the harness safety layer blocks
direct prod-DB and SSH access without explicit per-target approval).

---

## Status as of 2026-06-24 (what I verified this session)

| Area | State | Evidence |
|---|---|---|
| KronosStrategies branch `research/btc-edge` | pushed, current | `8916456` pushed to Bitbucket |
| KronosStrategies tests | **288 pass** | `pytest tests/` |
| C2 per-account client region hardening | present (committed) | `strategies/shared/metaapi_client.py` has `META_REGION`/double-label provisioning |
| position_manager close-path region hardening | **committed this session** | `8916456` |
| Kronos_Backend branch `fix/pnl-short-positions` | pushed, current | nothing unpushed |
| Backend migration `0005` | **trimmed, additive-only** (4 AddField, `default=''`) — safe | `apis/migrations/0005_rename_...and_more.py` |
| Backend tests | **39 pass** | `manage.py test apis` |
| Frontend `main` | pushed → Netlify deployed | nothing unpushed; `url.ts` → `https://app.algorobos.com` |

**Current prod state (inferred):** Frontend `/accounts` + `/marketplace` pages
are LIVE but call mutations the **un-deployed** backend lacks → those two pages
are broken until the backend is deployed + DB migrated. **Core live trading is
unaffected** (runner still on the old env-based entry path; C1/C2 not deployed).

**Blocker:** the automated session could not connect to the prod DB or SSH to
`algorobos` (safety classifier denied both). Run the steps below yourself, or
grant explicit permission and re-run the agent.

---

## Pre-flight facts

- **Runner+backend box:** Lightsail `algorobos` — `13.126.204.82`, user `ubuntu`, Ubuntu 22.04. Backend runs as bind-mounted `manage.py runserver` (autoreload). Served at `https://app.algorobos.com`.
- **Live app DB:** Lightsail `kronos-strategies-db` — db `tsdb`, user `dbmasteruser`, port `5432`, publicly accessible. Host: `ls-c3002c4cc96130d24250133c280823179d61a1da.czomeckmiuze.ap-south-1.rds.amazonaws.com`.
- **AWS creds:** `KronosStrategies/.env_aws` (user `anil`, acct `086769945463`, region `ap-south-1`). DB master password is fetched live (below) — do not hard-code it.
- **DB master password (run when needed):**
  ```bash
  aws lightsail get-relational-database-master-user-password \
    --relational-database-name kronos-strategies-db \
    --query masterUserPassword --output text --region ap-south-1
  ```
- **Backend reads DB from env:** `DB_NAME DB_USER DB_PASSWORD DB_HOST DB_PORT`.
- **Runner reads DB from env:** same `DB_*` (+ `DB_SSLMODE`), plus `META_ACCOUNT_ID`, `META_API_TOKEN`, `FIELD_ENCRYPTION_KEY`, `META_REGION` (optional).

> Order matters. Do **1 → 2 → 3 → 4 → 5**. In particular **Step 4 (C1) must
> finish before Step 5 (C2) goes live**, or every live account will start
> REFUSING trades (no env fallback by design) and trading halts.

---

## Step 1 — Apply migration `0005` to the live DB

Additive only (4 nullable-with-default columns). Safe to apply ahead of the
backend deploy; the currently-deployed `0004` backend ignores them.

Run locally from `Kronos_Backend` (no SSH needed — DB is public):

```bash
cd /c/Projects/PycharmProjects/personal/Kronos_Backend
export AWS_ACCESS_KEY_ID=...   # from .env_aws
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=ap-south-1
export DB_HOST=ls-c3002c4cc96130d24250133c280823179d61a1da.czomeckmiuze.ap-south-1.rds.amazonaws.com
export DB_USER=dbmasteruser
export DB_NAME=tsdb
export DB_PORT=5432
export DB_PASSWORD="$(aws lightsail get-relational-database-master-user-password --relational-database-name kronos-strategies-db --query masterUserPassword --output text)"

# Pre-check (read-only): expect 0004 applied, 0005 NOT.
.venv/Scripts/python.exe manage.py showmigrations apis

# Apply:
.venv/Scripts/python.exe manage.py migrate apis 0005

# Verify: 0005 now shows [X]
.venv/Scripts/python.exe manage.py showmigrations apis
```

**Rollback** (only if needed): `manage.py migrate apis 0004` — drops the 4 new
columns (no data unless Step 4 already ran; if it did, you'd lose the encrypted
tokens stored on accounts, which can be re-migrated from env via Step 4).

---

## Step 2 — Generate + set `FIELD_ENCRYPTION_KEY` (same value in 3 places)

Generate ONCE:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set the **identical** value as env on all three:
1. Backend box (`algorobos`) — backend process env.
2. Runner box (`algorobos`) — runner/compose env.
3. `position_manager` — its process env.

Without it: `AddAccount` (backend) and the runner refuse. Treat it as a secret —
keep it out of git. If you rotate it later, anything already encrypted with the
old key can't be decrypted, so re-run Step 4 after a rotation.

---

## Step 3 — Deploy the backend

On `algorobos`, pull `fix/pnl-short-positions` and ensure `FIELD_ENCRYPTION_KEY`
is in the backend env, then let the autoreload pick it up (or restart the
service / container). After deploy, the live backend will SELECT the new
`UserBroker` columns — which is why Step 1 must already be applied.

**Verify:** open `https://app.algorobos.com` `/accounts` and `/marketplace` — the
pages should now load (mutations resolve). `manage.py check` clean on the box.

---

## Step 4 — C1: migrate env creds into existing accounts (so they don't refuse)

The script is **dry-run by default** and needs explicit `UserBroker` ids.

First, list current accounts to choose the live ones:
```sql
-- against the live DB (db tsdb)
SELECT id, label, broker_id, meta_account_id,
       (meta_api_token_enc <> '') AS has_token
FROM apis_userbroker ORDER BY id;
```

Then, from `KronosStrategies/strategies/` with `DB_*` + `META_ACCOUNT_ID` +
`META_API_TOKEN` (the currently-live env creds) + `FIELD_ENCRYPTION_KEY` set:
```bash
cd /c/Projects/PycharmProjects/personal/KronosStrategies/strategies
# DRY RUN — shows what it would write, no DB change:
python -m db.migrate_env_creds_to_accounts <broker-id> [<broker-id> ...]
# APPLY:
python -m db.migrate_env_creds_to_accounts <broker-id> [<broker-id> ...] --commit
```
Target the broker id(s) currently trading on the env account so they carry the
same creds forward. **Verify:** re-run the SQL above — `has_token = true` and
`meta_account_id` populated for each migrated account.

---

## Step 5 — C2: cut the runner over to per-account entry (live money — last)

1. Deploy the runner from `research/btc-edge` (current HEAD `8916456`) on
   `algorobos`, with `FIELD_ENCRYPTION_KEY` set and **`DRY_RUN=true`**.
2. Watch logs through a few signals. Confirm **every active account resolves a
   client** — look for `place_market_order` dry-run lines, and confirm there is
   **no** `REJECTED no_account_credentials` / "refusing" for any active account.
   (If you see a refusal, an account is missing creds → fix in Step 4 first.)
3. Set the regional host correctly: either set `META_REGION=<region>` per the
   account, or rely on the `new-york` default (the demo account's region). A
   wrong region surfaces as connect/host errors in the dry-run.
4. Flip **`DRY_RUN=false`** and restart. Watch the first real entry place
   on the per-account client.

**Rollback:** set `DRY_RUN=true` (or redeploy the previous runner image/commit)
to stop placing real orders immediately.

---

## Step 6 — Pending feature request (after 1–5 are green)

- Add the new account via the **Accounts UI** (`/accounts`): MetaAPI account id
  `5216074f-3607-4285-967e-1383632f4815`; token was provided in chat — **keep it
  out of git**, paste it in the UI only.
- Deploy **"Neymar Telegram Copy"** to that account via the **Marketplace**
  (`/marketplace`). This is the first real end-to-end use of A+B+C.

---

## Not started (separate work)

- **Sub-project C3** — per-account close (`position_monitor.close_position_by_id`
  + remove the env globals in `position_manager/shared/metaapi_client.py`). The
  region hardening committed this session (`8916456`) is C3-prep; the actual
  per-account close routing is still to be designed/built like C1/C2.
