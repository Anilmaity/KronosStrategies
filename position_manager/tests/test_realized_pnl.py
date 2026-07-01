"""Broker-TRUE realized PnL for the automation strategies (Session Breakout etc).

position_monitor closes DB positions on its own OANDA-mid triggers and records
realized = (close - entry) * qty * 100 — a MODEL figure that ignores fill
slippage, spread, commission and swap, so the dashboard shows profit the real
MetaTrader5 account never earned. The fix reconciles against the broker's
settled deal. These tests pin the new reader that fetches it (mirrors the
Telegram bot's MetaApiClient.get_position_realized_pnl).
"""
from shared import metaapi_client as mc


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise mc.requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _configure(monkeypatch, resp_or_exc):
    """Give the module creds + a resolved host, and stub requests.get."""
    monkeypatch.setattr(mc, "_TOKEN", "tok")
    monkeypatch.setattr(mc, "_ACCOUNT", "acct")
    monkeypatch.setattr(mc, "_DRY_RUN", False)
    monkeypatch.setattr(mc, "_TRADING_URL", "https://test-host")  # short-circuit region lookup

    def _get(*a, **k):
        if isinstance(resp_or_exc, Exception):
            raise resp_or_exc
        return _FakeResp(resp_or_exc)

    monkeypatch.setattr(mc.requests, "get", _get)


def test_realized_pnl_sums_profit_commission_swap(monkeypatch):
    deals = [
        {"entryType": "DEAL_ENTRY_IN", "price": 2000.0, "profit": 0.0,
         "commission": -0.50, "swap": 0.0},
        {"entryType": "DEAL_ENTRY_OUT", "price": 2010.0, "profit": 10.0,
         "commission": -0.50, "swap": -0.20},
    ]
    _configure(monkeypatch, deals)
    out = mc.get_position_realized_pnl("pos-123")
    assert out is not None
    assert out["realized_pnl"] == 8.80          # 10 - 0.5 - 0.5 - 0.2
    assert out["close_price"] == 2010.0
    assert out["closed"] is True


def test_realized_pnl_not_closed_when_no_out_deal(monkeypatch):
    deals = [{"entryType": "DEAL_ENTRY_IN", "price": 2000.0, "profit": 0.0}]
    _configure(monkeypatch, deals)
    out = mc.get_position_realized_pnl("pos-1")
    assert out is not None
    assert out["closed"] is False


def test_realized_pnl_none_on_empty(monkeypatch):
    _configure(monkeypatch, [])
    assert mc.get_position_realized_pnl("pos-1") is None


def test_realized_pnl_none_on_error(monkeypatch):
    _configure(monkeypatch, mc.requests.ConnectionError("broker down"))
    assert mc.get_position_realized_pnl("pos-1") is None


def test_realized_pnl_none_without_creds_or_dryrun(monkeypatch):
    # dry-run
    monkeypatch.setattr(mc, "_DRY_RUN", True)
    monkeypatch.setattr(mc, "_TOKEN", "t")
    monkeypatch.setattr(mc, "_ACCOUNT", "a")
    assert mc.get_position_realized_pnl("p") is None
    # missing creds
    monkeypatch.setattr(mc, "_DRY_RUN", False)
    monkeypatch.setattr(mc, "_TOKEN", "")
    assert mc.get_position_realized_pnl("p") is None
    # empty position id
    monkeypatch.setattr(mc, "_TOKEN", "t")
    monkeypatch.setattr(mc, "_ACCOUNT", "a")
    assert mc.get_position_realized_pnl("") is None
