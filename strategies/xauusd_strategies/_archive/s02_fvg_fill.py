"""
Strategy 02 - Fair Value Gap Fill (ICT/SMC #2)

H1 FVG (3-candle imbalance) in direction of HTF bias (H4 EMA50).
M15 execution. Limit at FVG midpoint. Stop beyond FVG far side + 0.1*ATR.
Target 2R. Cancel after 96 M15 bars or close beyond FVG.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .engine import PendingOrder, run_backtest, save_results

DEFAULTS = dict(
    h4_ema=50,
    stop_buffer_atr=0.1,
    rr_target=2.0,
    fvg_max_age_h1=10,  # H1 bars
)
RESULTS_DIR = Path(__file__).resolve().parent / "results_strategy_02"


def build_features(m15: pd.DataFrame, h4_ema: int) -> pd.DataFrame:
    """Add columns: fvg_long_low, fvg_long_high, fvg_short_low, fvg_short_high, bias.
    For each M15 bar, these reflect the most recent unfilled H1 FVG (within max-age)
    and current H4 bias.
    """
    h1 = m15["close"].resample("1h", label="left", closed="left").ohlc().dropna()
    h1["high"] = m15["high"].resample("1h", label="left", closed="left").max()
    h1["low"] = m15["low"].resample("1h", label="left", closed="left").min()

    h4 = m15["close"].resample("4h", label="left", closed="left").ohlc().dropna()
    h4["ema"] = h4["close"].ewm(span=h4_ema, adjust=False).mean()
    h4["bias"] = np.where(h4["close"] > h4["ema"], 1, np.where(h4["close"] < h4["ema"], -1, 0))

    # detect FVGs on H1
    h1["fvg_long_low"] = np.nan
    h1["fvg_long_high"] = np.nan
    h1["fvg_short_low"] = np.nan
    h1["fvg_short_high"] = np.nan
    hi = h1["high"].values
    lo = h1["low"].values
    for t in range(2, len(h1)):
        if lo[t] > hi[t - 2]:  # bullish FVG
            h1.iat[t, h1.columns.get_loc("fvg_long_low")] = hi[t - 2]
            h1.iat[t, h1.columns.get_loc("fvg_long_high")] = lo[t]
        if hi[t] < lo[t - 2]:  # bearish FVG
            h1.iat[t, h1.columns.get_loc("fvg_short_low")] = hi[t]
            h1.iat[t, h1.columns.get_loc("fvg_short_high")] = lo[t - 2]

    # forward fill FVGs onto M15 timeline (one most-recent per direction)
    h1_ff = h1[["fvg_long_low", "fvg_long_high", "fvg_short_low", "fvg_short_high"]]
    h1_ff = h1_ff.reindex(m15.index, method="ffill")
    h4_ff = h4[["bias"]].reindex(m15.index, method="ffill")

    out = m15.copy()
    out["fvg_long_low"] = h1_ff["fvg_long_low"]
    out["fvg_long_high"] = h1_ff["fvg_long_high"]
    out["fvg_short_low"] = h1_ff["fvg_short_low"]
    out["fvg_short_high"] = h1_ff["fvg_short_high"]
    out["bias"] = h4_ff["bias"]
    return out


def make_detector(stop_buffer_atr: float, rr_target: float, **_):
    seen = {"long": None, "short": None}  # remember last FVG band we've signaled

    def detect(df, i, a) -> Optional[PendingOrder]:
        bar = df.iloc[i]
        bias = bar.get("bias", 0)
        if pd.isna(bias):
            return None
        if bias > 0 and not pd.isna(bar["fvg_long_low"]):
            band = (float(bar["fvg_long_low"]), float(bar["fvg_long_high"]))
            if seen["long"] != band and bar["close"] > band[1]:
                # price still above FVG -> wait for retrace
                seen["long"] = band
                low, high = band
                entry = (low + high) / 2.0
                stop = low - stop_buffer_atr * a
                if entry <= stop:
                    return None
                tp = entry + rr_target * (entry - stop)
                return PendingOrder("long", entry, stop, tp, low, 1e18, i)
        if bias < 0 and not pd.isna(bar["fvg_short_low"]):
            band = (float(bar["fvg_short_low"]), float(bar["fvg_short_high"]))
            if seen["short"] != band and bar["close"] < band[0]:
                seen["short"] = band
                low, high = band
                entry = (low + high) / 2.0
                stop = high + stop_buffer_atr * a
                if entry >= stop:
                    return None
                tp = entry - rr_target * (stop - entry)
                return PendingOrder("short", entry, stop, tp, -1e18, high, i)
        return None
    return detect


def main():
    ohlc = resample_ohlc(load_ticks(), "15min")
    feat = build_features(ohlc, DEFAULTS["h4_ema"])
    print(f"M15 bars: {len(feat):,}")
    trades = run_backtest(feat, make_detector(**DEFAULTS))
    m = save_results(trades, RESULTS_DIR, "Strategy 02 - FVG Fill",
                     "NOTE: 6mo data only; harness/directional only.")
    for k, v in m.items():
        print(f"{k:>22}: {v}")


if __name__ == "__main__":
    main()
