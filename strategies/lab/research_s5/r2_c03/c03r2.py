"""c03r2 -- research variant of concept_strategies/c03_fvg_fill.py (r2_c03, 2026-09-18).

Same code path as the shipped module, with the inline literals lifted to module constants so
`lab.harness.Cfg.patch` can reach them, plus two research-only overlays (daily-bias gate,
bias-definition switch). At the defaults below it must reproduce the shipped module's trade
list exactly (H0 in PROTOCOL.md). Loaded by the harness as `concept_strategies.c03r2` via
run_arms.py, which appends this folder to that package's __path__ in each worker.

NOT a live module: the daily-bias overlay reads the daily parquet from LAB_BARS_CACHE at import
time (look-ahead safe: a daily bar is used only once now >= its open + 1 day), which the live
runner does not provide.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig, htf_bias
from strategy.ict_engine import detect_fvgs

NAME = "C03_FVG_FILL"
CONFIG = StrategyConfig(
    name=NAME,
    description="5m FVG fill -- enter on reaction-close back outside the gap (r2 variant)",
    cooldown_s=600,
    session_start_hour=7,
    session_end_hour=20,
)

# ---- shipped literals, lifted (defaults == concept_strategies/c03_fvg_fill.py) ----
_KZ = ((7, 11), (13, 18))     # killzone UTC hour ranges [start, end)
_ATR_N = 20                   # M5 bars in the ATR mean
_FVG_MIN_ATR = 0.3            # min gap as a fraction of ATR5
_FVG_LOOKBACK = 40            # M5 bars scanned for FVGs
_ENTER_TOL = 0.2              # pts below/above the far edge still counted as "entered"
_SL_BUF_ATR = 0.10            # stop buffer beyond the gap's far edge, in ATR5
_TP_LOOKBACK = 20             # M5 bars for the swing target
_TP_PAD = 0.5                 # pts beyond the swing
_MIN_RR = 1.0                 # reward/risk floor
_EMA_PERIOD = 20              # H1 EMA period
_SLOPE_BARS = 4               # EMA slope lookback in H1 bars (iloc[-1] vs iloc[-1-(n-1)] ... see below)

# ---- research overlays ----
# "shipped": every 4th 15m close from the window START (phase-locked, newest sample up to 45 min stale)
# "h1_resampled": every 4th 15m close ending at the NEWEST bar
# "ema15_21": backtest_strategies.base.htf_bias (15m close vs EMA21)
# "none": no bias; the most recent unfilled FVG of either type is eligible (concept-removed control)
_BIAS_MODE = "shipped"
# None | "prev_candle" | "pdhl_engine" | "prev_candle:shuffle<seed>" | "pdhl_engine:shuffle<seed>"
_DAILY_GATE = None

_CACHE = Path(os.environ.get("LAB_BARS_CACHE") or
              (Path(__file__).resolve().parents[3] / "backtest" / "results" / "bars_cache_2y"))


class _Daily:
    """Daily-bias series, look-ahead safe. Bias at `now` is computed from the newest daily bar
    whose open + 1 day <= now (the harness RegimeGate convention)."""

    def __init__(self, spec: str):
        d = pd.read_parquet(_CACHE / "is_XAU_USD_1d.parquet")
        t = pd.to_datetime(d["time"], utc=True).dt.tz_convert(None).to_numpy("datetime64[ns]")
        order = np.argsort(t)
        t = t[order]
        o, h, l, c = (d[k].to_numpy(float)[order] for k in ("open", "high", "low", "close"))
        self.closes_at = t + np.timedelta64(1, "D")
        rule, _, shuf = spec.partition(":")
        bias = np.full(len(t), "", dtype=object)
        for i in range(len(t)):
            if rule == "prev_candle":
                bias[i] = "BULL" if c[i] > o[i] else "BEAR" if c[i] < o[i] else ""
            elif rule == "pdhl_engine":
                if i == 0:
                    continue
                if c[i] > h[i - 1]:
                    bias[i] = "BULL"
                elif c[i] < l[i - 1]:
                    bias[i] = "BEAR"
                elif h[i] > h[i - 1]:
                    bias[i] = "BEAR"
                elif l[i] < l[i - 1]:
                    bias[i] = "BULL"
            else:
                raise ValueError(spec)
        if shuf:
            seed = int(shuf.replace("shuffle", ""))
            rng = np.random.default_rng(seed)
            bias = bias[rng.permutation(len(bias))]      # permute across days: same marginals, no information
        self.bias = bias

    def at(self, now) -> str | None:
        ts = np.datetime64(pd.Timestamp(now).tz_convert(None) if pd.Timestamp(now).tzinfo
                           else pd.Timestamp(now), "ns")
        i = int(np.searchsorted(self.closes_at, ts, side="right")) - 1
        if i < 0:
            return None
        return self.bias[i] or None


_daily_cache: dict = {}


def _daily(spec: str) -> _Daily:
    if spec not in _daily_cache:
        _daily_cache[spec] = _Daily(spec)
    return _daily_cache[spec]


def _h1_ema_slope(w15m: pd.DataFrame, period: int = 20) -> str | None:
    """Shipped: approximate H1 EMA slope from 15m closes (every 4th bar from the window start)."""
    if w15m is None or len(w15m) < period * 4 + 4:
        return None
    closes = w15m["close"].astype(float)
    if _BIAS_MODE == "h1_resampled":
        h1 = closes.iloc[::-1].iloc[::4].iloc[::-1]     # every 4th bar ENDING at the newest bar
    else:
        h1 = closes.iloc[::4]
    if len(h1) < period + 4:
        return None
    ema = h1.ewm(span=period, adjust=False).mean()
    if ema.iloc[-1] > ema.iloc[-_SLOPE_BARS]:
        return "BULL"
    if ema.iloc[-1] < ema.iloc[-_SLOPE_BARS]:
        return "BEAR"
    return None


def _bias(w15m) -> str | None:
    if _BIAS_MODE in ("shipped", "h1_resampled"):
        return _h1_ema_slope(w15m, _EMA_PERIOD)
    if _BIAS_MODE == "ema15_21":
        return htf_bias(w15m, 21)
    if _BIAS_MODE == "none":
        return "ANY"
    raise ValueError(_BIAS_MODE)


def _in_kz(h: int) -> bool:
    return any(a <= h < b for a, b in _KZ)


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    try:
        if w5m is None or len(w5m) < 30 or w1m is None or len(w1m) < 5:
            return None
        if not _in_kz(now_utc.hour):
            return None

        recent = w5m.tail(_ATR_N)
        atr5 = float((recent["high"] - recent["low"]).mean())
        if atr5 <= 0:
            return None

        bias = _bias(w15m)
        if bias is None:
            return None

        fvgs = detect_fvgs(w5m.tail(_FVG_LOOKBACK), min_gap=_FVG_MIN_ATR * atr5)
        if not fvgs:
            return None

        current_px = float(w1m.iloc[-1]["close"])
        last5 = w5m.iloc[-1]
        prev5 = w5m.iloc[-2]

        wanted = None if bias == "ANY" else ("bullish" if bias == "BULL" else "bearish")
        daily = _daily(_DAILY_GATE).at(now_utc) if _DAILY_GATE else None

        for fvg in reversed(fvgs):
            if wanted is not None and fvg.type != wanted:
                continue
            after = w5m[pd.to_datetime(w5m["time"]) > pd.to_datetime(fvg.time)]
            if fvg.type == "bullish":
                if (after["close"] < fvg.zone_low).any():
                    continue
                entered = (prev5["low"] <= fvg.zone_high) and (prev5["low"] >= fvg.zone_low - _ENTER_TOL)
                reacted = float(last5["close"]) > fvg.zone_high
                if not (entered and reacted):
                    continue
                sl = round(fvg.zone_low - _SL_BUF_ATR * atr5, 2)
                tp = round(float(w5m["high"].tail(_TP_LOOKBACK).max()) + _TP_PAD, 2)
                entry = round(current_px, 2)
                if not (sl < entry < tp):
                    continue
                if (tp - entry) < _MIN_RR * (entry - sl):
                    continue
                if _DAILY_GATE and daily != "BULL":
                    return None
                return Signal("BUY", entry, sl, tp, "C03_FVG_BULL")
            else:
                if (after["close"] > fvg.zone_high).any():
                    continue
                entered = (prev5["high"] >= fvg.zone_low) and (prev5["high"] <= fvg.zone_high + _ENTER_TOL)
                reacted = float(last5["close"]) < fvg.zone_low
                if not (entered and reacted):
                    continue
                sl = round(fvg.zone_high + _SL_BUF_ATR * atr5, 2)
                tp = round(float(w5m["low"].tail(_TP_LOOKBACK).min()) - _TP_PAD, 2)
                entry = round(current_px, 2)
                if not (tp < entry < sl):
                    continue
                if (entry - tp) < _MIN_RR * (sl - entry):
                    continue
                if _DAILY_GATE and daily != "BEAR":
                    return None
                return Signal("SELL", entry, sl, tp, "C03_FVG_BEAR")
        return None
    except Exception:
        return None
