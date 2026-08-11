"""One dashboard row PER broker slice, not one clubbed row per signal.

The channel posts a signal with N take-profits; the bot places N *separate*
broker positions (0.03 x 4, not 0.12 x 1). Mirroring them as a single
apis_position hid the real trades behind an average and — because the clubbed
row's ENTRY order carried only slice 1's ticket — let fill_reconciler restate
the whole signal from one slice (live 2026-08-10: -$80.52 shown as -$19.65).

Each slice is its own real trade at the broker, so each gets its own row,
carrying its own volume, its own fill price, its own broker position id and its
own realized PnL.
"""
import asyncio
import json
import types

import live_trader as lt
from state_store import make_store

# The real 2026-08-10 signal 10985: one buy, four TP legs, stopped out.
_SLICES = [
    {"idx": 1, "ticket": "110323030", "bpos": "110323030", "tp": 4353.0,
     "fill": 4350.48, "realized": -19.65, "close_px": 4343.93},
    {"idx": 2, "ticket": "110323035", "bpos": "110323035", "tp": 4355.0,
     "fill": 4350.50, "realized": -20.28, "close_px": 4343.93},
    {"idx": 3, "ticket": "110323041", "bpos": "110323041", "tp": 4357.0,
     "fill": 4350.52, "realized": -20.28, "close_px": 4343.93},
    {"idx": 4, "ticket": "110323045", "bpos": "110323045", "tp": 4359.0,
     "fill": 4350.55, "realized": -20.31, "close_px": 4343.93},
]


class _FakeBroker:
    """Every slice has left the broker; history-deals reports each settled leg."""

    def __init__(self):
        self.deal_calls = []

    def get_open_positions(self, symbol=None):
        return []

    def get_pending_orders(self, symbol=None):
        return []

    def get_position_realized_pnl(self, position_id):
        self.deal_calls.append(position_id)
        s = next((s for s in _SLICES if s["bpos"] == position_id), None)
        if s is None:
            return None
        return {"realized_pnl": s["realized"], "close_price": s["close_px"],
                "closed": True}


class _FakeDash:
    def __init__(self):
        self.opened, self.concluded = [], []
        self._n = 0

    def find_open_position_id(self):
        return None

    def open_position(self, side, entry, volume, broker_ticket=None):
        self._n += 1
        pid = f"apis-{self._n}"
        self.opened.append({"pid": pid, "side": side, "entry": entry,
                            "volume": volume, "ticket": broker_ticket})
        return pid

    def update_live(self, pid, ltp, profit_loss):
        pass

    def conclude_position(self, pid, realized, close_price=None, side=None,
                          volume=None, reason="EXIT"):
        self.concluded.append({"pid": pid, "realized": realized, "volume": volume,
                               "close_price": close_price, "reason": reason})


def _seed():
    return {
        "msg_id": 10985,
        "side": "buy",
        "entry_mid": 4351.2,
        "total_volume": 0.12,
        "orders": [{
            "tp_index": s["idx"], "tp": s["tp"], "sl": 4344.0, "entry": 4351.2,
            "volume": 0.03, "ticket_id": s["ticket"], "kind": "market",
            "account": "primary", "broker_state": "filled", "observed": True,
            "broker_position_id": s["bpos"], "fill_price": s["fill"],
            "last_profit": -5.0, "last_price": 4349.0,
        } for s in _SLICES],
    }


def _run(monkeypatch, dash):
    broker = _FakeBroker()

    async def _go():
        lt.r, _ = await make_store(None)
        await lt.r.set(f"{lt.REDIS_PREFIX}:signal:10985", json.dumps(_seed()))
        await lt.r.sadd(f"{lt.REDIS_PREFIX}:open", "10985")
        await lt.reconcile_broker()
        return json.loads(await lt.r.get(f"{lt.REDIS_PREFIX}:signal:10985"))

    account = {"label": "primary", "client": broker, "risk_usd": 100.0, "apis": dash}
    monkeypatch.setattr(lt, "ACCOUNTS", [account])
    monkeypatch.setattr(lt, "ACCOUNTS_BY_LABEL", {"primary": broker})
    monkeypatch.setattr(lt, "APIS_BY_LABEL", {"primary": dash})
    monkeypatch.setattr(lt, "ABSENT_CONFIRM_POLLS", 1)
    monkeypatch.setattr(lt, "db", types.SimpleNamespace(
        record_fill=lambda *a, **k: None,
        record_slice_close=lambda *a, **k: None,
        conclude_signal=lambda *a, **k: None,
    ))
    return asyncio.run(_go()), dash


def test_one_dashboard_row_per_slice(monkeypatch):
    _, dash = _run(monkeypatch, _FakeDash())
    assert len(dash.opened) == 4, "four broker trades must be four dashboard rows"
    assert [o["volume"] for o in dash.opened] == [0.03] * 4, \
        "each row carries its own slice volume, never the clubbed 0.12"


def test_each_row_carries_its_own_broker_position_id(monkeypatch):
    _, dash = _run(monkeypatch, _FakeDash())
    assert [o["ticket"] for o in dash.opened] == [s["bpos"] for s in _SLICES], \
        "ENTRY order must reference the slice's own MetaAPI position id"


def test_each_row_opens_at_its_own_fill_price(monkeypatch):
    _, dash = _run(monkeypatch, _FakeDash())
    assert [o["entry"] for o in dash.opened] == [s["fill"] for s in _SLICES]


def test_each_row_concludes_with_its_own_realized(monkeypatch):
    _, dash = _run(monkeypatch, _FakeDash())
    assert len(dash.concluded) == 4
    assert [c["realized"] for c in dash.concluded] == [s["realized"] for s in _SLICES]
    assert [c["volume"] for c in dash.concluded] == [0.03] * 4
    # and the parts still add up to the broker's true total for the signal
    assert round(sum(c["realized"] for c in dash.concluded), 2) == -80.52


def test_slice_rows_are_distinct(monkeypatch):
    _, dash = _run(monkeypatch, _FakeDash())
    pids = [c["pid"] for c in dash.concluded]
    assert len(set(pids)) == 4, "each slice concludes its own row"
    assert set(pids) == {o["pid"] for o in dash.opened}


def test_slice_dashboard_ids_persist_on_the_order(monkeypatch):
    pos, _ = _run(monkeypatch, _FakeDash())
    ids = [o.get("apis_pos_id") for o in pos["orders"]]
    assert all(ids), "each slice remembers its dashboard row for later updates"
    assert len(set(ids)) == 4
