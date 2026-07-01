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
