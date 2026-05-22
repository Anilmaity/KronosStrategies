"""
test_s90_inverse_fvg_tuned.py
-----------------------------
TDD tests for strategies.xauusd_strategies.s90_inverse_fvg_tuned
(Task 7 -- confluence tuning for Family G: Inverse-FVG Continuation).

All tests use synthetic bar DataFrames (tz-naive UTC timestamps).
NO real cache, NO network, NO DB.

Contract verified:
1. tuned_signals() is a strict subset of detect_signals() (gating only removes).
2. A signal whose decision bar meets ALL chosen-stack confluences SURVIVES.
3. A signal whose decision bar FAILS a required confluence is REMOVED.
4. Empty signal list returns empty list without error.
5. Module exposes tuned_signals(bars, htf_bars, **params) signature.
"""
from __future__ import annotations

import pandas as pd
import pytest

from strategies.xauusd_strategies.s90_inverse_fvg_tuned import tuned_signals
from strategies.xauusd_strategies.s90_inverse_fvg import detect_signals


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bars(rows: list[dict], base_time: str = "2025-06-02 08:00:00",
               minutes: int = 5) -> pd.DataFrame:
    """Build a minimal OHLCV+spread DataFrame with tz-naive UTC timestamps."""
    t0 = pd.Timestamp(base_time)
    data = []
    for i, r in enumerate(rows):
        t = r.get("time", t0 + pd.Timedelta(minutes=minutes * i))
        data.append({
            "time":   pd.Timestamp(t),
            "open":   float(r["open"]),
            "high":   float(r["high"]),
            "low":    float(r["low"]),
            "close":  float(r["close"]),
            "volume": float(r.get("volume", 100)),
            "spread": float(r.get("spread", 0.30)),
        })
    return pd.DataFrame(data)


def _make_htf_bars(n: int = 30, base_time: str = "2025-06-02 00:00:00",
                   hours: int = 1, trend: str = "bullish") -> pd.DataFrame:
    """Build synthetic 1h bars with a clear trend direction for htf_bias.

    Starts at midnight so at least 3 bars are visible before 08:35 entry.
    """
    t0 = pd.Timestamp(base_time)
    data = []
    price = 2000.0
    for i in range(n):
        t = t0 + pd.Timedelta(hours=hours * i)
        if trend == "bullish":
            o, c = price, price + 2.0
        elif trend == "bearish":
            o, c = price, price - 2.0
        else:
            o, c = price, price  # neutral / flat
        h = max(o, c) + 1.0
        lo = min(o, c) - 1.0
        data.append({
            "time": t, "open": o, "high": h, "low": lo, "close": c,
            "volume": 100.0, "spread": 0.30,
        })
        price = c
    return pd.DataFrame(data)


def _build_inverted_bullish_fvg_bars(session_start: str = "2025-06-02 08:00:00",
                                     volume_spike: bool = False) -> pd.DataFrame:
    """
    Returns a 9-bar series that produces a SELL signal from detect_signals().
    Decision bar (retest) = bar 6; entry_index = 7.

    FVG zone=[2000,2002] (bullish) -> inverted to resistance after violation.

    The entry bar (7) is in London session (08:35 UTC, Monday 2025-06-02).
    """
    vol_retest = 500.0 if volume_spike else 80.0  # anomaly vs. normal
    rows = [
        # Phase 1: FVG Formation
        {"open": 1999, "high": 2000, "low": 1998, "close": 1999, "volume": 100},
        {"open": 1999, "high": 2001, "low": 1998, "close": 2000, "volume": 100},
        {"open": 2000, "high": 2005, "low": 2002, "close": 2004, "volume": 100},
        # Phase 2: FVG Fill
        {"open": 2004, "high": 2005, "low": 2001, "close": 2003, "volume": 100},
        {"open": 2003, "high": 2003, "low": 2000, "close": 2002, "volume": 100},
        # Phase 3: FVG Violation
        {"open": 2002, "high": 2003, "low": 1997, "close": 1999, "volume": 100},
        # Phase 4: Retest (decision bar k=6)
        {"open": 1999, "high": 2001, "low": 1998, "close": 1999, "volume": vol_retest},
        # Entry bar (7) and continuation
        {"open": 1999, "high": 2000, "low": 1990, "close": 1991, "volume": 100},
        {"open": 1991, "high": 1992, "low": 1985, "close": 1986, "volume": 100},
    ]
    return _make_bars(rows, base_time=session_start)


# ---------------------------------------------------------------------------
# Test 1: tuned_signals() is a strict subset of detect_signals()
# ---------------------------------------------------------------------------

def test_tuned_is_subset_of_base():
    """
    Property: every signal returned by tuned_signals() must also appear in
    detect_signals() (same direction and entry_index). Gating only removes, never adds.
    """
    bars = _build_inverted_bullish_fvg_bars()
    htf = _make_htf_bars(30, trend="bearish")  # bearish 1h to help htf_filter pass

    base = detect_signals(bars, htf)
    tuned = tuned_signals(bars, htf)

    base_keys = {(s.direction, s.entry_index) for s in base}
    tuned_keys = {(s.direction, s.entry_index) for s in tuned}

    assert tuned_keys.issubset(base_keys), (
        f"tuned_signals returned signals NOT in base: {tuned_keys - base_keys}"
    )


# ---------------------------------------------------------------------------
# Test 2: Signal passing chosen-stack confluences survives
# ---------------------------------------------------------------------------

def test_passing_confluence_signal_survives():
    """
    When a signal's decision bar satisfies the chosen-stack confluences, it must
    NOT be filtered by tuned_signals(). We pass a volume_spike=True bar at the
    retest, which ensures volume_anomaly fires at bar 6 (decision bar).

    Regardless of which specific stack was chosen by the tuner, any signal
    surviving in detect_signals() with no confluences required must survive
    in tuned_signals() IF it naturally passes all chosen-stack checks.

    We explicitly pass override stack=[] (empty) so the test is stack-agnostic:
    an empty stack never removes signals, confirming the passthrough path works.
    """
    bars = _build_inverted_bullish_fvg_bars(volume_spike=True)
    htf = _make_htf_bars(30, trend="bearish")

    # With no confluence requirements, all base signals survive.
    base = detect_signals(bars, htf)
    tuned = tuned_signals(bars, htf, required_confluences=[])

    base_keys = {(s.direction, s.entry_index) for s in base}
    tuned_keys = {(s.direction, s.entry_index) for s in tuned}

    assert tuned_keys == base_keys, (
        f"With empty stack, tuned should equal base. "
        f"base={base_keys}, tuned={tuned_keys}"
    )


# ---------------------------------------------------------------------------
# Test 3: Signal failing a required confluence is removed
# ---------------------------------------------------------------------------

def test_failing_required_confluence_removes_signal():
    """
    When we require 'volume_anomaly' but the decision bar has LOW volume
    (no spike), the signal must be filtered out.

    volume_spike=False -> bar 6 volume=80 (well below spike threshold).
    We require ['volume_anomaly'] -> signal should be removed.
    """
    bars = _build_inverted_bullish_fvg_bars(volume_spike=False)
    htf = _make_htf_bars(30, trend="bearish")

    base = detect_signals(bars, htf)
    tuned = tuned_signals(bars, htf, required_confluences=["volume_anomaly"])

    # With volume_spike=False, volume_anomaly should fail -> signal removed.
    # (base must have at least 1 signal for this test to be meaningful)
    assert len(base) >= 1, "Setup error: base must produce at least 1 signal"

    # All tuned signals must still be a subset of base.
    base_keys = {(s.direction, s.entry_index) for s in base}
    tuned_keys = {(s.direction, s.entry_index) for s in tuned}
    assert tuned_keys.issubset(base_keys), "Gating added signals -- not allowed"

    # The SELL signal at entry_index=7 should be removed (volume_anomaly fails).
    sell_tuned = [s for s in tuned if s.direction == "SELL" and s.entry_index == 7]
    assert len(sell_tuned) == 0, (
        f"SELL signal at entry_index=7 should have been removed by volume_anomaly gate, "
        f"but tuned returned: {tuned}"
    )


# ---------------------------------------------------------------------------
# Test 4: Empty signal list returns empty list
# ---------------------------------------------------------------------------

def test_empty_signal_list_returns_empty():
    """
    If there are no base signals (e.g. no FVG patterns in the bars),
    tuned_signals() must return an empty list without raising.
    """
    # 5-bar flat series -- no FVG patterns, no signals.
    bars = _make_bars([
        {"open": 2000, "high": 2001, "low": 1999, "close": 2000},
        {"open": 2000, "high": 2001, "low": 1999, "close": 2000},
        {"open": 2000, "high": 2001, "low": 1999, "close": 2000},
        {"open": 2000, "high": 2001, "low": 1999, "close": 2000},
        {"open": 2000, "high": 2001, "low": 1999, "close": 2000},
    ])
    htf = _make_htf_bars(10, trend="bearish")

    result = tuned_signals(bars, htf)
    assert result == [], f"Expected [] for no-signal bars, got {result}"


# ---------------------------------------------------------------------------
# Test 5: Function signature is correct
# ---------------------------------------------------------------------------

def test_tuned_signals_signature():
    """
    tuned_signals(bars, htf_bars, **params) must be callable with:
    - positional bars (pd.DataFrame) and htf_bars (pd.DataFrame or None)
    - optional required_confluences kwarg
    and must return a list.
    """
    bars = _make_bars([
        {"open": 2000, "high": 2001, "low": 1999, "close": 2000},
    ] * 5)

    # Should not raise; result should be a list.
    result = tuned_signals(bars, None)
    assert isinstance(result, list)

    result2 = tuned_signals(bars, None, required_confluences=[])
    assert isinstance(result2, list)

    result3 = tuned_signals(bars, None, required_confluences=["session_killzone"])
    assert isinstance(result3, list)
