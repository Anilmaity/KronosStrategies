"""Account-divergence: place_order must submit every mirrored account
CONCURRENTLY, not one-after-another.

Sequential submission made neymar2 decide its entry ~4-5 broker round-trips after
primary (a spec fetch, a price fetch, and 3 order POSTs run inside primary's
submit_signal_orders first), so on a fast retrace into the zone neymar2 fetched a
later/worse price and more often flipped to a market entry past the edge — a
systematic our-side fill divergence. This test pins concurrent fan-out: a
Barrier(2) is only satisfied if both accounts' submit calls are in flight at the
same time; sequential submission would deadlock the barrier and time out.
"""
import asyncio
import threading

import live_trader as lt


def _sig():
    return {"side": "buy", "instrument": "XAUUSD", "entry_low": 1995.0,
            "entry_high": 2000.0, "sl": 1990.0, "tps": [2005.0, 2010.0, 2015.0]}


def test_place_order_submits_accounts_concurrently(monkeypatch):
    barrier = threading.Barrier(2, timeout=3)

    class _C:
        def __init__(self, label):
            self.label = label

        def submit_signal_orders(self, side, symbol, entry, sl, tps,
                                 total_volume, msg_id):
            barrier.wait()   # both accounts must be in-flight together
            ve = round(total_volume / len(tps), 2)
            return [{"tp_index": i + 1, "tp": tps[i], "ticket_id": f"{self.label}-{i+1}",
                     "kind": "market", "volume": ve, "entry": entry, "sl": sl}
                    for i in range(len(tps))]

    accounts = [
        {"label": "primary", "client": _C("primary"), "risk_usd": 100.0, "apis": None},
        {"label": "neymar2", "client": _C("neymar2"), "risk_usd": 100.0, "apis": None},
    ]
    monkeypatch.setattr(lt, "ACCOUNTS", accounts)

    pos = asyncio.run(lt.place_order(123, _sig()))

    labels = {o["account"] for o in pos["orders"]}
    assert labels == {"primary", "neymar2"}      # both fanned out
    assert pos["status"] == "submitted"
    assert pos["total_volume"] > 0                # primary volume recorded
    assert len(pos["orders"]) == 6               # 3 TPs x 2 accounts


def test_place_order_one_account_failing_does_not_drop_the_other(monkeypatch):
    """A concurrent submit that raises for one account must not abort the other's
    already-placed orders — we still track what was submitted."""
    class _Good:
        def submit_signal_orders(self, side, symbol, entry, sl, tps, total_volume, msg_id):
            ve = round(total_volume / len(tps), 2)
            return [{"tp_index": 1, "tp": tps[0], "ticket_id": "g-1", "kind": "market",
                     "volume": ve, "entry": entry, "sl": sl}]

    class _Bad:
        def submit_signal_orders(self, *a, **k):
            raise RuntimeError("broker exploded")

    accounts = [
        {"label": "primary", "client": _Good(), "risk_usd": 100.0, "apis": None},
        {"label": "neymar2", "client": _Bad(), "risk_usd": 100.0, "apis": None},
    ]
    monkeypatch.setattr(lt, "ACCOUNTS", accounts)

    pos = asyncio.run(lt.place_order(456, _sig()))
    labels = {o["account"] for o in pos["orders"]}
    assert labels == {"primary"}                 # good account preserved
    assert pos["status"] == "submitted"
