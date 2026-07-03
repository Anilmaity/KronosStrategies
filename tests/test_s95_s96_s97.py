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
# S95 — session opening-range breakout
# ══════════════════════════════════════════════════════════════════════════════

def _s95_window(session_open_h: int, session_open_m: int,
                range_hi: float = 2004.0, range_lo: float = 2000.0,
                breakout_close: float | None = 2004.8) -> pd.DataFrame:
    """Build an M5 window: pre-session drift, a 3-bar opening range
    [range_lo, range_hi], four inside bars, then a final bar closing at
    `breakout_close` (or an inside close when None)."""
    mid = (range_hi + range_lo) / 2
    bars = []
    # Pre-session hour: flat inside the future range.
    for k in range(12):
        mm = k * 5
        hh = session_open_h - 1 + (session_open_m + mm) // 60
        m2 = (session_open_m + mm) % 60
        bars.append((hh, m2, mid, mid + 0.3, mid - 0.3, mid))
    # Opening range: 3 M5 bars spanning [range_lo, range_hi].
    for k in range(3):
        total = session_open_m + k * 5
        bars.append((session_open_h + total // 60, total % 60,
                     mid, range_hi, range_lo, mid + 0.5))
    # Inside bars after the range.
    for k in range(3, 7):
        total = session_open_m + k * 5
        bars.append((session_open_h + total // 60, total % 60,
                     mid, mid + 0.8, mid - 0.8, mid))
    # Final (breakout) bar.
    total = session_open_m + 35
    c = breakout_close if breakout_close is not None else mid
    o = mid
    hi = max(o, c) + 0.2
    lo = min(o, c) - 0.2
    bars.append((session_open_h + total // 60, total % 60, o, hi, lo, c))
    return _m5_frame(bars)


@pytest.fixture(autouse=True)
def _reset_s95_state():
    s95.reset_state()
    yield
    s95.reset_state()


def test_s95_london_breakout_long():
    w5m = _s95_window(7, 0, breakout_close=2004.8)
    sig = s95.get_signal(None, w5m, None, _utc(7, 40))
    assert sig is not None and isinstance(sig, Signal)
    assert sig.side == "BUY"
    # Structural SL just beyond the OPPOSITE (low) extreme.
    assert sig.stop_loss < 2000.0
    assert sig.stop_loss == pytest.approx(2000.0 - 0.10, abs=1e-6)
    # TP = 1.5R.
    risk = sig.entry_price - sig.stop_loss
    assert risk > 0
    assert sig.take_profit == pytest.approx(sig.entry_price + 1.5 * risk, abs=0.01)
    assert sig.max_hold_min == 240
    assert "S95" in sig.reason and "LONDON" in sig.reason


def test_s95_ny_breakout_short():
    w5m = _s95_window(13, 30, breakout_close=1999.2)
    sig = s95.get_signal(None, w5m, None, _utc(14, 10))
    assert sig is not None
    assert sig.side == "SELL"
    assert sig.stop_loss > 2004.0                       # beyond the high extreme
    assert sig.stop_loss == pytest.approx(2004.0 + 0.10, abs=1e-6)
    risk = sig.stop_loss - sig.entry_price
    assert sig.take_profit == pytest.approx(sig.entry_price - 1.5 * risk, abs=0.01)
    assert "NY" in sig.reason


def test_s95_one_trade_per_session_per_direction():
    w5m = _s95_window(7, 0, breakout_close=2004.8)
    now = _utc(7, 40)
    first = s95.get_signal(None, w5m, None, now)
    assert first is not None and first.side == "BUY"
    # Same session, same direction -> withheld.
    assert s95.get_signal(None, w5m, None, now) is None
    # Same session, OPPOSITE direction -> still allowed.
    w5m_dn = _s95_window(7, 0, breakout_close=1999.2)
    sig2 = s95.get_signal(None, w5m_dn, None, _utc(7, 45))
    assert sig2 is not None and sig2.side == "SELL"
    assert s95.get_signal(None, w5m_dn, None, _utc(7, 50)) is None


def test_s95_no_signal_without_breakout_close():
    w5m = _s95_window(7, 0, breakout_close=None)        # inside close
    assert s95.get_signal(None, w5m, None, _utc(7, 40)) is None


def test_s95_range_width_filter():
    # Too wide (> 12 pts).
    w5m = _s95_window(7, 0, range_hi=2015.0, range_lo=2000.0, breakout_close=2015.5)
    assert s95.get_signal(None, w5m, None, _utc(7, 40)) is None
    # Too narrow (< 2 pts).
    w5m = _s95_window(7, 0, range_hi=2001.0, range_lo=2000.0, breakout_close=2001.4)
    assert s95.get_signal(None, w5m, None, _utc(7, 40)) is None


def test_s95_risk_cap_skips_wide_structural_stop():
    # Range width 11 pts is allowed, but the structural risk (entry to the
    # opposite extreme) exceeds the 8-pt cap -> skip.
    w5m = _s95_window(7, 0, range_hi=2011.0, range_lo=2000.0, breakout_close=2011.5)
    assert s95.get_signal(None, w5m, None, _utc(7, 40)) is None


def test_s95_session_gating_defense_in_depth():
    w5m = _s95_window(7, 0, breakout_close=2004.8)
    # Outside any entry window (defense in depth inside get_signal).
    assert s95.get_signal(None, w5m, None, _utc(11, 0)) is None
    assert s95.get_signal(None, w5m, None, _utc(2, 0)) is None
    # Before the opening range completes.
    assert s95.get_signal(None, w5m, None, _utc(7, 10)) is None
    # After the 2h entry window closes (07:15 + 2h = 09:15).
    assert s95.get_signal(None, w5m, None, _utc(9, 20)) is None


def test_s95_insufficient_data():
    assert s95.get_signal(None, None, None, _utc(7, 40)) is None
    tiny = _m5_frame([(7, 0, 2000, 2001, 1999, 2000)])
    assert s95.get_signal(None, tiny, None, _utc(7, 40)) is None


def test_s95_config_contract():
    assert s95.NAME == "KRONOS_S95_SESSION_BREAKOUT"
    assert s95.NAME in _VARIATION_STRATEGY_NAME
    cfg = s95.CONFIG
    assert cfg.name == s95.NAME
    assert cfg.session_start_hour == 7 and cfg.session_end_hour == 16
    assert cfg.max_concurrent_positions == 1
    assert cfg.cooldown_s >= 300


# ══════════════════════════════════════════════════════════════════════════════
# S96 — pure M5 EMA9/21 crossover momentum
# ══════════════════════════════════════════════════════════════════════════════

def _m5_v_frame(direction: str, n_pre: int = 130, n_post: int = 60,
                step: float = 0.3) -> pd.DataFrame:
    """V-shaped M5 closes: `n_pre` bars trending against `direction`, then
    `n_post` bars trending with it. For direction='up' that's a downtrend
    followed by an uptrend, which forces EMA9 to cross above EMA21 somewhere
    after the turn (mirror for 'down')."""
    sign = 1.0 if direction == "up" else -1.0
    incs = [-sign * step] * n_pre + [sign * step] * n_post
    closes = 2000.0 + np.cumsum(incs)
    rows = []
    for k in range(len(closes)):
        c = float(closes[k])
        o = float(closes[k - 1]) if k else 2000.0
        rows.append(dict(time=_DAY + pd.Timedelta(minutes=5 * k), open=o,
                         high=max(o, c) + 0.2, low=min(o, c) - 0.2,
                         close=c, volume=100.0))
    return pd.DataFrame(rows)


def _m5_cross_window(direction: str) -> pd.DataFrame:
    """Truncate the V-frame at the FIRST bar where EMA9 crosses EMA21 in
    `direction` after the turn, so the cross event lands exactly on the
    last bar of the returned window."""
    df = _m5_v_frame(direction)
    c = df["close"].astype(float)
    ef = c.ewm(span=9, adjust=False, min_periods=9).mean()
    es = c.ewm(span=21, adjust=False, min_periods=21).mean()
    if direction == "up":
        crossed = (ef > es) & (ef.shift(1) <= es.shift(1))
    else:
        crossed = (ef < es) & (ef.shift(1) >= es.shift(1))
    crossed = crossed.fillna(False)
    turn = 130  # n_pre
    idx = [j for j in crossed[crossed].index if j > turn][0]
    out = df.iloc[: idx + 1].reset_index(drop=True)
    assert len(out) >= 120, "helper must clear the strategy warmup guard"
    return out


def test_s96_cross_up_emits_trailing_buy():
    w5m = _m5_cross_window("up")
    sig = s96.get_signal(None, w5m, None, _utc(12))
    assert sig is not None and isinstance(sig, Signal)
    assert sig.side == "BUY"
    assert sig.trailing is True
    assert sig.max_hold_min == 480
    assert sig.stop_loss < sig.entry_price              # hard SL below entry
    assert sig.take_profit > sig.entry_price            # far backstop above
    assert (sig.entry_price - sig.stop_loss) > 0        # 1.5xATR trail distance
    assert sig.reason == "S96_M5_CROSS_LONG"


def test_s96_cross_down_emits_trailing_sell():
    w5m = _m5_cross_window("down")
    sig = s96.get_signal(None, w5m, None, _utc(12))
    assert sig is not None
    assert sig.side == "SELL"
    assert sig.trailing is True
    assert sig.stop_loss > sig.entry_price              # SL above for a short
    assert sig.take_profit < sig.entry_price
    assert sig.reason == "S96_M5_CROSS_SHORT"


def test_s96_cross_is_an_event_not_a_state():
    # The FULL V-frame runs well past the cross: on its last bar EMA9 is
    # already above EMA21 on both bars (state, no event) -> no signal.
    w5m = _m5_v_frame("up")
    sig = s96.get_signal(None, w5m, None, _utc(12))
    assert sig is None


def test_s96_flat_market_no_signal():
    # Constant closes: EMAs identical, no strict cross either way.
    rows = []
    for k in range(150):
        rows.append(dict(time=_DAY + pd.Timedelta(minutes=5 * k), open=2000.0,
                         high=2000.2, low=1999.8, close=2000.0, volume=100.0))
    w5m = pd.DataFrame(rows)
    assert s96.get_signal(None, w5m, None, _utc(12)) is None


def test_s96_insufficient_history():
    short = _m5_v_frame("up").iloc[:100].reset_index(drop=True)  # < _MIN_M5
    assert s96.get_signal(None, short, None, _utc(12)) is None
    assert s96.get_signal(None, None, None, _utc(12)) is None


def test_s96_sl_distance_tracks_m5_atr():
    w5m = _m5_cross_window("up")
    sig = s96.get_signal(None, w5m, None, _utc(12))
    assert sig is not None
    # Each synthetic M5 bar has range ~ (0.3 + 2*0.2) = 0.7 -> ATR(14) ~ 0.7,
    # SL distance ~ 1.5 x 0.7 ~ 1.05. Ballpark-assert sane, not degenerate.
    dist = sig.entry_price - sig.stop_loss
    assert 0.3 < dist < 3.0
    # Far-TP backstop is ~30x ATR: at least 10x the SL distance.
    assert (sig.take_profit - sig.entry_price) > 10 * dist


def test_s96_config_contract():
    assert s96.NAME == "KRONOS_S96_H1_MOMENTUM"          # DB identity — unchanged
    assert s96.NAME in _VARIATION_STRATEGY_NAME
    cfg = s96.CONFIG
    assert cfg.name == s96.NAME
    assert cfg.cooldown_s == 300                         # one M5 bar backstop
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
    sigs = [
        s95.get_signal(None, _s95_window(7, 0, breakout_close=2004.8), None, _utc(7, 40)),
        s96.get_signal(None, _m5_cross_window("up"), None, _utc(12)),
        s97.get_signal(None, _m5_noise(last_two_increments=(1.5, 1.5)), None, _utc(5)),
    ]
    for sig in sigs:
        assert sig is not None
        assert sig.stop_loss is not None and sig.stop_loss > 0
        if sig.side == "BUY":
            assert sig.stop_loss < sig.entry_price < sig.take_profit
        else:
            assert sig.take_profit < sig.entry_price < sig.stop_loss
