"""
test_deploy_manager.py
----------------------
Integration tests for strategies/db/deploy_manager.py — the idempotent seed
script for the Strategy Manager v1 roster (design spec 2026-07-02 §4/§5).

All offline: in-memory SQLite via create_all on the mirrored metadata
(create_engine in shared.models is lazy — importing never connects).

Covers:
  * seed() creates the 2 child Strategy+UserStrategy pairs with
    entry_quantity=0.01, deployed=True, is_active=False (scalper slot empty
    since S98 failed train validation, spec 2026-07-03)
  * ManagedStrategy rows carry the spec slot/policy mapping, arm_mode=OFF,
    live_eligible=False for both new children
  * CHALLENGE_XAU adoption (slot=trend/always_on/live_eligible=True) when a
    deployed UserStrategy exists — and graceful skip when it doesn't
  * default ManagerConfig row (master OFF, -$150, max 3)
  * idempotency — a second seed() adds no rows and flips no is_active
  * every seeded policy_key exists in strategy_manager/policies.POLICIES and
    every Strategy name is registered in entry_manager._VARIATION_STRATEGY_NAME
"""
from __future__ import annotations

import os
import sys
import uuid
from decimal import Decimal

import pytest

# deploy_manager lives in strategies/db/ and does `from shared.models import`
# (absolute imports rooted at strategies/, same pattern as the runners).
_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies")
)
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from db import deploy_manager  # noqa: E402
from strategy.entry_manager import _VARIATION_STRATEGY_NAME  # noqa: E402

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

# The policy registry the live manager will actually use.
_SM_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategy_manager")
)
if _SM_DIR not in sys.path:
    sys.path.insert(0, _SM_DIR)
import policies as sm_policies  # noqa: E402

# Model classes exactly as deploy_manager resolved them (avoids any ambiguity
# between the strategies/ and strategy_manager/ shared.models mirrors).
CurrencyPair = deploy_manager.CurrencyPair
ManagedStrategy = deploy_manager.ManagedStrategy
ManagerConfig = deploy_manager.ManagerConfig
Strategy = deploy_manager.Strategy
UserBroker = deploy_manager.UserBroker
UserStrategy = deploy_manager.UserStrategy

NEW_NAMES = [
    "S99 MSS FVG Reversal",
    "S93 FVG Scalp",
    "S100 M3 Combo Scalper",
    "S94 Sweep Reversal",
]


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def db():
    """In-memory SQLite session over the mirror metadata, pre-seeded with the
    prerequisites: CurrencyPair XAU_USD + a User/UserBroker + the reference
    strategy binding deploy_manager mirrors."""
    engine = create_engine("sqlite://")
    Strategy.metadata.create_all(engine)
    Sess = sessionmaker(bind=engine)
    sess = Sess()

    # Minimal User row (UserBroker.user_id is NOT NULL). Resolve User from the
    # same shared.models module object deploy_manager imported from.
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
    yield sess
    sess.close()
    engine.dispose()


def _add_challenge(sess) -> UserStrategy:
    cp = sess.query(CurrencyPair).filter_by(symbol="XAU_USD").one()
    ub = sess.query(UserBroker).first()
    strat = Strategy(
        id=uuid.uuid4(),
        name=deploy_manager.CHALLENGE_STRATEGY_NAME,
        currencypair_id=cp.id,
        entry_quantity=Decimal("0.02"),
    )
    us = UserStrategy(
        id=uuid.uuid4(), name="challenge live", deployed=True, is_active=True,
        strategy_id=strat.id, user_broker_id=ub.id,
    )
    sess.add_all([strat, us])
    sess.commit()
    return us


# ──────────────────────────────────────────────────────────────────────────────
# Seeding
# ──────────────────────────────────────────────────────────────────────────────

def test_seed_creates_children_inactive(db):
    assert deploy_manager.seed(db) == 0
    db.commit()

    for name in NEW_NAMES:
        strat = db.query(Strategy).filter_by(name=name).one()
        assert Decimal(strat.entry_quantity) == Decimal("0.01")
        us = db.query(UserStrategy).filter_by(strategy_id=strat.id).one()
        assert us.deployed is True
        assert us.is_active is False, (
            f"{name}: UserStrategy must be seeded INACTIVE — the manager "
            "starts it, the deploy script must not"
        )


def test_seed_managed_rows_match_spec(db):
    assert deploy_manager.seed(db) == 0
    db.commit()

    expected = {
        "S99 MSS FVG Reversal":        ("reversal", "always_on"),
        "S93 FVG Scalp":               ("scalping", "always_on"),
        "S100 M3 Combo Scalper":       ("scalping", "always_on"),
        "S94 Sweep Reversal":          ("trend",    "always_on"),
    }
    for name, (slot, policy_key) in expected.items():
        strat = db.query(Strategy).filter_by(name=name).one()
        us = db.query(UserStrategy).filter_by(strategy_id=strat.id).one()
        m = db.query(ManagedStrategy).filter_by(user_strategy_id=us.id).one()
        assert m.slot == slot
        assert m.policy_key == policy_key
        assert m.arm_mode == "OFF"
        assert m.live_eligible is False, (
            f"{name}: live_eligible must stay False until the held-out "
            "backtest passes"
        )
        assert m.desired_active is False


def test_seeded_policies_and_names_are_registered(db):
    """Wiring cross-check: what the seeder writes must resolve in the live
    manager (policy registry) and the live entry path (variation map)."""
    assert deploy_manager.seed(db) == 0
    db.commit()

    for m in db.query(ManagedStrategy).all():
        assert m.policy_key in sm_policies.POLICIES, m.policy_key

    registered = set(_VARIATION_STRATEGY_NAME.values())
    for name in NEW_NAMES:
        assert name in registered, (
            f"Strategy name '{name}' not in entry_manager._VARIATION_STRATEGY_NAME "
            "— place_entry would refuse its signals"
        )


def test_seed_default_manager_config(db):
    assert deploy_manager.seed(db) == 0
    db.commit()

    cfg = db.query(ManagerConfig).one()
    assert cfg.master_mode == "OFF"
    assert Decimal(cfg.kill_switch_loss_usd) == Decimal("150.00")
    assert cfg.max_concurrent_positions == 3


# ──────────────────────────────────────────────────────────────────────────────
# CHALLENGE_XAU adoption
# ──────────────────────────────────────────────────────────────────────────────

def test_challenge_adopted_when_deployed(db):
    ch_us = _add_challenge(db)
    assert deploy_manager.seed(db) == 0
    db.commit()

    m = db.query(ManagedStrategy).filter_by(user_strategy_id=ch_us.id).one()
    assert m.slot == "trend"
    assert m.policy_key == "always_on"
    assert m.live_eligible is True
    assert m.arm_mode == "OFF"
    # Adoption must not touch the live switch — it keeps trading as today.
    db.refresh(ch_us)
    assert ch_us.is_active is True


def test_challenge_created_when_absent(db):
    # 2026-07-06: a fresh-account rollout creates the trend slot too, so all
    # three roster strategies land in one pass (scalper slot stays empty).
    assert deploy_manager.seed(db) == 0
    db.commit()
    assert db.query(ManagedStrategy).count() == 5
    ch = db.query(Strategy).filter_by(
        name=deploy_manager.CHALLENGE_STRATEGY_NAME).one()
    ch_us = db.query(UserStrategy).filter_by(strategy_id=ch.id).one()
    assert ch_us.deployed is True
    assert ch_us.is_active is False, "created trend slot must start paused"
    m = db.query(ManagedStrategy).filter_by(user_strategy_id=ch_us.id).one()
    assert m.slot == "trend" and m.policy_key == "always_on"
    assert m.live_eligible is True


def test_rollout_env_knobs(db, monkeypatch):
    # The 2026-07-06 live-account rollout: arm LIVE + live_eligible, still
    # paused (is_active False everywhere, master OFF).
    monkeypatch.setenv("MANAGER_ARM_MODE", "LIVE")
    monkeypatch.setenv("MANAGER_LIVE_ELIGIBLE", "true")
    assert deploy_manager.seed(db) == 0
    db.commit()
    for m in db.query(ManagedStrategy).all():
        assert m.arm_mode == "LIVE"
        assert m.live_eligible is True
        assert m.desired_active is False
        us = db.query(UserStrategy).filter_by(id=m.user_strategy_id).one()
        assert us.is_active is False, "rollout must land paused"
    cfg = db.query(ManagerConfig).one()
    assert cfg.master_mode == "OFF"


# ──────────────────────────────────────────────────────────────────────────────
# Retire of a pulled roster strategy (S97 retired; scalper slot left empty
# after S98 failed train validation, spec 2026-07-03)
# ──────────────────────────────────────────────────────────────────────────────

def _add_retired_s97(sess) -> UserStrategy:
    """Seed a live-looking S97 (Strategy + deployed/active UserStrategy +
    ManagedStrategy) so the retire pass has something to pull."""
    cp = sess.query(CurrencyPair).filter_by(symbol="XAU_USD").one()
    ub = sess.query(UserBroker).first()
    strat = Strategy(
        id=uuid.uuid4(),
        name="S97 Snap Scalper M5 (paper)",
        currencypair_id=cp.id,
        entry_quantity=Decimal("0.01"),
        json_data={"variation": "KRONOS_S97_SNAP_SCALPER"},
    )
    us = UserStrategy(
        id=uuid.uuid4(), name="s97 managed", deployed=True, is_active=True,
        strategy_id=strat.id, user_broker_id=ub.id,
    )
    m = ManagedStrategy(
        id=uuid.uuid4(), user_strategy_id=us.id, slot="scalper",
        policy_key="quiet_fade", arm_mode="OFF",
    )
    sess.add_all([strat, us, m])
    sess.commit()
    return us


def test_retire_pulls_s97(db):
    s97_us = _add_retired_s97(db)
    assert deploy_manager.seed(db) == 0
    db.commit()

    # ManagedStrategy row for the retired strategy is deleted.
    assert (
        db.query(ManagedStrategy).filter_by(user_strategy_id=s97_us.id).first()
        is None
    ), "retired S97 ManagedStrategy must be removed"
    # UserStrategy de-deployed and de-activated.
    db.refresh(s97_us)
    assert s97_us.deployed is False
    assert s97_us.is_active is False
    # Roster children (S93, S99, S100, S94) + the created challenge trend slot.
    assert db.query(ManagedStrategy).count() == 5


def test_retire_noop_when_absent(db):
    # No S97 rows present -> retire pass is a clean no-op, seed still succeeds.
    assert deploy_manager.seed(db) == 0
    db.commit()
    assert db.query(ManagedStrategy).count() == 5


# ──────────────────────────────────────────────────────────────────────────────
# Idempotency
# ──────────────────────────────────────────────────────────────────────────────

def test_seed_is_idempotent(db):
    _add_challenge(db)
    assert deploy_manager.seed(db) == 0
    db.commit()

    counts = (
        db.query(Strategy).count(),
        db.query(UserStrategy).count(),
        db.query(ManagedStrategy).count(),
        db.query(ManagerConfig).count(),
    )

    # Simulate the manager having started S93, then re-run the seeder: the
    # re-run must not flip is_active back off.
    s93 = db.query(Strategy).filter_by(name="S93 FVG Scalp").one()
    s93_us = db.query(UserStrategy).filter_by(strategy_id=s93.id).one()
    s93_us.is_active = True
    db.commit()

    assert deploy_manager.seed(db) == 0
    db.commit()

    assert counts == (
        db.query(Strategy).count(),
        db.query(UserStrategy).count(),
        db.query(ManagedStrategy).count(),
        db.query(ManagerConfig).count(),
    )
    db.refresh(s93_us)
    assert s93_us.is_active is True, "re-seed must not flip a running strategy off"


def test_seed_fails_without_currencypair():
    engine = create_engine("sqlite://")
    Strategy.metadata.create_all(engine)
    sess = sessionmaker(bind=engine)()
    try:
        assert deploy_manager.seed(sess) == 1
    finally:
        sess.close()
        engine.dispose()
