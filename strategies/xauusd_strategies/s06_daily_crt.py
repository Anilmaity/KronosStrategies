"""
Strategy 06 - Daily CRT (CRT #1)

D1 = range. D2 = sweeps one side of D1 but closes back inside. D3 = expansion
in opposite direction. Entry at D3 open (market). Stop beyond D2 extreme.
Target = D1 opposite extreme.

Operates on daily bars.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .engine import PendingOrder, run_backtest, save_results

DEFAULTS = dict(
    stop_buffer_atr=0.1,
    min_pierce_atr=0.05,
)
RESULTS_DIR = Path(__file__).resolve().parent / "results_strategy_06"


def make_detector(stop_buffer_atr: float, min_pierce_atr: float):
    def detect(df, i, a) -> Optional[PendingOrder]:
        # Signal at end of D2 (= bar i). Order fills at D3 open (bar i+1).
        if i < 2:
            return None
        d1 = df.iloc[i - 1]   # the "range" candle
        d2 = df.iloc[i]       # the "sweep" candle (current)

        # Sweep up: D2.high > D1.high, D2.close < D1.high -> short expected
        if d2["high"] > d1["high"] + min_pierce_atr * a and d2["close"] < d1["high"]:
            entry = d2["close"]    # will be replaced by next-bar open via market fill
            stop = d2["high"] + stop_buffer_atr * a
            tp = d1["low"]
            if stop <= entry or tp >= entry:
                return None
            return PendingOrder("short", entry, stop, tp, -1e18, d2["high"], i,
                                order_type="market")

        # Sweep down: D2.low < D1.low, D2.close > D1.low -> long expected
        if d2["low"] < d1["low"] - min_pierce_atr * a and d2["close"] > d1["low"]:
            entry = d2["close"]
            stop = d2["low"] - stop_buffer_atr * a
            tp = d1["high"]
            if stop >= entry or tp <= entry:
                return None
            return PendingOrder("long", entry, stop, tp, d2["low"], 1e18, i,
                                order_type="market")
        return None
    return detect


def main():
    ticks = load_ticks()
    daily = resample_ohlc(ticks, "1D")
    print(f"Daily bars: {len(daily)}")
    trades = run_backtest(daily, make_detector(**DEFAULTS), valid_bars=1)
    m = save_results(trades, RESULTS_DIR, "Strategy 06 - Daily CRT",
                     "NOTE: 6mo cache only (~130 daily bars).")
    for k, v in m.items():
        print(f"{k:>22}: {v}")


if __name__ == "__main__":
    main()
