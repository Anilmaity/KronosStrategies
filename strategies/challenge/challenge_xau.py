"""
challenge_xau.py
----------------
XAUUSD H4 trend-follow signal logic for the challenge edge.

Rules (per `xau-challenge-doctrine`):
  - LONG  when close breaks ABOVE the prior Donchian(20) upper channel AND the
    fast EMA is above the slow EMA (bullish bias).
  - SHORT on the mirror.
  - Initial hard stop = entry +/- 3xATR (the trigger close +/- 3xATR). No fixed
    take-profit: the exit is a 3xATR chandelier trailing stop, so the worst case
    is capped near -1R and winners run with the trend.

Everything here is causal: the decision on bar t uses only bars 0..t, and the
fill happens on bar t+1's open (`entry_index = t + 1`).

The trailing stop ratchet (`chandelier_trail`) is a pure function shared by the
live runner — it only ever tightens the stop, never loosens it.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


# Indicators — kept local so this module is self-contained (the repo's shared
# copies live behind a package __init__ with import side effects). Causal:
# value at bar t uses only closes/bars up to and including t.
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


# Defaults are the doctrine's parameters. Live/backtest callers override via kwargs.
DEFAULTS: dict = {
    "donchian_n": 20,
    "ema_fast": 20,
    "ema_slow": 50,
    "atr_n": 22,
    "atr_mult": 3.0,
}


@dataclass(frozen=True)
class ChallengeSignal:
    """A trend-follow entry. Fills at bars.iloc[entry_index].open.

    `sl` is the initial hard stop (trigger_close -/+ risk_per_unit). The chandelier
    trail tightens it over the life of the trade. `atr` is the ATR at the trigger
    bar; `risk_per_unit` = atr_mult * atr (the 1R distance).
    """
    direction: str          # "BUY" | "SELL"
    entry_index: int        # integer row index; fill at this bar's open
    sl: float               # initial hard stop level
    risk_per_unit: float    # 1R distance in price (atr_mult * atr)
    atr: float              # ATR at the trigger bar
    trigger_close: float    # close of the bar that triggered the breakout


def donchian_channels(bars: pd.DataFrame, n: int) -> pd.DataFrame:
    """Prior-N Donchian channel. `upper[t]` / `lower[t]` are the highest high /
    lowest low over the N bars STRICTLY BEFORE t (excludes the current bar), so a
    breakout test `close[t] > upper[t]` compares against levels already formed.
    The first n rows are NaN (no full prior window)."""
    upper = bars["high"].rolling(n, min_periods=n).max().shift(1)
    lower = bars["low"].rolling(n, min_periods=n).min().shift(1)
    return pd.DataFrame({"upper": upper, "lower": lower})


def chandelier_trail(direction: str, *, prev_stop: float, extreme_since_entry: float,
                     atr: float, mult: float) -> float:
    """Return the updated chandelier stop. Pure ratchet:

      LONG : candidate = highest_high_since_entry - mult*atr ; stop = max(prev, candidate)
      SHORT: candidate = lowest_low_since_entry  + mult*atr ; stop = min(prev, candidate)

    The stop only ever tightens toward price — a smaller favourable excursion or a
    wider ATR never loosens an already-tighter stop.
    """
    if direction == "BUY":
        candidate = extreme_since_entry - mult * atr
        return max(prev_stop, candidate)
    if direction == "SELL":
        candidate = extreme_since_entry + mult * atr
        return min(prev_stop, candidate)
    raise ValueError(f"direction must be 'BUY' or 'SELL', got {direction!r}")


def generate_signals(bars: pd.DataFrame, *,
                     donchian_n: int = DEFAULTS["donchian_n"],
                     ema_fast: int = DEFAULTS["ema_fast"],
                     ema_slow: int = DEFAULTS["ema_slow"],
                     atr_n: int = DEFAULTS["atr_n"],
                     atr_mult: float = DEFAULTS["atr_mult"]) -> list[ChallengeSignal]:
    """Scan `bars` left-to-right and emit one ChallengeSignal per breakout that
    agrees with the EMA bias. `bars` needs columns time/open/high/low/close with a
    0..N-1 integer position order (RangeIndex or reset).

    Causal guarantees:
      - the breakout uses `close[t]` vs the prior-N Donchian (excludes bar t's own high);
      - the bias is read on the PRIOR closed bar (ema_fast[t-1] vs ema_slow[t-1]) so a
        single just-formed spike cannot tip the bias;
      - entry is the NEXT bar's open (entry_index = t+1); the last bar never signals.
    """
    n = len(bars)
    if n == 0:
        return []
    close = bars["close"].astype(float).reset_index(drop=True)
    ef = ema(close, ema_fast)
    es = ema(close, ema_slow)
    a = atr(bars.reset_index(drop=True), atr_n)
    ch = donchian_channels(bars.reset_index(drop=True), donchian_n)
    upper, lower = ch["upper"], ch["lower"]

    out: list[ChallengeSignal] = []
    for t in range(1, n - 1):  # need a prior bar (t-1) for bias and a next bar (t+1) to fill
        up_t, lo_t = upper.iloc[t], lower.iloc[t]
        atr_t = a.iloc[t]
        ef_prev, es_prev = ef.iloc[t - 1], es.iloc[t - 1]
        if pd.isna(up_t) or pd.isna(lo_t) or pd.isna(atr_t) or pd.isna(ef_prev) or pd.isna(es_prev):
            continue
        c_t = float(close.iloc[t])
        risk = atr_mult * float(atr_t)
        bias_long = ef_prev > es_prev
        bias_short = ef_prev < es_prev
        if c_t > up_t and bias_long:
            out.append(ChallengeSignal("BUY", t + 1, c_t - risk, risk, float(atr_t), c_t))
        elif c_t < lo_t and bias_short:
            out.append(ChallengeSignal("SELL", t + 1, c_t + risk, risk, float(atr_t), c_t))
    return out
