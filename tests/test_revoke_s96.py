"""
test_revoke_s96.py
------------------
Unit tests for strategies/db/revoke_s96_live_eligibility.py — the one-off
idempotent script that revokes S96's live_eligible flag after the
2026-07-03 in-place logic rewrite (spec:
docs/superpowers/specs/2026-07-03-s96-m5-ema-cross-design.md).

All offline: in-memory SQLite via create_all on the mirrored metadata,
seeded through deploy_manager.seed() — same pattern as
test_deploy_manager.py.
"""
from __future__ import annotations

import os
import sys
import uuid
from decimal import Decimal

import pytest

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies")
)
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from db import deploy_manager  # noqa: E402
from db import revoke_s96_live_eligibility as revoke_mod  # noqa: E402

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

CurrencyPair = deploy_manager.CurrencyPair
ManagedStrategy = deploy_manager.ManagedStrategy
Strategy = deploy_manager.Strategy
UserBroker = deploy_manager.UserBroker
UserStrategy = deploy_manager.UserStrategy

S96_NAME = "S96 H1 Momentum"


@pytest.fixture()
def db():
    """In-memory SQLite pre-seeded with the full manager roster, then S96's
    ManagedStrategy manually granted live_eligible=True (mirroring the live
    DB state after the 2026-07-02 user approval)."""
    engine = create_engine("sqlite://")
    Strategy.metadata.create_all(engine)
    Sess = sessionmaker(bind=engine)
    sess = Sess()

    import shared.models as _sm
    user = _sm.User(id=uuid.uuid4(), email="test@kronos.local")
    cp = CurrencyPair(id=uuid.uuid4(), symbol="XAU_USD", name="Gold")
    ub = UserBroker(id=uuid.uuid4(), user_id=user.id, status="ACTIVE",
                    api_key=str(uuid.uuid4()))
    ref_strat = Strategy(
        id=uuid.uuid4(),
        name=deploy_manager.REFERENCE_STRATEGY_NAME,
        currencypair_id=cp.id,
        entry_quantity=Decimal("0.01"),
    )
    ref_us = UserStrategy(
        id=uuid.uuid4(), name="ref", deployed=True, is_active=True,
        strategy_id=ref_strat.id, user_broker_id=ub.id,
    )
    sess.add_all([user, cp, ub, ref_strat, ref_us])
    sess.commit()

    assert deploy_manager.seed(sess) == 0
    sess.commit()

    # Mirror the live DB state: the 2026-07-02 manual grant on the old logic,
    # and the stale H1 Donchian description (so the description-update path
    # is actually exercised, not a seeded no-op).
    m = _s96_managed(sess)
    m.live_eligible = True
    strat = sess.query(Strategy).filter_by(name=S96_NAME).one()
    strat.description = (
        "H1 Donchian(24) close-through continuation with EMA20/50 agreement."
    )
    sess.commit()

    yield sess
    sess.close()
    engine.dispose()


def _s96_managed(sess) -> ManagedStrategy:
    strat = sess.query(Strategy).filter_by(name=S96_NAME).one()
    us = sess.query(UserStrategy).filter_by(strategy_id=strat.id).one()
    return sess.query(ManagedStrategy).filter_by(user_strategy_id=us.id).one()


def test_revoke_flips_live_eligible_false(db):
    assert _s96_managed(db).live_eligible is True       # fixture precondition
    assert revoke_mod.revoke(db) == 0
    db.commit()
    m = _s96_managed(db)
    assert m.live_eligible is False
    assert m.arm_mode == "OFF"                          # untouched


def test_revoke_updates_strategy_description(db):
    assert revoke_mod.revoke(db) == 0
    db.commit()
    strat = db.query(Strategy).filter_by(name=S96_NAME).one()
    assert "M5 EMA9/21 crossover" in strat.description
    assert "Donchian" not in strat.description


def test_revoke_leaves_other_managed_rows_alone(db):
    others_before = {
        m.id: m.live_eligible
        for m in db.query(ManagedStrategy).all()
        if m.id != _s96_managed(db).id
    }
    assert revoke_mod.revoke(db) == 0
    db.commit()
    for m_id, flag in others_before.items():
        m = db.query(ManagedStrategy).filter_by(id=m_id).one()
        assert m.live_eligible == flag


def test_revoke_is_idempotent(db):
    assert revoke_mod.revoke(db) == 0
    db.commit()
    assert revoke_mod.revoke(db) == 0                   # second run: all SKIPs
    db.commit()
    assert _s96_managed(db).live_eligible is False


def test_revoke_missing_strategy_is_graceful():
    engine = create_engine("sqlite://")
    Strategy.metadata.create_all(engine)
    sess = sessionmaker(bind=engine)()
    try:
        assert revoke_mod.revoke(sess) == 0             # nothing to do, rc 0
    finally:
        sess.close()
        engine.dispose()
