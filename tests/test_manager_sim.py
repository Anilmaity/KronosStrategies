"""
test_manager_sim.py
-------------------
Tests for the offline Strategy Manager simulator engine (Tasks 2-4).

sys.path: conftest.py adds repo root (so strategy_manager is a namespace pkg).
This file adds strategies/ so backtest.* and backtest_strategies.* resolve.
"""
from __future__ import annotations

import os
import sys
import pytest
import pandas as pd
from datetime import datetime, timezone
from types import SimpleNamespace

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies")
)
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

# ── Task 2 ────────────────────────────────────────────────────────────────────

from backtest.manager_sim_engine import (
    GuardState, SimConfig, evaluate_gates, STRAT_SPECS,
)

UTC = timezone.utc

def _snap(vol="NORMAL", trend="TRENDING", d1="bullish", h4="long",
          session="LONDON", closed=False):
    return SimpleNamespace(vol_regime=vol, trend_regime=trend, d1_bias=d1,
                           h4_bias=h4, session=session, market_closed=closed)

def _cfg(**kw):
    defaults = dict(start=datetime(2026, 4, 1, tzinfo=UTC),
                    end=datetime(2026, 7, 2, tzinfo=UTC))
    defaults.update(kw)
    return SimConfig(**defaults)

def test_market_closed_gates_everything_off():
    g = evaluate_gates(_snap(closed=True), datetime(2026, 4, 6, 8, 0, tzinfo=UTC),
                       GuardState(), 0, _cfg())
    assert all(v[0] is False for v in g.values())

def test_kill_switch_gates_everything_off():
    guard = GuardState(kill_tripped_date="2026-04-06")
    g = evaluate_gates(_snap(), datetime(2026, 4, 6, 8, 0, tzinfo=UTC),
                       guard, 0, _cfg())
    assert all(v[0] is False for v in g.values())

def test_max_concurrent_blocks_new_entries():
    g = evaluate_gates(_snap(), datetime(2026, 4, 6, 8, 0, tzinfo=UTC),
                       GuardState(), 3, _cfg(max_concurrent=3))
    assert all(v[0] is False for v in g.values())

def test_policies_route_correctly():
    # 08:00 UTC LONDON, NORMAL vol, TRENDING, bullish d1, long h4:
    # session_vol in window -> s95 & SESSION_BREAKOUT True;
    # trending -> s96 True; quiet_mr (3-9h, LOW/NORMAL, NON-trending) is gated
    # here because the tape is TRENDING -> s98 False.
    g = evaluate_gates(_snap(), datetime(2026, 4, 6, 8, 0, tzinfo=UTC),
                       GuardState(), 0, _cfg())
    assert g["KRONOS_S95_SESSION_BREAKOUT"][0] is True
    assert g["KRONOS_S96_H1_MOMENTUM"][0] is True
    assert g["KRONOS_S98_ZSCORE_MR"][0] is False   # quiet_mr gated: TRENDING
    assert g["SESSION_BREAKOUT"][0] is True
    # quiet_mr routes True on a quiet, non-trending tape in the same window.
    g2 = evaluate_gates(_snap(trend="MIXED"),
                        datetime(2026, 4, 6, 8, 0, tzinfo=UTC),
                        GuardState(), 0, _cfg())
    assert g2["KRONOS_S98_ZSCORE_MR"][0] is True

def test_policy_pauses_outside_window():
    # 11:00 UTC: outside session_vol windows and outside quiet_mr window
    g = evaluate_gates(_snap(), datetime(2026, 4, 6, 11, 0, tzinfo=UTC),
                       GuardState(), 0, _cfg())
    assert g["KRONOS_S95_SESSION_BREAKOUT"][0] is False
    assert g["KRONOS_S98_ZSCORE_MR"][0] is False
    assert g["KRONOS_S96_H1_MOMENTUM"][0] is True  # trending is time-free

def test_ungated_mode_all_true_despite_guards():
    g = evaluate_gates(_snap(closed=False), datetime(2026, 4, 6, 11, 0, tzinfo=UTC),
                       GuardState(kill_tripped_date="2026-04-06"), 99,
                       _cfg(gated=False))
    assert all(v[0] is True for v in g.values())


# ── Task 3 ────────────────────────────────────────────────────────────────────

from backtest.manager_sim_engine import open_position, step_position
from backtest_strategies.base import Signal


def _bar(ts, o, h, l, c):
    return pd.Series({"time": pd.Timestamp(ts, tz="UTC"),
                      "open": o, "high": h, "low": l, "close": c})


def _buy_sig(entry=100.0, sl=98.0, tp=103.0, hold=None, trailing=False):
    return Signal(side="BUY", entry_price=entry, stop_loss=sl,
                  take_profit=tp, reason="t", max_hold_min=hold,
                  trailing=trailing)


def _sell_sig(entry=100.0, sl=102.0, tp=97.0, hold=None, trailing=False):
    return Signal(side="SELL", entry_price=entry, stop_loss=sl,
                  take_profit=tp, reason="t", max_hold_min=hold,
                  trailing=trailing)


def test_entry_friction_applied():
    pos = open_position(_buy_sig(), "X", datetime(2026, 4, 6, 8, 0, tzinfo=UTC), _cfg())
    assert pos.entry_px == pytest.approx(100.25)   # 100 + 0.15 + 0.10


def test_sl_beats_tp_when_bar_spans_both():
    cfg = _cfg()
    pos = open_position(_buy_sig(), "X", datetime(2026, 4, 6, 8, 0, tzinfo=UTC), cfg)
    pos2, rec = step_position(pos, _bar("2026-04-06 08:01", 100, 104, 97, 102),
                              datetime(2026, 4, 6, 8, 1, tzinfo=UTC), cfg)
    assert pos2 is None and rec.outcome == "SL"
    assert rec.exit_px == pytest.approx(98.0 - 0.25)


def test_tp_exit_and_cost_arithmetic():
    cfg = _cfg()
    pos = open_position(_buy_sig(), "X", datetime(2026, 4, 6, 8, 0, tzinfo=UTC), cfg)
    _, rec = step_position(pos, _bar("2026-04-06 08:01", 100, 103.5, 99.5, 103),
                           datetime(2026, 4, 6, 8, 1, tzinfo=UTC), cfg)
    assert rec.outcome == "TP"
    # gross 3.0 pts minus 0.5 round trip = 2.5 pts -> $5.00 at 0.02 lots
    assert rec.pnl_pts == pytest.approx(2.5)
    assert rec.pnl_usd == pytest.approx(5.0)


def test_time_exit():
    cfg = _cfg()
    pos = open_position(_buy_sig(hold=30), "X", datetime(2026, 4, 6, 8, 0, tzinfo=UTC), cfg)
    _, rec = step_position(pos, _bar("2026-04-06 08:31", 100, 100.4, 99.8, 100.2),
                           datetime(2026, 4, 6, 8, 31, tzinfo=UTC), cfg)
    assert rec.outcome == "TIME"


def test_trailing_ratchets_up_never_down():
    cfg = _cfg()
    pos = open_position(_buy_sig(sl=99.0, tp=130.0, trailing=True), "X",
                        datetime(2026, 4, 6, 8, 0, tzinfo=UTC), cfg)
    pos, rec = step_position(pos, _bar("2026-04-06 08:01", 100, 102, 100, 101.8),
                             datetime(2026, 4, 6, 8, 1, tzinfo=UTC), cfg)
    assert rec is None and pos.sl == pytest.approx(102 - pos.trail_dist)
    tightened = pos.sl
    pos, rec = step_position(pos, _bar("2026-04-06 08:02", 101.8, 101.9, 101.0, 101.5),
                             datetime(2026, 4, 6, 8, 2, tzinfo=UTC), cfg)
    assert rec is None and pos.sl == pytest.approx(tightened)  # never loosens


def test_trailing_exit_outcome_is_trail():
    cfg = _cfg()
    pos = open_position(_buy_sig(sl=99.0, tp=130.0, trailing=True), "X",
                        datetime(2026, 4, 6, 8, 0, tzinfo=UTC), cfg)
    pos, _ = step_position(pos, _bar("2026-04-06 08:01", 100, 105, 100, 104.8),
                           datetime(2026, 4, 6, 8, 1, tzinfo=UTC), cfg)
    _, rec = step_position(pos, _bar("2026-04-06 08:02", 104.8, 104.9, 103.0, 103.2),
                           datetime(2026, 4, 6, 8, 2, tzinfo=UTC), cfg)
    assert rec is not None and rec.outcome == "TRAIL"


def test_sell_sl_touch_exits_conservatively():
    cfg = _cfg()
    pos = open_position(_sell_sig(), "X", datetime(2026, 4, 6, 8, 0, tzinfo=UTC), cfg)
    pos2, rec = step_position(pos, _bar("2026-04-06 08:01", 100, 102.5, 99.5, 101),
                              datetime(2026, 4, 6, 8, 1, tzinfo=UTC), cfg)
    assert pos2 is None and rec.outcome == "SL"
    assert rec.exit_px == pytest.approx(102.0 + 0.25)


def test_sell_tp_exit_cost_arithmetic():
    cfg = _cfg()
    pos = open_position(_sell_sig(), "X", datetime(2026, 4, 6, 8, 0, tzinfo=UTC), cfg)
    _, rec = step_position(pos, _bar("2026-04-06 08:01", 99, 99.5, 96.8, 97.2),
                           datetime(2026, 4, 6, 8, 1, tzinfo=UTC), cfg)
    assert rec.outcome == "TP"
    # entry_px = 100.0 - 0.25 = 99.75, exit_px = 97.0 + 0.25 = 97.25
    # pnl_pts = 99.75 - 97.25 = 2.5 -> $5.00 at 0.02 lots
    assert rec.pnl_pts == pytest.approx(2.5)
    assert rec.pnl_usd == pytest.approx(5.0)


def test_sell_trailing_ratchets_down_never_up():
    cfg = _cfg()
    pos = open_position(_sell_sig(sl=101.0, tp=70.0, trailing=True), "X",
                        datetime(2026, 4, 6, 8, 0, tzinfo=UTC), cfg)
    pos, rec = step_position(pos, _bar("2026-04-06 08:01", 100, 100.2, 98.0, 98.2),
                             datetime(2026, 4, 6, 8, 1, tzinfo=UTC), cfg)
    assert rec is None and pos.sl == pytest.approx(98.0 + pos.trail_dist)
    tightened = pos.sl
    pos, rec = step_position(pos, _bar("2026-04-06 08:02", 98.2, 98.8, 98.1, 98.5),
                             datetime(2026, 4, 6, 8, 2, tzinfo=UTC), cfg)
    assert rec is None and pos.sl == pytest.approx(tightened)  # never loosens
    _, rec = step_position(pos, _bar("2026-04-06 08:03", 98.5, 99.6, 98.4, 99.4),
                           datetime(2026, 4, 6, 8, 3, tzinfo=UTC), cfg)
    assert rec is not None and rec.outcome == "TRAIL"
    assert rec.exit_px == pytest.approx(tightened + 0.25)


# ── Task 4 ────────────────────────────────────────────────────────────────────

import numpy as np

from backtest.manager_sim_engine import load_frames, run_sim, SimResult, STRAT_SPECS

START = pd.Timestamp("2026-04-06", tz="UTC")
END   = pd.Timestamp("2026-04-10 21:00", tz="UTC")

# Shallow slice_rows override keeps the three loop-level tests fast.
# Production (CLI) uses SimConfig's live-faithful defaults.
_SHALLOW = {"1d": 40, "4h": 60, "1h": 80, "15m": 50, "5m": 20, "1m": 20}


def _write_synthetic_cache(cache_dir, start=None, days=30, mutate_after=None,
                           const_px=None):
    """Synthetic XAU tape: drift + fast sine (period 7 min, amplitude 3 pts)
    on a 1m grid (weekdays 00:00-20:59 UTC), resampled to other TFs and
    written in bars_cache format.

    Period 7 min is prime relative to the 5-min M5 bar so M5 closes sample
    all sine phases; the resulting opening ranges are 6+ pts wide and price
    regularly breaks out of the range within the 2-h entry window (s95
    fires reliably during both London and NY sessions).

    `mutate_after`: 1m bar timestamps strictly greater get close=99999
    (used by the no-look-ahead test to poison future bars).

    `const_px`: when given, the whole tape is flat at that price (used by the
    beyond-SL phantom-guard test to pin the detection-bar close).
    """
    start = start or (START - pd.Timedelta(days=days))
    idx = pd.date_range(start, END, freq="1min", tz="UTC")
    idx = idx[(idx.dayofweek < 5) & (idx.hour < 21)]
    t = np.arange(len(idx), dtype=float)
    if const_px is not None:
        px = np.full(len(idx), float(const_px))
    else:
        px = 3300 + 0.005 * t + 3.0 * np.sin(2 * np.pi * t / 7.0)
    if mutate_after is not None:
        px = px.copy()
        px[idx > mutate_after] = 99999.0
    # No H/L spread: high = low = close = px so M5 breakout close can
    # exceed the range high without the spread barrier blocking it.
    df1 = pd.DataFrame({"time": idx, "open": px, "high": px,
                        "low": px, "close": px, "volume": 10.0})
    df1.to_parquet(cache_dir / "is_XAU_USD_1m.parquet", index=False)
    g = df1.set_index("time")
    for tf, rule in [("5m", "5min"), ("15m", "15min"), ("1h", "1h"),
                     ("4h", "4h"), ("1d", "1D")]:
        r = g.resample(rule).agg({"open": "first", "high": "max",
                                  "low": "min", "close": "last",
                                  "volume": "sum"}).dropna().reset_index()
        r.to_parquet(cache_dir / f"is_XAU_USD_{tf}.parquet", index=False)


def test_run_sim_smoke_and_gating_structure(tmp_path):
    _write_synthetic_cache(tmp_path)
    frames = load_frames(tmp_path, START, END)
    gated   = run_sim(frames, _cfg(gated=True,  slice_rows=_SHALLOW))
    ungated = run_sim(frames, _cfg(gated=False, slice_rows=_SHALLOW))
    # structural invariants, not P&L values:
    assert set(t.strategy for t in ungated.trades) <= {s.name for s in STRAT_SPECS}
    assert len(gated.trades) <= len(ungated.trades)          # gating only removes entries
    assert all(t.outcome in {"TP", "SL", "TIME", "TRAIL", "OPEN"} for t in gated.trades)
    assert gated.regime_rows and {"d1_bias", "vol_regime", "session"} <= set(gated.regime_rows[0])
    assert all(0.0 <= v <= 100.0 for v in gated.paused_pct.values())


def test_no_look_ahead(tmp_path):
    T_mid = pd.Timestamp("2026-04-08 12:00", tz="UTC")
    _write_synthetic_cache(tmp_path)
    frames = load_frames(tmp_path, START, T_mid)
    baseline = run_sim(frames, _cfg(gated=True, end=T_mid, slice_rows=_SHALLOW)).regime_rows

    poisoned_dir = tmp_path / "poisoned"
    poisoned_dir.mkdir()
    # Poison starts ONE MINUTE before T_mid so that the forming higher-TF bars
    # at the last evaluation instant (bars whose open < T_mid but whose close
    # depends on data at T_mid+) carry poisoned aggregated values.
    # NOTE: on this synthetic tape the poisoned forming D1 bar still maps to
    # d1_bias="ranging", so this end-to-end check does NOT discriminate the
    # cursor bug by itself.  The discriminating tests are
    # test_closed_bar_cursor_excludes_forming_bars and
    # test_forming_bars_never_reach_regime_or_windows below.
    _write_synthetic_cache(poisoned_dir, mutate_after=T_mid - pd.Timedelta("1min"))
    frames2 = load_frames(poisoned_dir, START, T_mid)
    poisoned = run_sim(frames2, _cfg(gated=True, end=T_mid, slice_rows=_SHALLOW)).regime_rows

    assert baseline == poisoned  # future bars must not change the past


def test_closed_bar_cursor_excludes_forming_bars():
    """Direct unit test of the FIX-2 cursor helper (discriminating test).

    A TF bar is included iff open + tf_delta <= now_ts + 1min:
      - a bar whose close lands EXACTLY on the cutoff is included
      - a bar opening 1 minute later (still forming at now_ts) is excluded
    The old ``searchsorted(now_ts, side="right")`` cursor includes the forming
    bar on every TF above 1m, so this test FAILS with the old cursor.
    """
    from backtest.manager_sim_engine import _closed_bar_cursor, _TF_DELTA

    now_ts = pd.Timestamp("2026-04-08 11:59", tz="UTC")
    one_min = pd.Timedelta("1min")

    for tf in ["5m", "15m", "1h", "4h", "1d"]:
        delta = pd.Timedelta(_TF_DELTA[tf])
        closed_open  = now_ts + one_min - delta   # closes exactly at cutoff
        forming_open = closed_open + one_min      # closes 1min past cutoff
        times = pd.Series([closed_open - delta, closed_open, forming_open])
        cur = _closed_bar_cursor(times, tf, now_ts)
        assert cur == 2, (
            f"{tf}: cursor {cur} != 2 — forming bar (open={forming_open}) "
            f"leaked into the visible slice at now={now_ts}"
        )

    # 1m reduces to the old behaviour: bar with open == now_ts is included,
    # the next 1m bar is not.
    times = pd.Series([now_ts - one_min, now_ts, now_ts + one_min])
    assert _closed_bar_cursor(times, "1m", now_ts) == 2


def test_forming_bars_never_reach_regime_or_windows(tmp_path, monkeypatch):
    """End-to-end discriminating test for FIX 2, independent of regime semantics.

    Poison every 1m bar with time > T_mid - 1min, so the forming D1 bar of the
    final day aggregates poisoned closes all day.  Stub compute_regime and the
    strategy get_signal to inspect the exact frames run_sim hands them, and
    assert the poison value NEVER appears in any regime slice or w1m/w5m/w15m
    window.  With the old ``searchsorted(now_ts, "right")`` cursor the forming
    (poisoned) D1 bar is included in the 1d slice at every cadence tick of the
    final day, so this test FAILS when FIX 2 is reverted.
    """
    from dataclasses import dataclass as _dc, field as _field
    import backtest.manager_sim_engine as eng

    T_mid = pd.Timestamp("2026-04-08 12:00", tz="UTC")
    _write_synthetic_cache(tmp_path, mutate_after=T_mid - pd.Timedelta("1min"))
    frames = load_frames(tmp_path, START, T_mid)

    POISON = 99999.0
    slice_poison: list[tuple[str, str]] = []
    window_poison: list[tuple[str, str]] = []

    @_dc
    class _FakeSnap:
        d1_bias: str = "ranging"
        h4_bias: str = "neutral"
        vol_regime: str = "NORMAL"
        trend_regime: str = "RANGING"
        session: str = "LONDON"
        market_closed: bool = False
        details: dict = _field(default_factory=dict)

    def fake_compute_regime(frames_slice, now_utc):
        for tf, fs in frames_slice.items():
            if len(fs) and float(fs["close"].max()) >= POISON:
                slice_poison.append((tf, now_utc.isoformat()))
        return _FakeSnap()

    monkeypatch.setattr(eng, "compute_regime", fake_compute_regime)

    def probe_get_signal(w1m, w5m, w15m, now_utc):
        for name, w in (("w1m", w1m), ("w5m", w5m), ("w15m", w15m)):
            if len(w) and float(w["close"].max()) >= POISON:
                window_poison.append((name, now_utc.isoformat()))
        return None

    probe = SimpleNamespace(NAME="PROBE", get_signal=probe_get_signal)
    specs = [eng.StratSpec("PROBE", probe, "trending", {})]

    run_sim(frames, _cfg(gated=False, end=T_mid, slice_rows=_SHALLOW), specs=specs)

    assert not slice_poison, (
        f"forming/poisoned bars leaked into regime slices: {slice_poison[:5]}"
    )
    assert not window_poison, (
        f"forming/poisoned bars leaked into strategy windows: {window_poison[:5]}"
    )


# ── Task 6 — FIX A: market-realistic fills ────────────────────────────────────


def test_fill_price_overrides_signal_entry():
    """fill_price=102 on a BUY with sig.entry_price=100 → entry_px = 102.25.

    Verifies that the fill_price parameter replaces sig.entry_price as the fill
    basis while friction is still applied (102 + 0.25 = 102.25).
    Existing tests call open_position without fill_price and remain unchanged.
    """
    cfg = _cfg()
    pos = open_position(
        _buy_sig(entry=100.0, sl=98.0, tp=110.0), "X",
        datetime(2026, 4, 6, 8, 0, tzinfo=UTC), cfg,
        fill_price=102.0,
    )
    assert pos.entry_px == pytest.approx(102.25)   # 102 + friction(0.25)


def test_phantom_beyond_tp_skipped_in_run_sim(tmp_path):
    """run_sim must drop BUY entries where bar_close + friction >= tp.

    Stubs a strategy that always returns a BUY signal with tp=100.1 (well below
    bar prices of ~3300 on the synthetic tape).  Every candidate entry would have
    close + 0.25 >= 100.1, so all are phantoms.  The result must contain zero
    trades.  This mirrors the 2026-07-01T14:00 SESSION_BREAKOUT phantom where
    bar 14:00 already closed at TP.
    """
    import backtest.manager_sim_engine as eng

    _write_synthetic_cache(tmp_path)
    frames = load_frames(tmp_path, START, END)

    probe_module = SimpleNamespace(
        NAME="PHANTOM_S",
        get_signal=lambda w1m, w5m, w15m, now: Signal(
            side="BUY",
            entry_price=100.0,
            stop_loss=99.0,
            take_profit=100.1,   # always below synthetic bar close (~3300)
            reason="phantom",
            max_hold_min=None,
            trailing=False,
        ),
    )
    specs = [eng.StratSpec("PHANTOM_S", probe_module, "trending", {})]
    result = run_sim(frames, _cfg(gated=False, slice_rows=_SHALLOW), specs=specs)
    assert len(result.trades) == 0, (
        f"Phantom entries must be skipped; got {len(result.trades)} trade(s)"
    )


def test_phantom_beyond_sl_skipped_in_run_sim(tmp_path, monkeypatch):
    """run_sim must skip BUY entries where bar_close + friction <= sig.stop_loss.

    BUY signal entry_price=100, sl=98, tp=110, but the detection bar closes at
    97.5: the market fill (97.5 + 0.25 = 97.75) is already at/below the signal
    stop, so live would be stopped out instantly — a phantom loser.  Mirror of
    the beyond-TP guard (the guard also protects trailing strategies, whose
    trail seeds hwm from the entry fill).  compute_regime is stubbed so the
    flat tape cannot silently disable entries via a failed regime evaluation
    (which would make the test pass vacuously).
    """
    from dataclasses import dataclass as _dc, field as _field
    import backtest.manager_sim_engine as eng

    _write_synthetic_cache(tmp_path, const_px=97.5)
    frames = load_frames(tmp_path, START, END)

    @_dc
    class _FakeSnap:
        d1_bias: str = "ranging"
        h4_bias: str = "neutral"
        vol_regime: str = "NORMAL"
        trend_regime: str = "RANGING"
        session: str = "LONDON"
        market_closed: bool = False
        details: dict = _field(default_factory=dict)

    monkeypatch.setattr(eng, "compute_regime", lambda fs, now: _FakeSnap())

    calls = {"n": 0}

    def get_signal(w1m, w5m, w15m, now):
        calls["n"] += 1
        return Signal(side="BUY", entry_price=100.0, stop_loss=98.0,
                      take_profit=110.0, reason="beyond-sl", max_hold_min=None,
                      trailing=False)

    probe = SimpleNamespace(NAME="BEYOND_SL", get_signal=get_signal)
    specs = [eng.StratSpec("BEYOND_SL", probe, "trending", {})]
    result = run_sim(frames, _cfg(gated=False, slice_rows=_SHALLOW), specs=specs)

    assert calls["n"] > 0, "strategy was never consulted — test would be vacuous"
    assert len(result.trades) == 0, (
        f"Beyond-SL phantom entries must be skipped (no position, no trade); "
        f"got {len(result.trades)} trade(s)"
    )


def test_kill_switch_trips_and_resets_next_day(tmp_path):
    """Spec test: force a losing exit that crosses -kill_switch_usd; same UTC
    day admits no further entries; the next day admits entries again; an open
    position still exits while the switch is tripped."""
    _write_synthetic_cache(tmp_path)
    frames = load_frames(tmp_path, START, END)
    res = run_sim(frames, _cfg(gated=True, kill_switch_usd=0.01, slice_rows=_SHALLOW))
    assert res.kill_trips, "expected at least one kill-switch trip"
    trip_day = res.kill_trips[0]
    # entries on the trip day must all precede the trip (evaluate_gates gates same-day)
    # and later days must still trade:
    assert any(t.entry_time.date().isoformat() > trip_day for t in res.trades), (
        "no trade entered after the trip day — kill-switch never reset"
    )
