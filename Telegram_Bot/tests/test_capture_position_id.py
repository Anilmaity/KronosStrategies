"""Fast scalps close inside one 30s poll, so a slice is often seen live 0 times
— and broker_position_id was only captured when a slice WAS seen live. Without
it, _slice_realized_pnl skips the history-deals lookup and records the
entry-moment unrealized snapshot (~-spread), which mislabels a TP win as SL and
shows entry==exit.

For MARKET orders MetaAPI returns positionId == orderId, so the ticket we get
back IS the position id — capture it at submit time. Limit tickets are order ids
(the position id is only assigned on fill), so those are still captured later.
"""
import metaapi_orders as mx


def _client_market():
    c = mx.MetaApiClient("tok", "acct", dry_run=False, label="t")
    c._trading_url = "https://h"
    c.get_symbol_price = lambda symbol: {"bid": 1999.8, "ask": 2000.0}
    c._get_symbol_spec = lambda b: {"stops_level_price": 3.0, "tick_size": 0.01}
    c.place_market_order_full = lambda side, symbol, volume, sl, tp, comment="": f"pos-{round(tp,2)}"
    return c


def test_market_slices_capture_broker_position_id_at_submit():
    c = _client_market()
    # market plan: price already at/through entry so use_market is chosen
    plan = {"use_market": True,
            "levels": [(1990.0, 2005.0), (1990.0, 2010.0), (1990.0, 2015.0)],
            "min_d": 3.0, "cur": 2000.0}
    submitted = c.submit_signal_orders("buy", "XAUUSD", 2000.0, 1990.0,
                                       [2005.0, 2010.0, 2015.0], 0.06, 4242, plan=plan)
    assert len(submitted) == 3
    for s in submitted:
        assert s["kind"] == "market"
        # the market ticket IS the position id -> captured immediately
        assert s["broker_position_id"] == s["ticket_id"]


def test_limit_slices_do_not_capture_position_id_at_submit():
    c = mx.MetaApiClient("tok", "acct", dry_run=False, label="t")
    c._trading_url = "https://h"
    c.get_symbol_price = lambda symbol: {"bid": 1999.8, "ask": 2000.0}
    c._get_symbol_spec = lambda b: {"stops_level_price": 3.0, "tick_size": 0.01}
    c.place_limit_order = lambda side, symbol, volume, entry, sl, tp, current_price=None, comment="": (f"ord-{round(tp,2)}", None)
    plan = {"use_market": False,
            "levels": [(1990.0, 2005.0), (1990.0, 2010.0)],
            "min_d": 3.0, "cur": 2000.0}
    submitted = c.submit_signal_orders("buy", "XAUUSD", 1995.0, 1990.0,
                                       [2005.0, 2010.0], 0.04, 4243, plan=plan)
    assert len(submitted) == 2
    for s in submitted:
        assert s["kind"] == "limit"
        # order id != position id; must be captured later when the limit fills
        assert s.get("broker_position_id") is None
