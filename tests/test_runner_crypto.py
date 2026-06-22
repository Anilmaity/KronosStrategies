import os

import pytest
from cryptography.fernet import Fernet


def _set_key(monkeypatch):
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode())


def test_round_trip(monkeypatch):
    _set_key(monkeypatch)
    from strategies.shared.crypto import encrypt_token, decrypt_token
    cipher = encrypt_token("tok-SECRET-1234")
    assert cipher != "tok-SECRET-1234"
    assert decrypt_token(cipher) == "tok-SECRET-1234"


def test_empty_passthrough(monkeypatch):
    _set_key(monkeypatch)
    from strategies.shared.crypto import encrypt_token, decrypt_token
    assert encrypt_token("") == ""
    assert decrypt_token("") == ""


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("FIELD_ENCRYPTION_KEY", raising=False)
    from strategies.shared.crypto import encrypt_token
    with pytest.raises(RuntimeError):
        encrypt_token("x")
