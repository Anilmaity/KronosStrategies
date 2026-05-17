"""
C15 - Post-News Second Move (Spike-Fade-Continuation) - XAUUSD
--------------------------------------------------------------
Adaptation of RBI Policy Reaction (two-move pattern) without a calendar.

Detects PRICE-ACTION SIGNATURE of the spike-and-fade-and-second-move
pattern that follows scheduled news:

  1) A 5m candle with range >= 2.0x the 20-bar 5m ATR (spike).
  2) The next TWO 5m bars retrace >= 50% of the spike candle's body.
  3) Enter on the break of the fade range high (if spike was bearish,
     i.e. continuation upward) or fade range low (if spike was bullish,
     continuation downward) in the FADE direction.
  4) TP = the pre-spike level (the spike candle's open).
  5) SL = the spike candle's extreme in the original spike direction.

This is essentially: "spike got rejected -> trade the rejection's
continuation back through the pre-spike price."
"""

from __future__ import annotations
import pandas as pd
import numpy as np

from backtest_strategies.base import Signal, StrategyConfig

NAME = "C15_SECOND_MOVE"
CONFIG = StrategyConfig(
    name=NAME,
    description="Spike-fade-continuation second-move trade on XAUUSD",
    cooldown_s=1800,
    session_start_hour=11,
    session_end_hour=20,
)


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    try:
        if w5m is None or len(w5m) < 25:
            return None
        if w1m is None or len(w1m) < 5:
            return None

        atr = float((w5m["high"] - w5m["low"]).tail(20).mean())
        if atr <= 0:
            return None

        # We need: spike candle at index -3, fade bars at -2 and -1,
        # and use the latest 1m close as the trigger.
        if len(w5m) < 4:
            return None

        spike = w5m.iloc[-3]
        fade1 = w5m.iloc[-2]
        fade2 = w5m.iloc[-1]

        spike_open = float(spike["open"])
        spike_close = float(spike["close"])
        spike_high = float(spike["high"])
        spike_low = float(spike["low"])
        spike_range = spike_high - spike_low
        spike_body = abs(spike_close - spike_open)

        if spike_range < 1.5 * atr or spike_range < 1.0:
            return None
        if spike_body < 0.5 * spike_range:
            return None

        bullish_spike = spike_close > spike_open
        # Required retrace = 50% of body, measured against extreme.
        if bullish_spike:
            # Fade should drag price DOWN from spike_close back toward
            # spike_open. Measure min(low) of the two fade bars.
            fade_min = min(float(fade1["low"]), float(fade2["low"]))
            retrace = spike_close - fade_min
            if retrace < 0.35 * spike_body:
                return None
            # Fade range: the two fade bars
            fade_low = fade_min
            fade_high = max(float(fade1["high"]), float(fade2["high"]))
            # Second-move = continuation of FADE (downward).
            # Enter on break of fade_low.
            last_1m_close = float(w1m.iloc[-1]["close"])
            if last_1m_close >= fade_low:
                return None
            entry = round(last_1m_close, 2)
            sl = round(spike_high, 2)            # stop above spike extreme
            tp = round(spike_open, 2)            # pre-spike level
            if not (tp < entry < sl):
                return None
            # Require min RR ~1
            if (entry - tp) < 0.5 * (sl - entry):
                return None
            return Signal("SELL", entry, sl, tp, "C15_SECOND_MOVE_DN")
        else:
            # Bearish spike, fade should push UP
            fade_max = max(float(fade1["high"]), float(fade2["high"]))
            retrace = fade_max - spike_close
            if retrace < 0.35 * spike_body:
                return None
            fade_high = fade_max
            fade_low = min(float(fade1["low"]), float(fade2["low"]))
            last_1m_close = float(w1m.iloc[-1]["close"])
            if last_1m_close <= fade_high:
                return None
            entry = round(last_1m_close, 2)
            sl = round(spike_low, 2)
            tp = round(spike_open, 2)
            if not (sl < entry < tp):
                return None
            if (tp - entry) < 0.5 * (entry - sl):
                return None
            return Signal("BUY", entry, sl, tp, "C15_SECOND_MOVE_UP")
    except Exception:
        return None
