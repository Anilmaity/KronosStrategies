"""Realized-PnL fidelity: the recorded realized PnL of a closed slice must come
from the broker's TRUE deal history, not the last live (unrealized) snapshot.

Background: reconcile_broker used to stamp realized_pnl = o["last_profit"], the
`profit` field from the last poll where the position was still OPEN — up to
BROKER_POLL_SEC x ABSENT_CONFIRM_POLLS (~60s) before the real close. Between
that snapshot and the actual TP/SL fill the price moves, so the figure (and the
tp/sl outcome inferred from its sign) was systematically wrong. MetaAPI's
history-deals endpoint exposes the real close deal; these tests pin the new
behaviour and its defensive snapshot fallback.
"""
import metaapi_orders as mx


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise mx.requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _client():
    c = mx.MetaApiClient("tok", "acct", dry_run=False, label="t")
    c._trading_url = "https://test-host"  # short-circuit region lookup
    return c


def test_realized_pnl_sums_profit_commission_swap(monkeypatch):
    """Realized = profit + commission + swap across the position's deals; close
    price comes from the OUT deal; closed=True once an OUT deal exists."""
    deals = [
        {"entryType": "DEAL_ENTRY_IN", "price": 2000.0, "profit": 0.0,
         "commission": -0.50, "swap": 0.0},
        {"entryType": "DEAL_ENTRY_OUT", "price": 2010.0, "profit": 10.0,
         "commission": -0.50, "swap": -0.20},
    ]
    monkeypatch.setattr(mx, "_request", lambda *a, **k: _FakeResp(deals))
    out = _client().get_position_realized_pnl("pos-123")
    assert out is not None
    assert out["realized_pnl"] == 8.80      # 10 - 0.5 - 0.5 - 0.2
    assert out["close_price"] == 2010.0
    assert out["closed"] is True


def test_realized_pnl_none_when_no_out_deal(monkeypatch):
    """A position with only an entry deal (not yet settled) is not terminal:
    closed=False so the caller keeps the snapshot fallback."""
    deals = [{"entryType": "DEAL_ENTRY_IN", "price": 2000.0, "profit": 0.0}]
    monkeypatch.setattr(mx, "_request", lambda *a, **k: _FakeResp(deals))
    out = _client().get_position_realized_pnl("pos-1")
    assert out is not None
    assert out["closed"] is False


def test_realized_pnl_none_on_empty(monkeypatch):
    monkeypatch.setattr(mx, "_request", lambda *a, **k: _FakeResp([]))
    assert _client().get_position_realized_pnl("pos-1") is None


def test_realized_pnl_none_on_error(monkeypatch):
    def boom(*a, **k):
        raise mx.requests.ConnectionError("broker down")
    monkeypatch.setattr(mx, "_request", boom)
    assert _client().get_position_realized_pnl("pos-1") is None


def test_realized_pnl_none_without_creds_or_dryrun():
    assert mx.MetaApiClient("", "", dry_run=False).get_position_realized_pnl("p") is None
    assert mx.MetaApiClient("t", "a", dry_run=True).get_position_realized_pnl("p") is None
    assert _client().get_position_realized_pnl("") is None
