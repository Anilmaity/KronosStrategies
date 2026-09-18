"""A slice that closes at the broker must flatten ITS dashboard row right away.

Live 2026-09-18 09:39-09:41: TP1/TP2/TP3 of signal 14117 hit at the broker and
were recorded closed in tg_orders (+8.85 / +14.22 / +20.34), but their
apis_position rows stayed open (qty 0.03, mark frozen at the TP price) because
the dashboard mirror only concluded rows once EVERY slice of the signal was
terminal. With the runner leg still live behind a breakeven stop that can take
hours, during which the dashboard shows closed trades as open, counts them in
"Open", and totals a stale unrealized instead of the realized figure.
"""
import asyncio
import json
import types

import live_trader as lt
from state_store import make_store


def _slice_tag(msg_id, idx):
    return f"tg-{msg_id}-tp{idx}"

_SLICES = [
    {"idx": 1, "ticket": "119216070", "tp": 4385.0, "realized": 8.85, "close_px": 4385.25},
    {"idx": 2, "ticket": "119216071", "tp": 4383.0, "realized": None, "close_px": None},
    {"idx": 3, "ticket": "119216072", "tp": 4381.0, "realized": None, "close_px": None},
    {"idx": 4, "ticket": "119216074", "tp": 4379.0, "realized": None, "close_px": None},
]


class _FakeBroker:
    """Slice 1 has left the broker (TP hit); slices 2-4 are still open."""

    def get_open_positions(self, symbol=None):
        return [{"id": s["ticket"], "openPrice": 4387.7, "profit": 5.0, "currentPrice": 4386.0,
                 "comment": _slice_tag(14117, s["idx"])}
                for s in _SLICES if s["idx"] != 1]

    def get_pending_orders(self, symbol=None):
        return []

    def get_position_realized_pnl(self, position_id):
        s = next((s for s in _SLICES if s["ticket"] == position_id), None)
        if s is None or s["realized"] is None:
            return None
        return {"realized_pnl": s["realized"], "close_price": s["close_px"], "closed": True}


class _FakeDash:
    def __init__(self):
        self.concluded, self.live = [], []

    def find_open_position_id(self):
        return None

    def open_position(self, side, entry, volume, broker_ticket=None):
        return f"apis-{broker_ticket}"

    def update_live(self, pid, ltp, profit_loss):
        self.live.append(pid)

    def conclude_position(self, pid, realized, close_price=None, side=None,
                          volume=None, reason="EXIT"):
        self.concluded.append({"pid": pid, "realized": realized, "close_price": close_price,
                               "volume": volume, "reason": reason})


def _seed():
    return {
        "msg_id": 14117, "side": "sell", "entry_mid": 4387.0, "total_volume": 0.12,
        "orders": [{
            "tp_index": s["idx"], "tp": s["tp"], "sl": 4387.0, "entry": 4387.0,
            "volume": 0.03, "ticket_id": s["ticket"], "kind": "market",
            "account": "primary", "broker_state": "filled", "observed": True,
            "broker_position_id": s["ticket"], "fill_price": 4387.7,
            "apis_pos_id": f"apis-{s['ticket']}",
            "last_profit": 5.0, "last_price": 4386.0,
        } for s in _SLICES],
    }


def _run(monkeypatch):
    broker, dash = _FakeBroker(), _FakeDash()
    signal_concluded = []

    async def _go():
        lt.r, _ = await make_store(None)
        await lt.r.set(f"{lt.REDIS_PREFIX}:signal:14117", json.dumps(_seed()))
        await lt.r.sadd(f"{lt.REDIS_PREFIX}:open", "14117")
        await lt.reconcile_broker()
        return json.loads(await lt.r.get(f"{lt.REDIS_PREFIX}:signal:14117"))

    monkeypatch.setattr(lt, "ACCOUNTS", [{"label": "primary", "client": broker, "risk_usd": 100.0, "apis": dash}])
    monkeypatch.setattr(lt, "ACCOUNTS_BY_LABEL", {"primary": broker})
    monkeypatch.setattr(lt, "APIS_BY_LABEL", {"primary": dash})
    monkeypatch.setattr(lt, "ABSENT_CONFIRM_POLLS", 1)
    monkeypatch.setattr(lt, "db", types.SimpleNamespace(
        record_fill=lambda *a, **k: None,
        record_slice_close=lambda *a, **k: None,
        conclude_signal=lambda *a, **k: signal_concluded.append(a),
    ))
    return asyncio.run(_go()), dash, signal_concluded


def test_closed_slice_row_is_concluded_while_signal_still_open(monkeypatch):
    pos, dash, signal_concluded = _run(monkeypatch)
    assert [c["pid"] for c in dash.concluded] == ["apis-119216070"], \
        "TP1's own dashboard row must flatten as soon as the broker closes it"
    c = dash.concluded[0]
    assert c["realized"] == 8.85 and c["close_price"] == 4385.25 and c["volume"] == 0.03
    assert c["reason"] == "broker_tp"
    assert not signal_concluded, "three legs are still live -- the signal is not over"
    assert pos["orders"][0]["broker_state"] == "closed"
    assert [o["broker_state"] for o in pos["orders"][1:]] == ["filled"] * 3


def test_live_slices_keep_refreshing_and_closed_one_does_not(monkeypatch):
    _, dash, _ = _run(monkeypatch)
    assert "apis-119216070" not in dash.live
    assert set(dash.live) == {"apis-119216071", "apis-119216072", "apis-119216074"}
