"""
regime_engine.py
----------------
Pure market-regime classification for the Kronos Strategy Manager
(design spec 2026-07-02, section 3). **No DB access, no network** in the
compute path — all inputs are pandas DataFrames; the OANDA fetch helper
``fetch_regime_frames`` is kept separate (lazy import of tsdb_reader).

Public API
----------
compute_regime(frames, now_utc, symbol="XAU_USD") -> RegimeSnapshot
fetch_regime_frames(symbol="XAU_USD")             -> dict[str, DataFrame]

RegimeSnapshot mirrors the ``apis_regimesnapshot`` columns (minus
id/created_at/modified_at); raw numbers land in ``details`` so thresholds
can be re-tuned later without invalidating persisted history.

Classification rules (thresholds are the module constants below):
  d1_bias      ict_engine.get_market_structure on the D1 frame
               -> 'bullish' | 'bearish' | 'ranging'
  h4_bias      ict_engine.get_htf_bias(H4, H1) -> 'long' | 'short' | 'neutral'
  vol_regime   ATR(14) on H1 vs its own trailing distribution (the ~30-day
               H1 frame): percentile <25 LOW, 25-75 NORMAL, >75 HIGH,
               >95 EXTREME (mid-rank percentile, so a flat-vol tape reads
               ~50 -> NORMAL rather than a degenerate 0/100)
  trend_regime efficiency ratio |net move| / path length over the last
               24 H1 bars and 30 M15 bars: both >0.35 TRENDING,
               both <0.20 RANGING, else MIXED
  session      UTC hour: ASIA 00-06, LONDON 07-12, OVERLAP 13-16,
               NY 17-20, ROLLOVER 21-23
  market_closed  shared.market_timing.is_market_closed_utc(now_utc)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from strategy.ict_engine import (
    get_htf_bias,
    get_liquidity_targets,
    get_market_structure,
)
from shared.market_timing import is_market_closed_utc


# ──────────────────────────────────────────────────────────────────────────────
# Thresholds & frame spec (single source of truth — tune here)
# ──────────────────────────────────────────────────────────────────────────────

ATR_PERIOD = 14              # ATR lookback on H1
VOL_PCTL_LOW = 25.0          # ATR percentile below  -> LOW
VOL_PCTL_HIGH = 75.0         # ATR percentile above  -> HIGH
VOL_PCTL_EXTREME = 95.0      # ATR percentile above  -> EXTREME

ER_H1_BARS = 24              # efficiency-ratio window on H1
ER_M15_BARS = 30             # efficiency-ratio window on M15
ER_TRENDING = 0.35           # both ERs above  -> TRENDING
ER_RANGING = 0.20            # both ERs below  -> RANGING

# UTC-hour -> session name (inclusive hour ranges per the design spec)
SESSION_RANGES: tuple[tuple[int, int, str], ...] = (
    (0, 6, "ASIA"),
    (7, 12, "LONDON"),
    (13, 16, "OVERLAP"),
    (17, 20, "NY"),
    (21, 23, "ROLLOVER"),
)

# timeframe -> days of history, for fetch_regime_frames
FRAME_SPEC: dict[str, int] = {
    "1d": 120,
    "4h": 90,
    "1h": 30,
    "15m": 10,
    "5m": 3,
    "1m": 1,
}


# ──────────────────────────────────────────────────────────────────────────────
# Snapshot dataclass (mirrors apis_regimesnapshot minus id/timestamps)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class RegimeSnapshot:
    symbol: str                      # varchar(20)
    d1_bias: str                     # 'bullish' | 'bearish' | 'ranging'
    h4_bias: str                     # 'long' | 'short' | 'neutral'
    vol_regime: str                  # 'LOW' | 'NORMAL' | 'HIGH' | 'EXTREME'
    trend_regime: str                # 'TRENDING' | 'RANGING' | 'MIXED'
    session: str                     # 'ASIA'|'LONDON'|'NY'|'OVERLAP'|'ROLLOVER'
    market_closed: bool
    details: dict = field(default_factory=dict)   # raw numbers (JSON-able)


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers (pure)
# ──────────────────────────────────────────────────────────────────────────────

def _atr_series(candles: pd.DataFrame, n: int = ATR_PERIOD) -> pd.Series:
    """Rolling-mean ATR (same convention as backtest_strategies indicators)."""
    h = candles["high"].astype(float)
    l = candles["low"].astype(float)
    c = candles["close"].astype(float)
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def _mid_rank_percentile(dist: pd.Series, value: float) -> float:
    """Mid-rank percentile of `value` within `dist` (0..100).

    (count_below + 0.5 * count_equal) / n — a constant-vol series therefore
    reads ~50 (NORMAL) instead of a degenerate 0 or 100.
    """
    d = dist.dropna()
    n = len(d)
    if n == 0:
        return float("nan")
    below = float((d < value).sum())
    equal = float((d == value).sum())
    return 100.0 * (below + 0.5 * equal) / n


def _classify_vol(atr_pct: float) -> str:
    if pd.isna(atr_pct):
        return "NORMAL"
    if atr_pct > VOL_PCTL_EXTREME:
        return "EXTREME"
    if atr_pct > VOL_PCTL_HIGH:
        return "HIGH"
    if atr_pct < VOL_PCTL_LOW:
        return "LOW"
    return "NORMAL"


def _efficiency_ratio(closes: pd.Series, n_bars: int) -> float:
    """Kaufman efficiency ratio |net|/path over the last `n_bars` moves.

    Uses the last n_bars+1 closes (n_bars diffs). NaN when history is short
    or the path length is zero.
    """
    c = closes.astype(float).dropna().reset_index(drop=True)
    if len(c) < n_bars + 1:
        return float("nan")
    window = c.iloc[-(n_bars + 1):]
    net = abs(float(window.iloc[-1]) - float(window.iloc[0]))
    path = float(window.diff().abs().sum())
    if path <= 0:
        return float("nan")
    return net / path


def _classify_trend(er_h1: float, er_m15: float) -> str:
    if pd.isna(er_h1) or pd.isna(er_m15):
        return "MIXED"
    if er_h1 > ER_TRENDING and er_m15 > ER_TRENDING:
        return "TRENDING"
    if er_h1 < ER_RANGING and er_m15 < ER_RANGING:
        return "RANGING"
    return "MIXED"


def session_for_hour(hour_utc: int) -> str:
    """Map a UTC hour (0-23) to its trading-session label."""
    for start, end, name in SESSION_RANGES:
        if start <= hour_utc <= end:
            return name
    raise ValueError(f"hour_utc out of range: {hour_utc}")


def _frame(frames: dict, key: str) -> pd.DataFrame:
    df = frames.get(key)
    if df is None:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close"])
    return df


def _safe_structure(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "ranging"
    return get_market_structure(df)


def _safe_swings(df: pd.DataFrame) -> dict:
    if df is None or df.empty:
        return {"nearest_high": None, "nearest_low": None}
    return get_liquidity_targets(df)


def _f(x: float) -> float | None:
    """NaN -> None so the details dict serialises cleanly to JSON."""
    return None if pd.isna(x) else float(x)


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def compute_regime(
    frames: dict[str, pd.DataFrame],
    now_utc: datetime,
    symbol: str = "XAU_USD",
) -> RegimeSnapshot:
    """Classify the current market regime from OHLC frames. Pure — no I/O.

    `frames` keys follow tsdb_reader timeframe strings: '1d', '4h', '1h',
    '15m', '5m', '1m' (see FRAME_SPEC / fetch_regime_frames). Missing or
    short frames degrade to neutral classifications rather than raising.
    """
    d1 = _frame(frames, "1d")
    h4 = _frame(frames, "4h")
    h1 = _frame(frames, "1h")
    m15 = _frame(frames, "15m")
    m5 = _frame(frames, "5m")
    m1 = _frame(frames, "1m")

    # Directional bias ---------------------------------------------------
    d1_bias = _safe_structure(d1)
    h4_bias = (
        get_htf_bias(h4, h1) if not (h4.empty or h1.empty) else "neutral"
    )

    # Volatility regime: ATR(14, H1) vs its trailing distribution --------
    atr_h1 = float("nan")
    atr_pct = float("nan")
    if len(h1) >= ATR_PERIOD + 1:
        atr = _atr_series(h1, ATR_PERIOD)
        if not atr.dropna().empty:
            atr_h1 = float(atr.iloc[-1])
            atr_pct = _mid_rank_percentile(atr, atr_h1)
    vol_regime = _classify_vol(atr_pct)

    # Trend regime: efficiency ratios on H1 and M15 ----------------------
    er_h1 = _efficiency_ratio(h1["close"], ER_H1_BARS) if not h1.empty else float("nan")
    er_m15 = _efficiency_ratio(m15["close"], ER_M15_BARS) if not m15.empty else float("nan")
    trend_regime = _classify_trend(er_h1, er_m15)

    # Session & market hours ---------------------------------------------
    session = session_for_hour(now_utc.hour)
    market_closed = is_market_closed_utc(now_utc)

    details = {
        "atr_h1": _f(atr_h1),
        "atr_pct": _f(atr_pct),
        "er_h1": _f(er_h1),
        "er_m15": _f(er_m15),
        "m15_structure": _safe_structure(m15),
        "m5_structure": _safe_structure(m5),
        "m1_structure": _safe_structure(m1),
        "swings_d1": _safe_swings(d1),
        "swings_h4": _safe_swings(h4),
        "hour_utc": int(now_utc.hour),
    }

    return RegimeSnapshot(
        symbol=symbol,
        d1_bias=d1_bias,
        h4_bias=h4_bias,
        vol_regime=vol_regime,
        trend_regime=trend_regime,
        session=session,
        market_closed=market_closed,
        details=details,
    )


def fetch_regime_frames(symbol: str = "XAU_USD") -> dict[str, pd.DataFrame]:
    """Fetch the OHLC frames compute_regime needs (live OANDA via tsdb_reader).

    Kept separate from the pure compute path; tsdb_reader is imported lazily
    so importing this module never touches network/env config (tests stay
    offline).
    """
    from shared.tsdb_reader import fetch_candles  # lazy: network layer

    return {
        tf: fetch_candles(tf, days=days, symbol=symbol)
        for tf, days in FRAME_SPEC.items()
    }
