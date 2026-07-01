"""End-to-end regression for the reported NeymarGoldTrader bug:

  "Telegram group shows TP1/TP2/TP3 hit, but the record shows a -$20 LOSS, the
   entry and exit price are the SAME, and it is labelled Stop Loss."

Root cause: the scalp closed inside one 30s poll, so the slice's only live
snapshot was the entry-moment unrealized (~ -spread). The broker deal lookup
either wasn't attempted (position id never captured) or was tried once and
missed, so the code recorded that tiny negative snapshot as realized — which is
< 0, so it was labelled SL, with no real close price (exit fell back to entry).

Fix (capture position id at submit + retry until the deal settles) makes the
signal conclude from BROKER TRUTH: the real +PnL, the TARGET label, and the real
close price so exit != entry. This test drives the whole reconcile path and
pins that outcome.
"""
import asyncio
import json
import types

import live_trader as lt
from state_store import make_store


class _WinScalp:
    """A TP win that only settles in history-deals after the position vanishes —
    exactly the fast-scalp timing that used to mislabel as SL."""
    def __init__(self):
        self.calls = 0

    def get_open_positions(self, symbol=None):
        return []

    def get_pending_orders(self, symbol=None):
        return []

    def get_position_realized_pnl(self, position_id):
        self.calls += 1
        if self.calls == 1:
            return None  # not settled on the first attempt
        # settled: real win, real close price well above the 2000 entry
        return {"realized_pnl": 21.0, "close_price": 2015.0, "closed": True}


class _Dash:
    def __init__(self):
        self.concluded = []

    def update_live(self, pid, ltp, pnl):
        pass

    def open_position(self, side, entry, vol, ticket=None):
        return "apis-1"

    def find_open_position_id(self):
        return None

    def conclude_position(self, pid, realized, close_price=None, side=None,
                          volume=None, reason="EXIT"):
        self.concluded.append({"realized": realized, "close_price": close_price,
                               "reason": reason})


def _seed_fast_scalp():
    # entry_mid 2000; the slice was seen live exactly once at the entry moment,
    # so its snapshot is a tiny spread loss — the value that used to be recorded.
    return {
        "msg_id": 6900, "side": "buy", "entry_mid": 2000.0, "total_volume": 0.02,
        "apis_pos_ids": {"primary": "apis-1"},
        "orders": [{
            "tp_index": 1, "tp": 2015.0, "sl": 1990.0, "entry": 2000.0,
            "volume": 0.02, "ticket_id": "pos-1", "kind": "market",
            "account": "primary", "broker_state": "filled", "observed": True,
            "broker_position_id": "pos-1",
            "last_profit": -0.7, "last_price": 2000.1,
        }],
    }


def test_fast_tp_scalp_concludes_from_broker_truth_not_snapshot(monkeypatch):
    broker, dash = _WinScalp(), _Dash()
    account = {"label": "primary", "client": broker, "risk_usd": 100.0, "apis": dash}
    monkeypatch.setattr(lt, "ACCOUNTS", [account])
    monkeypatch.setattr(lt, "ACCOUNTS_BY_LABEL", {"primary": broker})
    monkeypatch.setattr(lt, "APIS_BY_LABEL", {"primary": dash})
    monkeypatch.setattr(lt, "ABSENT_CONFIRM_POLLS", 1)
    monkeypatch.setattr(lt, "DEAL_SETTLE_POLLS", 5)
    concluded = {}
    monkeypatch.setattr(lt, "db", types.SimpleNamespace(
        record_fill=lambda *a, **k: None,
        record_slice_close=lambda *a, **k: None,
        conclude_signal=lambda *a, **k: concluded.setdefault("args", a)))

    async def _go():
        lt.r, _ = await make_store(None)
        await lt.r.set(f"{lt.REDIS_PREFIX}:signal:6900", json.dumps(_seed_fast_scalp()))
        await lt.r.sadd(f"{lt.REDIS_PREFIX}:open", "6900")
        await lt.reconcile_broker()   # poll 1: deal unsettled -> wait, do NOT conclude
        await lt.reconcile_broker()   # poll 2: settled -> conclude from broker truth
        raw = await lt.r.get(f"{lt.REDIS_PREFIX}:signal:6900")
        return json.loads(raw)

    pos = asyncio.run(_go())

    # Symptom 1 fixed: recorded PnL is the broker's real +21, not the -0.7 snapshot.
    assert pos["status"] == "closed_broker_tp"          # TARGET, not StopLoss
    assert concluded["args"][1] == "broker_tp"
    assert concluded["args"][2] == 21.0
    # Symptom 2 fixed: exit (close price) != entry (2000).
    assert dash.concluded[-1]["close_price"] == 2015.0
    assert dash.concluded[-1]["realized"] == 21.0
    assert "tp" in dash.concluded[-1]["reason"]
