"""Phase 3 of the 5s-backtest-fidelity spec: matching sim trades to live trades.

The deliverable is not "the sim looks right in aggregate" — it is a per-trade
diff that names WHICH mechanism diverged. These tests pin the matcher and the
tolerance evaluation; both are pure, so they run without the box or any data.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies"))
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from backtest import parity_harness as ph  # noqa: E402

_T0 = datetime(2026, 7, 8, 10, 0, 0, tzinfo=timezone.utc)


def _live(strategy="S100", offset_s=0, entry=4400.0, exit_px=4397.5,
          outcome="SL", usd=-25.0, side="BUY", ticket="1") -> ph.LiveTrade:
    return ph.LiveTrade(
        strategy=strategy, side=side,
        entry_time=_T0 + timedelta(seconds=offset_s),
        entry_px=entry, exit_time=_T0 + timedelta(seconds=offset_s + 600),
        exit_px=exit_px, outcome=outcome, usd=usd, ticket=ticket)


def _sim(strategy="S100", offset_s=0, entry=4400.0, exit_px=4397.5,
         outcome="SL", usd=-25.0, side="BUY") -> ph.SimTrade:
    return ph.SimTrade(
        strategy=strategy, side=side,
        entry_time=_T0 + timedelta(seconds=offset_s),
        entry_px=entry, exit_time=_T0 + timedelta(seconds=offset_s + 600),
        exit_px=exit_px, outcome=outcome, usd=usd)


# ── matching ──────────────────────────────────────────────────────────────────

def test_matches_same_strategy_within_the_window():
    res = ph.match_trades([_live()], [_sim(offset_s=30)], window_s=90)

    assert len(res.matched) == 1
    assert res.live_only == [] and res.sim_only == []


def test_does_not_match_across_strategies():
    res = ph.match_trades([_live(strategy="S100")],
                          [_sim(strategy="S94")], window_s=90)

    assert res.matched == []
    assert len(res.live_only) == 1 and len(res.sim_only) == 1


def test_does_not_match_outside_the_window():
    res = ph.match_trades([_live()], [_sim(offset_s=120)], window_s=90)

    assert res.matched == []
    assert len(res.live_only) == 1 and len(res.sim_only) == 1


def test_matches_symmetrically_for_an_earlier_sim_trade():
    """The window is absolute: a sim trade 30s BEFORE live still matches."""
    res = ph.match_trades([_live(offset_s=60)], [_sim(offset_s=30)],
                          window_s=90)

    assert len(res.matched) == 1


def test_picks_the_nearest_candidate():
    live = [_live(offset_s=0)]
    sims = [_sim(offset_s=80, entry=1111.0), _sim(offset_s=10, entry=2222.0)]

    res = ph.match_trades(live, sims, window_s=90)

    assert len(res.matched) == 1
    assert res.matched[0].sim.entry_px == pytest.approx(2222.0)
    assert len(res.sim_only) == 1        # the far one is reported, not dropped


def test_each_sim_trade_is_consumed_at_most_once():
    """Two live trades close together must not both claim the same sim trade."""
    live = [_live(offset_s=0, ticket="a"), _live(offset_s=20, ticket="b")]
    sims = [_sim(offset_s=10)]

    res = ph.match_trades(live, sims, window_s=90)

    assert len(res.matched) == 1
    assert len(res.live_only) == 1
    assert res.sim_only == []


def test_unmatched_live_trade_is_a_sim_miss():
    res = ph.match_trades([_live()], [], window_s=90)

    assert len(res.live_only) == 1
    assert res.matched == [] and res.sim_only == []


def test_unmatched_sim_trade_is_an_invented_trade():
    res = ph.match_trades([], [_sim()], window_s=90)

    assert len(res.sim_only) == 1
    assert res.matched == [] and res.live_only == []


# ── deltas ────────────────────────────────────────────────────────────────────

def test_entry_delta_is_signed_sim_minus_live():
    pair = ph.match_trades([_live(entry=4400.00)],
                           [_sim(entry=4400.25)], window_s=90).matched[0]

    assert pair.entry_delta == pytest.approx(0.25)


def test_exit_delta_is_signed_sim_minus_live():
    pair = ph.match_trades([_live(exit_px=4397.50)],
                           [_sim(exit_px=4397.20)], window_s=90).matched[0]

    assert pair.exit_delta == pytest.approx(-0.30)


def test_outcome_agreement_is_reported():
    agree = ph.match_trades([_live(outcome="SL")],
                            [_sim(outcome="SL")], window_s=90).matched[0]
    differ = ph.match_trades([_live(outcome="SL")],
                             [_sim(outcome="TP")], window_s=90).matched[0]

    assert agree.outcome_agrees is True
    assert differ.outcome_agrees is False


def test_usd_delta_is_signed_sim_minus_live():
    pair = ph.match_trades([_live(usd=-25.0)],
                           [_sim(usd=-17.7)], window_s=90).matched[0]

    assert pair.usd_delta == pytest.approx(7.3)


# ── mechanism attribution ─────────────────────────────────────────────────────

def test_outcome_flip_is_attributed_to_intrabar_ordering():
    pair = ph.match_trades([_live(outcome="TP", exit_px=4405.0, usd=50.0)],
                           [_sim(outcome="SL", exit_px=4397.5, usd=-25.0)],
                           window_s=90).matched[0]

    assert ph.attribute(pair) == "intrabar_order"


def test_entry_offset_with_matching_outcome_is_attributed_to_entry_fill():
    pair = ph.match_trades([_live(entry=4400.00)],
                           [_sim(entry=4400.60)], window_s=90).matched[0]

    assert ph.attribute(pair) == "entry_fill"


def test_exit_only_offset_is_attributed_to_exit_fill():
    pair = ph.match_trades([_live(exit_px=4397.50)],
                           [_sim(exit_px=4396.80)], window_s=90).matched[0]

    assert ph.attribute(pair) == "exit_fill"


def test_a_clean_match_is_attributed_to_nothing():
    pair = ph.match_trades([_live()], [_sim()], window_s=90).matched[0]

    assert ph.attribute(pair) is None


# ── tolerance (pre-registered in spec section 4.5) ────────────────────────────

def _clean_pairs(n: int) -> list:
    live = [_live(offset_s=i * 3600, ticket=str(i)) for i in range(n)]
    sim = [_live_to_sim(t) for t in live]
    return ph.match_trades(live, sim, window_s=90)


def _live_to_sim(t: ph.LiveTrade) -> ph.SimTrade:
    return ph.SimTrade(strategy=t.strategy, side=t.side,
                       entry_time=t.entry_time, entry_px=t.entry_px,
                       exit_time=t.exit_time, exit_px=t.exit_px,
                       outcome=t.outcome, usd=t.usd)


def test_tolerance_passes_on_a_perfect_reproduction():
    res = _clean_pairs(10)

    verdict = ph.evaluate_tolerance(res)

    assert verdict.passed is True
    assert verdict.failures == []


def test_tolerance_fails_when_too_many_live_trades_are_missed():
    live = [_live(offset_s=i * 3600, ticket=str(i)) for i in range(10)]
    sim = [_live_to_sim(t) for t in live[:5]]          # sim found only half
    res = ph.match_trades(live, sim, window_s=90)

    verdict = ph.evaluate_tolerance(res)

    assert verdict.passed is False
    assert any("match rate" in f for f in verdict.failures)


def test_tolerance_fails_on_excessive_entry_drift():
    live = [_live(offset_s=i * 3600, ticket=str(i)) for i in range(10)]
    sim = [ph.SimTrade(strategy=t.strategy, side=t.side,
                       entry_time=t.entry_time, entry_px=t.entry_px + 1.0,
                       exit_time=t.exit_time, exit_px=t.exit_px,
                       outcome=t.outcome, usd=t.usd) for t in live]
    res = ph.match_trades(live, sim, window_s=90)

    verdict = ph.evaluate_tolerance(res)

    assert verdict.passed is False
    assert any("entry" in f for f in verdict.failures)


def test_tolerance_fails_when_outcomes_disagree_too_often():
    live = [_live(offset_s=i * 3600, ticket=str(i), outcome="SL")
            for i in range(10)]
    sim = [ph.SimTrade(strategy=t.strategy, side=t.side,
                       entry_time=t.entry_time, entry_px=t.entry_px,
                       exit_time=t.exit_time, exit_px=t.exit_px,
                       outcome="TP" if i < 5 else "SL", usd=t.usd)
           for i, t in enumerate(live)]
    res = ph.match_trades(live, sim, window_s=90)

    verdict = ph.evaluate_tolerance(res)

    assert verdict.passed is False
    assert any("outcome" in f for f in verdict.failures)


def test_tolerance_fails_when_aggregate_usd_is_off_by_more_than_10pct():
    live = [_live(offset_s=i * 3600, ticket=str(i), usd=-25.0)
            for i in range(10)]
    sim = [ph.SimTrade(strategy=t.strategy, side=t.side,
                       entry_time=t.entry_time, entry_px=t.entry_px,
                       exit_time=t.exit_time, exit_px=t.exit_px,
                       outcome=t.outcome, usd=-15.0) for t in live]
    res = ph.match_trades(live, sim, window_s=90)

    verdict = ph.evaluate_tolerance(res)

    assert verdict.passed is False
    assert any("USD" in f for f in verdict.failures)


def test_verdict_reports_the_measured_numbers_not_just_pass_fail():
    res = _clean_pairs(4)

    verdict = ph.evaluate_tolerance(res)

    assert verdict.stats["matched"] == 4
    assert verdict.stats["match_rate"] == pytest.approx(1.0)
    assert verdict.stats["outcome_agreement"] == pytest.approx(1.0)
