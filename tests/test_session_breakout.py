# tests/test_session_breakout.py
from __future__ import annotations
import pandas as pd
import pytest
from strategies.backtest_strategies.kronos_session_breakout import (
    bias_series, opening_range,
)

def _m5(times, o, h, l, c):
    return pd.DataFrame({"time": pd.to_datetime(times, utc=True),
                         "open": o, "high": h, "low": l, "close": c,
                         "volume": [1.0]*len(c)})

def test_bias_up_when_price_above_rising_ema():
    # 400 bars: long flat warmup then a clean rising ramp -> bias +1 at the end
    n = 400
    closes = pd.Series([2000.0]*300 + [2000.0 + i for i in range(1, n-300+1)])
    b = bias_series(closes, n_long=240, slope_lk=48)
    assert b[-1] == 1

def test_bias_down_when_price_below_falling_ema():
    n = 400
    closes = pd.Series([2000.0]*300 + [2000.0 - i for i in range(1, n-300+1)])
    b = bias_series(closes, n_long=240, slope_lk=48)
    assert b[-1] == -1

def test_bias_undefined_is_zero_during_warmup():
    closes = pd.Series([2000.0]*100)
    b = bias_series(closes, n_long=240, slope_lk=48)
    assert set(b) == {0}

def test_opening_range_from_first_30min_bars():
    # session hour 7: bars at :00,:05,:10,:15,:20,:25 form the OR; :30 is outside
    times = [f"2026-06-15T07:{m:02d}:00Z" for m in (0,5,10,15,20,25,30)]
    o = [2000]*7; h = [2001,2003,2002,2004,2001,2000,2010]
    l = [1999,1998,1997,1999,2000,1999,1995]; c = [2000]*7
    bars = _m5(times, o, h, l, c)
    res = opening_range(bars, bars["time"].iloc[0].date(), 7, or_min=30)
    assert res is not None
    rng_hi, rng_lo, or_last = res
    assert rng_hi == 2004.0 and rng_lo == 1997.0
    assert or_last == 5                      # index of the :25 bar (last OR bar)

def test_opening_range_none_when_fewer_than_two_bars():
    times = ["2026-06-15T07:00:00Z"]
    bars = _m5(times, [2000],[2001],[1999],[2000])
    assert opening_range(bars, bars["time"].iloc[0].date(), 7, or_min=30) is None


from datetime import datetime, timezone
from strategies.backtest_strategies import kronos_session_breakout as sb

def _uptrend_m5_frame(session_hour=7, break_out=True):
    """Build a CLOSED-bar M5 frame that (a) warms the EMA into +1 bias, (b) forms an
    OR in `session_hour`, (c) ends on a bar that breaks (or not) the OR high.
    All rows are CLOSED bars — research_runner already strips the still-forming bar
    before calling get_signal, so we do NOT append an extra trailing row here."""
    rows = []
    base = pd.Timestamp("2026-06-01T00:00:00Z")
    price = 2000.0
    # 320 warmup bars, gently rising so EMA240 slopes up and price>EMA (bias +1)
    for k in range(320):
        price += 0.5
        rows.append((base + pd.Timedelta(minutes=5*k), price, price+0.4, price-0.4, price))
    # OR bars for the session hour on a fresh day at :00.. :25 (6 bars), tight range
    day = pd.Timestamp("2026-06-05T00:00:00Z")
    or_hi, or_lo = price + 2.0, price - 2.0
    for m in (0,5,10,15,20,25):
        ts = day + pd.Timedelta(hours=session_hour, minutes=m)
        rows.append((ts, price, or_hi-0.5, or_lo+0.5, price))
    # breakout bar at :30 (closed) — high pierces or_hi if break_out else stays inside
    bh = or_hi + 3.0 if break_out else or_hi - 1.0
    rows.append((day + pd.Timedelta(hours=session_hour, minutes=30), price, bh, price-0.5, price+0.2))
    return pd.DataFrame(rows, columns=["time","open","high","low","close"])

def setup_function(_):
    sb._fired_sessions.clear()

def test_long_signal_on_bias_aligned_breakout():
    f = _uptrend_m5_frame(session_hour=7, break_out=True)
    sig = sb.get_signal(None, f, None, datetime(2026,6,5,7,30,tzinfo=timezone.utc))
    assert sig is not None and sig.side == "BUY"
    assert sig.reason == "SESSION_BREAKOUT_LONG"
    assert sig.max_hold_min == 180.0
    # static sl/tp: sl == OR low, tp == entry + 1.5*OR width
    assert sig.stop_loss < sig.entry_price < sig.take_profit

def test_no_signal_when_break_absent():
    f = _uptrend_m5_frame(session_hour=7, break_out=False)
    assert sb.get_signal(None, f, None, datetime(2026,6,5,7,30,tzinfo=timezone.utc)) is None

def test_no_signal_outside_session_hours():
    f = _uptrend_m5_frame(session_hour=9, break_out=True)   # 9 not in SESSION_HOURS
    assert sb.get_signal(None, f, None, datetime(2026,6,5,9,30,tzinfo=timezone.utc)) is None

def test_no_signal_before_or_complete():
    # truncate so the last closed bar is at :25 (minute<30 -> OR incomplete)
    f = _uptrend_m5_frame(session_hour=7, break_out=True)
    f = f.iloc[:-1]   # drop the :30 breakout bar; last closed bar is now :25 (minute<30 -> OR incomplete)
    assert sb.get_signal(None, f, None, datetime(2026,6,5,7,25,tzinfo=timezone.utc)) is None

def test_one_entry_per_session_guard():
    f = _uptrend_m5_frame(session_hour=7, break_out=True)
    first = sb.get_signal(None, f, None, datetime(2026,6,5,7,30,tzinfo=timezone.utc))
    second = sb.get_signal(None, f, None, datetime(2026,6,5,7,30,tzinfo=timezone.utc))
    assert first is not None and second is None      # same (date,hour) suppressed

def test_bias_gate_blocks_counter_trend_long():
    # force bias down by flipping the warmup to a downtrend but keep an up-break
    f = _uptrend_m5_frame(session_hour=7, break_out=True)
    f.loc[:319, "close"] = [2000.0 - 0.5*k for k in range(320)]
    f.loc[:319, "high"] = f.loc[:319, "close"] + 0.4
    f.loc[:319, "low"] = f.loc[:319, "close"] - 0.4
    sig = sb.get_signal(None, f, None, datetime(2026,6,5,7,30,tzinfo=timezone.utc))
    assert sig is None or sig.side != "BUY"


def test_position_size_zero_on_nonpositive_width():
    assert sb.position_size(5000, 0.0) == (0.0, 0.0)

def test_fixed_002_lot_stop_stays_within_daily_limit():
    # At the fixed 0.02 lot, a full-OR-width stop for a wide (8pt) OR must risk
    # well under the $150 daily kill-switch: 0.02 lot = $2/pt -> 8pt = $16.
    or_pts = 8.0
    usd_per_pt_at_002 = 0.02 * (sb.USD_PER_POINT_PER_0_1_LOT / 0.1)   # $2.00
    assert or_pts * usd_per_pt_at_002 <= 150.0
