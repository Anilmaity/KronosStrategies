# tests/test_s93_fvg_scalp.py
"""S93 FVG continuation scalp (M5, killzones) — the scalping-category child
(2026-07-06). All synthetic; no DB, no network."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pandas as pd
import pytest

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies")
)
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from backtest_strategies import s93_fvg_scalp as s93  # noqa: E402
from backtest_strategies.base import Signal  # noqa: E402
from strategy.entry_manager import _VARIATION_STRATEGY_NAME  # noqa: E402

# 13:00Z: S93 was narrowed to the NY killzone hours (13,14) on 2026-09-02, so the
# fixture must sit inside a trading hour or every setup is gated out before it arms.
_T0 = pd.Timestamp("2026-06-05T13:00:00Z")


def _frame(gap=0.7):
    """20 flat warmup bars then a displacement bar leaving a bullish FVG of
    `gap` points above the bar-two-back high (2000.5)."""
    rows = []

    def add(o, h, l, c):
        rows.append(dict(time=_T0 + pd.Timedelta(minutes=5 * len(rows)),
                         open=float(o), high=float(h), low=float(l),
                         close=float(c), volume=1.0))

    for _ in range(20):
        add(2000, 2000.5, 1999.5, 2000)
    add(2000, 2000.8, 1999.8, 2000.5)                  # k-1
    add(2000.6, 2004.0, 2000.5 + gap, 2003.8)          # displacement, FVG=gap
    return pd.DataFrame(rows)


def _w1m(minute_offset, hi, lo):
    ts = _T0 + pd.Timedelta(minutes=21 * 5 + minute_offset)
    return pd.DataFrame([dict(time=ts, open=hi - 0.2, high=float(hi),
                              low=float(lo), close=hi - 0.1, volume=1.0)])


def _now(minute_offset=0):
    # 14:45Z -- matches the 22-bar fixture that starts at _T0 (13:00Z) and must sit
    # inside S93's NY killzone hours (13,14).
    base = datetime(2026, 6, 5, 14, 45, tzinfo=timezone.utc)
    return base + pd.Timedelta(minutes=minute_offset)


@pytest.fixture(autouse=True)
def _reset():
    s93.reset_state()
    yield
    s93.reset_state()


def test_fvg_arms_but_does_not_fire_on_own_bar():
    assert s93.get_signal(None, _frame(), None, _now()) is None
    assert s93._pending is not None and s93._pending["side"] == 1


def test_retrace_fires_long_with_validated_geometry():
    f = _frame()
    assert s93.get_signal(None, f, None, _now()) is None       # arm
    prox = s93._pending["prox"]
    sig = s93.get_signal(_w1m(6, hi=prox + 0.5, lo=prox - 0.1), f, None, _now(6))
    assert sig is not None and isinstance(sig, Signal)
    assert sig.side == "BUY"
    assert sig.reason == "S93_FVG_SCALP_LONG"
    assert sig.entry_price == pytest.approx(prox)              # proximal edge
    assert sig.stop_loss < 2000.5                              # beyond distal - buf
    risk = sig.entry_price - sig.stop_loss
    assert (sig.take_profit - sig.entry_price) == pytest.approx(1.5 * risk, abs=0.03)
    assert sig.max_hold_min == 120
    assert s93._pending is None


def test_probe_through_stop_cancels():
    f = _frame()
    s93.get_signal(None, f, None, _now())
    sl = s93._pending["sl"]
    assert s93.get_signal(_w1m(6, hi=2002.0, lo=sl - 0.3), f, None, _now(6)) is None
    assert s93._pending is None


def test_setup_expires():
    f = _frame()
    s93.get_signal(None, f, None, _now())
    late = _now(5 * s93._RETRACE_W + 5)
    prox = 2001.2
    assert s93.get_signal(_w1m(6, hi=prox + 0.5, lo=prox - 0.1), f, None, late) is None
    assert s93._pending is None


def test_out_of_killzone_clears_and_blocks():
    f = _frame()
    s93.get_signal(None, f, None, _now())
    off = datetime(2026, 6, 5, 10, 5, tzinfo=timezone.utc)    # 10 not a killzone hour
    assert s93.get_signal(_w1m(6, hi=2002.0, lo=2001.0), f, None, off) is None
    assert s93._pending is None


def test_small_fvg_is_ignored():
    # gap 0.2 < 0.3 x ATR(~1.0) -> no setup
    assert s93.get_signal(None, _frame(gap=0.2), None, _now()) is None
    assert s93._pending is None


def test_one_setup_per_fvg_bar():
    f = _frame()
    s93.get_signal(None, f, None, _now())
    s93._pending = None
    s93.get_signal(None, f, None, _now(1))
    assert s93._pending is None, "same FVG bar must not re-arm"


def test_config_contract():
    assert s93.NAME == "KRONOS_S93_FVG_SCALP"
    assert s93.NAME in _VARIATION_STRATEGY_NAME
    cfg = s93.CONFIG
    assert cfg.name == s93.NAME
    assert cfg.session_start_hour is None and cfg.session_end_hour is None
    assert cfg.max_concurrent_positions == 1
    assert cfg.cooldown_s >= 300


def test_hours_are_ny_killzone_only():
    """S93 was narrowed to the NY killzone hours on 2026-09-02.

    Pinned deliberately: the London-morning block (7, 8, 9) and the 12:00 US-data hour
    were persistent losers on 19.5 months of replay AND on the live broker record
    (hour 12 alone cost -$221.8 live), while 13 and 14 were the only hours positive in
    both backtest halves. Widening this tuple again should be a conscious, evidenced
    decision, not an accident -- see the module docstring for the full ladder.
    """
    assert s93._HOURS == (13, 14)


def test_morning_hours_are_gated_out():
    """A setup that would arm inside the old killzone hours must now be refused."""
    for hour in (7, 8, 9, 12):
        s93.reset_state()
        now = datetime(2026, 6, 5, hour, 45, tzinfo=timezone.utc)
        assert s93.get_signal(None, _frame(), None, now) is None
        assert s93._pending is None, f"hour {hour} must not arm a setup"
