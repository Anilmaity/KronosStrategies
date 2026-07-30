# tests/test_s93_soft_veto.py
"""opt15 Task 9 -- S93 SOFT M15 structure veto + 1.5xATR gap cap.

Both filters ship DEFAULT ON (the validated 2026-07-23 config: test n=388
PF 1.31 vs base 1.26; 0.80pt stress PF 1.20 vs 1.16) and are env-escapable
(S93_SOFT_VETO, S93_GAP_CAP_ATR). These tests prove:

  * the M15 swing-structure computation matches the validation harness
    (ClaudeTradingRD/m3_scalper/s93_struct_validate.m15_structure_on_m5) on
    synthetic +1/-1/0 fixtures -- swing lookback 3, 60-bar window, causal
    confirmation, closed-bar time mapping;
  * the SOFT veto blocks ONLY counter-structure entries (struct == -side) and
    lets aligned / ranging structure through;
  * the gap cap rejects an oversized (news-gap) FVG that would otherwise arm;
  * S93_SOFT_VETO=off (+ cap off) reproduces Task 8's golden legacy signals
    exactly -- the regression guard for the DEFAULT-ON change.

All synthetic; no DB, no network.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies"))
_LEGACY_DIR = os.path.join(os.path.dirname(__file__), "_legacy_ta")
for _p in (_STRAT_DIR, _LEGACY_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backtest_strategies import s93_fvg_scalp as s93  # noqa: E402
from backtest_strategies.base import Signal  # noqa: E402
import legacy_s93  # noqa: E402

_T0 = pd.Timestamp("2026-06-05T07:00:00Z")


@pytest.fixture(autouse=True)
def _reset():
    s93.reset_state()
    legacy_s93.reset_state()
    yield
    s93.reset_state()
    legacy_s93.reset_state()


# -- M5 displacement frames (bull / bear FVG) ----------------------------------
def _bull_frame(gap=0.7, hi=2004.0):
    """20 flat warmup bars then a bullish displacement FVG of `gap` points
    above the bar-two-back high (2000.5)."""
    rows = []

    def add(o, h, l, c):
        rows.append(dict(time=_T0 + pd.Timedelta(minutes=5 * len(rows)),
                         open=float(o), high=float(h), low=float(l),
                         close=float(c), volume=1.0))

    for _ in range(20):
        add(2000, 2000.5, 1999.5, 2000)
    add(2000, 2000.8, 1999.8, 2000.5)               # k-1
    add(2000.6, hi, 2000.5 + gap, hi - 0.2)         # displacement, FVG=gap
    return pd.DataFrame(rows)


def _bear_frame(gap=0.7):
    """Mirror of _bull_frame: a bearish displacement FVG of `gap` points below
    the bar-two-back low (1999.5)."""
    rows = []

    def add(o, h, l, c):
        rows.append(dict(time=_T0 + pd.Timedelta(minutes=5 * len(rows)),
                         open=float(o), high=float(h), low=float(l),
                         close=float(c), volume=1.0))

    for _ in range(20):
        add(2000, 2000.5, 1999.5, 2000)
    add(2000, 2000.2, 1999.2, 1999.8)               # k-1
    add(1999.4, 1999.5 - gap, 1996.0, 1996.2)       # displacement, FVG=gap
    return pd.DataFrame(rows)


def _now(minute_offset=0):
    return datetime(2026, 6, 5, 8, 45, tzinfo=timezone.utc) + pd.Timedelta(
        minutes=minute_offset)


# -- M15 structure fixtures (linear-interp zigzag, clean +/-3 pivots) ----------
# The decision M5 bar opens 08:45Z; the M15 series ends with close 08:30Z so the
# final confirmed pivot maps to the decision bar. Pivots sit in the last ~40
# bars (7-bar spacing) so the "last two swings within the 60-bar window" carry
# the intended structure. Verified equal to the harness for all three cases.
_M15_END = "2026-06-05 08:15:00+00:00"     # last bar OPEN (close 08:30)
_M15_N = 70

_BULL_PIV = [(31, 1990.0), (38, 2000.0), (45, 1995.0), (52, 2010.0),
             (59, 2000.0), (66, 2020.0)]                    # HH & HL -> +1
_BEAR_PIV = [(31, 2010.0), (38, 2000.0), (45, 2005.0), (52, 1990.0),
             (59, 2000.0), (66, 1980.0)]                    # LH & LL -> -1
_RANGE_PIV = [(31, 1990.0), (38, 2000.0), (45, 1995.0), (52, 2010.0),
              (59, 2000.0), (66, 2005.0)]                   # LH & HL -> 0


def _build_m15(pivots, n=_M15_N, end=_M15_END, tail_delta=-4.0):
    idxs = [p[0] for p in pivots]
    lvls = [p[1] for p in pivots]
    centers = np.empty(n, float)
    centers[:idxs[0] + 1] = np.linspace(lvls[0] + 3.0, lvls[0], idxs[0] + 1)
    for (i0, v0), (i1, v1) in zip(pivots[:-1], pivots[1:]):
        centers[i0:i1 + 1] = np.linspace(v0, v1, i1 - i0 + 1)
    last_i, last_v = pivots[-1]
    tail = n - 1 - last_i
    if tail > 0:
        centers[last_i:] = np.linspace(last_v, last_v + tail_delta, tail + 1)
    t = pd.date_range(end=end, periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({"time": t, "open": centers, "high": centers + 0.1,
                         "low": centers - 0.1, "close": centers, "volume": 1.0})


def _m15_bull():
    return _build_m15(_BULL_PIV, tail_delta=-4.0)


def _m15_bear():
    return _build_m15(_BEAR_PIV, tail_delta=+4.0)


def _m15_range():
    return _build_m15(_RANGE_PIV, tail_delta=-4.0)


# -- structure computation matches the validated harness -----------------------
def test_structure_fixtures_yield_known_values():
    dec = _T0 + pd.Timedelta(minutes=5 * 21)            # 08:45Z decision open
    assert s93._m15_structure_at(_m15_bull(), dec) == 1
    assert s93._m15_structure_at(_m15_bear(), dec) == -1
    assert s93._m15_structure_at(_m15_range(), dec) == 0


def test_structure_maps_by_closed_bar_time():
    # A decision bar that opens BEFORE any M15 bar has closed -> pos < 0 -> 0.
    early = pd.Timestamp("2026-06-04 14:00:00+00:00")
    assert s93._m15_structure_at(_m15_bull(), early) == 0


# -- SOFT veto: blocks ONLY counter-structure entries --------------------------
def test_veto_blocks_counter_structure_bull():
    # bearish M15 (struct -1) opposes a bull FVG (side +1) -> veto, no arm.
    s93.get_signal(None, _bull_frame(), _m15_bear(), _now())
    assert s93._pending is None


def test_veto_blocks_counter_structure_bear():
    # bullish M15 (struct +1) opposes a bear FVG (side -1) -> veto, no arm.
    s93.get_signal(None, _bear_frame(), _m15_bull(), _now())
    assert s93._pending is None


def test_veto_allows_aligned_bull():
    s93.get_signal(None, _bull_frame(), _m15_bull(), _now())
    assert s93._pending is not None and s93._pending["side"] == 1


def test_veto_allows_aligned_bear():
    s93.get_signal(None, _bear_frame(), _m15_bear(), _now())
    assert s93._pending is not None and s93._pending["side"] == -1


def test_ranging_structure_does_not_veto_bull():
    s93.get_signal(None, _bull_frame(), _m15_range(), _now())
    assert s93._pending is not None and s93._pending["side"] == 1


def test_ranging_structure_does_not_veto_bear():
    s93.get_signal(None, _bear_frame(), _m15_range(), _now())
    assert s93._pending is not None and s93._pending["side"] == -1


def test_vetoed_bull_does_not_fire_on_retrace():
    # Full pipeline: a counter-structure setup never arms, so a retrace probe
    # into the proximal edge cannot fire.
    f = _bull_frame()
    s93.get_signal(None, f, _m15_bear(), _now())        # arm attempt -> vetoed
    prox = 2000.5 + 0.7
    probe = pd.DataFrame([dict(time=_T0 + pd.Timedelta(minutes=21 * 5 + 6),
                               open=prox + 0.3, high=prox + 0.5, low=prox - 0.1,
                               close=prox + 0.4, volume=1.0)])
    assert s93.get_signal(probe, f, _m15_bear(), _now(6)) is None


# -- SOFT veto default ON, env-escapable ---------------------------------------
def test_veto_is_default_on():
    # No env set -> the counter-structure setup is vetoed.
    assert os.getenv("S93_SOFT_VETO") is None
    s93.get_signal(None, _bull_frame(), _m15_bear(), _now())
    assert s93._pending is None


def test_veto_off_env_unblocks_counter_structure(monkeypatch):
    monkeypatch.setenv("S93_SOFT_VETO", "off")
    s93.get_signal(None, _bull_frame(), _m15_bear(), _now())
    assert s93._pending is not None and s93._pending["side"] == 1


# -- veto fail-open on missing / shallow w15m ----------------------------------
def test_veto_fail_open_when_w15m_none():
    s93.get_signal(None, _bull_frame(), None, _now())
    assert s93._pending is not None and s93._pending["side"] == 1


def test_veto_fail_open_when_w15m_too_short():
    # Fewer than _MIN_M15 bars -> structure cannot be computed -> no veto.
    short = _m15_bear().iloc[-(s93._MIN_M15 - 1):].reset_index(drop=True)
    assert len(short) < s93._MIN_M15
    s93.get_signal(None, _bull_frame(), short, _now())
    assert s93._pending is not None and s93._pending["side"] == 1


# -- gap cap: rejects an oversized FVG that would otherwise arm/fire ------------
def test_gap_cap_rejects_oversized_fvg():
    # gap 10pt is ~5.7x ATR (>> 1.5x) -> rejected by the default cap.
    s93.get_signal(None, _bull_frame(gap=10.0, hi=2012.0), None, _now())
    assert s93._pending is None


def test_gap_cap_off_arms_and_fires_oversized_fvg(monkeypatch):
    # With the cap disabled the same oversized FVG arms and fires on retrace,
    # proving the cap (not some other gate) is what rejected it above.
    monkeypatch.setenv("S93_GAP_CAP_ATR", "0")
    f = _bull_frame(gap=10.0, hi=2012.0)
    s93.get_signal(None, f, None, _now())               # arm
    assert s93._pending is not None and s93._pending["side"] == 1
    prox = float(s93._pending["prox"])                  # 2010.5
    probe = pd.DataFrame([dict(time=_T0 + pd.Timedelta(minutes=21 * 5 + 6),
                               open=prox + 0.3, high=prox + 0.5, low=prox - 0.1,
                               close=prox + 0.4, volume=1.0)])
    sig = s93.get_signal(probe, f, None, _now(6))
    assert isinstance(sig, Signal) and sig.side == "BUY"


def test_gap_cap_is_default_on():
    assert os.getenv("S93_GAP_CAP_ATR") is None
    s93.get_signal(None, _bull_frame(gap=10.0, hi=2012.0), None, _now())
    assert s93._pending is None


def test_gap_cap_keeps_normal_fvg():
    # A normal 0.7pt gap (< 1.5x ATR) is NOT capped.
    s93.get_signal(None, _bull_frame(gap=0.7), None, _now())
    assert s93._pending is not None and s93._pending["side"] == 1


# -- MIN_BARS_15M contract (Task 7 pattern) ------------------------------------
def test_min_bars_15m_derived_from_structure_constants():
    assert s93.MIN_BARS_15M == s93._MIN_M15
    assert s93._MIN_M15 == s93._STRUCT_WINDOW + s93._STRUCT_LOOKBACK == 63


# -- regression guard: veto+cap OFF reproduces Task 8 golden signals -----------
def test_gates_off_reproduces_legacy_golden(monkeypatch):
    """S93_SOFT_VETO=off + cap off => new module == legacy oracle exactly, even
    when a counter-structure M15 frame is supplied (legacy ignores w15m)."""
    monkeypatch.setenv("S93_SOFT_VETO", "off")
    monkeypatch.setenv("S93_GAP_CAP_ATR", "0")
    f = _bull_frame()
    m15 = _m15_bear()                                   # would veto if ON
    prox = 2000.5 + 0.7
    probe = pd.DataFrame([dict(time=_T0 + pd.Timedelta(minutes=21 * 5 + 6),
                               open=prox + 0.3, high=prox + 0.5,
                               low=prox - 0.1,          # into (sl, prox] -> fill
                               close=prox + 0.4, volume=1.0)])

    def _norm(sig):
        if sig is None:
            return None
        return (sig.side, sig.entry_price, sig.stop_loss, sig.take_profit,
                sig.reason, sig.max_hold_min, sig.trailing)

    calls = [(None, f, m15, _now()), (probe, f, m15, _now(6))]

    legacy_s93.reset_state()
    legacy_out = [_norm(legacy_s93.get_signal(*c)) for c in calls]
    s93.reset_state()
    new_out = [_norm(s93.get_signal(*c)) for c in calls]

    assert new_out == legacy_out
    assert legacy_out[0] is None
    assert legacy_out[1] is not None and legacy_out[1][0] == "BUY"
