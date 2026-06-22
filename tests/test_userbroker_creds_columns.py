def test_userbroker_has_credential_columns():
    from strategies.shared.models import UserBroker
    cols = set(UserBroker.__table__.columns.keys())
    assert "meta_account_id" in cols
    assert "meta_api_token_enc" in cols
