"""
test_challenge_xau.py
---------------------
TDD tests for the XAUUSD H4 trend-follow challenge edge.

Covers the pure, deterministic logic (no network, no broker):
  - Donchian channel (causal prior-N extremes)
  - generate_signals (breakout + EMA-bias gate, no look-ahead, initial 3xATR stop)
  - chandelier trailing-stop ratchet
  - position_size (risk-based lots)
  - ChallengeGuard (FundingPips $5k 2-step drawdown / kill-switch / target)

All inputs are synthetic DataFrames built inline.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from strategies.challenge.challenge_xau import (
    donchian_channels,
    chandelier_trail,
    generate_signals,
    ChallengeSignal,
)
from strategies.challenge.risk import position_size, ChallengeGuard


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _bars(opens, highs, lows, closes) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame({
        "time":  pd.date_range("2024-01-01", periods=n, freq="4h"),
        "open":  list(map(float, opens)),
        "high":  list(map(float, highs)),
        "low":   list(map(float, lows)),
        "close": list(map(float, closes)),
        "volume": [1.0] * n,
    })


def _flat_then(series_close, *, span=60):
    """A long flat run (so EMAs warm up and converge) followed by `series_close`.
    Returns (opens, highs, lows, closes) lists. Flat run sits at 2000.0."""
    base = [2000.0] * span
    closes = base + list(series_close)
    opens = closes[:]                      # open==prior close-ish; keep simple = close
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    return opens, highs, lows, closes


# ---------------------------------------------------------------------------
# Donchian channel
# ---------------------------------------------------------------------------
def test_donchian_upper_is_prior_n_high_excluding_current():
    # highs: 1,2,3,4,5,6 ; n=3 ; upper at idx5 = max(high[2..4]) = max(3,4,5)=5
    b = _bars([0]*6, [1, 2, 3, 4, 5, 6], [1, 1, 1, 1, 1, 1], [1, 2, 3, 4, 5, 6])
    ch = donchian_channels(b, 3)
    assert math.isclose(ch["upper"].iloc[5], 5.0)   # excludes the current bar's high (6)
    assert math.isclose(ch["lower"].iloc[5], 1.0)


def test_donchian_warmup_is_nan():
    b = _bars([0]*6, [1, 2, 3, 4, 5, 6], [1]*6, [1, 2, 3, 4, 5, 6])
    ch = donchian_channels(b, 3)
    # first n bars (0,1,2) have no full prior-N window -> NaN
    assert ch["upper"].iloc[:3].isna().all()


# ---------------------------------------------------------------------------
# generate_signals
# ---------------------------------------------------------------------------
def test_buy_signal_on_upward_breakout_with_bullish_bias():
    # Flat at 2000, then a clean staircase up -> breakout above prior Donchian high
    # with EMA20 > EMA50 (rising). Expect at least one BUY, none SELL.
    up = [2000 + i * 5 for i in range(1, 16)]   # 2005..2075 rising
    o, h, l, c = _flat_then(up)
    b = _bars(o, h, l, c)
    sigs = generate_signals(b, donchian_n=10, ema_fast=20, ema_slow=50, atr_n=14, atr_mult=3.0)
    assert any(s.direction == "BUY" for s in sigs)
    assert all(s.direction != "SELL" for s in sigs)


def test_sell_signal_on_downward_breakout_with_bearish_bias():
    dn = [2000 - i * 5 for i in range(1, 16)]   # falling
    o, h, l, c = _flat_then(dn)
    b = _bars(o, h, l, c)
    sigs = generate_signals(b, donchian_n=10, ema_fast=20, ema_slow=50, atr_n=14, atr_mult=3.0)
    assert any(s.direction == "SELL" for s in sigs)
    assert all(s.direction != "BUY" for s in sigs)


def test_breakout_without_bias_is_filtered_out():
    # A single one-bar spike up on an otherwise flat series: it breaks the Donchian
    # high but the slow/fast EMAs are still equal (no bullish bias) -> no signal.
    o, h, l, c = _flat_then([2050.0])           # one spike then nothing
    b = _bars(o, h, l, c)
    sigs = generate_signals(b, donchian_n=10, ema_fast=20, ema_slow=50, atr_n=14, atr_mult=3.0)
    assert sigs == []


def test_signal_entry_is_next_bar_and_no_lookahead():
    up = [2000 + i * 5 for i in range(1, 16)]
    o, h, l, c = _flat_then(up)
    b = _bars(o, h, l, c)
    sigs = generate_signals(b, donchian_n=10, ema_fast=20, ema_slow=50, atr_n=14, atr_mult=3.0)
    s = sigs[0]
    # entry is the bar AFTER the signal bar; never the last bar (nothing to fill on)
    assert 0 < s.entry_index < len(b)
    # truncating the frame to include only through the entry bar yields the SAME
    # first signal -> proves the decision used only data up to the trigger bar
    # (no dependence on later bars).
    trig = s.entry_index - 1
    sigs_trunc = generate_signals(b.iloc[: trig + 2], donchian_n=10, ema_fast=20,
                                  ema_slow=50, atr_n=14, atr_mult=3.0)
    assert any(x.entry_index == s.entry_index for x in sigs_trunc)


def test_buy_initial_stop_is_three_atr_below_and_risk_positive():
    up = [2000 + i * 5 for i in range(1, 16)]
    o, h, l, c = _flat_then(up)
    b = _bars(o, h, l, c)
    sigs = generate_signals(b, donchian_n=10, ema_fast=20, ema_slow=50, atr_n=14, atr_mult=3.0)
    s = next(x for x in sigs if x.direction == "BUY")
    assert s.risk_per_unit > 0
    assert math.isclose(s.risk_per_unit, 3.0 * s.atr, rel_tol=1e-9)
    # stop sits risk_per_unit below the trigger close
    assert s.sl < s.trigger_close
    assert math.isclose(s.trigger_close - s.sl, s.risk_per_unit, rel_tol=1e-9)


# ---------------------------------------------------------------------------
# Chandelier trailing stop
# ---------------------------------------------------------------------------
def test_chandelier_long_trails_up_never_down():
    # long: stop = highest_high_since_entry - mult*atr ; ratchets up only
    s1 = chandelier_trail("BUY", prev_stop=1990.0, extreme_since_entry=2010.0, atr=5.0, mult=3.0)
    assert math.isclose(s1, 2010.0 - 15.0)       # 1995 -> moved up from 1990
    # a lower extreme / wider atr must NOT loosen the stop
    s2 = chandelier_trail("BUY", prev_stop=s1, extreme_since_entry=2008.0, atr=8.0, mult=3.0)
    assert s2 == s1                               # stays at 1995, never drops


def test_chandelier_short_trails_down_never_up():
    s1 = chandelier_trail("SELL", prev_stop=2010.0, extreme_since_entry=1990.0, atr=5.0, mult=3.0)
    assert math.isclose(s1, 1990.0 + 15.0)       # 2005
    s2 = chandelier_trail("SELL", prev_stop=s1, extreme_since_entry=1992.0, atr=8.0, mult=3.0)
    assert s2 == s1                               # never rises


# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------
def test_position_size_from_risk_rounds_down_to_lot_step():
    # 1% of 5000 = $50 risk ; sl_distance $15 ; XAU $100/$1/lot
    # raw = 50 / (15*100) = 0.0333 -> 0.03
    lots = position_size(equity=5000.0, risk_pct=0.01, sl_distance=15.0)
    assert math.isclose(lots, 0.03)


def test_position_size_zero_when_sl_distance_zero():
    assert position_size(equity=5000.0, risk_pct=0.01, sl_distance=0.0) == 0.0


def test_position_size_zero_when_min_lot_would_exceed_risk():
    # tiny equity: even 0.01 lot risks more than the budget -> refuse (0.0)
    lots = position_size(equity=100.0, risk_pct=0.01, sl_distance=15.0)
    assert lots == 0.0


# ---------------------------------------------------------------------------
# ChallengeGuard (FundingPips $5k 2-step phase 1)
# ---------------------------------------------------------------------------
def _guard():
    return ChallengeGuard(initial_balance=5000.0, profit_target_pct=0.10,
                          max_daily_dd_pct=0.05, max_overall_dd_pct=0.10,
                          max_consec_losses=2)


def test_guard_allows_trading_fresh():
    g = _guard()
    g.start_new_day(5000.0)
    ok, reason = g.can_trade(5000.0)
    assert ok and reason == "ok"


def test_guard_blocks_after_two_consecutive_losses():
    g = _guard(); g.start_new_day(5000.0)
    g.record_trade(-30.0)
    g.record_trade(-30.0)
    ok, reason = g.can_trade(4940.0)
    assert not ok and reason == "consec_losses"


def test_guard_win_resets_loss_streak():
    g = _guard(); g.start_new_day(5000.0)
    g.record_trade(-30.0)
    g.record_trade(+20.0)        # win clears the streak
    g.record_trade(-30.0)
    ok, reason = g.can_trade(4960.0)
    assert ok and reason == "ok"


def test_guard_blocks_on_daily_drawdown():
    g = _guard(); g.start_new_day(5000.0)
    g.record_trade(-260.0)       # > 5% of 5000 = 250
    ok, reason = g.can_trade(4740.0)
    assert not ok and reason == "daily_dd"


def test_guard_blocks_on_overall_drawdown_floor():
    g = _guard(); g.start_new_day(4600.0)
    ok, reason = g.can_trade(4490.0)   # below 4500 floor (10% of 5000)
    assert not ok and reason == "overall_dd"


def test_guard_blocks_when_profit_target_reached():
    g = _guard(); g.start_new_day(5400.0)
    ok, reason = g.can_trade(5510.0)   # >= 5500 target
    assert not ok and reason == "target_reached"


def test_guard_new_day_resets_daily_counters():
    g = _guard(); g.start_new_day(5000.0)
    g.record_trade(-260.0)             # blows the daily limit
    assert not g.can_trade(4740.0)[0]
    g.start_new_day(4740.0)            # next day, fresh daily budget
    ok, reason = g.can_trade(4740.0)
    assert ok and reason == "ok"
