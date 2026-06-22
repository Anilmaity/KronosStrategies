import types

import pytest
from cryptography.fernet import Fernet


def _set_key(monkeypatch):
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode())


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter_by(self, **kw):
        return self

    def first(self):
        return self._result


class _FakeSession:
    def __init__(self, broker):
        self._broker = broker

    def query(self, model):
        return _FakeQuery(self._broker)


def _broker(meta_account_id, meta_api_token_enc):
    return types.SimpleNamespace(
        meta_account_id=meta_account_id, meta_api_token_enc=meta_api_token_enc
    )


def test_returns_client_when_creds_present(monkeypatch):
    _set_key(monkeypatch)
    from strategies.shared import metaapi_client as mc
    from strategies.shared.crypto import encrypt_token
    mc._CLIENT_CACHE.clear()
    enc = encrypt_token("tok-REAL-1234")
    sess = _FakeSession(_broker("acct-777", enc))
    client = mc.client_for_broker(sess, "ub-1")
    assert isinstance(client, mc.MetaApiClient)
    assert client.account_id == "acct-777"
    assert client.token == "tok-REAL-1234"


def test_none_when_no_account_id(monkeypatch):
    _set_key(monkeypatch)
    from strategies.shared import metaapi_client as mc
    mc._CLIENT_CACHE.clear()
    sess = _FakeSession(_broker("", "whatever"))
    assert mc.client_for_broker(sess, "ub-1") is None


def test_none_when_no_token(monkeypatch):
    _set_key(monkeypatch)
    from strategies.shared import metaapi_client as mc
    mc._CLIENT_CACHE.clear()
    sess = _FakeSession(_broker("acct-1", ""))
    assert mc.client_for_broker(sess, "ub-1") is None


def test_none_when_broker_missing(monkeypatch):
    _set_key(monkeypatch)
    from strategies.shared import metaapi_client as mc
    mc._CLIENT_CACHE.clear()
    sess = _FakeSession(None)
    assert mc.client_for_broker(sess, "ub-1") is None


def test_none_on_decrypt_failure(monkeypatch):
    _set_key(monkeypatch)
    from strategies.shared import metaapi_client as mc
    mc._CLIENT_CACHE.clear()
    sess = _FakeSession(_broker("acct-1", "not-valid-fernet-ciphertext"))
    assert mc.client_for_broker(sess, "ub-1") is None
