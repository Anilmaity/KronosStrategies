"""S98 z-score MR: crossing logic, closed-bar guard, ADF gate, SL/TP maths."""
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

# Strategy modules use absolute imports rooted at `strategies/` (matching the
# live runner's sys.path), so put that dir on the path before importing.
_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies")
)
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from backtest_strategies import s98_zscore_mr_m15 as s98  # noqa: E402

LOOK = 117  # bars fed: >= 50 + 49 so 50 ADF residuals exist AND the last bar
#             lands on 05:00 UTC, so .replace(hour=5) keeps the closed/forming
#             timing consistent with _closed_only.
T0 = pd.Timestamp("2026-04-01 00:00:00+00:00")


def _w15m(closes, end_closed=True):
    """Build a w15m frame whose LAST bar is closed at `now` (default)."""
    n = len(closes)
    idx = pd.date_range(T0, periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({"time": idx, "open": closes, "high": closes,
                         "low": closes, "close": closes})


def _now_after(w15m, minutes=15):
    """A now_utc such that the last bar of w15m is CLOSED."""
    last = pd.to_datetime(w15m["time"].iloc[-1])
    return (last + pd.Timedelta(minutes=minutes)).to_pydatetime()


def _stationary_series(n=LOOK, spike=None):
    """AR(1) mean-reverting series around 4000 with tiny noise; optionally
    end with a `spike`-sigma jump on the final bar (in units of the rolling
    std of the base series)."""
    rng = np.random.default_rng(7)
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = 0.5 * x[i - 1] + rng.normal(0, 1.0)
    closes = 4000.0 + x
    if spike is not None:
        base = pd.Series(closes[:-1])
        sd = float(base.rolling(50).std(ddof=1).iloc[-1])
        mean = float(base.rolling(50).mean().iloc[-1])
        closes[-1] = mean + spike * sd
    return closes


def _sig(closes, hour=5, now=None):
    w = _w15m(closes)
    now = now or _now_after(w)
    now = now.replace(hour=hour)
    return s98.get_signal(None, None, w, now), w


def test_no_signal_without_cross():
    sig, _ = _sig(_stationary_series())          # no spike
    assert sig is None


def test_sell_on_upward_cross_with_stationary_residuals():
    sig, w = _sig(_stationary_series(spike=2.5))
    assert sig is not None and sig.side == "SELL"
    c = w["close"].astype(float)
    mean = float(c.rolling(50).mean().iloc[-1])
    sd = float(c.rolling(50).std(ddof=1).iloc[-1])
    assert sig.take_profit == round(mean, 2)
    assert sig.stop_loss == round(mean + 3.5 * sd, 2)
    assert sig.stop_loss > sig.entry_price > sig.take_profit
    assert sig.max_hold_min == 240


def test_buy_on_downward_cross():
    sig, _ = _sig(_stationary_series(spike=-2.5))
    assert sig is not None and sig.side == "BUY"
    assert sig.stop_loss < sig.entry_price < sig.take_profit


def test_no_refire_when_already_stretched():
    """prev |z| >= entry_z: state, not a cross -> no signal."""
    closes = _stationary_series(spike=2.5)
    closes = np.append(closes, closes[-1] + 0.5)  # stays stretched next bar
    sig, _ = _sig(closes)
    assert sig is None


def test_adf_gate_rejects_trending_series():
    """A strong deterministic trend is non-stationary -> ADF blocks the fade.

    Trend PLUS a final spike so the z-cross actually fires and ONLY the ADF
    gate can reject it (a constant-slope trend alone gives a constant z that
    never crosses -- see brief Step 5).
    """
    n = LOOK
    closes = 4000.0 + np.arange(n) * 3.0          # relentless uptrend
    sd = float(pd.Series(closes[:-1]).rolling(50).std(ddof=1).iloc[-1])
    closes[-1] += 3 * sd                          # force the cross on the last bar
    sig, _ = _sig(closes)
    assert sig is None


def test_session_gate():
    sig, _ = _sig(_stationary_series(spike=2.5), hour=12)
    assert sig is None


def test_forming_bar_excluded():
    """If now_utc is INSIDE the last bar's 15 minutes, that bar must not
    be used: with the spike on the forming bar, no signal fires."""
    closes = _stationary_series(spike=2.5)
    w = _w15m(closes)
    inside = (pd.to_datetime(w["time"].iloc[-1])
              + pd.Timedelta(minutes=5)).to_pydatetime().replace(hour=5)
    assert s98.get_signal(None, None, w, inside) is None


def test_insufficient_bars():
    assert s98.get_signal(None, None, _w15m(_stationary_series(30)[:30]),
                          datetime(2026, 4, 1, 5, 0, tzinfo=timezone.utc)) is None
    assert s98.get_signal(None, None, None,
                          datetime(2026, 4, 1, 5, 0, tzinfo=timezone.utc)) is None
