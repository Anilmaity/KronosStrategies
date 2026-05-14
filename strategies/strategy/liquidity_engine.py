"""
liquidity_engine.py
-------------------
Pure liquidity sweep signal functions. No DB access.

Public API
----------
detect_liquidity_pools(candles, lookback, n_pools) -> list[LiquidityPool]
detect_sweep(candle, pools)                        -> SweepEvent | None
is_rejection_wick(candle, min_wick_ratio)          -> bool
compute_tp_sl(sweep, entry_price, pools, fvgs, fixed_tp, sl_buffer) -> (tp, sl, mode)
"""

from __future__ import annotations

from dataclasses import dataclass
import pandas as pd

from strategy.ict_engine import FVGZone


# ──────────────────────────────────────────────────────────────────────────────
# Data types
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class LiquidityPool:
    type: str       # 'BSL' | 'SSL'
    level: float
    time: object    # pandas Timestamp
    is_equal: bool  # True if within 0.5 pts of another pool (equal high/low)


@dataclass
class SweepEvent:
    pool: LiquidityPool
    sweep_candle: pd.Series
    direction: str  # 'BUY' (swept SSL) | 'SELL' (swept BSL)


# ──────────────────────────────────────────────────────────────────────────────
# Pool detection
# ──────────────────────────────────────────────────────────────────────────────

def detect_liquidity_pools(
    candles: pd.DataFrame,
    lookback: int = 3,
    n_pools: int = 3,
    equal_tol: float = 0.5,
) -> list[LiquidityPool]:
    """
    Find the last n_pools swing highs (BSL) and n_pools swing lows (SSL).
    Marks equal highs/lows within equal_tol pts as is_equal=True.
    """
    df = candles.reset_index(drop=True)
    n = len(df)

    swing_highs: list[tuple[float, object]] = []
    swing_lows:  list[tuple[float, object]] = []

    for i in range(lookback, n - lookback):
        window = df.iloc[i - lookback: i + lookback + 1]
        if df.at[i, "high"] == window["high"].max():
            swing_highs.append((float(df.at[i, "high"]), df.at[i, "time"]))
        if df.at[i, "low"] == window["low"].min():
            swing_lows.append((float(df.at[i, "low"]), df.at[i, "time"]))

    # Keep the last n_pools of each
    swing_highs = swing_highs[-n_pools:]
    swing_lows  = swing_lows[-n_pools:]

    def _mark_equal(points: list[tuple[float, object]]) -> list[tuple[float, object, bool]]:
        levels = [p[0] for p in points]
        result = []
        for idx, (level, ts) in enumerate(points):
            is_eq = any(
                abs(level - levels[j]) <= equal_tol
                for j in range(len(levels)) if j != idx
            )
            result.append((level, ts, is_eq))
        return result

    pools: list[LiquidityPool] = []
    for level, ts, is_eq in _mark_equal(swing_highs):
        pools.append(LiquidityPool(type="BSL", level=level, time=ts, is_equal=is_eq))
    for level, ts, is_eq in _mark_equal(swing_lows):
        pools.append(LiquidityPool(type="SSL", level=level, time=ts, is_equal=is_eq))

    return pools


# ──────────────────────────────────────────────────────────────────────────────
# Sweep detection
# ──────────────────────────────────────────────────────────────────────────────

def detect_sweep(candle: pd.Series, pools: list[LiquidityPool]) -> SweepEvent | None:
    """
    Return a SweepEvent if the candle wick crosses a pool level and closes back inside.

    BUY  (swept SSL): low < pool.level AND close > pool.level
    SELL (swept BSL): high > pool.level AND close < pool.level
    """
    low   = float(candle["low"])
    high  = float(candle["high"])
    close = float(candle["close"])

    for pool in pools:
        if pool.type == "SSL":
            if low < pool.level and close > pool.level:
                return SweepEvent(pool=pool, sweep_candle=candle, direction="BUY")
        elif pool.type == "BSL":
            if high > pool.level and close < pool.level:
                return SweepEvent(pool=pool, sweep_candle=candle, direction="SELL")

    return None


# ──────────────────────────────────────────────────────────────────────────────
# Rejection wick filter
# ──────────────────────────────────────────────────────────────────────────────

def is_rejection_wick(candle: pd.Series, min_wick_ratio: float = 2.0) -> bool:
    """
    Return True if the sweep-side wick is >= min_wick_ratio * body size.
    Prevents entries on small-wick or doji candles.
    """
    o = float(candle["open"])
    c = float(candle["close"])
    h = float(candle["high"])
    l = float(candle["low"])

    body = abs(c - o)
    if body == 0:
        return False

    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    # Use the larger wick (the sweep side)
    max_wick = max(upper_wick, lower_wick)
    return max_wick >= min_wick_ratio * body


# ──────────────────────────────────────────────────────────────────────────────
# TP / SL computation
# ──────────────────────────────────────────────────────────────────────────────

def compute_tp_sl(
    sweep: SweepEvent,
    entry_price: float,
    pools: list[LiquidityPool],
    fvgs: list[FVGZone],
    fixed_tp: float = 4.0,
    sl_buffer: float = 1.0,
    dynamic_range: float = 15.0,
) -> tuple[float, float, str]:
    """
    Compute (take_profit, stop_loss, tp_mode).

    SL: always = swept wick extreme + sl_buffer

    TP priority:
      1. 15m FVG midpoint between entry and opposing pool
      2. Nearest opposing liquidity pool within dynamic_range pts
      3. Fallback: entry ± fixed_tp

    Returns (take_profit, stop_loss, mode)
    mode is one of: 'FIXED' | 'DYNAMIC_FVG' | 'DYNAMIC_POOL'
    """
    is_buy = sweep.direction == "BUY"

    # SL — beyond the wick extreme
    if is_buy:
        wick_extreme = float(sweep.sweep_candle["low"])
        sl = round(wick_extreme - sl_buffer, 2)
    else:
        wick_extreme = float(sweep.sweep_candle["high"])
        sl = round(wick_extreme + sl_buffer, 2)

    # TP 1 — FVG midpoint between entry and opposing pool
    if fvgs:
        opposing_pool = _nearest_opposing_pool(sweep.direction, pools, entry_price, dynamic_range)
        for fvg in reversed(fvgs):  # most recent first
            midpoint = (fvg.zone_low + fvg.zone_high) / 2
            if is_buy:
                fvg_valid = entry_price < midpoint
                in_range  = (opposing_pool is None) or (midpoint <= opposing_pool)
            else:
                fvg_valid = entry_price > midpoint
                in_range  = (opposing_pool is None) or (midpoint >= opposing_pool)
            if fvg_valid and in_range:
                return round(midpoint, 2), sl, "DYNAMIC_FVG"

    # TP 2 — nearest opposing pool within dynamic_range
    opposing = _nearest_opposing_pool(sweep.direction, pools, entry_price, dynamic_range)
    if opposing is not None:
        return round(opposing, 2), sl, "DYNAMIC_POOL"

    # TP 3 — fixed fallback
    tp = round(entry_price + fixed_tp if is_buy else entry_price - fixed_tp, 2)
    return tp, sl, "FIXED"


def _nearest_opposing_pool(
    direction: str,
    pools: list[LiquidityPool],
    entry_price: float,
    max_range: float,
) -> float | None:
    """Return the nearest opposing pool level within max_range, or None."""
    # BUY swept SSL → opposing = BSL (above entry)
    # SELL swept BSL → opposing = SSL (below entry)
    opposing_type = "BSL" if direction == "BUY" else "SSL"

    candidates: list[float] = []
    for p in pools:
        if p.type != opposing_type:
            continue
        dist = abs(p.level - entry_price)
        if dist <= max_range:
            if direction == "BUY" and p.level > entry_price:
                candidates.append(p.level)
            elif direction == "SELL" and p.level < entry_price:
                candidates.append(p.level)

    if not candidates:
        return None

    if direction == "BUY":
        return min(candidates)   # nearest above
    return max(candidates)       # nearest below
