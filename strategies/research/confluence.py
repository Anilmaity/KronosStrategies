"""
confluence.py
-------------
Confluence matrix builder for XAU/USD 90%-winrate research (Task 6).

Each trade setup can declare a ``confluences: list[str]`` requirement.
A trade only fires when ALL listed confluences evaluate to True at the entry tick.

Public API
----------
ConfluenceUnavailable   -- exception raised when data required for a detector is absent
ConfluenceContext       -- uniform input dataclass passed to every detector
htf_bias                -- directional bias via HTF close comparison
session_killzone        -- London/NY session window check
liquidity_sweep         -- prior swing sweep with reversal close
fvg_present             -- 3-candle Fair Value Gap aligned to direction
ob_present              -- Order Block (bearish->bullish or bullish->bearish transition)
volume_anomaly          -- current bar volume > vol_mult * recent mean
dxy_inverse_correlation_holding -- raises ConfluenceUnavailable (no DXY data in project)
DETECTORS               -- dict[str, callable] of all 7 detectors
SEARCHABLE              -- list[str] of the 6 computable detectors (excludes dxy)
evaluate                -- dict[name -> bool] for a list of detector names
all_met                 -- True iff every listed confluence is True
count_met               -- count of True confluences

DXY NOTE
--------
There is no DXY (US Dollar Index) data in this project — only XAU/USD and XAG/USD
tick data is available. ``dxy_inverse_correlation_holding`` therefore raises
``ConfluenceUnavailable`` immediately and is excluded from ``SEARCHABLE`` so that
automated confluence-tuning searches (Task 7) never attempt to evaluate it.
It remains in ``DETECTORS`` as a documented placeholder.

No-look-ahead guarantee
-----------------------
Every detector evaluated "at bar idx" uses ONLY bars[0..idx] (closed bars
at the decision point) and HTF bars whose ``time`` is strictly before
``bars.time[idx]``. Bars at indices > idx are never read.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class ConfluenceUnavailable(Exception):
    """Raised when a detector cannot be computed due to missing market data.

    Currently raised only by ``dxy_inverse_correlation_holding`` because
    no DXY data exists in this project's data store.
    """


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------

@dataclass
class ConfluenceContext:
    """Uniform input object passed to every detector.

    Parameters
    ----------
    bars : pd.DataFrame
        Entry-timeframe mid-OHLCV bars. Columns: time, open, high, low, close,
        volume, spread. ``time`` is tz-naive UTC datetime64[ns].
    idx : int
        Bar index that is the decision bar. The trade entry is evaluated AT
        this bar. Detectors may only read bars[0..idx] — never bars[idx+1..].
    direction : str
        "BUY" or "SELL".
    htf_bars : pd.DataFrame | None
        Higher-timeframe bars (e.g. 4H or 1H) for htf_bias. Same column
        format as ``bars``. If None, ``htf_bias`` returns False.
    killzone_windows : tuple of (start_hour, end_hour) pairs (UTC, half-open)
        Default is ((8.0, 10.0), (13.5, 15.5)) — London and NY kill zones.
        Hours are fractional: 13.5 = 13:30 UTC.
    lookback : int
        Number of bars to look back for htf_bias, ob_present, and the mean
        window in volume_anomaly. Default 20.
    vol_mult : float
        Volume multiplier for volume_anomaly. Default 1.5.
    fvg_lookback : int
        Number of bars to look back when searching for an FVG. Default 10.
    sweep_lookback : int
        Number of bars to look back when searching for a liquidity sweep.
        Default 20.
    """

    bars: pd.DataFrame
    idx: int
    direction: str
    htf_bars: pd.DataFrame | None = None
    killzone_windows: tuple = ((8.0, 10.0), (13.5, 15.5))
    lookback: int = 20
    vol_mult: float = 1.5
    fvg_lookback: int = 10
    sweep_lookback: int = 20


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

def htf_bias(ctx: ConfluenceContext) -> bool:
    """Return True iff the HTF directional bias aligns with ctx.direction.

    Bias rule: compare the most-recently CLOSED HTF bar's close to the close
    ``lookback`` HTF bars before it. If close_last > close_ref → bullish bias;
    close_last < close_ref → bearish bias; equal → neutral (False).

    Only HTF bars with ``time`` strictly BEFORE ``bars.time[ctx.idx]`` are
    visible (no look-ahead on higher timeframe).

    Returns False when:
      - htf_bars is None
      - fewer than (lookback + 1) closed HTF bars are visible
      - HTF bias is neutral (no directional conviction)
    """
    if ctx.htf_bars is None:
        return False

    entry_time: pd.Timestamp = ctx.bars.iloc[ctx.idx]["time"]

    # Only use HTF bars that closed BEFORE the entry bar's time (strict <).
    visible = ctx.htf_bars[ctx.htf_bars["time"] < entry_time].copy()

    # Need at least (lookback + 1) bars to compare
    if len(visible) < ctx.lookback + 1:
        return False

    close_last = float(visible.iloc[-1]["close"])
    close_ref  = float(visible.iloc[-(ctx.lookback + 1)]["close"])

    if ctx.direction == "BUY":
        return bool(close_last > close_ref)
    else:  # SELL
        return bool(close_last < close_ref)


def session_killzone(ctx: ConfluenceContext) -> bool:
    """Return True iff the decision bar's time falls inside a killzone window.

    A killzone is a half-open interval [start_hour, end_hour) in UTC fractional
    hours (e.g. 13.5 = 13:30). The check is weekday-only (Mon=0 … Fri=4).

    Uses only ``bars.time[ctx.idx]`` — inherently no look-ahead.
    """
    ts: pd.Timestamp = ctx.bars.iloc[ctx.idx]["time"]

    # Weekday check (0=Mon, 5=Sat, 6=Sun)
    if ts.weekday() >= 5:
        return False

    # Fractional hour of day: e.g. 13:30 → 13.5
    frac_hour = ts.hour + ts.minute / 60.0 + ts.second / 3600.0

    for start, end in ctx.killzone_windows:
        if start <= frac_hour < end:
            return True

    return False


def liquidity_sweep(ctx: ConfluenceContext) -> bool:
    """Return True iff a liquidity sweep with reversal close occurred within sweep_lookback.

    BUY setup: within bars[max(0, idx-sweep_lookback)..idx], find any bar j where:
        - bar j's low < the prior swing low (minimum of lows before bar j in the window)
        - bar j's close > that prior swing low   (closed back above → reversal)

    SELL setup: within bars[max(0, idx-sweep_lookback)..idx], find any bar j where:
        - bar j's high > the prior swing high (maximum of highs before bar j in the window)
        - bar j's close < that prior swing high  (closed back below → reversal)

    Only bars[0..idx] are read. No future bars are accessed.
    Returns False when fewer than 2 bars are in the lookback slice.
    """
    start = max(0, ctx.idx - ctx.sweep_lookback)
    window = ctx.bars.iloc[start: ctx.idx + 1]

    if len(window) < 2:
        return False

    lows  = window["low"].values
    highs = window["high"].values
    closes = window["close"].values
    n = len(window)

    if ctx.direction == "BUY":
        for j in range(1, n):
            prior_swing_low = lows[:j].min()
            if lows[j] < prior_swing_low and closes[j] > prior_swing_low:
                return True
    else:  # SELL
        for j in range(1, n):
            prior_swing_high = highs[:j].max()
            if highs[j] > prior_swing_high and closes[j] < prior_swing_high:
                return True

    return False


def fvg_present(ctx: ConfluenceContext) -> bool:
    """Return True iff a directionally-aligned 3-candle Fair Value Gap exists
    within bars[max(0, idx-fvg_lookback)..idx].

    Bullish FVG at bar i: high[i-2] < low[i]   (gap between candle i-2 and i)
    Bearish FVG at bar i: low[i-2] > high[i]

    BUY direction requires a bullish FVG; SELL requires a bearish FVG.
    Only bars[0..idx] are read. Bars earlier than start of the lookback slice
    are not accessed.

    Returns False when fewer than 3 bars exist in the lookback window.
    """
    start = max(0, ctx.idx - ctx.fvg_lookback)
    # We need slices, but must stay within bars[0..idx]
    window = ctx.bars.iloc[start: ctx.idx + 1]

    if len(window) < 3:
        return False

    highs = window["high"].values
    lows  = window["low"].values
    n = len(window)

    if ctx.direction == "BUY":
        for i in range(2, n):
            if highs[i - 2] < lows[i]:
                return True
    else:  # SELL
        for i in range(2, n):
            if lows[i - 2] > highs[i]:
                return True

    return False


def ob_present(ctx: ConfluenceContext) -> bool:
    """Return True iff a directionally-aligned Order Block exists within lookback.

    Simplified OB definition (documented, defensible, tunable by Task 7):

    Bullish OB (BUY): the last bearish candle (close < open) that is immediately
    followed by a bullish candle (close > open) within bars[max(0,idx-lookback)..idx].
    This represents the final institutional accumulation candle before an up-move.

    Bearish OB (SELL): the last bullish candle (close > open) that is immediately
    followed by a bearish candle (close < open) within the same window.
    This represents the final institutional distribution candle before a down-move.

    Only bars[0..idx] are read. Returns False when fewer than 2 bars in window.
    """
    start = max(0, ctx.idx - ctx.lookback)
    window = ctx.bars.iloc[start: ctx.idx + 1]

    if len(window) < 2:
        return False

    opens  = window["open"].values
    closes = window["close"].values
    n = len(window)

    if ctx.direction == "BUY":
        # Search for last bearish candle immediately followed by bullish candle
        found = False
        for i in range(n - 1):
            is_bearish = closes[i] < opens[i]
            next_bullish = closes[i + 1] > opens[i + 1]
            if is_bearish and next_bullish:
                found = True
        return found
    else:  # SELL
        # Search for last bullish candle immediately followed by bearish candle
        found = False
        for i in range(n - 1):
            is_bullish = closes[i] > opens[i]
            next_bearish = closes[i + 1] < opens[i + 1]
            if is_bullish and next_bearish:
                found = True
        return found


def volume_anomaly(ctx: ConfluenceContext) -> bool:
    """Return True iff bars.volume[idx] > vol_mult * mean(volume[idx-lookback:idx]).

    Uses a strict lookback window of bars[idx-lookback:idx] (exclusive of idx)
    as the reference period. If that window is empty (idx < 1 or lookback == 0),
    returns False.

    Only bars[0..idx] are read.
    """
    start = max(0, ctx.idx - ctx.lookback)
    ref_slice = ctx.bars.iloc[start: ctx.idx]

    if ref_slice.empty:
        return False

    mean_vol = ref_slice["volume"].mean()
    current_vol = float(ctx.bars.iloc[ctx.idx]["volume"])

    return bool(current_vol > ctx.vol_mult * mean_vol)


def dxy_inverse_correlation_holding(ctx: ConfluenceContext) -> bool:
    """Raises ConfluenceUnavailable — no DXY data in this project.

    The DXY (US Dollar Index) inverse correlation with XAU/USD is a well-known
    relationship. However, this project's data store contains only XAU/USD and
    XAG/USD tick data — no DXY time series. Computing this confluence is
    therefore impossible with the available data.

    This detector is listed in DETECTORS for documentation purposes but is
    EXCLUDED from SEARCHABLE so that automated confluence-tuning searches
    (Task 7) never attempt to evaluate it.

    Raises
    ------
    ConfluenceUnavailable
        Always. No DXY data is available in the .history_data cache.
    """
    raise ConfluenceUnavailable(
        "dxy_inverse_correlation_holding cannot be computed: no DXY (US Dollar "
        "Index) data is available in this project. Only XAU/USD and XAG/USD tick "
        "data is stored in .history_data/. To use this confluence, add a DXY "
        "data feed and implement the detector. This confluence is excluded from "
        "SEARCHABLE and will never be evaluated by automated tuning."
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

#: All 7 detector callables, keyed by name.
#: ``dxy_inverse_correlation_holding`` is present for documentation but raises
#: ``ConfluenceUnavailable`` — use SEARCHABLE for automated iteration.
DETECTORS: dict[str, callable] = {
    "htf_bias":                         htf_bias,
    "session_killzone":                  session_killzone,
    "liquidity_sweep":                   liquidity_sweep,
    "fvg_present":                       fvg_present,
    "ob_present":                        ob_present,
    "volume_anomaly":                    volume_anomaly,
    "dxy_inverse_correlation_holding":   dxy_inverse_correlation_holding,
}

#: The 6 computable detectors — safe to iterate in automated tuning (Task 7).
#: ``dxy_inverse_correlation_holding`` is intentionally excluded because it has
#: no underlying data and always raises ``ConfluenceUnavailable``.
SEARCHABLE: list[str] = [
    "htf_bias",
    "session_killzone",
    "liquidity_sweep",
    "fvg_present",
    "ob_present",
    "volume_anomaly",
]


# ---------------------------------------------------------------------------
# Aggregators
# ---------------------------------------------------------------------------

def evaluate(names: list[str], ctx: ConfluenceContext) -> dict[str, bool]:
    """Evaluate a list of confluence detector names against a context.

    Parameters
    ----------
    names : list[str]
        Detector names to evaluate. Each must be a key in DETECTORS.
    ctx : ConfluenceContext
        The evaluation context.

    Returns
    -------
    dict[str, bool]
        Mapping of detector name → bool result.

    Raises
    ------
    ConfluenceUnavailable
        If any detector in ``names`` raises it (e.g. dxy_inverse_correlation_holding).
    KeyError
        If a name is not found in DETECTORS.
    """
    return {name: DETECTORS[name](ctx) for name in names}


def all_met(names: list[str], ctx: ConfluenceContext) -> bool:
    """Return True iff every listed confluence evaluates to True.

    An empty ``names`` list is vacuously True (no requirements → always pass).

    Raises
    ------
    ConfluenceUnavailable
        If any detector raises it.
    """
    return all(DETECTORS[name](ctx) for name in names)


def count_met(names: list[str], ctx: ConfluenceContext) -> int:
    """Return the count of confluences that evaluate to True.

    An empty ``names`` list returns 0.

    Raises
    ------
    ConfluenceUnavailable
        If any detector raises it.
    """
    return sum(1 for name in names if DETECTORS[name](ctx))
