"""
test_strategy_manager.py
------------------------
Unit tests for the Strategy Manager service (strategy_manager/manager.py +
strategy_manager/policies.py) and the four new SQLAlchemy mirror tables in
shared/models.py (apis_regimesnapshot, apis_managedstrategy,
apis_managerconfig, apis_manageraction).

All offline: in-memory SQLite via create_all on the mirrored metadata, fake
regime snapshots (the RegimeSnapshot dataclass), fixed now_utc. No network,
no Postgres (create_engine in shared.models is lazy — importing never
connects).

Covers (per the design spec §10):
  * policy truth table (always_on / session_vol / trending / quiet_fade)
  * master OFF records the snapshot but touches nothing
  * arm_mode OFF strategies are never flipped even with master ON
  * kill-switch trips on daily loss, pauses armed strategies, auto-resets
    the next UTC day
  * transition-only ManagerAction rows (no repeated PAUSE spam)
  * market-closed guard, max-concurrent START guard, DRY_RUN semantics
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

# manager.py lives in strategy_manager/ and does `from shared...` /
# `from regime...` (absolute imports rooted at its own dir, same pattern as
# position_monitor), so put that dir on the path before importing it.
_SM_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategy_manager")
)
if _SM_DIR not in sys.path:
    sys.path.insert(0, _SM_DIR)

import manager  # noqa: E402
import policies  # noqa: E402
from regime.regime_engine import RegimeSnapshot as RegimeState  # noqa: E402

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures & helpers
# ──────────────────────────────────────────────────────────────────────────────

# Tuesday 2026-06-30 14:00 UTC — market open, inside the NY-overlap window.
NOW = datetime(2026, 6, 30, 14, 0, 0, tzinfo=timezone.utc)


def make_snap(**kw) -> RegimeState:
    """Fake regime snapshot; defaults describe a benign trending tape."""
    base = dict(
        symbol="XAU_USD",
        d1_bias="bullish",
        h4_bias="long",
        vol_regime="NORMAL",
        trend_regime="TRENDING",
        session="OVERLAP",
        market_closed=False,
        details={"atr_h1": 5.0},
    )
    base.update(kw)
    return RegimeState(**base)


@pytest.fixture()
def db():
    """In-memory SQLite session factory over the mirrored metadata."""
    engine = create_engine("sqlite://")
    # Use the metadata of the models module `manager` actually imported so
    # the test always creates tables for the exact classes under test.
    manager.ManagerConfig.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    sess = factory()
    yield sess
    sess.close()
    engine.dispose()


def seed_strategy(sess, *, arm_mode="PAPER", policy_key="always_on",
                  policy_params=None, is_active=False, slot="trend"):
    """Create a UserStrategy + ManagedStrategy pair; returns (ms, us)."""
    us = manager.UserStrategy(
        id=uuid.uuid4(), name=f"US-{slot}", is_active=is_active, deployed=True,
    )
    sess.add(us)
    ms = manager.ManagedStrategy(
        id=uuid.uuid4(),
        user_strategy_id=us.id,
        slot=slot,
        policy_key=policy_key,
        policy_params=policy_params or {},
        arm_mode=arm_mode,
    )
    sess.add(ms)
    sess.flush()
    return ms, us


def set_master_on(sess, **cfg_kw):
    cfg = manager._get_or_create_config(sess)
    cfg.master_mode = "ON"
    for k, v in cfg_kw.items():
        setattr(cfg, k, v)
    sess.flush()
    return cfg


def add_position(sess, us, *, realized=0.0, qty=0.0, modified_at=NOW):
    pos = manager.Position(
        id=uuid.uuid4(),
        symbol="XAU_USD",
        user_strategy_id=us.id,
        quantity=Decimal(str(qty)),
        realized_profit_loss=Decimal(str(realized)),
    )
    pos.modified_at = modified_at
    sess.add(pos)
    sess.flush()
    return pos


def actions(sess, kind=None):
    q = sess.query(manager.ManagerAction)
    if kind:
        q = q.filter(manager.ManagerAction.action == kind)
    return q.all()


# ──────────────────────────────────────────────────────────────────────────────
# Policy truth table
# ──────────────────────────────────────────────────────────────────────────────

def _at(hh, mm=0):
    return NOW.replace(hour=hh, minute=mm)


@pytest.mark.parametrize("snap_kw,now,expected", [
    # always_on: regime-independent
    (dict(), NOW, True),
    (dict(trend_regime="RANGING", vol_regime="EXTREME", d1_bias="ranging"), NOW, True),
])
def test_policy_always_on(snap_kw, now, expected):
    got, _ = policies.policy_always_on(make_snap(**snap_kw), {}, now)
    assert got is expected


@pytest.mark.parametrize("snap_kw,now,expected", [
    # window 06:45–10:00: in-window + NORMAL vol -> run
    (dict(vol_regime="NORMAL"), _at(7, 0), True),
    (dict(vol_regime="HIGH"), _at(9, 59), True),
    # window 13:15–16:00
    (dict(vol_regime="NORMAL"), _at(13, 15), True),
    (dict(vol_regime="NORMAL"), _at(15, 59), True),
    # boundaries: 06:44 too early, 10:00 exclusive, 13:14 too early, 16:00 out
    (dict(vol_regime="NORMAL"), _at(6, 44), False),
    (dict(vol_regime="NORMAL"), _at(10, 0), False),
    (dict(vol_regime="NORMAL"), _at(13, 14), False),
    (dict(vol_regime="NORMAL"), _at(16, 0), False),
    # in-window but wrong vol regime
    (dict(vol_regime="LOW"), _at(8, 0), False),
    (dict(vol_regime="EXTREME"), _at(8, 0), False),
])
def test_policy_session_vol(snap_kw, now, expected):
    got, _ = policies.policy_session_vol(make_snap(**snap_kw), {}, now)
    assert got is expected


def test_policy_session_vol_params_override():
    snap = make_snap(vol_regime="LOW")
    params = {"windows": [[0.0, 24.0]], "vol_regimes": ["LOW"]}
    got, _ = policies.policy_session_vol(snap, params, _at(3, 0))
    assert got is True


@pytest.mark.parametrize("trend,h4,expected", [
    ("TRENDING", "long", True),
    ("TRENDING", "short", True),
    ("TRENDING", "neutral", False),
    ("RANGING", "long", False),
    ("MIXED", "long", False),
])
def test_policy_trending(trend, h4, expected):
    got, _ = policies.policy_trending(
        make_snap(trend_regime=trend, h4_bias=h4), {}, NOW)
    assert got is expected


@pytest.mark.parametrize("snap_kw,now,expected", [
    # 03:00–09:00 UTC + LOW/NORMAL vol + directional D1
    (dict(vol_regime="LOW", d1_bias="bullish"), _at(3, 0), True),
    (dict(vol_regime="NORMAL", d1_bias="bearish"), _at(8, 59), True),
    (dict(vol_regime="NORMAL", d1_bias="bullish"), _at(2, 59), False),  # early
    (dict(vol_regime="NORMAL", d1_bias="bullish"), _at(9, 0), False),   # late
    (dict(vol_regime="HIGH", d1_bias="bullish"), _at(5, 0), False),     # vol
    (dict(vol_regime="NORMAL", d1_bias="ranging"), _at(5, 0), False),   # no bias
])
def test_policy_quiet_fade(snap_kw, now, expected):
    got, _ = policies.policy_quiet_fade(make_snap(**snap_kw), {}, now)
    assert got is expected


def test_policy_registry_complete():
    assert set(policies.POLICIES) == {
        "always_on", "session_vol", "trending", "quiet_fade"}


# ──────────────────────────────────────────────────────────────────────────────
# Manager: master OFF & arm OFF
# ──────────────────────────────────────────────────────────────────────────────

def test_master_off_records_snapshot_flips_nothing(db):
    ms, us = seed_strategy(db, arm_mode="LIVE", is_active=True)
    # default-created config is master OFF
    manager.evaluate_tick(db, make_snap(market_closed=True), NOW)
    db.flush()

    cfg = db.query(manager.ManagerConfig).one()
    assert cfg.master_mode == "OFF"                       # auto-created default
    assert db.query(manager.RegimeSnapshot).count() == 1  # snapshot recorded
    assert us.is_active is True                           # not flipped
    assert ms.desired_active is False                     # untouched verdict
    assert ms.last_evaluated_at is not None               # evaluated stamp set
    assert actions(db) == []                              # no action rows


def test_arm_off_never_flipped_even_when_master_on(db):
    set_master_on(db)
    ms, us = seed_strategy(db, arm_mode="OFF", is_active=True,
                           policy_key="trending")
    # regime says "pause" (ranging) — but the strategy is not armed
    manager.evaluate_tick(db, make_snap(trend_regime="RANGING"), NOW)
    db.flush()
    assert us.is_active is True
    assert ms.desired_active is False
    assert actions(db) == []
    assert ms.last_evaluated_at is not None


# ──────────────────────────────────────────────────────────────────────────────
# Manager: policy-driven flips, transitions, market-closed guard
# ──────────────────────────────────────────────────────────────────────────────

def test_start_and_pause_flow_with_transition_only_actions(db):
    set_master_on(db)
    ms, us = seed_strategy(db, arm_mode="PAPER", policy_key="trending",
                           is_active=False)

    # trending regime -> START (flip on)
    manager.evaluate_tick(db, make_snap(), NOW)
    db.flush()
    assert us.is_active is True
    assert ms.desired_active is True
    assert len(actions(db, "START")) == 1

    # same regime again -> no new action (no spam), stays on
    manager.evaluate_tick(db, make_snap(), NOW + timedelta(minutes=1))
    db.flush()
    assert us.is_active is True
    assert len(actions(db, "START")) == 1

    # regime flips to ranging -> PAUSE once
    for i in range(3):
        manager.evaluate_tick(db, make_snap(trend_regime="MIXED"),
                              NOW + timedelta(minutes=2 + i))
    db.flush()
    assert us.is_active is False
    assert ms.desired_active is False
    assert len(actions(db, "PAUSE")) == 1     # 3 ticks, 1 action
    assert "trend=MIXED" in actions(db, "PAUSE")[0].reason


def test_market_closed_pauses_all_armed_without_spam(db):
    set_master_on(db)
    ms1, us1 = seed_strategy(db, arm_mode="LIVE", policy_key="always_on",
                             is_active=True, slot="trend")
    ms2, us2 = seed_strategy(db, arm_mode="PAPER", policy_key="trending",
                             is_active=True, slot="momentum")
    # prime desired_active=True so the closed-market PAUSE is a transition
    manager.evaluate_tick(db, make_snap(), NOW)
    db.flush()
    assert us1.is_active and us2.is_active

    closed = make_snap(market_closed=True)
    for i in range(3):
        manager.evaluate_tick(db, closed, NOW + timedelta(minutes=1 + i))
    db.flush()
    assert us1.is_active is False and us2.is_active is False
    assert len(actions(db, "PAUSE")) == 2     # one per strategy, not per tick
    assert all("market closed" in a.reason for a in actions(db, "PAUSE"))


def test_unknown_policy_key_keeps_strategy_paused(db):
    set_master_on(db)
    ms, us = seed_strategy(db, arm_mode="PAPER", policy_key="bogus",
                           is_active=True)
    manager.evaluate_tick(db, make_snap(), NOW)
    db.flush()
    assert us.is_active is False
    assert "unknown policy_key" in ms.last_reason


# ──────────────────────────────────────────────────────────────────────────────
# Kill-switch
# ──────────────────────────────────────────────────────────────────────────────

def test_kill_switch_trips_pauses_and_resets_next_utc_day(db):
    cfg = set_master_on(db, kill_switch_loss_usd=Decimal("150.00"))
    ms, us = seed_strategy(db, arm_mode="LIVE", policy_key="always_on",
                           is_active=True)
    # prime running state
    manager.evaluate_tick(db, make_snap(), NOW)
    db.flush()
    assert us.is_active is True

    # today's realized loss breaches the limit -> trip
    add_position(db, us, realized=-90.0, modified_at=NOW)
    add_position(db, us, realized=-70.0, modified_at=NOW)
    manager.evaluate_tick(db, make_snap(), NOW + timedelta(minutes=1))
    db.flush()
    assert us.is_active is False
    assert ms.desired_active is False
    assert "kill-switch" in ms.last_reason
    assert len(actions(db, "KILL_SWITCH")) == 1
    assert cfg.state.get("kill_tripped_date") == NOW.date().isoformat()

    # further ticks the same day: stays paused, no more KILL_SWITCH actions
    manager.evaluate_tick(db, make_snap(), NOW + timedelta(minutes=2))
    db.flush()
    assert us.is_active is False
    assert len(actions(db, "KILL_SWITCH")) == 1

    # next UTC day: auto-reset -> INFO action, strategy restarts
    next_day = NOW + timedelta(days=1)
    manager.evaluate_tick(db, make_snap(), next_day)
    db.flush()
    assert cfg.state.get("kill_tripped_date") is None
    assert any("auto-reset" in a.reason for a in actions(db, "INFO"))
    assert us.is_active is True                     # always_on resumes


def test_kill_switch_counts_only_todays_managed_pnl(db):
    set_master_on(db, kill_switch_loss_usd=Decimal("150.00"))
    ms, us = seed_strategy(db, arm_mode="LIVE", policy_key="always_on",
                           is_active=True)
    ms.desired_active = True
    # yesterday's big loss must NOT trip today
    add_position(db, us, realized=-500.0, modified_at=NOW - timedelta(days=1))
    # unmanaged strategy's loss must NOT count either
    other = manager.UserStrategy(id=uuid.uuid4(), name="unmanaged")
    db.add(other)
    db.flush()
    add_position(db, other, realized=-500.0, modified_at=NOW)

    manager.evaluate_tick(db, make_snap(), NOW)
    db.flush()
    assert us.is_active is True
    assert actions(db, "KILL_SWITCH") == []


# ──────────────────────────────────────────────────────────────────────────────
# Max-concurrent guard
# ──────────────────────────────────────────────────────────────────────────────

def test_max_concurrent_blocks_new_starts_but_does_not_pause(db):
    set_master_on(db, max_concurrent_positions=2)
    ms_run, us_run = seed_strategy(db, arm_mode="LIVE", policy_key="always_on",
                                   is_active=True, slot="trend")
    ms_run.desired_active = True
    ms_new, us_new = seed_strategy(db, arm_mode="PAPER", policy_key="always_on",
                                   is_active=False, slot="momentum")
    # two open managed positions == cap
    add_position(db, us_run, qty=0.02)
    add_position(db, us_run, qty=0.02)

    manager.evaluate_tick(db, make_snap(), NOW)
    db.flush()
    assert us_run.is_active is True            # running one left alone
    assert us_new.is_active is False           # START blocked by the cap
    assert "max concurrent" in ms_new.last_reason

    # cap frees up -> the blocked strategy starts on a later tick
    for p in db.query(manager.Position).all():
        p.quantity = Decimal("0")
    manager.evaluate_tick(db, make_snap(), NOW + timedelta(minutes=1))
    db.flush()
    assert us_new.is_active is True
    assert len(actions(db, "START")) == 1


# ──────────────────────────────────────────────────────────────────────────────
# DRY_RUN
# ──────────────────────────────────────────────────────────────────────────────

def test_dry_run_writes_snapshot_and_actions_but_not_is_active(db):
    set_master_on(db)
    ms, us = seed_strategy(db, arm_mode="PAPER", policy_key="always_on",
                           is_active=False)
    manager.evaluate_tick(db, make_snap(), NOW, dry_run=True)
    db.flush()

    assert us.is_active is False                        # not flipped
    assert ms.desired_active is True                    # verdict recorded
    assert db.query(manager.RegimeSnapshot).count() == 1
    assert len(actions(db, "START")) == 1               # audit still written

    # repeated dry ticks do not spam actions (desired_active carries state)
    manager.evaluate_tick(db, make_snap(), NOW + timedelta(minutes=1), dry_run=True)
    db.flush()
    assert us.is_active is False
    assert len(actions(db, "START")) == 1


# ──────────────────────────────────────────────────────────────────────────────
# Bookkeeping & schema
# ──────────────────────────────────────────────────────────────────────────────

def test_config_auto_created_with_spec_defaults(db):
    cfg = manager._get_or_create_config(db)
    assert cfg.master_mode == "OFF"
    assert float(cfg.kill_switch_loss_usd) == 150.00
    assert int(cfg.max_concurrent_positions) == 3


def test_snapshot_row_mirrors_dataclass(db):
    snap = make_snap(vol_regime="HIGH", session="LONDON",
                     details={"atr_h1": 7.5, "er_h1": 0.4})
    manager.evaluate_tick(db, snap, NOW)
    db.flush()
    row = db.query(manager.RegimeSnapshot).one()
    assert (row.symbol, row.d1_bias, row.h4_bias) == ("XAU_USD", "bullish", "long")
    assert (row.vol_regime, row.trend_regime, row.session) == \
        ("HIGH", "TRENDING", "LONDON")
    assert row.market_closed is False
    assert row.details["atr_h1"] == 7.5


def test_new_tables_use_canonical_names():
    assert manager.RegimeSnapshot.__tablename__ == "apis_regimesnapshot"
    assert manager.ManagedStrategy.__tablename__ == "apis_managedstrategy"
    assert manager.ManagerConfig.__tablename__ == "apis_managerconfig"
    assert manager.ManagerAction.__tablename__ == "apis_manageraction"


def test_service_copy_of_models_matches_strategies_copy():
    """strategy_manager/shared/models.py must define the same 4 new tables
    as strategies/shared/models.py (both mirror the Django schema)."""
    import re

    def cols(path):
        src = open(path, encoding="utf-8").read()
        out = {}
        for cls in ("RegimeSnapshot", "ManagedStrategy", "ManagerConfig",
                    "ManagerAction"):
            m = re.search(rf"class {cls}\(BaseModel\):(.*?)(?=\nclass |\Z)",
                          src, re.S)
            assert m, f"{cls} missing in {path}"
            out[cls] = sorted(re.findall(r"^\s{4}(\w+)\s*=\s*Column",
                                         m.group(1), re.M))
        return out

    here = os.path.dirname(__file__)
    a = cols(os.path.join(here, "..", "strategies", "shared", "models.py"))
    b = cols(os.path.join(here, "..", "strategy_manager", "shared", "models.py"))
    assert a == b
