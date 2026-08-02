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
    CurrencyPair, Order, Position, Strategy, User, UserBroker, UserStrategy,
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


def _pos(s, us, realized, created_utc, qty=0, lots=0.10):
    # Stored created_at is naive IST wall clock (get_kolkata_time convention).
    pos = Position(
        symbol="XAU_USD", quantity=Decimal(qty),
        realized_profit_loss=Decimal(str(realized)),
        user_strategy_id=us.id,
        created_at=created_utc.replace(tzinfo=None) + _IST_SKEW,
    )
    s.add(pos)
    s.flush()
    # live_summary recovers sizing-invariant points via the ENTRY order's
    # lots -- every seeded live Position needs one for the join to match.
    s.add(Order(position_id=pos.id, condition="ENTRY",
                quantity=Decimal(str(lots))))
    s.flush()


def test_aggregates_wins_losses_and_excludes_open(db):
    # realized_profit_loss is stored in PnL UNITS (points x lots); live_summary
    # must report USD = units x 100 (first smoke run caught the missing factor).
    us = _seed(db, "KRONOS_S93_FVG_SCALP")
    # NB: realized_profit_loss is Numeric(25, 2) — seed 2dp unit values only.
    _pos(db, us, 0.10, WIN_START + timedelta(days=1))
    _pos(db, us, -0.04, WIN_START + timedelta(days=2))
    _pos(db, us, 0.08, WIN_START + timedelta(days=3))
    _pos(db, us, 0.99, WIN_START + timedelta(days=4), qty=1)   # open: excluded

    out = live_summary(db, ["KRONOS_S93_FVG_SCALP"], WIN_START, WIN_END)
    agg = out["KRONOS_S93_FVG_SCALP"]
    # All three seeded at the default lots=0.10 -> points = units / 0.10.
    assert agg["usd"]["trades"] == 3
    assert agg["usd"]["pnl_usd"] == pytest.approx(14.0)   # 0.14 units -> $14.00
    assert agg["usd"]["win_rate"] == pytest.approx(66.67)
    assert agg["points"]["trades"] == 3
    assert agg["points"]["pnl_pts"] == pytest.approx(1.4)   # 1.0 - 0.4 + 0.8
    assert agg["points"]["win_rate"] == pytest.approx(66.67)
    assert agg["points"]["profit_factor"] == pytest.approx(1.8 / 0.4)


def test_ist_skew_window_edges(db):
    us = _seed(db, "KRONOS_S99_MSS_FVG")
    # 30 min BEFORE the UTC window start: with the +5:30 shift applied to the
    # bounds this row's stored stamp falls below lo -> excluded.
    _pos(db, us, 0.05, WIN_START - timedelta(minutes=30))
    # exactly at the window start: included.
    _pos(db, us, 0.03, WIN_START)
    out = live_summary(db, ["KRONOS_S99_MSS_FVG"], WIN_START, WIN_END)
    blk = out["KRONOS_S99_MSS_FVG"]
    assert blk["usd"]["trades"] == 1
    assert blk["usd"]["pnl_usd"] == pytest.approx(3.0)
    assert blk["points"]["trades"] == 1
    assert blk["points"]["pnl_pts"] == pytest.approx(0.3)   # 0.03 / 0.10


def test_name_filter(db):
    us = _seed(db, "KRONOS_S94_SWEEP_REVERSAL")
    _pos(db, us, 5.0, WIN_START + timedelta(days=1))
    out = live_summary(db, ["KRONOS_S93_FVG_SCALP"], WIN_START, WIN_END)
    assert out == {}


def test_deltas_math_and_live_missing():
    sim = {
        "A": {"points": {"pnl_pts": 10.0, "trades": 4, "win_rate": 50.0},
              "usd": {"pnl_usd": 100.0, "trades": 4, "win_rate": 50.0}},
        "B": {"points": {"pnl_pts": -2.0, "trades": 1, "win_rate": 0.0}},
        "D": {"points": {"pnl_pts": 1.0, "trades": 2, "win_rate": 50.0}},
    }
    live = {
        "A": {"points": {"pnl_pts": 6.0, "trades": 5, "win_rate": 40.0},
              "usd": {"pnl_usd": 60.0, "trades": 5, "win_rate": 40.0}},
        "D": {"points": {"pnl_pts": 0.5, "trades": 1, "win_rate": 100.0},
              "usd": {"pnl_usd": 5.0, "trades": 1, "win_rate": 100.0}},
    }
    out = deltas(sim, live)
    assert out["A"]["delta"]["points"] == {"pnl_pts": 4.0, "trades": -1,
                                           "win_rate": 10.0}
    assert out["A"]["delta"]["usd"] == {"pnl_usd": 40.0, "trades": -1,
                                        "win_rate": 10.0}
    assert out["B"]["live"] is None and out["B"]["delta"] is None
    # sim has no usd block for D -> delta stays points-only, even though
    # live priced one.
    assert out["D"]["delta"]["points"] == {"pnl_pts": 0.5, "trades": 1,
                                           "win_rate": -50.0}
    assert "usd" not in out["D"]["delta"]


def test_deltas_all_names_surfaces_zero_sim_trade_strategies():
    # A roster strategy the sim produced ZERO trades for (absent from `sim`)
    # must still appear -- these are the biggest sim/live gaps and were
    # previously silently dropped because `deltas` only iterated sim.items().
    sim = {
        "A": {"points": {"pnl_pts": 10.0, "trades": 4, "win_rate": 50.0},
              "usd": {"pnl_usd": 100.0, "trades": 4, "win_rate": 50.0}},
    }
    live = {
        # C traded live but the sim never fired it in this window.
        "C": {"points": {"pnl_pts": 2.0, "trades": 1, "win_rate": 100.0},
              "usd": {"pnl_usd": 20.0, "trades": 1, "win_rate": 100.0}},
    }
    out = deltas(sim, live, all_names=["A", "C", "E"])

    # C: zero sim trades but live activity -> present, zeroed sim.points,
    # live populated, delta computed against the zeroed sim baseline.
    assert out["C"]["sim"] == {"points": {"pnl_pts": 0.0, "trades": 0,
                                          "win_rate": 0.0,
                                          "profit_factor": None}}
    assert "usd" not in out["C"]["sim"]
    assert out["C"]["live"] == live["C"]
    assert out["C"]["delta"]["points"] == {"pnl_pts": -2.0, "trades": -1,
                                           "win_rate": -100.0}
    # sim has no usd block for C (zeroed) -> delta stays points-only even
    # though live priced one, same rule as an ordinary sim strategy.
    assert "usd" not in out["C"]["delta"]

    # E: zero sim trades AND no live activity -> still present, both sides
    # empty/zeroed, delta is None (never both-sided).
    assert out["E"]["sim"] == {"points": {"pnl_pts": 0.0, "trades": 0,
                                          "win_rate": 0.0,
                                          "profit_factor": None}}
    assert out["E"]["live"] is None
    assert out["E"]["delta"] is None

    # A (present in sim, absent from all_names duplication) still behaves
    # exactly as the no-all_names case.
    assert out["A"]["live"] is None and out["A"]["delta"] is None

    assert set(out.keys()) == {"A", "C", "E"}
