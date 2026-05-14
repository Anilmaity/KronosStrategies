"""
scalper_engine.py
-----------------
Pure signal functions for the Micro Liquidity Scalper (VAR3).

Thesis: On the 1m chart, price constantly oscillates between micro
liquidity pools (swing highs/lows of the last ~15 candles). Each sweep
of a micro pool that closes back inside creates a high-probability
small-target reversal — the Ping Pong model run continuously.

Goal: $5/hour profit at 0.01 lots → 5 points/hour.
Math: 8 trades/hr × 65% WR × (1.5pt TP / 1.0pt SL) = +5 pts/hr.

Public API
----------
get_micro_signal(candles_1m, candles_5m, ...) -> ScalperSignal | None
is_session_active(now_utc, allow_asia=False)  -> bool
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from strategy.liquidity_engine import (
    detect_liquidity_pools,
    detect_sweep,
    is_rejection_wick,
)


# ──────────────────────────────────────────────────────────────────────────────
# Output type
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ScalperSignal:
    side: str          # 'BUY' | 'SELL'
    entry_price: float
    stop_loss: float
    take_profit: float
    reason: str
    pool_level: float
    pool_type: str     # 'BSL' | 'SSL'
    is_equal: bool


# ──────────────────────────────────────────────────────────────────────────────
# Session filter — only trade when the market actually moves
# ──────────────────────────────────────────────────────────────────────────────
#
# London open: 07:00–11:00 UTC   (London 02:00–06:00 NY = killzone)
# NY open:     12:00–16:00 UTC   (NY 07:00–11:00 NY = killzone)
# We trade the bridge 07:00–16:00 UTC continuously since the scalper feeds on
# constant micro-volatility, not specific kill-zone setups.

def is_session_active(now_utc: datetime, allow_asia: bool = False, kill_zones_only: bool = False) -> bool:
    """Return True if we're inside active hours.

    Default (07:00–16:00 UTC): London + NY overlap — broad coverage.
    kill_zones_only=True: 07:00–09:00 + 13:00–15:00 UTC — only the highest-prob windows.
    """
    if allow_asia:
        return 0 <= now_utc.hour < 22
    if kill_zones_only:
        h = now_utc.hour
        return (7 <= h < 9) or (13 <= h < 15)
    return 7 <= now_utc.hour < 16


# ──────────────────────────────────────────────────────────────────────────────
# Volatility filter — skip dead markets
# ──────────────────────────────────────────────────────────────────────────────

def _recent_avg_range(candles_1m: pd.DataFrame, n: int = 5) -> float:
    """Avg high-low range over the last n completed 1m candles."""
    tail = candles_1m.tail(n)
    return float((tail["high"] - tail["low"]).mean())


# ──────────────────────────────────────────────────────────────────────────────
# Trend filter — don't fight a strong 5m trend
# ──────────────────────────────────────────────────────────────────────────────

def _trend_bias_5m(candles_5m: pd.DataFrame, ema_period: int = 20) -> tuple[str, float]:
    """Return (bias, distance_from_ema). bias is 'bull' | 'bear' | 'neutral'."""
    if len(candles_5m) < ema_period:
        return "neutral", 0.0
    closes = candles_5m["close"].tail(ema_period)
    ema = float(closes.mean())  # SMA approximation (simpler, no state)
    current = float(candles_5m.iloc[-1]["close"])
    distance = current - ema
    if abs(distance) < 0.5:
        return "neutral", distance
    return ("bull" if distance > 0 else "bear", distance)


# ──────────────────────────────────────────────────────────────────────────────
# Main signal function
# ──────────────────────────────────────────────────────────────────────────────

def get_micro_signal(
    candles_1m: pd.DataFrame,
    candles_5m: pd.DataFrame,
    *,
    tp_points: float = 1.5,
    sl_points: float = 1.0,
    min_wick_ratio: float = 1.3,
    min_avg_range: float = 0.5,
    counter_trend_max_dist: float = 4.0,
    n_pools: int = 5,
    pool_lookback: int = 2,
    equal_pools_only: bool = False,
    kill_zones_only: bool = False,
    now_utc: datetime | None = None,
    require_session: bool = True,
) -> ScalperSignal | None:
    """
    Detect a micro liquidity scalp opportunity on the latest completed 1m candle.

    Pipeline:
      1. Session filter (07:00–16:00 UTC)
      2. Volatility filter (recent 5-candle avg range >= min_avg_range)
      3. Pool detection (last n_pools swing H/L on 1m, lookback=2 for micro)
      4. Sweep detection (wick crosses pool, close back inside)
      5. Rejection wick filter (wick >= min_wick_ratio * body)
      6. Counter-trend filter (skip if price is far past 5m EMA against signal)
      7. Fixed-distance TP/SL from entry (consistent 1.5:1 R:R)

    Returns ScalperSignal or None.
    """
    if candles_1m.empty:
        return None

    now_utc = now_utc or datetime.now(timezone.utc)
    if require_session and not is_session_active(now_utc, kill_zones_only=kill_zones_only):
        return None

    # Volatility filter
    avg_range = _recent_avg_range(candles_1m, n=5)
    if avg_range < min_avg_range:
        return None

    # Build micro pools — smaller lookback (2) captures finer 1m swings
    pools = detect_liquidity_pools(candles_1m, lookback=pool_lookback, n_pools=n_pools)
    if equal_pools_only:
        pools = [p for p in pools if p.is_equal]
    if not pools:
        return None

    # Latest completed candle
    candle = candles_1m.iloc[-1]
    sweep = detect_sweep(candle, pools)
    if sweep is None:
        return None

    # Rejection wick (lower threshold than VAR1/VAR2 — micro moves have smaller wicks)
    if not is_rejection_wick(candle, min_wick_ratio=min_wick_ratio):
        return None


    # Counter-trend distance filter — if 5m price is far from EMA against the signal, skip
    if not candles_5m.empty:
        trend, distance = _trend_bias_5m(candles_5m)
        if sweep.direction == "BUY" and trend == "bear" and distance < -counter_trend_max_dist:
            return None
        if sweep.direction == "SELL" and trend == "bull" and distance > counter_trend_max_dist:
            return None

    # TP: prefer nearest opposing micro pool within [min_tp_points, 3pt], else fixed
    entry_price = float(candle["close"])
    min_tp_dist = sl_points * 1.2  # enforce at least 1.2:1 R:R
    if sweep.direction == "BUY":
        sl = round(entry_price - sl_points, 2)
        opposing = sorted([p.level for p in pools if p.type == "BSL" and p.level > entry_price + min_tp_dist])
        tp_dyn = opposing[0] if opposing else None
        if tp_dyn and (tp_dyn - entry_price) <= 3.0:
            tp = round(tp_dyn, 2)
        else:
            tp = round(entry_price + tp_points, 2)
    else:
        sl = round(entry_price + sl_points, 2)
        opposing = sorted([p.level for p in pools if p.type == "SSL" and p.level < entry_price - min_tp_dist], reverse=True)
        tp_dyn = opposing[0] if opposing else None
        if tp_dyn and (entry_price - tp_dyn) <= 3.0:
            tp = round(tp_dyn, 2)
        else:
            tp = round(entry_price - tp_points, 2)

    return ScalperSignal(
        side=sweep.direction,
        entry_price=round(entry_price, 2),
        stop_loss=sl,
        take_profit=tp,
        reason=f"MICRO_SCALP_{'EQ' if sweep.pool.is_equal else 'STD'}",
        pool_level=sweep.pool.level,
        pool_type=sweep.pool.type,
        is_equal=sweep.pool.is_equal,
    )
