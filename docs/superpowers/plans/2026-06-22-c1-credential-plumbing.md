# Runner Credential Plumbing + Env-Cred Migration (Sub-project C1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the two runner repos able to read & decrypt per-account MetaAPI credentials, and provide a dry-run-by-default script to migrate the current env credentials into designated accounts. **No trading behavior change.**

**Architecture:** Add `meta_account_id`/`meta_api_token_enc` to the SQLAlchemy `UserBroker` model in `strategies/` and `position_manager/`; add a Fernet crypto helper to both (keyed by env `FIELD_ENCRYPTION_KEY`, same key as the backend); and a one-time `db/` script that encrypts the env token into explicitly-named accounts. Trading still uses the env globals.

**Tech Stack:** Python, SQLAlchemy, `cryptography` (Fernet), pytest (strategies repo). `position_manager` has no pytest harness — verified via smoke checks.

**Spec:** `docs/superpowers/specs/2026-06-22-c1-credential-plumbing-design.md`

**Repo paths (absolute):** everything is in `C:/Projects/PycharmProjects/personal/KronosStrategies` (current git branch `research/btc-edge` — commit there; do NOT branch). Run from the repo root unless noted.

**Conventions confirmed:**
- pytest: `tests/conftest.py` puts the REPO ROOT on `sys.path`; tests import as `from strategies.shared.X import ...`. Run: `./.venv/Scripts/python.exe -m pytest tests/<file> -q`.
- `db/` scripts: top of file does `sys.path.insert(0, <strategies dir>)` then `from shared.X import ...`, run as `python -m db.<name>` from `strategies/`, dry-run by default with a `--commit` flag (e.g. `db/disable_standalone_kronos_legs.py`).
- `strategies/shared/models.py`: `from sqlalchemy import (... Column ... String ...)` (no `Text`), `Session = sessionmaker(bind=engine)`, `create_engine` is lazy (imports without a live DB). `class UserBroker(BaseModel)`. Same shape in `position_manager/shared/models.py` (UserBroker ~line 83).

---

## File Structure

**strategies/**
- Modify: `strategies/requirements.txt` — add `cryptography`.
- Create: `strategies/shared/crypto.py` — Fernet `encrypt_token`/`decrypt_token`.
- Modify: `strategies/shared/models.py` — add `Text` import + two `UserBroker` columns.
- Create: `strategies/db/migrate_env_creds_to_accounts.py` — env→account migration.
- Create tests: `tests/test_runner_crypto.py`, `tests/test_userbroker_creds_columns.py`, `tests/test_migrate_env_creds.py`.

**position_manager/**
- Modify: `position_manager/requirements.txt` — add `cryptography`.
- Create: `position_manager/shared/crypto.py` — Fernet `decrypt_token` (+ `encrypt_token` for symmetry).
- Modify: `position_manager/shared/models.py` — add `Text` import + two `UserBroker` columns.

---

## Task 1: strategies — `cryptography` dep + crypto helper (pytest TDD)

**Files:**
- Modify: `strategies/requirements.txt`
- Create: `strategies/shared/crypto.py`
- Test: `tests/test_runner_crypto.py`

- [ ] **Step 1: Add dependency + install**

Append `cryptography==42.0.5` to `strategies/requirements.txt`. Install into the repo venv:
```
./.venv/Scripts/python.exe -m pip install cryptography==42.0.5
```
Verify: `./.venv/Scripts/python.exe -c "from cryptography.fernet import Fernet; print('ok')"` prints `ok`.

- [ ] **Step 2: Write the failing test**

Create `tests/test_runner_crypto.py`:
```python
import os

import pytest
from cryptography.fernet import Fernet


def _set_key(monkeypatch):
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode())


def test_round_trip(monkeypatch):
    _set_key(monkeypatch)
    from strategies.shared.crypto import encrypt_token, decrypt_token
    cipher = encrypt_token("tok-SECRET-1234")
    assert cipher != "tok-SECRET-1234"
    assert decrypt_token(cipher) == "tok-SECRET-1234"


def test_empty_passthrough(monkeypatch):
    _set_key(monkeypatch)
    from strategies.shared.crypto import encrypt_token, decrypt_token
    assert encrypt_token("") == ""
    assert decrypt_token("") == ""


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("FIELD_ENCRYPTION_KEY", raising=False)
    from strategies.shared.crypto import encrypt_token
    with pytest.raises(RuntimeError):
        encrypt_token("x")
```

- [ ] **Step 3: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_runner_crypto.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'strategies.shared.crypto'`.

- [ ] **Step 4: Create `strategies/shared/crypto.py`**

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

- [ ] **Step 5: Run to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_runner_crypto.py -q`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
cd "C:/Projects/PycharmProjects/personal/KronosStrategies"
git add strategies/requirements.txt strategies/shared/crypto.py tests/test_runner_crypto.py
git commit -m "feat(c1): Fernet crypto helper for the strategies runner

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: strategies — `UserBroker` ORM credential columns (pytest TDD)

**Files:**
- Modify: `strategies/shared/models.py`
- Test: `tests/test_userbroker_creds_columns.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_userbroker_creds_columns.py`:
```python
def test_userbroker_has_credential_columns():
    from strategies.shared.models import UserBroker
    cols = set(UserBroker.__table__.columns.keys())
    assert "meta_account_id" in cols
    assert "meta_api_token_enc" in cols
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_userbroker_creds_columns.py -q`
Expected: FAIL — assertion error (columns absent).

- [ ] **Step 3: Add `Text` import + the two columns**

In `strategies/shared/models.py`:
(a) Add `Text` to the sqlalchemy import. Change the line:
```python
from sqlalchemy import (ARRAY, Boolean, Column, Date, DateTime, Float,
```
to include `Text` — i.e. add `Text` into the imported names (e.g. after `String,`):
```python
from sqlalchemy import (ARRAY, Boolean, Column, Date, DateTime, Float,
                        ForeignKey, Integer, Numeric, String, Text, create_engine,
```
(Match the existing wrapped-import formatting; ensure `Text` is added exactly once and the import still parses.)

(b) In `class UserBroker(BaseModel):`, after the `margin_used` column line, add:
```python
    meta_account_id    = Column(String(120), default="")
    meta_api_token_enc = Column(Text, default="")
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_userbroker_creds_columns.py -q`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
cd "C:/Projects/PycharmProjects/personal/KronosStrategies"
git add strategies/shared/models.py tests/test_userbroker_creds_columns.py
git commit -m "feat(c1): UserBroker credential columns in strategies ORM

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: strategies — env-cred migration script (pytest TDD)

**Files:**
- Create: `strategies/db/migrate_env_creds_to_accounts.py`
- Test: `tests/test_migrate_env_creds.py`

Design: a pure, DB-free helper `build_cred_payload(account_id, token)` (imports only `crypto`) is unit-tested; the DB work (`Session`, `UserBroker`) is lazily imported inside `main()` so importing the helper does not require a DB. Dry-run by default; `--commit` to write. Explicit target ids only.

- [ ] **Step 1: Write the failing test**

Create `tests/test_migrate_env_creds.py`:
```python
import os

from cryptography.fernet import Fernet


def test_build_cred_payload_encrypts(monkeypatch):
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode())
    from strategies.db.migrate_env_creds_to_accounts import build_cred_payload
    from strategies.shared.crypto import decrypt_token
    payload = build_cred_payload("acct-XYZ", "tok-PLAIN-9999")
    assert payload["meta_account_id"] == "acct-XYZ"
    assert payload["meta_api_token_enc"] != "tok-PLAIN-9999"
    assert decrypt_token(payload["meta_api_token_enc"]) == "tok-PLAIN-9999"


def test_build_cred_payload_idempotent_shape(monkeypatch):
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode())
    from strategies.db.migrate_env_creds_to_accounts import build_cred_payload
    p = build_cred_payload("a", "b")
    assert set(p.keys()) == {"meta_account_id", "meta_api_token_enc"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_migrate_env_creds.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'strategies.db.migrate_env_creds_to_accounts'`.

- [ ] **Step 3: Create the script**

Create `strategies/db/migrate_env_creds_to_accounts.py`:
```python
"""One-time: copy the env MetaAPI creds into designated accounts (encrypted).

Reads META_ACCOUNT_ID + META_API_TOKEN + FIELD_ENCRYPTION_KEY + DB_* from env,
encrypts the token, and writes meta_account_id / meta_api_token_enc onto the
named UserBroker rows. Dry-run by default; --commit to write. Explicit target
ids only — never a blanket update. Idempotent.

Run (from strategies/):
  python -m db.migrate_env_creds_to_accounts <broker-id> [<broker-id> ...]
  python -m db.migrate_env_creds_to_accounts <broker-id> --commit
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.crypto import encrypt_token  # noqa: E402  (DB-free)


def build_cred_payload(account_id: str, token: str) -> dict:
    """Pure: the column values to set for one account. Encrypts the token."""
    return {
        "meta_account_id": account_id,
        "meta_api_token_enc": encrypt_token(token),
    }


def main(target_ids: list[str], commit: bool) -> int:
    account_id = os.getenv("META_ACCOUNT_ID", "")
    token = os.getenv("META_API_TOKEN", "")
    if not account_id or not token:
        print("[ERR] META_ACCOUNT_ID / META_API_TOKEN not set")
        return 2
    if not os.getenv("FIELD_ENCRYPTION_KEY", ""):
        print("[ERR] FIELD_ENCRYPTION_KEY not set")
        return 2
    if not target_ids:
        print("[ERR] no target UserBroker ids given")
        return 2

    from shared.models import Session, UserBroker  # lazy (needs DB)

    payload = build_cred_payload(account_id, token)
    last4 = token[-4:]
    sess = Session()
    updated = missing = 0
    try:
        for bid in target_ids:
            broker = sess.query(UserBroker).filter_by(id=bid).first()
            if broker is None:
                print(f"[MISS] UserBroker {bid} not found")
                missing += 1
                continue
            print(
                f"[SET ] {bid}: meta_account_id={account_id} "
                f"token=<{len(payload['meta_api_token_enc'])} bytes, last4 {last4}>"
            )
            if commit:
                broker.meta_account_id = payload["meta_account_id"]
                broker.meta_api_token_enc = payload["meta_api_token_enc"]
                updated += 1
        if commit:
            sess.commit()
            print(f"[DONE] committed {updated} updated, {missing} missing")
        else:
            print(f"[DRY ] would update {len(target_ids) - missing}, {missing} missing "
                  f"(re-run with --commit to apply)")
    finally:
        sess.close()
    return 1 if missing else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("broker_ids", nargs="+")
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()
    raise SystemExit(main(args.broker_ids, args.commit))
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_migrate_env_creds.py -q`
Expected: PASS (2 tests). (The pure helper is testable without a DB; `main`'s DB path is exercised manually in Task 5.)

- [ ] **Step 5: Commit**

```bash
cd "C:/Projects/PycharmProjects/personal/KronosStrategies"
git add strategies/db/migrate_env_creds_to_accounts.py tests/test_migrate_env_creds.py
git commit -m "feat(c1): env-cred migration script (dry-run default, explicit targets)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: position_manager — `cryptography` dep + crypto helper + ORM columns (smoke-verified)

**Files:**
- Modify: `position_manager/requirements.txt`
- Create: `position_manager/shared/crypto.py`
- Modify: `position_manager/shared/models.py`

`position_manager` has no pytest harness; verify with smoke checks.

- [ ] **Step 1: Add dependency**

Append `cryptography==42.0.5` to `position_manager/requirements.txt`.

- [ ] **Step 2: Create `position_manager/shared/crypto.py`**

Identical to the strategies helper:
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

- [ ] **Step 3: Add `Text` import + columns to `position_manager/shared/models.py`**

(a) Add `Text` to the `from sqlalchemy import (...)` line (same as Task 2 Step 3a).
(b) In `class UserBroker(BaseModel):`, after `margin_used`, add:
```python
    meta_account_id    = Column(String(120), default="")
    meta_api_token_enc = Column(Text, default="")
```

- [ ] **Step 4: Smoke-verify**

Run (cryptography must be installed in the venv used; the repo `.venv` is shared):
```
./.venv/Scripts/python.exe -c "import os; from cryptography.fernet import Fernet; os.environ['FIELD_ENCRYPTION_KEY']=Fernet.generate_key().decode(); import sys; sys.path.insert(0,'position_manager'); from shared.crypto import encrypt_token, decrypt_token; assert decrypt_token(encrypt_token('x'))=='x'; from shared.models import UserBroker; c=set(UserBroker.__table__.columns.keys()); assert 'meta_account_id' in c and 'meta_api_token_enc' in c; print('pm smoke OK')"
```
Expected: prints `pm smoke OK`.

- [ ] **Step 5: Commit**

```bash
cd "C:/Projects/PycharmProjects/personal/KronosStrategies"
git add position_manager/requirements.txt position_manager/shared/crypto.py position_manager/shared/models.py
git commit -m "feat(c1): crypto helper + UserBroker credential columns in position_manager

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Operational — dry-run the migration against the live DB (manual)

**Files:** none (operational; live DB). This is run by the operator, like A's migration.

Prereqs: Sub-project A's migration `0005` applied to the live DB (the columns must exist); `FIELD_ENCRYPTION_KEY` set to the **same** value as the backend; `META_ACCOUNT_ID`/`META_API_TOKEN`/`DB_*` set in the env the runner uses.

- [ ] **Step 1: Identify target accounts**

The accounts currently trading on the env MetaAPI account. From earlier work these are the `UserBroker`s with live deployed strategies (e.g. `e673869c-8c56-4521-9a49-ac62f07d7da9`). Confirm the exact ids before running.

- [ ] **Step 2: Dry run**

From `strategies/` with the live env loaded:
```
python -m db.migrate_env_creds_to_accounts <broker-id-1> [<broker-id-2> ...]
```
Expected: `[SET ]` lines for each found id + `[DRY ] would update N`. No writes.

- [ ] **Step 3: Apply**

Re-run with `--commit`:
```
python -m db.migrate_env_creds_to_accounts <broker-id-1> [<broker-id-2> ...] --commit
```
Expected: `[DONE] committed N updated`.

- [ ] **Step 4: Verify in DB**

Confirm the targeted accounts now have `meta_account_id` set and a non-empty `meta_api_token_enc`, and (using the same key) that `decrypt_token` returns the env token. The accounts now hold creds; C2/C3 can later route trades to them. Trading is unchanged until C2/C3 ship.

- [ ] **Step 5: Record results**

Note which accounts were migrated. If all pass, C1 is complete and C2 (per-account entry) can begin.

---

## Self-Review notes (author)

- **Spec coverage:** ORM columns both repos (Tasks 2, 4); crypto helper both repos (Tasks 1, 4); migration script dry-run/explicit-targets/idempotent (Task 3 + 5); cryptography dep (Tasks 1, 4); no-trading-change preserved (no edits to entry_manager/position_monitor/metaapi_client). All spec sections mapped.
- **Import paths pinned:** pytest tests import `from strategies.shared.X`; the `db/` script uses `sys.path.insert` + `from shared.X` (works run-as-script and when pytest imports it, since the insert runs first and the crypto import is DB-free). DB (`Session`/`UserBroker`) is lazily imported inside `main()` so the unit-testable `build_cred_payload` needs no DB.
- **Safety:** migration is dry-run by default, requires explicit ids and `--commit`; writes only the two credential columns; idempotent. Live run is operator-gated (Task 5), like A's migration.
- **position_manager** has no pytest harness, so Task 4 is smoke-verified via a single `python -c` round-trip + column check.
