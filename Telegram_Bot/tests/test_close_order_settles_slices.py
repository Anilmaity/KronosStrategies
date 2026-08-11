"""A signal ended by a CHANNEL REPLY must settle its slices, like broker truth does.

close_order closed the legs at the broker and dropped the signal from the ':open'
set, but never marked the slices closed, never recorded their per-slice outcome,
and used db.close_signal (status only) instead of db.conclude_signal (status +
realized). Once out of the open set reconcile_broker can never revisit them, so
the legs stayed 'filled' with NULL realized forever.

Live case, signal 11001 (2026-08-10): TP1 was settled by reconcile at 10:37:58;
the 'tp3' reply at 11:02 closed TP2-4 at the broker, which really paid +25.24 /
+25.36 / +24.72 — none of it recorded. The signal's realized_pnl is still NULL
while the dashboard (rebuilt from broker deals) shows the true +83.92.
"""
import asyncio
import json
import types

import live_trader as lt
from state_store import make_store


class _FakeBroker:
    def __init__(self):
        self.closed, self.cancelled = [], []

    def close_position(self, ticket):
        self.closed.append(ticket)
        return True

    def cancel_order(self, ticket):
        self.cancelled.append(ticket)
        return True

    def get_position_realized_pnl(self, position_id):
        paid = {"bp-2": 25.24, "bp-3": 25.36, "bp-4": 24.72}
        if position_id not in paid:
            return None
        return {"realized_pnl": paid[position_id], "close_price": 4335.84,
                "closed": True}


class _FakeDash:
    def __init__(self):
        self.concluded = []

    def conclude_position(self, pid, realized, close_price=None, side=None,
                          volume=None, reason="EXIT"):
        self.concluded.append({"pid": pid, "realized": realized})


def _seed():
    """TP1 already settled by reconcile; TP2-4 still open at the broker."""
    def leg(i, state, realized):
        return {"tp_index": i, "tp": 4340.0 - 2 * (i - 1), "sl": 4349.0,
                "entry": 4342.0, "volume": 0.04, "ticket_id": f"t-{i}",
                "kind": "market", "account": "primary", "broker_state": state,
                "observed": True, "broker_position_id": f"bp-{i}",
                "apis_pos_id": f"apis-{i}", "realized_pnl": realized,
                "last_profit": 1.0, "last_price": 4336.0}

    return {
        "msg_id": 11001, "side": "sell", "entry_mid": 4342.0, "total_volume": 0.16,
        "orders": [leg(1, "closed", 8.60), leg(2, "filled", None),
                   leg(3, "filled", None), leg(4, "filled", None)],
    }


def _run(monkeypatch, seed=None):
    broker, dash = _FakeBroker(), _FakeDash()
    calls = {"slice_close": [], "conclude": [], "close_signal": []}

    async def _go():
        lt.r, _ = await make_store(None)
        await lt.r.set(f"{lt.REDIS_PREFIX}:signal:11001",
                       json.dumps(seed if seed is not None else _seed()))
        await lt.r.sadd(f"{lt.REDIS_PREFIX}:open", "11001")
        await lt.close_order(11001, "tp3")
        return json.loads(await lt.r.get(f"{lt.REDIS_PREFIX}:signal:11001"))

    monkeypatch.setattr(lt, "ACCOUNTS", [
        {"label": "primary", "client": broker, "risk_usd": 100.0, "apis": dash}])
    monkeypatch.setattr(lt, "ACCOUNTS_BY_LABEL", {"primary": broker})
    monkeypatch.setattr(lt, "db", types.SimpleNamespace(
        record_slice_close=lambda *a, **k: calls["slice_close"].append(a),
        conclude_signal=lambda *a, **k: calls["conclude"].append(a),
        close_signal=lambda *a, **k: calls["close_signal"].append(a),
    ))
    return asyncio.run(_go()), calls, broker, dash


def test_reply_close_marks_slices_closed(monkeypatch):
    pos, _, _, _ = _run(monkeypatch)
    assert [o["broker_state"] for o in pos["orders"]] == ["closed"] * 4, \
        "legs closed by a reply must not stay 'filled' forever"


def test_reply_close_records_each_slice_outcome(monkeypatch):
    _, calls, _, _ = _run(monkeypatch)
    recorded = {a[1]: a[4] for a in calls["slice_close"]}   # tp_index -> pnl
    assert recorded == {2: 25.24, 3: 25.36, 4: 24.72}, \
        "each newly-closed leg records its own settled PnL"


def test_reply_close_stamps_signal_realized(monkeypatch):
    _, calls, _, _ = _run(monkeypatch)
    assert calls["conclude"], "must conclude the signal, not just set its status"
    msg_id, reason, realized = calls["conclude"][-1][:3]
    assert (msg_id, reason) == (11001, "tp3")
    assert round(realized, 2) == 83.92, "8.60 + 25.24 + 25.36 + 24.72"
    assert not calls["close_signal"], "close_signal loses realized_pnl"


def test_already_settled_slice_is_not_re_recorded(monkeypatch):
    _, calls, _, _ = _run(monkeypatch)
    assert 1 not in {a[1] for a in calls["slice_close"]}, \
        "TP1 was already settled by reconcile; don't overwrite it"


def test_unfilled_legs_are_cancelled_not_closed(monkeypatch):
    seed = _seed()
    seed["orders"][3].update(broker_state="pending", kind="limit",
                             apis_pos_id=None, realized_pnl=None)
    pos, calls, broker, _ = _run(monkeypatch, seed)
    assert pos["orders"][3]["broker_state"] == "cancelled"
    assert broker.cancelled == ["t-4"]
    cancelled = [a for a in calls["slice_close"] if a[1] == 4]
    assert cancelled and cancelled[0][3] == "cancelled" and cancelled[0][4] is None
    # a never-filled leg contributes nothing to the signal total
    assert round(calls["conclude"][-1][2], 2) == 59.20
