"""
S12 — 90-Minute Cycle Fade with HTF Bias Confluence (Experiment E1c)
---------------------------------------------------------------------
Stacked-filter variant of S10:
  - Only fire BUY (M90_FADE_DN, fade a down-move) when 15m bias is BULL.
  - Only fire SELL (M90_FADE_UP, fade an up-move) when 15m bias is BEAR.

Hypothesis: the losing M90 trades are the ones fading *with* the dominant
HTF trend. Restricting fades to the direction of 15m bias should remove
the noisy "counter-trend" signals.

Bias = 15m close vs 15m EMA(21). See `strategies.base.htf_bias`.

Both directions kept because s10's BUY (M90_FADE_DN, +131 pts) and SELL
(M90_FADE_UP, -73 pts) baselines suggest BUY is fine and SELL is the side
that desperately needs a filter — together they should now both contribute
positively if the bias hypothesis holds.
"""

from __future__ import annotations
import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig, htf_bias
from backtest_strategies import s10_90min_fade

NAME = "M90_BIAS"
CONFIG = StrategyConfig(
    name=NAME,
    description="S10 90m fade gated by 15m EMA21 bias confluence (E1c)",
    cooldown_s=300,
    session_start_hour=7,
    session_end_hour=16,
)


def get_signal(w1m: pd.DataFrame, w5m: pd.DataFrame, w15m: pd.DataFrame, now_utc) -> Signal | None:
    sig = s10_90min_fade.get_signal(w1m, w5m, w15m, now_utc)
    if sig is None:
        return None

    bias = htf_bias(w15m, period=21)
    if bias is None:
        return None  # not enough 15m history yet

    # Only fade in the direction of HTF bias
    if sig.side == "BUY"  and bias != "BULL":
        return None
    if sig.side == "SELL" and bias != "BEAR":
        return None

    return Signal(
        side=sig.side,
        entry_price=sig.entry_price,
        stop_loss=sig.stop_loss,
        take_profit=sig.take_profit,
        reason=f"M90_BIAS_{sig.side}",
    )
