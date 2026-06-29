"""
Kronos CHALLENGE_XAU -- H4 Donchian Trend-Follow (chandelier trailing exit)
---------------------------------------------------------------------------
Port of bot/challenge_xau.py from the research repo: the deployable answer to
the "$5000 -> $5500" FundingPips challenge research. The manual high-win-rate
no-stop scalp reproduces its win rate in backtest but ruins a challenge ~72.5%
of the time; this trend-follow edge (PF 1.83 on H4, positive every year
2023-2026) passes when sized so a -1R loss stays inside the daily limit.

Design (zero discretion), evaluated on the last CLOSED H4 bar:
  bias  : EMA20 > EMA50 (long-only) / EMA20 < EMA50 (short-only)
  entry : close breaks the prior-N(20) Donchian high (long) / low (short)
          in the bias direction
  stop  : initial hard stop = entry -/+ K_ATR * ATR(14); thereafter a
          chandelier TRAILING stop (Signal.trailing=True -> position_monitor
          ratchets it off the high/low-water mark). No fixed take-profit:
          a far placeholder TP is emitted only as a broker backstop so the
          right tail is never capped server-side.

Working timeframe is H4, resampled from the 15m runner window. The live
research_runner must pull enough 15m history to form >= EMA_SLOW + N closed H4
bars -- set RESEARCH_WIN_15M / RESEARCH_DAYS_15M accordingly (see compose.yml
and deploy_challenge_xau.py). The last (still-forming) H4 bucket is always
dropped, so the signal is strictly causal.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig
from backtest_strategies._kronos_indicators import ema, atr

NAME = "CHALLENGE_XAU"
CONFIG = StrategyConfig(
    name=NAME,
    description="H4 Donchian(20) trend-follow, EMA20/50 bias, 3xATR chandelier "
                "trailing stop. Port of bot/challenge_xau.py (5000->5500).",
    cooldown_s=14400,          # one H4 bar: at most one entry per closed H4 bar
    session_start_hour=None,   # trend-follow runs around the clock
    session_end_hour=None,
    max_concurrent_positions=1,
)

# ── Knobs (match bot/challenge_xau.py defaults) ───────────────────────────────
_N        = 20      # Donchian lookback (prior bars, excluding current)
_EMA_FAST = 20
_EMA_SLOW = 50
_ATR_N    = 14
_K_ATR    = 3.0     # chandelier multiple == initial-stop distance
_FAR_ATR  = 50.0    # broker placeholder TP distance (never realistically hit)

# Minimum closed H4 bars needed: EMA_SLOW stabilisation + Donchian lookback.
_MIN_H4 = _EMA_SLOW + _N + 2


def _resample_h4(w15m: pd.DataFrame) -> pd.DataFrame:
    """Resample a 15m window to H4 OHLC (UTC, midnight-aligned buckets),
    dropping the last still-forming bucket so every returned bar is CLOSED."""
    t = pd.to_datetime(w15m["time"], utc=True)
    df = pd.DataFrame(
        {
            "open":  w15m["open"].astype(float).to_numpy(),
            "high":  w15m["high"].astype(float).to_numpy(),
            "low":   w15m["low"].astype(float).to_numpy(),
            "close": w15m["close"].astype(float).to_numpy(),
        },
        index=t,
    )
    h4 = (
        df.resample("4h", label="left", closed="left")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )
    # The final bucket is (almost always) still forming -> not a closed bar.
    return h4.iloc[:-1] if len(h4) else h4


def get_signal(w1m, w5m, w15m: pd.DataFrame, now_utc: datetime) -> Signal | None:
    if w15m is None or len(w15m) < _MIN_H4 * 16:  # ~16 fifteen-min bars per H4
        return None

    h4 = _resample_h4(w15m)
    if len(h4) < _MIN_H4:
        return None

    c = h4["close"].reset_index(drop=True)
    h = h4["high"].reset_index(drop=True)
    lo = h4["low"].reset_index(drop=True)

    ef = ema(c, _EMA_FAST)
    es = ema(c, _EMA_SLOW)
    a = atr(h4.reset_index(drop=True), _ATR_N)

    i = len(c) - 1
    A = float(a.iloc[i])
    if not (A > 0):
        return None
    if pd.isna(ef.iloc[i]) or pd.isna(es.iloc[i]):
        return None

    donch_hi = float(h.iloc[i - _N:i].max())
    donch_lo = float(lo.iloc[i - _N:i].min())
    close = float(c.iloc[i])
    up = float(ef.iloc[i]) > float(es.iloc[i])

    if close > donch_hi and up:
        return Signal(
            side="BUY",
            entry_price=close,
            stop_loss=round(close - _K_ATR * A, 2),
            take_profit=round(close + _FAR_ATR * A, 2),  # broker backstop only
            reason="CHALLENGE_XAU_LONG",
            trailing=True,
        )
    if close < donch_lo and not up:
        return Signal(
            side="SELL",
            entry_price=close,
            stop_loss=round(close + _K_ATR * A, 2),
            take_profit=round(close - _FAR_ATR * A, 2),  # broker backstop only
            reason="CHALLENGE_XAU_SHORT",
            trailing=True,
        )
    return None
