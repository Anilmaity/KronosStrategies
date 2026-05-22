"""
s90_asia_range.py
-----------------
Setup Family C: Asia Range Break-and-Retest (XAU/USD 90%-winrate research).

Strategy logic (5-minute bars, tz-naive UTC):
  1. Asia range: high & low of the 00:00–05:55 UTC bars (bars whose time.hour < 6).
     The range is FINALISED at 06:00 UTC — no London bar is used to define it.
  2. London session (07:00–11:00 UTC, weekdays only):
       * If a bar CLOSES beyond Asia high (close > asia_high) → break-up event.
         Sweep extreme = rolling max of bar highs during the break.
         When a subsequent bar CLOSES back at or below asia_high (retest+rejection):
           → SHORT signal: entry_index = retest_bar_index + 1
             SL  = sweep_extreme + SL_BUFFER_DEFAULT
             TP  = asia_low
             Skip if R:R < MIN_RR.
       * If a bar CLOSES below Asia low (close < asia_low) → break-down event.
         Sweep extreme = rolling min of bar lows during the break.
         When a subsequent bar CLOSES back at or above asia_low (retest+reclaim):
           → BUY signal: entry_index = retest_bar_index + 1
             SL  = sweep_extreme - SL_BUFFER_DEFAULT
             TP  = asia_high
             Skip if R:R < MIN_RR.
  3. At most one signal per day (first valid setup wins). State resets each day.
  4. entry_index must be within the bar array (< len(bars)); if entry bar would
     fall out of range, the signal is dropped.

Look-ahead guarantee
---------------------
All decisions at index k use only bars[0..k].  The Asia range is computed from
bars whose time.hour < 6 (strictly before 06:00 UTC), so it cannot include any
London bar.  Signals emit with entry_index = k+1; the caller (exec_sim) fills at
bars.iloc[k+1].open.

Parameters
----------
MIN_RR : float
    Minimum reward/risk ratio required to emit a signal. Default 1.5.
SL_BUFFER_DEFAULT : float
    Price units added beyond the sweep extreme for the stop-loss. Default 0.50.
MAX_HOLD_BARS : int
    Maximum number of bars to hold the trade. Default 48 (~4 hours on 5m).
"""
from __future__ import annotations

import json
import os
from typing import Any

import pandas as pd

from strategies.research.exec_sim import Signal, simulate

# ---------------------------------------------------------------------------
# Public constants (imported by tests and main)
# ---------------------------------------------------------------------------
MIN_RR: float = 1.5
SL_BUFFER_DEFAULT: float = 0.50
MAX_HOLD_BARS: int = 48

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_weekday(ts: pd.Timestamp) -> bool:
    """Return True for Monday–Friday (dayofweek 0–4)."""
    return ts.dayofweek < 5


def _compute_asia_range(day_bars: pd.DataFrame) -> tuple[float, float] | None:
    """
    Compute Asia range from bars whose time.hour < 6 (00:00–05:55 UTC).
    Returns (asia_high, asia_low) or None if no Asia bars found.
    """
    asia = day_bars[day_bars["time"].dt.hour < 6]
    if asia.empty:
        return None
    return float(asia["high"].max()), float(asia["low"].min())


def _rr_ok(entry_open: float, sl: float, tp: float, direction: str) -> bool:
    """Return True if the R:R ratio >= MIN_RR."""
    if direction == "SELL":
        risk   = sl - entry_open
        reward = entry_open - tp
    else:  # BUY
        risk   = entry_open - sl
        reward = tp - entry_open
    if risk <= 0 or reward <= 0:
        return False
    return (reward / risk) >= MIN_RR


# ---------------------------------------------------------------------------
# Core signal generator
# ---------------------------------------------------------------------------

def generate_signals(
    bars: pd.DataFrame,
    sl_buffer: float = SL_BUFFER_DEFAULT,
    min_rr: float = MIN_RR,
) -> list[Signal]:
    """
    Scan the full bar series and return a list of Signal objects for the
    Asia Range Break-and-Retest strategy.

    Parameters
    ----------
    bars:
        Mid-OHLCV DataFrame with columns: time, open, high, low, close,
        volume, spread.  ``time`` must be tz-naive datetime64[ns].
    sl_buffer:
        Price units added beyond sweep extreme for the SL. Default 0.50.
    min_rr:
        Minimum reward/risk ratio. Default MIN_RR (1.5).

    Returns
    -------
    List of Signal instances (may be empty).

    LOOK-AHEAD SELF-AUDIT
    ----------------------
    At every bar index k:
      * asia_high / asia_low are derived from bars whose time.hour < 6.
        Since London entries only fire when bar k has time.hour in [7,11),
        and 7 > 6, the Asia range is always frozen before any signal-generating
        bar is processed. ✓
      * sweep_extreme is updated from bar[k].high (or bar[k].low), which is the
        current bar being processed — no future bar is touched. ✓
      * entry_index = k + 1.  The entry bar's open is used by exec_sim; we never
        read that bar's values to make the decision. ✓
      * The day split uses bars["time"].dt.date, which is derived from each bar's
        own timestamp — no look-ahead. ✓
    """
    signals: list[Signal] = []

    if bars.empty:
        return signals

    # Group by UTC date for day-level state management
    bars = bars.copy()
    bars["_date"] = bars["time"].dt.date

    for date, day_df in bars.groupby("_date", sort=True):
        day_df = day_df.reset_index(drop=True)

        # Skip weekends (should not occur in live data but guard synthetic tests)
        first_ts = day_df.iloc[0]["time"]
        if not _is_weekday(first_ts):
            continue

        # --- compute Asia range (bars with hour < 6) ---
        range_result = _compute_asia_range(day_df)
        if range_result is None:
            continue
        asia_high, asia_low = range_result

        # --- London session state machine ---
        # States: "idle", "broke_up", "broke_down"
        state = "idle"
        sweep_extreme: float = 0.0
        fired_today = False

        for i in range(len(day_df)):
            if fired_today:
                break

            bar = day_df.iloc[i]
            ts: pd.Timestamp = bar["time"]
            hour = ts.hour

            # Only process London window bars for entries: 07:00–10:55 UTC
            # (entry fires at next bar; next bar's open must be available, so
            # we process retest bars up through 10:55 and entry is at 11:00 at latest)
            in_london = (7 <= hour < 11) and _is_weekday(ts)
            if not in_london:
                # Still update sweep extreme if we're mid-sequence but outside
                # London window → reset state (no entry allowed)
                if state != "idle" and hour >= 11:
                    state = "idle"
                continue

            bar_close = float(bar["close"])
            bar_high  = float(bar["high"])
            bar_low   = float(bar["low"])

            # Ensure entry bar (i+1) exists in day_df
            if i + 1 >= len(day_df):
                break

            # --- state transitions ---
            if state == "idle":
                if bar_close > asia_high:
                    # Break up
                    state = "broke_up"
                    sweep_extreme = bar_high
                elif bar_close < asia_low:
                    # Break down
                    state = "broke_down"
                    sweep_extreme = bar_low

            elif state == "broke_up":
                # Track sweep extreme (highest high during the break)
                if bar_high > sweep_extreme:
                    sweep_extreme = bar_high

                if bar_close <= asia_high:
                    # Retest + rejection confirmed → SHORT
                    # entry_index in the global bar DataFrame = day_df.iloc[i+1]["time"]
                    entry_bar_local = day_df.iloc[i + 1]
                    entry_open = float(entry_bar_local["open"])

                    sl = sweep_extreme + sl_buffer
                    tp = asia_low

                    # Find entry_index in the original (full) bars DataFrame
                    # We need the positional index in the passed-in `bars` DataFrame.
                    # Since we reset_index, we use the time to look up.
                    entry_time = entry_bar_local["time"]
                    global_mask = bars["time"] == entry_time
                    global_matches = bars.index[global_mask]
                    if len(global_matches) == 0:
                        state = "idle"
                        continue
                    entry_idx_global = int(global_matches[0])

                    # Validate R:R using entry bar open
                    if _rr_ok(entry_open, sl, tp, "SELL"):
                        signals.append(Signal(
                            direction="SELL",
                            entry_index=entry_idx_global,
                            sl=sl,
                            tp=tp,
                        ))
                        fired_today = True
                    else:
                        state = "idle"

            elif state == "broke_down":
                # Track sweep extreme (lowest low during the break)
                if bar_low < sweep_extreme:
                    sweep_extreme = bar_low

                if bar_close >= asia_low:
                    # Retest + reclaim confirmed → LONG
                    entry_bar_local = day_df.iloc[i + 1]
                    entry_open = float(entry_bar_local["open"])

                    sl = sweep_extreme - sl_buffer
                    tp = asia_high

                    entry_time = entry_bar_local["time"]
                    global_mask = bars["time"] == entry_time
                    global_matches = bars.index[global_mask]
                    if len(global_matches) == 0:
                        state = "idle"
                        continue
                    entry_idx_global = int(global_matches[0])

                    if _rr_ok(entry_open, sl, tp, "BUY"):
                        signals.append(Signal(
                            direction="BUY",
                            entry_index=entry_idx_global,
                            sl=sl,
                            tp=tp,
                        ))
                        fired_today = True
                    else:
                        state = "idle"

    return signals


# ---------------------------------------------------------------------------
# Metrics helpers
# ---------------------------------------------------------------------------

def _compute_metrics(
    results: list[Any],
    bars: pd.DataFrame,
) -> dict:
    """Compute the required metrics dict from TradeResult list."""
    import math
    from collections import defaultdict

    n = len(results)
    if n == 0:
        return {
            "n_trades": 0,
            "winrate": 0.0,
            "expectancy_R": 0.0,
            "avg_win_R": 0.0,
            "avg_loss_R": 0.0,
            "profit_factor": 0.0,
            "max_dd_R": 0.0,
            "trades_per_day": 0.0,
            "by_hour": {},
        }

    rs = [t.r_multiple for t in results]
    wins  = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]

    winrate      = len(wins) / n
    expectancy_R = sum(rs) / n
    avg_win_R    = sum(wins)  / len(wins)  if wins   else 0.0
    avg_loss_R   = sum(losses) / len(losses) if losses else 0.0
    gross_profit = sum(wins)
    gross_loss   = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # max drawdown in R
    peak = 0.0
    cumulative = 0.0
    max_dd_R = 0.0
    for r in rs:
        cumulative += r
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd_R:
            max_dd_R = dd

    # trades_per_day: based on calendar trading days in IS window
    total_days = (bars["time"].max() - bars["time"].min()).days + 1
    trading_days = total_days * 5 / 7  # approximate weekdays
    trades_per_day = n / trading_days if trading_days > 0 else 0.0

    # by_hour: use entry_time from results
    by_hour: dict[int, dict] = defaultdict(lambda: {"trades": 0, "wins": 0})
    for t in results:
        h = pd.Timestamp(t.entry_time).hour
        by_hour[h]["trades"] += 1
        if t.r_multiple > 0:
            by_hour[h]["wins"] += 1

    by_hour_out = {}
    for h in sorted(by_hour.keys()):
        td = by_hour[h]["trades"]
        wr = by_hour[h]["wins"] / td if td > 0 else 0.0
        by_hour_out[str(h)] = {"trades": td, "winrate": round(wr, 4)}

    return {
        "n_trades":       n,
        "winrate":        round(winrate, 4),
        "expectancy_R":   round(expectancy_R, 4),
        "avg_win_R":      round(avg_win_R, 4),
        "avg_loss_R":     round(avg_loss_R, 4),
        "profit_factor":  round(profit_factor, 4),
        "max_dd_R":       round(max_dd_R, 4),
        "trades_per_day": round(trades_per_day, 4),
        "by_hour":        by_hour_out,
    }


# ---------------------------------------------------------------------------
# Main: backtest on IS bars + write JSON results
# ---------------------------------------------------------------------------

def run_backtest_and_save(
    output_path: str = "strategies/backtest/results/s90_asia_range_20260522.json",
) -> dict:
    from strategies.research.dataset import load_is_bars

    print("[s90_asia_range] Loading IS 5m bars...")
    bars = load_is_bars("5m")
    print(f"[s90_asia_range] {len(bars):,} bars loaded, "
          f"range {bars['time'].min()} to {bars['time'].max()}")

    print("[s90_asia_range] Generating signals...")
    signals = generate_signals(bars)
    print(f"[s90_asia_range] {len(signals)} signals generated")

    print("[s90_asia_range] Simulating trades...")
    results = simulate(
        bars,
        signals,
        half_spread=0.15,
        slip=0.05,
        max_hold_bars=MAX_HOLD_BARS,
    )
    print(f"[s90_asia_range] {len(results)} trades simulated")

    metrics = _compute_metrics(results, bars)

    params = {
        "timeframe": "5m",
        "asia_session_utc": "00:00–05:55",
        "london_session_utc": "07:00–10:55",
        "sl_buffer": SL_BUFFER_DEFAULT,
        "min_rr": MIN_RR,
        "max_hold_bars": MAX_HOLD_BARS,
        "half_spread": 0.15,
        "slip": 0.05,
    }

    output = {
        "family": "C — Asia Range Break-and-Retest",
        "params": params,
        **metrics,
    }

    # Ensure results directory exists
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w") as fh:
        json.dump(output, fh, indent=2)

    print(f"\n[s90_asia_range] Results written to: {output_path}")
    print(json.dumps(output, indent=2))
    return output


if __name__ == "__main__":
    run_backtest_and_save()
