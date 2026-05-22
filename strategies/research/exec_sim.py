"""
exec_sim.py
-----------
Execution-realistic backtest engine for single mid-price bar series.

The raw data is a single MID price; there is no real bid/ask.  We model:
    bid = mid - half_spread
    ask = mid + half_spread
using a constant ``half_spread`` (default 0.15 → 30c full spread).
Slippage ``slip`` default 0.05 (5c).  Both are fully configurable.

All trading costs are **adverse** — every fill is priced to hurt the trader.

Fill formulas
~~~~~~~~~~~~~
Entry (at the entry bar's open mid ``O``):
    BUY:  entry_fill = O + half_spread + slip   (buying at ask + slip)
    SELL: entry_fill = O - half_spread - slip   (selling at bid - slip)

Exit trigger detection (worst-case bar wick, scanning from entry bar inclusive):
    BUY:  TP triggered when bar.high >= tp
          SL triggered when bar.low  <= sl
    SELL: TP triggered when bar.low  <= tp
          SL triggered when bar.high >= sl
    Tie (both trigger in same bar) → SL wins (conservative).

Exit fills:
    BUY  TP: tp  - half_spread            (close long at bid; limit, no slip)
    BUY  SL: sl  - half_spread - slip     (close long at bid; market, adverse slip)
    SELL TP: tp  + half_spread            (close short at ask; limit, no slip)
    SELL SL: sl  + half_spread + slip     (close short at ask; market, adverse slip)
    TIMEOUT / EOD (BUY):  close - half_spread - slip
    TIMEOUT / EOD (SELL): close + half_spread + slip

PnL and R:
    pnl_price    = exit_fill - entry_fill               (BUY)
                 = entry_fill - exit_fill               (SELL)
    risk_per_unit= entry_fill - sl                      (BUY,  must be > 0)
                 = sl - entry_fill                      (SELL, must be > 0)
    r_multiple   = pnl_price / risk_per_unit

Signal validation (O = entry bar open mid):
    BUY:  requires sl < O < tp   (SL below, TP above open)
    SELL: requires tp < O < sl   (TP below, SL above open)
    Violation raises ValueError with a descriptive message.

Tick replay is intentionally NOT implemented; worst-case wick is the default.
Tick replay can be added as a future extension.

Bars are mid-OHLC DataFrames (output of bar_builder.build_bars):
    columns: time, open, high, low, close, volume, spread
    time: tz-naive UTC datetime64[ns]
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Signal:
    """A trading signal specifying direction, entry bar, stop-loss, and take-profit."""
    direction: str      # "BUY" or "SELL"
    entry_index: int    # integer row index into bars; entry fills at bars.iloc[entry_index].open
    sl: float           # mid price stop-loss level
    tp: float           # mid price take-profit level


@dataclass(frozen=True)
class TradeResult:
    """The result of simulating a single trade signal against a bar series."""
    direction: str
    entry_index: int
    entry_time: Any         # the bar's time value (tz-naive pd.Timestamp)
    entry_price: float      # fill price (adverse spread + slip)
    exit_index: int
    exit_time: Any          # the bar's time value
    exit_price: float       # fill price (adverse spread + slip)
    exit_reason: str        # "TP" | "SL" | "TIMEOUT" | "EOD"
    pnl_price: float        # profit/loss in price units
    r_multiple: float       # pnl_price / risk_per_unit
    bars_held: int          # number of bars in the trade (inclusive of entry and exit bars)


# ---------------------------------------------------------------------------
# Core simulation functions
# ---------------------------------------------------------------------------

def simulate_trade(
    bars: pd.DataFrame,
    signal: Signal,
    *,
    half_spread: float = 0.15,
    slip: float = 0.05,
    max_hold_bars: int | None = None,
) -> TradeResult:
    """Simulate a single trade signal against a bar series.

    Parameters
    ----------
    bars:
        Mid-OHLCV DataFrame with columns ``time, open, high, low, close,
        volume, spread``.  The ``time`` column must be tz-naive datetime64[ns].
    signal:
        A :class:`Signal` instance specifying direction, entry bar index, SL,
        and TP (all in mid-price units).
    half_spread:
        Half the assumed full bid/ask spread.  Default 0.15 (30c full spread).
    slip:
        One-way slippage applied on market orders (entries and SL exits).
        Default 0.05 (5c).  TP exits are limit orders; no slip is applied.
    max_hold_bars:
        Maximum number of bars to hold the trade (inclusive of the entry bar).
        If the trade has not hit TP or SL after ``max_hold_bars`` bars, it
        is closed at the last bar's close (reason "TIMEOUT").  ``None`` means
        hold until TP, SL, or EOD.

    Returns
    -------
    TradeResult

    Raises
    ------
    ValueError
        If ``signal.direction`` is not "BUY" or "SELL", or if the SL/TP
        levels are on the wrong side of the entry bar's open mid price.
    """
    direction = signal.direction
    entry_idx = signal.entry_index
    sl = signal.sl
    tp = signal.tp

    # -----------------------------------------------------------------------
    # Validate direction
    # -----------------------------------------------------------------------
    if direction not in ("BUY", "SELL"):
        raise ValueError(
            f"Invalid direction '{direction}': must be 'BUY' or 'SELL'."
        )

    # -----------------------------------------------------------------------
    # Entry bar open mid price
    # -----------------------------------------------------------------------
    entry_bar = bars.iloc[entry_idx]
    O = float(entry_bar["open"])

    # -----------------------------------------------------------------------
    # Signal validation: BUY requires sl < O < tp; SELL requires tp < O < sl
    # -----------------------------------------------------------------------
    if direction == "BUY":
        if not (sl < O):
            raise ValueError(
                f"Invalid BUY signal: sl ({sl}) must be strictly below entry bar open mid "
                f"({O}).  Received sl={sl}, open={O}, tp={tp}."
            )
        if not (O < tp):
            raise ValueError(
                f"Invalid BUY signal: tp ({tp}) must be strictly above entry bar open mid "
                f"({O}).  Received sl={sl}, open={O}, tp={tp}."
            )
    else:  # SELL
        if not (tp < O):
            raise ValueError(
                f"Invalid SELL signal: tp ({tp}) must be strictly below entry bar open mid "
                f"({O}).  Received sl={sl}, open={O}, tp={tp}."
            )
        if not (O < sl):
            raise ValueError(
                f"Invalid SELL signal: sl ({sl}) must be strictly above entry bar open mid "
                f"({O}).  Received sl={sl}, open={O}, tp={tp}."
            )

    # -----------------------------------------------------------------------
    # Entry fill
    # -----------------------------------------------------------------------
    if direction == "BUY":
        entry_fill = O + half_spread + slip
    else:  # SELL
        entry_fill = O - half_spread - slip

    # -----------------------------------------------------------------------
    # Scan bars from entry_index inclusive for TP / SL triggers
    # -----------------------------------------------------------------------
    n_bars = len(bars)
    scan_start = entry_idx
    scan_end = n_bars  # exclusive

    # Apply max_hold_bars limit
    if max_hold_bars is not None:
        scan_end = min(scan_end, entry_idx + max_hold_bars)

    exit_index: int | None = None
    exit_reason: str | None = None
    exit_fill: float | None = None

    for i in range(scan_start, scan_end):
        bar = bars.iloc[i]
        bar_high = float(bar["high"])
        bar_low  = float(bar["low"])

        if direction == "BUY":
            tp_hit = bar_high >= tp
            sl_hit = bar_low  <= sl
        else:  # SELL
            tp_hit = bar_low  <= tp
            sl_hit = bar_high >= sl

        if sl_hit:
            # SL wins ties (conservative)
            exit_index  = i
            exit_reason = "SL"
            if direction == "BUY":
                exit_fill = sl - half_spread - slip
            else:
                exit_fill = sl + half_spread + slip
            break

        if tp_hit:
            exit_index  = i
            exit_reason = "TP"
            if direction == "BUY":
                exit_fill = tp - half_spread
            else:
                exit_fill = tp + half_spread
            break

    # -----------------------------------------------------------------------
    # If no TP/SL triggered → TIMEOUT or EOD
    # -----------------------------------------------------------------------
    if exit_index is None:
        last_scanned_i = scan_end - 1  # last bar we had budget to scan

        # Determine if we exhausted max_hold_bars or actual bars
        if max_hold_bars is not None and scan_end == entry_idx + max_hold_bars:
            reason = "TIMEOUT"
        else:
            reason = "EOD"

        exit_index  = last_scanned_i
        exit_reason = reason
        close_price = float(bars.iloc[exit_index]["close"])
        if direction == "BUY":
            exit_fill = close_price - half_spread - slip
        else:
            exit_fill = close_price + half_spread + slip

    # -----------------------------------------------------------------------
    # PnL and R
    # -----------------------------------------------------------------------
    if direction == "BUY":
        pnl_price    = exit_fill - entry_fill
        risk_per_unit = entry_fill - sl
    else:
        pnl_price    = entry_fill - exit_fill
        risk_per_unit = sl - entry_fill

    # risk_per_unit should be > 0 by construction (validated signal + adverse entry fill)
    r_multiple = pnl_price / risk_per_unit

    # -----------------------------------------------------------------------
    # Build result
    # -----------------------------------------------------------------------
    return TradeResult(
        direction   = direction,
        entry_index = entry_idx,
        entry_time  = bars.iloc[entry_idx]["time"],
        entry_price = entry_fill,
        exit_index  = exit_index,
        exit_time   = bars.iloc[exit_index]["time"],
        exit_price  = exit_fill,
        exit_reason = exit_reason,
        pnl_price   = pnl_price,
        r_multiple  = r_multiple,
        bars_held   = exit_index - entry_idx + 1,
    )


def simulate(
    bars: pd.DataFrame,
    signals: list[Signal],
    *,
    half_spread: float = 0.15,
    slip: float = 0.05,
    max_hold_bars: int | None = None,
) -> list[TradeResult]:
    """Simulate a list of signals independently against the same bar series.

    Each signal is evaluated independently — there is no position netting or
    concurrent-trade interaction.  Position sizing and portfolio aggregation
    are handled by the risk layer in a later phase.

    Parameters
    ----------
    bars:
        Mid-OHLCV DataFrame (same as :func:`simulate_trade`).
    signals:
        List of :class:`Signal` instances.  May be empty.
    half_spread:
        Half the assumed bid/ask spread.  Passed through to
        :func:`simulate_trade`.
    slip:
        One-way market slippage.  Passed through to :func:`simulate_trade`.
    max_hold_bars:
        Maximum hold duration.  Passed through to :func:`simulate_trade`.

    Returns
    -------
    List of :class:`TradeResult`, one per signal, in the same order as
    ``signals``.
    """
    return [
        simulate_trade(bars, sig, half_spread=half_spread, slip=slip, max_hold_bars=max_hold_bars)
        for sig in signals
    ]
