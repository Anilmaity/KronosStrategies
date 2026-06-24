"""The channel-driven close path (close_order, fired by a TG 'SL hit'/'all TPs'
reply) must also mirror the broker's TRUE realized PnL to the dashboard, not the
stale last-live snapshot."""
import asyncio
import json
import types

import live_trader as lt
from state_store import make_store


class _FakeBroker:
    def __init__(self, realized):
        self._realized = realized
        self.closed = []

    def close_position(self, ticket):
        self.closed.append(ticket)
        return True

    def cancel_order(self, ticket):
        return True

    def get_position_realized_pnl(self, position_id):
        if self._realized is None:
            return None
        return {"realized_pnl": self._realized, "close_price": 1990.0, "closed": True}


class _FakeDash:
    def __init__(self):
        self.concluded = []

    def conclude_position(self, pid, realized, close_price=None, side=None,
                          volume=None, reason="EXIT"):
        self.concluded.append({"realized": realized, "reason": reason})


def _seed():
    return {
        "msg_id": 777, "side": "buy", "entry_mid": 2000.0, "total_volume": 0.02,
        "apis_pos_ids": {"primary": "apis-1"},
        "orders": [{
            "tp_index": 1, "tp": 2015.0, "sl": 1990.0, "entry": 2000.0,
            "volume": 0.02, "ticket_id": "order-1", "kind": "market",
            "account": "primary", "broker_state": "filled",
            "broker_position_id": "bpos-9",
            "last_profit": -3.0,    # stale snapshot; real SL fill settled at -20
            "last_price": 1995.0,
        }],
    }


def test_close_order_uses_deal_pnl(monkeypatch):
    broker, dash = _FakeBroker(realized=-20.0), _FakeDash()
    account = {"label": "primary", "client": broker, "risk_usd": 100.0, "apis": dash}
    monkeypatch.setattr(lt, "ACCOUNTS", [account])
    monkeypatch.setattr(lt, "ACCOUNTS_BY_LABEL", {"primary": broker})
    monkeypatch.setattr(lt, "db", types.SimpleNamespace(close_signal=lambda *a, **k: None))

    async def _go():
        lt.r, _ = await make_store(None)
        await lt.r.set(f"{lt.REDIS_PREFIX}:signal:777", json.dumps(_seed()))
        await lt.r.sadd(f"{lt.REDIS_PREFIX}:open", "777")
        await lt.close_order(777, "sl")

    asyncio.run(_go())
    assert broker.closed == ["order-1"]
    assert dash.concluded and dash.concluded[-1]["realized"] == -20.0  # deal, not -3.0


def test_close_order_falls_back_to_snapshot(monkeypatch):
    broker, dash = _FakeBroker(realized=None), _FakeDash()
    account = {"label": "primary", "client": broker, "risk_usd": 100.0, "apis": dash}
    monkeypatch.setattr(lt, "ACCOUNTS", [account])
    monkeypatch.setattr(lt, "ACCOUNTS_BY_LABEL", {"primary": broker})
    monkeypatch.setattr(lt, "db", types.SimpleNamespace(close_signal=lambda *a, **k: None))

    async def _go():
        lt.r, _ = await make_store(None)
        await lt.r.set(f"{lt.REDIS_PREFIX}:signal:777", json.dumps(_seed()))
        await lt.r.sadd(f"{lt.REDIS_PREFIX}:open", "777")
        await lt.close_order(777, "sl")

    asyncio.run(_go())
    assert dash.concluded[-1]["realized"] == -3.0   # snapshot fallback
