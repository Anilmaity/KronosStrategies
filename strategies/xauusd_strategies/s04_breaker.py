"""
Strategy 04 - Breaker Block Continuation (ICT/SMC #4)

A breaker = an OB that was violated. On retest of the violated zone,
trade in the direction of the violation (continuation).

M15. Detect bullish/bearish OB candidates via BOS + displacement (re-uses
Strategy 1 detection). Track each OB. If a later close pierces the OB
extreme on the opposite side, flag it as a breaker. On retest (price
returns into the zone), enter market in continuation direction.
Stop beyond breaker far side. Target 2R.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .engine import PendingOrder, run_backtest, save_results

DEFAULTS = dict(
    swing_lookback=5,
    displace_atr_mult=1.5,
    ob_search_bars=10,
    stop_buffer_atr=0.1,
    rr_target=2.0,
    breaker_max_age=200,  # bars before we drop a breaker
)
RESULTS_DIR = Path(__file__).resolve().parent / "results_strategy_04"


@dataclass
class Breaker:
    side: str  # the trade side on retest ('short' for broken-bullish-OB, 'long' for broken-bearish-OB)
    low: float
    high: float
    created_idx: int
    triggered: bool = False


def make_detector(swing_lookback: int, displace_atr_mult: float, ob_search_bars: int,
                  stop_buffer_atr: float, rr_target: float, breaker_max_age: int):
    # active OBs (not yet broken)
    pending_obs: list[dict] = []  # {side, low, high, created_idx}
    breakers: list[Breaker] = []

    def detect(df, i, a) -> Optional[PendingOrder]:
        if i < max(14, swing_lookback + ob_search_bars):
            return None
        bar = df.iloc[i]

        # 1) prune expired
        breakers[:] = [b for b in breakers if i - b.created_idx <= breaker_max_age and not b.triggered]
        pending_obs[:] = [ob for ob in pending_obs if i - ob["created_idx"] <= breaker_max_age]

        # 2) detect new OB on this bar (using strategy_01 logic)
        body = bar["close"] - bar["open"]
        if abs(body) > displace_atr_mult * a:
            prior_high = df["high"].iloc[i - swing_lookback : i].max()
            prior_low = df["low"].iloc[i - swing_lookback : i].min()
            if body > 0 and bar["close"] > prior_high:
                window = df.iloc[i - ob_search_bars : i]
                bearish = window[window["close"] < window["open"]]
                if not bearish.empty:
                    ob = bearish.iloc[-1]
                    pending_obs.append({"side": "long", "low": float(ob["close"]),
                                        "high": float(ob["open"]), "created_idx": i})
            elif body < 0 and bar["close"] < prior_low:
                window = df.iloc[i - ob_search_bars : i]
                bullish = window[window["close"] > window["open"]]
                if not bullish.empty:
                    ob = bullish.iloc[-1]
                    pending_obs.append({"side": "short", "low": float(ob["open"]),
                                        "high": float(ob["close"]), "created_idx": i})

        # 3) check if any active OB just got broken -> flip to breaker
        remaining = []
        for ob in pending_obs:
            if ob["side"] == "long" and bar["close"] < ob["low"]:
                # bullish OB invalidated -> bearish breaker
                breakers.append(Breaker("short", ob["low"], ob["high"], i))
            elif ob["side"] == "short" and bar["close"] > ob["high"]:
                breakers.append(Breaker("long", ob["low"], ob["high"], i))
            else:
                remaining.append(ob)
        pending_obs[:] = remaining

        # 4) retest detection: price re-enters a breaker zone
        for b in breakers:
            if b.triggered:
                continue
            in_zone = b.low <= bar["close"] <= b.high
            if not in_zone:
                continue
            if b.side == "short":
                entry = bar["close"]
                stop = b.high + stop_buffer_atr * a
                if stop <= entry:
                    continue
                tp = entry - rr_target * (stop - entry)
                b.triggered = True
                return PendingOrder("short", entry, stop, tp, -1e18, b.high, i, order_type="market")
            else:
                entry = bar["close"]
                stop = b.low - stop_buffer_atr * a
                if stop >= entry:
                    continue
                tp = entry + rr_target * (entry - stop)
                b.triggered = True
                return PendingOrder("long", entry, stop, tp, b.low, 1e18, i, order_type="market")

        return None
    return detect


def main():
    ohlc = resample_ohlc(load_ticks(), "15min")
    trades = run_backtest(ohlc, make_detector(**DEFAULTS))
    m = save_results(trades, RESULTS_DIR, "Strategy 04 - Breaker Block Continuation",
                     "NOTE: 6mo data only.")
    for k, v in m.items():
        print(f"{k:>22}: {v}")


if __name__ == "__main__":
    main()
