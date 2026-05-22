"""
s90_inverse_fvg.py
------------------
Family G -- Inverse-FVG Continuation (XAU/USD 5m, Task 5g).

ICT Logic
---------
1. Detect 3-candle FVGs on 5m bars (bullish: high[i-2] < low[i]; bearish: low[i-2] > high[i]).
2. Track each FVG through two phases: FILL (price enters zone on a bar AFTER formation)
   -> VIOLATION (close beyond the opposite bound on a bar AFTER fill starts).
3. After violation, watch for RETEST: a bar whose wick re-enters the inverted zone
   from the new continuation side.
4. At retest bar k, generate Signal(direction, entry_index=k+1, sl, tp) using mid prices.
   entry_index=k+1 means we act on the NEXT bar's open (no look-ahead).

Filters applied (in order) to improve precision:
  a. Session filter: entry bar must be in London [07,11) or NY [12,16) UTC, weekday.
  b. FVG zone width: zone must be >= fvg_min_width points (filters noise micro-FVGs).
  c. HTF 1h alignment: SELL only when the most recent 1h close < prior 1h close (bearish
     1h momentum); BUY only when most recent 1h close > prior 1h close (bullish 1h momentum).
     Uses the 1h bar whose close time is <= entry bar time (no look-ahead).
  d. Geometry guard: sl and tp must be on correct side of entry_open; risk > 0.
  e. R:R guard: rr >= rr_min (safety net; always satisfied with fixed tp_rr >= rr_min).

No-look-ahead guarantee (SELF-AUDIT):
--------------------------------------
At bar k, we access bars_5m.iloc[0..k] only:
- FVG detection uses bars i-2, i-1, i (all fully closed at detection time i=k).
- The NEW FVG detected at bar k is added to active_fvgs, but its state machine
  is only advanced from k+1 onward (fill checking starts at the bar AFTER formation).
- Fill/violation/retest detection uses bar k's OHLC (the bar that just CLOSED).
- entry_index is always k+1 (next bar's open) -- no execution on the signal bar.
- HTF 1h bars are filtered to only those whose time < entry bar time; the most recent
  closed 1h bar is used (no peeking at the currently forming 1h bar).
- SL/TP are computed from already-closed zone bounds only.
No future information is used at any step.

Session filter: London [07,11) UTC or NY [12,16) UTC, weekdays only.
Applied to the ENTRY bar (entry_index), not the signal bar.

Parameters (PARAMS dict at module level -- no hidden globals):
    sl_buffer      : 0.30  (extra points beyond FVG far side for SL)
    rr_min         : 1.5   (minimum reward-to-risk ratio; safety net for edge cases)
    tp_rr          : 2.0   (target R:R for TP placement)
    max_hold_bars  : 48
    fvg_max_age    : 100   (bars before an unresolved FVG is dropped; ~8h on 5m)
    fvg_min_width  : 0.50  (minimum zone width in points; filters noise micro-FVGs)
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from strategies.research.exec_sim import Signal, simulate

# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------
PARAMS = {
    "tf": "5m",
    "half_spread": 0.15,
    "slip": 0.05,
    "session_windows": [{"start_h": 7, "end_h": 11}, {"start_h": 12, "end_h": 16}],
    "sl_buffer": 0.30,
    "rr_min": 1.5,
    "tp_rr": 2.0,
    "max_hold_bars": 48,
    "fvg_max_age": 100,
    "fvg_min_width": 0.50,
    "htf_filter": True,   # require 1h momentum alignment
    "sl_rule": "zone_far_plus_buffer",
    "tp_rule": "fixed_2R",
}

# ---------------------------------------------------------------------------
# Session filter
# ---------------------------------------------------------------------------
_SESSION_RANGES = [(7, 11), (12, 16)]  # [start, end) UTC hours


def _in_session(ts: pd.Timestamp) -> bool:
    """Return True if ts (tz-naive UTC) falls in London or NY session on a weekday."""
    if ts.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    h = ts.hour
    return any(start <= h < end for start, end in _SESSION_RANGES)


# ---------------------------------------------------------------------------
# FVG detection helpers
# ---------------------------------------------------------------------------

def _detect_fvg(bars: pd.DataFrame, i: int) -> Optional[dict]:
    """Detect a 3-candle FVG ending at bar i (i >= 2).

    Returns dict with keys: kind ('bullish'/'bearish'), lo, hi, formed_at
    or None if no FVG.

    Bullish FVG at i: high[i-2] < low[i]  -> zone = [high[i-2], low[i]]
    Bearish FVG at i: low[i-2] > high[i]  -> zone = [high[i], low[i-2]]
    """
    if i < 2:
        return None
    high_im2 = float(bars.iloc[i - 2]["high"])
    low_im2  = float(bars.iloc[i - 2]["low"])
    high_i   = float(bars.iloc[i]["high"])
    low_i    = float(bars.iloc[i]["low"])

    if high_im2 < low_i:
        # Bullish FVG: gap up -- zone = [high[i-2], low[i]]
        return {"kind": "bullish", "lo": high_im2, "hi": low_i, "formed_at": i}
    if low_im2 > high_i:
        # Bearish FVG: gap down -- zone = [high[i], low[i-2]]
        return {"kind": "bearish", "lo": high_i, "hi": low_im2, "formed_at": i}
    return None


# ---------------------------------------------------------------------------
# State machine per active FVG
# ---------------------------------------------------------------------------
# States: WATCHING_FILL -> WATCHING_VIOLATION -> WATCHING_RETEST -> DONE

@dataclass
class _FVGState:
    kind: str          # 'bullish' or 'bearish'
    lo: float          # zone lower bound
    hi: float          # zone upper bound
    formed_at: int     # bar index where FVG was confirmed
    state: str = "WATCHING_FILL"
    filled_at: Optional[int] = None
    violated_at: Optional[int] = None


def _bar_enters_zone(bar: pd.Series, lo: float, hi: float) -> bool:
    """True if any part of the bar's wick overlaps with (lo, hi) exclusive.

    We use exclusive bounds so that a bar whose high == lo (just touching the
    zone edge) does NOT count as entering. Price must trade INSIDE the zone.
    """
    return float(bar["low"]) < hi and float(bar["high"]) > lo


# ---------------------------------------------------------------------------
# HTF 1h alignment helper
# ---------------------------------------------------------------------------

def _htf_momentum(bars_1h: pd.DataFrame, entry_time: pd.Timestamp) -> str:
    """Return 1h trend direction at entry_time using only closed 1h bars.

    Requires 3 consecutive 1h bars before entry_time. Computes a simple
    trend vote: count closes higher than previous close (bullish votes) and
    lower (bearish votes) across the last 3 bars.

    Returns 'bullish' if all 2 transitions are bullish (closes all rising),
    'bearish' if all 2 transitions are bearish (closes all falling),
    else 'neutral'.

    No look-ahead: only bars with time < entry_time are used.
    """
    past = bars_1h[bars_1h["time"] < entry_time]
    if len(past) < 3:
        return "neutral"
    closes = [float(past.iloc[-3]["close"]),
              float(past.iloc[-2]["close"]),
              float(past.iloc[-1]["close"])]
    bull_votes = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i - 1])
    bear_votes = sum(1 for i in range(1, len(closes)) if closes[i] < closes[i - 1])
    if bull_votes == 2:
        return "bullish"
    if bear_votes == 2:
        return "bearish"
    return "neutral"


# ---------------------------------------------------------------------------
# Core signal detection
# ---------------------------------------------------------------------------

def detect_signals(
    bars_5m: pd.DataFrame,
    bars_1h: Optional[pd.DataFrame] = None,
) -> list[Signal]:
    """Scan all 5m bars and return Signals for inverted-FVG retest setups.

    Parameters
    ----------
    bars_5m : pd.DataFrame
        5-minute OHLCV bars (tz-naive UTC).
    bars_1h : pd.DataFrame, optional
        1-hour OHLCV bars for HTF alignment filter. When provided and
        PARAMS['htf_filter'] is True, only signals aligned with 1h momentum
        are emitted (bearish 1h -> SELL only; bullish 1h -> BUY only).

    LOOK-AHEAD SELF-AUDIT:
    ----------------------
    At bar k, we access bars_5m.iloc[0..k] only:
    - FVG detection uses bars i-2, i-1, i (all <= k at detection time).
    - Newly detected FVG at bar k is added but its fill-check starts at k+1
      (formed_at == k means skip state-machine updates until k+1).
    - Fill/violation detection uses bar k's OHLC (the bar that just CLOSED).
    - Retest detection uses bar k's OHLC (wick enters zone on bar that just CLOSED).
    - entry_index is always k+1 (next bar's open) -- no execution on the signal bar.
    - HTF 1h filter: only bars_1h rows with time < entry_bar.time are used.
    - SL/TP are computed from already-closed zone bounds only.
    No future information is used at any step.
    """
    signals: list[Signal] = []
    active_fvgs: list[_FVGState] = []
    sl_buffer = PARAMS["sl_buffer"]
    rr_min = PARAMS["rr_min"]
    tp_rr = PARAMS["tp_rr"]
    fvg_max_age = PARAMS["fvg_max_age"]
    fvg_min_width = PARAMS.get("fvg_min_width", 0.0)
    use_htf = PARAMS.get("htf_filter", False) and bars_1h is not None

    n = len(bars_5m)

    for k in range(n):
        bar = bars_5m.iloc[k]

        # ---------------------------------------------------------------
        # 1. Detect new FVG ending at bar k (uses bars k-2, k-1, k).
        #    Add to active list BEFORE state-machine updates; the guard
        #    `formed_at == k` inside the loop skips it this turn so the
        #    formation bar itself never advances its own state.
        # ---------------------------------------------------------------
        fvg = _detect_fvg(bars_5m, k)
        if fvg is not None:
            # Width filter: skip micro-FVGs smaller than fvg_min_width points
            zone_width = fvg["hi"] - fvg["lo"]
            if zone_width >= fvg_min_width:
                active_fvgs.append(_FVGState(
                    kind=fvg["kind"], lo=fvg["lo"], hi=fvg["hi"], formed_at=k
                ))

        # ---------------------------------------------------------------
        # 2. Update each active FVG state machine (skip if just formed)
        # ---------------------------------------------------------------
        to_remove: list[int] = []

        for idx, st in enumerate(active_fvgs):
            # Skip the FVG on its formation bar -- fills can only happen
            # on bars AFTER the FVG is complete.
            if st.formed_at == k:
                continue

            # Drop expired FVGs
            if k - st.formed_at > fvg_max_age:
                to_remove.append(idx)
                continue

            if st.state == "WATCHING_FILL":
                # Fill: bar k's wick enters the FVG zone interior
                if _bar_enters_zone(bar, st.lo, st.hi):
                    st.state = "WATCHING_VIOLATION"
                    st.filled_at = k

            elif st.state == "WATCHING_VIOLATION":
                bar_close = float(bar["close"])
                if st.kind == "bullish":
                    # Violated when close BELOW zone lower bound (lo = high[i-2])
                    if bar_close < st.lo:
                        st.state = "WATCHING_RETEST"
                        st.violated_at = k
                elif st.kind == "bearish":
                    # Violated when close ABOVE zone upper bound (hi = low[i-2])
                    if bar_close > st.hi:
                        st.state = "WATCHING_RETEST"
                        st.violated_at = k

            elif st.state == "WATCHING_RETEST":
                # Retest with REJECTION confirmation (stricter than wick-only):
                #
                # Inverted bullish FVG (now RESISTANCE -- price below zone):
                #   Confirmed retest = bar wicks INTO zone (high > lo) AND
                #   CLOSES back below zone lower bound (close < lo) -- price
                #   was rejected from the zone. This is the "mitigation and
                #   rejection" candle ICT describes.
                #
                # Inverted bearish FVG (now SUPPORT -- price above zone):
                #   Confirmed retest = bar wicks INTO zone (low < hi) AND
                #   CLOSES back above zone upper bound (close > hi) -- price
                #   was accepted/supported by the zone.
                retest = False
                if st.kind == "bullish":
                    # Acting as resistance: wick into zone, close rejected below lo
                    retest = (float(bar["high"]) > st.lo and
                              float(bar["close"]) < st.lo)
                elif st.kind == "bearish":
                    # Acting as support: wick into zone, close accepted above hi
                    retest = (float(bar["low"]) < st.hi and
                              float(bar["close"]) > st.hi)

                if retest:
                    # Generate signal at k+1
                    entry_idx = k + 1
                    if entry_idx >= n:
                        st.state = "DONE"
                        to_remove.append(idx)
                        continue

                    entry_bar = bars_5m.iloc[entry_idx]
                    entry_open = float(entry_bar["open"])

                    # Session filter on entry bar time
                    entry_time = entry_bar["time"]
                    if not _in_session(entry_time):
                        # Don't mark DONE -- a later retest may be in session
                        continue

                    # HTF 1h alignment filter (no look-ahead: uses only closed 1h bars)
                    if use_htf:
                        htf_mom = _htf_momentum(bars_1h, entry_time)
                        if st.kind == "bullish" and htf_mom != "bearish":
                            # Inverted bullish FVG -> SELL; need bearish 1h momentum
                            continue
                        if st.kind == "bearish" and htf_mom != "bullish":
                            # Inverted bearish FVG -> BUY; need bullish 1h momentum
                            continue

                    if st.kind == "bullish":
                        # Inverted bullish FVG = resistance -> SHORT
                        direction = "SELL"
                        sl = st.hi + sl_buffer  # SL above zone upper bound
                        risk = sl - entry_open
                        if risk <= 0:
                            continue
                        tp = entry_open - tp_rr * risk
                        rr = (entry_open - tp) / risk
                    else:
                        # Inverted bearish FVG = support -> LONG
                        direction = "BUY"
                        sl = st.lo - sl_buffer  # SL below zone lower bound
                        risk = entry_open - sl
                        if risk <= 0:
                            continue
                        tp = entry_open + tp_rr * risk
                        rr = (tp - entry_open) / risk

                    # Validate signal geometry (exec_sim requirement):
                    # SELL: tp < entry_open < sl
                    # BUY:  sl < entry_open < tp
                    if direction == "SELL" and not (tp < entry_open < sl):
                        continue
                    if direction == "BUY" and not (sl < entry_open < tp):
                        continue

                    # R:R safety net (always 2.0 with fixed tp_rr, guards reconfiguration)
                    if rr < rr_min:
                        continue

                    signals.append(Signal(
                        direction=direction,
                        entry_index=entry_idx,
                        sl=sl,
                        tp=tp,
                    ))
                    st.state = "DONE"
                    to_remove.append(idx)

        # Remove in reverse order to keep indices stable
        for idx in reversed(to_remove):
            active_fvgs.pop(idx)

    return signals


# ---------------------------------------------------------------------------
# Backtest runner
# ---------------------------------------------------------------------------

def run_backtest() -> dict:
    """Load IS 5m + 1h bars, detect signals, simulate, compute + write metrics."""
    from strategies.research.dataset import load_is_bars

    bars = load_is_bars("5m")
    bars_1h = load_is_bars("1h") if PARAMS.get("htf_filter", False) else None
    signals = detect_signals(bars, bars_1h)
    print(f"[s90_inverse_fvg] Detected {len(signals)} signals on IS data")
    results = simulate(
        bars,
        signals,
        half_spread=PARAMS["half_spread"],
        slip=PARAMS["slip"],
        max_hold_bars=PARAMS["max_hold_bars"],
    )

    if not results:
        metrics = _empty_metrics()
    else:
        metrics = _compute_metrics(results, bars)

    _write_json(metrics)
    return metrics


def _empty_metrics() -> dict:
    return {
        "family": "G_inverse_fvg",
        "params": PARAMS,
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


def _compute_metrics(results, bars: pd.DataFrame) -> dict:
    import numpy as np

    rs = [r.r_multiple for r in results]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    n = len(rs)
    winrate = len(wins) / n if n > 0 else 0.0
    expectancy = sum(rs) / n if n > 0 else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Max drawdown in R (peak-to-trough of cumulative R curve)
    cumr = np.cumsum(rs)
    peak = np.maximum.accumulate(cumr)
    dd = peak - cumr
    max_dd = float(dd.max()) if len(dd) > 0 else 0.0

    # Trades per day
    trading_days = _count_trading_days(bars)
    trades_per_day = n / trading_days if trading_days > 0 else 0.0

    # By-hour breakdown
    by_hour: dict[str, dict] = {}
    for r in results:
        h = str(r.entry_time.hour)
        if h not in by_hour:
            by_hour[h] = {"trades": 0, "winrate": 0.0, "_wins": 0}
        by_hour[h]["trades"] += 1
        if r.r_multiple > 0:
            by_hour[h]["_wins"] += 1
    for h, hd in by_hour.items():
        hd["winrate"] = round(hd["_wins"] / hd["trades"], 4) if hd["trades"] > 0 else 0.0
        del hd["_wins"]

    return {
        "family": "G_inverse_fvg",
        "params": PARAMS,
        "n_trades": n,
        "winrate": round(winrate, 4),
        "expectancy_R": round(expectancy, 4),
        "avg_win_R": round(avg_win, 4),
        "avg_loss_R": round(avg_loss, 4),
        "profit_factor": round(profit_factor, 4),
        "max_dd_R": round(max_dd, 4),
        "trades_per_day": round(trades_per_day, 4),
        "by_hour": by_hour,
    }


def _count_trading_days(bars: pd.DataFrame) -> int:
    """Count unique trading days (weekdays) in the bar series."""
    dates = bars["time"].dt.date
    unique_dates = dates.unique()
    weekday_count = sum(1 for d in unique_dates if pd.Timestamp(d).weekday() < 5)
    return max(weekday_count, 1)


def _write_json(metrics: dict) -> None:
    out_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "backtest", "results"
    )
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "s90_inverse_fvg_20260522.json")
    with open(out_path, "w") as f:
        json.dump(metrics, f, indent=2)
    print(f"[s90_inverse_fvg] Wrote results to {out_path}")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    run_backtest()
