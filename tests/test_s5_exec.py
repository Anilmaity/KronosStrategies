"""Phase 2 of the 5s-backtest-fidelity spec: sequence-faithful exit resolution.

The M1 engine checks SL before TP against one bar's high/low, so any minute that
touches both levels is booked a loss. These tests pin the 5s walk resolving that
order from the observed sequence instead.

Cycle 1 scope: ordering + ambiguity counting only, using the SAME mid-price
trigger predicates as manager_sim_engine.step_position, so a parity run can
attribute any improvement to ordering alone. Sided bid/ask fills come next.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies"))
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from backtest import s5_exec  # noqa: E402
from backtest import manager_sim_engine as mse  # noqa: E402
from backtest.manager_sim_engine import SimConfig, SimPosition  # noqa: E402

_T0 = datetime(2026, 7, 8, 10, 0, 0, tzinfo=timezone.utc)


def _cfg(**kw) -> SimConfig:
    base = dict(start=_T0, end=_T0 + timedelta(days=1),
                spread_pts=0.30, slippage_pts=0.10, lots=0.10)
    base.update(kw)
    return SimConfig(**base)


def _pos(side="BUY", entry=4400.0, sl=4397.5, tp=4405.0,
         max_hold_min=None, trailing=False, entry_time=None) -> SimPosition:
    return SimPosition(
        strategy="S100 M3 Combo Scalper",
        side=side,
        entry_time=entry_time or _T0,
        entry_px=entry,
        sl=sl,
        tp=tp,
        max_hold_min=max_hold_min,
        trailing=trailing,
        trail_dist=abs(entry - sl),
        hwm=entry,
    )


def _bars(rows: list[tuple[int, float, float, float, float]]) -> pd.DataFrame:
    """rows = [(second_offset, open, high, low, close), ...] with a 0.30 spread."""
    return pd.DataFrame({
        "time":  [_T0 + timedelta(seconds=s) for s, *_ in rows],
        "o":     [o for _, o, _, _, _ in rows],
        "h":     [h for _, _, h, _, _ in rows],
        "l":     [l for _, _, _, l, _ in rows],
        "c":     [c for _, _, _, _, c in rows],
        "bid_c": [c - 0.15 for _, _, _, _, c in rows],
        "ask_c": [c + 0.15 for _, _, _, _, c in rows],
        "volume": [5.0] * len(rows),
    })


# ── ordering: the whole point of 5s ───────────────────────────────────────────

def test_buy_takes_tp_when_tp_comes_first_in_the_sequence():
    """M1 would see low<=sl AND high>=tp in one bar and wrongly book SL."""
    bars = _bars([
        (0,  4400.0, 4405.5, 4399.8, 4405.2),   # TP touched here
        (5,  4405.0, 4405.2, 4397.0, 4397.2),   # SL touched later
    ])

    updated, rec = s5_exec.walk_exit(_pos(), bars, _cfg())

    assert updated is None
    assert rec.outcome == "TP"


def test_buy_takes_sl_when_sl_comes_first_in_the_sequence():
    bars = _bars([
        (0,  4400.0, 4400.2, 4397.0, 4397.2),   # SL first
        (5,  4397.5, 4405.5, 4397.4, 4405.2),   # TP later
    ])

    updated, rec = s5_exec.walk_exit(_pos(), bars, _cfg())

    assert updated is None
    assert rec.outcome == "SL"


def test_sell_takes_tp_when_tp_comes_first():
    pos = _pos(side="SELL", entry=4400.0, sl=4402.5, tp=4395.0)
    bars = _bars([
        (0,  4400.0, 4400.2, 4394.8, 4394.9),   # SELL TP is below
        (5,  4395.0, 4403.0, 4394.9, 4402.9),   # SL later
    ])

    updated, rec = s5_exec.walk_exit(pos, bars, _cfg())

    assert updated is None
    assert rec.outcome == "TP"


def test_position_stays_open_when_neither_level_is_touched():
    bars = _bars([
        (0,  4400.0, 4401.0, 4399.0, 4400.5),
        (5,  4400.5, 4401.5, 4399.5, 4401.0),
    ])

    updated, rec = s5_exec.walk_exit(_pos(), bars, _cfg())

    assert rec is None
    assert updated is not None
    assert updated.sl == pytest.approx(4397.5)


# ── residual ambiguity: honest floor on fidelity ──────────────────────────────

def test_ambiguous_bar_keeps_sl_first_and_is_counted():
    """One 5s bar touching both levels is still unresolved — count it, don't hide."""
    bars = _bars([
        (0,  4400.0, 4405.5, 4397.0, 4400.1),   # both levels inside ONE bar
    ])
    counter: dict[str, int] = {}

    updated, rec = s5_exec.walk_exit(_pos(), bars, _cfg(), ambiguity=counter)

    assert rec.outcome == "SL"                  # conservative
    assert counter.get("ambiguous_bars") == 1


def test_unambiguous_sequence_records_no_ambiguity():
    bars = _bars([
        (0,  4400.0, 4400.2, 4397.0, 4397.1),
    ])
    counter: dict[str, int] = {}

    s5_exec.walk_exit(_pos(), bars, _cfg(), ambiguity=counter)

    assert counter.get("ambiguous_bars", 0) == 0


# ── time exit at 5s granularity ───────────────────────────────────────────────

def test_time_exit_fires_on_the_5s_bar_that_crosses_max_hold():
    """Live checks TIME every second; M1 could only check once a minute."""
    pos = _pos(max_hold_min=0.25, entry_time=_T0)      # 15 s hold
    bars = _bars([
        (0,  4400.0, 4400.2, 4399.8, 4400.1),
        (5,  4400.1, 4400.3, 4399.9, 4400.2),
        (10, 4400.2, 4400.4, 4400.0, 4400.3),
        (15, 4400.3, 4400.5, 4400.1, 4400.4),          # 15 s elapsed -> exit here
        (20, 4400.4, 4400.6, 4400.2, 4400.5),
    ])

    updated, rec = s5_exec.walk_exit(pos, bars, _cfg())

    assert updated is None
    assert rec.outcome == "TIME"
    assert rec.exit_time == _T0 + timedelta(seconds=15)


def test_sl_beats_time_exit_when_it_happens_earlier_in_the_sequence():
    pos = _pos(max_hold_min=0.25, entry_time=_T0)
    bars = _bars([
        (0,  4400.0, 4400.2, 4397.0, 4397.1),          # stopped at t+0
        (15, 4400.3, 4400.5, 4400.1, 4400.4),
    ])

    updated, rec = s5_exec.walk_exit(pos, bars, _cfg())

    assert rec.outcome == "SL"


# ── trailing ratchet per 5s bar ───────────────────────────────────────────────

def test_trailing_stop_ratchets_per_5s_bar_not_per_minute():
    pos = _pos(trailing=True, entry=4400.0, sl=4397.5, tp=4405.0)
    bars = _bars([
        (0,  4400.0, 4402.0, 4399.9, 4401.9),   # hwm 4402.0 -> sl 4399.5
        (5,  4401.9, 4403.0, 4401.5, 4402.9),   # hwm 4403.0 -> sl 4400.5
    ])

    updated, rec = s5_exec.walk_exit(pos, bars, _cfg())

    assert rec is None
    assert updated.hwm == pytest.approx(4403.0)
    assert updated.sl == pytest.approx(4400.5)


def test_trailing_ratchet_does_not_stop_out_within_the_same_bar():
    """The ratchet from a bar's own high must only affect LATER bars."""
    pos = _pos(trailing=True, entry=4400.0, sl=4397.5)
    bars = _bars([
        (0,  4400.0, 4403.0, 4400.5, 4400.6),   # ratchets sl to 4400.5;
    ])                                          # its own low must not trigger it

    updated, rec = s5_exec.walk_exit(pos, bars, _cfg())

    assert rec is None
    assert updated.sl == pytest.approx(4400.5)


def test_trailing_exit_is_labelled_trail():
    pos = _pos(trailing=True, entry=4400.0, sl=4399.5)
    bars = _bars([
        (0,  4400.0, 4400.2, 4399.0, 4399.1),
    ])

    updated, rec = s5_exec.walk_exit(pos, bars, _cfg())

    assert rec.outcome == "TRAIL"


# ── dispatch: exec_resolution flag must be inert by default ───────────────────

def _m1_bar_touching_both_levels() -> pd.Series:
    """One M1 bar whose high reaches TP and whose low reaches SL.

    Note the column convention differs by timeframe: the M1 frames loaded by
    load_frames use open/high/low/close, while the S5 cache uses o/h/l/c.
    step_exit routes each to the function that speaks its dialect.
    """
    return pd.Series({"time": _T0, "open": 4400.0, "high": 4405.5,
                      "low": 4397.0, "close": 4400.1})


def test_exec_resolution_defaults_to_1m():
    assert _cfg().exec_resolution == "1m"


def test_1m_mode_ignores_5s_bars_even_when_they_change_the_answer():
    """The flag must be provably inert: same inputs, 5s data says TP first,
    1m mode must still book the SL it books today."""
    s5 = _bars([
        (0,  4400.0, 4405.5, 4399.8, 4405.2),   # TP first, unambiguously
        (5,  4405.0, 4405.2, 4397.0, 4397.2),
    ])

    _, rec = mse.step_exit(_pos(), _m1_bar_touching_both_levels(), _T0,
                           _cfg(exec_resolution="1m"), s5_slice=s5)

    assert rec.outcome == "SL"


def test_5s_mode_resolves_the_same_bar_from_the_sequence():
    s5 = _bars([
        (0,  4400.0, 4405.5, 4399.8, 4405.2),   # TP first
        (5,  4405.0, 4405.2, 4397.0, 4397.2),
    ])

    _, rec = mse.step_exit(_pos(), _m1_bar_touching_both_levels(), _T0,
                           _cfg(exec_resolution="5s"), s5_slice=s5)

    assert rec.outcome == "TP"


def test_5s_mode_falls_back_to_the_m1_bar_when_5s_data_is_missing():
    """A data gap must degrade to the M1 result, never crash or skip the exit."""
    _, rec = mse.step_exit(_pos(), _m1_bar_touching_both_levels(), _T0,
                           _cfg(exec_resolution="5s"), s5_slice=None)

    assert rec.outcome == "SL"


def test_5s_mode_falls_back_on_an_empty_slice():
    _, rec = mse.step_exit(_pos(), _m1_bar_touching_both_levels(), _T0,
                           _cfg(exec_resolution="5s"), s5_slice=_bars([]))

    assert rec.outcome == "SL"


def test_dispatch_threads_the_ambiguity_counter_through():
    s5 = _bars([(0, 4400.0, 4405.5, 4397.0, 4400.1)])   # both in one 5s bar
    counter: dict[str, int] = {}

    mse.step_exit(_pos(), _m1_bar_touching_both_levels(), _T0,
                  _cfg(exec_resolution="5s"), s5_slice=s5, ambiguity=counter)

    assert counter.get("ambiguous_bars") == 1


# ── per-minute slicing (feeds run_sim) ────────────────────────────────────────

def test_slice_for_minute_returns_exactly_that_minute():
    df = _bars([(s, 4400.0, 4400.2, 4399.8, 4400.1) for s in range(0, 120, 5)])

    out = s5_exec.slice_for_minute(df, df["time"], pd.Timestamp(_T0))

    assert len(out) == 12
    assert out["time"].iloc[0] == pd.Timestamp(_T0)
    assert out["time"].iloc[-1] == pd.Timestamp(_T0) + timedelta(seconds=55)


def test_slice_for_minute_excludes_the_next_minutes_first_bar():
    """A bar stamped exactly at now+60s belongs to the NEXT minute."""
    df = _bars([(0, 4400.0, 4400.2, 4399.8, 4400.1),
                (60, 4401.0, 4401.2, 4400.8, 4401.1)])

    out = s5_exec.slice_for_minute(df, df["time"], pd.Timestamp(_T0))

    assert len(out) == 1
    assert out["time"].iloc[0] == pd.Timestamp(_T0)


def test_slice_for_minute_returns_none_on_a_data_gap():
    df = _bars([(300, 4400.0, 4400.2, 4399.8, 4400.1)])   # 5 minutes later

    assert s5_exec.slice_for_minute(df, df["time"], pd.Timestamp(_T0)) is None


def test_slice_for_minute_returns_none_for_an_empty_frame():
    df = _bars([])

    assert s5_exec.slice_for_minute(df, df["time"], pd.Timestamp(_T0)) is None


# ── empty input ───────────────────────────────────────────────────────────────

def test_empty_bar_slice_leaves_the_position_untouched():
    pos = _pos()

    updated, rec = s5_exec.walk_exit(pos, _bars([]), _cfg())

    assert rec is None
    assert updated is pos
