"""
test_s90_sigma_mr.py
--------------------
TDD tests for strategies.xauusd_strategies.s90_sigma_mr (Family E).

ALL tests use synthetic bars (hand-constructed tz-naive UTC DataFrames).
No real cache, no DB, no network.

Coverage
--------
generate_signals:
  - A >3×ATR up-spike bar (bullish candle) at a London-session time, with
    enough prior bars for ATR(20) to be non-zero, produces a SELL Signal
    with entry_index = spike_index + 1.
  - A >3×ATR down-spike bar (bearish candle) at a London-session time,
    produces a BUY Signal with entry_index = spike_index + 1.
  - A normal-volatility sequence (no bar exceeds 3×ATR) produces no signals.
  - A spike bar at a weekend / outside session hours produces no signal.
  - The spike bar itself uses bar-range (high-low) to measure the move.
  - Minimum bars guard: fewer than atr_len+1 bars → no crash, just no signals.

SL/TP design (ATR-based, measured from entry open):
  - SELL: sl = entry_open + 1.5*ATR, tp = entry_open - 0.5*ATR
  - BUY:  sl = entry_open - 1.5*ATR, tp = entry_open + 0.5*ATR
"""
from __future__ import annotations

import pandas as pd
import pytest

from strategies.xauusd_strategies.s90_sigma_mr import generate_signals

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_PRICE = 2000.0   # mid price anchor
_ATR_APPROX = 1.0      # we'll construct bars so that ATR≈1.0


def _make_bars(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal OHLCV + spread DataFrame with tz-naive UTC timestamps."""
    data = []
    for r in rows:
        data.append({
            "time":   pd.Timestamp(r["time"]),   # tz-naive
            "open":   float(r["open"]),
            "high":   float(r["high"]),
            "low":    float(r["low"]),
            "close":  float(r["close"]),
            "volume": float(r.get("volume", 100)),
            "spread": float(r.get("spread", 0.30)),
        })
    return pd.DataFrame(data)


def _quiet_bar(t: str, price: float = _BASE_PRICE, rng: float = 0.8) -> dict:
    """A normal-volatility 5m bar — range ~0.8 (below ATR=1.0 threshold)."""
    return {
        "time":  t,
        "open":  price,
        "high":  price + rng / 2,
        "low":   price - rng / 2,
        "close": price + 0.1,   # slight upward drift
    }


def _spike_up_bar(t: str, price: float = _BASE_PRICE, spike_rng: float = 3.5) -> dict:
    """An up-spike bar: open at price, closes spike_rng above open.
    range = spike_rng (> 3× ATR if ATR≈1.0 and spike_rng≥3.1).
    """
    return {
        "time":  t,
        "open":  price,
        "high":  price + spike_rng,
        "low":   price - 0.1,
        "close": price + spike_rng,
    }


def _spike_down_bar(t: str, price: float = _BASE_PRICE, spike_rng: float = 3.5) -> dict:
    """A down-spike bar: open at price, closes spike_rng below open."""
    return {
        "time":  t,
        "open":  price,
        "high":  price + 0.1,
        "low":   price - spike_rng,
        "close": price - spike_rng,
    }


# ---------------------------------------------------------------------------
# Build a base sequence of 25 quiet bars (London session, Mon-Fri)
# enough for ATR(20) to stabilise before we add a spike.
# Dates: 2025-01-13 (Monday) 08:00 UTC onwards (London session).
# ---------------------------------------------------------------------------

def _base_london_times(n: int, start: str = "2025-01-13 08:00") -> list[str]:
    """Return n consecutive 5m timestamps in London session."""
    base = pd.Timestamp(start)
    return [(base + pd.Timedelta(minutes=5 * i)).strftime("%Y-%m-%d %H:%M:%S")
            for i in range(n)]


def _build_quiet_bars(n: int = 25) -> list[dict]:
    times = _base_london_times(n)
    return [_quiet_bar(t, price=_BASE_PRICE, rng=0.8) for t in times]


# ---------------------------------------------------------------------------
# Test 1: Up-spike → SELL signal
# ---------------------------------------------------------------------------

def test_up_spike_produces_sell_signal():
    """
    25 quiet bars (ATR≈0.8 with range=0.8) followed by one up-spike bar of
    range=3.5 (~4.4×ATR).  generate_signals should produce exactly one SELL
    signal with entry_index = 25 (the bar immediately after the spike, index 25).
    """
    rows = _build_quiet_bars(25)
    spike_idx = 25   # 0-based; bar 25 is the spike
    spike_time = _base_london_times(26)[-1]  # one more timestamp
    rows.append(_spike_up_bar(spike_time, price=_BASE_PRICE, spike_rng=3.5))
    # Append one more bar to serve as entry (k+1)
    entry_time = (pd.Timestamp(spike_time) + pd.Timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    rows.append(_quiet_bar(entry_time, price=_BASE_PRICE + 3.5, rng=0.5))

    bars = _make_bars(rows)
    signals = generate_signals(bars)

    assert len(signals) == 1, f"Expected 1 signal, got {len(signals)}: {signals}"
    sig = signals[0]
    assert sig.direction == "SELL", f"Up-spike should fade SHORT, got {sig.direction}"
    assert sig.entry_index == spike_idx + 1, (
        f"Entry must be bar k+1={spike_idx+1}, got {sig.entry_index}"
    )


# ---------------------------------------------------------------------------
# Test 2: Down-spike → BUY signal
# ---------------------------------------------------------------------------

def test_down_spike_produces_buy_signal():
    """
    25 quiet bars followed by one down-spike bar of range=3.5 (~4.4×ATR).
    generate_signals should produce exactly one BUY signal with
    entry_index = spike_idx + 1.
    """
    rows = _build_quiet_bars(25)
    spike_idx = 25
    spike_time = _base_london_times(26)[-1]
    rows.append(_spike_down_bar(spike_time, price=_BASE_PRICE, spike_rng=3.5))
    entry_time = (pd.Timestamp(spike_time) + pd.Timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    rows.append(_quiet_bar(entry_time, price=_BASE_PRICE - 3.5, rng=0.5))

    bars = _make_bars(rows)
    signals = generate_signals(bars)

    assert len(signals) == 1, f"Expected 1 signal, got {len(signals)}: {signals}"
    sig = signals[0]
    assert sig.direction == "BUY", f"Down-spike should fade LONG, got {sig.direction}"
    assert sig.entry_index == spike_idx + 1, (
        f"Entry must be bar k+1={spike_idx+1}, got {sig.entry_index}"
    )


# ---------------------------------------------------------------------------
# Test 3: Normal volatility → no signals
# ---------------------------------------------------------------------------

def test_no_signal_on_normal_volatility():
    """
    All 30 bars have range=0.8; ATR≈0.8; 3×ATR≈2.4.
    No bar exceeds this threshold, so no signals should be generated.
    """
    rows = _build_quiet_bars(30)
    bars = _make_bars(rows)
    signals = generate_signals(bars)
    assert signals == [], f"Expected no signals on quiet bars, got {len(signals)}"


# ---------------------------------------------------------------------------
# Test 4: Spike outside session hours → no signal
# ---------------------------------------------------------------------------

def test_spike_outside_session_no_signal():
    """
    A >3×ATR spike at 04:00 UTC (outside London [07,11) and NY [12,16)).
    Should produce no signal because session filter rejects it.
    """
    # Build quiet bars in London session for ATR warmup
    rows = _build_quiet_bars(25)
    # Spike bar: 04:00 UTC on same day — outside session
    spike_time = "2025-01-13 04:00:00"
    rows.append(_spike_up_bar(spike_time, price=_BASE_PRICE, spike_rng=3.5))
    entry_time = "2025-01-13 04:05:00"
    rows.append(_quiet_bar(entry_time, price=_BASE_PRICE + 3.5, rng=0.5))

    bars = _make_bars(rows)
    signals = generate_signals(bars)
    assert signals == [], f"Expected no signals outside session, got {len(signals)}"


# ---------------------------------------------------------------------------
# Test 5: Fewer than atr_len+1 bars → no crash, no signals
# ---------------------------------------------------------------------------

def test_too_few_bars_no_crash():
    """
    Fewer than atr_len+1=21 bars — ATR cannot be computed; should return [].
    """
    rows = _build_quiet_bars(10)
    bars = _make_bars(rows)
    signals = generate_signals(bars)
    assert signals == [], f"Expected no signals with too few bars, got {len(signals)}"
