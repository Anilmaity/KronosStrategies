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
