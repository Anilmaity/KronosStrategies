import os, sys
_STRAT_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "strategies"))
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from datetime import datetime, time

import pytest

from shared.gate_rules import (  # noqa: E402
    parse_utc_windows, in_news_blackout, sl_too_tight,
    drift_budget_pts, entry_drift_pts, entry_drift_exceeded,
)


def test_parse_and_blackout():
    wins = parse_utc_windows("12:25-12:45,13:55-14:05")
    assert wins == [(time(12, 25), time(12, 45)), (time(13, 55), time(14, 5))]
    assert in_news_blackout(datetime(2026, 7, 1, 12, 30), wins) is True
    assert in_news_blackout(datetime(2026, 7, 1, 12, 50), wins) is False


def test_parse_skips_malformed():
    assert parse_utc_windows("bad,12:25-12:45") == [(time(12, 25), time(12, 45))]
    assert parse_utc_windows("") == []


def test_sl_too_tight():
    assert sl_too_tight(2000.0, 1999.0, 1.5) is True    # 1.0 < 1.5
    assert sl_too_tight(2000.0, 1997.0, 1.5) is False   # 3.0 >= 1.5
    assert sl_too_tight(2000.0, None, 1.5) is False     # no stop -> never too tight


def test_sl_too_tight_zero_distance_falls_through():
    # Matches live entry_manager.place_entry's inline check EXACTLY: a
    # sl_dist of exactly 0 (stop == entry) is NOT flagged as too-tight here
    # -- it falls through to _risk_sized_qty's degenerate-stop path instead.
    # Without the `0 <` lower bound this predicate would (wrongly) return
    # True at sl_dist == 0, diverging from live.
    assert sl_too_tight(2000.0, 2000.0, 1.5) is False


# ── entry_drift (lifted from entry_manager, 2026-08-12 fidelity work) ─────────
# The offline sim could not model this gate at all on M1 bars: it compares the
# signal level to the LTP at ORDER time, which is a sub-minute quantity. Live
# rejected 2 of 11 signals on it on 2026-08-12. Lifting the predicate here lets
# the 5s sim apply the identical rule (shared.gate_rules is the single source).

def test_drift_budget_is_capped_by_the_absolute_limit():
    # 0.25 * 10pt stop = 2.5pt, but the absolute cap is 0.5pt.
    assert drift_budget_pts(2000.0, 1990.0, max_pts=0.5, max_frac=0.25) == 0.5


def test_drift_budget_tightens_on_a_close_stop():
    # 0.25 * 1.2pt stop = 0.3pt, tighter than the 0.5pt cap.
    assert drift_budget_pts(2000.0, 1998.8, max_pts=0.5,
                            max_frac=0.25) == pytest.approx(0.3)


def test_drift_budget_falls_back_to_the_cap_without_a_stop():
    assert drift_budget_pts(2000.0, None, max_pts=0.5, max_frac=0.25) == 0.5


def test_drift_budget_ignores_a_zero_distance_stop():
    assert drift_budget_pts(2000.0, 2000.0, max_pts=0.5, max_frac=0.25) == 0.5


def test_entry_drift_is_positive_when_the_market_ran_with_the_trade():
    # A BUY whose market price already rose costs more than modelled.
    assert entry_drift_pts("BUY", 2000.0, 2000.7) == pytest.approx(0.7)
    # A SELL whose market price already fell sells for less than modelled.
    assert entry_drift_pts("SELL", 2000.0, 1999.3) == pytest.approx(0.7)


def test_entry_drift_is_negative_on_a_favourable_move():
    assert entry_drift_pts("BUY", 2000.0, 1999.4) == pytest.approx(-0.6)
    assert entry_drift_pts("SELL", 2000.0, 2000.6) == pytest.approx(-0.6)


def test_entry_drift_exceeded_rejects_beyond_budget():
    bad, detail = entry_drift_exceeded("BUY", 2000.0, 1996.0, ltp=2000.6,
                                       max_pts=0.5, max_frac=0.25)
    assert bad is True
    assert "drift +0.60pt vs budget 0.50pt" in detail


def test_entry_drift_exceeded_allows_within_budget():
    bad, _ = entry_drift_exceeded("BUY", 2000.0, 1996.0, ltp=2000.4,
                                  max_pts=0.5, max_frac=0.25)
    assert bad is False


def test_entry_drift_exceeded_allows_a_favourable_move():
    bad, _ = entry_drift_exceeded("SELL", 2000.0, 2004.0, ltp=2001.0,
                                  max_pts=0.5, max_frac=0.25)
    assert bad is False


def test_entry_drift_detail_matches_the_live_rejection_format():
    """Live wrote: 'entry_drift: drift +1.40pt vs budget 0.50pt (ltp 4388.63)'."""
    _, detail = entry_drift_exceeded("BUY", 4387.23, 4380.0, ltp=4388.63,
                                     max_pts=0.5, max_frac=0.25)
    assert detail == "drift +1.40pt vs budget 0.50pt (ltp 4388.63)"
