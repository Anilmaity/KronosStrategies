"""
C03 — 5-Minute Fair Value Gap (FVG) Fill (XAUUSD)
-------------------------------------------------
An unfilled 5m FVG inside an impulse leg that broke prior swing
structure. Trade the reaction off the gap in the impulse direction.
Trigger: price enters the gap and a 5m candle closes back outside the
gap in the impulse direction.
"""

from __future__ import annotations
import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig
from strategy.ict_engine import detect_fvgs

NAME = "C03_FVG_FILL"
CONFIG = StrategyConfig(
    name=NAME,
    description="5m FVG fill — enter on reaction-close back outside the gap",
    cooldown_s=600,
    session_start_hour=7,
    session_end_hour=20,
)


def _h1_ema_slope(w15m: pd.DataFrame, period: int = 20) -> str | None:
    """Approximate H1 EMA slope from 15m closes (every 4th bar)."""
    if w15m is None or len(w15m) < period * 4 + 4:
        return None
    closes = w15m["close"].astype(float)
    # Resample-ish: take every 4th bar to simulate H1
    h1 = closes.iloc[::4]
    if len(h1) < period + 4:
        return None
    ema = h1.ewm(span=period, adjust=False).mean()
    # 4-hour lookback on H1 = 4 bars back
    if ema.iloc[-1] > ema.iloc[-4]:
        return "BULL"
    if ema.iloc[-1] < ema.iloc[-4]:
        return "BEAR"
    return None


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    try:
        if w5m is None or len(w5m) < 30 or w1m is None or len(w1m) < 5:
            return None

        # Kill-zone filter: London 07-11 UTC or NY 13-18 UTC
        h = now_utc.hour
        if not ((7 <= h < 11) or (13 <= h < 18)):
            return None

        # 5m ATR
        recent = w5m.tail(20)
        atr5 = float((recent["high"] - recent["low"]).mean())
        if atr5 <= 0:
            return None

        # HTF trend
        bias = _h1_ema_slope(w15m)
        if bias is None:
            return None

        # Detect FVGs in recent history
        fvgs = detect_fvgs(w5m.tail(40), min_gap=0.3 * atr5)
        if not fvgs:
            return None

        current_px = float(w1m.iloc[-1]["close"])
        last5 = w5m.iloc[-1]
        prev5 = w5m.iloc[-2]

        wanted = "bullish" if bias == "BULL" else "bearish"

        # Most recent unfilled FVG of the desired type
        for fvg in reversed(fvgs):
            if fvg.type != wanted:
                continue
            # Check unfilled: no subsequent 5m close beyond far edge
            after = w5m[pd.to_datetime(w5m["time"]) > pd.to_datetime(fvg.time)]
            if fvg.type == "bullish":
                # Far edge is zone_low (the gap is above it from below)
                # Bullish FVG: price ABOVE the gap from impulse; fill = price returns to gap.
                # "Filled & broken" = close < zone_low
                if (after["close"] < fvg.zone_low).any():
                    continue
                # Impulse check — price made a new high after the FVG vs before
                # (simplified: skip strict structural check, gap-size already filters)

                # Trigger: prev candle entered gap, current closes back above gap top
                entered = (prev5["low"] <= fvg.zone_high) and (prev5["low"] >= fvg.zone_low - 0.2)
                reacted = float(last5["close"]) > fvg.zone_high
                if not (entered and reacted):
                    continue

                sl = round(fvg.zone_low - 0.10 * atr5, 2)
                # TP: recent swing high
                tp = round(float(w5m["high"].tail(20).max()) + 0.5, 2)
                entry = round(current_px, 2)
                if not (sl < entry < tp):
                    continue
                if (tp - entry) < (entry - sl):  # need at least 1:1
                    continue
                return Signal("BUY", entry, sl, tp, "C03_FVG_BULL")

            else:  # bearish
                if (after["close"] > fvg.zone_high).any():
                    continue
                entered = (prev5["high"] >= fvg.zone_low) and (prev5["high"] <= fvg.zone_high + 0.2)
                reacted = float(last5["close"]) < fvg.zone_low
                if not (entered and reacted):
                    continue
                sl = round(fvg.zone_high + 0.10 * atr5, 2)
                tp = round(float(w5m["low"].tail(20).min()) - 0.5, 2)
                entry = round(current_px, 2)
                if not (tp < entry < sl):
                    continue
                if (entry - tp) < (sl - entry):
                    continue
                return Signal("SELL", entry, sl, tp, "C03_FVG_BEAR")

        return None
    except Exception:
        return None
