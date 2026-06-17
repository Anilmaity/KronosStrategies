"""
metaapi_orders must support more than one MetaAPI account in a single process,
so one Telegram listener can fan the same signal out to two accounts.

The refactor introduces a MetaApiClient class (per-account token / trading-host /
spec cache) while keeping the module-level functions working as a delegating
default client, so the single-account path is unchanged.
"""
import metaapi_orders as mx


def test_client_isolates_account_and_caches():
    a = mx.MetaApiClient("tokA", "acctA", dry_run=True)
    b = mx.MetaApiClient("tokB", "acctB", dry_run=True)
    assert a.account == "acctA"
    assert b.account == "acctB"
    a._spec_cache["XAUUSD"] = {"stops_level_price": 1.0, "tick_size": 0.01}
    assert "XAUUSD" not in b._spec_cache  # caches are per-instance, not shared


def test_dry_run_market_order_returns_ticket():
    a = mx.MetaApiClient("t", "acc", dry_run=True)
    tid = a.place_market_order_full("buy", "XAUUSD", 0.01, 1990.0, 2010.0, "tg-1-tp1")
    assert tid == "dry-run"


def test_dry_run_limit_order_returns_ticket_and_no_retcode():
    a = mx.MetaApiClient("t", "acc", dry_run=True)
    tid, rc = a.place_limit_order("buy", "XAUUSD", 0.01, 2000.0, 1990.0, 2010.0, 2000.0, "tg-1-tp1")
    assert tid == "dry-run"
    assert rc is None


def test_dry_run_submit_signal_orders_one_per_tp():
    a = mx.MetaApiClient("t", "acc", dry_run=True)
    orders = a.submit_signal_orders(
        side="buy", symbol="XAUUSD", entry=2000.0, sl=1990.0,
        tps=[2005.0, 2010.0, 2015.0], total_volume=0.06, msg_id=42)
    assert len(orders) == 3
    assert {o["tp_index"] for o in orders} == {1, 2, 3}
    assert all(o["ticket_id"] == "dry-run" for o in orders)
    assert all(o["volume"] == 0.02 for o in orders)


def test_dry_run_position_queries_return_empty_not_none():
    a = mx.MetaApiClient("t", "acc", dry_run=True)
    assert a.get_open_positions("XAUUSD") == []
    assert a.get_pending_orders("XAUUSD") == []


def test_module_level_default_delegates():
    # back-compat: the single-account module API still exists and is callable
    for name in ("submit_signal_orders", "modify_position_sl", "cancel_order",
                 "close_position", "get_open_positions", "get_pending_orders",
                 "get_symbol_price", "place_market_order_full", "place_limit_order"):
        assert callable(getattr(mx, name)), name
