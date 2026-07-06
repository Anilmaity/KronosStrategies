"""
test_s95_s96_s97.py
-------------------
Unit tests for the three Strategy Manager v1 child strategies
(docs/superpowers/specs/2026-07-02-strategy-manager-design.md §4):

  s95_session_breakout  — London/NY opening-range breakout (M5)
  s96_h1_momentum       — pure M5 EMA9/21 crossover, chandelier trailing
  s97_snap_scalper_m5   — M5 snap-fade with HTF structure bias (PAPER slot)

All synthetic: hand-built M5/15m DataFrames, tz-naive UTC time column.
No DB, no network, no real cache.
"""
from __future__ import annotations

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

from backtest_strategies import s95_session_breakout as s95  # noqa: E402
from backtest_strategies import s96_h1_momentum as s96  # noqa: E402
from backtest_strategies import s97_snap_scalper_m5 as s97  # noqa: E402
from backtest_strategies.base import Signal  # noqa: E402
from strategy.entry_manager import _VARIATION_STRATEGY_NAME  # noqa: E402


# ══════════════════════════════════════════════════════════════════════════════
# Shared helpers
# ══════════════════════════════════════════════════════════════════════════════

_DAY = pd.Timestamp("2025-01-21")  # a Tuesday, tz-naive UTC


def _utc(h: int, m: int = 0) -> datetime:
    return datetime(2025, 1, 21, h, m, tzinfo=timezone.utc)


def _m5_frame(bars: list[tuple]) -> pd.DataFrame:
    """bars = [(hh, mm, o, h, l, c), ...] -> M5 DataFrame (closed bars)."""
    rows = []
    for hh, mm, o, h, l, c in bars:
        rows.append(dict(
            time=_DAY + pd.Timedelta(hours=hh, minutes=mm),
            open=float(o), high=float(h), low=float(l), close=float(c),
            volume=100.0,
        ))
    return pd.DataFrame(rows)


def _ramp_15m(n: int, start: float, step_per_bar: float, rng: float = 0.5) -> pd.DataFrame:
    """n consecutive 15m bars trending by `step_per_bar` per bar."""
    rows = []
    px = start
    t0 = _DAY - pd.Timedelta(days=30)
    for i in range(n):
        t = t0 + pd.Timedelta(minutes=15 * i)
        o = px
        c = px + step_per_bar
        hi = max(o, c) + rng
        lo = min(o, c) - rng
        rows.append(dict(time=t, open=o, high=hi, low=lo, close=c, volume=100.0))
        px = c
    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════════════════════════
# S95 — session opening-range breakout (delegate of kronos_session_breakout,
# rewritten 2026-07-06: 30-min OR, EMA240 bias, SL 2.0xOR / TP 0.8xOR)
# ══════════════════════════════════════════════════════════════════════════════

def _s95_orb_window(session_hour: int = 7, break_out: bool = True,
                    with_break_bar: bool = True):
    """320-bar rising M5 warmup (EMA240+48 bias -> +1), a 6-bar 30-min OR at
    `session_hour` on _DAY, optionally a :30 bar breaking the OR high.
    Returns (frame, rng_hi, rng_lo)."""
    rows = []
    base = _DAY - pd.Timedelta(days=4)
    price = 2000.0
    for k in range(320):
        price += 0.5
        rows.append(dict(time=base + pd.Timedelta(minutes=5 * k), open=price,
                         high=price + 0.4, low=price - 0.4, close=price,
                         volume=100.0))
    or_hi, or_lo = price + 2.0, price - 2.0
    for m in (0, 5, 10, 15, 20, 25):
        rows.append(dict(time=_DAY + pd.Timedelta(hours=session_hour, minutes=m),
                         open=price, high=or_hi - 0.5, low=or_lo + 0.5,
                         close=price, volume=100.0))
    rng_hi, rng_lo = or_hi - 0.5, or_lo + 0.5
    if with_break_bar:
        bh = or_hi + 3.0 if break_out else rng_hi - 1.0
        rows.append(dict(time=_DAY + pd.Timedelta(hours=session_hour, minutes=30),
                         open=price, high=bh, low=price - 0.5, close=price + 0.2,
                         volume=100.0))
    return pd.DataFrame(rows), rng_hi, rng_lo


@pytest.fixture(autouse=True)
def _reset_s95_state():
    s95.reset_state()
    yield
    s95.reset_state()


def test_s95_breakout_long_geometry():
    w5m, rng_hi, rng_lo = _s95_orb_window(7)
    sig = s95.get_signal(None, w5m, None, _utc(7, 35))
    assert sig is not None and isinstance(sig, Signal)
    assert sig.side == "BUY"
    assert sig.reason == "S95_ORB_LONG"
    rng = rng_hi - rng_lo
    # Entry books at the boundary; SL 2.0xOR below it, TP 0.8xOR above it.
    assert sig.entry_price == pytest.approx(rng_hi)
    assert sig.stop_loss == pytest.approx(round(rng_hi - 2.0 * rng, 2))
    assert sig.take_profit == pytest.approx(round(rng_hi + 0.8 * rng, 2))
    assert sig.max_hold_min == 180.0


def test_s95_one_trade_per_session_window():
    w5m, _, _ = _s95_orb_window(7)
    now = _utc(7, 35)
    assert s95.get_signal(None, w5m, None, now) is not None
    # One entry per (date, session-hour) — a refire is withheld.
    assert s95.get_signal(None, w5m, None, now) is None


def test_s95_no_signal_without_break():
    w5m, _, _ = _s95_orb_window(7, break_out=False)
    assert s95.get_signal(None, w5m, None, _utc(7, 35)) is None


def test_s95_session_gating():
    w5m, _, _ = _s95_orb_window(9)     # 9 not in SESSION_HOURS [1,7,12,13,14]
    assert s95.get_signal(None, w5m, None, _utc(9, 35)) is None


def test_s95_bias_gate_blocks_counter_trend_short():
    # Rising warmup -> bias +1; a downside break must NOT fire a short, and a
    # bias-blocked break must not consume the session either.
    w5m, rng_hi, rng_lo = _s95_orb_window(7, with_break_bar=False)
    down = dict(time=_DAY + pd.Timedelta(hours=7, minutes=30),
                open=rng_lo + 0.5, high=rng_lo + 0.6, low=rng_lo - 3.0,
                close=rng_lo - 1.0, volume=100.0)
    w5m = pd.concat([w5m, pd.DataFrame([down])], ignore_index=True)
    assert s95.get_signal(None, w5m, None, _utc(7, 35)) is None


def test_s95_insufficient_data():
    assert s95.get_signal(None, None, None, _utc(7, 40)) is None
    tiny = _m5_frame([(7, 0, 2000, 2001, 1999, 2000)])
    assert s95.get_signal(None, tiny, None, _utc(7, 40)) is None


def test_s95_config_contract():
    assert s95.NAME == "KRONOS_S95_SESSION_BREAKOUT"
    assert s95.NAME in _VARIATION_STRATEGY_NAME
    cfg = s95.CONFIG
    assert cfg.name == s95.NAME
    # Five discrete session hours are gated inside get_signal, not via CONFIG.
    assert cfg.session_start_hour is None and cfg.session_end_hour is None
    assert cfg.max_concurrent_positions == 1
    assert cfg.cooldown_s >= 300


# ══════════════════════════════════════════════════════════════════════════════
# S96 — H1 Donchian(24) continuation (static high-WR exits, rewritten 2026-07-06)
# ══════════════════════════════════════════════════════════════════════════════

_N15 = 700   # 15m bars -> ~175 H1 bars, comfortably past the 76-bar minimum


def test_s96_uptrend_breakout_buy_static_geometry():
    w15m = _ramp_15m(_N15, start=2000.0, step_per_bar=0.2)
    sig = s96.get_signal(None, None, w15m, _utc(12))
    assert sig is not None and isinstance(sig, Signal)
    assert sig.side == "BUY"
    assert sig.trailing is False                        # static SL/TP pair
    assert sig.max_hold_min is None                     # broker SL/TP resolve it
    risk = sig.entry_price - sig.stop_loss
    assert risk > 0
    # TP = 0.4R (the high-WR geometry).
    assert (sig.take_profit - sig.entry_price) == pytest.approx(0.4 * risk, abs=0.03)
    assert sig.reason == "S96_H1_DON_LONG"


def test_s96_downtrend_breakdown_sell():
    w15m = _ramp_15m(_N15, start=2000.0, step_per_bar=-0.2)
    sig = s96.get_signal(None, None, w15m, _utc(12))
    assert sig is not None
    assert sig.side == "SELL"
    assert sig.trailing is False
    risk = sig.stop_loss - sig.entry_price
    assert risk > 0
    assert (sig.entry_price - sig.take_profit) == pytest.approx(0.4 * risk, abs=0.03)
    assert sig.reason == "S96_H1_DON_SHORT"


def test_s96_flat_market_no_signal():
    # Perfectly flat: no Donchian break, bias undefined -> no trade.
    w15m = _ramp_15m(_N15, start=2000.0, step_per_bar=0.0)
    assert s96.get_signal(None, None, w15m, _utc(12)) is None


def test_s96_insufficient_history():
    short = _ramp_15m(120, start=2000.0, step_per_bar=0.1)  # < _MIN_H1 * 4
    assert s96.get_signal(None, None, short, _utc(12)) is None
    assert s96.get_signal(None, None, None, _utc(12)) is None


def test_s96_sl_distance_tracks_h1_atr():
    w15m = _ramp_15m(_N15, start=2000.0, step_per_bar=0.2)
    sig = s96.get_signal(None, None, w15m, _utc(12))
    assert sig is not None
    # H1 bucket = 4 x 15m bars of +0.1 drift and +/-0.5 range -> ATR(14,H1)
    # ~ 1.4; SL distance = 3 x ATR ~ 4.2. Ballpark-assert sane, not degenerate.
    dist = sig.entry_price - sig.stop_loss
    assert 1.0 < dist < 12.0


def test_s96_config_contract():
    assert s96.NAME == "KRONOS_S96_H1_MOMENTUM"          # DB identity — unchanged
    assert s96.NAME in _VARIATION_STRATEGY_NAME
    cfg = s96.CONFIG
    assert cfg.name == s96.NAME
    assert cfg.cooldown_s == 3600                        # one H1 bar
    assert cfg.session_start_hour is None and cfg.session_end_hour is None
    assert cfg.max_concurrent_positions == 1


# ══════════════════════════════════════════════════════════════════════════════
# S97 — M5 snap-fade scalper (PAPER slot)
# ══════════════════════════════════════════════════════════════════════════════

def _m5_noise(n: int = 80, scale: float = 0.15,
              last_two_increments: tuple[float, float] | None = None) -> pd.DataFrame:
    """M5 closes = 2000 + cumulative deterministic pseudo-noise; optionally
    force the last two close-to-close increments (the '2-bar move')."""
    rs = np.random.RandomState(42)
    inc = rs.normal(0.0, scale, size=n)
    if last_two_increments is not None:
        inc[-2], inc[-1] = last_two_increments
    closes = 2000.0 + np.cumsum(inc)
    rows = []
    t0 = _DAY + pd.Timedelta(hours=4)                   # inside 03-09 UTC
    for i in range(n):
        c = float(closes[i])
        o = float(closes[i - 1]) if i else 2000.0
        rows.append(dict(time=t0 + pd.Timedelta(minutes=5 * i), open=o,
                         high=max(o, c) + 0.1, low=min(o, c) - 0.1,
                         close=c, volume=100.0))
    return pd.DataFrame(rows)


def test_s97_up_spike_fades_short_with_short_bias(monkeypatch):
    monkeypatch.setattr(s97, "_htf_bias", lambda w: "short")
    w5m = _m5_noise(last_two_increments=(1.5, 1.5))     # 2-bar move = +3.0
    sig = s97.get_signal(None, w5m, None, _utc(5))
    assert sig is not None and isinstance(sig, Signal)
    assert sig.side == "SELL"
    assert sig.max_hold_min == 30
    # TP = 0.5x overshoot, SL = 0.9x overshoot, both beyond the 1.0 floor.
    ov = 3.0
    assert sig.take_profit == pytest.approx(sig.entry_price - 0.5 * ov, abs=0.02)
    assert sig.stop_loss == pytest.approx(sig.entry_price + 0.9 * ov, abs=0.02)
    assert sig.stop_loss > sig.entry_price > sig.take_profit
    assert "S97" in sig.reason


def test_s97_down_spike_fades_long_with_long_bias(monkeypatch):
    monkeypatch.setattr(s97, "_htf_bias", lambda w: "long")
    w5m = _m5_noise(last_two_increments=(-1.5, -1.5))   # 2-bar move = -3.0
    sig = s97.get_signal(None, w5m, None, _utc(5))
    assert sig is not None
    assert sig.side == "BUY"
    assert sig.stop_loss < sig.entry_price < sig.take_profit
    ov = 3.0
    assert sig.take_profit == pytest.approx(sig.entry_price + 0.5 * ov, abs=0.02)
    assert sig.stop_loss == pytest.approx(sig.entry_price - 0.9 * ov, abs=0.02)


def test_s97_bias_gating(monkeypatch):
    w5m = _m5_noise(last_two_increments=(1.5, 1.5))     # up spike -> would SELL
    # Fade must agree with HTF bias: long or neutral bias blocks the short fade.
    monkeypatch.setattr(s97, "_htf_bias", lambda w: "long")
    assert s97.get_signal(None, w5m, None, _utc(5)) is None
    monkeypatch.setattr(s97, "_htf_bias", lambda w: "neutral")
    assert s97.get_signal(None, w5m, None, _utc(5)) is None


def test_s97_session_gating(monkeypatch):
    monkeypatch.setattr(s97, "_htf_bias", lambda w: "short")
    w5m = _m5_noise(last_two_increments=(1.5, 1.5))
    assert s97.get_signal(None, w5m, None, _utc(5)) is not None   # in session
    for hour in (0, 2, 9, 10, 14, 23):                            # outside 03-09
        assert s97.get_signal(None, w5m, None, _utc(hour)) is None


def test_s97_no_overshoot_no_trade(monkeypatch):
    monkeypatch.setattr(s97, "_htf_bias", lambda w: "short")
    w5m = _m5_noise()                                   # calm noise, no spike
    assert s97.get_signal(None, w5m, None, _utc(5)) is None


def test_s97_tp_sl_floor_one_point(monkeypatch):
    monkeypatch.setattr(s97, "_htf_bias", lambda w: "short")
    # Small overshoot (0.8 pt) on very quiet noise: still a valid snap, but
    # TP (0.4) and SL (0.72) distances get floored at 1.0 pt.
    w5m = _m5_noise(scale=0.05, last_two_increments=(0.4, 0.4))
    sig = s97.get_signal(None, w5m, None, _utc(5))
    assert sig is not None and sig.side == "SELL"
    assert (sig.stop_loss - sig.entry_price) == pytest.approx(1.0, abs=0.02)
    assert (sig.entry_price - sig.take_profit) == pytest.approx(1.0, abs=0.02)


def test_s97_htf_bias_neutral_on_flat_or_short_window():
    # Real (un-patched) bias helper: too little data or flat structure -> neutral.
    assert s97._htf_bias(None) == "neutral"
    assert s97._htf_bias(_ramp_15m(50, 2000.0, 0.1)) == "neutral"
    flat = _ramp_15m(1400, start=2000.0, step_per_bar=0.0)
    assert s97._htf_bias(flat) == "neutral"


def test_s97_insufficient_data(monkeypatch):
    monkeypatch.setattr(s97, "_htf_bias", lambda w: "short")
    assert s97.get_signal(None, None, None, _utc(5)) is None
    assert s97.get_signal(None, _m5_noise(n=20), None, _utc(5)) is None


def test_s97_config_contract():
    assert s97.NAME == "KRONOS_S97_SNAP_SCALPER"
    assert s97.NAME in _VARIATION_STRATEGY_NAME
    cfg = s97.CONFIG
    assert cfg.name == s97.NAME
    assert cfg.session_start_hour == 3 and cfg.session_end_hour == 9
    assert cfg.max_concurrent_positions == 1


# ══════════════════════════════════════════════════════════════════════════════
# Cross-cutting: every emitted signal carries a hard SL on the correct side
# ══════════════════════════════════════════════════════════════════════════════

def test_all_signals_have_hard_sl_and_sane_geometry(monkeypatch):
    s95.reset_state()
    monkeypatch.setattr(s97, "_htf_bias", lambda w: "short")
    w5m_orb, _, _ = _s95_orb_window(7)
    sigs = [
        s95.get_signal(None, w5m_orb, None, _utc(7, 35)),
        s96.get_signal(None, None, _ramp_15m(_N15, start=2000.0, step_per_bar=0.2), _utc(12)),
        s97.get_signal(None, _m5_noise(last_two_increments=(1.5, 1.5)), None, _utc(5)),
    ]
    for sig in sigs:
        assert sig is not None
        assert sig.stop_loss is not None and sig.stop_loss > 0
        if sig.side == "BUY":
            assert sig.stop_loss < sig.entry_price < sig.take_profit
        else:
            assert sig.take_profit < sig.entry_price < sig.stop_loss
