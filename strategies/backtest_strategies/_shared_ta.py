"""
strategies/backtest_strategies/_shared_ta.py
--------------------------------------------
Shared technical-analysis primitives for the LIVE FVG/retrace scalp family
(S93 FVG scalp, S99 MSS+FVG reversal, S100 M3 combo). Extracted 2026-07-31
(opt15 Task 8) from byte-identical / near-duplicated code in those three
modules. This is PURE consolidation -- every helper reproduces the
pre-refactor behavior exactly, guarded by tests/test_shared_ta_parity.py
(the ship gate: identical signals on the fixtures or the refactor does not
ship).

Contents:
  atr_last(h, l, c, n)          -- mean-of-true-range ATR, last value
  fvg_at(h, l, k)               -- 3-bar fair-value-gap geometry at bar k
  ensure_utc_ts(ts)             -- normalize a scalar timestamp to tz-aware UTC
  PendingRetrace + check_touch  -- single-pending retrace/phantom-guard machine

s94 (list-based MULTI-pending) reuses only ensure_utc_ts; its touch machine
and level/sweep state stay local by design and are NOT consolidated here.

NOTE (naming): the Task-8 brief calls the tz helper `ensure_utc_index`. The
repeated idiom in these modules is a *scalar* pd.Timestamp guard
(`if ts.tzinfo is None: ts = ts.tz_localize("UTC")`), not a DataFrame index,
so the accurate name here is `ensure_utc_ts`. Documented in the task-8 report.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from backtest_strategies.base import Signal


def atr_last(h, l, c, n: int) -> float:
    """Mean-of-true-range ATR (simple rolling mean, not Wilder), final value.

    Byte-identical to the former per-module ``_atr`` in s93/s99/s100.
    """
    prev_c = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
    return float(pd.Series(tr).rolling(n).mean().iloc[-1])


def fvg_at(h, l, k):
    """Pure 3-bar fair-value-gap geometry across bars k-2, k-1, k.

    Returns ``(kind, gap, prox, dist)``::

        bull  low[k] > high[k-2] -> ("bull", low[k]-high[k-2], low[k], high[k-2])
        bear  high[k] < low[k-2] -> ("bear", low[k-2]-high[k], high[k], low[k-2])
        none  otherwise          -> (None, 0.0, nan, nan)

    No ATR threshold, no direction gate, no max-gap cap -- each consumer keeps
    its own filters (S93 min-gap; S99 MSS+sweep; S100 EMA-dir + [min,max] band).
    ``prox`` is the proximal (near) edge = fill/entry price; ``dist`` is the
    distal (far) edge = stop anchor. bull and bear are mutually exclusive
    (h[k] >= l[k] makes both impossible at once).
    """
    if l[k] > h[k - 2]:
        return "bull", float(l[k] - h[k - 2]), float(l[k]), float(h[k - 2])
    if h[k] < l[k - 2]:
        return "bear", float(l[k - 2] - h[k]), float(h[k]), float(l[k - 2])
    return None, 0.0, float("nan"), float("nan")


def ensure_utc_ts(ts) -> pd.Timestamp:
    """Normalize a scalar timestamp-like to a tz-aware UTC ``pd.Timestamp``.

    Naive input is assumed to already be UTC (localize, do NOT convert) -- the
    exact ``if tzinfo is None: tz_localize("UTC")`` idiom these modules repeated
    for every ``armed_after`` build and probe-time comparison.
    """
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return t


@dataclass
class PendingRetrace:
    """One armed single-pending retrace setup (S93/S99/S100).

    Dict-subscriptable for backward compatibility with existing tests that read
    e.g. ``_pending["side"]`` / ``_pending["prox"]`` / ``_pending["kind"]``.

    ``side`` is +1 (long/BUY) or -1 (short/SELL); ``prox`` is the proximal edge
    = fill price; ``sl``/``tp`` are the stored levels (already rounded where the
    owning module rounds them -- S100 rounds prox/sl, S93/S99 keep prox raw);
    ``reason`` is the full trade-log label; ``kind`` is S100's 'fvg'/'ob' tag
    (None for S93/S99). ``armed_after``/``expires_at`` drive the fill-eligibility
    and expiry checks in the owning module.
    """

    side: int
    prox: float
    sl: float
    tp: float
    armed_after: pd.Timestamp
    expires_at: object
    reason: str
    max_hold_min: float | None = None
    kind: str | None = None

    def __getitem__(self, key):
        return getattr(self, key)

    def __setitem__(self, key, value):
        setattr(self, key, value)


def _fill(p: PendingRetrace) -> Signal:
    return Signal(
        side="BUY" if p.side > 0 else "SELL",
        entry_price=p.prox,
        stop_loss=p.sl,
        take_profit=p.tp,
        reason=p.reason,
        max_hold_min=p.max_hold_min,
    )


def check_touch(p: PendingRetrace, probe_time, probe_hi: float,
                probe_lo: float):
    """Evaluate one probe bar against a single armed setup.

    Returns ``(clear, signal)``::

        (False, None)   -- probe is pre-armed_after, or has not reached prox/sl:
                           the caller KEEPS the pending setup.
        (True,  None)   -- phantom guard: the probe already pierced the stop:
                           the caller CLEARS the pending setup (no fill).
        (True,  Signal) -- the probe retraced to the proximal edge: fill, and
                           the caller CLEARS the pending setup.

    Matches the s93/s99/s100 ``_touch`` single-pending semantics exactly: for a
    long (side>0) the stop sits BELOW prox, so ``low <= sl`` cancels and
    ``low <= prox`` fills; for a short (side<0) the stop sits ABOVE prox, so
    ``high >= sl`` cancels and ``high >= prox`` fills. Pre-arm and no-touch both
    return ``(False, None)`` so the setup survives to the next probe.
    """
    t = ensure_utc_ts(probe_time)
    if t < p.armed_after:
        return False, None
    if p.side > 0:
        if probe_lo <= p.sl:
            return True, None
        if probe_lo <= p.prox:
            return True, _fill(p)
    else:
        if probe_hi >= p.sl:
            return True, None
        if probe_hi >= p.prox:
            return True, _fill(p)
    return False, None
