"""A closed slice must WAIT for the broker's deal to settle before it concludes,
instead of finalizing on the entry-moment `last_profit` snapshot the moment the
position leaves the broker.

Fast scalps close inside one poll: the only snapshot is the just-opened
unrealized (~ -spread), so concluding on it records a tiny loss and mislabels a
TP win as SL. reconcile now retries the history-deals lookup for up to
DEAL_SETTLE_POLLS polls; it only falls back to the snapshot as a last resort so
live trading is still never blocked.
"""
import asyncio
import json
import types

import live_trader as lt
from state_store import make_store


class _SettleAfter:
    """history-deals returns None (unsettled) for the first `after` lookups, then
    a settled winning deal."""
    def __init__(self, after, realized=42.0, close_price=2015.0):
        self.after = after
        self.realized = realized
        self.close_price = close_price
        self.calls = 0

    def get_open_positions(self, symbol=None):
        return []          # slice already gone from the broker

    def get_pending_orders(self, symbol=None):
        return []

    def get_position_realized_pnl(self, position_id):
        self.calls += 1
        if self.calls <= self.after:
            return None
        return {"realized_pnl": self.realized, "close_price": self.close_price,
                "closed": True}


def _seed():
    return {
        "msg_id": 777, "side": "buy", "entry_mid": 2000.0, "total_volume": 0.02,
        "orders": [{
            "tp_index": 1, "tp": 2015.0, "sl": 1990.0, "entry": 2000.0,
            "volume": 0.02, "ticket_id": "order-1", "kind": "market",
            "account": "primary", "broker_state": "filled", "observed": True,
            "broker_position_id": "bpos-1",
            "last_profit": -0.7,    # entry-moment spread snapshot (must NOT win)
            "last_price": 2000.1,
        }],
    }


def _wire(monkeypatch, broker, settle_polls):
    account = {"label": "primary", "client": broker, "risk_usd": 100.0, "apis": None}
    monkeypatch.setattr(lt, "ACCOUNTS", [account])
    monkeypatch.setattr(lt, "ACCOUNTS_BY_LABEL", {"primary": broker})
    monkeypatch.setattr(lt, "ABSENT_CONFIRM_POLLS", 1)
    monkeypatch.setattr(lt, "DEAL_SETTLE_POLLS", settle_polls)
    db_calls = {}
    monkeypatch.setattr(lt, "db", types.SimpleNamespace(
        record_fill=lambda *a, **k: None,
        record_slice_close=lambda *a, **k: db_calls.setdefault("close", []).append(a),
        conclude_signal=lambda *a, **k: db_calls.setdefault("conclude", []).append(a)))
    return db_calls


async def _poll_once(seed_first):
    if seed_first:
        lt.r, _ = await make_store(None)
        await lt.r.set(f"{lt.REDIS_PREFIX}:signal:777", json.dumps(_seed()))
        await lt.r.sadd(f"{lt.REDIS_PREFIX}:open", "777")
    await lt.reconcile_broker()
    raw = await lt.r.get(f"{lt.REDIS_PREFIX}:signal:777")
    return json.loads(raw) if raw else None


def test_waits_for_settlement_then_records_broker_truth(monkeypatch):
    broker = _SettleAfter(after=2, realized=42.0, close_price=2015.0)
    db_calls = _wire(monkeypatch, broker, settle_polls=5)

    async def _go():
        pos = await _poll_once(seed_first=True)     # poll 1: unsettled -> wait
        assert pos["orders"][0]["broker_state"] == "filled"
        assert "conclude" not in db_calls
        pos = await _poll_once(seed_first=False)     # poll 2: unsettled -> wait
        assert pos["orders"][0]["broker_state"] == "filled"
        pos = await _poll_once(seed_first=False)     # poll 3: settled -> conclude
        return pos

    pos = asyncio.run(_go())
    slice0 = pos["orders"][0]
    assert slice0["broker_state"] == "closed"
    assert slice0["realized_pnl"] == 42.0            # broker truth, not the -0.7 snapshot
    assert slice0["last_price"] == 2015.0            # real close, so exit != entry
    assert db_calls["conclude"][-1][2] == 42.0


def test_falls_back_to_snapshot_after_max_wait(monkeypatch):
    broker = _SettleAfter(after=999)                 # never settles
    _wire(monkeypatch, broker, settle_polls=3)

    async def _go():
        await _poll_once(seed_first=True)             # poll 1: wait
        await _poll_once(seed_first=False)            # poll 2: wait
        return await _poll_once(seed_first=False)     # poll 3: last resort -> snapshot

    pos = asyncio.run(_go())
    slice0 = pos["orders"][0]
    assert slice0["broker_state"] == "closed"
    assert slice0["realized_pnl"] == -0.7            # snapshot fallback preserved (never blocked)
