"""A restart must not duplicate dashboard rows.

The state store is in-memory on the box, so a restart rebuilds open signals
from tg_orders -- which carries no apis_pos_id. The first mirror pass then
opened a SECOND apis_position for every still-live slice (live 2026-09-18
09:57: ea400c79 / 236c1f81 next to the originals), leaving the originals
orphaned and the Open count / P&L double-counted. The ENTRY order of every row
already stores the slice's broker ref (apis_order.broker_order_id), so a slice
without an apis_pos_id must look its row up by that ref before creating one.
"""
import asyncio
import json
import types

import live_trader as lt
from state_store import make_store


def _tag(idx):
    return f"tg-14117-tp{idx}"


class _FakeBroker:
    def get_open_positions(self, symbol=None):
        return [{"id": "119216074", "openPrice": 4387.7, "profit": 5.0,
                 "currentPrice": 4386.0, "comment": _tag(4)}]

    def get_pending_orders(self, symbol=None):
        return []

    def get_position_realized_pnl(self, position_id):
        return None


class _FakeDash:
    """Knows one pre-existing row for broker ref 119216074."""

    def __init__(self):
        self.rows = {"119216074": "apis-existing-tp4"}
        self.opened, self.live, self.lookups = [], [], []

    def find_open_position_id(self):
        return None

    def find_position_by_broker_ref(self, ref):
        self.lookups.append(ref)
        return self.rows.get(ref)

    def open_position(self, side, entry, volume, broker_ticket=None):
        self.opened.append(broker_ticket)
        return f"apis-new-{broker_ticket}"

    def update_live(self, pid, ltp, profit_loss):
        self.live.append(pid)

    def conclude_position(self, *a, **k):
        pass


def _hydrated_signal():
    # as db.load_open_signals rebuilds it: broker_state known, no apis_pos_id
    return {
        "msg_id": 14117, "side": "sell", "entry_mid": 4387.0, "total_volume": 0.12,
        "orders": [{
            "tp_index": 4, "tp": 4379.0, "sl": 4387.0, "entry": 4387.0, "volume": 0.03,
            "ticket_id": "119216074", "kind": "market", "account": "primary",
            "broker_state": "filled", "observed": True, "fill_price": 4387.7,
        }],
    }


def test_rehydrated_live_slice_reuses_its_existing_row(monkeypatch):
    broker, dash = _FakeBroker(), _FakeDash()

    async def _go():
        lt.r, _ = await make_store(None)
        await lt.r.set(f"{lt.REDIS_PREFIX}:signal:14117", json.dumps(_hydrated_signal()))
        await lt.r.sadd(f"{lt.REDIS_PREFIX}:open", "14117")
        await lt.reconcile_broker()
        return json.loads(await lt.r.get(f"{lt.REDIS_PREFIX}:signal:14117"))

    monkeypatch.setattr(lt, "ACCOUNTS", [{"label": "primary", "client": broker, "risk_usd": 100.0, "apis": dash}])
    monkeypatch.setattr(lt, "ACCOUNTS_BY_LABEL", {"primary": broker})
    monkeypatch.setattr(lt, "APIS_BY_LABEL", {"primary": dash})
    monkeypatch.setattr(lt, "db", types.SimpleNamespace(
        record_fill=lambda *a, **k: None, record_slice_close=lambda *a, **k: None,
        conclude_signal=lambda *a, **k: None))
    pos = asyncio.run(_go())

    assert dash.opened == [], "must not open a second row for a slice that already has one"
    assert dash.lookups == ["119216074"]
    assert pos["orders"][0]["apis_pos_id"] == "apis-existing-tp4"
    assert dash.live == ["apis-existing-tp4"], "live P&L keeps flowing into the ORIGINAL row"
