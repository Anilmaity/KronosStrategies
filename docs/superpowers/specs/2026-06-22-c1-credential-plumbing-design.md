# Runner Credential Plumbing + Env-Cred Migration (Sub-project C1) — Design Spec

**Date:** 2026-06-22
**Status:** Approved (design); pending implementation plan
**Author:** Anil + Claude

## Context

Sub-project **C** makes the live runner trade each deployed strategy on **its account's** MetaAPI credentials instead of the single env-var account. C is large (two repos/services, entry path, close path, key distribution, a data migration), so it is decomposed:

- **C1 — Credential plumbing + env-cred migration (this spec).** Make the stored creds *readable* by the runners and *populate* existing accounts with the current env creds. **No trading behavior change.**
- **C2 — Per-account entry.** Refactor `metaapi_client` to per-account creds; `entry_manager` opens on the right account and **refuses to open** if the account has no creds.
- **C3 — Per-account close.** Same for `position_monitor.close_position_by_id` (SL/TP exits).

This spec covers **only C1**.

Builds on Sub-project A, which added the DB columns (`meta_account_id`, `meta_api_token_enc`, `meta_api_token_last4`, `label`) to `apis_userbroker` via migration `0005`, with the token Fernet-encrypted under env `FIELD_ENCRYPTION_KEY`. C1 assumes `0005` is (or will be) applied to the live DB.

### Current state (as explored)

- `strategies/shared/metaapi_client.py`: module globals `_TOKEN = getenv("META_API_TOKEN")`, `_ACCOUNT = getenv("META_ACCOUNT_ID")`; `load_dotenv()` at import. Used by `entry_manager.py` (`place_market_order`) and `position_manager/position_monitor.py` (`close_position_by_id`).
- SQLAlchemy `UserBroker` model exists in **both** `strategies/shared/models.py` and `position_manager/shared/models.py` (`__tablename__ = f"{APP_PREFIX}_userbroker"`), with `api_key`, `margin_available`, `margin_used`, `status`, `is_active`, `last_updated`, `user_id` — but **not** the new credential columns. Imports include `Column, String` (not `Text`).
- Neither runner repo has crypto/decrypt code or references `FIELD_ENCRYPTION_KEY`.
- Tests use **pytest** in the repo-root `tests/` directory (`conftest.py`, `test_*.py`).
- `cryptography` is likely **not** installed in the runner venvs/containers (it was added only to the backend in A).

## Goal

After C1: both runner repos' ORM can read `meta_account_id` and `meta_api_token_enc`; both have a Fernet `decrypt_token` helper keyed by the **same** `FIELD_ENCRYPTION_KEY` as the backend; and a one-time script has (optionally) populated designated existing accounts with the current env credentials (account id + encrypted token). Trading still uses the env globals — **no behavior change**.

## Non-goals (out of scope for C1)

- Any change to `entry_manager` / `position_monitor` / `metaapi_client` trading logic (C2/C3).
- Refusing to trade when creds are missing (C2/C3).
- Adding `label` / `meta_api_token_last4` to the runner ORM (the runner only needs `meta_account_id` + `meta_api_token_enc`).

## Architecture

Three independent, low-risk units:

1. **ORM columns** (both repos): declare the two credential columns so SELECTs can read them.
2. **Crypto helper** (both repos): Fernet encrypt/decrypt keyed by `FIELD_ENCRYPTION_KEY`.
3. **Migration script** (`strategies/`): one-time, explicit-target, dry-run-by-default population of existing accounts from env creds.

### 1. ORM columns

In `strategies/shared/models.py` and `position_manager/shared/models.py`, add `Text` to the `from sqlalchemy import (...)` line, and add to `class UserBroker`:

```python
    meta_account_id    = Column(String(120), default="")
    meta_api_token_enc = Column(Text, default="")
```

These are read-only as far as the runner is concerned (the backend writes them). No SQLAlchemy migration system is in use here — the columns already exist in the DB from `0005`; this just declares them to the ORM. Existing rows return `""` defaults if the DB value is null.

### 2. Crypto helper

Create `strategies/shared/crypto.py`:

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

Create `position_manager/shared/crypto.py` with the same `_fernet` + `decrypt_token` (it does not need `encrypt_token`, but including both for symmetry is acceptable).

- The byte-for-byte identical helper is intentionally duplicated because the two repos are separate deployables (same pattern as the duplicated `shared/models.py`).
- `FIELD_ENCRYPTION_KEY` must be the **same value** set for the backend, added to each runner box's `.env`. A token encrypted by the backend (A) must decrypt here; the migration script (C1) encrypts with the same key so C2/C3 can decrypt.
- Add `cryptography` to each runner repo's dependency manifest (`requirements.txt` / Dockerfile) and install it in the venvs/containers.

### 3. One-time env-cred migration script

Create `strategies/db/migrate_env_creds_to_accounts.py` (a tracked location, not `_scratch/`):

- Reads from env: `META_API_TOKEN`, `META_ACCOUNT_ID`, `FIELD_ENCRYPTION_KEY`, and the DB connection (same env the runner uses).
- Accepts **explicit target `UserBroker` IDs** as CLI arguments (positional). It never updates accounts that were not named.
- Flags: `--apply` to write (default is **dry-run**, which prints the intended changes and writes nothing).
- For each target id: load the `UserBroker`; set `meta_account_id = META_ACCOUNT_ID` and `meta_api_token_enc = encrypt_token(META_API_TOKEN)`; on `--apply`, commit. Dry-run prints `id, label/email, would-set account_id=<id>, token=<encrypted-len> (last4 <xxxx>)` without committing.
- Idempotent: re-running sets the same values; safe to run twice.
- Refuses to run if `META_API_TOKEN` / `META_ACCOUNT_ID` / `FIELD_ENCRYPTION_KEY` are unset (clear error, exit non-zero).
- Prints a summary: how many targets updated/skipped (not found).

Usage:
```
# dry run (default) — shows what it would do
python -m db.migrate_env_creds_to_accounts <broker-id-1> <broker-id-2>
# apply
python -m db.migrate_env_creds_to_accounts <broker-id-1> <broker-id-2> --apply
```

### 4. No trading change

`entry_manager.py`, `position_monitor.py`, and `metaapi_client.py` trading code are **not** modified in C1. After C1, the runner behaves exactly as before (env globals). A regression check confirms imports still resolve and the dry-run path doesn't touch live data.

## Data flow

```
Backend (A):  AddAccount/UpdateAccount → encrypt(token, KEY) → apis_userbroker.meta_api_token_enc
Migration (C1): env META_* → encrypt(token, KEY) → designated apis_userbroker rows
Runner ORM (C1): SELECT meta_account_id, meta_api_token_enc  (readable; not yet used for trading)
Decrypt helper (C1): decrypt_token(enc, KEY) → plaintext  (available for C2/C3)
```

## Error handling

- **Missing `FIELD_ENCRYPTION_KEY`** in `_fernet()`: raises `RuntimeError`. The migration script catches startup-config errors and exits non-zero with a clear message; the runner does not call crypto in C1 so it is unaffected.
- **Migration target not found:** the script reports the id as "not found" and continues with the others; exit code reflects whether all targets were found.
- **Null DB values:** ORM defaults make `meta_account_id` / `meta_api_token_enc` read as `""`.

## Testing

`strategies/` (pytest, in `tests/`):

- `test_crypto.py`: `decrypt_token(encrypt_token(x)) == x`; empty passthrough; missing-key raises `RuntimeError` (set `FIELD_ENCRYPTION_KEY` via `Fernet.generate_key()` in the test; monkeypatch/env).
- `test_userbroker_columns.py`: the `UserBroker` model has `meta_account_id` and `meta_api_token_enc` attributes (and they map to the expected column names).
- `test_migrate_env_creds.py`: with a fixture/sqlite or mocked session, dry-run does not commit; `--apply` writes `meta_account_id` and a non-plaintext `meta_api_token_enc` that decrypts back to the env token; re-run is idempotent; unset env → non-zero exit.

`position_manager/`: `test_crypto.py` round-trip (mirror), and the `UserBroker` column attributes.

(If the runner test suites cannot easily spin a DB, the migration-script test may use a SQLAlchemy SQLite in-memory engine or a mocked session that records `.commit()` calls; the crypto and column tests need no DB.)

## Security / safety notes

- The same `FIELD_ENCRYPTION_KEY` lives on the backend box and both runner boxes; it is never in the DB or git. A DB leak alone still yields only ciphertext.
- The migration writes real credentials into prod account rows; it is **dry-run by default**, requires **explicit target ids**, and requires `--apply`. Run it against the live DB only deliberately (same prod-DB gating as A's migration).
- C1 introduces **no** path that trades on a different account — trading remains on the env globals until C2/C3.

## Open follow-ups (C2 / C3)

- C2: `MetaApiClient(account_id, token)` per-account refactor; `entry_manager` loads the deployed account's creds via `decrypt_token`, refuses to open if absent.
- C3: `position_monitor` closes on the position's account creds.
- Operational: set `FIELD_ENCRYPTION_KEY` on both runner boxes; install `cryptography`; run the migration for the live accounts before enabling C2/C3.
