"""Pure entry-gate predicates shared by live entry_manager and the offline
manager sim so the two can never drift. NO DB / broker / metaapi imports —
the Manager Backtest worker imports this transitively and must stay clean of
shared.metaapi_client (tests/test_mbt_worker.py)."""
from __future__ import annotations

import os
from datetime import datetime, time as dtime

# Single-sourced env-read gate constants — live entry_manager and the offline
# manager sim both import these (same env var names/defaults) so a box-level
# env override can never make the sim model a different gate than live
# (2026-08 fidelity fix).
MIN_SL_DIST_PTS = float(os.getenv("MIN_SL_DIST_PTS", "1.5"))
NEWS_BLACKOUT_UTC = os.getenv("NEWS_BLACKOUT_UTC", "12:25-12:45")
MAX_ENTRY_DRIFT_PTS = float(os.getenv("MAX_ENTRY_DRIFT_PTS", "0.5"))
MAX_ENTRY_DRIFT_FRAC = float(os.getenv("MAX_ENTRY_DRIFT_FRAC", "0.25"))


def parse_utc_windows(spec: str) -> list[tuple[dtime, dtime]]:
    """'12:25-12:45,13:55-14:05' -> [(time(12,25), time(12,45)), ...].
    Malformed parts are skipped, never fatal."""
    wins: list[tuple[dtime, dtime]] = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            a, b = part.split("-")
            ah, am = a.split(":")
            bh, bm = b.split(":")
            wins.append((dtime(int(ah), int(am)), dtime(int(bh), int(bm))))
        except ValueError:
            continue
    return wins


def in_news_blackout(now_utc: datetime, windows: list[tuple[dtime, dtime]]) -> bool:
    t = now_utc.time()
    return any(a <= t <= b for a, b in windows)


def drift_budget_pts(entry_price: float, stop_loss: float | None,
                     max_pts: float = MAX_ENTRY_DRIFT_PTS,
                     max_frac: float = MAX_ENTRY_DRIFT_FRAC) -> float:
    """Max tolerated adverse move past the signal level before entry.

    The absolute cap, tightened to a fraction of the stop distance when that is
    smaller — chasing 0.5pt into a 1.2pt stop is 42% of the risk budget.
    A zero-distance stop falls back to the cap (matches live exactly).
    """
    budget = max_pts
    if stop_loss is not None:
        sl_dist = abs(float(entry_price) - float(stop_loss))
        if sl_dist > 0:
            budget = min(budget, max_frac * sl_dist)
    return budget


def entry_drift_pts(side: str, entry_price: float, ltp: float) -> float:
    """Adverse drift in points. POSITIVE = the market already ran in the trade's
    direction, so a BUY costs more / a SELL receives less than modelled."""
    if side == "BUY":
        return float(ltp) - float(entry_price)
    return float(entry_price) - float(ltp)


def entry_drift_exceeded(side: str, entry_price: float,
                         stop_loss: float | None, ltp: float,
                         max_pts: float = MAX_ENTRY_DRIFT_PTS,
                         max_frac: float = MAX_ENTRY_DRIFT_FRAC
                         ) -> tuple[bool, str]:
    """(exceeded, detail) for the entry-drift gate — the PURE half of live's
    entry_manager._entry_drift_exceeded.

    `ltp` is passed in rather than fetched, so the offline sim can supply the S5
    close at fill time and apply byte-identical logic. The live wrapper keeps the
    fetch and the ENTRY_DRIFT_FAIL_MODE no-price handling, which are I/O
    concerns and must stay out of this module.

    The detail string format is load-bearing: it is written to
    StrategySignal.rejection_reason and parsed when reconciling sim against live.
    """
    drift = entry_drift_pts(side, entry_price, ltp)
    budget = drift_budget_pts(entry_price, stop_loss, max_pts, max_frac)
    detail = (f"drift {drift:+.2f}pt vs budget {budget:.2f}pt "
              f"(ltp {float(ltp):.2f})")
    return drift > budget, detail


def sl_too_tight(entry_price: float, stop_loss: float | None,
                 min_dist_pts: float) -> bool:
    """Matches live entry_manager.place_entry's inline check EXACTLY: a
    sl_dist of exactly 0 is NOT too-tight (it falls through to
    _risk_sized_qty's degenerate-stop path) -- only reject when the stop is
    strictly between 0 and min_dist_pts."""
    if stop_loss is None:
        return False
    sl_dist = abs(float(entry_price) - float(stop_loss))
    return 0 < sl_dist < min_dist_pts
