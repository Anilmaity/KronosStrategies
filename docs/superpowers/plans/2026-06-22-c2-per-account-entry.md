# Per-Account Entry (Sub-project C2) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Open each deployed strategy's MetaAPI market order on **its account's** decrypted credentials, refusing to open if the account has none; remove the env-based entry path. (Close path stays for C3.)

**Architecture:** Add a `MetaApiClient(account_id, token)` class and a `client_for_broker()` helper to `strategies/shared/metaapi_client.py`; cut `entry_manager` and `micro_scalper` over to per-account clients with refuse-if-missing; delete the module-level `place_market_order`. Keep the module helpers `_headers`/`_trading_url`/`_get_symbol_spec`/`close_position_by_id` (still used by the close path until C3).

**Tech Stack:** Python, SQLAlchemy, `requests`, pytest. **This is a live-money change** — roll out with `DRY_RUN=true` first (Task 5).

**Spec:** `docs/superpowers/specs/2026-06-22-c2-per-account-entry-design.md`

**Repo:** `C:/Projects/PycharmProjects/personal/KronosStrategies` (branch `research/btc-edge` — commit there; do NOT branch). pytest: `./.venv/Scripts/python.exe -m pytest <path> -q`.

---

## File Structure

- Modify: `strategies/shared/metaapi_client.py` — add `MetaApiClient` class + `client_for_broker`; remove module `place_market_order`.
- Modify: `strategies/strategy/entry_manager.py` — cut `place_entry` over to a per-account client.
- Modify: `strategies/micro_scalper.py` — cut its order placement over.
- Create tests: `tests/test_metaapi_client_for_broker.py`, `tests/test_metaapi_place_order.py`, `tests/test_entry_manager_refuse.py`.

---

## Task 1: `MetaApiClient` class + `client_for_broker` helper (pytest TDD)

**Files:**
- Modify: `strategies/shared/metaapi_client.py`
- Test: `tests/test_metaapi_client_for_broker.py`, `tests/test_metaapi_place_order.py`

The existing module functions `_headers()`, `_trading_url()`, `_get_symbol_spec(broker_symbol)`, `place_market_order(...)` use the globals `_TOKEN`/`_ACCOUNT`/`_TRADING_URL`/`_SPEC_CACHE`. We add a class whose methods are **copies of those bodies** with per-instance state, add a broker→client helper, and remove the module `place_market_order`. We KEEP the module `_headers`/`_trading_url`/`_get_symbol_spec`/`_TRADING_URL`/`_SPEC_CACHE`/`close_position_by_id` because `close_position_by_id` still calls them (C3 removes them later).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_metaapi_client_for_broker.py`:
```python
import types

import pytest
from cryptography.fernet import Fernet


def _set_key(monkeypatch):
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode())


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter_by(self, **kw):
        return self

    def first(self):
        return self._result


class _FakeSession:
    def __init__(self, broker):
        self._broker = broker

    def query(self, model):
        return _FakeQuery(self._broker)


def _broker(meta_account_id, meta_api_token_enc):
    return types.SimpleNamespace(
        meta_account_id=meta_account_id, meta_api_token_enc=meta_api_token_enc
    )


def test_returns_client_when_creds_present(monkeypatch):
    _set_key(monkeypatch)
    from strategies.shared import metaapi_client as mc
    from strategies.shared.crypto import encrypt_token
    mc._CLIENT_CACHE.clear()
    enc = encrypt_token("tok-REAL-1234")
    sess = _FakeSession(_broker("acct-777", enc))
    client = mc.client_for_broker(sess, "ub-1")
    assert isinstance(client, mc.MetaApiClient)
    assert client.account_id == "acct-777"
    assert client.token == "tok-REAL-1234"


def test_none_when_no_account_id(monkeypatch):
    _set_key(monkeypatch)
    from strategies.shared import metaapi_client as mc
    mc._CLIENT_CACHE.clear()
    sess = _FakeSession(_broker("", "whatever"))
    assert mc.client_for_broker(sess, "ub-1") is None


def test_none_when_no_token(monkeypatch):
    _set_key(monkeypatch)
    from strategies.shared import metaapi_client as mc
    mc._CLIENT_CACHE.clear()
    sess = _FakeSession(_broker("acct-1", ""))
    assert mc.client_for_broker(sess, "ub-1") is None


def test_none_when_broker_missing(monkeypatch):
    _set_key(monkeypatch)
    from strategies.shared import metaapi_client as mc
    mc._CLIENT_CACHE.clear()
    sess = _FakeSession(None)
    assert mc.client_for_broker(sess, "ub-1") is None


def test_none_on_decrypt_failure(monkeypatch):
    _set_key(monkeypatch)
    from strategies.shared import metaapi_client as mc
    mc._CLIENT_CACHE.clear()
    sess = _FakeSession(_broker("acct-1", "not-valid-fernet-ciphertext"))
    assert mc.client_for_broker(sess, "ub-1") is None
```

Create `tests/test_metaapi_place_order.py`:
```python
import types

from cryptography.fernet import Fernet


def test_place_market_order_posts_to_account_url(monkeypatch):
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("META_REGION", "new-york")  # skip provisioning
    from strategies.shared import metaapi_client as mc

    captured = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"positionId": "POS-1", "stringCode": "TRADE_RETCODE_DONE"}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return _Resp()

    monkeypatch.setattr(mc.requests, "post", _fake_post)

    client = mc.MetaApiClient("acct-XYZ", "tok-ABC")
    pos = client.place_market_order(
        side="BUY", symbol="XAU_USD", volume=0.01,
        stop_loss=4490.0, take_profit=4520.0, entry_price=4500.0,
    )
    assert pos == "POS-1"
    assert "/accounts/acct-XYZ/trade" in captured["url"]
    assert captured["headers"]["auth-token"] == "tok-ABC"
```

- [ ] **Step 2: Run to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_metaapi_client_for_broker.py tests/test_metaapi_place_order.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'MetaApiClient'` / `client_for_broker` / `_CLIENT_CACHE`.

- [ ] **Step 3: Add the class + helper; remove the module `place_market_order`**

Edit `strategies/shared/metaapi_client.py`:

(a) Add imports near the top (after the existing imports):
```python
from shared.crypto import decrypt_token
from shared.models import UserBroker
```
(If those imports cause a circular import or the module is imported in a context without `shared` on the path, import them lazily inside `client_for_broker` instead — verify the test passes either way.)

(b) Add the per-account client class and the helper. Create `class MetaApiClient` whose methods are **exact copies of the existing module functions** `_headers`, `_trading_url`, `_get_symbol_spec`, `place_market_order`, moved into the class with these substitutions applied in each method body:
- `_TOKEN`  → `self.token`
- `_ACCOUNT` → `self.account_id`
- the module global `_TRADING_URL` (read/`global _TRADING_URL`/assignment) → `self._trading_url_cache`
- `_SPEC_CACHE` → `self._spec_cache`
- calls `_trading_url()` → `self._trading_url()`, `_headers()` → `self._headers()`, `_get_symbol_spec(x)` → `self._get_symbol_spec(x)`
- leave references to module-level config unchanged: `_REGION_OVERRIDE`, `_PROVISION_URL`, `_CLIENT_DOMAIN`, `_DEFAULT_REGION`, `_SYMBOL_MAP`, `_DRY_RUN`, `_DEFAULT_MIN_STOP_DISTANCE`, `_TIMEOUT`, `_apply_stops_floor(...)`, `requests`, `log`.

The class skeleton (fill the method bodies by moving + substituting as above):
```python
class MetaApiClient:
    """Per-account MetaAPI client. One instance per (account_id, token)."""

    def __init__(self, account_id: str, token: str):
        self.account_id = account_id
        self.token = token
        self._trading_url_cache: str | None = None
        self._spec_cache: dict[str, dict] = {}

    def _headers(self) -> dict:
        return {"auth-token": self.token, "Content-Type": "application/json"}

    def _trading_url(self) -> str:
        # MOVE the body of the module _trading_url() here, applying the
        # substitutions above (self._trading_url_cache instead of the global,
        # self.account_id instead of _ACCOUNT, self._headers()).
        ...

    def _get_symbol_spec(self, broker_symbol: str) -> dict:
        # MOVE the module _get_symbol_spec() body here (self._spec_cache,
        # self.account_id, self._trading_url(), self._headers(), self.token).
        ...

    def place_market_order(self, side, symbol, volume, stop_loss, take_profit,
                           entry_price=None) -> str | None:
        # MOVE the module place_market_order() body here (self.account_id,
        # self.token, self._trading_url(), self._headers(),
        # self._get_symbol_spec(...)). DRY_RUN behavior unchanged.
        ...


_CLIENT_CACHE: dict[str, "MetaApiClient"] = {}


def client_for_broker(session, user_broker_id):
    """Return a MetaApiClient for the broker's stored creds, or None (→ refuse)."""
    broker = session.query(UserBroker).filter_by(id=user_broker_id).first()
    if broker is None:
        log.warning("[MetaAPI] UserBroker %s not found — refusing", user_broker_id)
        return None
    acct = (getattr(broker, "meta_account_id", "") or "").strip()
    enc = getattr(broker, "meta_api_token_enc", "") or ""
    if not acct or not enc:
        log.warning("[MetaAPI] account %s has no per-account creds — refusing",
                    user_broker_id)
        return None
    if acct in _CLIENT_CACHE:
        return _CLIENT_CACHE[acct]
    try:
        token = decrypt_token(enc)
    except Exception as e:
        log.error("[MetaAPI] decrypt failed for %s: %s — refusing", user_broker_id, e)
        return None
    client = MetaApiClient(acct, token)
    _CLIENT_CACHE[acct] = client
    return client
```

(c) **Delete** the module-level `def place_market_order(...)` function entirely (the env-based entry path). **Keep** the module `_headers`, `_trading_url`, `_get_symbol_spec`, the `_TRADING_URL`/`_SPEC_CACHE` globals, and `close_position_by_id` (the close path still uses them; C3 removes them).

- [ ] **Step 4: Run to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_metaapi_client_for_broker.py tests/test_metaapi_place_order.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
cd "C:/Projects/PycharmProjects/personal/KronosStrategies"
git add strategies/shared/metaapi_client.py tests/test_metaapi_client_for_broker.py tests/test_metaapi_place_order.py
git commit -m "feat(c2): per-account MetaApiClient + client_for_broker; remove env entry path

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `entry_manager.place_entry` cutover (pytest TDD)

**Files:**
- Modify: `strategies/strategy/entry_manager.py`
- Test: `tests/test_entry_manager_refuse.py`

`place_entry` currently calls the now-deleted module `place_market_order(...)` (before it opens `sess = Session()` for recording). Replace that call with a per-account client lookup that refuses if absent.

- [ ] **Step 1: Write the failing test**

Create `tests/test_entry_manager_refuse.py`:
```python
def test_place_entry_refuses_without_account_creds(monkeypatch):
    from strategies.strategy import entry_manager as em

    # Context resolves to a broker, but client_for_broker yields no client.
    monkeypatch.setattr(em, "_get_context", lambda *a, **k: {
        "user_strategy_id": "us-1", "user_broker_id": "ub-1",
        "currency_pair_id": "cp-1", "quantity": 0.01,
    })
    monkeypatch.setattr(em, "_log_signal_fired", lambda *a, **k: "sig-1")
    monkeypatch.setattr(em, "client_for_broker", lambda *a, **k: None)

    placed = {"called": False}
    # If any client were built, this would be how it'd place — assert it is NOT.
    monkeypatch.setattr(em, "_open_position_count", lambda *a, **k: 0)

    status = {}
    monkeypatch.setattr(em, "_update_signal_status",
                        lambda sid, st, **k: status.update(st=st, **k))

    from strategies.strategy.ict_engine import EntrySignal  # adjust import if needed
    sig = EntrySignal(side="BUY", entry_price=4500.0, stop_loss=4490.0,
                      take_profit=4520.0)  # construct per the real dataclass

    result = em.place_entry(sig)
    assert result is False
    assert status.get("st") == "REJECTED"
    assert status.get("rejection_reason") == "no_account_credentials"
    assert placed["called"] is False
```
NOTE for the implementer: `EntrySignal`'s real constructor/fields may differ — read `strategies/strategy/ict_engine.py` and build a valid `EntrySignal`, and read `place_entry`'s real signature/early-return guards (e.g. `_open_position_count`/`max_concurrent`) so the test reaches the credential check. Keep the test focused on the refuse path; mock whatever `place_entry` calls before the order so it gets there. If `place_entry` is too entangled to unit-test cleanly, extract the smallest decision into a helper `_resolve_entry_client(ctx)` and test that instead (and call it from `place_entry`).

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_entry_manager_refuse.py -q`
Expected: FAIL — `place_entry` still references the deleted `place_market_order` (ImportError/NameError) or doesn't set `no_account_credentials`.

- [ ] **Step 3: Cut `place_entry` over**

In `strategies/strategy/entry_manager.py`:
(a) Update the import: change `from shared.metaapi_client import place_market_order` to `from shared.metaapi_client import client_for_broker`.
(b) Replace the `place_market_order(...)` call block with:
```python
    _sess = Session()
    try:
        client = client_for_broker(_sess, ctx["user_broker_id"])
    finally:
        _sess.close()
    if client is None:
        _update_signal_status(signal_log_id, "REJECTED",
                              rejection_reason="no_account_credentials")
        log.warning("[ENTRY] account %s has no usable MetaAPI creds — refusing to open",
                    ctx["user_broker_id"])
        return False
    broker_position_id = client.place_market_order(
        side=signal.side,
        symbol=symbol,
        volume=qty,
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit,
        entry_price=signal.entry_price,
    )
```
(`Session` is already imported in `entry_manager.py` via `shared.models`; if not, add it. `signal_log_id`, `signal`, `symbol`, `qty`, `ctx` are the existing locals at that point — confirm by reading the surrounding code.)

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_entry_manager_refuse.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd "C:/Projects/PycharmProjects/personal/KronosStrategies"
git add strategies/strategy/entry_manager.py tests/test_entry_manager_refuse.py
git commit -m "feat(c2): entry_manager opens on per-account creds, refuses if missing

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `micro_scalper` cutover

**Files:**
- Modify: `strategies/micro_scalper.py`

`micro_scalper` calls the deleted module `place_market_order(...)` and already has `sess = Session()` and the deployed `us = UserStrategy(... deployed=True)` in scope at its placement site.

- [ ] **Step 1: Cut it over**

In `strategies/micro_scalper.py`:
(a) Change `from shared.metaapi_client import place_market_order` (or the relevant import) to `from shared.metaapi_client import client_for_broker`.
(b) At the order-placement site (where it currently calls `place_market_order(...)`), insert before the call:
```python
        client = client_for_broker(sess, us.user_broker_id)
        if client is None:
            log.warning("[MICRO] account %s has no usable MetaAPI creds — skipping entry",
                        us.user_broker_id)
            return  # or `continue`/skip per the existing loop structure
```
and change the `place_market_order(...)` call to `client.place_market_order(...)` with the same keyword args. (`sess` and `us` are the existing locals; read the surrounding code to use the correct skip control-flow — `return` vs `continue` — and the correct `log` name.)

- [ ] **Step 2: Verify it imports/compiles**

Run: `./.venv/Scripts/python.exe -c "import ast; ast.parse(open('strategies/micro_scalper.py').read()); print('parse ok')"`
Expected: `parse ok`. Also: `grep -n "place_market_order" strategies/micro_scalper.py` should show only `client.place_market_order(`.

- [ ] **Step 3: Commit**

```bash
cd "C:/Projects/PycharmProjects/personal/KronosStrategies"
git add strategies/micro_scalper.py
git commit -m "feat(c2): micro_scalper opens on per-account creds, refuses if missing

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Regression — env entry path gone, suite green

**Files:** none (verification).

- [ ] **Step 1: Confirm the env entry path is removed and callers are cut over**

Run:
```
grep -rn "from shared.metaapi_client import place_market_order" strategies/ | grep -v _scratch
grep -rn "^def place_market_order" strategies/shared/metaapi_client.py
grep -rn "place_market_order(" strategies/strategy/entry_manager.py strategies/micro_scalper.py
```
Expected: first two return nothing (no module-level entry function, no caller importing it); the third shows only `client.place_market_order(`.

- [ ] **Step 2: Run the full C1+C2 test set**

Run:
```
./.venv/Scripts/python.exe -m pytest tests/test_runner_crypto.py tests/test_userbroker_creds_columns.py tests/test_migrate_env_creds.py tests/test_metaapi_client_for_broker.py tests/test_metaapi_place_order.py tests/test_entry_manager_refuse.py -q
```
Expected: all PASS.

- [ ] **Step 3: Confirm the close path still imports (untouched)**

Run: `grep -n "def close_position_by_id\|def _trading_url\|def _headers\|def _get_symbol_spec" strategies/shared/metaapi_client.py`
Expected: all four still present (the module helpers + close stay for C3).

---

## Task 5: Operational rollout (manual, live-money — DRY_RUN first)

**Files:** none (operational). Run by the operator on the runner box(es).

Prereqs: C1's env-cred migration has populated the live accounts (`meta_account_id` + `meta_api_token_enc`); `FIELD_ENCRYPTION_KEY` is set to the **same** value as the backend on every runner box; `cryptography` is installed in the runner images.

- [ ] **Step 1: Deploy with `DRY_RUN=true`**

Roll out C2 to the runner box(es) with `DRY_RUN=true` in the env. In dry-run, `client_for_broker` still resolves real clients (so missing-cred refusals surface in logs) but `place_market_order` returns the `'dry-run'` sentinel without sending an order.

- [ ] **Step 2: Watch the logs**

For each deployed strategy that fires, confirm the log shows it resolved a per-account client (no `"has no usable MetaAPI creds — refusing"` for accounts you migrated). Any account still showing "refusing" needs its creds set (re-run C1's migration or add via the Accounts UI).

- [ ] **Step 3: Go live**

Once dry-run confirms every active account resolves a client, set `DRY_RUN=false` and restart. Watch the first live order's log line — confirm the `positionId` and that it hit the correct account's trading host.

- [ ] **Step 4: Record results**

Note which accounts went live per-account. C2 is complete when active strategies open on their own account and accounts without creds refuse (never the env account). C3 (per-account close) can then begin.

---

## Self-Review notes (author)

- **Spec coverage:** `MetaApiClient` + `client_for_broker` with cache (Task 1); refuse-if-missing cutover of both callers (Tasks 2-3); env entry path removed, close path kept (Tasks 1, 4); DRY_RUN rollout (Task 5). All spec sections mapped.
- **Move-refactor:** Task 1 instructs moving the existing function bodies into methods with an explicit substitution table rather than re-transcribing the order logic, to avoid transcription error in live-money code; the tests assert the per-account URL/header and the refuse paths.
- **Scope discipline:** `close_position_by_id` and the module `_headers`/`_trading_url`/`_get_symbol_spec` are explicitly KEPT (C3). Only the module `place_market_order` is removed.
- **Test reality:** `entry_manager` is DB/engine-entangled; Task 2 allows extracting `_resolve_entry_client` if `place_entry` can't be unit-tested cleanly. `EntrySignal`'s real shape must be read from `ict_engine.py`.
- **Safety:** live-money rollout is operator-gated and DRY_RUN-first; missing/invalid creds refuse (never env fallback).
