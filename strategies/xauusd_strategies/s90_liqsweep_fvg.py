"""
s90_liqsweep_fvg.py
--------------------
Setup Family A — Liquidity Sweep of Session Extreme + FVG Fade.

Strategy logic (ICT-derived):
1. Entry timeframe: 5m. HTF context: 4H (closed bars only).
2. Session: London [07,11) UTC or NY [12,16) UTC, weekdays only.
   Running session high/low reset at each session start.
3. Detect a SWEEP of the session high (Buy-Side Liquidity, BSL) or session
   low (Sell-Side Liquidity, SSL) at bar k (bar must be CLOSED):
     BSL sweep: bar.high > running_session_high AND bar.close < running_session_high
     SSL sweep: bar.low  < running_session_low  AND bar.close > running_session_low
4. Require an aligned FVG (3-candle ICT definition) within the last
   FVG_LOOKBACK bars, near the swept level:
     Bearish FVG at index i: bars.low[i-2] > bars.high[i]
                              zone = [bars.high[i], bars.low[i-2]]
     Bullish FVG at index i: bars.high[i-2] < bars.low[i]
                              zone = [bars.high[i-2], bars.low[i]]
   "Near the swept level" means the FVG zone overlaps or is adjacent to the
   swept extreme within FVG_PROXIMITY ticks.
   For a BSL sweep (short): look for a bearish FVG in bars [k-FVG_LOOKBACK, k].
   For an SSL sweep (long): look for a bullish FVG in bars [k-FVG_LOOKBACK, k].
5. Require 4H HTF bias to ALIGN with the fade:
     Short signal: 4H bias must be "bearish"
     Long signal:  4H bias must be "bullish"
   Bias rule: compare the last two CLOSED 4H swing points
   (last two swing highs and last two swing lows):
     Bearish = lower-high AND lower-low
     Bullish = higher-high AND higher-low
     else = neutral (no trade)
   "Closed" means 4H bar time <= bar k time (no look-ahead).
6. Entry at open of bar k+1.
   SL = swept extreme + SL_BUFFER (short) or swept extreme - SL_BUFFER (long).
   TP = opposite session extreme (session low for shorts, session high for longs).
   Minimum R:R = MIN_RR (default 1.0); if TP gives < MIN_RR, skip.
7. max_hold_bars = 48 (≈ 4 hours of 5m bars).

Look-ahead guarantee
--------------------
At decision point bar k (0-indexed):
  - Only bars 0..k are used.
  - HTF bars are filtered: only 4H bars with time <= bars.iloc[k]["time"].
  - Session extreme is built from bars 0..k-1 (NOT including bar k's contribution
    to the running extreme; bar k is the sweep bar itself).
  - entry_index = k + 1 always.

No look-ahead is possible because:
  - We never read bars[k+1..] during detection.
  - HTF filter is strict: time <= current bar time.
  - Session running high/low is updated with bar k-1 before processing bar k.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from strategies.research.exec_sim import Signal, simulate

# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------
LONDON_HOURS = range(7, 11)   # [07, 11) UTC
NY_HOURS = range(12, 16)      # [12, 16) UTC
FVG_LOOKBACK = 10             # look back this many bars for an aligned FVG
FVG_PROXIMITY = 5.0           # max distance (price units) from FVG zone to swept level
SL_BUFFER = 0.50              # ticks beyond the sweep extreme for stop-loss
MIN_RR = 1.0                  # minimum risk:reward ratio to emit a signal
MAX_HOLD_BARS = 48            # ≈ 4 hours of 5m bars


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def _in_session(hour: int) -> bool:
    """Return True if the hour is in London or NY session."""
    return hour in LONDON_HOURS or hour in NY_HOURS


def _session_id(bar_time: pd.Timestamp) -> Optional[str]:
    """Return a session identifier string for grouping, or None if outside sessions."""
    h = bar_time.hour
    d = bar_time.date()
    if h in LONDON_HOURS:
        return f"{d}_london"
    if h in NY_HOURS:
        return f"{d}_ny"
    return None


# ---------------------------------------------------------------------------
# HTF bias
# ---------------------------------------------------------------------------

def _htf_bias(htf_bars: pd.DataFrame, as_of: pd.Timestamp) -> str:
    """Determine the 4H bias using the last two swing highs and lows.

    Only 4H bars with time STRICTLY <= as_of are considered (closed bars only,
    no look-ahead).

    A simple, defensible operationalization:
      - Take the last 4 closed 4H bars.
      - Compare swing1 = bars[-4:-2] (earlier swing) vs swing2 = bars[-2:] (later swing).
      - swing_high = max(high) of that 2-bar group, swing_low = min(low).
      - If swing2_high < swing1_high AND swing2_low < swing1_low -> bearish
      - If swing2_high > swing1_high AND swing2_low > swing1_low -> bullish
      - else -> neutral

    This captures the directional bias from the most recent completed structure
    without requiring a full swing-point algorithm.
    """
    closed = htf_bars[htf_bars["time"] <= as_of]
    if len(closed) < 2:
        return "neutral"

    # Compare the most recent half vs the prior half (need at least 2 bars).
    # With n bars: s1 = closed[:n//2], s2 = closed[n//2:]
    # Minimum 2 bars: s1 = bars[-2:-1], s2 = bars[-1:]
    # For 4+ bars: take last 4, split into two 2-bar swing groups.
    if len(closed) >= 4:
        recent = closed.iloc[-4:]
        s1 = recent.iloc[:2]
        s2 = recent.iloc[2:]
    else:
        # 2 or 3 bars: compare last bar vs earlier bars
        s1 = closed.iloc[:-1]
        s2 = closed.iloc[-1:]

    s1_high = float(s1["high"].max())
    s1_low  = float(s1["low"].min())
    s2_high = float(s2["high"].max())
    s2_low  = float(s2["low"].min())

    if s2_high < s1_high and s2_low < s1_low:
        return "bearish"
    if s2_high > s1_high and s2_low > s1_low:
        return "bullish"
    return "neutral"


# ---------------------------------------------------------------------------
# FVG detection helpers
# ---------------------------------------------------------------------------

def _find_bearish_fvg_near(
    bars: pd.DataFrame,
    k: int,
    swept_high: float,
    lookback: int = FVG_LOOKBACK,
    proximity: float = FVG_PROXIMITY,
) -> Optional[tuple[float, float]]:
    """Search for a bearish FVG in bars[max(0, k-lookback) .. k] near swept_high.

    Bearish FVG at index i: bars.low[i-2] > bars.high[i]
    Zone: (bars.high[i], bars.low[i-2])  — the gap area price may return into.

    "Near" means: the FVG zone overlaps or is within proximity of swept_high.
    We return the first matching FVG zone (closest to swept_high if multiple).

    Returns (fvg_low, fvg_high) or None.
    """
    start = max(2, k - lookback)
    best: Optional[tuple[float, float]] = None
    best_dist = float("inf")

    for i in range(start, k + 1):
        low_i_minus_2 = float(bars.iloc[i - 2]["low"])
        high_i        = float(bars.iloc[i]["high"])
        if low_i_minus_2 > high_i:
            # Bearish FVG: zone = [high_i, low_i_minus_2]
            fvg_low  = high_i
            fvg_high = low_i_minus_2
            # Compute distance from swept_high to FVG zone
            if fvg_low <= swept_high <= fvg_high:
                dist = 0.0  # swept_high is inside the FVG zone
            else:
                dist = min(abs(swept_high - fvg_low), abs(swept_high - fvg_high))
            if dist <= proximity and dist < best_dist:
                best = (fvg_low, fvg_high)
                best_dist = dist

    return best


def _find_bullish_fvg_near(
    bars: pd.DataFrame,
    k: int,
    swept_low: float,
    lookback: int = FVG_LOOKBACK,
    proximity: float = FVG_PROXIMITY,
) -> Optional[tuple[float, float]]:
    """Search for a bullish FVG in bars[max(0, k-lookback) .. k] near swept_low.

    Bullish FVG at index i: bars.high[i-2] < bars.low[i]
    Zone: (bars.high[i-2], bars.low[i])  — the gap area.

    Returns (fvg_low, fvg_high) or None.
    """
    start = max(2, k - lookback)
    best: Optional[tuple[float, float]] = None
    best_dist = float("inf")

    for i in range(start, k + 1):
        high_i_minus_2 = float(bars.iloc[i - 2]["high"])
        low_i          = float(bars.iloc[i]["low"])
        if high_i_minus_2 < low_i:
            # Bullish FVG: zone = [high_i_minus_2, low_i]
            fvg_low  = high_i_minus_2
            fvg_high = low_i
            # Distance from swept_low to FVG zone
            if fvg_low <= swept_low <= fvg_high:
                dist = 0.0
            else:
                dist = min(abs(swept_low - fvg_low), abs(swept_low - fvg_high))
            if dist <= proximity and dist < best_dist:
                best = (fvg_low, fvg_high)
                best_dist = dist

    return best


# ---------------------------------------------------------------------------
# Signal detector — main entrypoint
# ---------------------------------------------------------------------------

def detect_signals(
    bars: pd.DataFrame,
    htf_bars: pd.DataFrame,
    *,
    fvg_lookback: int = FVG_LOOKBACK,
    fvg_proximity: float = FVG_PROXIMITY,
    sl_buffer: float = SL_BUFFER,
    min_rr: float = MIN_RR,
) -> list[Signal]:
    """Detect Liquidity Sweep + FVG Fade signals on 5m bars.

    Parameters
    ----------
    bars:
        5-minute OHLCV DataFrame (tz-naive UTC), columns:
        time, open, high, low, close, volume, spread.
    htf_bars:
        4H OHLCV DataFrame (tz-naive UTC), same columns.
    fvg_lookback:
        Number of bars to look back for an aligned FVG.
    fvg_proximity:
        Max price distance from swept level to FVG zone.
    sl_buffer:
        Extra distance beyond the swept extreme for the stop-loss.
    min_rr:
        Minimum risk:reward ratio; below this, skip the trade.

    Returns
    -------
    List of Signal objects (empty if no setups found).
    """
    signals: list[Signal] = []

    # Track session state
    current_session: Optional[str] = None
    session_high: float = -float("inf")
    session_low:  float = float("inf")

    n = len(bars)

    for k in range(1, n - 1):
        bar   = bars.iloc[k]
        bar_t = bar["time"]

        # --- Session management ---
        # Weekday filter
        if bar_t.weekday() >= 5:
            current_session = None
            continue

        sess = _session_id(bar_t)

        if sess != current_session:
            # New session: reset extremes using only bars BEFORE k (bars 0..k-1)
            current_session = sess
            if sess is None:
                session_high = -float("inf")
                session_low  = float("inf")
                continue
            # Initialise from previous bars within this new session
            # (find the bars in this session before bar k)
            prev_in_session = [
                bars.iloc[j]
                for j in range(k)
                if _session_id(bars.iloc[j]["time"]) == sess
                   and bars.iloc[j]["time"].weekday() < 5
            ]
            if prev_in_session:
                session_high = max(float(b["high"]) for b in prev_in_session)
                session_low  = min(float(b["low"])  for b in prev_in_session)
            else:
                session_high = -float("inf")
                session_low  = float("inf")
        else:
            # Same session: update running extremes with bar k-1 (already closed)
            prev_bar = bars.iloc[k - 1]
            if _session_id(prev_bar["time"]) == current_session:
                session_high = max(session_high, float(prev_bar["high"]))
                session_low  = min(session_low,  float(prev_bar["low"]))

        # Skip if no valid session or session hasn't formed an extreme yet
        if sess is None:
            continue
        if session_high == -float("inf") or session_low == float("inf"):
            continue

        # --- Only emit entries during London or NY session ---
        if not _in_session(bar_t.hour):
            continue

        bar_high  = float(bar["high"])
        bar_low   = float(bar["low"])
        bar_close = float(bar["close"])

        # --- BSL sweep detection (→ SHORT / SELL) ---
        bsl_sweep = (bar_high > session_high) and (bar_close < session_high)

        # --- SSL sweep detection (→ LONG / BUY) ---
        ssl_sweep = (bar_low < session_low) and (bar_close > session_low)

        # Process BSL (short) sweep
        if bsl_sweep:
            swept_high = session_high  # the level that was swept

            # 4H bias must be bearish
            bias = _htf_bias(htf_bars, bar_t)
            if bias != "bearish":
                # After processing, update session high with this bar
                session_high = max(session_high, bar_high)
                session_low  = min(session_low,  bar_low)
                continue

            # Look for a bearish FVG near the swept level in recent bars
            fvg = _find_bearish_fvg_near(bars, k, swept_high, fvg_lookback, fvg_proximity)
            if fvg is None:
                session_high = max(session_high, bar_high)
                session_low  = min(session_low,  bar_low)
                continue

            # Entry at next bar's open
            entry_idx = k + 1
            if entry_idx >= n:
                continue

            entry_open = float(bars.iloc[entry_idx]["open"])

            # SL: above sweep high + buffer
            sl = bar_high + sl_buffer

            # TP: session low (opposite extreme)
            tp = session_low

            # Validate geometry: tp < entry_open < sl
            if not (tp < entry_open < sl):
                session_high = max(session_high, bar_high)
                session_low  = min(session_low,  bar_low)
                continue

            # Minimum R:R check
            risk   = sl - entry_open
            reward = entry_open - tp
            if risk <= 0 or reward / risk < min_rr:
                session_high = max(session_high, bar_high)
                session_low  = min(session_low,  bar_low)
                continue

            signals.append(Signal(direction="SELL", entry_index=entry_idx, sl=sl, tp=tp))

        # Process SSL (long) sweep
        elif ssl_sweep:
            swept_low = session_low

            # 4H bias must be bullish
            bias = _htf_bias(htf_bars, bar_t)
            if bias != "bullish":
                session_high = max(session_high, bar_high)
                session_low  = min(session_low,  bar_low)
                continue

            # Look for a bullish FVG near the swept level
            fvg = _find_bullish_fvg_near(bars, k, swept_low, fvg_lookback, fvg_proximity)
            if fvg is None:
                session_high = max(session_high, bar_high)
                session_low  = min(session_low,  bar_low)
                continue

            # Entry at next bar's open
            entry_idx = k + 1
            if entry_idx >= n:
                continue

            entry_open = float(bars.iloc[entry_idx]["open"])

            # SL: below sweep low - buffer
            sl = bar_low - sl_buffer

            # TP: session high (opposite extreme)
            tp = session_high

            # Validate geometry: sl < entry_open < tp
            if not (sl < entry_open < tp):
                session_high = max(session_high, bar_high)
                session_low  = min(session_low,  bar_low)
                continue

            # Minimum R:R check
            risk   = entry_open - sl
            reward = tp - entry_open
            if risk <= 0 or reward / risk < min_rr:
                session_high = max(session_high, bar_high)
                session_low  = min(session_low,  bar_low)
                continue

            signals.append(Signal(direction="BUY", entry_index=entry_idx, sl=sl, tp=tp))

        # Update running session extremes with the current bar (now closed)
        session_high = max(session_high, bar_high)
        session_low  = min(session_low,  bar_low)

    return signals


# ---------------------------------------------------------------------------
# Backtest runner
# ---------------------------------------------------------------------------

def run_backtest(
    bars_5m: pd.DataFrame,
    htf_bars: pd.DataFrame,
    *,
    half_spread: float = 0.15,
    slip: float = 0.05,
    max_hold_bars: int = MAX_HOLD_BARS,
) -> list:
    """Run the full detect + simulate pipeline.

    Returns a list of TradeResult objects.
    """
    signals = detect_signals(bars_5m, htf_bars)
    if not signals:
        return []
    return simulate(
        bars_5m,
        signals,
        half_spread=half_spread,
        slip=slip,
        max_hold_bars=max_hold_bars,
    )


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------

def compute_metrics(results: list, bars_5m: pd.DataFrame) -> dict:
    """Compute standard research metrics from a list of TradeResult objects."""
    import numpy as np

    if not results:
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

    winrate      = len(wins) / len(rs)
    expectancy_R = float(np.mean(rs))
    avg_win_R    = float(np.mean(wins))  if wins   else 0.0
    avg_loss_R   = float(np.mean(losses)) if losses else 0.0
    profit_factor = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else float("inf")

    # Max drawdown of cumulative R equity curve
    cum_r = np.cumsum(rs)
    running_max = np.maximum.accumulate(cum_r)
    drawdowns = running_max - cum_r
    max_dd_R = float(np.max(drawdowns))

    # trades_per_day = n_trades / distinct session-hour dates
    session_mask = bars_5m["time"].apply(
        lambda t: t.weekday() < 5 and _in_session(t.hour)
    )
    session_bars = bars_5m[session_mask]
    distinct_dates = session_bars["time"].dt.date.nunique()
    trades_per_day = len(results) / distinct_dates if distinct_dates > 0 else 0.0

    # by_hour breakdown
    by_hour: dict[int, dict] = {}
    for t in results:
        h = t.entry_time.hour
        if h not in by_hour:
            by_hour[h] = {"trades": 0, "wins": 0}
        by_hour[h]["trades"] += 1
        if t.r_multiple > 0:
            by_hour[h]["wins"] += 1
    by_hour_out = {
        h: {"trades": v["trades"], "winrate": v["wins"] / v["trades"]}
        for h, v in sorted(by_hour.items())
    }

    return {
        "n_trades": len(results),
        "winrate": winrate,
        "expectancy_R": expectancy_R,
        "avg_win_R": avg_win_R,
        "avg_loss_R": avg_loss_R,
        "profit_factor": profit_factor,
        "max_dd_R": max_dd_R,
        "trades_per_day": trades_per_day,
        "by_hour": by_hour_out,
    }


# ---------------------------------------------------------------------------
# Main backtest script
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    import os

    from strategies.research.dataset import load_is_bars

    print("[s90_liqsweep_fvg] Loading IS bars...")
    bars_5m = load_is_bars("5m")
    htf_bars = load_is_bars("4h")

    print(f"[s90_liqsweep_fvg] 5m bars: {len(bars_5m):,}, 4H bars: {len(htf_bars):,}")

    print("[s90_liqsweep_fvg] Detecting signals...")
    results = run_backtest(bars_5m, htf_bars)

    print(f"[s90_liqsweep_fvg] Trades: {len(results)}")

    metrics = compute_metrics(results, bars_5m)

    output = {
        "family": "A_liqsweep_fvg",
        "params": {
            "tf": "5m",
            "htf": "4h",
            "half_spread": 0.15,
            "slip": 0.05,
            "session_windows": ["London 07-11 UTC", "NY 12-16 UTC"],
            "sl_rule": "swept_extreme + 0.50 buffer",
            "tp_rule": "opposite session extreme",
            "min_rr": MIN_RR,
            "fvg_lookback": FVG_LOOKBACK,
            "fvg_proximity": FVG_PROXIMITY,
            "max_hold_bars": MAX_HOLD_BARS,
        },
        **metrics,
    }

    results_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "backtest", "results"
    )
    os.makedirs(results_dir, exist_ok=True)
    out_path = os.path.join(results_dir, "s90_liqsweep_fvg_20260522.json")

    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print("\n=== s90_liqsweep_fvg backtest results ===")
    print(json.dumps(output, indent=2, default=str))
    print(f"\nWritten to: {out_path}")
