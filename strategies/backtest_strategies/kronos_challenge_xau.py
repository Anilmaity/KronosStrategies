"""
Kronos CHALLENGE_XAU -- H4 Donchian Trend-Follow (static high-WR exits)
-----------------------------------------------------------------------
Port of bot/challenge_xau.py from the research repo: the deployable answer to
the "$5000 -> $5500" FundingPips challenge research. Exits re-optimized
2026-07-06 for the 70-80%-WR mandate: the chandelier trail (WR ~40%s, big
right tail) is replaced by a static SL 4xATR + TP 0.4R pair -- train WR 81.0%
PF 1.73 / test WR 86.2% PF 1.90 @0.45pt cost, robust at 0.80pt stress and
across the sl 3-4 x tp 0.4-0.75 neighborhood. The trailing variant lives in
git history.

Design (zero discretion), evaluated on the last CLOSED H4 bar:
  bias  : EMA20 > EMA50 (long-only) / EMA20 < EMA50 (short-only)
  entry : close breaks the prior-N(20) Donchian high (long) / low (short)
          in the bias direction
  stop  : static, entry -/+ 4.0 x ATR(14, H4)
  tp    : static, entry +/- 0.4R = 1.6 x ATR(14, H4)

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
    description="H4 Donchian(20) trend-follow, EMA20/50 bias, static SL 4xATR / "
                "static TP 0.4R (1.6xATR). High-WR geometry validated 2026-07-06 "
                "(train WR 81.0%/PF 1.73, test WR 86.2%/PF 1.90).",
    cooldown_s=14400,          # one H4 bar: at most one entry per closed H4 bar
    session_start_hour=None,   # trend-follow runs around the clock
    session_end_hour=None,
    max_concurrent_positions=1,
)

# ── Knobs ─────────────────────────────────────────────────────────────────────
# Exit geometry re-optimized 2026-07-06 for the 70-80%-WR mandate
# (backtest/optimize_manager_strategies.py): the 3xATR chandelier trail
# (WR ~40%s) is replaced by static SL 4xATR + TP 0.4R = 1.6xATR. Train
# WR 81.0% PF 1.73 / test WR 86.2% PF 1.90 @0.45pt cost; nearly unchanged at
# 0.80pt stress, with profitable sl 3-4 x tp 0.4-0.75 neighbors (plateau).
_N        = 20      # Donchian lookback (prior bars, excluding current)
_EMA_FAST = 20
_EMA_SLOW = 50
_ATR_N    = 14
_K_ATR    = 4.0     # static initial-stop distance
_TP_R     = 0.4     # TP as fraction of the SL distance => 1.6 x ATR

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

    risk = _K_ATR * A
    if close > donch_hi and up:
        return Signal(
            side="BUY",
            entry_price=close,
            stop_loss=round(close - risk, 2),
            take_profit=round(close + _TP_R * risk, 2),
            reason="CHALLENGE_XAU_LONG",
        )
    if close < donch_lo and not up:
        return Signal(
            side="SELL",
            entry_price=close,
            stop_loss=round(close + risk, 2),
            take_profit=round(close - _TP_R * risk, 2),
            reason="CHALLENGE_XAU_SHORT",
        )
    return None
