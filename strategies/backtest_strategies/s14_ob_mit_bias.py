"""
S14 — Order Block Mitigation with HTF Bias Confluence (Experiment E2)
----------------------------------------------------------------------
Stacks 15m EMA21 bias filter on top of S03's OB mitigation logic.

Hypothesis (parallel to E1c on M90_FADE): the losing OB_MIT trades are
those where the OB direction conflicts with the dominant 15m trend.
Filtering to bias-aligned mitigations should lift WR materially.

Baseline (S03, 30d): 36.4% WR, +160.9 pts, max DD -93.4 on 546 trades.
"""

from __future__ import annotations
import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig, htf_bias
from backtest_strategies import s03_ob_mitigation

NAME = "OB_MIT_BIAS"
CONFIG = StrategyConfig(
    name=NAME,
    description="S03 OB mitigation gated by 15m EMA21 bias confluence (E2)",
    cooldown_s=300,
    session_start_hour=7,
    session_end_hour=16,
)


def get_signal(w1m: pd.DataFrame, w5m: pd.DataFrame, w15m: pd.DataFrame, now_utc) -> Signal | None:
    sig = s03_ob_mitigation.get_signal(w1m, w5m, w15m, now_utc)
    if sig is None:
        return None

    bias = htf_bias(w15m, period=21)
    if bias is None:
        return None
    if sig.side == "BUY"  and bias != "BULL":
        return None
    if sig.side == "SELL" and bias != "BEAR":
        return None

    return Signal(
        side=sig.side,
        entry_price=sig.entry_price,
        stop_loss=sig.stop_loss,
        take_profit=sig.take_profit,
        reason=f"OB_BIAS_{sig.side}",
    )
