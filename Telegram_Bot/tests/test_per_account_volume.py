"""conclude_position must record each account's OWN volume, not primary's.

pos["total_volume"] is primary-only; passing it for neymar2's close-order row
gave neymar2 a wrong quantity on the dashboard whenever the two accounts size
differently (different risk). Cosmetic (PnL unaffected) but a copy-fidelity
defect.
"""
import asyncio
import json
import types

import live_trader as lt
from state_store import make_store


class _FakeBroker:
    def close_position(self, ticket):
        return True

    def cancel_order(self, ticket):
        return True

    def get_position_realized_pnl(self, position_id):
        return None  # force snapshot path; volume is what we're testing


class _FakeDash:
    def __init__(self):
        self.concluded = []

    def conclude_position(self, pid, realized, close_price=None, side=None,
                          volume=None, reason="EXIT"):
        self.concluded.append({"volume": volume, "realized": realized})


def test_close_order_records_per_account_volume(monkeypatch):
    broker = _FakeBroker()
    dash_p, dash_n = _FakeDash(), _FakeDash()
    accounts = [
        {"label": "primary", "client": broker, "risk_usd": 100.0, "apis": dash_p},
        {"label": "neymar2", "client": broker, "risk_usd": 50.0, "apis": dash_n},
    ]
    monkeypatch.setattr(lt, "ACCOUNTS", accounts)
    monkeypatch.setattr(lt, "ACCOUNTS_BY_LABEL", {"primary": broker, "neymar2": broker})
    monkeypatch.setattr(lt, "db", types.SimpleNamespace(close_signal=lambda *a, **k: None))

    def _slice(label, vol):
        # Each slice carries its OWN dashboard row (one row per broker position).
        return {"tp_index": 1, "tp": 2015.0, "sl": 1990.0, "entry": 2000.0,
                "volume": vol, "ticket_id": f"{label}-1", "kind": "market",
                "account": label, "broker_state": "filled",
                "broker_position_id": f"bp-{label}", "last_profit": 1.0,
                "last_price": 2010.0, "apis_pos_id": f"apis-{label}"}

    pos = {
        "msg_id": 888, "side": "buy", "entry_mid": 2000.0, "total_volume": 0.10,
        "orders": [_slice("primary", 0.10), _slice("neymar2", 0.05)],
    }

    async def _go():
        lt.r, _ = await make_store(None)
        await lt.r.set(f"{lt.REDIS_PREFIX}:signal:888", json.dumps(pos))
        await lt.r.sadd(f"{lt.REDIS_PREFIX}:open", "888")
        await lt.close_order(888, "sl")

    asyncio.run(_go())
    assert dash_p.concluded[-1]["volume"] == 0.10   # primary's own size
    assert dash_n.concluded[-1]["volume"] == 0.05   # neymar2's own size, not 0.10
