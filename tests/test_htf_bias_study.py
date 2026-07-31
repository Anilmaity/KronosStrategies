# tests/test_htf_bias_study.py
"""Unit tests for the opt15 Task-13 HTF-bias study scaffolding
(strategies/research/replay_lib.py).

Covered regardless of the study's ship/no-ship verdict (brief requirement):
  * bias computation on synthetic D1/H4 fixtures -- known HH/HL (bullish),
    LH/LL (bearish), and mixed (ranging); alignment + H4-only + veto logic.
  * the M1-path fill model -- BUY/SELL TP, SL, SL-before-TP tie-break, TIME
    exit, end-of-data force close, and friction/half-size net arithmetic.
  * trade_stats PF / avg / maxDD / win-rate.

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
# Synthetic HTF frames
# ──────────────────────────────────────────────────────────────────────────────

def _wave_frame(n=48, drift_h=0.0, drift_l=0.0, period=8, amp=6.0,
                base_h=100.0, base_l=90.0):
    """A sine zigzag with independent drift on the high and low envelopes.

    A sin peak is a swing high; a trough is a swing low. Positive drift makes
    each successive peak/trough higher; negative lower. So:
      drift_h>0, drift_l>0 -> HH & HL -> bullish
      drift_h<0, drift_l<0 -> LH & LL -> bearish
      drift_h>0, drift_l<0 -> HH & LL -> mixed (ranging)
    """
    t = np.arange(n)
    s = np.sin(2 * np.pi * t / period)
    high = base_h + drift_h * t + amp * s + 0.5
    low = base_l + drift_l * t + amp * s - 0.5
    mid = (high + low) / 2.0
    return pd.DataFrame({
        "time": pd.date_range("2025-01-01", periods=n, freq="4h"),
        "open": mid, "high": high, "low": low, "close": mid,
    })


def test_structure_bias_bullish():
    assert rl.structure_bias(_wave_frame(drift_h=0.8, drift_l=0.8)) == "bullish"


def test_structure_bias_bearish():
    assert rl.structure_bias(_wave_frame(drift_h=-0.8, drift_l=-0.8)) == "bearish"


def test_structure_bias_mixed_is_ranging():
    # higher highs but lower lows -> expansion, neither HH&HL nor LH&LL
    assert rl.structure_bias(_wave_frame(drift_h=0.8, drift_l=-0.8)) == "ranging"


def test_structure_bias_short_frame_ranging():
    assert rl.structure_bias(_wave_frame(n=6)) == "ranging"
    assert rl.structure_bias(pd.DataFrame(columns=["high", "low"])) == "ranging"


def test_aligned_bias_agreeing():
    bull = _wave_frame(drift_h=0.8, drift_l=0.8)
    bear = _wave_frame(drift_h=-0.8, drift_l=-0.8)
    assert rl.aligned_d1h4_bias(bull, bull) == "bull"
    assert rl.aligned_d1h4_bias(bear, bear) == "bear"


def test_aligned_bias_disagreement_is_none():
    bull = _wave_frame(drift_h=0.8, drift_l=0.8)
    bear = _wave_frame(drift_h=-0.8, drift_l=-0.8)
    rang = _wave_frame(drift_h=0.8, drift_l=-0.8)
    assert rl.aligned_d1h4_bias(bull, bear) == "none"   # disagree
    assert rl.aligned_d1h4_bias(bull, rang) == "none"   # one ranging
    assert rl.aligned_d1h4_bias(rang, bear) == "none"


def test_h4_only_bias():
    assert rl.h4_only_bias(_wave_frame(drift_h=0.8, drift_l=0.8)) == "bull"
    assert rl.h4_only_bias(_wave_frame(drift_h=-0.8, drift_l=-0.8)) == "bear"
    assert rl.h4_only_bias(_wave_frame(drift_h=0.8, drift_l=-0.8)) == "none"


def test_opposes():
    assert rl.opposes("SELL", "bull") is True
    assert rl.opposes("BUY", "bear") is True
    assert rl.opposes("BUY", "bull") is False
    assert rl.opposes("SELL", "bear") is False
    assert rl.opposes("BUY", "none") is False
    assert rl.opposes("SELL", "none") is False


# ──────────────────────────────────────────────────────────────────────────────
# BiasEngine no-look-ahead behaviour
# ──────────────────────────────────────────────────────────────────────────────

def test_bias_engine_uses_only_closed_bars():
    # D1/H4 rising frames; query mid-way. The engine must not consult the
    # still-forming bar (D1 closes at label+1d, H4 at label+4h).
    d1 = _wave_frame(n=60, drift_h=0.8, drift_l=0.8)
    d1 = d1.assign(time=pd.date_range("2025-01-01", periods=60, freq="1D"))
    h4 = _wave_frame(n=200, drift_h=0.8, drift_l=0.8)
    h4 = h4.assign(time=pd.date_range("2025-01-01", periods=200, freq="4h"))
    eng = rl.BiasEngine(d1, h4)
    # a query at 2025-02-15 12:00 should see D1 bars up to 2025-02-14 and H4 up
    # to the 08:00 bar (12:00 bar not yet closed) -- both rising -> 'bull'.
    aligned, h4o = eng.bias_at(pd.Timestamp("2025-02-15 12:00"))
    assert aligned == "bull"
    assert h4o == "bull"
    # a query before enough closed history exists -> 'none'
    aligned0, _ = eng.bias_at(pd.Timestamp("2025-01-02 00:00"))
    assert aligned0 == "none"


# ──────────────────────────────────────────────────────────────────────────────
# Fill model
# ──────────────────────────────────────────────────────────────────────────────

def _m1(prices):
    """Build M1 arrays from a list of (high, low, close) tuples, 1-min spaced
    starting at 2025-01-01 00:00. Returns (t_ns, high, low, close)."""
    n = len(prices)
    t = pd.date_range("2025-01-01", periods=n, freq="1min")
    t_ns = t.astype("datetime64[ns]").astype("int64").to_numpy()
    high = np.array([p[0] for p in prices], float)
    low = np.array([p[1] for p in prices], float)
    close = np.array([p[2] for p in prices], float)
    return t_ns, high, low, close


def test_fill_buy_tp():
    # entry at bar0 (00:00); scan starts bar1. bar1 no hit; bar2 high>=tp.
    t_ns, h, l, c = _m1([(100, 100, 100), (101, 99.5, 100.5), (102.5, 100, 102)])
    r = rl.simulate_exit(t_ns, h, l, c, pd.Timestamp("2025-01-01 00:00"),
                         "BUY", entry_price=100.0, sl=99.0, tp=102.0,
                         max_hold_min=None)
    assert r.reason == "TP"
    assert r.exit_price == 102.0
    assert r.gross_pts == pytest.approx(2.0)


def test_fill_buy_sl():
    t_ns, h, l, c = _m1([(100, 100, 100), (100.5, 98.5, 99.0)])
    r = rl.simulate_exit(t_ns, h, l, c, pd.Timestamp("2025-01-01 00:00"),
                         "BUY", 100.0, 99.0, 102.0, None)
    assert r.reason == "SL"
    assert r.gross_pts == pytest.approx(-1.0)


def test_fill_sl_before_tp_tie():
    # bar1 touches BOTH tp(102) and sl(99) -> SL wins (conservative)
    t_ns, h, l, c = _m1([(100, 100, 100), (103.0, 98.0, 101.0)])
    r = rl.simulate_exit(t_ns, h, l, c, pd.Timestamp("2025-01-01 00:00"),
                         "BUY", 100.0, 99.0, 102.0, None)
    assert r.reason == "SL"
    assert r.gross_pts == pytest.approx(-1.0)


def test_fill_time_exit():
    # sl/tp never hit; max_hold 3 min -> exit at the bar whose elapsed>=3min.
    t_ns, h, l, c = _m1([(100, 100, 100), (100.2, 99.8, 100.1),
                         (100.3, 99.7, 100.2), (100.4, 99.6, 100.25)])
    r = rl.simulate_exit(t_ns, h, l, c, pd.Timestamp("2025-01-01 00:00"),
                         "BUY", 100.0, 90.0, 110.0, max_hold_min=3)
    assert r.reason == "TIME"
    assert r.exit_price == pytest.approx(100.25)   # close of the +3min bar
    assert r.gross_pts == pytest.approx(0.25)


def test_fill_sell_tp():
    t_ns, h, l, c = _m1([(100, 100, 100), (100.5, 97.5, 98.0)])
    r = rl.simulate_exit(t_ns, h, l, c, pd.Timestamp("2025-01-01 00:00"),
                         "SELL", 100.0, 101.0, 98.0, None)
    assert r.reason == "TP"
    assert r.gross_pts == pytest.approx(2.0)


def test_fill_eod_force_close():
    t_ns, h, l, c = _m1([(100, 100, 100), (100.5, 99.8, 100.2)])
    r = rl.simulate_exit(t_ns, h, l, c, pd.Timestamp("2025-01-01 00:00"),
                         "BUY", 100.0, 90.0, 110.0, None)
    assert r.reason == "EOD"
    assert r.exit_price == pytest.approx(100.2)


def test_net_and_halfsize():
    assert rl.net_from_gross(2.0, 0.45) == pytest.approx(1.55)
    assert rl.net_from_gross(2.0, 0.80) == pytest.approx(1.20)
    assert rl.net_from_gross(2.0, 0.45, size=0.5) == pytest.approx(0.775)


def test_trade_stats():
    st = rl.trade_stats([2.0, -1.0, 3.0, -2.0])
    assert st["n"] == 4
    assert st["pf"] == pytest.approx(5.0 / 3.0)
    assert st["avg_pts"] == pytest.approx(0.5)
    assert st["max_dd"] == pytest.approx(-2.0)
    assert st["win_rate"] == pytest.approx(0.5)
    assert st["total"] == pytest.approx(2.0)


def test_trade_stats_no_losses_pf_inf():
    st = rl.trade_stats([1.0, 2.0])
    assert st["pf"] == float("inf")
    assert st["max_dd"] == pytest.approx(0.0)
