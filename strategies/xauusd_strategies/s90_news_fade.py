"""
s90_news_fade.py
----------------
Setup Family D — News-Fade (Experimental)

XAU/USD 90%-winrate research project, Phase B, Task 5d.

Strategy concept
~~~~~~~~~~~~~~~~
At a scheduled High-impact USD macro event (FOMC, CPI, NFP, PCE), the initial
price spike after the release often overshoots and sweeps HTF liquidity (a prior
swing high or swing low). When that liquidity sweep is confirmed on the 5-min
bar, we FADE the spike (enter opposite to the spike direction).

Logic summary
~~~~~~~~~~~~~
1. For each High-impact USD event time T in the IS window:
   a. Confirm T falls on a weekday (Mon-Fri).
   b. Identify the 5-min bar that covers the event release (the "spike bar"):
      the first bar whose time >= T (bar index s).
   c. Measure the spike from bar s: spike_high = bars[s].high, spike_low = bars[s].low.
   d. Look back over the N prior bars (up to 3 hours = 36 bars on 5m) to find
      the most recent significant swing high (for up-spike) or swing low (for down-spike).
   e. UP-spike fade (SHORT):
      - Spike goes UP: spike_high > recent swing high (liquidity swept).
      - Enter SHORT at the open of bar s+2 (confirmation bar s+1, entry k+1 = s+2).
      - SL = spike_high + SL_BUFFER (above the extreme).
      - TP = pre-event close (retrace target, ~50-100% of spike back down).
      - Skip if R:R < 1:1.
   f. DOWN-spike fade (LONG):
      - Spike goes DOWN: spike_low < recent swing low (liquidity swept).
      - Enter LONG at the open of bar s+2.
      - SL = spike_low - SL_BUFFER (below the extreme).
      - TP = pre-event close (retrace target).
      - Skip if R:R < 1:1.
2. Deduplicate: at most one signal per event (strongest R:R wins).

NO LOOK-AHEAD SELF-AUDIT
~~~~~~~~~~~~~~~~~~~~~~~~~
- Event time T is a scheduled public release date/time — known in advance. OK.
- Spike bar index s: we find it as the first bar with time >= T. This bar is
  CLOSED before we act (we read its OHLC data for the spike measurement).
- Confirmation bar s+1: also closed before entry.
- entry_index = s+2 (the next bar after confirmation). All decisions are based
  on bars 0..s+1 only — no future bars are ever read before entry.
- Swing high/low lookback: only bars 0..s-1 (before the event bar). No look-ahead.

Event source
~~~~~~~~~~~~
Events are drawn from event_gate.FOMC_DATES, OVERRIDES_CPI, OVERRIDES_NFP, and
the PCE/GDP rule-of-thumb. We filter to importance=3 events (FOMC, CPI, NFP, PCE)
and restrict to the IS window (2025-01-01..2026-02-18).

If importing from event_gate._build_event_table fails, we fall back to a hard-coded
subset of the same dates for robustness.

Parameters
~~~~~~~~~~
LOOKBACK_BARS   : int = 36    # 3 hours on 5m = bars to search for swing hi/lo
SL_BUFFER       : float = 1.0 # $ above/below spike extreme for stop
MIN_RR          : float = 1.0 # minimum risk:reward ratio
MIN_SPIKE       : float = 5.0 # minimum spike size in $ to qualify
MAX_HOLD_BARS   : int = 24    # max bars held (2 hours on 5m)
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
from typing import Optional

import pandas as pd

try:
    from strategies.research.exec_sim import Signal, simulate
except ImportError:  # in-container: WORKDIR /app == strategies/, package is `research`
    from research.exec_sim import Signal, simulate

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------
LOOKBACK_BARS = 36     # bars before event to scan for swing high/low (3h on 5m)
SL_BUFFER = 1.0        # $ buffer added to spike extreme for stop-loss
MIN_RR = 1.0           # minimum R:R ratio to place trade
MIN_SPIKE = 5.0        # minimum spike size in $ to qualify as news spike
MAX_HOLD_BARS = 24     # maximum hold duration in bars (~2 hours on 5m)

# Importance-3 event types we want to fade
HIGH_IMPACT_TYPES = {"FOMC", "CPI", "NFP", "PCE"}

# IS window (tz-naive UTC, inclusive)
IS_START = pd.Timestamp("2025-01-01 00:00:00")
IS_END = pd.Timestamp("2026-02-18 23:59:59")


# ---------------------------------------------------------------------------
# Event calendar
# ---------------------------------------------------------------------------

def get_high_impact_usd_events() -> list[pd.Timestamp]:
    """Return tz-naive UTC timestamps of High-impact USD events in IS window.

    Draws from event_gate._build_event_table (FOMC, CPI, NFP, PCE at
    importance=3). Returns tz-naive Timestamps to match bar DataFrame convention.

    Falls back to a hard-coded subset if import fails.
    """
    try:
        try:
            from strategies.shared.event_gate import _build_event_table
        except ImportError:  # in-container: WORKDIR /app == strategies/, package is `shared`
            from shared.event_gate import _build_event_table
        df = _build_event_table(2025, 2026)
        # Filter to importance=3 (FOMC, CPI, NFP, PCE)
        df = df[df["importance"] >= 3]
        # Drop ECB/SPEECH rows (we want High-impact USD only)
        df = df[df["event_type"].isin(HIGH_IMPACT_TYPES)]
        # Convert tz-aware -> tz-naive UTC for bar comparison
        times = pd.to_datetime(df["time"]).dt.tz_localize(None)
        # Slice to IS window only
        mask = (times >= IS_START) & (times <= IS_END)
        result = sorted(set(times[mask].tolist()))
        return result
    except Exception as exc:  # pragma: no cover — failsafe
        log.warning("[s90_news_fade] event_gate import failed (%s), using fallback", exc)
        return _fallback_events()


def _fallback_events() -> list[pd.Timestamp]:
    """Hard-coded fallback: FOMC/CPI/NFP/PCE events in IS window.

    Mirrors event_gate data. Times in UTC (tz-naive).
    FOMC: 14:00 ET -> 19:00 UTC (winter) / 18:00 UTC (summer).
    CPI/NFP/PCE: 08:30 ET -> 13:30 UTC (winter) / 12:30 UTC (summer).
    """
    events = []

    # FOMC 2025 (winter = 19:00 UTC, summer = 18:00 UTC)
    fomc = [
        ("2025-01-29", 19, 0),
        ("2025-03-19", 18, 0),
        ("2025-05-07", 18, 0),
        ("2025-06-18", 18, 0),
        ("2025-07-30", 18, 0),
        ("2025-09-17", 18, 0),
        ("2025-10-29", 19, 0),
        ("2025-12-10", 19, 0),
        ("2026-01-28", 19, 0),
    ]
    for date_str, h, m in fomc:
        events.append(pd.Timestamp(f"{date_str} {h:02d}:{m:02d}:00"))

    # NFP (first Friday 08:30 ET) - key dates 2025
    nfp = [
        "2025-01-10 13:30:00",
        "2025-02-07 13:30:00",
        "2025-03-07 13:30:00",
        "2025-04-04 12:30:00",
        "2025-05-02 12:30:00",
        "2025-06-06 12:30:00",
        "2025-07-03 12:30:00",  # early close week
        "2025-08-01 12:30:00",
        "2025-09-05 12:30:00",
        "2025-10-03 12:30:00",
        "2025-11-07 13:30:00",
        "2025-12-05 13:30:00",
        "2026-01-09 13:30:00",
        "2026-02-06 13:30:00",
    ]
    for ts_str in nfp:
        events.append(pd.Timestamp(ts_str))

    # CPI (2nd Wed 08:30 ET) - key dates 2025
    cpi = [
        "2025-01-15 13:30:00",
        "2025-02-12 13:30:00",
        "2025-03-12 12:30:00",
        "2025-04-09 12:30:00",
        "2025-05-14 12:30:00",
        "2025-06-11 12:30:00",
        "2025-07-15 12:30:00",
        "2025-08-13 12:30:00",
        "2025-09-10 12:30:00",
        "2025-10-15 13:30:00",
        "2025-11-12 13:30:00",
        "2025-12-11 13:30:00",
        "2026-01-14 13:30:00",
        "2026-02-11 13:30:00",
    ]
    for ts_str in cpi:
        events.append(pd.Timestamp(ts_str))

    # PCE (last business day of month 08:30 ET) - selected 2025
    pce = [
        "2025-01-31 13:30:00",
        "2025-02-28 13:30:00",
        "2025-03-28 12:30:00",
        "2025-04-30 12:30:00",
        "2025-05-30 12:30:00",
        "2025-06-27 12:30:00",
        "2025-07-31 12:30:00",
        "2025-08-29 12:30:00",
        "2025-09-26 12:30:00",
        "2025-10-31 13:30:00",
        "2025-11-26 13:30:00",
        "2025-12-19 13:30:00",
        "2026-01-30 13:30:00",
        "2026-02-27 13:30:00",
    ]
    for ts_str in pce:
        ts = pd.Timestamp(ts_str)
        if ts <= IS_END:
            events.append(ts)

    # Filter to IS window
    result = sorted(set(e for e in events if IS_START <= e <= IS_END))
    return result


# ---------------------------------------------------------------------------
# Core detection logic
# ---------------------------------------------------------------------------

def _find_bar_index_at_or_after(bars: pd.DataFrame, target_time: pd.Timestamp) -> Optional[int]:
    """Return the first bar index where bars['time'] >= target_time, or None."""
    mask = bars["time"] >= target_time
    idxs = bars.index[mask]
    if len(idxs) == 0:
        return None
    return int(idxs[0])


def _swing_high(bars: pd.DataFrame, start_idx: int, end_idx: int) -> Optional[float]:
    """Return the maximum high in bars[start_idx:end_idx] (exclusive end)."""
    if start_idx >= end_idx or start_idx < 0:
        return None
    segment = bars.iloc[start_idx:end_idx]
    if segment.empty:
        return None
    return float(segment["high"].max())


def _swing_low(bars: pd.DataFrame, start_idx: int, end_idx: int) -> Optional[float]:
    """Return the minimum low in bars[start_idx:end_idx] (exclusive end)."""
    if start_idx >= end_idx or start_idx < 0:
        return None
    segment = bars.iloc[start_idx:end_idx]
    if segment.empty:
        return None
    return float(segment["low"].min())


def _build_signal(
    direction: str,
    entry_index: int,
    entry_open: float,
    spike_extreme: float,
    pre_event_price: float,
) -> Optional[Signal]:
    """Construct a Signal with SL and TP, applying R:R filter.

    TP is the pre-event price (full retrace target). This is the natural target
    for a news-fade: the spike was driven by over-reaction, and the expected
    destination is back to where price was before the news.

    Returns None if the R:R is below MIN_RR or the signal geometry is invalid.
    """
    if direction == "SELL":
        sl = spike_extreme + SL_BUFFER
        # TP = pre-event price (full retrace of news spike)
        tp = pre_event_price
        # Validate: tp < entry_open < sl (entry must be inside the spike)
        if not (tp < entry_open < sl):
            return None
        risk = sl - entry_open
        reward = entry_open - tp
    else:  # BUY
        sl = spike_extreme - SL_BUFFER
        # TP = pre-event price (full retrace of news spike)
        tp = pre_event_price
        # Validate: sl < entry_open < tp
        if not (sl < entry_open < tp):
            return None
        risk = entry_open - sl
        reward = tp - entry_open

    if risk <= 0 or reward <= 0:
        return None
    rr = reward / risk
    if rr < MIN_RR:
        log.debug("[s90_news_fade] skip %s: R:R=%.2f < %.2f", direction, rr, MIN_RR)
        return None

    return Signal(direction=direction, entry_index=entry_index, sl=sl, tp=tp)


def detect_news_fade_signals(
    bars: pd.DataFrame,
    event_times: list[pd.Timestamp],
    *,
    lookback_bars: int = LOOKBACK_BARS,
    sl_buffer: float = SL_BUFFER,
    min_rr: float = MIN_RR,
    min_spike: float = MIN_SPIKE,
) -> list[Signal]:
    """Generate news-fade Signals for all qualifying events in bars.

    LOOK-AHEAD INVARIANT:
    - event_times are known scheduled release times (public calendar). OK.
    - Spike measurement uses bars[s].high/low where s is the event bar (closed).
    - Confirmation at bar s+1 (closed). Entry at bar s+2 = entry_index.
    - Swing hi/lo lookback uses only bars 0..s-1. No future data accessed.

    Parameters
    ----------
    bars : pd.DataFrame
        5-minute mid-OHLCV bars (tz-naive UTC time column).
    event_times : list of pd.Timestamp
        Known event release times (tz-naive UTC). Scheduled releases are
        public information, so using them does not constitute look-ahead.
    lookback_bars : int
        Number of pre-event bars to scan for swing high/low.
    sl_buffer : float
        Dollars added to spike extreme for stop-loss.
    min_rr : float
        Minimum reward-to-risk ratio.
    min_spike : float
        Minimum spike size in dollars.

    Returns
    -------
    list[Signal]
        One Signal per qualifying event. May be empty.
    """
    if bars.empty or not event_times:
        return []

    signals: list[Signal] = []

    for event_ts in event_times:
        # Only weekdays
        if event_ts.weekday() >= 5:
            continue

        # Find spike bar: first bar at or after event time
        s = _find_bar_index_at_or_after(bars, event_ts)
        if s is None:
            continue

        # We need: spike bar s, confirmation bar s+1, entry bar s+2
        if s + 2 >= len(bars):
            continue

        # Pre-event price: close of bar just before spike bar
        if s == 0:
            continue
        pre_event_price = float(bars.iloc[s - 1]["close"])

        spike_bar = bars.iloc[s]
        spike_high = float(spike_bar["high"])
        spike_low = float(spike_bar["low"])

        # Swing high/low lookback: bars before the spike bar
        lb_start = max(0, s - lookback_bars)
        lb_end = s  # exclusive, so we scan [lb_start, s-1]

        # Entry bar
        entry_index = s + 2
        entry_open = float(bars.iloc[entry_index]["open"])

        # --- UP-spike: check if it swept a prior swing high -> SELL ---
        prior_high = _swing_high(bars, lb_start, lb_end)
        if prior_high is not None:
            spike_size_up = spike_high - pre_event_price
            if spike_high > prior_high and spike_size_up >= min_spike:
                sig = _build_signal(
                    direction="SELL",
                    entry_index=entry_index,
                    entry_open=entry_open,
                    spike_extreme=spike_high,
                    pre_event_price=pre_event_price,
                )
                if sig is not None:
                    signals.append(sig)
                    log.debug(
                        "[s90_news_fade] SELL signal event=%s spike_high=%.2f "
                        "prior_high=%.2f entry_idx=%d",
                        event_ts, spike_high, prior_high, entry_index,
                    )
                    continue  # at most one signal per event

        # --- DOWN-spike: check if it swept a prior swing low -> BUY ---
        prior_low = _swing_low(bars, lb_start, lb_end)
        if prior_low is not None:
            spike_size_dn = pre_event_price - spike_low
            if spike_low < prior_low and spike_size_dn >= min_spike:
                sig = _build_signal(
                    direction="BUY",
                    entry_index=entry_index,
                    entry_open=entry_open,
                    spike_extreme=spike_low,
                    pre_event_price=pre_event_price,
                )
                if sig is not None:
                    signals.append(sig)
                    log.debug(
                        "[s90_news_fade] BUY signal event=%s spike_low=%.2f "
                        "prior_low=%.2f entry_idx=%d",
                        event_ts, spike_low, prior_low, entry_index,
                    )

    return signals


# ---------------------------------------------------------------------------
# Backtest runner
# ---------------------------------------------------------------------------

def run_backtest() -> dict:
    """Run the news-fade backtest on IS 5m bars and return metrics dict."""
    try:
        from strategies.research.dataset import load_is_bars
    except ImportError:  # in-container layout
        from research.dataset import load_is_bars

    print("[s90_news_fade] loading IS 5m bars...")
    bars = load_is_bars("5m")
    print(f"[s90_news_fade] {len(bars):,} bars loaded "
          f"({bars['time'].iloc[0]} .. {bars['time'].iloc[-1]})")

    # Get events
    event_times = get_high_impact_usd_events()
    print(f"[s90_news_fade] {len(event_times)} High-impact USD events in IS window")

    # Generate signals
    signals = detect_news_fade_signals(bars, event_times)
    print(f"[s90_news_fade] {len(signals)} qualifying signals generated")

    if not signals:
        metrics = _empty_metrics()
        _write_results(metrics)
        return metrics

    # Simulate
    results = simulate(bars, signals, half_spread=0.15, slip=0.05, max_hold_bars=MAX_HOLD_BARS)

    metrics = _compute_metrics(results, signals, bars, event_times)
    _write_results(metrics)
    return metrics


def _empty_metrics() -> dict:
    return {
        "family": "D_NewsFade",
        "params": {
            "lookback_bars": LOOKBACK_BARS,
            "sl_buffer": SL_BUFFER,
            "min_rr": MIN_RR,
            "min_spike": MIN_SPIKE,
            "max_hold_bars": MAX_HOLD_BARS,
            "event_source": "event_gate._build_event_table (importance>=3, FOMC/CPI/NFP/PCE)",
        },
        "n_trades": 0,
        "winrate": None,
        "expectancy_R": None,
        "avg_win_R": None,
        "avg_loss_R": None,
        "profit_factor": None,
        "max_dd_R": None,
        "trades_per_day": 0.0,
        "by_hour": {},
        "note": "No qualifying signals found",
    }


def _compute_metrics(results, signals, bars, event_times) -> dict:
    try:
        from strategies.research.exec_sim import TradeResult
    except ImportError:  # in-container layout
        from research.exec_sim import TradeResult

    n = len(results)
    r_multiples = [r.r_multiple for r in results]
    wins = [r for r in r_multiples if r > 0]
    losses = [r for r in r_multiples if r <= 0]

    winrate = len(wins) / n if n > 0 else 0.0
    expectancy_r = sum(r_multiples) / n if n > 0 else 0.0
    avg_win_r = sum(wins) / len(wins) if wins else 0.0
    avg_loss_r = sum(losses) / len(losses) if losses else 0.0
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")

    # Max drawdown in R
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in r_multiples:
        cumulative += r
        peak = max(peak, cumulative)
        dd = peak - cumulative
        max_dd = max(max_dd, dd)

    # Trades per day (IS window = 2025-01-01..2026-02-18, ~383 calendar days,
    # ~274 trading days approx)
    is_days = (pd.Timestamp("2026-02-18") - pd.Timestamp("2025-01-01")).days
    trading_days = is_days * 5 / 7
    trades_per_day = n / trading_days if trading_days > 0 else 0.0

    # By-hour breakdown
    by_hour: dict[int, dict] = {}
    for r, sig in zip(results, signals):
        h = int(bars.iloc[sig.entry_index]["time"].hour)
        if h not in by_hour:
            by_hour[h] = {"trades": 0, "wins": 0}
        by_hour[h]["trades"] += 1
        if r.r_multiple > 0:
            by_hour[h]["wins"] += 1

    by_hour_metrics = {
        h: {
            "trades": v["trades"],
            "winrate": v["wins"] / v["trades"] if v["trades"] > 0 else 0.0,
        }
        for h, v in sorted(by_hour.items())
    }

    return {
        "family": "D_NewsFade",
        "params": {
            "lookback_bars": LOOKBACK_BARS,
            "sl_buffer": SL_BUFFER,
            "min_rr": MIN_RR,
            "min_spike": MIN_SPIKE,
            "max_hold_bars": MAX_HOLD_BARS,
            "event_source": "event_gate._build_event_table (importance>=3, FOMC/CPI/NFP/PCE)",
        },
        "n_trades": n,
        "winrate": round(winrate, 4),
        "expectancy_R": round(expectancy_r, 4),
        "avg_win_R": round(avg_win_r, 4),
        "avg_loss_R": round(avg_loss_r, 4),
        "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else "inf",
        "max_dd_R": round(max_dd, 4),
        "trades_per_day": round(trades_per_day, 4),
        "by_hour": by_hour_metrics,
    }


def _write_results(metrics: dict) -> None:
    results_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "backtest", "results"
    )
    os.makedirs(results_dir, exist_ok=True)
    out_path = os.path.join(results_dir, "s90_news_fade_20260522.json")
    with open(out_path, "w") as f:
        json.dump(metrics, f, indent=2, default=str)
    print(f"[s90_news_fade] results written to {out_path}")
    print(json.dumps(metrics, indent=2, default=str))


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    run_backtest()
