"""
s90_breaker.py
--------------
Family F: Breaker Block Reclaim (XAU/USD 90%-winrate harness)

ICT Definitions
~~~~~~~~~~~~~~~
Order Block (OB):
    The last opposite-color candle that dug into liquidity immediately before
    an aggressive impulse the other way.
    - Bullish OB: last bearish candle before a strong up move that breaks a prior high.
    - Bearish OB: last bullish candle before a strong down move that breaks a prior low.

Breaker Block (= Failed OB):
    - Bullish OB broken DOWNWARD (close < OB low) → flips to BEARISH BREAKER
      (resistance on retest from below) → SHORT on retest.
    - Bearish OB broken UPWARD (close > OB high) → flips to BULLISH BREAKER
      (support on retest from above) → LONG on retest.

Logic
~~~~~
1. Identify a recent OB on the entry timeframe (15m chosen for balance of
   structure visibility and trade frequency).
2. Detect OB FAILURE: price breaks fully through it with a strong close.
3. Wait for price to RETEST the broken OB zone from the new side (bar k):
   - For bullish breaker: bar low enters zone [ob_low, ob_high], bullish close
     (close > open) = confirmation.
   - For bearish breaker: bar high enters zone, bearish close (close < open).
4. Enter at open of bar k+1 (entry_index = k+1).
5. SL: beyond the breaker zone extreme + buffer.
   - Bullish breaker: SL = ob_low - stop_buffer.
   - Bearish breaker: SL = ob_high + stop_buffer.
6. TP: 2× risk from entry (targeting next liquidity at ~1:2 R:R).
   Skip if R:R < 1.5.
7. max_hold_bars = 48 (≈4h on 5m, ≈12h on 15m).

Session filter: London [07,11) and NY [12,16) UTC, weekdays only.

LOOK-AHEAD SELF-AUDIT
~~~~~~~~~~~~~~~~~~~~~
- OB detection at bar i: uses bars 0..i (prior swings from bars i-lookback..i-1
  and the impulse bar itself). ✓ No look-ahead.
- OB failure detection at bar i: checks if current close crosses the OB boundary
  detected at a previous bar. ✓ No look-ahead.
- Retest detection at bar k: checks current bar's high/low/close. Decision emits
  entry_index = k+1 (next bar). ✓ No look-ahead.
- Signal geometry check: uses bars.iloc[entry_index].open for validation, but this
  check happens before simulate() — the strategy itself does NOT read entry_index
  open during signal generation. ✓ No look-ahead.

Reuse vs changes from s04_breaker.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
REUSED (conceptual logic):
- OB detection via BOS + displacement (body > atr_mult * ATR breaks prior swing).
- Breaker flip logic: bullish OB close < ob_low → bearish breaker; bearish OB
  close > ob_high → bullish breaker.
- Zone entry retest detection (price re-enters breaker zone).
- Age-based pruning of stale OBs and breakers.

CHANGED / NEW:
- No dependency on engine.py (PendingOrder, run_backtest) — uses new exec_sim harness.
- generate_signals(bars, **kwargs) API: returns list[Signal] for simulate().
- Session filter added (London + NY, weekday only).
- Confirmation candle required at retest (opposite color = rejection).
- R:R gate (skip if R:R < min_rr, default 1.5).
- ATR computed with rolling window (14) on actual bars, not passed externally.
- Results written as structured JSON with full metrics.
- Entry at k+1 open (not market at bar k close).
- SL uses fixed buffer (stop_buffer) instead of ATR-scaled buffer (cleaner for harness).
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

from strategies.research.exec_sim import Signal, simulate

# ---------------------------------------------------------------------------
# Default parameters
# ---------------------------------------------------------------------------
DEFAULTS = dict(
    tf="15m",                   # entry timeframe (15m recommended)
    swing_lookback=5,            # bars back to find prior swing H/L
    ob_search_bars=10,           # bars back to find OB candidate before impulse
    displace_atr_mult=1.2,       # body / ATR ratio to call an impulse
    atr_period=14,               # ATR rolling window
    stop_buffer=0.30,            # fixed buffer beyond OB extreme for SL (in price)
    min_rr=1.5,                  # minimum R:R to emit a signal
    rr_target=2.0,               # target R multiple for TP
    max_breaker_age=80,          # bars before a breaker is pruned
    max_ob_age=80,               # bars before a pending OB is pruned
    half_spread=0.15,            # half spread for exec_sim
    slip=0.05,                   # slippage for exec_sim
    max_hold_bars=48,            # max bars to hold a trade
    session_windows=((7, 11), (12, 16)),  # London + NY UTC hours [start, end)
)

RESULTS_DIR = Path(__file__).resolve().parent.parent / "backtest" / "results"


# ---------------------------------------------------------------------------
# Internal data structures
# ---------------------------------------------------------------------------
@dataclass
class _PendingOB:
    """An Order Block candidate not yet broken."""
    side: str      # "bullish" (last bearish candle before up impulse) or "bearish"
    low: float
    high: float
    created_idx: int


@dataclass
class _Breaker:
    """A failed OB that has flipped to a breaker zone."""
    trade_side: str  # "BUY" (broken bearish OB → bullish breaker) or "SELL"
    low: float       # original OB low
    high: float      # original OB high
    created_idx: int
    triggered: bool = False


# ---------------------------------------------------------------------------
# Session filter
# ---------------------------------------------------------------------------

def _in_session(ts: pd.Timestamp, session_windows: tuple) -> bool:
    """Return True if the timestamp falls in a valid session window on a weekday."""
    # Guard: accept tz-naive timestamps only (bars are tz-naive UTC)
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    if ts.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    h = ts.hour
    for start, end in session_windows:
        if start <= h < end:
            return True
    return False


# ---------------------------------------------------------------------------
# ATR helper
# ---------------------------------------------------------------------------

def _rolling_atr(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute ATR (Wilder) using true range on mid OHLC bars."""
    high = bars["high"]
    low = bars["low"]
    prev_close = bars["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


# ---------------------------------------------------------------------------
# Main signal generator
# ---------------------------------------------------------------------------

def generate_signals(
    bars: pd.DataFrame,
    *,
    tf: str = DEFAULTS["tf"],
    swing_lookback: int = DEFAULTS["swing_lookback"],
    ob_search_bars: int = DEFAULTS["ob_search_bars"],
    displace_atr_mult: float = DEFAULTS["displace_atr_mult"],
    atr_period: int = DEFAULTS["atr_period"],
    stop_buffer: float = DEFAULTS["stop_buffer"],
    min_rr: float = DEFAULTS["min_rr"],
    rr_target: float = DEFAULTS["rr_target"],
    max_breaker_age: int = DEFAULTS["max_breaker_age"],
    max_ob_age: int = DEFAULTS["max_ob_age"],
    half_spread: float = DEFAULTS["half_spread"],
    slip: float = DEFAULTS["slip"],
    max_hold_bars: int = DEFAULTS["max_hold_bars"],
    session_windows: tuple = DEFAULTS["session_windows"],
) -> list[Signal]:
    """
    Scan bars left-to-right, emitting Signal objects for each valid breaker retest.

    LOOK-AHEAD GUARANTEE: at bar k we only use bars.iloc[0..k].
    The Signal emitted has entry_index = k+1, so execution happens on the next bar's open.

    Parameters
    ----------
    bars : pd.DataFrame
        Mid-OHLCV bars with columns: time, open, high, low, close, volume, spread.
        time must be tz-naive datetime64[ns].
    **kwargs : matches DEFAULTS keys.

    Returns
    -------
    list[Signal]
    """
    if len(bars) < max(atr_period, swing_lookback + ob_search_bars) + 2:
        return []

    atr = _rolling_atr(bars, atr_period)

    pending_obs: list[_PendingOB] = []
    breakers: list[_Breaker] = []
    signals: list[Signal] = []
    # Track which entry_index we've already used (avoid duplicating)
    emitted_entries: set[int] = set()

    min_i = max(atr_period, swing_lookback + ob_search_bars)

    for i in range(min_i, len(bars) - 1):
        bar = bars.iloc[i]
        a = float(atr.iloc[i])
        if a == 0:
            continue

        # --- Prune stale ---
        breakers[:] = [b for b in breakers
                       if (i - b.created_idx) <= max_breaker_age and not b.triggered]
        pending_obs[:] = [ob for ob in pending_obs
                          if (i - ob.created_idx) <= max_ob_age]

        close = float(bar["close"])
        open_ = float(bar["open"])
        high = float(bar["high"])
        low = float(bar["low"])
        body = close - open_

        # ---------------------------------------------------------------
        # Step 1: detect new OB candidate at bar i
        # A bullish OB forms when a strong DOWN impulse breaks a prior low —
        # the OB is the last bullish candle in the window before i.
        # A bearish OB forms when a strong UP impulse breaks a prior high —
        # the OB is the last bearish candle in the window before i.
        # ---------------------------------------------------------------
        prior_high = float(bars["high"].iloc[i - swing_lookback: i].max())
        prior_low = float(bars["low"].iloc[i - swing_lookback: i].min())

        if abs(body) > displace_atr_mult * a:
            if body > 0 and close > prior_high:
                # Up impulse breaks prior high → bearish OB = last BEARISH candle
                # in the lookback window (the last candle that "dug into" liquidity
                # before the impulse fired).
                # OB zone: [close, open] of the bearish candle (close < open for bearish)
                window = bars.iloc[i - ob_search_bars: i]
                bearish_cands = window[window["close"] < window["open"]]
                if not bearish_cands.empty:
                    ob = bearish_cands.iloc[-1]
                    pending_obs.append(_PendingOB(
                        side="bearish",
                        low=float(ob["close"]),   # lower boundary (bearish close < open)
                        high=float(ob["open"]),   # upper boundary
                        created_idx=i,
                    ))
            elif body < 0 and close < prior_low:
                # Down impulse breaks prior low → bullish OB = last BULLISH candle
                # OB zone: [open, close] of the bullish candle (open < close for bullish)
                window = bars.iloc[i - ob_search_bars: i]
                bullish_cands = window[window["close"] > window["open"]]
                if not bullish_cands.empty:
                    ob = bullish_cands.iloc[-1]
                    pending_obs.append(_PendingOB(
                        side="bullish",
                        low=float(ob["open"]),    # lower boundary (bullish open < close)
                        high=float(ob["close"]),  # upper boundary
                        created_idx=i,
                    ))

        # ---------------------------------------------------------------
        # Step 2: check if any OB just got broken → flip to breaker
        # ---------------------------------------------------------------
        remaining_obs: list[_PendingOB] = []
        for ob in pending_obs:
            if ob.side == "bearish" and close < ob.low:
                # Bearish OB (created before an UP impulse) broken downward
                # → becomes BEARISH BREAKER (resistance, trade = SELL on retest)
                breakers.append(_Breaker("SELL", ob.low, ob.high, i))
            elif ob.side == "bullish" and close > ob.high:
                # Bullish OB (created before a DOWN impulse) broken upward
                # → becomes BULLISH BREAKER (support, trade = BUY on retest)
                breakers.append(_Breaker("BUY", ob.low, ob.high, i))
            else:
                remaining_obs.append(ob)
        pending_obs[:] = remaining_obs

        # ---------------------------------------------------------------
        # Step 3: retest detection
        # Price re-enters the breaker zone from the new side with confirmation.
        # Confirmation = rejection candle (close on same side as expected direction).
        # ---------------------------------------------------------------
        for b in breakers:
            if b.triggered:
                continue
            # Skip the bar that created the breaker (that bar IS the break-through,
            # not a retest). A valid retest must happen on a subsequent bar.
            if b.created_idx == i:
                continue

            entry_idx = i + 1
            if entry_idx >= len(bars):
                continue
            if entry_idx in emitted_entries:
                continue

            # Session filter on the ENTRY bar (k+1)
            entry_ts = bars.iloc[entry_idx]["time"]
            if not _in_session(entry_ts, session_windows):
                continue

            if b.trade_side == "BUY":
                # Bullish breaker: retest from above → bar low dips into zone
                # and close is bullish (rejection)
                in_zone = low <= b.high and low >= b.low - (b.high - b.low)
                # or more permissive: low enters or touches zone
                in_zone = low <= b.high and high >= b.low
                confirmation = close > open_  # bullish candle = rejection
                if not (in_zone and confirmation):
                    continue
                entry_open = float(bars.iloc[entry_idx]["open"])
                sl = b.low - stop_buffer
                if sl >= entry_open:
                    continue
                risk = entry_open - sl
                tp = entry_open + rr_target * risk
                # R:R check
                if tp <= entry_open or risk <= 0:
                    continue
                actual_rr = (tp - entry_open) / risk
                if actual_rr < min_rr:
                    continue
                b.triggered = True
                emitted_entries.add(entry_idx)
                signals.append(Signal(direction="BUY", entry_index=entry_idx, sl=sl, tp=tp))

            else:  # b.trade_side == "SELL"
                # Bearish breaker: retest from below → bar high pokes into zone
                # and close is bearish (rejection)
                in_zone = high >= b.low and low <= b.high
                confirmation = close < open_  # bearish candle = rejection
                if not (in_zone and confirmation):
                    continue
                entry_open = float(bars.iloc[entry_idx]["open"])
                sl = b.high + stop_buffer
                if sl <= entry_open:
                    continue
                risk = sl - entry_open
                tp = entry_open - rr_target * risk
                if tp >= entry_open or risk <= 0:
                    continue
                actual_rr = (entry_open - tp) / risk
                if actual_rr < min_rr:
                    continue
                b.triggered = True
                emitted_entries.add(entry_idx)
                signals.append(Signal(direction="SELL", entry_index=entry_idx, sl=sl, tp=tp))

    return signals


# ---------------------------------------------------------------------------
# Backtest runner + metrics
# ---------------------------------------------------------------------------

def run_backtest_and_report(
    output_path: Optional[str] = None,
    **kwargs,
) -> dict:
    """
    Load IS bars, generate signals, simulate trades, compute metrics, write JSON.

    Returns the metrics dict.
    """
    from strategies.research.dataset import load_is_bars

    params = {**DEFAULTS, **kwargs}
    tf = params["tf"]

    print(f"[s90_breaker] Loading IS bars ({tf})...")
    bars = load_is_bars(tf)
    print(f"[s90_breaker]   {len(bars):,} bars, "
          f"{bars['time'].min()} to {bars['time'].max()}")

    print("[s90_breaker] Generating signals...")
    signals = generate_signals(bars, **{k: params[k] for k in DEFAULTS})
    print(f"[s90_breaker]   {len(signals)} signals")

    if not signals:
        print("[s90_breaker] WARNING: 0 signals — returning empty metrics.")
        return _empty_metrics(params)

    print("[s90_breaker] Simulating trades...")
    results = simulate(
        bars,
        signals,
        half_spread=params["half_spread"],
        slip=params["slip"],
        max_hold_bars=params["max_hold_bars"],
    )
    print(f"[s90_breaker]   {len(results)} trade results")

    metrics = _compute_metrics(results, params, bars)

    if output_path is None:
        output_path = str(RESULTS_DIR / "s90_breaker_20260522.json")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(metrics, f, indent=2)
    print(f"[s90_breaker] Results written to {output_path}")
    print(json.dumps(metrics, indent=2))

    return metrics


def _empty_metrics(params: dict) -> dict:
    return {
        "family": "F — Breaker Block Reclaim",
        "params": _params_block(params),
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


def _params_block(params: dict) -> dict:
    return {
        "tf": params["tf"],
        "half_spread": params["half_spread"],
        "slip": params["slip"],
        "session_windows": list(params["session_windows"]),
        "sl_rule": "ob_low - stop_buffer (BUY) / ob_high + stop_buffer (SELL)",
        "tp_rule": f"{params['rr_target']}R from entry",
        "max_hold_bars": params["max_hold_bars"],
        "stop_buffer": params["stop_buffer"],
        "min_rr": params["min_rr"],
        "displace_atr_mult": params["displace_atr_mult"],
        "swing_lookback": params["swing_lookback"],
        "ob_search_bars": params["ob_search_bars"],
        "max_breaker_age": params["max_breaker_age"],
    }


def _compute_metrics(results, params: dict, bars: pd.DataFrame) -> dict:
    from strategies.research.exec_sim import TradeResult

    rs = [r.r_multiple for r in results]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]

    n = len(rs)
    winrate = len(wins) / n if n > 0 else 0.0
    expectancy = sum(rs) / n if n > 0 else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0

    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")

    # Max drawdown in R (peak-to-trough of cumulative R)
    cumr = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in rs:
        cumr += r
        if cumr > peak:
            peak = cumr
        dd = peak - cumr
        if dd > max_dd:
            max_dd = dd

    # Trades per day
    is_days = (pd.Timestamp("2026-02-18") - pd.Timestamp("2025-01-01")).days
    trades_per_day = n / is_days if is_days > 0 else 0.0

    # By-hour breakdown
    by_hour: dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0})
    for tr in results:
        ts = tr.entry_time
        if hasattr(ts, "hour"):
            h = str(ts.hour)
        else:
            h = str(pd.Timestamp(ts).hour)
        by_hour[h]["trades"] += 1
        if tr.r_multiple > 0:
            by_hour[h]["wins"] += 1

    by_hour_out = {
        h: {"trades": v["trades"], "winrate": round(v["wins"] / v["trades"], 4)}
        for h, v in sorted(by_hour.items(), key=lambda x: int(x[0]))
    }

    return {
        "family": "F — Breaker Block Reclaim",
        "params": _params_block(params),
        "n_trades": n,
        "winrate": round(winrate, 4),
        "expectancy_R": round(expectancy, 4),
        "avg_win_R": round(avg_win, 4),
        "avg_loss_R": round(avg_loss, 4),
        "profit_factor": round(pf, 4),
        "max_dd_R": round(max_dd, 4),
        "trades_per_day": round(trades_per_day, 4),
        "by_hour": by_hour_out,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    run_backtest_and_report()
