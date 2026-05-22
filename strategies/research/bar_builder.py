"""
bar_builder.py
--------------
Aggregate a single-price (mid/last) tick stream into OHLC bars.

The tick data carries only one price per tick (no bid/ask), so a real spread
cannot be computed per-bar.  Instead, an ``assumed_spread`` constant is written
to every bar's ``spread`` column.  The default 0.30 corresponds to a 30-cent
assumed half-spread for XAU/USD.

4h and 1d bars use a UTC-midnight anchor (pandas default).
Broker daily-rollover handling (e.g. 5 PM NY anchor) is out of scope.
"""
from __future__ import annotations

import pandas as pd

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

VALID_TFS = ("1m", "5m", "15m", "1h", "4h", "1d")

_TF_ALIAS: dict[str, str] = {
    "1m":  "1min",
    "5m":  "5min",
    "15m": "15min",
    "1h":  "1h",
    "4h":  "4h",
    "1d":  "1D",
}

_OUTPUT_COLS = ["time", "open", "high", "low", "close", "volume", "spread"]


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------

def build_bars(
    ticks: pd.DataFrame,
    tf: str,
    *,
    assumed_spread: float = 0.30,
    dedup: bool = True,
) -> pd.DataFrame:
    """Aggregate a single-price tick stream into OHLC bars.

    Parameters
    ----------
    ticks:
        DataFrame with columns ``time`` (tz-aware UTC datetime) and
        ``price`` (float).
    tf:
        One of VALID_TFS.
    assumed_spread:
        Constant spread (price units, e.g. 0.30 = 30 cents) written to every
        bar's ``spread`` column.  The raw data carries no real bid/ask spread,
        so this is a caller-supplied assumption.
    dedup:
        Drop exact duplicate (time, price) rows before aggregating (default
        True).  ~5% of ticks in the XAU_USD cache are collector double-writes
        with identical timestamp AND price.

    Returns
    -------
    DataFrame with columns:
        time   — tz-naive datetime64[ns], UTC wall-clock, bar-open label
        open, high, low, close — mid price (float)
        volume — count of (de-duplicated) ticks in the bar (float)
        spread — assumed_spread constant (float)

    Empty input returns an empty DataFrame with those exact columns.
    Unknown ``tf`` raises ``ValueError``.
    """
    alias = _TF_ALIAS.get(tf)
    if alias is None:
        raise ValueError(
            f"Unknown timeframe '{tf}'. Valid timeframes: {VALID_TFS}"
        )

    if ticks is None or ticks.empty:
        return pd.DataFrame(columns=_OUTPUT_COLS)

    # Guard: reject tz-naive input before pandas raises a cryptic internal error
    if ticks["time"].dt.tz is None:
        raise ValueError(
            "build_bars: input 'time' column must be tz-aware UTC. "
            "Received tz-naive timestamps. "
            "Convert with: ticks['time'] = pd.to_datetime(ticks['time'], utc=True)"
        )

    df = ticks.copy()

    # -----------------------------------------------------------------------
    # De-duplicate exact (time, price) rows — collector double-write artifact
    # -----------------------------------------------------------------------
    if dedup:
        df = df.drop_duplicates(subset=["time", "price"])

    # -----------------------------------------------------------------------
    # Resample
    # -----------------------------------------------------------------------
    series = df.set_index("time")["price"].sort_index()

    rs = series.resample(alias)

    bars = pd.DataFrame({
        "open":   rs.first(),
        "high":   rs.max(),
        "low":    rs.min(),
        "close":  rs.last(),
        "volume": rs.count(),
    })

    # Drop empty bars (no ticks in that window) — match cache_loader.resample convention
    bars = bars.dropna(subset=["open", "close"]).reset_index()

    # -----------------------------------------------------------------------
    # Type coercions
    # -----------------------------------------------------------------------
    # time → tz-naive datetime64[ns] (UTC wall-clock), matching cache_loader.resample
    bars["time"] = bars["time"].dt.tz_convert(None).astype("datetime64[ns]")

    for col in ("open", "high", "low", "close"):
        bars[col] = bars[col].astype(float)
    bars["volume"] = bars["volume"].astype(float)

    # -----------------------------------------------------------------------
    # Assumed spread — constant per bar (no per-tick bid/ask in the source)
    # -----------------------------------------------------------------------
    bars["spread"] = float(assumed_spread)

    # Ensure consistent column order
    bars = bars[_OUTPUT_COLS]

    return bars


# ---------------------------------------------------------------------------
# Step-3 real-data sanity (not a pytest test)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    import os

    _here      = os.path.dirname(os.path.abspath(__file__))
    _repo_root = os.path.normpath(os.path.join(_here, "..", ".."))
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)

    from strategies.backtest.cache_loader import load_ticks  # noqa: E402

    print("Loading XAU_USD ticks (this may take a moment) …")
    all_ticks = load_ticks("XAU_USD")

    # Tail the last 3 days worth of data to keep it fast
    cutoff = all_ticks["time"].max() - pd.Timedelta(days=3)
    recent = all_ticks[all_ticks["time"] >= cutoff].copy()
    print(f"  Ticks in last 3 days: {len(recent):,}")

    bars_5m = build_bars(recent, "5m")

    print(f"\n5m bars built: {len(bars_5m)} bars")
    print(f"spread column unique values: {bars_5m['spread'].unique().tolist()}")

    print("\n--- head (first 5 bars) ---")
    print(bars_5m.head().to_string(index=False))

    print("\n--- tail (last 5 bars) ---")
    print(bars_5m.tail().to_string(index=False))

    # Sanity assertions
    assert (bars_5m["spread"] == 0.30).all(), "FAIL: spread != 0.30"
    print("\nSanity check PASSED: all spread values == 0.30")
