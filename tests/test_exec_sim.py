"""
test_exec_sim.py
----------------
TDD tests for strategies.research.exec_sim.

ALL tests use synthetic bars (hand-constructed DataFrames with tz-naive UTC
timestamps) — NO real cache, NO DB, NO network.

Default params throughout (unless overridden):
    half_spread = 0.15  (30c full spread on mid bars)
    slip        = 0.05  (5c slippage)

Fill formulas (all costs are adverse):
    BUY  entry_fill  = O + half_spread + slip
    SELL entry_fill  = O - half_spread - slip

    BUY  TP fill     = tp - half_spread             (limit, no slip)
    BUY  SL fill     = sl - half_spread - slip      (market, adverse)
    SELL TP fill     = tp + half_spread             (limit, no slip)
    SELL SL fill     = sl + half_spread + slip      (market, adverse)

    TIMEOUT / EOD (BUY):   C - half_spread - slip
    TIMEOUT / EOD (SELL):  C + half_spread + slip

pnl_price:
    BUY:  exit_fill - entry_fill
    SELL: entry_fill - exit_fill

risk_per_unit:
    BUY:  entry_fill - sl    (must be > 0)
    SELL: sl - entry_fill    (must be > 0)

r_multiple = pnl_price / risk_per_unit
"""
from __future__ import annotations

import math
import pandas as pd
import pytest

from strategies.research.exec_sim import Signal, TradeResult, simulate_trade, simulate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

HS = 0.15   # default half_spread
SL_DEF = 0.05  # default slip


def _make_bars(rows: list[dict]) -> pd.DataFrame:
    """
    Build a minimal OHLCV + spread DataFrame with tz-naive UTC timestamps.

    Each dict must have: time (str or Timestamp), open, high, low, close.
    volume and spread are given defaults if absent.
    """
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


# ---------------------------------------------------------------------------
# 1. BUY — hits TP (no SL trigger in any bar)
# ---------------------------------------------------------------------------

def test_buy_hits_tp_exact_fills():
    """
    BUY signal; TP reached on bar 2, SL never touched.

    Bar layout (mid prices):
      bar 0 (entry): O=2700.00  H=2700.80  L=2699.60  C=2700.50
                     → high < tp(2701.00) and low > sl(2699.00): no exit on entry bar
      bar 1:         O=2700.60  H=2700.95  L=2700.30  C=2700.80
                     → high(2700.95) < tp(2701.00) and low(2700.30) > sl: no exit
      bar 2:         O=2700.90  H=2701.20  L=2700.70  C=2701.00
                     → high(2701.20) >= tp(2701.00): TP hit; low(2700.70) > sl: not both
    """
    sl = 2699.00
    tp = 2701.00
    bars = _make_bars([
        {"time": "2026-01-05 09:00", "open": 2700.00, "high": 2700.80, "low": 2699.60, "close": 2700.50},
        {"time": "2026-01-05 10:00", "open": 2700.60, "high": 2700.95, "low": 2700.30, "close": 2700.80},
        {"time": "2026-01-05 11:00", "open": 2700.90, "high": 2701.20, "low": 2700.70, "close": 2701.00},
    ])
    sig = Signal(direction="BUY", entry_index=0, sl=sl, tp=tp)
    result = simulate_trade(bars, sig)

    # Entry fill
    expected_entry = 2700.00 + HS + SL_DEF    # 2700.20
    assert result.entry_price == pytest.approx(expected_entry, abs=1e-9)

    # Exit: BUY TP fill = tp - half_spread
    expected_exit = tp - HS                    # 2700.85
    assert result.exit_price == pytest.approx(expected_exit, abs=1e-9)

    assert result.exit_reason == "TP"
    assert result.exit_index == 2
    assert result.bars_held == 3   # bars 0, 1, 2 inclusive

    # pnl_price = exit_fill - entry_fill
    expected_pnl = expected_exit - expected_entry   # 2700.85 - 2700.20 = 0.65
    assert result.pnl_price == pytest.approx(expected_pnl, abs=1e-9)

    # risk_per_unit = entry_fill - sl = 2700.20 - 2699.00 = 1.20
    expected_risk = expected_entry - sl
    assert expected_risk > 0
    expected_r = expected_pnl / expected_risk
    assert result.r_multiple == pytest.approx(expected_r, abs=1e-9)


# ---------------------------------------------------------------------------
# 2. BUY — hits SL: realized loss slightly worse than -1R
# ---------------------------------------------------------------------------

def test_buy_hits_sl_exact_fills_and_loss_worse_than_minus1r():
    """
    BUY signal; SL hit on bar 1, TP never reached.

    We verify:
    - entry fill, SL fill correct to the cent
    - pnl is negative
    - r_multiple < -1.0  (due to 2× spread + slip on exit)
    """
    sl = 2699.00
    tp = 2702.00
    bars = _make_bars([
        {"time": "2026-01-05 09:00", "open": 2700.00, "high": 2700.80, "low": 2699.60, "close": 2700.30},
        {"time": "2026-01-05 10:00", "open": 2699.80, "high": 2699.90, "low": 2698.50, "close": 2699.00},
    ])
    sig = Signal(direction="BUY", entry_index=0, sl=sl, tp=tp)
    result = simulate_trade(bars, sig)

    expected_entry = 2700.00 + HS + SL_DEF    # 2700.20
    assert result.entry_price == pytest.approx(expected_entry, abs=1e-9)

    # BUY SL fill = sl - half_spread - slip
    expected_exit = sl - HS - SL_DEF          # 2698.80
    assert result.exit_price == pytest.approx(expected_exit, abs=1e-9)

    assert result.exit_reason == "SL"
    assert result.exit_index == 1

    expected_pnl = expected_exit - expected_entry  # 2698.80 - 2700.20 = -1.40
    assert result.pnl_price == pytest.approx(expected_pnl, abs=1e-9)

    # risk_per_unit = entry_fill - sl = 2700.20 - 2699.00 = 1.20
    expected_risk = expected_entry - sl
    expected_r = expected_pnl / expected_risk  # -1.40 / 1.20 ≈ -1.1667
    assert result.r_multiple == pytest.approx(expected_r, abs=1e-9)

    # Verify r_multiple is worse than -1.0
    assert result.r_multiple < -1.0, (
        f"SL exit r_multiple should be < -1.0 due to spread+slip costs, got {result.r_multiple}"
    )


# ---------------------------------------------------------------------------
# 3. SELL — hits TP
# ---------------------------------------------------------------------------

def test_sell_hits_tp_exact_fills():
    """
    SELL signal; TP reached on bar 2, SL never triggered.

    Bar layout:
      bar 0 (entry): O=2700.00  H=2700.40  L=2699.50  C=2699.80
                     → low(2699.50) > tp(2699.00) and high(2700.40) < sl(2701.50): no exit
      bar 1:         O=2699.80  H=2700.00  L=2699.20  C=2699.40
                     → low(2699.20) > tp(2699.00): no exit
      bar 2:         O=2699.30  H=2699.60  L=2698.80  C=2699.00
                     → low(2698.80) <= tp(2699.00): TP hit; high(2699.60) < sl(2701.50): not both
    """
    sl = 2701.50
    tp = 2699.00
    bars = _make_bars([
        {"time": "2026-01-05 09:00", "open": 2700.00, "high": 2700.40, "low": 2699.50, "close": 2699.80},
        {"time": "2026-01-05 10:00", "open": 2699.80, "high": 2700.00, "low": 2699.20, "close": 2699.40},
        {"time": "2026-01-05 11:00", "open": 2699.30, "high": 2699.60, "low": 2698.80, "close": 2699.00},
    ])
    sig = Signal(direction="SELL", entry_index=0, sl=sl, tp=tp)
    result = simulate_trade(bars, sig)

    # SELL entry fill = O - half_spread - slip
    expected_entry = 2700.00 - HS - SL_DEF    # 2699.80
    assert result.entry_price == pytest.approx(expected_entry, abs=1e-9)

    # SELL TP fill = tp + half_spread
    expected_exit = tp + HS                    # 2699.15
    assert result.exit_price == pytest.approx(expected_exit, abs=1e-9)

    assert result.exit_reason == "TP"
    assert result.exit_index == 2

    # pnl_price = entry_fill - exit_fill = 2699.80 - 2699.15 = 0.65
    expected_pnl = expected_entry - expected_exit
    assert result.pnl_price == pytest.approx(expected_pnl, abs=1e-9)
    assert result.pnl_price > 0

    # risk_per_unit = sl - entry_fill = 2701.50 - 2699.80 = 1.70
    expected_risk = sl - expected_entry
    assert expected_risk > 0
    expected_r = expected_pnl / expected_risk
    assert result.r_multiple == pytest.approx(expected_r, abs=1e-9)


# ---------------------------------------------------------------------------
# 4. SELL — hits SL
# ---------------------------------------------------------------------------

def test_sell_hits_sl_exact_fills():
    """
    SELL signal; SL hit on bar 1.

    SELL SL fill = sl + half_spread + slip
    """
    sl = 2701.50
    tp = 2699.00
    bars = _make_bars([
        {"time": "2026-01-05 09:00", "open": 2700.00, "high": 2700.40, "low": 2699.50, "close": 2700.20},
        {"time": "2026-01-05 10:00", "open": 2700.80, "high": 2701.80, "low": 2700.50, "close": 2701.00},
    ])
    sig = Signal(direction="SELL", entry_index=0, sl=sl, tp=tp)
    result = simulate_trade(bars, sig)

    expected_entry = 2700.00 - HS - SL_DEF    # 2699.80
    assert result.entry_price == pytest.approx(expected_entry, abs=1e-9)

    # SELL SL fill = sl + half_spread + slip
    expected_exit = sl + HS + SL_DEF          # 2701.70
    assert result.exit_price == pytest.approx(expected_exit, abs=1e-9)

    assert result.exit_reason == "SL"
    assert result.exit_index == 1

    expected_pnl = expected_entry - expected_exit  # 2699.80 - 2701.70 = -1.90
    assert result.pnl_price == pytest.approx(expected_pnl, abs=1e-9)
    assert result.pnl_price < 0

    # risk_per_unit = sl - entry_fill = 2701.50 - 2699.80 = 1.70
    expected_risk = sl - expected_entry
    expected_r = expected_pnl / expected_risk  # -1.90 / 1.70 ≈ -1.1176
    assert result.r_multiple == pytest.approx(expected_r, abs=1e-9)
    assert result.r_multiple < -1.0


# ---------------------------------------------------------------------------
# 5. Same-bar SL + TP → resolves SL (conservative tie-break)
# ---------------------------------------------------------------------------

def test_same_bar_sl_and_tp_hit_resolves_sl():
    """
    BUY signal; entry bar's range spans both TP and SL → must resolve as SL.

    bar 0: O=2700.00  H=2702.00  L=2698.00  C=2700.00
           high(2702.00) >= tp(2701.00) AND low(2698.00) <= sl(2699.00)
           → both triggered; tie-break → SL
    """
    sl = 2699.00
    tp = 2701.00
    bars = _make_bars([
        {"time": "2026-01-05 09:00", "open": 2700.00, "high": 2702.00, "low": 2698.00, "close": 2700.00},
    ])
    sig = Signal(direction="BUY", entry_index=0, sl=sl, tp=tp)
    result = simulate_trade(bars, sig)

    assert result.exit_reason == "SL", (
        f"Expected SL on same-bar TP+SL, got {result.exit_reason}"
    )
    assert result.exit_index == 0

    expected_entry = 2700.00 + HS + SL_DEF    # 2700.20
    expected_exit  = sl - HS - SL_DEF         # 2698.80
    assert result.entry_price == pytest.approx(expected_entry, abs=1e-9)
    assert result.exit_price  == pytest.approx(expected_exit,  abs=1e-9)


def test_same_bar_sl_and_tp_hit_sell_resolves_sl():
    """
    SELL signal; entry bar spans both TP and SL → resolves as SL.

    bar 0: O=2700.00  H=2702.00  L=2698.00  C=2700.00
           SELL: TP triggered when bar.low <= tp(2698.50) → low(2698.00) <= 2698.50 YES
                 SL triggered when bar.high >= sl(2701.50) → high(2702.00) >= 2701.50 YES
           → both triggered → SL
    """
    sl = 2701.50
    tp = 2698.50
    bars = _make_bars([
        {"time": "2026-01-05 09:00", "open": 2700.00, "high": 2702.00, "low": 2698.00, "close": 2700.00},
    ])
    sig = Signal(direction="SELL", entry_index=0, sl=sl, tp=tp)
    result = simulate_trade(bars, sig)

    assert result.exit_reason == "SL"
    assert result.exit_index == 0

    expected_entry = 2700.00 - HS - SL_DEF    # 2699.80
    expected_exit  = sl + HS + SL_DEF         # 2701.70
    assert result.entry_price == pytest.approx(expected_entry, abs=1e-9)
    assert result.exit_price  == pytest.approx(expected_exit,  abs=1e-9)


# ---------------------------------------------------------------------------
# 6. Entry bar itself triggers SL (no same-bar TP)
# ---------------------------------------------------------------------------

def test_entry_bar_triggers_sl_buy():
    """
    BUY: entry bar's low dips below SL but high does NOT reach TP → SL on bar 0.
    """
    sl = 2699.50
    tp = 2702.00
    bars = _make_bars([
        {"time": "2026-01-05 09:00", "open": 2700.00, "high": 2700.80, "low": 2699.00, "close": 2699.60},
        {"time": "2026-01-05 10:00", "open": 2699.60, "high": 2699.80, "low": 2699.30, "close": 2699.50},
    ])
    sig = Signal(direction="BUY", entry_index=0, sl=sl, tp=tp)
    result = simulate_trade(bars, sig)

    assert result.exit_reason == "SL"
    assert result.exit_index == 0
    assert result.bars_held == 1  # only the entry bar

    expected_entry = 2700.00 + HS + SL_DEF
    expected_exit  = sl - HS - SL_DEF
    assert result.entry_price == pytest.approx(expected_entry, abs=1e-9)
    assert result.exit_price  == pytest.approx(expected_exit,  abs=1e-9)


def test_entry_bar_triggers_sl_sell():
    """
    SELL: entry bar's high exceeds SL → SL on bar 0.
    """
    sl = 2701.00
    tp = 2698.00
    bars = _make_bars([
        {"time": "2026-01-05 09:00", "open": 2700.00, "high": 2701.50, "low": 2699.80, "close": 2700.50},
    ])
    sig = Signal(direction="SELL", entry_index=0, sl=sl, tp=tp)
    result = simulate_trade(bars, sig)

    assert result.exit_reason == "SL"
    assert result.exit_index == 0

    expected_entry = 2700.00 - HS - SL_DEF
    expected_exit  = sl + HS + SL_DEF
    assert result.entry_price == pytest.approx(expected_entry, abs=1e-9)
    assert result.exit_price  == pytest.approx(expected_exit,  abs=1e-9)


# ---------------------------------------------------------------------------
# 7. TIMEOUT exit at max_hold_bars
# ---------------------------------------------------------------------------

def test_timeout_exit_buy():
    """
    BUY: max_hold_bars=2; neither TP nor SL hit in bars 0 or 1 → exit at bar 1 close.

    TIMEOUT fill (BUY) = C - half_spread - slip
    """
    sl = 2698.00
    tp = 2703.00
    bars = _make_bars([
        {"time": "2026-01-05 09:00", "open": 2700.00, "high": 2700.80, "low": 2699.50, "close": 2700.30},
        {"time": "2026-01-05 10:00", "open": 2700.30, "high": 2700.90, "low": 2699.80, "close": 2700.60},
        {"time": "2026-01-05 11:00", "open": 2700.60, "high": 2703.50, "low": 2700.40, "close": 2703.00},
    ])
    sig = Signal(direction="BUY", entry_index=0, sl=sl, tp=tp)
    result = simulate_trade(bars, sig, max_hold_bars=2)

    assert result.exit_reason == "TIMEOUT"
    assert result.exit_index == 1      # bar 0 and bar 1 exhausted → exit at bar 1

    # TIMEOUT fill: close of exit bar - half_spread - slip
    close_1 = 2700.60
    expected_exit = close_1 - HS - SL_DEF     # 2700.40
    assert result.exit_price == pytest.approx(expected_exit, abs=1e-9)

    expected_entry = 2700.00 + HS + SL_DEF    # 2700.20
    expected_pnl = expected_exit - expected_entry
    assert result.pnl_price == pytest.approx(expected_pnl, abs=1e-9)

    assert result.bars_held == 2


def test_timeout_exit_sell():
    """
    SELL: max_hold_bars=2 → exit at bar 1 close.

    TIMEOUT fill (SELL) = C + half_spread + slip
    """
    sl = 2703.00
    tp = 2697.00
    bars = _make_bars([
        {"time": "2026-01-05 09:00", "open": 2700.00, "high": 2700.80, "low": 2699.50, "close": 2699.80},
        {"time": "2026-01-05 10:00", "open": 2699.80, "high": 2700.20, "low": 2699.20, "close": 2699.50},
        {"time": "2026-01-05 11:00", "open": 2699.50, "high": 2699.80, "low": 2696.00, "close": 2696.50},
    ])
    sig = Signal(direction="SELL", entry_index=0, sl=sl, tp=tp)
    result = simulate_trade(bars, sig, max_hold_bars=2)

    assert result.exit_reason == "TIMEOUT"
    assert result.exit_index == 1

    close_1 = 2699.50
    expected_exit = close_1 + HS + SL_DEF     # 2699.70
    assert result.exit_price == pytest.approx(expected_exit, abs=1e-9)

    expected_entry = 2700.00 - HS - SL_DEF    # 2699.80
    expected_pnl = expected_entry - expected_exit  # 2699.80 - 2699.70 = 0.10
    assert result.pnl_price == pytest.approx(expected_pnl, abs=1e-9)

    assert result.bars_held == 2


# ---------------------------------------------------------------------------
# 8. EOD exit when bars run out
# ---------------------------------------------------------------------------

def test_eod_exit_buy():
    """
    BUY: bars run out before TP/SL → exit at last bar close, reason "EOD".

    EOD fill (BUY) = C - half_spread - slip  (same formula as TIMEOUT)
    """
    sl = 2698.00
    tp = 2703.00
    bars = _make_bars([
        {"time": "2026-01-05 09:00", "open": 2700.00, "high": 2700.80, "low": 2699.50, "close": 2700.30},
        {"time": "2026-01-05 10:00", "open": 2700.30, "high": 2700.90, "low": 2699.80, "close": 2700.60},
    ])
    sig = Signal(direction="BUY", entry_index=0, sl=sl, tp=tp)
    result = simulate_trade(bars, sig)   # no max_hold_bars

    assert result.exit_reason == "EOD"
    assert result.exit_index == 1  # last bar

    close_last = 2700.60
    expected_exit = close_last - HS - SL_DEF
    assert result.exit_price == pytest.approx(expected_exit, abs=1e-9)


def test_eod_exit_sell():
    """
    SELL: bars run out → EOD exit at last bar close.

    EOD fill (SELL) = C + half_spread + slip
    """
    sl = 2703.00
    tp = 2697.00
    bars = _make_bars([
        {"time": "2026-01-05 09:00", "open": 2700.00, "high": 2700.40, "low": 2699.50, "close": 2699.80},
        {"time": "2026-01-05 10:00", "open": 2699.80, "high": 2700.10, "low": 2699.30, "close": 2699.60},
    ])
    sig = Signal(direction="SELL", entry_index=0, sl=sl, tp=tp)
    result = simulate_trade(bars, sig)

    assert result.exit_reason == "EOD"
    assert result.exit_index == 1

    close_last = 2699.60
    expected_exit = close_last + HS + SL_DEF
    assert result.exit_price == pytest.approx(expected_exit, abs=1e-9)


# ---------------------------------------------------------------------------
# 9. Malformed signal → ValueError
# ---------------------------------------------------------------------------

def test_buy_signal_sl_above_open_raises():
    """BUY: sl >= open is invalid (SL must be below open for BUY)."""
    bars = _make_bars([
        {"time": "2026-01-05 09:00", "open": 2700.00, "high": 2701.00, "low": 2699.50, "close": 2700.50},
    ])
    # sl above open violates sl < O < tp
    sig = Signal(direction="BUY", entry_index=0, sl=2701.00, tp=2702.00)
    with pytest.raises(ValueError, match=r"(?i)(sl|stop|invalid|signal|buy)"):
        simulate_trade(bars, sig)


def test_buy_signal_tp_below_open_raises():
    """BUY: tp <= open is invalid (TP must be above open for BUY)."""
    bars = _make_bars([
        {"time": "2026-01-05 09:00", "open": 2700.00, "high": 2701.00, "low": 2699.50, "close": 2700.50},
    ])
    sig = Signal(direction="BUY", entry_index=0, sl=2699.00, tp=2699.50)
    with pytest.raises(ValueError, match=r"(?i)(tp|target|invalid|signal|buy)"):
        simulate_trade(bars, sig)


def test_sell_signal_sl_below_open_raises():
    """SELL: sl <= open is invalid (SL must be above open for SELL)."""
    bars = _make_bars([
        {"time": "2026-01-05 09:00", "open": 2700.00, "high": 2701.00, "low": 2699.50, "close": 2700.50},
    ])
    # For SELL: requires tp < O < sl → sl=2699 violates sl > O
    sig = Signal(direction="SELL", entry_index=0, sl=2699.00, tp=2698.00)
    with pytest.raises(ValueError):
        simulate_trade(bars, sig)


def test_sell_signal_tp_above_open_raises():
    """SELL: tp >= open is invalid (TP must be below open for SELL)."""
    bars = _make_bars([
        {"time": "2026-01-05 09:00", "open": 2700.00, "high": 2701.00, "low": 2699.50, "close": 2700.50},
    ])
    sig = Signal(direction="SELL", entry_index=0, sl=2702.00, tp=2700.50)
    with pytest.raises(ValueError):
        simulate_trade(bars, sig)


def test_invalid_direction_raises():
    """Direction other than BUY/SELL raises ValueError."""
    bars = _make_bars([
        {"time": "2026-01-05 09:00", "open": 2700.00, "high": 2701.00, "low": 2699.50, "close": 2700.50},
    ])
    sig = Signal(direction="LONG", entry_index=0, sl=2699.00, tp=2702.00)
    with pytest.raises(ValueError, match=r"(?i)(direction|invalid|buy|sell)"):
        simulate_trade(bars, sig)


# ---------------------------------------------------------------------------
# 10. Custom half_spread / slip changes fills as expected
# ---------------------------------------------------------------------------

def test_custom_half_spread_and_slip():
    """
    Double the slip (2× = 0.10) and custom half_spread=0.20.
    Verify entry/exit fills change exactly as the formula predicts.
    """
    hs2 = 0.20
    sl2 = 0.10
    sl_price = 2699.00
    tp_price = 2703.00

    bars = _make_bars([
        {"time": "2026-01-05 09:00", "open": 2700.00, "high": 2703.50, "low": 2699.50, "close": 2702.00},
    ])
    sig = Signal(direction="BUY", entry_index=0, sl=sl_price, tp=tp_price)
    result = simulate_trade(bars, sig, half_spread=hs2, slip=sl2)

    expected_entry = 2700.00 + hs2 + sl2   # 2700.30
    expected_exit  = tp_price - hs2         # 2702.80   (TP, no slip)

    assert result.entry_price == pytest.approx(expected_entry, abs=1e-9)
    assert result.exit_price  == pytest.approx(expected_exit,  abs=1e-9)
    assert result.exit_reason == "TP"

    # Verify different from defaults
    default_result = simulate_trade(bars, sig, half_spread=HS, slip=SL_DEF)
    assert result.entry_price != default_result.entry_price


def test_zero_slip_fills():
    """
    With slip=0.0, entry and SL fills should only include spread cost, no slippage.
    """
    hs = 0.15
    sl_price = 2699.00
    tp_price = 2703.00

    bars = _make_bars([
        {"time": "2026-01-05 09:00", "open": 2700.00, "high": 2700.50, "low": 2698.50, "close": 2699.50},
    ])
    sig = Signal(direction="BUY", entry_index=0, sl=sl_price, tp=tp_price)
    result = simulate_trade(bars, sig, half_spread=hs, slip=0.0)

    # Entry: O + hs + 0 = 2700.15
    assert result.entry_price == pytest.approx(2700.00 + hs, abs=1e-9)

    # SL fill: sl - hs - 0 = 2698.85
    assert result.exit_price == pytest.approx(sl_price - hs, abs=1e-9)
    assert result.exit_reason == "SL"


# ---------------------------------------------------------------------------
# 11. simulate() over list of signals
# ---------------------------------------------------------------------------

def test_simulate_returns_one_result_per_signal():
    """simulate() with 3 independent signals returns list of 3 TradeResults."""
    bars = _make_bars([
        {"time": "2026-01-05 09:00", "open": 2700.00, "high": 2701.50, "low": 2699.50, "close": 2701.00},
        {"time": "2026-01-05 10:00", "open": 2701.00, "high": 2702.00, "low": 2700.50, "close": 2701.50},
        {"time": "2026-01-05 11:00", "open": 2701.50, "high": 2703.00, "low": 2701.00, "close": 2702.50},
    ])
    signals = [
        Signal(direction="BUY",  entry_index=0, sl=2699.00, tp=2703.00),
        Signal(direction="BUY",  entry_index=1, sl=2700.00, tp=2703.00),
        Signal(direction="SELL", entry_index=0, sl=2702.00, tp=2698.00),
    ]
    results = simulate(bars, signals)

    assert isinstance(results, list)
    assert len(results) == 3
    for r in results:
        assert isinstance(r, TradeResult)


def test_simulate_empty_signals_returns_empty_list():
    """simulate() with no signals returns empty list."""
    bars = _make_bars([
        {"time": "2026-01-05 09:00", "open": 2700.00, "high": 2701.00, "low": 2699.00, "close": 2700.50},
    ])
    results = simulate(bars, [])
    assert results == []


def test_simulate_signals_are_independent():
    """
    Two signals at the same entry bar — results should be independent.
    Signal A: BUY; Signal B: also BUY with different SL/TP.
    Both should produce their own TradeResult with no cross-contamination.
    """
    sl_a, tp_a = 2699.00, 2703.00
    sl_b, tp_b = 2699.50, 2701.50
    bars = _make_bars([
        {"time": "2026-01-05 09:00", "open": 2700.00, "high": 2701.60, "low": 2699.60, "close": 2701.20},
        {"time": "2026-01-05 10:00", "open": 2701.20, "high": 2703.50, "low": 2700.80, "close": 2703.00},
    ])
    sig_a = Signal(direction="BUY", entry_index=0, sl=sl_a, tp=tp_a)
    sig_b = Signal(direction="BUY", entry_index=0, sl=sl_b, tp=tp_b)

    results = simulate(bars, [sig_a, sig_b])

    assert len(results) == 2

    # sig_b TP=2701.50 is hit on bar 0 (high=2701.60 >= 2701.50) since sl_b=2699.50 and low=2699.60 > 2699.50
    r_b = results[1]
    assert r_b.exit_reason == "TP"
    assert r_b.exit_index == 0

    # sig_a TP=2703.00 is not hit on bar 0 (high=2701.60 < 2703.00); hit on bar 1 (high=2703.50 >= 2703.00)
    r_a = results[0]
    assert r_a.exit_reason == "TP"
    assert r_a.exit_index == 1


# ---------------------------------------------------------------------------
# 12. entry_index not at 0 — mid-series entry
# ---------------------------------------------------------------------------

def test_entry_index_mid_series():
    """
    Signal with entry_index=2 scans only from bar 2 onward;
    bars 0 and 1 are irrelevant to this trade.
    """
    sl = 2699.00
    tp = 2702.00
    bars = _make_bars([
        # bars 0-1: contain wicks that would trigger SL/TP if scanned — must be ignored
        {"time": "2026-01-05 07:00", "open": 2700.00, "high": 2703.00, "low": 2698.00, "close": 2700.00},
        {"time": "2026-01-05 08:00", "open": 2700.00, "high": 2703.00, "low": 2698.00, "close": 2700.00},
        # bar 2 (entry bar): no TP/SL trigger
        {"time": "2026-01-05 09:00", "open": 2700.00, "high": 2700.80, "low": 2699.60, "close": 2700.50},
        # bar 3: TP hit
        {"time": "2026-01-05 10:00", "open": 2700.50, "high": 2702.30, "low": 2700.20, "close": 2702.00},
    ])
    sig = Signal(direction="BUY", entry_index=2, sl=sl, tp=tp)
    result = simulate_trade(bars, sig)

    assert result.entry_index == 2
    assert result.exit_index == 3
    assert result.exit_reason == "TP"

    expected_entry = 2700.00 + HS + SL_DEF
    expected_exit  = tp - HS
    assert result.entry_price == pytest.approx(expected_entry, abs=1e-9)
    assert result.exit_price  == pytest.approx(expected_exit,  abs=1e-9)


# ---------------------------------------------------------------------------
# 13. TradeResult metadata correctness
# ---------------------------------------------------------------------------

def test_trade_result_metadata():
    """Verify direction, entry_time, exit_time, bars_held are correctly populated."""
    sl = 2699.00
    tp = 2702.00
    t0 = pd.Timestamp("2026-01-05 09:00")
    t1 = pd.Timestamp("2026-01-05 10:00")
    t2 = pd.Timestamp("2026-01-05 11:00")

    bars = _make_bars([
        {"time": t0, "open": 2700.00, "high": 2700.80, "low": 2699.60, "close": 2700.50},
        {"time": t1, "open": 2700.50, "high": 2700.90, "low": 2699.80, "close": 2700.80},
        {"time": t2, "open": 2700.80, "high": 2702.30, "low": 2700.50, "close": 2702.00},
    ])
    sig = Signal(direction="BUY", entry_index=0, sl=sl, tp=tp)
    result = simulate_trade(bars, sig)

    assert result.direction == "BUY"
    assert result.entry_index == 0
    assert result.exit_index == 2
    assert result.entry_time == t0
    assert result.exit_time  == t2
    assert result.bars_held  == 3  # bars 0, 1, 2


# ---------------------------------------------------------------------------
# 14. Risk-per-unit is always positive (buy and sell)
# ---------------------------------------------------------------------------

def test_risk_per_unit_buy_positive():
    """For BUY, risk_per_unit = entry_fill - sl must be > 0."""
    sl = 2699.00
    tp = 2702.00
    bars = _make_bars([
        {"time": "2026-01-05 09:00", "open": 2700.00, "high": 2702.50, "low": 2699.60, "close": 2702.00},
    ])
    sig = Signal(direction="BUY", entry_index=0, sl=sl, tp=tp)
    result = simulate_trade(bars, sig)

    entry = result.entry_price
    risk  = entry - sl
    assert risk > 0
    # r_multiple = pnl / risk
    expected_r = result.pnl_price / risk
    assert result.r_multiple == pytest.approx(expected_r, abs=1e-9)


def test_risk_per_unit_sell_positive():
    """For SELL, risk_per_unit = sl - entry_fill must be > 0."""
    sl = 2702.00
    tp = 2698.00
    bars = _make_bars([
        {"time": "2026-01-05 09:00", "open": 2700.00, "high": 2700.50, "low": 2698.00, "close": 2698.50},
    ])
    sig = Signal(direction="SELL", entry_index=0, sl=sl, tp=tp)
    result = simulate_trade(bars, sig)

    entry = result.entry_price
    risk  = sl - entry
    assert risk > 0
    expected_r = result.pnl_price / risk
    assert result.r_multiple == pytest.approx(expected_r, abs=1e-9)


# ---------------------------------------------------------------------------
# 15. TP exactly equal to bar high/low triggers exit
# ---------------------------------------------------------------------------

def test_buy_tp_exact_high_triggers():
    """BUY: bar.high exactly == tp should trigger TP (>= condition)."""
    sl = 2699.00
    tp = 2701.00
    bars = _make_bars([
        {"time": "2026-01-05 09:00", "open": 2700.00, "high": 2701.00, "low": 2699.60, "close": 2700.80},
    ])
    sig = Signal(direction="BUY", entry_index=0, sl=sl, tp=tp)
    result = simulate_trade(bars, sig)

    assert result.exit_reason == "TP"


def test_sell_tp_exact_low_triggers():
    """SELL: bar.low exactly == tp should trigger TP (<= condition)."""
    sl = 2702.00
    tp = 2699.00
    bars = _make_bars([
        {"time": "2026-01-05 09:00", "open": 2700.00, "high": 2700.50, "low": 2699.00, "close": 2699.50},
    ])
    sig = Signal(direction="SELL", entry_index=0, sl=sl, tp=tp)
    result = simulate_trade(bars, sig)

    assert result.exit_reason == "TP"


# ---------------------------------------------------------------------------
# 16. SL exactly equal to bar high/low triggers exit
# ---------------------------------------------------------------------------

def test_buy_sl_exact_low_triggers():
    """BUY: bar.low exactly == sl should trigger SL (<= condition)."""
    sl = 2699.50
    tp = 2702.00
    bars = _make_bars([
        {"time": "2026-01-05 09:00", "open": 2700.00, "high": 2700.80, "low": 2699.50, "close": 2700.20},
    ])
    sig = Signal(direction="BUY", entry_index=0, sl=sl, tp=tp)
    result = simulate_trade(bars, sig)

    assert result.exit_reason == "SL"


def test_sell_sl_exact_high_triggers():
    """SELL: bar.high exactly == sl should trigger SL (>= condition)."""
    sl = 2701.00
    tp = 2698.00
    bars = _make_bars([
        {"time": "2026-01-05 09:00", "open": 2700.00, "high": 2701.00, "low": 2699.50, "close": 2700.30},
    ])
    sig = Signal(direction="SELL", entry_index=0, sl=sl, tp=tp)
    result = simulate_trade(bars, sig)

    assert result.exit_reason == "SL"


# ---------------------------------------------------------------------------
# 17. max_hold_bars=1 → exits on entry bar if no TP/SL, reason TIMEOUT
# ---------------------------------------------------------------------------

def test_max_hold_bars_one_timeout():
    """
    max_hold_bars=1 means we can only hold bar 0.
    If neither TP nor SL is hit on bar 0 → TIMEOUT exit at bar 0 close.
    """
    sl = 2698.00
    tp = 2703.00
    bars = _make_bars([
        {"time": "2026-01-05 09:00", "open": 2700.00, "high": 2700.80, "low": 2699.50, "close": 2700.40},
        {"time": "2026-01-05 10:00", "open": 2700.40, "high": 2703.50, "low": 2700.20, "close": 2703.00},
    ])
    sig = Signal(direction="BUY", entry_index=0, sl=sl, tp=tp)
    result = simulate_trade(bars, sig, max_hold_bars=1)

    assert result.exit_reason == "TIMEOUT"
    assert result.exit_index == 0
    assert result.bars_held == 1

    close_0 = 2700.40
    expected_exit = close_0 - HS - SL_DEF
    assert result.exit_price == pytest.approx(expected_exit, abs=1e-9)
