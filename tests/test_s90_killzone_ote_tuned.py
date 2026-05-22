"""
test_s90_killzone_ote_tuned.py
------------------------------
TDD tests for the tuned Killzone OTE strategy (Task 7).

All tests are synthetic / offline — NO real data, NO disk I/O.

CONTRACT VERIFIED:
  - tuned_signals() is a strict subset of base generate_signals()
  - A signal that passes every confluence in the chosen stack survives
  - A signal that fails ANY confluence in the chosen stack is removed
  - When the stack is empty the output equals the base output
"""
from __future__ import annotations

import pandas as pd
import pytest

from strategies.research.exec_sim import Signal


# ---------------------------------------------------------------------------
# Helpers: build synthetic bar DataFrames
# ---------------------------------------------------------------------------

def _make_bars(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal bar DataFrame for testing."""
    data = []
    for r in rows:
        data.append({
            "time":   pd.Timestamp(r["time"]),
            "open":   float(r.get("open", 2600)),
            "high":   float(r.get("high", 2605)),
            "low":    float(r.get("low", 2595)),
            "close":  float(r.get("close", 2602)),
            "volume": float(r.get("volume", 100)),
            "spread": float(r.get("spread", 0.30)),
        })
    return pd.DataFrame(data)


def _london_5m_bars(base_time: str = "2026-01-13 08:05", n_bars: int = 3) -> pd.DataFrame:
    """Generate n synthetic 5M bars starting in London killzone."""
    t0 = pd.Timestamp(base_time)
    rows = []
    for i in range(n_bars):
        t = t0 + pd.Timedelta(minutes=5 * i)
        rows.append({
            "time": str(t),
            "open": 2600 + i,
            "high": 2605 + i,
            "low":  2595 + i,
            "close": 2602 + i,
            "volume": 150.0,
        })
    return _make_bars(rows)


def _make_htf_bars(n: int = 5, base_hour: int = 3) -> pd.DataFrame:
    """Generate n 1H bars for HTF context."""
    rows = []
    for i in range(n):
        t = pd.Timestamp(f"2026-01-13 {base_hour + i:02d}:00")
        rows.append({
            "time": str(t),
            "open": 2600 + i * 2,
            "high": 2605 + i * 2,
            "low":  2595 + i * 2,
            "close": 2603 + i * 2,
            "volume": 1000.0,
        })
    return _make_bars(rows)


# ---------------------------------------------------------------------------
# Import the module under test (after RED phase, these will exist)
# ---------------------------------------------------------------------------

def _import_tuned():
    from strategies.xauusd_strategies.s90_killzone_ote_tuned import tuned_signals
    return tuned_signals


# ---------------------------------------------------------------------------
# Test Suite
# ---------------------------------------------------------------------------

class TestTunedSignalsIsSubsetOfBase:
    """tuned_signals() must produce a subset of base generate_signals() output."""

    def test_tuned_is_importable(self):
        """The module must be importable and expose tuned_signals."""
        tuned_signals = _import_tuned()
        assert callable(tuned_signals)

    def test_empty_stack_returns_same_count_as_base(self):
        """With an empty confluence stack, tuned_signals == base signals in count.

        Build a minimal but valid scenario with a clear BUY signal:
        1H bars produce bullish bias + swing leg + FVG confluence.
        5M bar in London killzone tags OTE zone.
        """
        from strategies.xauusd_strategies.s90_killzone_ote import generate_signals

        bars_1h = _make_bars([
            {"time": "2026-01-13 04:00", "open": 2558, "high": 2570, "low": 2560, "close": 2562},
            {"time": "2026-01-13 05:00", "open": 2562, "high": 2568, "low": 2558, "close": 2565},
            {"time": "2026-01-13 06:00", "open": 2580, "high": 2640, "low": 2580, "close": 2635},
            {"time": "2026-01-13 07:00", "open": 2635, "high": 2648, "low": 2625, "close": 2645},
        ])
        bars_5m = _make_bars([
            {"time": "2026-01-13 08:05", "open": 2595, "high": 2597, "low": 2578, "close": 2582},
            {"time": "2026-01-13 08:10", "open": 2583, "high": 2640, "low": 2581, "close": 2638},
            {"time": "2026-01-13 08:15", "open": 2638, "high": 2650, "low": 2630, "close": 2648},
        ])

        tuned_signals = _import_tuned()

        base_sigs = generate_signals(bars_5m, bars_1h)
        tuned_sigs = tuned_signals(bars_5m, bars_1h, stack=[])

        # With empty stack, tuned == base
        assert len(tuned_sigs) == len(base_sigs)


class TestStackFiltersSignals:
    """The confluence stack must gate signals: signals failing any confluence are removed."""

    def _base_signals(self):
        """Return a synthetic list of two signals at different entry indices."""
        # Signals at entry_index 1 and 3 (decision bars 0 and 2)
        return [
            Signal(direction="BUY", entry_index=1, sl=2570.0, tp=2650.0),
            Signal(direction="BUY", entry_index=3, sl=2572.0, tp=2652.0),
        ]

    def test_signal_passing_all_confluences_survives(self):
        """A signal where all stack confluences are True must be kept."""
        from strategies.xauusd_strategies.s90_killzone_ote_tuned import (
            _apply_stack_filter,
        )

        # Build bars: decision bar for entry_index=1 is bar idx=0 (08:05 = London killzone)
        # session_killzone will be True for 08:05 UTC
        bars_5m = _make_bars([
            {"time": "2026-01-13 08:05", "open": 2600, "high": 2605, "low": 2595, "close": 2602,
             "volume": 200},  # high volume for volume_anomaly
            {"time": "2026-01-13 08:10", "open": 2602, "high": 2607, "low": 2599, "close": 2605,
             "volume": 50},
            {"time": "2026-01-13 08:15", "open": 2605, "high": 2610, "low": 2603, "close": 2608,
             "volume": 200},  # high volume
            {"time": "2026-01-13 08:20", "open": 2608, "high": 2612, "low": 2606, "close": 2610,
             "volume": 50},
        ])
        bars_1h = _make_htf_bars()

        signals = [Signal(direction="BUY", entry_index=1, sl=2570.0, tp=2650.0)]

        # "session_killzone" should be True for bar idx=0 at 08:05 UTC
        kept = _apply_stack_filter(signals, bars_5m, bars_1h, stack=["session_killzone"])
        assert len(kept) == 1

    def test_signal_failing_confluence_is_removed(self):
        """A signal where any stack confluence is False must be removed.

        Bar at 12:00 UTC is NOT in a killzone window.
        So session_killzone will be False for that bar.
        """
        from strategies.xauusd_strategies.s90_killzone_ote_tuned import (
            _apply_stack_filter,
        )

        # Decision bar idx=0 at 12:00 (between killzones → outside any window)
        bars_5m = _make_bars([
            {"time": "2026-01-13 12:00", "open": 2600, "high": 2605, "low": 2595, "close": 2602},
            {"time": "2026-01-13 12:05", "open": 2602, "high": 2607, "low": 2599, "close": 2605},
        ])
        bars_1h = _make_htf_bars()

        signals = [Signal(direction="BUY", entry_index=1, sl=2570.0, tp=2650.0)]

        # session_killzone is False at 12:00 → signal should be filtered out
        kept = _apply_stack_filter(signals, bars_5m, bars_1h, stack=["session_killzone"])
        assert len(kept) == 0

    def test_tuned_is_always_subset_of_base(self):
        """For any non-empty stack, tuned_signals output is a subset of base signals.

        We verify that every signal in tuned_sigs appears in base_sigs.
        """
        from strategies.xauusd_strategies.s90_killzone_ote import generate_signals
        from strategies.xauusd_strategies.s90_killzone_ote_tuned import tuned_signals

        bars_1h = _make_bars([
            {"time": "2026-01-13 04:00", "open": 2558, "high": 2570, "low": 2560, "close": 2562},
            {"time": "2026-01-13 05:00", "open": 2562, "high": 2568, "low": 2558, "close": 2565},
            {"time": "2026-01-13 06:00", "open": 2580, "high": 2640, "low": 2580, "close": 2635},
            {"time": "2026-01-13 07:00", "open": 2635, "high": 2648, "low": 2625, "close": 2645},
        ])
        bars_5m = _make_bars([
            {"time": "2026-01-13 08:05", "open": 2595, "high": 2597, "low": 2578, "close": 2582},
            {"time": "2026-01-13 08:10", "open": 2583, "high": 2640, "low": 2581, "close": 2638},
            {"time": "2026-01-13 08:15", "open": 2638, "high": 2650, "low": 2630, "close": 2648},
        ])

        base = generate_signals(bars_5m, bars_1h)
        base_keys = {(s.direction, s.entry_index, s.sl, s.tp) for s in base}

        # Try with session_killzone as a single-filter stack
        tuned = tuned_signals(bars_5m, bars_1h, stack=["session_killzone"])
        for sig in tuned:
            key = (sig.direction, sig.entry_index, sig.sl, sig.tp)
            assert key in base_keys, (
                f"Tuned signal {sig} not found in base signals — "
                "tuned must be a subset of base."
            )

    def test_stack_with_all_confluences_same_or_fewer_signals(self):
        """Applying all SEARCHABLE confluences gives same or fewer signals than base."""
        from strategies.research.confluence import SEARCHABLE
        from strategies.xauusd_strategies.s90_killzone_ote import generate_signals
        from strategies.xauusd_strategies.s90_killzone_ote_tuned import tuned_signals

        bars_1h = _make_bars([
            {"time": "2026-01-13 04:00", "open": 2558, "high": 2570, "low": 2560, "close": 2562},
            {"time": "2026-01-13 05:00", "open": 2562, "high": 2568, "low": 2558, "close": 2565},
            {"time": "2026-01-13 06:00", "open": 2580, "high": 2640, "low": 2580, "close": 2635},
            {"time": "2026-01-13 07:00", "open": 2635, "high": 2648, "low": 2625, "close": 2645},
        ])
        bars_5m = _make_bars([
            {"time": "2026-01-13 08:05", "open": 2595, "high": 2597, "low": 2578, "close": 2582,
             "volume": 50},
            {"time": "2026-01-13 08:10", "open": 2583, "high": 2640, "low": 2581, "close": 2638,
             "volume": 50},
            {"time": "2026-01-13 08:15", "open": 2638, "high": 2650, "low": 2630, "close": 2648,
             "volume": 50},
        ])

        base_count = len(generate_signals(bars_5m, bars_1h))
        tuned_count = len(tuned_signals(bars_5m, bars_1h, stack=SEARCHABLE))
        assert tuned_count <= base_count


class TestChosenStackKnownBehavior:
    """Tests for the CHOSEN_STACK property exposed by the module."""

    def test_chosen_stack_is_list_of_strings(self):
        """CHOSEN_STACK must be a list of string confluence names."""
        from strategies.xauusd_strategies import s90_killzone_ote_tuned as mod
        assert isinstance(mod.CHOSEN_STACK, list)
        assert all(isinstance(s, str) for s in mod.CHOSEN_STACK)

    def test_chosen_stack_names_are_valid_detectors(self):
        """Every name in CHOSEN_STACK must be in confluence.SEARCHABLE."""
        from strategies.research.confluence import SEARCHABLE
        from strategies.xauusd_strategies import s90_killzone_ote_tuned as mod
        for name in mod.CHOSEN_STACK:
            assert name in SEARCHABLE, (
                f"'{name}' in CHOSEN_STACK is not a valid SEARCHABLE confluence"
            )

    def test_default_stack_is_chosen_stack(self):
        """Calling tuned_signals() without explicit stack= uses CHOSEN_STACK."""
        from strategies.xauusd_strategies import s90_killzone_ote_tuned as mod

        bars_1h = _make_bars([
            {"time": "2026-01-13 04:00", "open": 2558, "high": 2570, "low": 2560, "close": 2562},
            {"time": "2026-01-13 05:00", "open": 2562, "high": 2568, "low": 2558, "close": 2565},
            {"time": "2026-01-13 06:00", "open": 2580, "high": 2640, "low": 2580, "close": 2635},
            {"time": "2026-01-13 07:00", "open": 2635, "high": 2648, "low": 2625, "close": 2645},
        ])
        bars_5m = _make_bars([
            {"time": "2026-01-13 08:05", "open": 2595, "high": 2597, "low": 2578, "close": 2582},
            {"time": "2026-01-13 08:10", "open": 2583, "high": 2640, "low": 2581, "close": 2638},
            {"time": "2026-01-13 08:15", "open": 2638, "high": 2650, "low": 2630, "close": 2648},
        ])

        # Explicit stack call should match default call
        explicit = mod.tuned_signals(bars_5m, bars_1h, stack=mod.CHOSEN_STACK)
        default_ = mod.tuned_signals(bars_5m, bars_1h)
        assert len(explicit) == len(default_)


class TestWilsonLowerBound:
    """Test the wilson_lb helper function."""

    def test_wilson_lb_high_winrate(self):
        """At 95% winrate with n=20, wilson LB > 0.70."""
        from strategies.xauusd_strategies.s90_killzone_ote_tuned import wilson_lb
        lb = wilson_lb(w=19, n=20)
        assert lb > 0.70

    def test_wilson_lb_zero_trades(self):
        """n=0 should return 0.0 (no trades)."""
        from strategies.xauusd_strategies.s90_killzone_ote_tuned import wilson_lb
        lb = wilson_lb(w=0, n=0)
        assert lb == 0.0

    def test_wilson_lb_all_wins(self):
        """100% winrate with large n: wilson LB approaches 1.0."""
        from strategies.xauusd_strategies.s90_killzone_ote_tuned import wilson_lb
        lb = wilson_lb(w=100, n=100)
        assert lb > 0.94  # with n=100 and 100% WR, LB is very tight

    def test_wilson_lb_monotone_with_n(self):
        """Same winrate, larger n → tighter (higher) lower bound."""
        from strategies.xauusd_strategies.s90_killzone_ote_tuned import wilson_lb
        lb_small = wilson_lb(w=9,  n=10)
        lb_large = wilson_lb(w=90, n=100)
        assert lb_large > lb_small  # more data → more confidence
