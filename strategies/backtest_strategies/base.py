"""
strategies/base.py
------------------
Shared types for the strategy library. Every strategy in `strategies/sNN_*.py`
exports:

    NAME    : str                — unique short identifier (e.g. 'OTE')
    CONFIG  : StrategyConfig     — TP/SL points, cooldown, session window
    get_signal(window_1m, window_5m, window_15m, now_utc) -> Signal | None

The backtest runner walks 1m candles forward, calls each strategy's
get_signal on the appropriate window, and simulates exits with the
strategy's TP/SL config.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Signal:
    side: str            # 'BUY' | 'SELL'
    entry_price: float
    stop_loss: float
    take_profit: float
    reason: str          # short label for trade-log
    # Optional time-exit in MINUTES. None => exit only on SL/TP/manual (legacy
    # behaviour). When set, place_entry creates a CUSTOM/TIME_EXIT trigger and
    # position_monitor closes the position once that many minutes elapse.
    max_hold_min: float | None = None


@dataclass(frozen=True)
class StrategyConfig:
    name: str
    description: str
    cooldown_s: int = 60
    # session_filter:  if set, only signals within this UTC hour range are kept
    session_start_hour: int | None = 7
    session_end_hour:   int | None = 16
    # How many positions this ONE strategy may hold open at once. Default 1
    # preserves the legacy one-position-per-strategy cap; a portfolio strategy
    # (e.g. KRONOS_COMBINED_V2) raises it so several legs run concurrently.
    max_concurrent_positions: int = 1


def in_session(now_utc: datetime, cfg: StrategyConfig) -> bool:
    if cfg.session_start_hour is None or cfg.session_end_hour is None:
        return True
    return cfg.session_start_hour <= now_utc.hour < cfg.session_end_hour


def htf_bias(candles_15m, period: int = 21) -> str | None:
    """Return 'BULL' / 'BEAR' / None based on 15m close vs EMA(period).

    None when there isn't enough data to compute the EMA.
    Used as a confluence filter — strategies that fade should only fire
    in the direction of htf_bias to avoid fading the dominant trend.
    """
    if candles_15m is None or len(candles_15m) < period + 1:
        return None
    closes = candles_15m["close"].astype(float)
    ema = closes.ewm(span=period, adjust=False).mean().iloc[-1]
    last_close = float(closes.iloc[-1])
    if last_close > ema:
        return "BULL"
    if last_close < ema:
        return "BEAR"
    return None
