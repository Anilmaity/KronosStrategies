# tests/test_session_breakout.py
"""SESSION_BREAKOUT (M5 bias-filtered ORB) unit tests.

Restored from the fix/tg-copy-fidelity branch (the module was reconciled onto
feat/strategy-manager without its tests) and extended for the 2026-07-06
stale-fill fix: entries now fire off the newest CLOSED 1m bar's boundary touch
(~1 min after the touch) instead of waiting for the M5 break bar to become
visible (live fills were 5-15 min late — every 07-01..07-06 live entry fired
exactly one M5 bar behind the achievable time).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pandas as pd
import pytest

# Strategy modules use absolute imports rooted at `strategies/` (matching the
# live runner's sys.path), so put that dir on the path before importing.
_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies")
)
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from backtest_strategies import kronos_session_breakout as sb  # noqa: E402
from backtest_strategies.kronos_session_breakout import (  # noqa: E402
    bias_series, opening_range,
)


def _m5(times, o, h, l, c):
    return pd.DataFrame({"time": pd.to_datetime(times, utc=True),
                         "open": o, "high": h, "low": l, "close": c,
                         "volume": [1.0] * len(c)})


def setup_function(_):
    sb._fired_sessions.clear()


# ── bias / opening-range helpers (restored) ──────────────────────────────────

def test_bias_up_when_price_above_rising_ema():
    n = 400
    closes = pd.Series([2000.0] * 300 + [2000.0 + i for i in range(1, n - 300 + 1)])
    b = bias_series(closes, n_long=240, slope_lk=48)
    assert b[-1] == 1


def test_bias_down_when_price_below_falling_ema():
    n = 400
    closes = pd.Series([2000.0] * 300 + [2000.0 - i for i in range(1, n - 300 + 1)])
    b = bias_series(closes, n_long=240, slope_lk=48)
    assert b[-1] == -1


def test_bias_undefined_is_zero_during_warmup():
    closes = pd.Series([2000.0] * 100)
    b = bias_series(closes, n_long=240, slope_lk=48)
    assert set(b) == {0}


def test_opening_range_from_first_30min_bars():
    times = [f"2026-06-15T07:{m:02d}:00Z" for m in (0, 5, 10, 15, 20, 25, 30)]
    o = [2000] * 7; h = [2001, 2003, 2002, 2004, 2001, 2000, 2010]
    l = [1999, 1998, 1997, 1999, 2000, 1999, 1995]; c = [2000] * 7
    bars = _m5(times, o, h, l, c)
    res = opening_range(bars, bars["time"].iloc[0].date(), 7, or_min=30)
    assert res is not None
    rng_hi, rng_lo, or_last = res
    assert rng_hi == 2004.0 and rng_lo == 1997.0
    assert or_last == 5


def test_opening_range_none_when_fewer_than_two_bars():
    times = ["2026-06-15T07:00:00Z"]
    bars = _m5(times, [2000], [2001], [1999], [2000])
    assert opening_range(bars, bars["time"].iloc[0].date(), 7, or_min=30) is None


# ── fixtures ──────────────────────────────────────────────────────────────────

_DAY = pd.Timestamp("2026-06-05T00:00:00Z")


def _uptrend_m5_frame(session_hour=7, break_out=True, with_break_bar=True):
    """CLOSED-bar M5 frame: 320-bar rising warmup (bias +1), an OR at
    `session_hour` (:00..:25, 6 bars), and optionally the :30 breakout bar.
    Returns (frame, rng_hi, rng_lo): rng_* are the OR extremes the strategy
    should derive."""
    rows = []
    base = pd.Timestamp("2026-06-01T00:00:00Z")
    price = 2000.0
    for k in range(320):
        price += 0.5
        rows.append((base + pd.Timedelta(minutes=5 * k),
                     price, price + 0.4, price - 0.4, price))
    or_hi, or_lo = price + 2.0, price - 2.0
    for m in (0, 5, 10, 15, 20, 25):
        ts = _DAY + pd.Timedelta(hours=session_hour, minutes=m)
        rows.append((ts, price, or_hi - 0.5, or_lo + 0.5, price))
    rng_hi, rng_lo = or_hi - 0.5, or_lo + 0.5
    if with_break_bar:
        bh = or_hi + 3.0 if break_out else rng_hi - 1.0
        rows.append((_DAY + pd.Timedelta(hours=session_hour, minutes=30),
                     price, bh, price - 0.5, price + 0.2))
    f = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
    return f, rng_hi, rng_lo


def _w1m_bar(session_hour, minute, hi, lo):
    """A single closed 1m bar (the only row the 1m probe reads)."""
    ts = _DAY + pd.Timedelta(hours=session_hour, minutes=minute)
    return pd.DataFrame([(ts, hi - 0.5, hi, lo, hi - 0.2)],
                        columns=["time", "open", "high", "low", "close"])


# ── M5 fallback path (restored originals) ────────────────────────────────────

def test_long_signal_on_bias_aligned_breakout():
    f, rng_hi, rng_lo = _uptrend_m5_frame(session_hour=7, break_out=True)
    sig = sb.get_signal(None, f, None, datetime(2026, 6, 5, 7, 30, tzinfo=timezone.utc))
    assert sig is not None and sig.side == "BUY"
    assert sig.reason == "SESSION_BREAKOUT_LONG"
    assert sig.max_hold_min == 180.0
    assert sig.entry_price == pytest.approx(rng_hi)
    assert sig.stop_loss == pytest.approx(rng_lo)
    assert sig.take_profit == pytest.approx(round(rng_hi + 1.5 * (rng_hi - rng_lo), 2))


def test_no_signal_when_break_absent():
    f, _, _ = _uptrend_m5_frame(session_hour=7, break_out=False)
    assert sb.get_signal(None, f, None,
                         datetime(2026, 6, 5, 7, 30, tzinfo=timezone.utc)) is None


def test_no_signal_outside_session_hours():
    f, _, _ = _uptrend_m5_frame(session_hour=9, break_out=True)  # 9 not in SESSION_HOURS
    assert sb.get_signal(None, f, None,
                         datetime(2026, 6, 5, 9, 30, tzinfo=timezone.utc)) is None


def test_no_signal_before_or_complete():
    f, _, _ = _uptrend_m5_frame(session_hour=7, break_out=True)
    f = f.iloc[:-1]  # last closed bar :25 -> OR incomplete
    assert sb.get_signal(None, f, None,
                         datetime(2026, 6, 5, 7, 25, tzinfo=timezone.utc)) is None


# ── 1m-touch path (2026-07-06 stale-fill fix) ────────────────────────────────

def test_1m_touch_fires_before_m5_break_bar_closes():
    # M5 frame ends at the :25 OR bar — the M5 break bar has NOT closed yet.
    f, rng_hi, rng_lo = _uptrend_m5_frame(session_hour=7, with_break_bar=False)
    w1m = _w1m_bar(7, 31, hi=rng_hi + 1.0, lo=rng_hi - 1.0)
    sig = sb.get_signal(w1m, f, None, datetime(2026, 6, 5, 7, 32, tzinfo=timezone.utc))
    assert sig is not None and sig.side == "BUY"
    # Entry books at the boundary, exactly like the backtest fill.
    assert sig.entry_price == pytest.approx(rng_hi)
    assert sig.stop_loss == pytest.approx(rng_lo)


def test_1m_touch_blocked_before_or_complete():
    f, rng_hi, _ = _uptrend_m5_frame(session_hour=7, with_break_bar=False)
    w1m = _w1m_bar(7, 29, hi=rng_hi + 1.0, lo=rng_hi - 1.0)  # minute 29 < 30
    assert sb.get_signal(w1m, f, None,
                         datetime(2026, 6, 5, 7, 30, tzinfo=timezone.utc)) is None


def test_1m_touch_and_m5_probe_share_session_dedup():
    f_no_break, rng_hi, _ = _uptrend_m5_frame(session_hour=7, with_break_bar=False)
    w1m = _w1m_bar(7, 31, hi=rng_hi + 1.0, lo=rng_hi - 1.0)
    first = sb.get_signal(w1m, f_no_break, None,
                          datetime(2026, 6, 5, 7, 32, tzinfo=timezone.utc))
    assert first is not None
    # Five minutes later the M5 break bar has closed — same session must NOT refire.
    f_break, _, _ = _uptrend_m5_frame(session_hour=7, break_out=True)
    again = sb.get_signal(w1m, f_break, None,
                          datetime(2026, 6, 5, 7, 36, tzinfo=timezone.utc))
    assert again is None


def test_bias_blocked_break_does_not_consume_session():
    # Flat warmup -> bias 0: a touch must neither fire nor mark the session
    # fired (a later bias-aligned break in the same session may still trade).
    rows = []
    base = pd.Timestamp("2026-06-01T00:00:00Z")
    for k in range(320):
        rows.append((base + pd.Timedelta(minutes=5 * k), 2000.0, 2000.4, 1999.6, 2000.0))
    for m in (0, 5, 10, 15, 20, 25):
        ts = _DAY + pd.Timedelta(hours=7, minutes=m)
        rows.append((ts, 2000.0, 2001.5, 1998.5, 2000.0))
    f = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
    w1m = _w1m_bar(7, 31, hi=2002.5, lo=2001.0)
    assert sb.get_signal(w1m, f, None,
                         datetime(2026, 6, 5, 7, 32, tzinfo=timezone.utc)) is None
    assert len(sb._fired_sessions) == 0
