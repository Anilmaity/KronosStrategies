"""Per-strategy lookback windows in the offline sim.

Live sets the window depth PER STRATEGY in compose.yml (S94 RESEARCH_WIN_5M=1500,
S93/S99=160, S100 RESEARCH_WIN_1M=700), but manager_sim_engine used one global
_WIN_5M=300 for every strategy. S94 was therefore simulated with a fifth of the
level history it has live — a trade-selection divergence, not an execution one.
S94's own get_signal logs a warning about exactly this.
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

from backtest import manager_sim_engine as mse  # noqa: E402


def _frame(n: int, freq: str) -> pd.DataFrame:
    t0 = datetime(2026, 3, 1, tzinfo=timezone.utc)
    return pd.DataFrame({
        "time": pd.date_range(t0, periods=n, freq=freq, tz="UTC"),
        "open": [1.0] * n, "high": [1.0] * n,
        "low": [1.0] * n, "close": [1.0] * n, "volume": [1.0] * n,
    })


@pytest.fixture()
def frames():
    return {"1m": _frame(2000, "1min"),
            "5m": _frame(2000, "5min"),
            "15m": _frame(500, "15min")}


def _spec(**kw) -> mse.StratSpec:
    base = dict(name="X", module=object(), policy_key="always_on",
                policy_params={})
    base.update(kw)
    return mse.StratSpec(**base)


def test_stratspec_windows_default_to_none():
    """No override means 'use the engine default' — existing specs unchanged."""
    s = _spec()

    assert s.win_1m is None and s.win_5m is None and s.win_15m is None


def test_default_spec_gets_the_engine_default_windows(frames):
    cursors = {"1m": 2000, "5m": 2000, "15m": 500}

    w1m, w5m, w15m = mse.windows_for(_spec(), frames, cursors)

    assert len(w1m) == mse._WIN_1M
    assert len(w5m) == mse._WIN_5M
    assert len(w15m) == mse._WIN_15M


def test_per_strategy_override_deepens_the_5m_window(frames):
    """S94 lives on 1500 M5 bars; the sim must give it 1500, not 300."""
    cursors = {"1m": 2000, "5m": 2000, "15m": 500}

    _, w5m, _ = mse.windows_for(_spec(win_5m=1500), frames, cursors)

    assert len(w5m) == 1500


def test_override_is_clamped_by_available_history(frames):
    """Early in a replay there may be fewer bars than the window asks for."""
    cursors = {"1m": 2000, "5m": 120, "15m": 500}

    _, w5m, _ = mse.windows_for(_spec(win_5m=1500), frames, cursors)

    assert len(w5m) == 120


def test_windows_end_at_the_cursor_so_no_future_bars_leak(frames):
    """The window must be the CLOSED bars up to the cursor — never beyond."""
    cursors = {"1m": 100, "5m": 100, "15m": 100}

    w1m, w5m, w15m = mse.windows_for(_spec(win_1m=10, win_5m=10, win_15m=10),
                                     frames, cursors)

    for w, tf in ((w1m, "1m"), (w5m, "5m"), (w15m, "15m")):
        assert w["time"].iloc[-1] == frames[tf]["time"].iloc[99]
        assert len(w) == 10


def test_overrides_are_independent_per_timeframe(frames):
    cursors = {"1m": 2000, "5m": 2000, "15m": 500}

    w1m, w5m, w15m = mse.windows_for(_spec(win_5m=1500), frames, cursors)

    assert len(w5m) == 1500
    assert len(w1m) == mse._WIN_1M      # untouched
    assert len(w15m) == mse._WIN_15M    # untouched


def test_live_roster_windows_are_declared_from_compose():
    """The engine ships the live depths so a sim run cannot silently differ."""
    assert mse.LIVE_WINDOWS["KRONOS_S94_SWEEP_REVERSAL"]["win_5m"] == 1500
    assert mse.LIVE_WINDOWS["KRONOS_S93_FVG_SCALP"]["win_5m"] == 160
    assert mse.LIVE_WINDOWS["KRONOS_S99_MSS_FVG"]["win_5m"] == 160
    assert mse.LIVE_WINDOWS["KRONOS_S100_M3_COMBO"]["win_1m"] == 700


# ── entry-drift gate (only modelable with 5s data) ────────────────────────────

def _s5(closes: list[float]) -> pd.DataFrame:
    t0 = datetime(2026, 8, 12, 2, 24, tzinfo=timezone.utc)
    return pd.DataFrame({
        "time": [t0 + timedelta(seconds=5 * i) for i in range(len(closes))],
        "o": closes, "h": closes, "l": closes, "c": closes,
        "volume": [1.0] * len(closes),
    })


def test_ltp_at_order_time_picks_the_bar_at_the_latency_offset():
    bars = _s5([100.0, 101.0, 102.0, 103.0])

    assert mse.ltp_at_order_time(bars, 5.0, fallback=99.0) == 101.0
    assert mse.ltp_at_order_time(bars, 10.0, fallback=99.0) == 102.0


def test_ltp_at_order_time_clamps_to_the_last_available_bar():
    bars = _s5([100.0, 101.0])

    assert mse.ltp_at_order_time(bars, 60.0, fallback=99.0) == 101.0


def test_ltp_at_order_time_falls_back_without_5s_data():
    """No 5s data -> use the M1 close, so drift reads 0 and the gate fails OPEN,
    matching live's no-price behaviour."""
    assert mse.ltp_at_order_time(None, 5.0, fallback=4403.0) == 4403.0
    assert mse.ltp_at_order_time(_s5([]), 5.0, fallback=4403.0) == 4403.0


def test_entry_drift_modelling_is_off_by_default():
    cfg = mse.SimConfig(start=datetime(2026, 8, 12, tzinfo=timezone.utc),
                        end=datetime(2026, 8, 13, tzinfo=timezone.utc))

    assert cfg.model_entry_drift is False
    assert cfg.entry_latency_s == 5.0


# ── entry_time stamping (TIME-exit fidelity) ──────────────────────────────────

def test_entry_time_at_bar_close_is_off_by_default():
    cfg = mse.SimConfig(start=datetime(2026, 8, 12, tzinfo=timezone.utc),
                        end=datetime(2026, 8, 13, tzinfo=timezone.utc))

    assert cfg.entry_time_at_bar_close is False


def test_open_position_stamps_the_moment_it_is_given():
    """run_sim passes the FILL moment (bar close) when the flag is on; the engine
    filled at bar["close"] but stamped the bar's OPEN, so max_hold ran from a
    minute too early and every TIME exit fired ~60s premature (live 2026-08-12:
    sim exited 08:47:00 vs live 08:48:06)."""
    from backtest_strategies.base import Signal

    cfg = mse.SimConfig(start=datetime(2026, 8, 12, tzinfo=timezone.utc),
                        end=datetime(2026, 8, 13, tzinfo=timezone.utc))
    sig = Signal(side="BUY", entry_price=4404.85, stop_loss=4402.0,
                 take_profit=4410.0, reason="test")
    bar_open = datetime(2026, 8, 12, 7, 35, tzinfo=timezone.utc)
    fill_moment = bar_open + timedelta(minutes=1)

    pos = mse.open_position(sig, "S100", fill_moment, cfg, fill_price=4404.85)

    assert pos.entry_time == fill_moment
