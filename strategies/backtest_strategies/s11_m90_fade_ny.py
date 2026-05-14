"""
S11 — 90-Minute Cycle Fade, NY Open BUY-only (Experiment E1)
-------------------------------------------------------------
Stacked-filter variant of S10:
  - BUY direction only (M90_FADE_DN reason — fade down-moves with longs).
    Baseline: 47.7% WR / +131 pts on 342 trades over 30 days.
  - NY-open session only (14:00 <= hour < 17:00 UTC).
    Baseline (full M90 @ 15:00): 55.1% WR / +140 pts on 107 trades.

Hypothesis: stacking direction + session lifts WR above either filter alone.
Target: cross the 70% live-promotion bar.

Logic is delegated to s10's get_signal — this module only adds the filters.
"""

from __future__ import annotations
import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig
from strategies import s10_90min_fade

NAME = "M90_NY_BUY"
CONFIG = StrategyConfig(
    name=NAME,
    description="S10 90m fade — BUY-only + NY-open session (E1 stacked filter)",
    cooldown_s=300,
    session_start_hour=14,
    session_end_hour=17,
)

_BUY_REASON = "M90_FADE_DN"  # in s10 nomenclature, DN = price ran down → BUY back to open


def get_signal(w1m: pd.DataFrame, w5m: pd.DataFrame, w15m: pd.DataFrame, now_utc) -> Signal | None:
    sig = s10_90min_fade.get_signal(w1m, w5m, w15m, now_utc)
    if sig is None or sig.side != "BUY" or sig.reason != _BUY_REASON:
        return None
    # Re-tag so this variant is distinguishable in the combined CSV
    return Signal(
        side=sig.side,
        entry_price=sig.entry_price,
        stop_loss=sig.stop_loss,
        take_profit=sig.take_profit,
        reason="M90_NY_BUY",
    )
