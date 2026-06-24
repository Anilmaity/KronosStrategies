"""Account-divergence, deeper fix: mirrored accounts must place IDENTICAL SL/TP
levels. Previously each account independently re-decided market-vs-limit and
re-applied _apply_stops_floor against its OWN broker price + stops-level, so
neymar2's TP could be widened to a level it never hit while primary caught it
(sig 6877: neymar2 TP1 widened to 4201.92). place_order now builds ONE plan
(strictest stops-floor across accounts, reference price from the first account)
and every account places those exact levels — only the fill price differs by
broker spread, which is unavoidable.
"""
import asyncio

import live_trader as lt
import metaapi_orders as mx


def _make_client(label, ask, stops):
    """Real MetaApiClient with its broker price + stops-level mocked, and order
    placement captured instead of sent over HTTP."""
    c = mx.MetaApiClient("tok", f"acct-{label}", dry_run=False, label=label)
    c._trading_url = "https://h"
    c.get_symbol_price = lambda symbol, _a=ask: {"bid": _a - 0.2, "ask": _a}
    c._get_symbol_spec = lambda b, _s=stops: {"stops_level_price": _s, "tick_size": 0.01}
    c.placed = []

    def rec_market(side, symbol, volume, sl, tp, comment=""):
        c.placed.append(("market", round(sl, 2), round(tp, 2), volume))
        return f"{label}-m-{len(c.placed)}"

    def rec_limit(side, symbol, volume, entry, sl, tp, current_price=None, comment=""):
        c.placed.append(("limit", round(sl, 2), round(tp, 2), volume))
        return f"{label}-l-{len(c.placed)}", None

    c.place_market_order_full = rec_market
    c.place_limit_order = rec_limit
    return c


def _sig():
    return {"side": "buy", "instrument": "XAUUSD", "entry_low": 1995.0,
            "entry_high": 2000.0, "sl": 1990.0, "tps": [2005.0, 2010.0, 2015.0]}


def test_accounts_place_identical_tp_sl_levels(monkeypatch):
    # Different prices AND different stops-levels: under the old per-account logic
    # these would yield different TP/SL levels.
    primary = _make_client("primary", ask=2000.5, stops=3.0)
    neymar2 = _make_client("neymar2", ask=2001.0, stops=8.0)
    accounts = [
        {"label": "primary", "client": primary, "risk_usd": 100.0, "apis": None},
        {"label": "neymar2", "client": neymar2, "risk_usd": 50.0, "apis": None},
    ]
    monkeypatch.setattr(lt, "ACCOUNTS", accounts)

    pos = asyncio.run(lt.place_order(1, _sig()))

    p_levels = [(sl, tp) for _, sl, tp, _ in primary.placed]
    n_levels = [(sl, tp) for _, sl, tp, _ in neymar2.placed]
    assert len(p_levels) == 3
    assert p_levels == n_levels                       # IDENTICAL targets across accounts
    # strictest stops (8.0) applied against the reference ask (2000.5):
    # TP1 (2005) is within 8.0 of 2000.5 -> floored up to 2008.5; SL stays 1990.
    assert p_levels[0] == (1990.0, 2008.5)
    # per-account sizing still differs (risk 100 vs 50): vol 0.1 vs 0.05 / 3 TPs
    assert primary.placed[0][3] != neymar2.placed[0][3]
    # kind is decided once -> both market
    assert {k for k, *_ in primary.placed} == {"market"}
    assert {k for k, *_ in neymar2.placed} == {"market"}


def test_plan_built_once_from_reference_account(monkeypatch):
    """Only the reference (first) account's price drives the market/limit + level
    decision; the second account does not re-fetch its own price for the plan."""
    primary = _make_client("primary", ask=2000.5, stops=3.0)
    neymar2 = _make_client("neymar2", ask=2001.0, stops=3.0)
    n_price_calls = {"n": 0}
    neymar2.get_symbol_price = lambda symbol: (n_price_calls.__setitem__("n", n_price_calls["n"] + 1)
                                               or {"bid": 2000.8, "ask": 2001.0})
    accounts = [
        {"label": "primary", "client": primary, "risk_usd": 100.0, "apis": None},
        {"label": "neymar2", "client": neymar2, "risk_usd": 100.0, "apis": None},
    ]
    monkeypatch.setattr(lt, "ACCOUNTS", accounts)

    asyncio.run(lt.place_order(2, _sig()))
    # market plan -> neymar2 never needs its own price (no INVALID_PRICE fallback)
    assert n_price_calls["n"] == 0
