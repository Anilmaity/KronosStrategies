"""position_monitor must record the BROKER's settled realized PnL + close price
when it flattens a position, not the OANDA-mid model figure it computes from its
own trigger price. The decision is isolated in `_resolve_realized_close` so it
can be unit-tested without a DB or broker.

Model figure = (trigger_price - entry) * qty * 100 — ignores fill slippage,
spread, commission and swap, so a trade the monitor models as +$56 can be ~$0
or negative in the real account. When the broker's history-deals lookup returns
a settled deal we must prefer it; otherwise fall back to the model so a close is
never lost waiting on the broker.
"""
import position_monitor as pm


def test_prefers_broker_settled_realized_and_close_price():
    lookup = lambda pid: {"realized_pnl": 1.20, "close_price": 2011.4, "closed": True}
    realized, close_price, source = pm._resolve_realized_close(
        model_realized=56.0, model_close_price=2012.0,
        broker_pid="pos-1", lookup=lookup)
    assert realized == 1.20           # broker truth, not the +56 model
    assert close_price == 2011.4      # real broker close, not the OANDA-mid 2012.0
    assert source == "broker"


def test_broker_closed_but_no_close_price_falls_back_to_model_price():
    lookup = lambda pid: {"realized_pnl": -3.0, "close_price": None, "closed": True}
    realized, close_price, source = pm._resolve_realized_close(
        model_realized=56.0, model_close_price=2012.0,
        broker_pid="pos-1", lookup=lookup)
    assert realized == -3.0
    assert close_price == 2012.0      # keep the model close price when broker omits it
    assert source == "broker"


def test_lookup_unavailable_falls_back_to_model():
    realized, close_price, source = pm._resolve_realized_close(
        model_realized=56.0, model_close_price=2012.0,
        broker_pid="pos-1", lookup=lambda pid: None)
    assert realized == 56.0
    assert close_price == 2012.0
    assert source == "model"


def test_unsettled_broker_deal_not_trusted():
    # closed=False means the exit deal hasn't landed in history yet — do NOT
    # record a half-settled figure; keep the model until it settles.
    lookup = lambda pid: {"realized_pnl": 0.0, "close_price": None, "closed": False}
    realized, close_price, source = pm._resolve_realized_close(
        model_realized=56.0, model_close_price=2012.0,
        broker_pid="pos-1", lookup=lookup)
    assert source == "model"
    assert realized == 56.0


def test_no_broker_pid_uses_model():
    calls = []
    def lookup(pid):
        calls.append(pid)
        return {"realized_pnl": 1.0, "close_price": 1.0, "closed": True}
    for pid in (None, "", "dry-run"):
        realized, close_price, source = pm._resolve_realized_close(
            model_realized=56.0, model_close_price=2012.0,
            broker_pid=pid, lookup=lookup)
        assert source == "model"
    assert calls == []                # never hit the broker without a real id
