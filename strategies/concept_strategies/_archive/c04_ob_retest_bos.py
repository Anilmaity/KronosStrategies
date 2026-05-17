"""
C04 — Order Block Retest After London BOS (XAUUSD)
--------------------------------------------------
After London prints its first 15m BOS (preceded by an overnight-extreme
sweep), identify the OB that produced it. Enter on first retest of the
OB midpoint in the BOS direction.
"""

from __future__ import annotations
import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig
from strategy.ict_engine import detect_order_blocks

NAME = "C04_OB_RETEST_BOS"
CONFIG = StrategyConfig(
    name=NAME,
    description="London BOS + OB retest entry at 50% OB body",
    cooldown_s=1200,
    session_start_hour=7,
    session_end_hour=15,
)


def _overnight_extremes(w5m: pd.DataFrame, now_utc) -> tuple[float, float] | None:
    """Asia + late NY (yesterday 18:00 UTC -> today 07:00 UTC)."""
    if w5m is None or w5m.empty:
        return None
    df = w5m.copy()
    times = pd.to_datetime(df["time"])
    today = pd.Timestamp(now_utc).tz_localize(None).normalize()
    yesterday = today - pd.Timedelta(days=1)
    start = yesterday + pd.Timedelta(hours=18)
    end = today + pd.Timedelta(hours=7)
    sess = df[(times >= start) & (times < end)]
    if len(sess) < 10:
        return None
    return float(sess["low"].min()), float(sess["high"].max())


def _atr_15m(w15m: pd.DataFrame, n: int = 14) -> float:
    if w15m is None or len(w15m) < n:
        return 0.0
    tail = w15m.tail(n)
    return float((tail["high"] - tail["low"]).mean())


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    try:
        if (w15m is None or len(w15m) < 20
                or w5m is None or len(w5m) < 40
                or w1m is None or len(w1m) < 5):
            return None

        # London window 07-15 UTC
        if not (7 <= now_utc.hour < 15):
            return None

        ext = _overnight_extremes(w5m, now_utc)
        if ext is None:
            return None
        o_low, o_high = ext

        atr15 = _atr_15m(w15m)
        if atr15 <= 0:
            return None

        # Step 1: detect sweep in 07-10 UTC window on 5m
        df5 = w5m.copy()
        df5["time"] = pd.to_datetime(df5["time"])
        today = pd.Timestamp(now_utc).tz_localize(None).normalize()
        london = df5[(df5["time"] >= today + pd.Timedelta(hours=7))
                     & (df5["time"] < today + pd.Timedelta(hours=10))]
        if len(london) < 3:
            return None

        sweep_high = ((london["high"] > o_high) & (london["close"] < o_high)).any()
        sweep_low = ((london["low"] < o_low) & (london["close"] > o_low)).any()
        if not (sweep_high or sweep_low):
            return None

        # Step 2: 15m BOS — has any 15m candle in London window closed beyond
        # the most recent pre-London 15m swing in the reversal direction?
        df15 = w15m.copy()
        df15["time"] = pd.to_datetime(df15["time"])
        pre_london15 = df15[df15["time"] < today + pd.Timedelta(hours=7)]
        london15 = df15[(df15["time"] >= today + pd.Timedelta(hours=7))
                        & (df15["time"] <= pd.Timestamp(now_utc).tz_localize(None))]
        if len(pre_london15) < 4 or len(london15) < 1:
            return None

        # reversal direction:
        if sweep_high:
            direction = "SELL"
            ref_low = float(pre_london15["low"].tail(8).min())
            bos = (london15["close"] < ref_low).any()
        else:
            direction = "BUY"
            ref_high = float(pre_london15["high"].tail(8).max())
            bos = (london15["close"] > ref_high).any()

        if not bos:
            return None

        # Step 3: find OB on 15m in BOS direction (last opposing candle before
        # the displacement). Use detect_order_blocks on London-only 15m.
        # Provide enough context — last ~30 15m bars.
        obs = detect_order_blocks(df15.tail(40), displacement_mult=1.5)
        if not obs:
            return None
        wanted_type = "bearish" if direction == "SELL" else "bullish"
        # OBs formed during London only
        london_start = today + pd.Timedelta(hours=7)
        valid_obs = [o for o in obs
                     if o.type == wanted_type
                     and pd.to_datetime(o.time) >= london_start]
        if not valid_obs:
            return None
        ob = valid_obs[-1]
        mid = (ob.zone_low + ob.zone_high) / 2

        current_px = float(w1m.iloc[-1]["close"])
        # Must be retesting OB midpoint now (within 0.4 of mid)
        if abs(current_px - mid) > 0.4:
            return None

        if direction == "BUY":
            sl = round(ob.zone_low - 0.10 * atr15, 2)
            # TP: BOS impulse swing extreme — use london highs
            tp = round(float(london15["high"].max()) + 0.5, 2)
            entry = round(current_px, 2)
            if not (sl < entry < tp):
                return None
            if (tp - entry) < (entry - sl):
                return None
            return Signal("BUY", entry, sl, tp, "C04_OB_BULL_BOS")
        else:
            sl = round(ob.zone_high + 0.10 * atr15, 2)
            tp = round(float(london15["low"].min()) - 0.5, 2)
            entry = round(current_px, 2)
            if not (tp < entry < sl):
                return None
            if (entry - tp) < (sl - entry):
                return None
            return Signal("SELL", entry, sl, tp, "C04_OB_BEAR_BOS")

    except Exception:
        return None
