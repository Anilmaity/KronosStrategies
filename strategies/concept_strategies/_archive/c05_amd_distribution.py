"""
C05 — Power-of-Three (AMD) Distribution Leg (XAUUSD)
----------------------------------------------------
Accumulation (Asian range) -> Manipulation (one-sided sweep in London
window) -> Distribution (15m BOS opposite the sweep). Entry on the first
retracement after BOS to the OB or FVG left by the BOS impulse.
Target = opposite Asian extreme.
"""

from __future__ import annotations
import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig
from strategy.ict_engine import detect_order_blocks, detect_fvgs

NAME = "C05_AMD_DIST"
CONFIG = StrategyConfig(
    name=NAME,
    description="AMD distribution-leg entry after manipulation sweep + 15m BOS",
    cooldown_s=1800,
    session_start_hour=7,
    session_end_hour=15,
)


def _asian_range(w5m: pd.DataFrame, now_utc) -> tuple[float, float, float] | None:
    """Return (low, high, close) for Asian session yesterday 22:00 -> today 06:00 UTC."""
    if w5m is None or w5m.empty:
        return None
    df = w5m.copy()
    df["time"] = pd.to_datetime(df["time"])
    today = pd.Timestamp(now_utc).tz_localize(None).normalize()
    yesterday = today - pd.Timedelta(days=1)
    a_start = yesterday + pd.Timedelta(hours=22)
    a_end = today + pd.Timedelta(hours=6)
    sess = df[(df["time"] >= a_start) & (df["time"] < a_end)]
    if len(sess) < 10:
        return None
    return (float(sess["low"].min()),
            float(sess["high"].max()),
            float(sess.iloc[-1]["close"]))


def _atr_15m(w15m: pd.DataFrame, n: int = 14) -> float:
    if w15m is None or len(w15m) < n:
        return 0.0
    tail = w15m.tail(n)
    return float((tail["high"] - tail["low"]).mean())


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    try:
        if (w15m is None or len(w15m) < 20
                or w5m is None or len(w5m) < 60
                or w1m is None or len(w1m) < 5):
            return None

        # Distribution window: 07-15 UTC
        if not (7 <= now_utc.hour < 15):
            return None

        asian = _asian_range(w5m, now_utc)
        if asian is None:
            return None
        a_low, a_high, a_close = asian
        a_range = a_high - a_low
        if a_range < 3.0:
            return None

        # Range-vs-trend classifier: close within 20% of an extreme = trend, skip
        if (a_close - a_low) < 0.20 * a_range:
            return None
        if (a_high - a_close) < 0.20 * a_range:
            return None

        atr15 = _atr_15m(w15m)
        if atr15 <= 0:
            return None

        # Manipulation: exactly one side swept in 07-10 UTC on 5m
        df5 = w5m.copy()
        df5["time"] = pd.to_datetime(df5["time"])
        today = pd.Timestamp(now_utc).tz_localize(None).normalize()
        manip_start = today + pd.Timedelta(hours=7)
        manip_end = today + pd.Timedelta(hours=10)
        manip = df5[(df5["time"] >= manip_start) & (df5["time"] < manip_end)]
        if len(manip) < 3:
            return None

        swept_high = ((manip["high"] > a_high) & (manip["close"] < a_high)).any()
        swept_low = ((manip["low"] < a_low) & (manip["close"] > a_low)).any()

        # Need exactly one
        if swept_high == swept_low:
            return None

        # Extract sweep extreme price
        if swept_high:
            sweep_bars = manip[manip["high"] > a_high]
            sweep_extreme = float(sweep_bars["high"].max())
            direction = "SELL"
        else:
            sweep_bars = manip[manip["low"] < a_low]
            sweep_extreme = float(sweep_bars["low"].min())
            direction = "BUY"

        # Distribution BOS: 15m close opposite the sweep, within 90 min of sweep end
        sweep_time = pd.to_datetime(sweep_bars.iloc[-1]["time"])
        df15 = w15m.copy()
        df15["time"] = pd.to_datetime(df15["time"])
        pre = df15[df15["time"] < sweep_time]
        post = df15[(df15["time"] > sweep_time)
                    & (df15["time"] <= sweep_time + pd.Timedelta(minutes=90))]
        if len(pre) < 4 or len(post) < 1:
            return None

        if direction == "SELL":
            ref = float(pre["low"].tail(8).min())
            bos = (post["close"] < ref).any()
        else:
            ref = float(pre["high"].tail(8).max())
            bos = (post["close"] > ref).any()
        if not bos:
            return None

        # Entry zone: 15m OB or 5m FVG left by BOS impulse
        wanted_ob_type = "bearish" if direction == "SELL" else "bullish"
        obs = detect_order_blocks(df15.tail(30), displacement_mult=1.5)
        valid_obs = [o for o in obs
                     if o.type == wanted_ob_type
                     and pd.to_datetime(o.time) >= sweep_time - pd.Timedelta(minutes=30)]

        # 5m FVG fallback / additional zone
        fvgs = detect_fvgs(df5.tail(40), min_gap=0.20)
        wanted_fvg = wanted_ob_type
        valid_fvgs = [f for f in fvgs
                      if f.type == wanted_fvg
                      and pd.to_datetime(f.time) >= sweep_time - pd.Timedelta(minutes=15)]

        current_px = float(w1m.iloc[-1]["close"])
        entry_zone = None

        if valid_obs:
            ob = valid_obs[-1]
            mid = (ob.zone_low + ob.zone_high) / 2
            if abs(current_px - mid) <= 0.5:
                entry_zone = ("OB", ob.zone_low, ob.zone_high)
        if entry_zone is None and valid_fvgs:
            fvg = valid_fvgs[-1]
            mid = (fvg.zone_low + fvg.zone_high) / 2
            if abs(current_px - mid) <= 0.5:
                entry_zone = ("FVG", fvg.zone_low, fvg.zone_high)

        if entry_zone is None:
            return None

        zlow, zhigh = entry_zone[1], entry_zone[2]

        if direction == "BUY":
            sl = round(sweep_extreme - 0.10 * atr15, 2)
            tp = round(a_high, 2)            # opposite Asian extreme
            entry = round(current_px, 2)
            if not (sl < entry < tp):
                return None
            if (tp - entry) < (entry - sl):
                return None
            return Signal("BUY", entry, sl, tp, f"C05_AMD_BUY_{entry_zone[0]}")
        else:
            sl = round(sweep_extreme + 0.10 * atr15, 2)
            tp = round(a_low, 2)
            entry = round(current_px, 2)
            if not (tp < entry < sl):
                return None
            if (entry - tp) < (sl - entry):
                return None
            return Signal("SELL", entry, sl, tp, f"C05_AMD_SELL_{entry_zone[0]}")

    except Exception:
        return None
