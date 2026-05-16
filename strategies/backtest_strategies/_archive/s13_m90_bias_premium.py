"""
S13 — 90-Minute Cycle Fade, HTF-bias + London Open (Experiment E1d)
--------------------------------------------------------------------
Stacks three filters on top of S10:
  1. HTF bias confluence (15m close vs EMA21) — same as S12.
  2. Hour 9 UTC only (London open).
  3. Both sides allowed (BUY and SELL each had train→test WR holding ~70%).

Walk-forward (15d train / 15d test):
  hour=9 BUY:  train 75.0% (n=12) -> test 69.2% (n=13)
  hour=9 SELL: train 76.9% (n=13) -> test 66.7% (n=15)
Other "premium" cells from baseline overfit and are dropped:
  hour=15 BUY: train 82.4% (n=17) -> test 27.3% (n=22) — REJECTED.

Best honest estimate: ~70% WR borderline, ~1 trade/day. Sample is still small;
~90+ days of data needed before live promotion.
"""

from __future__ import annotations
import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig, htf_bias
from backtest_strategies import s10_90min_fade

NAME = "M90_LDN_BIAS"
CONFIG = StrategyConfig(
    name=NAME,
    description="S10 90m fade + 15m bias + London open hour only (E1d, walk-fwd validated)",
    cooldown_s=300,
    session_start_hour=9,
    session_end_hour=10,
)

# Walk-forward validated cells only — hour 9 UTC (London open), both sides.
_PREMIUM_CELLS: set[tuple[int, str]] = {
    (9, "BUY"),
    (9, "SELL"),
}


def get_signal(w1m: pd.DataFrame, w5m: pd.DataFrame, w15m: pd.DataFrame, now_utc) -> Signal | None:
    sig = s10_90min_fade.get_signal(w1m, w5m, w15m, now_utc)
    if sig is None:
        return None

    # Premium hour-side gate
    if (now_utc.hour, sig.side) not in _PREMIUM_CELLS:
        return None

    # HTF bias confluence
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
        reason=f"M90_LDN_BIAS_{sig.side}",
    )
