# Per-Account Entry (Sub-project C2) — Design Spec

**Date:** 2026-06-22
**Status:** Approved (design); pending implementation plan
**Author:** Anil + Claude

## Context

Sub-project **C** routes each deployed strategy's trades to **its** account. Decomposed:

- **C1 — Credential plumbing + env-cred migration (done).** ORM columns + Fernet decrypt helper in both runner repos; env-cred migration script. Spec: `2026-06-22-c1-credential-plumbing-design.md`.
- **C2 — Per-account entry (this spec).** The **open/entry** path trades on the deployed strategy's account credentials; refuses to open if the account has none. Full cutover: the env-based entry path is removed.
- **C3 — Per-account close.** Same for `position_monitor.close_position_by_id`.

This spec covers **only C2**. It is a **live-money** change.

### Decisions (from brainstorming)

- **Full cutover:** both entry callers (`entry_manager` and `micro_scalper`) switch to per-account; the module-level env-based `place_market_order` is removed. Nothing opens on the env account anymore.
- **Refuse if missing:** if a deployed strategy's account has no usable creds (empty `meta_account_id`/`meta_api_token_enc`, or decryption fails), the runner logs and **does not open** — it never falls back to the env account.

### Current state (as explored)

`strategies/shared/metaapi_client.py`:
- Module globals: `_TOKEN`/`_ACCOUNT` (from env), `_DRY_RUN`, provisioning/region config (`_PROVISION_URL`, `_CLIENT_DOMAIN`, `_REGION_OVERRIDE` (`META_REGION`), `_DEFAULT_REGION`), `_TRADING_URL` (cached), `_SYMBOL_MAP`, `_SPEC_CACHE`, `_DEFAULT_MIN_STOP_DISTANCE`, `_TIMEOUT`.
- `_headers()` uses `_TOKEN`. `_trading_url()` resolves the regional host (precedence: `META_REGION` override → provisioning GET on `_ACCOUNT` → `_DEFAULT_REGION`) and caches globally. `_get_symbol_spec(broker_symbol)` uses `_ACCOUNT`, caches in `_SPEC_CACHE`. `place_market_order(side, symbol, volume, stop_loss, take_profit, entry_price=None)` uses `_ACCOUNT` + `_trading_url()` + `_headers()`; returns the broker positionId or `None` (or `'dry-run'`). `close_position_by_id(...)` uses `_ACCOUNT` (C3, untouched here).
- Entry callers: `strategies/strategy/entry_manager.py` (`place_entry` → `place_market_order`, context already has `user_broker_id`) and `strategies/micro_scalper.py` (calls `place_market_order` directly for its deployed strategy). `_scratch/place_test_order.py` is untracked and ignored.
- C1 added `meta_account_id`/`meta_api_token_enc` to the `UserBroker` ORM and `shared/crypto.py` (`decrypt_token`) in this repo.

## Goal

After C2: a deployed strategy opens its MetaAPI market order on **its account's** credentials (decrypted at runtime), or refuses to open if the account lacks creds. No entry ever uses the env account.

## Non-goals (out of scope for C2)

- The close path (`close_position_by_id` / `position_monitor`) — that's C3; the env globals remain for it.
- Position-monitor / reconciliation behavior.
- Any backend / frontend change.
- Multi-region per-account auto-resolution beyond the existing precedence (see Limitations).

## Architecture

`strategies/shared/metaapi_client.py` gains a per-account client class and a broker→client helper; the two entry callers use them; the env-based module `place_market_order` is removed.

### 1. `MetaApiClient` class

```python
class MetaApiClient:
    def __init__(self, account_id: str, token: str):
        self.account_id = account_id
        self.token = token
        self._trading_url_cache: str | None = None
        self._spec_cache: dict[str, dict] = {}

    def _headers(self) -> dict: ...            # uses self.token
    def _trading_url(self) -> str: ...         # same precedence, uses self.account_id, caches on self
    def _get_symbol_spec(self, broker_symbol: str) -> dict: ...   # uses self
    def place_market_order(self, side, symbol, volume, stop_loss, take_profit, entry_price=None) -> str | None: ...
```

- Method bodies are the existing function bodies with `_TOKEN`→`self.token`, `_ACCOUNT`→`self.account_id`, the global `_TRADING_URL`→`self._trading_url_cache`, and `_SPEC_CACHE`→`self._spec_cache`. The shared config constants (`_PROVISION_URL`, `_CLIENT_DOMAIN`, `_REGION_OVERRIDE`, `_DEFAULT_REGION`, `_SYMBOL_MAP`, `_DRY_RUN`, `_DEFAULT_MIN_STOP_DISTANCE`, `_TIMEOUT`, `_apply_stops_floor`) stay module-level and are referenced by the methods.
- `_DRY_RUN` behavior is preserved (returns the `'dry-run'` sentinel without hitting the network).

### 2. `client_for_broker` helper

```python
_CLIENT_CACHE: dict[str, MetaApiClient] = {}   # keyed by account_id

def client_for_broker(session, user_broker_id) -> MetaApiClient | None:
    broker = session.query(UserBroker).filter_by(id=user_broker_id).first()
    if broker is None:
        log.warning("[MetaAPI] UserBroker %s not found — refusing", user_broker_id)
        return None
    acct = (broker.meta_account_id or "").strip()
    enc = broker.meta_api_token_enc or ""
    if not acct or not enc:
        log.warning("[MetaAPI] account %s has no per-account creds — refusing", user_broker_id)
        return None
    if acct in _CLIENT_CACHE:
        return _CLIENT_CACHE[acct]
    try:
        token = decrypt_token(enc)
    except Exception as e:
        log.error("[MetaAPI] could not decrypt creds for %s: %s — refusing", user_broker_id, e)
        return None
    client = MetaApiClient(acct, token)
    _CLIENT_CACHE[acct] = client
    return client
```

- Imports `UserBroker` from `shared.models` and `decrypt_token` from `shared.crypto`.
- Caches by `account_id` so the region/host is resolved once per account in a long-running process. **Staleness:** a credential change (via the backend) is picked up on runner restart — acceptable; noted operationally.
- Any failure path returns `None`, and the caller refuses to open (never env fallback).

### 3. Caller cutover

`entry_manager.place_entry` — replace the `place_market_order(...)` call with:
```python
client = client_for_broker(sess, ctx["user_broker_id"])
if client is None:
    _update_signal_status(signal_log_id, "REJECTED", rejection_reason="no_account_credentials")
    log.warning("[ENTRY] account has no usable MetaAPI creds — refusing to open")
    return False
broker_position_id = client.place_market_order(
    side=signal.side, symbol=symbol, volume=qty,
    stop_loss=signal.stop_loss, take_profit=signal.take_profit,
    entry_price=signal.entry_price,
)
```
(`sess` is the SQLAlchemy session already in scope in `place_entry`/its context; the plan pins the exact session variable.)

`micro_scalper` — at its order-placement point, resolve the deployed strategy's `user_broker_id`, call `client_for_broker(sess, user_broker_id)`, refuse if `None` (log + skip this entry), else `client.place_market_order(...)`. The plan pins micro_scalper's exact placement site and how it gets `user_broker_id` (it already queries the deployed `UserStrategy`).

### 4. Remove the env entry path

Delete the module-level `place_market_order` function (and, if now unused by anything but the class, the global `_TRADING_URL`/`_get_symbol_spec`/`_headers` env forms can be folded into the class). `close_position_by_id` and the `_TOKEN`/`_ACCOUNT` globals **remain** (used by the close path until C3). After C2, `grep place_market_order` shows only the class method + the two updated callers.

## Data flow

```
signal → entry_manager.place_entry
  → ctx.user_broker_id
  → client_for_broker(sess, user_broker_id)
       broker has creds?  no → log + REJECTED(no_account_credentials) + return False
       yes → decrypt_token(enc) → MetaApiClient(account_id, token) [cached]
  → client.place_market_order(...) → POST mt-client-api-v1.<region>/users/current/accounts/<account_id>/trade
```

## Error handling

- **No creds / broker missing / decrypt failure:** `client_for_broker` returns `None`; the caller refuses to open and records the signal as REJECTED (entry_manager) or skips (micro_scalper). A bad `FIELD_ENCRYPTION_KEY` on the box therefore **stops opening** rather than crashing the loop or trading on a wrong account.
- **MetaAPI rejection:** unchanged — `place_market_order` returns `None`, caller records REJECTED.
- **DRY_RUN:** unchanged — returns the `'dry-run'` sentinel.

## Limitations

- Region resolution keeps the existing precedence per client: `META_REGION` override → provisioning lookup (needs a management-scoped token) → default. With client-scoped tokens and a single `META_REGION`, all accounts resolve to that region. Multi-region accounts would need a per-account region (a future enhancement; out of scope). Current accounts are same-region, so this is acceptable.
- Client cache is process-lifetime; credential changes require a runner restart.

## Testing (pytest, `tests/`)

- `client_for_broker`: returns a `MetaApiClient` with the decrypted token when the (mocked-session) broker has `meta_account_id` + `meta_api_token_enc` (encrypted with a test `FIELD_ENCRYPTION_KEY`); returns `None` when either is empty; returns `None` when the broker is missing; returns `None` on a decrypt failure (corrupt ciphertext). Resets `_CLIENT_CACHE` between cases.
- `MetaApiClient.place_market_order`: with a mocked HTTP `post`/`get` (and `DRY_RUN` off), the trade POST URL contains the instance `account_id` and the `auth-token` header equals the instance token; returns the parsed positionId. (Inject the mock via monkeypatching `requests` in the module, mirroring the backend's exit-helper tests.)
- `entry_manager.place_entry` refuse path: when `client_for_broker` returns `None` (monkeypatched), no order is placed, the signal is marked REJECTED with `no_account_credentials`, and it returns `False`. (Mock the engine/session as the existing entry tests do, or unit-test the smallest extracted decision.)
- Regression: `grep` shows the env-based module `place_market_order` is gone and both callers use `client_for_broker`.

## Security / safety notes

- The plaintext token exists only in process memory for the lifetime of the cached client; it is never logged (logs show `account_id`/`user_broker_id`, never the token).
- C2 cannot open on a different account than the one deployed-to: the only path is `client_for_broker(user_broker_id)`, and missing/invalid creds refuse rather than fall back.
- **Operational gate:** before deploying C2, C1's env-cred migration must have populated the live accounts, and `FIELD_ENCRYPTION_KEY` must be set (same value) on the runner box(es) — otherwise every strategy refuses to open. Roll out with `DRY_RUN=true` first to confirm clients resolve before live orders.

## Open follow-ups (C3)

- `position_monitor.close_position_by_id` → close on the position's account creds (reuse `MetaApiClient` + `client_for_broker`); then the `_TOKEN`/`_ACCOUNT` globals can be fully removed.
