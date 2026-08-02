import os, sys
_STRAT_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "strategies"))
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from datetime import datetime, time
from shared.gate_rules import parse_utc_windows, in_news_blackout, sl_too_tight  # noqa: E402


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
