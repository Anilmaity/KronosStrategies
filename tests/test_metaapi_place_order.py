from cryptography.fernet import Fernet


def test_place_market_order_posts_to_account_url(monkeypatch):
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("META_REGION", "new-york")
    from strategies.shared import metaapi_client as mc

    captured = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"positionId": "POS-1", "stringCode": "TRADE_RETCODE_DONE"}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return _Resp()

    monkeypatch.setattr(mc.requests, "post", _fake_post)

    client = mc.MetaApiClient("acct-XYZ", "tok-ABC")
    pos = client.place_market_order(
        side="BUY", symbol="XAU_USD", volume=0.01,
        stop_loss=4490.0, take_profit=4520.0, entry_price=4500.0,
    )
    assert pos == "POS-1"
    assert "/accounts/acct-XYZ/trade" in captured["url"]
    assert captured["headers"]["auth-token"] == "tok-ABC"


def test_place_market_order_captures_broker_error(monkeypatch):
    """2026-08-02 observability fix: a broker rejection must leave the real
    reason on client._last_order_error so entry_manager can persist
    'metaapi_rejection: <detail>' instead of a bare string."""
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("META_REGION", "new-york")
    monkeypatch.setenv("META_ORDER_MAX_RETRIES", "0")
    from strategies.shared import metaapi_client as mc

    class _Resp:
        status_code = 400
        text = '{"error":"TradeError","message":"invalid stops"}'

        def raise_for_status(self):
            raise mc.requests.HTTPError(response=self)

        def json(self):
            return {}

    monkeypatch.setattr(mc.requests, "post",
                        lambda url, headers=None, json=None, timeout=None: _Resp())

    client = mc.MetaApiClient("acct-XYZ", "tok-ABC")
    pos = client.place_market_order(
        side="BUY", symbol="XAU_USD", volume=0.01,
        stop_loss=4490.0, take_profit=4520.0,  # no entry_price → skip stops-floor GET
    )
    assert pos is None
    assert "HTTP 400" in client._last_order_error
    assert "invalid stops" in client._last_order_error
