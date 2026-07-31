# tests/test_trail_exit_study.py
"""Unit tests for the opt15 Task-14 trailing-exit study scaffolding
(strategies/research/replay_lib.py chandelier simulators + the two required
replay_lib fixes).

Covered (brief requirements):
  * replay_lib fix #1 -- trade_stats maxDD seeds a 0 equity origin (a book
    underwater from trade 1 reports its true peak-to-trough DD).
  * tail_capture / trade_stats.tail5 -- sum of top-5 winners.
  * simulate_chandelier_exit (arm a): trail activates ONLY after +1R; ratchets
    monotonically (never loosens on a lower high); mirror symmetry for shorts;
    TIME backstop retained; EOD fallback.
  * simulate_time_replace_exit (arm b): identical to baseline on SL/TP; only
    trails AFTER max_hold; a would-be TIME winner is extended; a trade that gave
    back more than k*ATR by max_hold exits at the baseline TIME close (no
    fictitious above/below-market fill); short mirror.

Matches the test-suite convention: put strategies/ on sys.path and import flat.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies")
)
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from research import replay_lib as rl  # noqa: E402


# ──────────────────────────────────────────────────────────────────────────────
# Synthetic M1 path builder
# ──────────────────────────────────────────────────────────────────────────────

def _m1(prices):
    """M1 arrays from a list of (high, low, close) tuples, 1-min spaced from
    2025-01-01 00:00. bar0 is the entry bar (excluded by _scan_start)."""
    n = len(prices)
    t = pd.date_range("2025-01-01", periods=n, freq="1min")
    t_ns = t.astype("datetime64[ns]").astype("int64").to_numpy()
    high = np.array([p[0] for p in prices], float)
    low = np.array([p[1] for p in prices], float)
    close = np.array([p[2] for p in prices], float)
    return t_ns, high, low, close


ENTRY_T = pd.Timestamp("2025-01-01 00:00")


# ──────────────────────────────────────────────────────────────────────────────
# Fix #1: maxDD seeds a 0 origin
# ──────────────────────────────────────────────────────────────────────────────

def test_maxdd_underwater_from_trade_one():
    # Pre-fix cummax started at the first trade's equity (-5) and reported -3;
    # the true peak-to-trough from the 0 pre-trade origin is -8.
    st = rl.trade_stats([-5.0, -3.0])
    assert st["max_dd"] == pytest.approx(-8.0)


def test_maxdd_unchanged_for_recovering_book():
    # A book that goes positive first is unaffected by the 0 seed.
    st = rl.trade_stats([2.0, -1.0, 3.0, -2.0])
    assert st["max_dd"] == pytest.approx(-2.0)


def test_maxdd_no_losses_is_zero():
    st = rl.trade_stats([1.0, 2.0])
    assert st["max_dd"] == pytest.approx(0.0)


# ──────────────────────────────────────────────────────────────────────────────
# tail_capture / tail5
# ──────────────────────────────────────────────────────────────────────────────

def test_tail_capture_top5():
    assert rl.tail_capture([1, 2, 3, 4, 5, 6], 5) == pytest.approx(2 + 3 + 4 + 5 + 6)


def test_tail_capture_fewer_than_five_winners():
    assert rl.tail_capture([-1, -2, 3, -4], 5) == pytest.approx(3.0)


def test_tail_capture_no_winners_is_zero():
    assert rl.tail_capture([-1, -2, -3], 5) == pytest.approx(0.0)


def test_trade_stats_carries_tail5():
    st = rl.trade_stats([1.0, 5.0, -2.0, 4.0])
    assert st["tail5"] == pytest.approx(1.0 + 5.0 + 4.0)


# ──────────────────────────────────────────────────────────────────────────────
# Chandelier arm (a): activation only after +1R
# ──────────────────────────────────────────────────────────────────────────────

def test_chandelier_no_activation_static_sl():
    # R=1 (entry 100, sl 99); price never reaches +1R (101) before stopping at
    # sl -> exit reason 'SL' (NOT 'TRAIL'), static-SL loss.
    t_ns, h, l, c = _m1([
        (100, 100, 100),        # entry bar (excluded)
        (100.5, 99.8, 100.0),
        (100.8, 99.5, 100.0),   # peak 100.8 < 101 -> never activates
        (100.2, 98.9, 99.0),    # low 98.9 <= sl 99 -> SL
    ])
    r = rl.simulate_chandelier_exit(t_ns, h, l, c, ENTRY_T, "BUY",
                                    entry_price=100.0, sl=99.0, atr=2.0, k=2.0,
                                    max_hold_min=None)
    assert r.reason == "SL"
    assert r.exit_price == pytest.approx(99.0)
    assert r.gross_pts == pytest.approx(-1.0)


def test_chandelier_activates_after_1r_and_trails():
    # R=1, atr=1, k=2 (dist=2). Activation at +1R=101.
    t_ns, h, l, c = _m1([
        (100, 100, 100),        # entry
        (102, 100, 101),        # hw 102 >= 101 -> activate; stop=100 (checked next)
        (103, 101, 102),        # hw 103 -> stop=101
        (100.9, 100.5, 100.7),  # low 100.5 <= stop 101 -> TRAIL at 101
    ])
    r = rl.simulate_chandelier_exit(t_ns, h, l, c, ENTRY_T, "BUY",
                                    100.0, 99.0, atr=1.0, k=2.0, max_hold_min=None)
    assert r.reason == "TRAIL"
    assert r.exit_price == pytest.approx(101.0)
    assert r.gross_pts == pytest.approx(1.0)


def test_chandelier_ratchet_is_monotonic():
    # After a high water of 106 (stop -> 104), a bar with a LOWER high must NOT
    # lower the stop; the trade later stops out at the ratcheted 104.
    t_ns, h, l, c = _m1([
        (100, 100, 100),        # entry (R=5, sl=95)
        (106, 101, 105),        # hw 106 -> activate, stop=104
        (105, 104.2, 104.5),    # lower high: stop stays 104 (monotonic)
        (104.3, 103.9, 104.0),  # low 103.9 <= 104 -> TRAIL at 104
    ])
    r = rl.simulate_chandelier_exit(t_ns, h, l, c, ENTRY_T, "BUY",
                                    100.0, 95.0, atr=1.0, k=2.0, max_hold_min=None)
    assert r.reason == "TRAIL"
    assert r.exit_price == pytest.approx(104.0)
    assert r.gross_pts == pytest.approx(4.0)


def test_chandelier_short_mirror():
    # Mirror of the long trail case around entry 100.
    t_ns, h, l, c = _m1([
        (100, 100, 100),        # entry (R=1, sl=101)
        (100, 98, 99),          # lw 98 <= 99 (act at -1R=99) -> activate, stop=100
        (99.5, 97, 98),         # lw 97 -> stop=99
        (99.2, 98.5, 99.0),     # high 99.2 >= stop 99 -> TRAIL at 99
    ])
    r = rl.simulate_chandelier_exit(t_ns, h, l, c, ENTRY_T, "SELL",
                                    100.0, 101.0, atr=1.0, k=2.0, max_hold_min=None)
    assert r.reason == "TRAIL"
    assert r.exit_price == pytest.approx(99.0)
    assert r.gross_pts == pytest.approx(1.0)


def _mirror(prices, entry):
    """Reflect a long path around ``entry`` into the exact short path:
    (h, l, c) -> (2E-l, 2E-h, 2E-c)."""
    return [(2 * entry - p[1], 2 * entry - p[0], 2 * entry - p[2]) for p in prices]


def test_chandelier_mirror_symmetry_gross_equal():
    # A long scenario and its price-mirror short must produce identical gross.
    longp = [
        (100, 100, 100),
        (104, 101, 103),
        (106, 103, 105),
        (107, 105.5, 106),   # pulls back
        (107, 103.8, 104),   # trail hit
    ]
    t_ns, h, l, c = _m1(longp)
    rlong = rl.simulate_chandelier_exit(t_ns, h, l, c, ENTRY_T, "BUY",
                                        100.0, 99.0, atr=1.0, k=2.0,
                                        max_hold_min=None)
    sp = _mirror(longp, 100.0)
    t2, h2, l2, c2 = _m1(sp)
    rshort = rl.simulate_chandelier_exit(t2, h2, l2, c2, ENTRY_T, "SELL",
                                         100.0, 101.0, atr=1.0, k=2.0,
                                         max_hold_min=None)
    assert rlong.reason == rshort.reason
    assert rlong.gross_pts == pytest.approx(rshort.gross_pts)


def test_chandelier_time_backstop():
    # No trail activation (drifts up < +1R); TIME fires at the +3min bar close.
    t_ns, h, l, c = _m1([
        (100, 100, 100),          # entry (R=1, sl=99, act 101)
        (100.5, 99.9, 100.2),
        (100.8, 100.1, 100.5),
        (100.9, 100.2, 100.7),    # elapsed 3min -> TIME at close 100.7
    ])
    r = rl.simulate_chandelier_exit(t_ns, h, l, c, ENTRY_T, "BUY",
                                    100.0, 99.0, atr=1.0, k=3.0, max_hold_min=3)
    assert r.reason == "TIME"
    assert r.exit_price == pytest.approx(100.7)
    assert r.gross_pts == pytest.approx(0.7)


# ──────────────────────────────────────────────────────────────────────────────
# TIME-replacement arm (b)
# ──────────────────────────────────────────────────────────────────────────────

def test_time_replace_matches_baseline_on_tp():
    prices = [(100, 100, 100), (101, 99.5, 100.5), (102.5, 100, 102)]
    t_ns, h, l, c = _m1(prices)
    base = rl.simulate_exit(t_ns, h, l, c, ENTRY_T, "BUY", 100.0, 99.0, 102.0,
                            max_hold_min=1000)
    tr = rl.simulate_time_replace_exit(t_ns, h, l, c, ENTRY_T, "BUY", 100.0,
                                       99.0, 102.0, atr=1.0, k=2.0,
                                       max_hold_min=1000)
    assert base.reason == "TP" and tr.reason == "TP"
    assert tr.gross_pts == pytest.approx(base.gross_pts)


def test_time_replace_matches_baseline_on_sl():
    prices = [(100, 100, 100), (100.5, 98.5, 99.0)]
    t_ns, h, l, c = _m1(prices)
    base = rl.simulate_exit(t_ns, h, l, c, ENTRY_T, "BUY", 100.0, 99.0, 102.0,
                            max_hold_min=1000)
    tr = rl.simulate_time_replace_exit(t_ns, h, l, c, ENTRY_T, "BUY", 100.0,
                                       99.0, 102.0, atr=1.0, k=2.0,
                                       max_hold_min=1000)
    assert base.reason == "SL" and tr.reason == "SL"
    assert tr.gross_pts == pytest.approx(base.gross_pts)


def test_time_replace_extends_would_be_time_winner():
    # TP=200 (never), max_hold=3. Baseline flat-closes at the +3min bar (TIME).
    # arm (b) instead trails from there and rides higher.
    prices = [
        (100, 100, 100),   # entry (sl 95)
        (104, 101, 103),
        (105, 103, 104),
        (106, 104, 105),   # +3min: max_hold; cand=104<=close105 -> start trail
        (110, 106, 109),   # hw 110 -> stop 108
        (110, 107.5, 108),  # low 107.5 <= 108 -> TRAIL at 108
    ]
    t_ns, h, l, c = _m1(prices)
    base = rl.simulate_exit(t_ns, h, l, c, ENTRY_T, "BUY", 100.0, 95.0, 200.0,
                            max_hold_min=3)
    tr = rl.simulate_time_replace_exit(t_ns, h, l, c, ENTRY_T, "BUY", 100.0,
                                       95.0, 200.0, atr=1.0, k=2.0,
                                       max_hold_min=3)
    assert base.reason == "TIME"
    assert base.gross_pts == pytest.approx(5.0)      # close of the +3min bar
    assert tr.reason == "TRAIL"
    assert tr.exit_price == pytest.approx(108.0)
    assert tr.gross_pts == pytest.approx(8.0)         # captured the extension


def test_time_replace_breached_at_maxhold_equals_time_close():
    # Ran to 110 then gave back > k*ATR by max_hold: the implied chandelier stop
    # (108) is above the max_hold close (103), so arm (b) must exit at the close
    # -- identical to the baseline TIME exit (no fictitious above-market fill).
    prices = [
        (100, 100, 100),   # entry sl 95
        (110, 101, 109),   # hw 110
        (110, 102, 103),
        (104, 102, 103),   # +3min max_hold; cand 108 > close 103 -> TIME close
    ]
    t_ns, h, l, c = _m1(prices)
    base = rl.simulate_exit(t_ns, h, l, c, ENTRY_T, "BUY", 100.0, 95.0, 200.0,
                            max_hold_min=3)
    tr = rl.simulate_time_replace_exit(t_ns, h, l, c, ENTRY_T, "BUY", 100.0,
                                       95.0, 200.0, atr=1.0, k=2.0,
                                       max_hold_min=3)
    assert base.reason == "TIME"
    assert tr.reason == "TIME"
    assert tr.gross_pts == pytest.approx(base.gross_pts)
    assert tr.gross_pts == pytest.approx(3.0)


def test_time_replace_short_extension():
    # Short mirror of the extension case.
    prices = [
        (100, 100, 100),   # entry sl 105
        (99, 95, 96),
        (97, 92, 93),
        (94, 90, 91),      # +3min max_hold; cand 92 >= close 91 -> trail
        (90, 86, 87),      # lw 86 -> stop 88
        (89, 87.5, 88),    # high 89 >= 88 -> TRAIL at 88
    ]
    t_ns, h, l, c = _m1(prices)
    base = rl.simulate_exit(t_ns, h, l, c, ENTRY_T, "SELL", 100.0, 105.0, 0.0,
                            max_hold_min=3)
    tr = rl.simulate_time_replace_exit(t_ns, h, l, c, ENTRY_T, "SELL", 100.0,
                                       105.0, 0.0, atr=1.0, k=2.0,
                                       max_hold_min=3)
    assert base.reason == "TIME"
    assert tr.reason == "TRAIL"
    assert tr.exit_price == pytest.approx(88.0)
    assert tr.gross_pts == pytest.approx(12.0)


def test_scan_start_excludes_entry_bar_uniformly():
    # All three simulators share _scan_start: an SL touch on the entry bar itself
    # must be ignored (that minute is excluded); the exit only comes later.
    prices = [
        (100, 90, 100),          # entry bar dips to 90 -- MUST be ignored
        (100.5, 99.0, 99.5),     # first scanned bar; no SL/TP
        (102.5, 100, 102),       # TP
    ]
    t_ns, h, l, c = _m1(prices)
    base = rl.simulate_exit(t_ns, h, l, c, ENTRY_T, "BUY", 100.0, 95.0, 102.0,
                            max_hold_min=None)
    cha = rl.simulate_chandelier_exit(t_ns, h, l, c, ENTRY_T, "BUY", 100.0, 95.0,
                                      atr=1.0, k=2.0, max_hold_min=None)
    tr = rl.simulate_time_replace_exit(t_ns, h, l, c, ENTRY_T, "BUY", 100.0,
                                       95.0, 102.0, atr=1.0, k=2.0,
                                       max_hold_min=None)
    # baseline + time_replace reach TP; chandelier (no TP) trails/EOD -- but none
    # may stop out on the entry bar's 90 low.
    assert base.reason == "TP"
    assert tr.reason == "TP"
    assert cha.reason in ("TRAIL", "EOD", "SL")
    assert cha.exit_price > 95.0    # never filled the excluded entry-bar dip
