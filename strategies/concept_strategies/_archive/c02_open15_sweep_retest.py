"""
C02 — Opening 15-Min Liquidity Sweep + Retest (XAUUSD)
------------------------------------------------------
The first 15m candle of London (07:00 UTC) or NY (13:30 UTC) open
sweeps the prior session extreme and reverses. Entry on retest of
the 50% body of the sweep candle after a 5m BOS confirms reversal.
"""

from __future__ import annotations
import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig

NAME = "C02_OPEN15_RETEST"
CONFIG = StrategyConfig(
    name=NAME,
    description="Opening 15m sweep + 5m BOS retest entry at sweep candle 50% body",
    cooldown_s=1200,
    session_start_hour=7,
    session_end_hour=20,
)

_LONDON_OPEN_H = 7         # 07:00 UTC
_NY_OPEN_H = 13            # 13:30 UTC ~ NY open
_NY_OPEN_M = 30


def _prior_session_range(w5m: pd.DataFrame, now_utc, which: str) -> tuple[float, float] | None:
    """For London open -> prior NY session 13:00-21:00 UTC yesterday.
    For NY open -> overnight including London 22:00 prior -> 13:30 today."""
    if w5m is None or w5m.empty:
        return None
    df = w5m.copy()
    times = pd.to_datetime(df["time"])
    today = pd.Timestamp(now_utc).tz_localize(None).normalize()
    yesterday = today - pd.Timedelta(days=1)

    if which == "london":
        start = yesterday + pd.Timedelta(hours=13)
        end = yesterday + pd.Timedelta(hours=21)
    else:  # ny
        start = yesterday + pd.Timedelta(hours=22)
        end = today + pd.Timedelta(hours=13, minutes=30)

    sess = df[(times >= start) & (times < end)]
    if len(sess) < 10:
        return None
    return float(sess["low"].min()), float(sess["high"].max())


def _opening_15m_candle(w5m: pd.DataFrame, now_utc, which: str) -> dict | None:
    """Build the first 15m candle (3 x 5m) of the named session for today."""
    df = w5m.copy()
    times = pd.to_datetime(df["time"])
    today = pd.Timestamp(now_utc).tz_localize(None).normalize()

    if which == "london":
        open_start = today + pd.Timedelta(hours=_LONDON_OPEN_H)
    else:
        open_start = today + pd.Timedelta(hours=_NY_OPEN_H, minutes=_NY_OPEN_M)
    open_end = open_start + pd.Timedelta(minutes=15)

    bars = df[(times >= open_start) & (times < open_end)]
    if len(bars) < 3:
        return None
    return {
        "open": float(bars.iloc[0]["open"]),
        "high": float(bars["high"].max()),
        "low": float(bars["low"].min()),
        "close": float(bars.iloc[-1]["close"]),
        "start": open_start,
        "end": open_end,
    }


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    try:
        if w5m is None or len(w5m) < 50 or w1m is None or len(w1m) < 5:
            return None

        # Determine which open we're in (and the trigger window after)
        h, m = now_utc.hour, now_utc.minute
        if 7 <= h < 11:
            which = "london"
        elif 13 <= h < 18:
            which = "ny"
        else:
            return None

        prior = _prior_session_range(w5m, now_utc, which)
        if prior is None:
            return None
        p_low, p_high = prior
        p_range = p_high - p_low
        if p_range < 3.0:
            return None

        sweep = _opening_15m_candle(w5m, now_utc, which)
        if sweep is None:
            return None

        # Body extremes
        body_high = max(sweep["open"], sweep["close"])
        body_low = min(sweep["open"], sweep["close"])
        body_mid = (body_high + body_low) / 2
        wick_pen = 0.10 * p_range

        # SELL setup: swept prior high, closed back inside
        bearish_sweep = (sweep["high"] > p_high + wick_pen
                         and sweep["close"] < p_high)
        # BUY setup: swept prior low, closed back inside
        bullish_sweep = (sweep["low"] < p_low - wick_pen
                         and sweep["close"] > p_low)

        if not (bearish_sweep or bullish_sweep):
            return None

        # Look for 5m BOS post-sweep: 5m close beyond a recent swing in the
        # reversal direction. Use bars after the sweep ended.
        df5 = w5m.copy()
        df5["time"] = pd.to_datetime(df5["time"])
        post = df5[df5["time"] >= sweep["end"]]
        if len(post) < 3:
            return None

        current_px = float(w1m.iloc[-1]["close"])

        if bearish_sweep:
            # BOS: a 5m close < min low of pre-sweep 3 bars
            pre = df5[(df5["time"] >= sweep["start"] - pd.Timedelta(minutes=30))
                      & (df5["time"] < sweep["start"])]
            if len(pre) < 3:
                return None
            ref_low = float(pre["low"].min())
            bos = (post["close"] < ref_low).any()
            if not bos:
                return None
            # Limit retest at body_mid (50% of sweep candle body)
            if abs(current_px - body_mid) > 0.6:
                return None
            sl = round(sweep["high"] + 0.05 * p_range, 2)
            tp = round(p_low, 2)
            entry = round(current_px, 2)
            if not (tp < entry < sl):
                return None
            return Signal("SELL", entry, sl, tp, "C02_OPEN15_SWEEP_HIGH")

        if bullish_sweep:
            pre = df5[(df5["time"] >= sweep["start"] - pd.Timedelta(minutes=30))
                      & (df5["time"] < sweep["start"])]
            if len(pre) < 3:
                return None
            ref_high = float(pre["high"].max())
            bos = (post["close"] > ref_high).any()
            if not bos:
                return None
            if abs(current_px - body_mid) > 0.6:
                return None
            sl = round(sweep["low"] - 0.05 * p_range, 2)
            tp = round(p_high, 2)
            entry = round(current_px, 2)
            if not (sl < entry < tp):
                return None
            return Signal("BUY", entry, sl, tp, "C02_OPEN15_SWEEP_LOW")

        return None
    except Exception:
        return None
