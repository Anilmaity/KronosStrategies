"""
Fan-out: one Telegram listener mirrors each signal onto N MetaAPI accounts.

The correctness that matters for live money: every tracked slice is routed to —
and reconciled against — ONLY its own account. A position that exists on the
primary account must never mark the second account's slice filled, and a slice
absent from its own account must not be concluded using another account's broker
state.
"""
import asyncio
import json

import live_trader as lt
from state_store import MemoryStore


# ── account construction ─────────────────────────────────────────────────────
def test_build_accounts_single_when_no_second_creds(monkeypatch):
    monkeypatch.delenv("TG2_META_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("TG2_META_API_TOKEN", raising=False)
    accts = lt._build_accounts()
    assert [a["label"] for a in accts] == ["primary"]


def test_build_accounts_adds_second_when_creds_present(monkeypatch):
    monkeypatch.setenv("TG2_META_ACCOUNT_ID", "acct-2")
    monkeypatch.setenv("TG2_META_API_TOKEN", "tok-2")
    accts = lt._build_accounts()
    assert [a["label"] for a in accts] == ["primary", "neymar2"]
    assert accts[1]["client"].account == "acct-2"


# ── fan-out placement ────────────────────────────────────────────────────────
def _dry(label):
    import metaapi_orders as mx
    return mx.MetaApiClient("t", f"acc-{label}", dry_run=True, label=label)


def test_place_order_fans_out_and_tags_each_slice(monkeypatch):
    monkeypatch.setattr(lt, "ACCOUNTS", [
        {"label": "primary", "client": _dry("primary"), "risk_usd": 100.0},
        {"label": "neymar2", "client": _dry("neymar2"), "risk_usd": 100.0},
    ])
    sig = {"side": "buy", "instrument": "XAUUSD",
           "entry_low": 1999.0, "entry_high": 2000.0, "sl": 1990.0,
           "tps": [2005.0, 2010.0, 2015.0]}
    pos = asyncio.run(lt.place_order(1, sig))
    assert len(pos["orders"]) == 6
    by_acct = {}
    for o in pos["orders"]:
        by_acct.setdefault(o["account"], []).append(o)
        assert o["broker_state"] in ("filled", "pending")
        assert o["observed"] is False
    assert set(by_acct) == {"primary", "neymar2"}
    assert len(by_acct["primary"]) == 3 and len(by_acct["neymar2"]) == 3


# ── per-account reconciliation (the safety-critical part) ─────────────────────
class _FakeClient:
    def __init__(self, label, positions, orders):
        self.label = label
        self._p, self._o = positions, orders

    def get_open_positions(self, symbol=None):
        return self._p

    def get_pending_orders(self, symbol=None):
        return self._o


def _silence_io(monkeypatch):
    for name in ("record_fill", "record_slice_close", "conclude_signal",
                 "insert_signal", "update_sl", "close_signal"):
        monkeypatch.setattr(lt.db, name, lambda *a, **k: None)
    for name in ("open_position", "update_live", "conclude_position",
                 "find_open_position_id"):
        monkeypatch.setattr(lt.apis, name, lambda *a, **k: None)


def test_reconcile_routes_state_per_account(monkeypatch):
    _silence_io(monkeypatch)
    store = MemoryStore()
    monkeypatch.setattr(lt, "r", store)

    # primary reports tp1 as a LIVE POSITION; neymar2 reports tp1 still PENDING.
    primary = _FakeClient(
        "primary",
        positions=[{"comment": "tg-1-tp1", "openPrice": 2000.0, "profit": 5.0, "currentPrice": 2003.0}],
        orders=[],
    )
    neymar2 = _FakeClient(
        "neymar2",
        positions=[],
        orders=[{"comment": "tg-1-tp1"}],
    )
    monkeypatch.setattr(lt, "ACCOUNTS", [
        {"label": "primary", "client": primary, "risk_usd": 100.0},
        {"label": "neymar2", "client": neymar2, "risk_usd": 100.0},
    ])

    pos = {
        "msg_id": 1, "side": "buy", "instrument": "XAUUSD", "entry_mid": 2000.0,
        "sl": 1990.0, "tps": [2005.0], "total_volume": 0.02,
        "orders": [
            {"tp_index": 1, "tp": 2005.0, "ticket_id": "P1", "kind": "limit",
             "volume": 0.01, "entry": 2000.0, "sl": 1990.0,
             "account": "primary", "broker_state": "pending", "observed": True, "miss": 0},
            {"tp_index": 1, "tp": 2005.0, "ticket_id": "N1", "kind": "limit",
             "volume": 0.01, "entry": 2000.0, "sl": 1990.0,
             "account": "neymar2", "broker_state": "pending", "observed": True, "miss": 0},
        ],
    }
    asyncio.run(store.set(f"{lt.REDIS_PREFIX}:signal:1", json.dumps(pos)))
    asyncio.run(store.sadd(f"{lt.REDIS_PREFIX}:open", "1"))

    asyncio.run(lt.reconcile_broker())

    out = json.loads(asyncio.run(store.get(f"{lt.REDIS_PREFIX}:signal:1")))
    states = {o["account"]: o["broker_state"] for o in out["orders"]}
    # primary slice promoted to filled by ITS OWN position; neymar2 still pending.
    assert states["primary"] == "filled"
    assert states["neymar2"] == "pending"


def test_signal_concludes_only_when_all_accounts_terminal(monkeypatch):
    _silence_io(monkeypatch)
    monkeypatch.setattr(lt, "ABSENT_CONFIRM_POLLS", 1)
    store = MemoryStore()
    monkeypatch.setattr(lt, "r", store)

    # Both accounts report nothing live (empty broker). Primary slice is already
    # terminal (closed); neymar2 slice is still pending but observed — so on this
    # poll it goes absent->cancelled, making the whole signal terminal.
    primary = _FakeClient("primary", positions=[], orders=[])
    neymar2 = _FakeClient("neymar2", positions=[], orders=[])
    monkeypatch.setattr(lt, "ACCOUNTS", [
        {"label": "primary", "client": primary, "risk_usd": 100.0},
        {"label": "neymar2", "client": neymar2, "risk_usd": 100.0},
    ])

    pos = {
        "msg_id": 7, "side": "buy", "instrument": "XAUUSD", "entry_mid": 2000.0,
        "sl": 1990.0, "tps": [2005.0], "total_volume": 0.02,
        "orders": [
            {"tp_index": 1, "tp": 2005.0, "ticket_id": "P1", "kind": "market",
             "volume": 0.01, "entry": 2000.0, "sl": 1990.0, "account": "primary",
             "broker_state": "closed", "observed": True, "miss": 0},
            {"tp_index": 1, "tp": 2005.0, "ticket_id": "N1", "kind": "limit",
             "volume": 0.01, "entry": 2000.0, "sl": 1990.0, "account": "neymar2",
             "broker_state": "pending", "observed": True, "miss": 0},
        ],
    }
    asyncio.run(store.set(f"{lt.REDIS_PREFIX}:signal:7", json.dumps(pos)))
    asyncio.run(store.sadd(f"{lt.REDIS_PREFIX}:open", "7"))

    asyncio.run(lt.reconcile_broker())

    out = json.loads(asyncio.run(store.get(f"{lt.REDIS_PREFIX}:signal:7")))
    assert out["status"].startswith("closed_")
    assert "7" not in asyncio.run(store.smembers(f"{lt.REDIS_PREFIX}:open"))


class _FakeDash:
    def __init__(self, label):
        self.label = label; self.opened = []
    def open_position(self, side, entry, vol, ticket=None):
        self.opened.append((side, entry, vol)); return f"{self.label}-pos"
    def update_live(self, pid, ltp, pnl): pass
    def conclude_position(self, *a, **k): pass
    def find_open_position_id(self): return None


def test_each_account_opens_its_own_dashboard_position(monkeypatch):
    _silence_io(monkeypatch)
    store = MemoryStore(); monkeypatch.setattr(lt, "r", store)

    def fc(label):
        return _FakeClient(label,
            positions=[{"comment": "tg-1-tp1", "openPrice": 2000.0, "profit": 2.0, "currentPrice": 2002.0}],
            orders=[])
    dprim, dney = _FakeDash("primary"), _FakeDash("neymar2")
    monkeypatch.setattr(lt, "ACCOUNTS", [
        {"label": "primary", "client": fc("primary"), "risk_usd": 100.0, "apis": dprim},
        {"label": "neymar2", "client": fc("neymar2"), "risk_usd": 100.0, "apis": dney},
    ])
    pos = {
        "msg_id": 1, "side": "buy", "instrument": "XAUUSD", "entry_mid": 2000.0,
        "sl": 1990.0, "tps": [2005.0], "total_volume": 0.02,
        "orders": [
            {"tp_index": 1, "tp": 2005.0, "ticket_id": "P1", "kind": "market", "volume": 0.01,
             "entry": 2000.0, "sl": 1990.0, "account": "primary", "broker_state": "pending", "observed": True, "miss": 0},
            {"tp_index": 1, "tp": 2005.0, "ticket_id": "N1", "kind": "market", "volume": 0.01,
             "entry": 2000.0, "sl": 1990.0, "account": "neymar2", "broker_state": "pending", "observed": True, "miss": 0},
        ],
    }
    asyncio.run(store.set(f"{lt.REDIS_PREFIX}:signal:1", json.dumps(pos)))
    asyncio.run(store.sadd(f"{lt.REDIS_PREFIX}:open", "1"))

    asyncio.run(lt.reconcile_broker())

    assert dprim.opened, "primary dashboard position not opened"
    assert dney.opened, "neymar2 dashboard position not opened"
    out = json.loads(asyncio.run(store.get(f"{lt.REDIS_PREFIX}:signal:1")))
    assert out["apis_pos_ids"] == {"primary": "primary-pos", "neymar2": "neymar2-pos"}
