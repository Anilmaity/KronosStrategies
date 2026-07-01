"""reconcile_broker must conclude a closed slice with the broker's TRUE realized
PnL (from history-deals), not the stale last-live `profit` snapshot.

This is the end-to-end regression for the snapshot-PnL inaccuracy: a slice whose
last open snapshot showed +5 but which actually settled at +42 (price kept
running into TP between polls) must be recorded as +42 on both the audit DB and
the dashboard. When the deal lookup fails it must fall back to the snapshot so
live trading is never blocked.
"""
import asyncio
import json
import types

import live_trader as lt
from state_store import make_store


class _FakeBroker:
    """Stands in for one account's MetaApiClient: the slice is gone from the
    broker, and history-deals reports the real settled figure."""
    def __init__(self, realized):
        self._realized = realized
        self.deal_calls = []

    def get_open_positions(self, symbol=None):
        return []           # slice no longer live

    def get_pending_orders(self, symbol=None):
        return []

    def get_position_realized_pnl(self, position_id):
        self.deal_calls.append(position_id)
        if self._realized is None:
            return None     # lookup unavailable -> caller falls back to snapshot
        return {"realized_pnl": self._realized, "close_price": 2015.0, "closed": True}


class _FakeDash:
    def __init__(self):
        self.concluded = []

    def find_open_position_id(self):
        return None

    def open_position(self, side, entry, vol, ticket=None):
        return "apis-pos-1"

    def conclude_position(self, pid, realized, close_price=None, side=None,
                          volume=None, reason="EXIT"):
        self.concluded.append({"pid": pid, "realized": realized,
                               "close_price": close_price, "reason": reason})


def _seed_signal_with_filled_slice():
    return {
        "msg_id": 555,
        "side": "buy",
        "entry_mid": 2000.0,
        "total_volume": 0.02,
        "orders": [{
            "tp_index": 1, "tp": 2015.0, "sl": 1990.0, "entry": 2000.0,
            "volume": 0.02, "ticket_id": "order-1", "kind": "market",
            "account": "primary", "broker_state": "filled", "observed": True,
            "broker_position_id": "bpos-1",
            "last_profit": 5.0,   # STALE snapshot — must NOT be the recorded realized
            "last_price": 2001.0,
        }],
    }


def _run_reconcile_with(monkeypatch, broker, dash):
    async def _go():
        lt.r, _ = await make_store(None)        # in-memory state store
        await lt.r.set(f"{lt.REDIS_PREFIX}:signal:555", json.dumps(_seed_signal_with_filled_slice()))
        await lt.r.sadd(f"{lt.REDIS_PREFIX}:open", "555")
        await lt.reconcile_broker()
        raw = await lt.r.get(f"{lt.REDIS_PREFIX}:signal:555")
        return json.loads(raw)

    account = {"label": "primary", "client": broker, "risk_usd": 100.0, "apis": dash}
    monkeypatch.setattr(lt, "ACCOUNTS", [account])
    monkeypatch.setattr(lt, "ACCOUNTS_BY_LABEL", {"primary": broker})
    monkeypatch.setattr(lt, "APIS_BY_LABEL", {"primary": dash})
    monkeypatch.setattr(lt, "ABSENT_CONFIRM_POLLS", 1)
    monkeypatch.setattr(lt, "DEAL_SETTLE_POLLS", 1)  # disable settle-wait: fall back at once

    db_calls = {}
    fake_db = types.SimpleNamespace(
        record_fill=lambda *a, **k: None,
        record_slice_close=lambda *a, **k: db_calls.setdefault("slice_close", []).append(a),
        conclude_signal=lambda *a, **k: db_calls.setdefault("conclude", []).append(a),
    )
    monkeypatch.setattr(lt, "db", fake_db)
    return asyncio.run(_go()), db_calls


def test_reconcile_records_deal_pnl_not_snapshot(monkeypatch):
    broker, dash = _FakeBroker(realized=42.0), _FakeDash()
    pos, db_calls = _run_reconcile_with(monkeypatch, broker, dash)

    slice0 = pos["orders"][0]
    assert slice0["broker_state"] == "closed"
    assert slice0["realized_pnl"] == 42.0          # deal truth, not the 5.0 snapshot
    assert broker.deal_calls == ["bpos-1"]         # looked up by captured position id

    # audit DB conclude got the real total
    assert db_calls["conclude"][-1][2] == 42.0
    # dashboard conclude got the real realized
    assert dash.concluded and dash.concluded[-1]["realized"] == 42.0
    assert dash.concluded[-1]["reason"] == "broker_tp"


def test_reconcile_falls_back_to_snapshot_when_deal_unavailable(monkeypatch):
    broker, dash = _FakeBroker(realized=None), _FakeDash()
    pos, db_calls = _run_reconcile_with(monkeypatch, broker, dash)

    slice0 = pos["orders"][0]
    assert slice0["broker_state"] == "closed"
    assert slice0["realized_pnl"] == 5.0           # snapshot fallback preserved
    assert broker.deal_calls == ["bpos-1"]         # we did try the deal lookup
