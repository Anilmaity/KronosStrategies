"""
test_challenge_xau.py
---------------------
TDD tests for backtest_strategies.kronos_challenge_xau — the H4 Donchian
trend-follow port of bot/challenge_xau.py (the deployable 5000->5500 answer).

The economic edge (PF 1.83, positive every year) was validated offline in the
source repo's backtest; these tests guard the PORT — that get_signal correctly
resamples the 15m window to H4, applies the EMA20/50 bias + Donchian(20)
breakout, and emits a *trailing* Signal with the right geometry and contract.

All synthetic: hand-built 15m DataFrames, tz-naive UTC time column. No DB,
no network, no real cache.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pandas as pd

# Strategy modules use absolute imports rooted at `strategies/` (matching the
# live runner's sys.path), so put that dir on the path before importing.
_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies")
)
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from backtest_strategies import kronos_challenge_xau as strat  # noqa: E402
from backtest_strategies.base import Signal  # noqa: E402


_START = pd.Timestamp("2025-01-01 00:00:00")  # tz-naive UTC


def _ramp_15m(n: int, start: float, step_per_bar: float, rng: float = 0.5) -> pd.DataFrame:
    """n consecutive 15m bars trending by `step_per_bar` per bar, each with a
    +/- `rng` intrabar range so ATR > 0. close == open + step (drift)."""
    rows = []
    px = start
    for i in range(n):
        t = _START + pd.Timedelta(minutes=15 * i)
        o = px
        c = px + step_per_bar
        hi = max(o, c) + rng
        lo = min(o, c) - rng
        rows.append(dict(time=t, open=o, high=hi, low=lo, close=c, volume=100.0))
        px = c
    return pd.DataFrame(rows)


# How many 15m bars to comfortably clear the strategy's H4 lookback need.
_N_BARS = 1400


def _now():
    return datetime(2025, 1, 21, tzinfo=timezone.utc)


def test_uptrend_breakout_emits_trailing_buy():
    w15m = _ramp_15m(_N_BARS, start=2000.0, step_per_bar=0.1)  # steady climb
    sig = strat.get_signal(None, None, w15m, _now())
    assert sig is not None, "steady uptrend making new highs should break out long"
    assert isinstance(sig, Signal)
    assert sig.side == "BUY"
    assert sig.trailing is True
    # Chandelier geometry: hard stop below entry, far placeholder TP above.
    assert sig.stop_loss < sig.entry_price
    assert sig.take_profit > sig.entry_price
    # Trail distance is positive and (entry - stop) ~ k_atr * ATR > 0.
    assert (sig.entry_price - sig.stop_loss) > 0
    assert "CHALLENGE" in sig.reason.upper()


def test_downtrend_breakdown_emits_trailing_sell():
    w15m = _ramp_15m(_N_BARS, start=2000.0, step_per_bar=-0.1)  # steady decline
    sig = strat.get_signal(None, None, w15m, _now())
    assert sig is not None, "steady downtrend making new lows should break out short"
    assert sig.side == "SELL"
    assert sig.trailing is True
    assert sig.stop_loss > sig.entry_price    # stop above for a short
    assert sig.take_profit < sig.entry_price  # far placeholder below
    assert (sig.stop_loss - sig.entry_price) > 0


def test_flat_market_no_signal():
    # Perfectly flat: no breakout, bias undefined -> no trade.
    w15m = _ramp_15m(_N_BARS, start=2000.0, step_per_bar=0.0, rng=0.5)
    sig = strat.get_signal(None, None, w15m, _now())
    assert sig is None


def test_insufficient_history_returns_none():
    w15m = _ramp_15m(100, start=2000.0, step_per_bar=0.1)  # nowhere near H4 reach
    assert strat.get_signal(None, None, w15m, _now()) is None
    assert strat.get_signal(None, None, None, _now()) is None


def test_config_contract():
    assert strat.NAME == "CHALLENGE_XAU"
    cfg = strat.CONFIG
    assert cfg.name == strat.NAME
    # H4 strategy: one entry per bar at most -> cooldown ~= one H4 (14400s),
    # and it runs around the clock (no intraday session gate).
    assert cfg.cooldown_s >= 14400
    assert cfg.session_start_hour is None and cfg.session_end_hour is None
    assert cfg.max_concurrent_positions == 1
