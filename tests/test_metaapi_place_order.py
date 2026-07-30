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
