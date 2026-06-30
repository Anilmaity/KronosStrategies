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
    signal_at_last_bar,
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
# Live trigger on the most-recently-closed bar
# ---------------------------------------------------------------------------
def test_signal_at_last_bar_fires_on_breakout_trigger():
    # Build the up-staircase frame, then trim it so the LAST row is a breakout
    # trigger bar -> live evaluation should return a BUY entering at market now.
    up = [2000 + i * 5 for i in range(1, 16)]
    o, h, l, c = _flat_then(up)
    b = _bars(o, h, l, c)
    full = generate_signals(b, donchian_n=10, ema_fast=20, ema_slow=50, atr_n=14, atr_mult=3.0)
    trig = full[0].entry_index - 1                      # the trigger bar index
    live_frame = b.iloc[: trig + 1]                     # last row == trigger bar
    s = signal_at_last_bar(live_frame, donchian_n=10, ema_fast=20, ema_slow=50,
                           atr_n=14, atr_mult=3.0)
    assert s is not None and s.direction == "BUY"
    assert s.entry_index == len(live_frame)             # market entry "now" (next bar)


def test_signal_at_last_bar_none_when_no_breakout():
    o, h, l, c = _flat_then([2000.0])                   # flat, no breakout on last bar
    b = _bars(o, h, l, c)
    s = signal_at_last_bar(b, donchian_n=10, ema_fast=20, ema_slow=50, atr_n=14, atr_mult=3.0)
    assert s is None


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


def test_position_size_floors_to_min_lot_when_under_risk_cap():
    # 1% sizing rounds to 0, but 0.01 lot risks 0.01*100*100=$100 = 2% of 5000,
    # which is <= the 2.5% cap -> trade the min lot.
    lots = position_size(equity=5000.0, risk_pct=0.01, sl_distance=100.0,
                         max_trade_risk_pct=0.025)
    assert lots == 0.01


def test_position_size_refuses_min_lot_when_over_risk_cap():
    # 0.01 lot risks 0.01*100*200=$200 = 4% of 5000 > 2.5% cap -> refuse.
    lots = position_size(equity=5000.0, risk_pct=0.01, sl_distance=200.0,
                         max_trade_risk_pct=0.025)
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


def test_guard_risk_budget_is_min_of_daily_room_and_overall_room():
    g = _guard(); g.start_new_day(5000.0)
    # fresh: daily limit 250 (5% of 5000), overall room 500 (down to 4500 floor) -> 250
    assert math.isclose(g.risk_budget(5000.0), 250.0)
    g.record_trade(-100.0)             # daily room now 150
    assert math.isclose(g.risk_budget(4900.0), 150.0)


def test_guard_risk_budget_never_negative():
    g = _guard(); g.start_new_day(5000.0)
    g.record_trade(-300.0)             # blown past daily limit
    assert g.risk_budget(4700.0) == 0.0


def test_guard_new_day_resets_daily_counters():
    g = _guard(); g.start_new_day(5000.0)
    g.record_trade(-260.0)             # blows the daily limit
    assert not g.can_trade(4740.0)[0]
    g.start_new_day(4740.0)            # next day, fresh daily budget
    ok, reason = g.can_trade(4740.0)
    assert ok and reason == "ok"


# ---------------------------------------------------------------------------
# Runner glue (dry broker, no network) — enter then trail
# ---------------------------------------------------------------------------
def test_completed_bars_keeps_the_newest_closed_bar():
    # tsdb_reader.fetch_candles returns ONLY completed candles (it filters the
    # still-forming one). The runner must therefore NOT drop the last row — the
    # newest bar IS the just-closed H4 bar we act on. Dropping it evaluated signals
    # one H4 bar (~4h) late. Pin that _prepare_bars preserves the newest bar.
    import strategies.challenge.live_runner as lr
    raw = pd.DataFrame({
        "time":  pd.to_datetime(["2026-06-29T08:00:00Z", "2026-06-29T12:00:00Z",
                                 "2026-06-29T16:00:00Z"], utc=True),
        "open":  [2000.0, 2001.0, 2002.0], "high": [2003.0, 2004.0, 2005.0],
        "low":   [1999.0, 2000.0, 2001.0], "close": [2001.0, 2002.0, 2003.0],
        "volume": [1.0, 1.0, 1.0],
    })
    out = lr._prepare_bars(raw)
    assert len(out) == 3                                   # nothing dropped
    assert pd.Timestamp(out["time"].iloc[-1]).hour == 16   # newest bar preserved


def test_prepare_bars_sorts_and_handles_empty():
    import strategies.challenge.live_runner as lr
    assert lr._prepare_bars(pd.DataFrame()).empty
    raw = pd.DataFrame({
        "time":  pd.to_datetime(["2026-06-29T16:00:00Z", "2026-06-29T08:00:00Z"], utc=True),
        "open":  [2.0, 1.0], "high": [2.0, 1.0], "low": [2.0, 1.0],
        "close": [2.0, 1.0], "volume": [1.0, 1.0],
    })
    out = lr._prepare_bars(raw)
    assert list(out["close"]) == [1.0, 2.0]                # sorted oldest->newest


def test_challenge_dashboard_disabled_makes_no_db_calls():
    # When disabled (no ids / sync off), every method returns None and never tries
    # to connect — so a misconfigured dashboard can't break the bot.
    from strategies.challenge.dashboard import ChallengeDashboard
    d = ChallengeDashboard(strategy_id="", user_strategy_id="", user_broker_id="",
                           currencypair_id="", enabled=False)
    assert d.open_position("buy", 2000.0, 0.01, "t1") is None
    assert d.conclude_position("p1", 10.0) is None
    assert d.record_signal("BUY", 2000.0, 1990.0, None) is None
    assert d.find_open_position_id() is None
    d.update_live("p1", ltp=2000.0)   # no-op, must not raise


def test_challenge_dashboard_cash_conversion():
    # Broker dollars -> dashboard 'cash' unit (divide by XAUUSD contract size 100).
    from strategies.challenge.dashboard import ChallengeDashboard
    d = ChallengeDashboard(strategy_id="s", user_strategy_id="u", user_broker_id="b",
                           currencypair_id="c", enabled=True, contract_size=100.0)
    assert d._to_cash(250.0) == 2.5
    assert d._to_cash(None) is None


class _FakeDashboard:
    """Records calls so tests can assert the runner drives the dashboard correctly."""
    def __init__(self):
        self.calls = []
        self.next_id = "dash-1"
    def open_position(self, side, entry, volume, broker_ticket=None):
        self.calls.append(("open", side, round(entry, 2), volume, broker_ticket)); return self.next_id
    def update_live(self, position_id, ltp=None, profit_loss=None):
        self.calls.append(("update", position_id, ltp, profit_loss))
    def conclude_position(self, position_id, realized_pnl, close_price=None, side=None, volume=None, reason="EXIT"):
        self.calls.append(("conclude", position_id, realized_pnl))
    def record_signal(self, side, entry, sl, tp, status="PLACED", reason="", rejection_reason="", signal_at=None, position_id=None):
        self.calls.append(("signal", status, side, rejection_reason))
    def find_open_position_id(self):
        self.calls.append(("find",)); return None


class _FakeBroker:
    dry_run = False
    def __init__(self, pnl=0.0):
        self._pnl = pnl
    def position_realized_pnl(self, position_id):
        return self._pnl


def _runner_with(dash, broker=None, equity_guard=True):
    from strategies.challenge.broker import ChallengeBroker
    from strategies.challenge.risk import ChallengeGuard as _G
    from strategies.challenge.live_runner import Runner
    b = broker or ChallengeBroker("", "", dry_run=True, label="t")
    g = _G(5000.0, profit_target_pct=0.10, max_daily_dd_pct=0.05, max_overall_dd_pct=0.10, max_consec_losses=2)
    g.start_new_day(5000.0)
    return Runner(b, g, dashboard=dash)


def _breakout_frame():
    up = [2000 + i * 5 for i in range(1, 16)]
    o, h, l, c = _flat_then(up)
    b = _bars(o, h, l, c)
    full = generate_signals(b, donchian_n=10, ema_fast=20, ema_slow=50, atr_n=14, atr_mult=3.0)
    return b.iloc[: full[0].entry_index - 1 + 1]   # ends on the trigger bar


def test_runner_records_dashboard_on_entry():
    import strategies.challenge.live_runner as lr
    lr.PARAMS = {"donchian_n": 10, "ema_fast": 20, "ema_slow": 50, "atr_n": 14, "atr_mult": 3.0}
    dash = _FakeDashboard()
    r = _runner_with(dash)
    r._maybe_enter(_breakout_frame(), equity=5000.0)
    assert r.position is not None
    assert r.position.get("dash_id") == "dash-1"
    kinds = [c[0] for c in dash.calls]
    assert "open" in kinds                                   # apis_position created
    assert ("signal", "PLACED", "BUY", "") in dash.calls     # Signals-tab row


def test_runner_records_rejected_when_guard_blocks():
    import strategies.challenge.live_runner as lr
    lr.PARAMS = {"donchian_n": 10, "ema_fast": 20, "ema_slow": 50, "atr_n": 14, "atr_mult": 3.0}
    dash = _FakeDashboard()
    r = _runner_with(dash)
    r.guard.record_trade(-30.0); r.guard.record_trade(-30.0)   # 2 consec losses -> blocked
    r._maybe_enter(_breakout_frame(), equity=4940.0)
    assert r.position is None
    assert not any(c[0] == "open" for c in dash.calls)          # nothing opened
    assert any(c[0] == "signal" and c[1] == "REJECTED" for c in dash.calls)


def test_runner_concludes_dashboard_on_close():
    dash = _FakeDashboard()
    r = _runner_with(dash, broker=_FakeBroker(pnl=12.5))
    r.position = {"id": "t1", "dash_id": "d1", "direction": "BUY", "stop": 1990.0,
                  "extreme": 2010.0, "volume": 0.01, "atr": 5.0}
    r._on_position_closed()
    assert r.position is None
    assert ("conclude", "d1", 12.5) in dash.calls
    assert r.guard.day_realized == 12.5                        # PnL still recorded into guard


def test_runner_enters_and_trails_with_dry_broker():
    from strategies.challenge.broker import ChallengeBroker
    from strategies.challenge.risk import ChallengeGuard as _G
    from strategies.challenge.live_runner import Runner

    broker = ChallengeBroker("", "", dry_run=True, label="test")
    guard = _G(5000.0, profit_target_pct=0.10, max_daily_dd_pct=0.05,
               max_overall_dd_pct=0.10, max_consec_losses=2)
    guard.start_new_day(5000.0)
    r = Runner(broker, guard)

    # a frame whose LAST bar is an up-breakout trigger -> runner opens a long
    up = [2000 + i * 5 for i in range(1, 16)]
    o, h, l, c = _flat_then(up)
    b = _bars(o, h, l, c)
    full = generate_signals(b, donchian_n=10, ema_fast=20, ema_slow=50, atr_n=14, atr_mult=3.0)
    trig = full[0].entry_index - 1
    frame = b.iloc[: trig + 1]

    # default PARAMS use donchian_n=20 etc; this synthetic frame is tuned for n=10,
    # so drive the decision directly with matching params via signal_at_last_bar path.
    import strategies.challenge.live_runner as lr
    lr.PARAMS = {"donchian_n": 10, "ema_fast": 20, "ema_slow": 50, "atr_n": 14, "atr_mult": 3.0}

    r._maybe_enter(frame, equity=5000.0)
    assert r.position is not None
    assert r.position["direction"] == "BUY"
    entry_stop = r.position["stop"]

    # a strongly higher bar must ratchet the chandelier stop UP, never down
    higher = pd.Series({"high": 2200.0, "low": 2150.0, "close": 2190.0})
    r._trail(higher, current_atr=5.0)
    assert r.position["stop"] >= entry_stop
