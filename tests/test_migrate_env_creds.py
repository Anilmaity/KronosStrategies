import os

from cryptography.fernet import Fernet


def test_build_cred_payload_encrypts(monkeypatch):
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode())
    from strategies.db.migrate_env_creds_to_accounts import build_cred_payload
    from strategies.shared.crypto import decrypt_token
    payload = build_cred_payload("acct-XYZ", "tok-PLAIN-9999")
    assert payload["meta_account_id"] == "acct-XYZ"
    assert payload["meta_api_token_enc"] != "tok-PLAIN-9999"
    assert decrypt_token(payload["meta_api_token_enc"]) == "tok-PLAIN-9999"


def test_build_cred_payload_idempotent_shape(monkeypatch):
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode())
    from strategies.db.migrate_env_creds_to_accounts import build_cred_payload
    p = build_cred_payload("a", "b")
    assert set(p.keys()) == {"meta_account_id", "meta_api_token_enc"}
