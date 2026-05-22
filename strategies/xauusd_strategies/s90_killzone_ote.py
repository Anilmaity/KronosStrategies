"""
s90_killzone_ote.py
-------------------
Setup Family B: Killzone OTE (Optimal Trade Entry) on XAU/USD.

STRATEGY LOGIC OVERVIEW
-----------------------
Entry TF : 5-minute bars (tz-naive UTC)
Bias TF  : 1-hour bars  (tz-naive UTC, closed bars only)

KILLZONE WINDOWS (UTC, weekdays only):
    London : 08:00 – 10:00  (exclusive end)
    NY     : 13:30 – 15:30  (exclusive end)

HTF BIAS RULE (1H closed bars only):
    Bullish: the last completed 1H bar closes above the prior 1H swing high
             (Break of Structure upward), OR the 1H series shows at least 2
             consecutive higher-highs and higher-lows over the last
             htf_lookback bars.
    Bearish: mirror of bullish — BOS down or 2+ consecutive lower-highs &
             lower-lows.
    Neutral: neither condition → skip entry.

OTE ZONE DEFINITION:
    Identify the last completed impulse swing in the bias direction within
    the 1H bars:
        Bullish → find the most recent swing low then the swing high that
                  followed it (the "leg" goes from low→high).
        Bearish → find the most recent swing high then the swing low that
                  followed it (leg goes from high→low).
    OTE zone = 62%–79% retracement of that leg:
        Bullish OTE low  = leg_high - 0.79 * leg_range
        Bullish OTE high = leg_high - 0.62 * leg_range
        Bearish OTE low  = leg_low  + 0.62 * leg_range
        Bearish OTE high = leg_low  + 0.79 * leg_range
    where leg_range = abs(leg_high - leg_low).

CONFLUENCE REQUIREMENT:
    At least one of these must overlap the OTE zone on the 1H bars:
    • FVG (3-candle imbalance):
        Bullish FVG at bar i: high[i-2] < low[i]   (gap between i-2 high and i low)
        Bearish FVG at bar i: low[i-2]  > high[i]  (gap between i-2 low and i high)
        Overlap = FVG range intersects [ote_low, ote_high]
    • Order Block (OB):
        Bullish OB = last bearish (close < open) 1H candle immediately
                     before the impulse leg (the candle at the base of the move).
        Bearish OB = last bullish candle before the impulse.
        Overlap = OB range [low, high] intersects [ote_low, ote_high]

ENTRY TRIGGER (5M bar k):
    During a killzone, if price tags the OTE zone (5M bar k has low <= ote_high
    and high >= ote_low), AND confluence exists, generate Signal:
        direction  = "BUY" (bullish) or "SELL" (bearish)
        entry_index = k + 1  (fill at next bar's open — no look-ahead)
        sl         = swing origin - buffer  (BUY: leg_low - sl_buffer)
                                            (SELL: leg_high + sl_buffer)
        tp         = swing extreme           (BUY: leg_high; SELL: leg_low)
    Skip if:
        • R:R (mid-price) < min_rr (default 1.5)
        • entry_index >= len(bars)

LOOK-AHEAD SELF-AUDIT:
    ✓ HTF bias: only 1H bars with time < current_1h_open are used.
      A 1H bar opening at T is "closed" only at T+1H. Since a 5M bar at
      time t_k belongs to the 1H period starting at floor(t_k, 1H), we
      use the filter: h1_bars["time"] < floor(t_k, 1H). This correctly
      excludes the currently-open (not yet closed) 1H bar.
    ✓ Swing detection: same time filter → uses only closed 1H bars.
    ✓ Signal: entry_index = k+1; the decision at bar k uses bars 0..k only.
    ✓ FVG/OB detection: computed on 1H bars already closed before bar k.
    ✓ No future bar prices are accessed during the k-scan loop.
"""
from __future__ import annotations

import json
import os
from typing import Optional

import pandas as pd

from strategies.research.exec_sim import Signal, simulate

# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------
HALF_SPREAD   = 0.15   # 30c full spread
SLIP          = 0.05   # 5c slippage
MAX_HOLD_BARS = 36     # ~3 hours on 5m
MIN_RR        = 1.5
SL_BUFFER     = 0.50   # $0.50 beyond swing origin (mid)
HTF_LOOKBACK  = 10     # 1H bars to scan for swing + bias

# Killzone windows (UTC hour, minute) — half-open [start, end)
_LONDON_START = (8,  0)
_LONDON_END   = (10, 0)
_NY_START     = (13, 30)
_NY_END       = (15, 30)


# ---------------------------------------------------------------------------
# 1. Killzone filter
# ---------------------------------------------------------------------------

def is_killzone(ts: pd.Timestamp) -> bool:
    """Return True if ts (tz-naive UTC) falls in London or NY killzone on a weekday."""
    if ts.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    h, m = ts.hour, ts.minute
    total = h * 60 + m
    london_start = _LONDON_START[0] * 60 + _LONDON_START[1]
    london_end   = _LONDON_END[0]   * 60 + _LONDON_END[1]
    ny_start     = _NY_START[0]     * 60 + _NY_START[1]
    ny_end       = _NY_END[0]       * 60 + _NY_END[1]
    in_london = london_start <= total < london_end
    in_ny     = ny_start     <= total < ny_end
    return in_london or in_ny


# ---------------------------------------------------------------------------
# 2. HTF Bias Detection
# ---------------------------------------------------------------------------

def _closed_1h_cutoff(as_of_time: pd.Timestamp) -> pd.Timestamp:
    """Return the cutoff for closed 1H bars given a 5M bar time.

    A 1H bar labelled T (its open time) is only fully closed at T+1H.
    At 5M bar time t_k, the current 1H period started at floor(t_k, 1H).
    That bar is NOT yet closed. So we include only bars with time <
    floor(t_k, 1H), i.e. bars opened before the current 1H period.
    """
    return as_of_time.floor("h")


def get_htf_bias(h1_bars: pd.DataFrame, as_of_time: pd.Timestamp) -> str:
    """Determine HTF bias from closed 1H bars strictly before the current 1H period.

    Rule:
        Use bars with time < floor(as_of_time, 1H) (closed 1H bars only).
        BOS check: compare the last closed bar's close against the prior
        bars' high (for bullish) and low (for bearish) within htf_lookback.
            Bullish BOS: last_close > max(prior_highs)
            Bearish BOS: last_close < min(prior_lows)
        If neither → neutral.

    Requires at least 2 visible bars; returns 'neutral' otherwise.
    """
    cutoff = _closed_1h_cutoff(as_of_time)
    visible = h1_bars[h1_bars["time"] < cutoff].copy()
    if len(visible) < 2:
        return "neutral"

    # Take up to HTF_LOOKBACK recent visible bars
    recent = visible.tail(HTF_LOOKBACK)
    last  = recent.iloc[-1]
    prior = recent.iloc[:-1]

    last_close  = float(last["close"])
    prior_high  = float(prior["high"].max())
    prior_low   = float(prior["low"].min())

    if last_close > prior_high:
        return "bullish"
    if last_close < prior_low:
        return "bearish"
    return "neutral"


# ---------------------------------------------------------------------------
# 3. Swing Leg and OTE Zone
# ---------------------------------------------------------------------------

def get_swing_leg(
    h1_bars: pd.DataFrame,
    bias: str,
    as_of_time: pd.Timestamp,
) -> Optional[tuple[float, float]]:
    """Return (leg_low, leg_high) for the most recent impulse swing leg.

    For 'bullish': scan backward for the lowest low (swing origin), then the
    highest high after that point (swing extreme).
    For 'bearish': scan backward for the highest high (swing origin), then the
    lowest low after that point (swing extreme).

    Uses only bars with time < floor(as_of_time, 1H) (closed bars; no look-ahead).
    Returns None if fewer than 3 visible bars exist.
    """
    cutoff = _closed_1h_cutoff(as_of_time)
    visible = h1_bars[h1_bars["time"] < cutoff].tail(HTF_LOOKBACK)
    if len(visible) < 3:
        return None

    highs = visible["high"].to_numpy()
    lows  = visible["low"].to_numpy()
    n = len(highs)

    if bias == "bullish":
        # Find swing low: bar with lowest low
        low_idx = int(lows.argmin())
        # Swing high: max high AFTER the swing low
        if low_idx >= n - 1:
            return None  # no bars after swing low
        leg_low  = float(lows[low_idx])
        leg_high = float(highs[low_idx + 1:].max())
        return (leg_low, leg_high)
    else:  # bearish
        # Find swing high: bar with highest high
        high_idx = int(highs.argmax())
        # Swing low: min low AFTER the swing high
        if high_idx >= n - 1:
            return None  # no bars after swing high
        leg_high = float(highs[high_idx])
        leg_low  = float(lows[high_idx + 1:].min())
        return (leg_low, leg_high)


def get_ote_zone(
    leg_low: float,
    leg_high: float,
    bias: str,
) -> tuple[float, float]:
    """Return (ote_low, ote_high) for the 62–79% retracement zone.

    Bullish (leg goes from leg_low up to leg_high):
        retracement measured from leg_high downward.
        ote_low  = leg_high - 0.79 * leg_range
        ote_high = leg_high - 0.62 * leg_range

    Bearish (leg goes from leg_high down to leg_low):
        retracement measured from leg_low upward.
        ote_low  = leg_low + 0.62 * leg_range
        ote_high = leg_low + 0.79 * leg_range
    """
    leg_range = leg_high - leg_low
    if bias == "bullish":
        ote_low  = leg_high - 0.79 * leg_range
        ote_high = leg_high - 0.62 * leg_range
    else:
        ote_low  = leg_low + 0.62 * leg_range
        ote_high = leg_low + 0.79 * leg_range
    return (ote_low, ote_high)


# ---------------------------------------------------------------------------
# 4. Confluence Detection
# ---------------------------------------------------------------------------

def _ranges_overlap(a_low: float, a_high: float, b_low: float, b_high: float) -> bool:
    """True if [a_low, a_high] overlaps [b_low, b_high] (inclusive)."""
    return a_low <= b_high and b_low <= a_high


def find_confluence(
    h1_bars: pd.DataFrame,
    as_of_time: pd.Timestamp,
    ote_low: float,
    ote_high: float,
    bias: str,
) -> bool:
    """Return True if any FVG or Order Block on visible 1H bars overlaps the OTE zone.

    FVG detection (3-candle imbalance):
        Bullish FVG at bar i: high[i-2] < low[i]  → gap = [high[i-2], low[i]]
        Bearish FVG at bar i: low[i-2]  > high[i] → gap = [high[i], low[i-2]]

    Order Block detection:
        Bullish OB = last bearish (close < open) candle in visible bars.
            OB range = [close, open]  (body, bearish candle: open > close)
        Bearish OB = last bullish (close > open) candle in visible bars.
            OB range = [open, close]  (body, bullish candle)
        Overlap tested against OTE zone.

    Uses only bars with time < floor(as_of_time, 1H) (no look-ahead).
    """
    cutoff = _closed_1h_cutoff(as_of_time)
    visible = h1_bars[h1_bars["time"] < cutoff].reset_index(drop=True)
    n = len(visible)

    # --- FVG check ---
    for i in range(2, n):
        h_i2 = float(visible.iloc[i - 2]["high"])
        l_i2 = float(visible.iloc[i - 2]["low"])
        h_i  = float(visible.iloc[i]["high"])
        l_i  = float(visible.iloc[i]["low"])

        if bias == "bullish":
            # Bullish FVG: high[i-2] < low[i]
            if h_i2 < l_i:
                fvg_low, fvg_high = h_i2, l_i
                if _ranges_overlap(fvg_low, fvg_high, ote_low, ote_high):
                    return True
        else:
            # Bearish FVG: low[i-2] > high[i]
            if l_i2 > h_i:
                fvg_low, fvg_high = h_i, l_i2
                if _ranges_overlap(fvg_low, fvg_high, ote_low, ote_high):
                    return True

    # --- Order Block check ---
    opens  = visible["open"].to_numpy()
    closes = visible["close"].to_numpy()

    if bias == "bullish":
        # Bullish OB = last bearish candle; OB range = [close, open]
        for j in range(n - 1, -1, -1):
            if closes[j] < opens[j]:  # bearish candle
                ob_low, ob_high = float(closes[j]), float(opens[j])
                if _ranges_overlap(ob_low, ob_high, ote_low, ote_high):
                    return True
                break  # only check the LAST bearish candle
    else:
        # Bearish OB = last bullish candle; OB range = [open, close]
        for j in range(n - 1, -1, -1):
            if closes[j] > opens[j]:  # bullish candle
                ob_low, ob_high = float(opens[j]), float(closes[j])
                if _ranges_overlap(ob_low, ob_high, ote_low, ote_high):
                    return True
                break

    return False


# ---------------------------------------------------------------------------
# 5. Signal Generation
# ---------------------------------------------------------------------------

def generate_signals(
    bars_5m: pd.DataFrame,
    bars_1h: pd.DataFrame,
) -> list[Signal]:
    """Scan 5M bars and generate OTE entry signals.

    For each 5M bar k (from 0 to len-2):
        1. Check if bar k is in a killzone window. Skip if not.
        2. Determine HTF bias from 1H bars closed at or before bar k's time.
           Skip if neutral.
        3. Identify the most recent impulse swing leg on 1H (closed bars <= bar k).
           Skip if insufficient bars.
        4. Compute the OTE zone [ote_low, ote_high] from the swing leg.
        5. Check if bar k tags the OTE zone (low[k] <= ote_high and high[k] >= ote_low).
           Skip if not.
        6. Check for FVG or OB confluence on 1H bars (closed <= bar k) overlapping OTE.
           Skip if no confluence.
        7. Compute SL and TP (mid prices):
            BUY:  sl = leg_low  - SL_BUFFER; tp = leg_high
            SELL: sl = leg_high + SL_BUFFER; tp = leg_low
        8. Compute expected mid R:R:
            BUY:  entry_open = bars_5m.iloc[k+1].open
                  risk_mid   = entry_open - sl
                  reward_mid = tp - entry_open
            SELL: risk_mid   = sl - entry_open
                  reward_mid = entry_open - tp
           Skip if reward_mid / risk_mid < MIN_RR or risk_mid <= 0.
        9. Emit Signal(direction, entry_index=k+1, sl=sl, tp=tp).

    NO look-ahead: HTF bars are filtered to time <= bars_5m.iloc[k].time.
    """
    signals: list[Signal] = []
    n5 = len(bars_5m)

    for k in range(n5 - 1):   # k+1 must exist
        bar_k = bars_5m.iloc[k]
        t_k   = pd.Timestamp(bar_k["time"])  # tz-naive UTC

        # 1. Killzone filter
        if not is_killzone(t_k):
            continue

        # 2. HTF bias (closed 1H bars only — bars with time <= t_k)
        bias = get_htf_bias(bars_1h, t_k)
        if bias == "neutral":
            continue

        # 3. Swing leg
        swing = get_swing_leg(bars_1h, bias, t_k)
        if swing is None:
            continue
        leg_low, leg_high = swing

        # Sanity: leg must be non-trivial
        if leg_high - leg_low < 0.10:
            continue

        # 4. OTE zone
        ote_low, ote_high = get_ote_zone(leg_low, leg_high, bias)

        # 5. Price tag: bar k touches OTE zone
        bar_low  = float(bar_k["low"])
        bar_high = float(bar_k["high"])
        if not (bar_low <= ote_high and bar_high >= ote_low):
            continue

        # 6. Confluence
        if not find_confluence(bars_1h, t_k, ote_low, ote_high, bias):
            continue

        # 7. SL and TP
        if bias == "bullish":
            direction = "BUY"
            sl = leg_low  - SL_BUFFER
            tp = leg_high
        else:
            direction = "SELL"
            sl = leg_high + SL_BUFFER
            tp = leg_low

        # 8. R:R check (mid prices, using next bar's open as proxy for entry)
        entry_open = float(bars_5m.iloc[k + 1]["open"])

        if direction == "BUY":
            # exec_sim requires sl < entry_open < tp
            if not (sl < entry_open < tp):
                continue
            risk_mid   = entry_open - sl
            reward_mid = tp - entry_open
        else:  # SELL
            if not (tp < entry_open < sl):
                continue
            risk_mid   = sl - entry_open
            reward_mid = entry_open - tp

        if risk_mid <= 0 or reward_mid / risk_mid < MIN_RR:
            continue

        # 9. Emit signal
        signals.append(Signal(
            direction   = direction,
            entry_index = k + 1,
            sl          = sl,
            tp          = tp,
        ))

    return signals


# ---------------------------------------------------------------------------
# 6. Backtest Runner + Metrics
# ---------------------------------------------------------------------------

RESULTS_PATH = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "backtest", "results", "s90_killzone_ote_20260522.json"
))


def compute_metrics(results, n_trades: int, bars_5m: pd.DataFrame) -> dict:
    """Compute all required metrics from a list of TradeResult."""
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
    wins   = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]

    winrate     = len(wins) / len(rs)
    expectancy  = sum(rs) / len(rs)
    avg_win     = sum(wins)   / len(wins)   if wins   else 0.0
    avg_loss    = sum(losses) / len(losses) if losses else 0.0

    gross_win  = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")

    # Max drawdown in R (peak-to-trough of cumulative R)
    cum_r  = 0.0
    peak   = 0.0
    max_dd = 0.0
    for r in rs:
        cum_r += r
        if cum_r > peak:
            peak = cum_r
        dd = peak - cum_r
        if dd > max_dd:
            max_dd = dd

    # Trades per day: unique calendar days with at least one killzone bar
    session_dates = set()
    for _, row in bars_5m.iterrows():
        ts = pd.Timestamp(row["time"])
        if is_killzone(ts):
            session_dates.add(ts.date())
    trades_per_day = n_trades / len(session_dates) if session_dates else 0.0

    # By-hour breakdown
    by_hour: dict[int, dict] = {}
    for t in results:
        h = pd.Timestamp(t.entry_time).hour
        if h not in by_hour:
            by_hour[h] = {"trades": 0, "wins": 0}
        by_hour[h]["trades"] += 1
        if t.r_multiple > 0:
            by_hour[h]["wins"] += 1
    by_hour_out = {
        str(h): {
            "trades":  v["trades"],
            "winrate": v["wins"] / v["trades"],
        }
        for h, v in sorted(by_hour.items())
    }

    return {
        "n_trades":       n_trades,
        "winrate":        round(winrate, 4),
        "expectancy_R":   round(expectancy, 4),
        "avg_win_R":      round(avg_win, 4),
        "avg_loss_R":     round(avg_loss, 4),
        "profit_factor":  round(profit_factor, 4),
        "max_dd_R":       round(max_dd, 4),
        "trades_per_day": round(trades_per_day, 4),
        "by_hour":        by_hour_out,
    }


def run_backtest() -> dict:
    """Load IS bars, generate signals, simulate, compute and write metrics JSON."""
    from strategies.research.dataset import load_is_bars

    print("[s90_killzone_ote] Loading in-sample bars...")
    bars_5m = load_is_bars("5m")
    bars_1h = load_is_bars("1h")
    print(f"[s90_killzone_ote] 5M bars: {len(bars_5m):,} | 1H bars: {len(bars_1h):,}")

    print("[s90_killzone_ote] Generating signals (this may take a minute)...")
    signals = generate_signals(bars_5m, bars_1h)
    print(f"[s90_killzone_ote] Signals: {len(signals)}")

    results = simulate(
        bars_5m, signals,
        half_spread=HALF_SPREAD,
        slip=SLIP,
        max_hold_bars=MAX_HOLD_BARS,
    )

    metrics = compute_metrics(results, len(results), bars_5m)

    output = {
        "family": "B_KillzoneOTE",
        "params": {
            "tf":              "5m",
            "htf_tf":          "1h",
            "half_spread":     HALF_SPREAD,
            "slip":            SLIP,
            "session_windows": {"london": "08:00-10:00 UTC", "ny": "13:30-15:30 UTC"},
            "sl_rule":         "swing_origin - 0.50",
            "tp_rule":         "swing_extreme (0% retracement level)",
            "ote_zone":        "62%-79% Fibonacci retracement",
            "min_rr":          MIN_RR,
            "max_hold_bars":   MAX_HOLD_BARS,
            "htf_lookback":    HTF_LOOKBACK,
            "sl_buffer":       SL_BUFFER,
        },
        **metrics,
    }

    os.makedirs(os.path.dirname(RESULTS_PATH), exist_ok=True)
    with open(RESULTS_PATH, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n[s90_killzone_ote] Results written to: {RESULTS_PATH}")
    print(f"  n_trades       : {output['n_trades']}")
    print(f"  winrate        : {output['winrate']:.1%}")
    print(f"  expectancy_R   : {output['expectancy_R']:.3f}")
    print(f"  trades_per_day : {output['trades_per_day']:.3f}")
    print(f"  profit_factor  : {output['profit_factor']:.3f}")
    print(f"  max_dd_R       : {output['max_dd_R']:.3f}")
    print(json.dumps(output, indent=2))

    return output


if __name__ == "__main__":
    run_backtest()
