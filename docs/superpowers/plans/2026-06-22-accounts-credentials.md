# Accounts with Credentials (Sub-project A) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a logged-in user create/manage trading accounts that each hold a MetaAPI account ID + API token, with the token encrypted at rest and write-only in the API (shown masked).

**Architecture:** Add credential fields to the existing `UserBroker` model with a Fernet crypto helper; expose a write-only GraphQL surface (`AddAccount`/`UpdateAccount`, user-scoped `DeleteUserBroker`, masked display fields); build a `/accounts` page in the Next.js frontend. No trading/runner change (that is Sub-project C).

**Tech Stack:** Django 5 + graphene, `cryptography` (Fernet), `python manage.py test apis`, Next.js + `@apollo/client`, `tsc`/`next build`.

**Spec:** `docs/superpowers/specs/2026-06-22-accounts-credentials-design.md`

**Repo paths (absolute):**
- Backend: `C:/Projects/PycharmProjects/personal/Kronos_Backend`
- Frontend: `C:/Projects/PycharmProjects/personal/Kronos/Kronos App/kronos_frontend`

**Backend test runner:** `./.venv/Scripts/python.exe manage.py test <dotted.path> -v 2` (run from the backend repo).

---

## File Structure

**Backend (`Kronos_Backend`)**
- Modify: `requirements.txt` — uncomment `cryptography==42.0.5`.
- Create: `apis/crypto.py` — `encrypt_token` / `decrypt_token` (Fernet, key from env `FIELD_ENCRYPTION_KEY`).
- Modify: `apis/models.py` — add `label`, `meta_account_id`, `meta_api_token_enc`, `meta_api_token_last4` to `UserBroker`.
- Create: `apis/migrations/0004_userbroker_credentials.py` — via `makemigrations` (name may differ; use whatever it generates).
- Modify: `apis/schema/types/user_broker_type.py` — exclude `meta_api_token_enc`; add `hasToken`.
- Create: `apis/schema/mutation/user/add_account.py` — `AddAccount` (auto-registered).
- Create: `apis/schema/mutation/user/update_account.py` — `UpdateAccount` (auto-registered).
- Modify: `apis/schema/mutation/user/delete_user_broker.py` — make user-scoped.
- Modify: `apis/tests.py` — tests for all of the above.

**Frontend (`kronos_frontend`)**
- Create: `GraphQL/accountControls.ts` — `ADD_ACCOUNT`, `UPDATE_ACCOUNT`, `DELETE_USER_BROKER`.
- Modify: `types.ts` — account credential fields on `UserExchangeSetProps`.
- Modify: `app/(main)/_components/constants.ts` — add "Accounts" nav entry.
- Create: `app/(main)/accounts/page.tsx` — route.
- Create: `app/(main)/accounts/_components/AccountsManager.tsx` — list + add/edit/delete UI.

Mutations auto-register: a file named `add_account.py` whose class is `AddAccount` is wired into `UserMutation` automatically by `apis/schema/mutation/user/__init__.py` (class name = each `_`-word capitalized). No schema-root edit needed.

---

## Task 1: Backend — enable `cryptography` + crypto helper

**Files:**
- Modify: `requirements.txt`
- Create: `apis/crypto.py`
- Test: `apis/tests.py`

- [ ] **Step 1: Uncomment and install cryptography**

In `requirements.txt`, change `#cryptography==42.0.5` to `cryptography==42.0.5`. Then install:
```
cd "C:/Projects/PycharmProjects/personal/Kronos_Backend"
./.venv/Scripts/python.exe -m pip install cryptography==42.0.5
```
Verify: `./.venv/Scripts/python.exe -c "from cryptography.fernet import Fernet; print('ok')"` prints `ok`.

- [ ] **Step 2: Write the failing test**

Add to the end of `apis/tests.py`:
```python
# ───────────────────────────────────────────────────────────────────────────────
# Accounts: credential crypto (2026-06-22)
# ───────────────────────────────────────────────────────────────────────────────

import os
from cryptography.fernet import Fernet


class CryptoTests(TestCase):
    def setUp(self):
        os.environ["FIELD_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

    def test_round_trip(self):
        from apis.crypto import encrypt_token, decrypt_token
        cipher = encrypt_token("super-secret-token-1234")
        self.assertNotEqual(cipher, "super-secret-token-1234")
        self.assertEqual(decrypt_token(cipher), "super-secret-token-1234")

    def test_empty_passthrough(self):
        from apis.crypto import encrypt_token, decrypt_token
        self.assertEqual(encrypt_token(""), "")
        self.assertEqual(decrypt_token(""), "")

    def test_missing_key_raises(self):
        os.environ.pop("FIELD_ENCRYPTION_KEY", None)
        from apis.crypto import encrypt_token
        with self.assertRaises(RuntimeError):
            encrypt_token("x")
```

- [ ] **Step 3: Run to verify it fails**

Run: `./.venv/Scripts/python.exe manage.py test apis.tests.CryptoTests -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'apis.crypto'`.

- [ ] **Step 4: Create `apis/crypto.py`**

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

Run: `./.venv/Scripts/python.exe manage.py test apis.tests.CryptoTests -v 2`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
cd "C:/Projects/PycharmProjects/personal/Kronos_Backend"
git add requirements.txt apis/crypto.py apis/tests.py
git commit -m "feat(accounts): Fernet crypto helper for credential encryption

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Backend — `UserBroker` credential fields + migration

**Files:**
- Modify: `apis/models.py` (the `UserBroker` class, ~line 94)
- Create: migration (generated)
- Test: `apis/tests.py`

- [ ] **Step 1: Write the failing test**

Add to the end of `apis/tests.py`:
```python
class UserBrokerCredentialFieldTests(TestCase):
    def test_fields_exist_with_defaults(self):
        us = _mk_user_strategy()
        broker = us.user_broker
        broker.label = "Primary Live"
        broker.meta_account_id = "acct-uuid-1"
        broker.meta_api_token_enc = "cipher"
        broker.meta_api_token_last4 = "1234"
        broker.save()
        broker.refresh_from_db()
        self.assertEqual(broker.label, "Primary Live")
        self.assertEqual(broker.meta_account_id, "acct-uuid-1")
        self.assertEqual(broker.meta_api_token_enc, "cipher")
        self.assertEqual(broker.meta_api_token_last4, "1234")
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe manage.py test apis.tests.UserBrokerCredentialFieldTests -v 2`
Expected: FAIL — error setting/saving unknown attribute (field does not exist).

- [ ] **Step 3: Add the fields to `UserBroker`**

In `apis/models.py`, inside `class UserBroker(BaseModel):` (after the `margin_used` line, before `def __str__`), add:
```python
    label = models.CharField(max_length=120, default="")
    meta_account_id = models.CharField(max_length=120, default="")
    meta_api_token_enc = models.TextField(default="")
    meta_api_token_last4 = models.CharField(max_length=4, default="")
```

- [ ] **Step 4: Generate the migration**

Run: `./.venv/Scripts/python.exe manage.py makemigrations apis`
Expected: creates a migration adding the four fields (note the generated filename).

- [ ] **Step 5: Run to verify it passes**

Run: `./.venv/Scripts/python.exe manage.py test apis.tests.UserBrokerCredentialFieldTests -v 2`
Expected: PASS (1 test). (The test runner applies the new migration to its throwaway DB.)

- [ ] **Step 6: Commit**

```bash
cd "C:/Projects/PycharmProjects/personal/Kronos_Backend"
git add apis/models.py apis/migrations/ apis/tests.py
git commit -m "feat(accounts): add credential fields to UserBroker

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Backend — `UserBrokerType` (hide ciphertext, add `hasToken`)

**Files:**
- Modify: `apis/schema/types/user_broker_type.py`
- Test: `apis/tests.py`

The new model fields auto-expose as `label`, `metaAccountId`, `metaApiTokenLast4` (camelCased) — good. But `meta_api_token_enc` would auto-expose as `metaApiTokenEnc` (ciphertext) — it MUST be excluded. Add `hasToken`.

- [ ] **Step 1: Write the failing test**

Add to the end of `apis/tests.py`:
```python
from apis.schema.types.user_broker_type import UserBrokerType as _UBType


class UserBrokerTypeTests(TestCase):
    def test_has_token_reflects_presence(self):
        us = _mk_user_strategy()
        broker = us.user_broker
        broker.meta_api_token_enc = ""
        self.assertFalse(_UBType.resolve_hasToken(broker, None))
        broker.meta_api_token_enc = "cipher"
        self.assertTrue(_UBType.resolve_hasToken(broker, None))

    def test_ciphertext_field_not_in_schema(self):
        field_names = set(_UBType._meta.fields.keys())
        self.assertNotIn("metaApiTokenEnc", field_names)
        self.assertNotIn("meta_api_token_enc", field_names)
        self.assertIn("hasToken", field_names)
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe manage.py test apis.tests.UserBrokerTypeTests -v 2`
Expected: FAIL — `resolve_hasToken` / `hasToken` does not exist (and `metaApiTokenEnc` IS currently in the schema).

- [ ] **Step 3: Add `hasToken` and exclude ciphertext**

In `apis/schema/types/user_broker_type.py`:

(a) Add an explicit field + resolver inside the class (next to the other graphene fields, e.g. after the `name = graphene.String()` block):
```python
    hasToken = graphene.Boolean()

    def resolve_hasToken(self, info):
        return bool(self.meta_api_token_enc)
```

(b) Update the `Meta.exclude` to also hide the ciphertext:
```python
    class Meta:
        model = UserBroker
        exclude = ("user", "userstrategy_set", "order_set", "meta_api_token_enc")
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/Scripts/python.exe manage.py test apis.tests.UserBrokerTypeTests -v 2`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
cd "C:/Projects/PycharmProjects/personal/Kronos_Backend"
git add apis/schema/types/user_broker_type.py apis/tests.py
git commit -m "feat(accounts): expose hasToken; never expose token ciphertext

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Backend — `AddAccount` mutation

**Files:**
- Create: `apis/schema/mutation/user/add_account.py`
- Test: `apis/tests.py`

- [ ] **Step 1: Write the failing test**

Add to the end of `apis/tests.py`:
```python
class AddAccountTests(TestCase):
    @staticmethod
    def _info(user):
        from types import SimpleNamespace
        return SimpleNamespace(context=SimpleNamespace(user=user))

    def setUp(self):
        os.environ["FIELD_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

    def test_creates_encrypted_account(self):
        from apis.schema.mutation.user.add_account import AddAccount
        from apis.crypto import decrypt_token
        us = _mk_user_strategy()
        user = us.user_broker.user
        res = AddAccount.mutate(
            None, self._info(user),
            label="Live A", meta_account_id="acct-1", meta_api_token="tok-ABCD1234",
        )
        self.assertEqual(res.Response, "Success")
        b = res.UserBroker
        self.assertEqual(b.label, "Live A")
        self.assertEqual(b.meta_account_id, "acct-1")
        self.assertEqual(b.meta_api_token_last4, "1234")
        self.assertNotEqual(b.meta_api_token_enc, "tok-ABCD1234")
        self.assertEqual(decrypt_token(b.meta_api_token_enc), "tok-ABCD1234")
        self.assertEqual(b.user_id, user.id)

    def test_missing_key_is_handled(self):
        from apis.schema.mutation.user.add_account import AddAccount
        os.environ.pop("FIELD_ENCRYPTION_KEY", None)
        us = _mk_user_strategy()
        user = us.user_broker.user
        res = AddAccount.mutate(
            None, self._info(user),
            label="X", meta_account_id="y", meta_api_token="z",
        )
        self.assertIn("encryption key", res.Response)
        self.assertIsNone(res.UserBroker)
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe manage.py test apis.tests.AddAccountTests -v 2`
Expected: FAIL — `ModuleNotFoundError: ... add_account`.

- [ ] **Step 3: Create `apis/schema/mutation/user/add_account.py`**

```python
import graphene

from apis.models import UserBroker
from apis.schema.utils import user_authenticate
from apis.schema.types.user_broker_type import UserBrokerType
from apis.crypto import encrypt_token


class AddAccount(graphene.Mutation):
    Response = graphene.String()
    UserBroker = graphene.Field(UserBrokerType)

    class Arguments:
        label = graphene.String(required=True)
        meta_account_id = graphene.String(required=True)
        meta_api_token = graphene.String(required=True)

    @user_authenticate
    def mutate(self, info, label, meta_account_id, meta_api_token):
        try:
            enc = encrypt_token(meta_api_token)
        except Exception:
            return AddAccount(
                Response="Server encryption key not configured", UserBroker=None
            )
        broker = UserBroker.objects.create(
            user=info.context.user,
            label=label,
            meta_account_id=meta_account_id,
            meta_api_token_enc=enc,
            meta_api_token_last4=meta_api_token[-4:],
        )
        return AddAccount(Response="Success", UserBroker=broker)
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/Scripts/python.exe manage.py test apis.tests.AddAccountTests -v 2`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
cd "C:/Projects/PycharmProjects/personal/Kronos_Backend"
git add apis/schema/mutation/user/add_account.py apis/tests.py
git commit -m "feat(accounts): AddAccount mutation (encrypts token, stores last4)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Backend — `UpdateAccount` mutation

**Files:**
- Create: `apis/schema/mutation/user/update_account.py`
- Test: `apis/tests.py`

- [ ] **Step 1: Write the failing test**

Add to the end of `apis/tests.py`:
```python
class UpdateAccountTests(TestCase):
    @staticmethod
    def _info(user):
        from types import SimpleNamespace
        return SimpleNamespace(context=SimpleNamespace(user=user))

    def setUp(self):
        os.environ["FIELD_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

    def _make(self, user):
        from apis.schema.mutation.user.add_account import AddAccount
        return AddAccount.mutate(
            None, self._info(user),
            label="Orig", meta_account_id="acct-1", meta_api_token="tok-OLD9999",
        ).UserBroker

    def test_label_only_keeps_token(self):
        from apis.schema.mutation.user.update_account import UpdateAccount
        us = _mk_user_strategy()
        user = us.user_broker.user
        b = self._make(user)
        old_enc = b.meta_api_token_enc
        res = UpdateAccount.mutate(None, self._info(user), id=str(b.id), label="Renamed")
        self.assertEqual(res.Response, "Success")
        b.refresh_from_db()
        self.assertEqual(b.label, "Renamed")
        self.assertEqual(b.meta_api_token_enc, old_enc)
        self.assertEqual(b.meta_api_token_last4, "9999")

    def test_new_token_reencrypts(self):
        from apis.schema.mutation.user.update_account import UpdateAccount
        from apis.crypto import decrypt_token
        us = _mk_user_strategy()
        user = us.user_broker.user
        b = self._make(user)
        res = UpdateAccount.mutate(
            None, self._info(user), id=str(b.id), meta_api_token="tok-NEW1111"
        )
        self.assertEqual(res.Response, "Success")
        b.refresh_from_db()
        self.assertEqual(b.meta_api_token_last4, "1111")
        self.assertEqual(decrypt_token(b.meta_api_token_enc), "tok-NEW1111")

    def test_other_users_account_not_found(self):
        from apis.schema.mutation.user.update_account import UpdateAccount
        owner = _mk_user_strategy().user_broker.user
        b = self._make(owner)
        other = _mk_user_strategy().user_broker.user
        res = UpdateAccount.mutate(None, self._info(other), id=str(b.id), label="hax")
        self.assertIn("does not exist", res.Response)
        self.assertIsNone(res.UserBroker)
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe manage.py test apis.tests.UpdateAccountTests -v 2`
Expected: FAIL — `ModuleNotFoundError: ... update_account`.

- [ ] **Step 3: Create `apis/schema/mutation/user/update_account.py`**

```python
import graphene

from apis.models import UserBroker
from apis.schema.utils import user_authenticate
from apis.schema.types.user_broker_type import UserBrokerType
from apis.crypto import encrypt_token


class UpdateAccount(graphene.Mutation):
    Response = graphene.String()
    UserBroker = graphene.Field(UserBrokerType)

    class Arguments:
        id = graphene.String(required=True)
        label = graphene.String()
        meta_account_id = graphene.String()
        meta_api_token = graphene.String()

    @user_authenticate
    def mutate(self, info, id, label=None, meta_account_id=None, meta_api_token=None):
        try:
            if info.context.user.is_superuser:
                broker = UserBroker.objects.get(id=id)
            else:
                broker = UserBroker.objects.get(id=id, user=info.context.user)
        except UserBroker.DoesNotExist:
            return UpdateAccount(Response="Account does not exist", UserBroker=None)

        if label is not None:
            broker.label = label
        if meta_account_id is not None:
            broker.meta_account_id = meta_account_id
        if meta_api_token:
            try:
                broker.meta_api_token_enc = encrypt_token(meta_api_token)
            except Exception:
                return UpdateAccount(
                    Response="Server encryption key not configured", UserBroker=None
                )
            broker.meta_api_token_last4 = meta_api_token[-4:]

        broker.save()
        return UpdateAccount(Response="Success", UserBroker=broker)
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/Scripts/python.exe manage.py test apis.tests.UpdateAccountTests -v 2`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
cd "C:/Projects/PycharmProjects/personal/Kronos_Backend"
git add apis/schema/mutation/user/update_account.py apis/tests.py
git commit -m "feat(accounts): UpdateAccount mutation (token blank = keep current)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Backend — make `DeleteUserBroker` user-scoped

**Files:**
- Modify: `apis/schema/mutation/user/delete_user_broker.py`
- Test: `apis/tests.py`

Currently `@admin_authenticate` and unscoped — a regular user cannot delete their own account, and an admin can delete any by id. Change to `@user_authenticate`, scoped to the owner (superuser may delete any).

- [ ] **Step 1: Write the failing test**

Add to the end of `apis/tests.py`:
```python
class DeleteUserBrokerScopeTests(TestCase):
    @staticmethod
    def _info(user):
        from types import SimpleNamespace
        return SimpleNamespace(context=SimpleNamespace(user=user))

    def test_owner_can_delete(self):
        from apis.schema.mutation.user.delete_user_broker import DeleteUserBroker
        from apis.models import UserBroker
        us = _mk_user_strategy()
        # delete the strategy first so the broker has no protected relations
        us.delete()
        broker = us.user_broker
        user = broker.user
        res = DeleteUserBroker.mutate(None, self._info(user), broker_id=str(broker.id))
        self.assertEqual(res.Response, "Success")
        self.assertFalse(UserBroker.objects.filter(id=broker.id).exists())

    def test_non_owner_cannot_delete(self):
        from apis.schema.mutation.user.delete_user_broker import DeleteUserBroker
        from apis.models import UserBroker
        owner_us = _mk_user_strategy()
        broker = owner_us.user_broker
        other = _mk_user_strategy().user_broker.user
        res = DeleteUserBroker.mutate(None, self._info(other), broker_id=str(broker.id))
        self.assertIn("Not Found", res.Response)
        self.assertTrue(UserBroker.objects.filter(id=broker.id).exists())
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe manage.py test apis.tests.DeleteUserBrokerScopeTests -v 2`
Expected: FAIL — `@admin_authenticate` rejects the non-superuser call (or the non-owner deletion succeeds), so assertions fail.

- [ ] **Step 3: Rewrite `delete_user_broker.py`**

Replace the entire file with:
```python
import graphene

from apis.models import UserBroker
from apis.schema.utils import user_authenticate


class DeleteUserBroker(graphene.Mutation):
    Response = graphene.String()

    class Arguments:
        broker_id = graphene.String(required=True)

    @user_authenticate
    def mutate(self, info, broker_id):
        try:
            if info.context.user.is_superuser:
                userbroker = UserBroker.objects.get(id=broker_id)
            else:
                userbroker = UserBroker.objects.get(
                    id=broker_id, user=info.context.user
                )
            userbroker.delete()
            return DeleteUserBroker(Response="Success")
        except UserBroker.DoesNotExist:
            return DeleteUserBroker(Response="Broker Not Found")
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/Scripts/python.exe manage.py test apis.tests.DeleteUserBrokerScopeTests -v 2`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the whole accounts test set + full suite**

Run:
```
./.venv/Scripts/python.exe manage.py test apis.tests.CryptoTests apis.tests.UserBrokerCredentialFieldTests apis.tests.UserBrokerTypeTests apis.tests.AddAccountTests apis.tests.UpdateAccountTests apis.tests.DeleteUserBrokerScopeTests -v 2
./.venv/Scripts/python.exe manage.py test apis -v 1
```
Expected: all PASS; full suite green.

- [ ] **Step 6: Commit**

```bash
cd "C:/Projects/PycharmProjects/personal/Kronos_Backend"
git add apis/schema/mutation/user/delete_user_broker.py apis/tests.py
git commit -m "fix(accounts): scope DeleteUserBroker to the owning user

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Frontend — `accountControls.ts` mutation module

**Files:**
- Create: `GraphQL/accountControls.ts`

- [ ] **Step 1: Create the module**

Create `C:/Projects/PycharmProjects/personal/Kronos/Kronos App/kronos_frontend/GraphQL/accountControls.ts`:
```ts
import { gql } from "@apollo/client";

// Create a trading account (stores encrypted MetaAPI token).
export const ADD_ACCOUNT = gql`
  mutation AddAccount(
    $label: String!
    $metaAccountId: String!
    $metaApiToken: String!
  ) {
    AddAccount(
      label: $label
      metaAccountId: $metaAccountId
      metaApiToken: $metaApiToken
    ) {
      Response
      UserBroker {
        id
        label
        metaAccountId
        metaApiTokenLast4
        hasToken
      }
    }
  }
`;

// Update an account. Omit metaApiToken to keep the current token.
export const UPDATE_ACCOUNT = gql`
  mutation UpdateAccount(
    $id: String!
    $label: String
    $metaAccountId: String
    $metaApiToken: String
  ) {
    UpdateAccount(
      id: $id
      label: $label
      metaAccountId: $metaAccountId
      metaApiToken: $metaApiToken
    ) {
      Response
      UserBroker {
        id
        label
        metaAccountId
        metaApiTokenLast4
        hasToken
      }
    }
  }
`;

// Delete an account (user-scoped on the backend).
export const DELETE_USER_BROKER = gql`
  mutation DeleteUserBroker($brokerId: String!) {
    DeleteUserBroker(brokerId: $brokerId) {
      Response
    }
  }
`;

// Fetch the current user's accounts (reuses the existing getuserdata resolver).
export const GET_ACCOUNTS = gql`
  query GetAccounts {
    getuserdata {
      userbrokers {
        id
        label
        metaAccountId
        metaApiTokenLast4
        hasToken
        isActive
        status
      }
    }
  }
`;
```

- [ ] **Step 2: Typecheck**

Run (from the frontend repo):
```
npx tsc --noEmit
```
Expected: no new errors from this file.

- [ ] **Step 3: Commit**

```bash
cd "C:/Projects/PycharmProjects/personal/Kronos/Kronos App/kronos_frontend"
git add GraphQL/accountControls.ts
git commit -m "feat(accounts): centralized account mutation/query module

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Frontend — types + "Accounts" nav tab

**Files:**
- Modify: `types.ts`
- Modify: `app/(main)/_components/constants.ts`

- [ ] **Step 1: Add credential fields to the account type**

In `types.ts`, inside `export interface UserExchangeSetProps {`, add (after the existing `accountHolderName: string;` line):
```ts
  label: string;
  metaAccountId: string;
  metaApiTokenLast4: string;
  hasToken: boolean;
  status: string;
```
(If `status` already exists on the interface, do not add it twice.)

- [ ] **Step 2: Add the nav entry**

In `app/(main)/_components/constants.ts`, add this object to `sidebarLinkArray` (place it right after the Dashboard entry). The icon import `FaRectangleList` already exists; reuse `HiChartPie` (already imported) for Accounts:
```ts
  {
    title: "Accounts",
    path: "/accounts",
    icon: HiChartPie,
  },
```

- [ ] **Step 3: Typecheck**

Run: `npx tsc --noEmit`
Expected: no new errors.

- [ ] **Step 4: Commit**

```bash
cd "C:/Projects/PycharmProjects/personal/Kronos/Kronos App/kronos_frontend"
git add types.ts "app/(main)/_components/constants.ts"
git commit -m "feat(accounts): account types + Accounts nav tab

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Frontend — Accounts page (list + add/edit/delete)

**Files:**
- Create: `app/(main)/accounts/page.tsx`
- Create: `app/(main)/accounts/_components/AccountsManager.tsx`

- [ ] **Step 1: Create the route**

Create `app/(main)/accounts/page.tsx`:
```tsx
import AccountsManager from "./_components/AccountsManager";

export default function AccountsPage() {
  return <AccountsManager />;
}
```

- [ ] **Step 2: Create the manager component**

Create `app/(main)/accounts/_components/AccountsManager.tsx`:
```tsx
"use client";

import React, { useEffect, useState } from "react";
import { toast } from "sonner";

import { client } from "@/GraphQL/client";
import { middleware } from "@/GraphQL/middleware";
import {
  ADD_ACCOUNT,
  UPDATE_ACCOUNT,
  DELETE_USER_BROKER,
  GET_ACCOUNTS,
} from "@/GraphQL/accountControls";

interface Account {
  id: string;
  label: string;
  metaAccountId: string;
  metaApiTokenLast4: string;
  hasToken: boolean;
  isActive: boolean;
  status: string;
}

const emptyForm = { id: "", label: "", metaAccountId: "", metaApiToken: "" };

const AccountsManager: React.FC = () => {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [form, setForm] = useState({ ...emptyForm });
  const [editing, setEditing] = useState(false);

  const load = () => {
    client
      .query({ query: GET_ACCOUNTS, fetchPolicy: "no-cache" })
      .then((res) => {
        setAccounts(res.data?.getuserdata?.userbrokers ?? []);
      })
      .catch((err) => middleware(err));
  };

  useEffect(() => {
    load();
  }, []);

  const resetForm = () => {
    setForm({ ...emptyForm });
    setEditing(false);
  };

  const handleSubmit = () => {
    if (editing) {
      const variables: Record<string, string> = {
        id: form.id,
        label: form.label,
        metaAccountId: form.metaAccountId,
      };
      if (form.metaApiToken) variables.metaApiToken = form.metaApiToken;
      client
        .mutate({ mutation: UPDATE_ACCOUNT, variables, fetchPolicy: "no-cache" })
        .then((res) => {
          if (res.data.UpdateAccount.Response === "Success") {
            toast.success("Account updated");
            resetForm();
            load();
          } else {
            toast.error(res.data.UpdateAccount.Response);
          }
        })
        .catch((err) => middleware(err));
    } else {
      client
        .mutate({
          mutation: ADD_ACCOUNT,
          variables: {
            label: form.label,
            metaAccountId: form.metaAccountId,
            metaApiToken: form.metaApiToken,
          },
          fetchPolicy: "no-cache",
        })
        .then((res) => {
          if (res.data.AddAccount.Response === "Success") {
            toast.success("Account added");
            resetForm();
            load();
          } else {
            toast.error(res.data.AddAccount.Response);
          }
        })
        .catch((err) => middleware(err));
    }
  };

  const handleEdit = (a: Account) => {
    setForm({
      id: a.id,
      label: a.label,
      metaAccountId: a.metaAccountId,
      metaApiToken: "",
    });
    setEditing(true);
  };

  const handleDelete = (id: string) => {
    client
      .mutate({
        mutation: DELETE_USER_BROKER,
        variables: { brokerId: id },
        fetchPolicy: "no-cache",
      })
      .then((res) => {
        if (res.data.DeleteUserBroker.Response === "Success") {
          toast.success("Account deleted");
          load();
        } else {
          toast.error(res.data.DeleteUserBroker.Response);
        }
      })
      .catch((err) => middleware(err));
  };

  return (
    <div className="flex flex-col gap-6 w-full">
      <h1 className="text-xl font-semibold">Accounts</h1>

      <div className="flex flex-col gap-3 border rounded-md p-4 max-w-xl">
        <div className="font-semibold">
          {editing ? "Edit account" : "Add account"}
        </div>
        <input
          className="border rounded px-3 py-2"
          placeholder="Label (e.g. Primary Live)"
          value={form.label}
          onChange={(e) => setForm({ ...form, label: e.target.value })}
        />
        <input
          className="border rounded px-3 py-2"
          placeholder="MetaAPI account ID"
          value={form.metaAccountId}
          onChange={(e) => setForm({ ...form, metaAccountId: e.target.value })}
        />
        <input
          className="border rounded px-3 py-2"
          type="password"
          placeholder={
            editing ? "Token (leave blank to keep current)" : "MetaAPI token"
          }
          value={form.metaApiToken}
          onChange={(e) => setForm({ ...form, metaApiToken: e.target.value })}
        />
        <div className="flex gap-2">
          <button
            className="bg-myGreen1 text-black rounded px-4 py-2"
            onClick={handleSubmit}
          >
            {editing ? "Save" : "Add"}
          </button>
          {editing && (
            <button className="rounded px-4 py-2 border" onClick={resetForm}>
              Cancel
            </button>
          )}
        </div>
      </div>

      <div className="flex flex-col gap-2 w-full">
        {accounts.length === 0 && (
          <div className="text-sm opacity-70">No accounts yet.</div>
        )}
        {accounts.map((a) => (
          <div
            key={a.id}
            className="flex items-center justify-between border rounded-md px-4 py-3 w-full"
          >
            <div className="flex flex-col">
              <span className="font-semibold">{a.label || "(no label)"}</span>
              <span className="text-sm opacity-70">{a.metaAccountId}</span>
            </div>
            <div className="text-sm">
              {a.hasToken ? `••••${a.metaApiTokenLast4}` : "no token set"}
            </div>
            <div className="flex gap-2">
              <button className="border rounded px-3 py-1" onClick={() => handleEdit(a)}>
                Edit
              </button>
              <button
                className="border rounded px-3 py-1 text-myRed1"
                onClick={() => handleDelete(a.id)}
              >
                Delete
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default AccountsManager;
```

- [ ] **Step 3: Typecheck and build**

Run (from the frontend repo):
```
npx tsc --noEmit
npm run build
```
Expected: typecheck clean; `next build` succeeds (route `/accounts` listed in the output).

- [ ] **Step 4: Commit**

```bash
cd "C:/Projects/PycharmProjects/personal/Kronos/Kronos App/kronos_frontend"
git add "app/(main)/accounts"
git commit -m "feat(accounts): /accounts page — list/add/edit/delete accounts

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: End-to-end verification (manual, against the running local stack)

**Files:** none (verification only).

The local backend (`runserver 8000`, pointed at the live DB) hot-reloads the backend tasks; the frontend dev server (`:3003`) hot-reloads the frontend tasks. The backend needs `FIELD_ENCRYPTION_KEY` set in its environment for the mutations to work.

- [ ] **Step 1: Set the encryption key for the running backend**

Generate a key and restart the local backend with it in the environment (alongside the existing `DB_*` vars):
```
./.venv/Scripts/python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
Add the printed value as `FIELD_ENCRYPTION_KEY` when launching `manage.py runserver 8000`. Persist the same key in `Kronos_Backend/.env` (uncommitted) so it survives restarts. (Production sets its own `FIELD_ENCRYPTION_KEY`.)

- [ ] **Step 2: Add an account in the UI**

Log in at `http://localhost:3003/login`, open the new **Accounts** tab, and add an account (label, a MetaAPI account ID, and a token). Confirm a success toast and that the row appears with the token shown as `••••<last4>`.

- [ ] **Step 3: Confirm encryption in the DB**

From `Kronos_Backend` with the live DB env + `FIELD_ENCRYPTION_KEY` loaded:
```
./.venv/Scripts/python.exe manage.py shell -c "from apis.models import UserBroker; b=UserBroker.objects.filter(label='<your label>').first(); print('last4=', b.meta_api_token_last4, 'enc_is_ciphertext=', b.meta_api_token_enc[:8], 'plaintext_not_stored=', '<your token>' not in b.meta_api_token_enc)"
```
Expected: `last4` matches; `meta_api_token_enc` is ciphertext (not your token).

- [ ] **Step 4: Edit (keep token) and delete**

Edit the account's label, leaving the token field blank → save → confirm the label changes and the masked `••••<last4>` is unchanged. Then enter a new token → save → confirm `last4` updates. Finally delete the account → confirm it disappears.

- [ ] **Step 5: Record results**

Note pass/fail for add, masked display, edit-without-token, edit-with-token, delete, and the DB-ciphertext check. If all pass, Sub-project A is complete and B (marketplace + deploy) can begin.

---

## Self-Review notes (author)

- **Spec coverage:** model fields + crypto (Tasks 1-2), write-only token type (Task 3), AddAccount/UpdateAccount (Tasks 4-5), user-scoped delete (Task 6), frontend module + nav + page (Tasks 7-9), manual E2E + DB-ciphertext check (Task 10). Error handling (missing key) covered in Tasks 4-5 tests. All spec sections mapped.
- **Names pinned:** model fields `label` / `meta_account_id` / `meta_api_token_enc` / `meta_api_token_last4`; GraphQL `metaAccountId` / `metaApiTokenLast4` / `hasToken`; mutations `AddAccount` (args `label`,`metaAccountId`,`metaApiToken`), `UpdateAccount` (`id` + optional), `DeleteUserBroker` (`brokerId`). Auto-registration requires the class name to equal the capitalized file name (`add_account.py`→`AddAccount`, `update_account.py`→`UpdateAccount`).
- **Token never exposed:** `meta_api_token_enc` added to `Meta.exclude`; no field returns plaintext; `UpdateAccount` blank-token path leaves ciphertext untouched (Task 5 test).
- **No JS test runner:** frontend verified via `tsc --noEmit` + `next build` + the Task 10 manual checklist, matching repo convention.
