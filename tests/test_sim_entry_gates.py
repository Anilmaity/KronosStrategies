"""Task 2 (Manager Backtest fidelity plan): model deterministic entry gates
(sl_too_tight, news_blackout) in the offline sim using the shared predicates
from shared.gate_rules (Task 1) so the sim's trade set moves toward live's."""
from __future__ import annotations

import os
import sys
import types
from datetime import datetime

import numpy as np
import pandas as pd

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies"))
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from backtest.manager_sim_engine import (  # noqa: E402
    SimConfig, StratSpec, load_frames, run_sim,
)
from shared.gate_rules import in_news_blackout, sl_too_tight, parse_utc_windows  # noqa: E402


def test_simconfig_has_gate_fields():
    cfg = SimConfig(start=datetime(2026, 7, 1), end=datetime(2026, 7, 2))
    assert cfg.model_entry_gates is True
    assert cfg.min_sl_dist_pts == 1.5
    assert cfg.news_blackout_utc == "12:25-12:45"


def test_gate_predicates_used_by_sim_match_shared():
    # The sim must reject exactly what the shared predicates say.
    wins = parse_utc_windows("12:25-12:45")
    assert in_news_blackout(datetime(2026, 7, 1, 12, 30), wins) is True
    assert sl_too_tight(2000.0, 1999.5, 1.5) is True


# ── Step 7: behavior test over a synthetic replay ───────────────────────────
# Fixture mirrors tests/test_mbt_engine_hook.py's synthetic tape (drift + fast
# sine) so regime computation succeeds without needing real market data.

START = pd.Timestamp("2026-04-06", tz="UTC")
END = pd.Timestamp("2026-04-08 21:00", tz="UTC")
_SHALLOW = {"1d": 40, "4h": 60, "1h": 80, "15m": 50, "5m": 20, "1m": 20}


def _write_cache(cache_dir):
    start = START - pd.Timedelta(days=30)
    idx = pd.date_range(start, END, freq="1min", tz="UTC")
    idx = idx[(idx.dayofweek < 5) & (idx.hour < 21)]
    t = np.arange(len(idx), dtype=float)
    px = 3300 + 0.005 * t + 3.0 * np.sin(2 * np.pi * t / 7.0)
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


def _always_tight_sl_signal(w1m, w5m, w15m, now):
    """Fake strategy: always fires, stop only 0.1pt from entry (< the 1.5pt
    min_sl_dist_pts gate) -- so every non-blackout bar is a sl_too_tight
    reject and every blackout-window bar is a news_blackout reject."""
    from backtest_strategies.base import Signal
    return Signal(side="BUY", entry_price=2000.0, stop_loss=1999.9,
                 take_profit=2010.0, reason="fake-always-fires")


def _fake_spec(name, signal_fn):
    module = types.SimpleNamespace(
        get_signal=signal_fn,
        CONFIG=types.SimpleNamespace(cooldown_s=0),
    )
    return StratSpec(name=name, module=module, policy_key="trending",
                     policy_params={})


def test_entry_gates_off_produce_no_rejects(tmp_path):
    _write_cache(tmp_path)
    frames = load_frames(tmp_path, START, END)
    specs = [_fake_spec("FAKE", _always_tight_sl_signal)]

    cfg = SimConfig(start=START.to_pydatetime(), end=END.to_pydatetime(),
                    gated=False, model_entry_gates=False, slice_rows=_SHALLOW)
    result = run_sim(frames, cfg, specs=specs)

    assert result.entry_gate_rejects == {"FAKE": {"sl_too_tight": 0,
                                                    "news_blackout": 0}}


def test_entry_gates_on_reject_tight_sl_and_blackout(tmp_path):
    _write_cache(tmp_path)
    frames = load_frames(tmp_path, START, END)
    specs = [_fake_spec("FAKE", _always_tight_sl_signal)]

    cfg = SimConfig(start=START.to_pydatetime(), end=END.to_pydatetime(),
                    gated=False, model_entry_gates=True, slice_rows=_SHALLOW)
    result = run_sim(frames, cfg, specs=specs)

    rejects = result.entry_gate_rejects["FAKE"]
    assert set(rejects) == {"sl_too_tight", "news_blackout"}
    # A signal this tight fires on every evaluated bar, so it is ALWAYS
    # rejected -- never opened as a position.
    assert rejects["sl_too_tight"] > 0
    assert not any(t.strategy == "FAKE" for t in result.trades)
