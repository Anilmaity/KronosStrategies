"""
C01 — Asian Range Sweep + London Open Reversal (XAUUSD)
-------------------------------------------------------
Asian session (22:00-06:00 UTC) range gets swept in the London window
(07:00-11:00 UTC). Entry on reclaim back inside the range via an Order
Block midpoint near the swept extreme. SL beyond sweep wick, TP at the
opposite Asian extreme.
"""

from __future__ import annotations
import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig
from strategy.ict_engine import detect_order_blocks

NAME = "C01_ASIAN_SWEEP"
CONFIG = StrategyConfig(
    name=NAME,
    description="Asian range sweep + London reclaim, OB-midpoint entry",
    cooldown_s=1800,
    session_start_hour=7,
    session_end_hour=11,
)

# UTC hours
_ASIAN_START_H = 22       # 22:00 UTC prior day
_ASIAN_END_H = 6          # up to 06:00 UTC current day
_LONDON_START_H = 7
_LONDON_END_H = 11


def _asian_range(w5m: pd.DataFrame, now_utc) -> tuple[float, float] | None:
    """Return (asian_low, asian_high) covering the most recent Asian session."""
    if w5m is None or w5m.empty:
        return None
    df = w5m.copy()
    times = pd.to_datetime(df["time"])
    today = pd.Timestamp(now_utc).tz_localize(None).normalize()
    yesterday = today - pd.Timedelta(days=1)

    # Asian = yesterday 22:00 -> today 06:00 UTC
    a_start = yesterday + pd.Timedelta(hours=_ASIAN_START_H)
    a_end = today + pd.Timedelta(hours=_ASIAN_END_H)

    mask = (times >= a_start) & (times < a_end)
    sess = df[mask]
    if len(sess) < 10:
        return None
    return float(sess["low"].min()), float(sess["high"].max())


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    try:
        if w5m is None or len(w5m) < 50 or w1m is None or len(w1m) < 5:
            return None
        # Only trigger in London window
        if not (_LONDON_START_H <= now_utc.hour < _LONDON_END_H):
            return None

        rng = _asian_range(w5m, now_utc)
        if rng is None:
            return None
        a_low, a_high = rng
        a_range = a_high - a_low
        if a_range <= 0:
            return None

        # ATR floor — minimum meaningful range for XAU (in dollars)
        if a_range < 3.0:
            return None

        # Look at the most recent few 5m bars for a sweep + reclaim pattern
        recent = w5m.tail(6).reset_index(drop=True)
        if len(recent) < 3:
            return None

        last = recent.iloc[-1]
        prev = recent.iloc[-2]

        wick_pen = 0.15 * a_range

        # Bearish sweep of Asian HIGH -> BUY reversal back down... wait,
        # sweep of HIGH means price spiked above then reclaimed -> SELL setup.
        # Sweep of LOW -> BUY setup.

        # Detect within recent bars: a candle whose high > a_high + wick_pen
        # but close back inside range. Check last 2 bars.
        for idx in (-2, -3):
            if abs(idx) > len(recent):
                continue
            sweep_bar = recent.iloc[idx]

            # SELL setup: sweep of Asian HIGH
            if (sweep_bar["high"] > a_high + wick_pen
                    and sweep_bar["close"] < a_high
                    and last["high"] < sweep_bar["high"]):
                # Find a bearish OB near the swept high
                obs = detect_order_blocks(w5m.tail(40), displacement_mult=1.5)
                bear_obs = [o for o in obs if o.type == "bearish"
                            and abs(o.zone_high - a_high) < 0.30 * a_range]
                if not bear_obs:
                    continue
                ob = bear_obs[-1]
                mid = (ob.zone_low + ob.zone_high) / 2
                current_px = float(w1m.iloc[-1]["close"])
                # Entry valid only if price is near the OB mid
                if abs(current_px - mid) > 0.5:
                    continue
                sl = round(sweep_bar["high"] + 0.10 * a_range, 2)
                # Targets: 50% Asian range
                t1 = a_low + 0.5 * a_range
                tp = round(t1, 2)
                entry = round(current_px, 2)
                if not (tp < entry < sl):
                    continue
                return Signal("SELL", entry, sl, tp, "C01_ASIAN_SWEEP_HIGH")

            # BUY setup: sweep of Asian LOW
            if (sweep_bar["low"] < a_low - wick_pen
                    and sweep_bar["close"] > a_low
                    and last["low"] > sweep_bar["low"]):
                obs = detect_order_blocks(w5m.tail(40), displacement_mult=1.5)
                bull_obs = [o for o in obs if o.type == "bullish"
                            and abs(o.zone_low - a_low) < 0.30 * a_range]
                if not bull_obs:
                    continue
                ob = bull_obs[-1]
                mid = (ob.zone_low + ob.zone_high) / 2
                current_px = float(w1m.iloc[-1]["close"])
                if abs(current_px - mid) > 0.5:
                    continue
                sl = round(sweep_bar["low"] - 0.10 * a_range, 2)
                t1 = a_low + 0.5 * a_range
                tp = round(t1, 2)
                entry = round(current_px, 2)
                if not (sl < entry < tp):
                    continue
                return Signal("BUY", entry, sl, tp, "C01_ASIAN_SWEEP_LOW")

        return None
    except Exception:
        return None
