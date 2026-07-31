"""Task 8 (Manager Backtest plan): live summary + deltas (SQLite, offline)."""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies"))
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from audit_worker.live_deltas import deltas, live_summary, _IST_SKEW  # noqa: E402
from shared.models import (  # noqa: E402
    CurrencyPair, Position, Strategy, User, UserBroker, UserStrategy,
)

WIN_START = datetime(2026, 7, 1, tzinfo=timezone.utc)
WIN_END = datetime(2026, 7, 31, tzinfo=timezone.utc)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Position.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as s:
        yield s
    engine.dispose()


def _seed(s, name):
    user = User(email=f"{uuid.uuid4()}@t.local")
    cp = CurrencyPair(symbol="XAU_USD", name="XAU_USD")
    strat = Strategy(name=name, apis_currencypair=cp)
    ub = UserBroker(apis_user=user, api_key=str(uuid.uuid4()))
    us = UserStrategy(apis_strategy=strat, apis_userbroker=ub)
    s.add_all([user, cp, strat, ub, us])
    s.flush()
    return us


def _pos(s, us, realized, created_utc, qty=0):
    # Stored created_at is naive IST wall clock (get_kolkata_time convention).
    s.add(Position(
        symbol="XAU_USD", quantity=Decimal(qty),
        realized_profit_loss=Decimal(str(realized)),
        user_strategy_id=us.id,
        created_at=created_utc.replace(tzinfo=None) + _IST_SKEW,
    ))
    s.flush()


def test_aggregates_wins_losses_and_excludes_open(db):
    us = _seed(db, "KRONOS_S93_FVG_SCALP")
    _pos(db, us, 10.0, WIN_START + timedelta(days=1))
    _pos(db, us, -4.0, WIN_START + timedelta(days=2))
    _pos(db, us, 7.5, WIN_START + timedelta(days=3))
    _pos(db, us, 99.0, WIN_START + timedelta(days=4), qty=1)   # open: excluded

    out = live_summary(db, ["KRONOS_S93_FVG_SCALP"], WIN_START, WIN_END)
    agg = out["KRONOS_S93_FVG_SCALP"]
    assert agg["trades"] == 3
    assert agg["pnl_usd"] == pytest.approx(13.5)
    assert agg["win_rate"] == pytest.approx(66.67)


def test_ist_skew_window_edges(db):
    us = _seed(db, "KRONOS_S99_MSS_FVG")
    # 30 min BEFORE the UTC window start: with the +5:30 shift applied to the
    # bounds this row's stored stamp falls below lo -> excluded.
    _pos(db, us, 5.0, WIN_START - timedelta(minutes=30))
    # exactly at the window start: included.
    _pos(db, us, 3.0, WIN_START)
    out = live_summary(db, ["KRONOS_S99_MSS_FVG"], WIN_START, WIN_END)
    assert out["KRONOS_S99_MSS_FVG"]["trades"] == 1
    assert out["KRONOS_S99_MSS_FVG"]["pnl_usd"] == pytest.approx(3.0)


def test_name_filter(db):
    us = _seed(db, "KRONOS_S94_SWEEP_REVERSAL")
    _pos(db, us, 5.0, WIN_START + timedelta(days=1))
    out = live_summary(db, ["KRONOS_S93_FVG_SCALP"], WIN_START, WIN_END)
    assert out == {}


def test_deltas_math_and_live_missing():
    sim = {
        "A": {"pnl_usd": 10.0, "trades": 4, "win_rate": 50.0},
        "B": {"pnl_usd": -2.0, "trades": 1, "win_rate": 0.0},
    }
    live = {"A": {"pnl_usd": 6.0, "trades": 5, "win_rate": 40.0}}
    out = deltas(sim, live)
    assert out["A"]["delta"] == {"pnl_usd": 4.0, "trades": -1, "win_rate": 10.0}
    assert out["B"]["live"] is None and out["B"]["delta"] is None
