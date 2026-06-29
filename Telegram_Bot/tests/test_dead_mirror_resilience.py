"""A dead/undeployed MIRROR account must never freeze trading on the healthy one.

Regression for the 2026-06-26 outage: the neymar2 mirror account (c9bf3b9d) was
undeployed at MetaAPI and returned HTTP 404 on every positions/orders query.
Because both broker-verification paths aborted the *entire* pipeline when *any*
single account was unreachable, the result was:

  - reconcile_broker() returned early every cycle, so the primary's open signal
    never concluded and `:open` never drained;
  - with a stuck open signal, _classify_open_signals() returned ok=False, so every
    later signal hit "broker check failed — skip (no pyramiding, fail-safe)" and
    placed NO trade — not even on the perfectly healthy primary account.

Net effect: Telegram posted signals (7350, 7371) and the platform took no trade.

The contract these tests pin: a verification step fails safe ONLY when it cannot
verify a SINGLE account. An account that is individually unreachable is excluded
from the decision (it holds no orders for the new signal anyway) so the healthy
accounts keep trading and concluding.
"""
import asyncio
import json
import types

import live_trader as lt
from state_store import make_store


class _Acct:
    """Fake MetaApiClient. positions=None simulates an unreachable/undeployed
    broker (the neymar2 404); a list is healthy broker truth."""
    def __init__(self, positions):
        self._positions = positions

    def get_open_positions(self, symbol=None):
        return self._positions

    def get_pending_orders(self, symbol=None):
        return self._positions


def _set_accounts(monkeypatch, accounts):
    monkeypatch.setattr(lt, "ACCOUNTS", accounts)
    monkeypatch.setattr(lt, "ACCOUNTS_BY_LABEL",
                        {a["label"]: a["client"] for a in accounts})


def _classify(monkeypatch, primary_positions, mirror_positions):
    accounts = [
        {"label": "primary", "client": _Acct(primary_positions), "risk_usd": 100.0, "apis": None},
        {"label": "neymar2", "client": _Acct(mirror_positions), "risk_usd": 100.0, "apis": None},
    ]
    _set_accounts(monkeypatch, accounts)

    async def _go():
        lt.r, _ = await make_store(None)
        await lt.r.set(f"{lt.REDIS_PREFIX}:signal:7334", json.dumps({"msg_id": 7334}))
        return await lt._classify_open_signals(["7334"])

    return asyncio.run(_go())


def test_classify_excludes_unreachable_mirror_but_still_verifies(monkeypatch):
    # primary healthy with no live position; neymar2 undeployed (None).
    live, unfilled, ok = _classify(monkeypatch, primary_positions=[], mirror_positions=None)
    assert ok is True              # reachable primary => we DID verify (not fail-safe skip)
    assert unfilled == ["7334"]    # no live position on primary => supersedable
    assert live == []


def test_classify_sees_live_position_on_reachable_account(monkeypatch):
    # signal IS live on the healthy primary; mirror still dead.
    live, unfilled, ok = _classify(
        monkeypatch, primary_positions=[{"comment": "tg-7334-tp1"}], mirror_positions=None)
    assert ok is True
    assert live == ["7334"]        # correctly seen live => still no pyramiding
    assert unfilled == []


def test_classify_fails_safe_only_when_no_account_verifiable(monkeypatch):
    # BOTH accounts unreachable => genuinely can't verify => fail safe (ok=False).
    live, unfilled, ok = _classify(monkeypatch, primary_positions=None, mirror_positions=None)
    assert ok is False


def test_reconcile_concludes_primary_signal_when_mirror_unreachable(monkeypatch):
    """reconcile must process the healthy account even when a mirror 404s, so the
    primary's signal concludes and :open drains (un-wedging the pyramiding guard)."""
    class _Primary:
        def get_open_positions(self, symbol=None):
            return []                       # the filled slice has left the broker
        def get_pending_orders(self, symbol=None):
            return []
        def get_position_realized_pnl(self, pid):
            return {"realized_pnl": 12.0, "close_price": 4027.0, "closed": True}

    class _DeadMirror:
        def get_open_positions(self, symbol=None):
            return None                     # undeployed / 404
        def get_pending_orders(self, symbol=None):
            return None

    primary, mirror = _Primary(), _DeadMirror()
    accounts = [
        {"label": "primary", "client": primary, "risk_usd": 100.0, "apis": None},
        {"label": "neymar2", "client": mirror, "risk_usd": 100.0, "apis": None},
    ]
    monkeypatch.setattr(lt, "ACCOUNTS", accounts)
    monkeypatch.setattr(lt, "ACCOUNTS_BY_LABEL", {"primary": primary, "neymar2": mirror})
    monkeypatch.setattr(lt, "APIS_BY_LABEL", {"primary": None, "neymar2": None})
    monkeypatch.setattr(lt, "ABSENT_CONFIRM_POLLS", 1)

    db_calls = {}
    monkeypatch.setattr(lt, "db", types.SimpleNamespace(
        record_fill=lambda *a, **k: None,
        record_slice_close=lambda *a, **k: db_calls.setdefault("slice_close", []).append(a),
        conclude_signal=lambda *a, **k: db_calls.setdefault("conclude", []).append(a),
    ))

    async def _go():
        lt.r, _ = await make_store(None)
        seed = {
            "msg_id": 7334, "side": "sell", "entry_mid": 4034.0, "total_volume": 0.05,
            "orders": [{
                "tp_index": 1, "tp": 4027.0, "sl": 4041.0, "entry": 4034.0, "volume": 0.05,
                "ticket_id": "order-1", "kind": "market", "account": "primary",
                "broker_state": "filled", "observed": True, "broker_position_id": "bpos-1",
                "last_profit": 8.0, "last_price": 4028.0,
            }],
        }
        await lt.r.set(f"{lt.REDIS_PREFIX}:signal:7334", json.dumps(seed))
        await lt.r.sadd(f"{lt.REDIS_PREFIX}:open", "7334")
        await lt.reconcile_broker()
        members = await lt.r.smembers(f"{lt.REDIS_PREFIX}:open")
        raw = await lt.r.get(f"{lt.REDIS_PREFIX}:signal:7334")
        return members, json.loads(raw)

    members, pos = asyncio.run(_go())
    assert pos["orders"][0]["broker_state"] == "closed"   # primary slice concluded
    assert "7334" not in members                          # drained from :open (un-wedged)
    assert "conclude" in db_calls                         # audit row written
