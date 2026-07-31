"""
strategies/research/replay_lib.py
---------------------------------
Shared, dependency-light scaffolding for the opt15 offline research studies
(Task 13 HTF-bias filter, Task 14 trailing exits, Task 15 S100 ER gate). It is
NOT part of the live trading path -- no DB, no network, no broker. Everything
operates on the 3-year OANDA M1 parquet
(``ClaudeTradingRD/m3_scalper/xau_m1_3y.parquet``).

What it provides
----------------
* ``load_m1(path, start, end)``        -- read the parquet, naive-UTC ``time``
* ``resample_ohlc(m1, rule)``          -- house convention label/closed = left
* ``build_frames(m1)``                 -- M5/M15/H1/H4/D1 in one dict
* window slicing for the get_signal replay (``trailing_window``)
* ``simulate_exit(...)``               -- M1-path fill sim (SL-before-TP)
* ``BiasEngine``                       -- D1+H4 / H4-only swing-structure bias,
                                          reusing ``ict_engine.get_market_structure``
* ``arm_stats`` / ``trade_stats``      -- PF, n, avg pts, maxDD, WR

Design notes that matter for parity with the live runner
--------------------------------------------------------
* The live runner (``research_runner.py``) fetches candles through
  ``tsdb_reader`` which returns a **naive-UTC** ``time`` column, and passes a
  **tz-aware** ``now_utc``. We mirror that combination exactly: resampled frames
  carry naive-UTC ``time``; ``now_utc`` handed to ``get_signal`` is tz-aware UTC.
* Resampling uses ``label="left", closed="left"`` -- the convention already used
  across the repo (``_kronos_indicators.htf_bias_from_window``,
  ``kronos_challenge_xau``, ``s96``, ``s97``). OANDA candles are left-labelled.
* HTF-bias reads only fully-CLOSED higher-timeframe bars as of ``now_utc``
  (a D1 bar labelled day D is closed only at D+1 00:00; an H4 bar at T is closed
  at T+4h) -- so the bias never peeks at the still-forming bar.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

import numpy as np
import pandas as pd

# Put strategies/ on sys.path (the live runner's root) so the flat package
# imports (strategy.*, backtest_strategies.*, regime.*) resolve -- matching the
# test-suite convention (tests/test_regime_engine.py, tests/test_s93_*).
_STRAT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from strategy.ict_engine import get_market_structure  # noqa: E402

# House cost model (opt15 brief): friction is a per-round-trip point charge
# deducted once from gross points. Base 0.45pt, stress 0.80pt.
BASE_FRICTION = 0.45
STRESS_FRICTION = 0.80

# HTF-bias frame depth -- mirrors regime_engine.FRAME_SPEC (D1=120d, H4=90d).
BIAS_D1_DAYS = 120
BIAS_H4_DAYS = 90

_RULE_FREQ = {
    "5min": pd.Timedelta(minutes=5),
    "15min": pd.Timedelta(minutes=15),
    "1h": pd.Timedelta(hours=1),
    "4h": pd.Timedelta(hours=4),
    "1D": pd.Timedelta(days=1),
}


# ──────────────────────────────────────────────────────────────────────────────
# Data loading / resampling
# ──────────────────────────────────────────────────────────────────────────────

def load_m1(path: str, start=None, end=None) -> pd.DataFrame:
    """Load the M1 parquet with a naive-UTC ``time`` column (matching
    tsdb_reader.fetch_candles' ``.dt.tz_convert(None)``). Optional [start, end]
    slice on ``time`` (inclusive-left, exclusive-right)."""
    df = pd.read_parquet(path)
    t = pd.to_datetime(df["time"], utc=True).dt.tz_convert(None)
    df = df.assign(time=t).sort_values("time").reset_index(drop=True)
    df = df[["time", "open", "high", "low", "close"]].copy()
    if start is not None:
        df = df[df["time"] >= pd.Timestamp(start)]
    if end is not None:
        df = df[df["time"] < pd.Timestamp(end)]
    return df.reset_index(drop=True)


def resample_ohlc(m1: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample naive-UTC M1 OHLC to ``rule`` using the house left/left
    convention. Returns a frame with a naive-UTC ``time`` column and
    open/high/low/close, dropping empty (weekend) buckets."""
    o = m1.set_index("time")
    agg = o.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()
    return agg.reset_index()


def build_frames(m1: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Build the five research frames from M1 in one pass."""
    return {
        "M5": resample_ohlc(m1, "5min"),
        "M15": resample_ohlc(m1, "15min"),
        "H1": resample_ohlc(m1, "1h"),
        "H4": resample_ohlc(m1, "4h"),
        "D1": resample_ohlc(m1, "1D"),
    }


# ──────────────────────────────────────────────────────────────────────────────
# get_signal replay window slicing
# ──────────────────────────────────────────────────────────────────────────────

def trailing_window(frame: pd.DataFrame, end_idx: int, depth: int) -> pd.DataFrame:
    """The ``depth`` bars of ``frame`` ending at (and including) ``end_idx``.

    Mirrors the live runner's ``c5m.tail(WIN_5M)``: the newest ``depth`` closed
    bars. ``end_idx`` is the last CLOSED bar's positional index."""
    lo = max(0, end_idx - depth + 1)
    return frame.iloc[lo:end_idx + 1]


# ──────────────────────────────────────────────────────────────────────────────
# Fill simulation on the M1 path
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ExitResult:
    exit_idx: int
    exit_price: float
    reason: str          # 'SL' | 'TP' | 'TIME' | 'EOD'
    gross_pts: float     # signed points before friction


def _as_ns(ts) -> int:
    return int(pd.Timestamp(ts).value)


def simulate_exit(
    t_ns: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    entry_time,
    side: str,
    entry_price: float,
    sl: float,
    tp: float,
    max_hold_min: float | None,
) -> ExitResult:
    """Walk the M1 path forward from the first bar strictly AFTER ``entry_time``
    and return the first exit.

    Conservative rules (opt15 brief):
      * SL is checked before TP, so a bar that touches BOTH counts as a stop-out.
      * Intrabar price exits (SL/TP) take priority over the wall-clock TIME exit
        within the same bar; TIME fires at the first bar whose elapsed >=
        max_hold_min (exit at that bar's close).
      * If the path ends before any exit, force-close at the last close ('EOD').

    ``t_ns``/``high``/``low``/``close`` are the full M1 arrays (int64 ns time).
    Entry fill is at ``entry_price``; the entry bar itself is excluded (scan
    starts at the next M1 bar) to avoid intra-entry-bar look-ahead."""
    is_buy = side == "BUY"
    entry_ns = _as_ns(entry_time)
    start = int(np.searchsorted(t_ns, entry_ns, side="right"))
    n = t_ns.shape[0]
    max_hold_ns = None if max_hold_min is None else int(max_hold_min * 60 * 1_000_000_000)

    for i in range(start, n):
        hi = high[i]
        lo = low[i]
        if is_buy:
            if lo <= sl:
                return ExitResult(i, sl, "SL", sl - entry_price)
            if hi >= tp:
                return ExitResult(i, tp, "TP", tp - entry_price)
        else:
            if hi >= sl:
                return ExitResult(i, sl, "SL", entry_price - sl)
            if lo <= tp:
                return ExitResult(i, tp, "TP", entry_price - tp)
        if max_hold_ns is not None and (t_ns[i] - entry_ns) >= max_hold_ns:
            px = close[i]
            gross = (px - entry_price) if is_buy else (entry_price - px)
            return ExitResult(i, px, "TIME", gross)

    px = close[n - 1]
    gross = (px - entry_price) if is_buy else (entry_price - px)
    return ExitResult(n - 1, px, "EOD", gross)


# ──────────────────────────────────────────────────────────────────────────────
# HTF-bias engine (reuses ict_engine swing-structure semantics)
# ──────────────────────────────────────────────────────────────────────────────

def structure_bias(frame: pd.DataFrame) -> str:
    """'bullish' | 'bearish' | 'ranging' via ict_engine.get_market_structure
    (the canonical swing-structure classifier the regime engine's
    _structure_and_swings was proven identical to -- opt15 Task 11). Reused by
    import so this study measures the SAME structure the live regime engine sees.
    """
    if frame is None or len(frame) == 0:
        return "ranging"
    return get_market_structure(frame, lookback=3)


def aligned_d1h4_bias(d1_frame: pd.DataFrame, h4_frame: pd.DataFrame) -> str:
    """'bull' | 'bear' | 'none'. Aligned == both frames directional AND agreeing
    (D1 bullish & H4 bullish -> 'bull'; both bearish -> 'bear'). Any ranging or
    disagreement -> 'none' (never vetoes)."""
    d1 = structure_bias(d1_frame)
    h4 = structure_bias(h4_frame)
    if d1 == "bullish" and h4 == "bullish":
        return "bull"
    if d1 == "bearish" and h4 == "bearish":
        return "bear"
    return "none"


def h4_only_bias(h4_frame: pd.DataFrame) -> str:
    """'bull' | 'bear' | 'none' from H4 structure alone (the live-wireable bias:
    a runner can resample H4 from w15m but cannot reach D1 depth)."""
    s = structure_bias(h4_frame)
    if s == "bullish":
        return "bull"
    if s == "bearish":
        return "bear"
    return "none"


def opposes(side: str, bias: str) -> bool:
    """True when a trade ``side`` ('BUY'/'SELL') opposes a directional ``bias``
    ('bull'/'bear'/'none'). 'none' never opposes."""
    if bias == "bull" and side == "SELL":
        return True
    if bias == "bear" and side == "BUY":
        return True
    return False


class BiasEngine:
    """Precomputes D1/H4 arrays once and answers bias-at-now_utc queries with no
    look-ahead. A D1 bar labelled day D is treated as CLOSED at D+1 00:00; an H4
    bar labelled T is closed at T+4h. For each query the frames are trailing
    windows (D1=120d, H4=90d) of closed bars only -- mirroring regime_engine's
    FRAME_SPEC. Results are memoized on (d1_lo,d1_hi,h4_lo,h4_hi)."""

    def __init__(self, d1: pd.DataFrame, h4: pd.DataFrame):
        self._d1 = d1.reset_index(drop=True)
        self._h4 = h4.reset_index(drop=True)
        self._d1_open_ns = self._d1["time"].astype("datetime64[ns]").astype("int64").to_numpy()
        self._h4_open_ns = self._h4["time"].astype("datetime64[ns]").astype("int64").to_numpy()
        self._d1_close_ns = self._d1_open_ns + int(_RULE_FREQ["1D"].value)
        self._h4_close_ns = self._h4_open_ns + int(_RULE_FREQ["4h"].value)
        self._d1_span = int(pd.Timedelta(days=BIAS_D1_DAYS).value)
        self._h4_span = int(pd.Timedelta(days=BIAS_H4_DAYS).value)
        self._cache: dict[tuple, tuple[str, str]] = {}

    def _bounds(self, open_ns, close_ns, now_ns, span_ns):
        hi = int(np.searchsorted(close_ns, now_ns, side="right"))   # closed bars [0:hi)
        lo = int(np.searchsorted(open_ns, now_ns - span_ns, side="left"))
        lo = min(lo, hi)
        return lo, hi

    def bias_at(self, now_utc_naive) -> tuple[str, str]:
        """Return (aligned_d1h4, h4_only) bias strings at ``now_utc_naive``
        (a naive-UTC Timestamp)."""
        now_ns = _as_ns(now_utc_naive)
        d_lo, d_hi = self._bounds(self._d1_open_ns, self._d1_close_ns, now_ns, self._d1_span)
        h_lo, h_hi = self._bounds(self._h4_open_ns, self._h4_close_ns, now_ns, self._h4_span)
        key = (d_lo, d_hi, h_lo, h_hi)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        d1_frame = self._d1.iloc[d_lo:d_hi]
        h4_frame = self._h4.iloc[h_lo:h_hi]
        aligned = aligned_d1h4_bias(d1_frame, h4_frame)
        h4o = h4_only_bias(h4_frame)
        self._cache[key] = (aligned, h4o)
        return aligned, h4o


# ──────────────────────────────────────────────────────────────────────────────
# Trade statistics
# ──────────────────────────────────────────────────────────────────────────────

def trade_stats(net_pts) -> dict:
    """PF, n, avg pts, maxDD, win-rate, total from a chronological list of net
    (post-friction) point results. PF = sum(wins)/abs(sum(losses)); when there
    are no losses PF is reported as inf (float('inf'))."""
    arr = np.asarray(list(net_pts), dtype=float)
    n = int(arr.shape[0])
    if n == 0:
        return {"n": 0, "pf": float("nan"), "avg_pts": float("nan"),
                "max_dd": 0.0, "win_rate": float("nan"), "total": 0.0}
    wins = arr[arr > 0].sum()
    losses = arr[arr < 0].sum()
    pf = float("inf") if losses == 0 else float(wins / abs(losses))
    equity = np.cumsum(arr)
    running_max = np.maximum.accumulate(equity)
    max_dd = float((equity - running_max).min())   # <= 0
    win_rate = float((arr > 0).mean())
    return {
        "n": n,
        "pf": pf,
        "avg_pts": float(arr.mean()),
        "max_dd": max_dd,
        "win_rate": win_rate,
        "total": float(arr.sum()),
    }


def net_from_gross(gross_pts: float, friction: float, size: float = 1.0) -> float:
    """Net points for one trade: (gross - round-trip friction) scaled by ``size``
    (size<1 models the half-size arm -- both P&L and friction scale with size)."""
    return size * (gross_pts - friction)
