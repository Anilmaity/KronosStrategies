"""
ict_engine.py
-------------
Pure ICT/SMC analysis functions. No DB access — all inputs are DataFrames.

Public API
----------
get_market_structure(candles)           -> 'bullish' | 'bearish' | 'ranging'
detect_fvgs(candles)                    -> list[FVGZone]
detect_order_blocks(candles)            -> list[OBZone]
get_htf_bias(candles_4h, candles_1h)    -> 'long' | 'short' | 'neutral'
get_liquidity_targets(candles)          -> {'nearest_high': float, 'nearest_low': float}
check_entry(candles_15m, candles_1m, bias) -> EntrySignal | None
"""

from __future__ import annotations
from dataclasses import dataclass
import pandas as pd


# ──────────────────────────────────────────────────────────────────────────────
# Data types
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class EntrySignal:
    side: str          # 'BUY' or 'SELL'
    entry_price: float
    stop_loss: float
    take_profit: float
    reason: str        # 'FVG_RETEST' | 'OB_RETEST'
    zone_low: float
    zone_high: float
    max_hold_min: float | None = None   # optional time-exit (minutes); see base.Signal
    trailing: bool = False              # chandelier trailing exit; see base.Signal


@dataclass
class FVGZone:
    type: str      # 'bullish' | 'bearish'
    zone_low: float
    zone_high: float
    time: object   # pandas Timestamp


@dataclass
class OBZone:
    type: str      # 'bullish' | 'bearish'
    zone_low: float
    zone_high: float
    time: object


# ──────────────────────────────────────────────────────────────────────────────
# Swing structure helpers
# ──────────────────────────────────────────────────────────────────────────────

def _swing_points(candles: pd.DataFrame, lookback: int = 3) -> pd.DataFrame:
    """Add boolean swing_high / swing_low columns to a copy of candles."""
    df = candles.copy().reset_index(drop=True)
    df["swing_high"] = False
    df["swing_low"] = False

    n = len(df)
    for i in range(lookback, n - lookback):
        window = df.iloc[i - lookback : i + lookback + 1]
        if df.at[i, "high"] == window["high"].max():
            df.at[i, "swing_high"] = True
        if df.at[i, "low"] == window["low"].min():
            df.at[i, "swing_low"] = True

    return df


def get_market_structure(candles: pd.DataFrame, lookback: int = 3) -> str:
    """Determine trend from the last two swing highs and lows."""
    if len(candles) < lookback * 2 + 3:
        return "ranging"

    df = _swing_points(candles, lookback)
    highs = df[df["swing_high"]]["high"].values
    lows = df[df["swing_low"]]["low"].values

    if len(highs) < 2 or len(lows) < 2:
        return "ranging"

    hh = highs[-1] > highs[-2]
    hl = lows[-1] > lows[-2]
    lh = highs[-1] < highs[-2]
    ll = lows[-1] < lows[-2]

    if hh and hl:
        return "bullish"
    if lh and ll:
        return "bearish"
    return "ranging"


def get_liquidity_targets(candles: pd.DataFrame, lookback: int = 3) -> dict:
    """Return the nearest swing high and low as liquidity targets."""
    df = _swing_points(candles, lookback)
    highs = df[df["swing_high"]]["high"].values
    lows = df[df["swing_low"]]["low"].values
    return {
        "nearest_high": float(highs[-1]) if len(highs) else None,
        "nearest_low": float(lows[-1]) if len(lows) else None,
    }


# ──────────────────────────────────────────────────────────────────────────────
# FVG detection
# ──────────────────────────────────────────────────────────────────────────────

def detect_fvgs(candles: pd.DataFrame, min_gap: float = 0.30) -> list[FVGZone]:
    """
    Detect Fair Value Gaps (3-candle pattern).

    Bullish FVG : candle[i-2].high < candle[i].low  (gap with middle bullish impulse)
    Bearish FVG : candle[i-2].low  > candle[i].high (gap with middle bearish impulse)
    """
    fvgs: list[FVGZone] = []
    df = candles.reset_index(drop=True)

    for i in range(2, len(df)):
        c0, c1, c2 = df.iloc[i - 2], df.iloc[i - 1], df.iloc[i]

        bull_gap = c2["low"] - c0["high"]
        if bull_gap >= min_gap and c1["close"] > c1["open"]:
            fvgs.append(FVGZone(
                type="bullish",
                zone_low=float(c0["high"]),
                zone_high=float(c2["low"]),
                time=c2["time"],
            ))

        bear_gap = c0["low"] - c2["high"]
        if bear_gap >= min_gap and c1["close"] < c1["open"]:
            fvgs.append(FVGZone(
                type="bearish",
                zone_low=float(c2["high"]),
                zone_high=float(c0["low"]),
                time=c2["time"],
            ))

    return fvgs


# ──────────────────────────────────────────────────────────────────────────────
# Order Block detection
# ──────────────────────────────────────────────────────────────────────────────

def detect_order_blocks(
    candles: pd.DataFrame,
    displacement_mult: float = 1.5,
) -> list[OBZone]:
    """
    Detect Order Blocks — last opposing candle before a displacement move.

    Bullish OB : last bearish candle before a large bullish impulse candle.
    Bearish OB : last bullish candle before a large bearish impulse candle.
    """
    obs: list[OBZone] = []
    df = candles.reset_index(drop=True)
    avg_body = (df["close"] - df["open"]).abs().mean()
    if avg_body == 0:
        return obs

    for i in range(1, len(df)):
        prev, curr = df.iloc[i - 1], df.iloc[i]
        curr_body = abs(curr["close"] - curr["open"])
        is_displacement = curr_body > avg_body * displacement_mult

        if not is_displacement:
            continue

        # Bullish OB
        if prev["close"] < prev["open"] and curr["close"] > curr["open"]:
            obs.append(OBZone(
                type="bullish",
                zone_low=float(prev["low"]),
                zone_high=float(prev["high"]),
                time=prev["time"],
            ))

        # Bearish OB
        elif prev["close"] > prev["open"] and curr["close"] < curr["open"]:
            obs.append(OBZone(
                type="bearish",
                zone_low=float(prev["low"]),
                zone_high=float(prev["high"]),
                time=prev["time"],
            ))

    return obs


# ──────────────────────────────────────────────────────────────────────────────
# HTF bias
# ──────────────────────────────────────────────────────────────────────────────

def get_htf_bias(candles_4h: pd.DataFrame, candles_1h: pd.DataFrame) -> str:
    """
    Determine high-timeframe directional bias.
    4H structure is the primary filter; 1H must agree or be ranging.
    Returns 'long' | 'short' | 'neutral'.
    """
    s4h = get_market_structure(candles_4h, lookback=3)
    s1h = get_market_structure(candles_1h, lookback=3)

    if s4h == "bullish" and s1h in ("bullish", "ranging"):
        return "long"
    if s4h == "bearish" and s1h in ("bearish", "ranging"):
        return "short"
    return "neutral"


# ──────────────────────────────────────────────────────────────────────────────
# Entry trigger
# ──────────────────────────────────────────────────────────────────────────────

def check_entry(
    candles_15m: pd.DataFrame,
    candles_1m: pd.DataFrame,
    bias: str,
    sl_buffer: float = 0.50,
    min_rr: float = 1.5,
    fvg_lookback_candles: int = 30,
) -> EntrySignal | None:
    """
    Check for a valid ICT entry on the latest 1m close.

    Conditions:
      1. HTF bias is 'long' or 'short'
      2. A recent 15m FVG or OB exists in the bias direction
      3. Current 1m close price is inside that zone
      4. Risk:Reward >= min_rr
    """
    if bias == "neutral" or candles_1m.empty or candles_15m.empty:
        return None

    current_price = float(candles_1m.iloc[-1]["close"])

    # ── Gather zones ─────────────────────────────────────────────────────────
    fvgs = detect_fvgs(candles_15m)
    obs = detect_order_blocks(candles_15m)

    wanted_type = "bullish" if bias == "long" else "bearish"

    # Keep only recent zones (last N candles)
    cutoff = candles_15m.iloc[-fvg_lookback_candles]["time"] if len(candles_15m) >= fvg_lookback_candles else candles_15m.iloc[0]["time"]

    zones = []
    for z in fvgs:
        if z.type == wanted_type and z.time >= cutoff:
            zones.append({"low": z.zone_low, "high": z.zone_high, "time": z.time, "reason": "FVG_RETEST"})
    for o in obs:
        if o.type == wanted_type and o.time >= cutoff:
            zones.append({"low": o.zone_low, "high": o.zone_high, "time": o.time, "reason": "OB_RETEST"})

    if not zones:
        return None

    # Sort zones — most recent first
    zones.sort(key=lambda z: z["time"], reverse=True)

    liquidity = get_liquidity_targets(candles_15m)

    for zone in zones:
        zl, zh = zone["low"], zone["high"]

        if not (zl <= current_price <= zh):
            continue

        if bias == "long":
            sl = zl - sl_buffer
            tp = liquidity["nearest_high"]
            if tp is None or tp <= current_price:
                continue
            if (current_price - sl) <= 0:
                continue
            rr = (tp - current_price) / (current_price - sl)
            if rr < min_rr:
                continue
            return EntrySignal(
                side="BUY",
                entry_price=round(current_price, 2),
                stop_loss=round(sl, 2),
                take_profit=round(tp, 2),
                reason=zone["reason"],
                zone_low=zl,
                zone_high=zh,
            )

        else:  # short
            sl = zh + sl_buffer
            tp = liquidity["nearest_low"]
            if tp is None or tp >= current_price:
                continue
            if (sl - current_price) <= 0:
                continue
            rr = (current_price - tp) / (sl - current_price)
            if rr < min_rr:
                continue
            return EntrySignal(
                side="SELL",
                entry_price=round(current_price, 2),
                stop_loss=round(sl, 2),
                take_profit=round(tp, 2),
                reason=zone["reason"],
                zone_low=zl,
                zone_high=zh,
            )

    return None
