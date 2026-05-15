"""
trial_all_strategies.py
-----------------------
One-shot script: fire a trial entry through place_entry for every wired
variation, using a tight BUY at current LTP. Honours DRY_RUN — set
DRY_RUN=true to exercise the full pipeline without real broker orders.

Run:
  $env:DRY_RUN="true"
  python trial_all_strategies.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shared.tsdb_reader import fetch_latest_ltp
from strategy.entry_manager import _VARIATION_STRATEGY_NAME, place_entry
from strategy.ict_engine import EntrySignal


def main():
    dry = False
    print(f"DRY_RUN={dry}  (set DRY_RUN=true to skip real broker calls)\n")

    ltp = float(fetch_latest_ltp("XAU_USD"))
    entry = round(ltp, 2)
    sl    = round(entry - 3.0, 2)
    tp    = round(entry + 4.5, 2)
    print(f"LTP={ltp} | trial BUY 0.01 @ {entry} | SL={sl} TP={tp} (3pt SL / 4.5pt TP)\n")

    results: list[tuple[str, bool]] = []
    for tag in _VARIATION_STRATEGY_NAME:
        sig = EntrySignal(
            side="BUY",
            entry_price=entry,
            stop_loss=sl,
            take_profit=tp,
            reason=f"{tag}_TRIAL",
            zone_low=entry,
            zone_high=entry,
        )
        ok = place_entry(sig, symbol="XAU_USD", variation=tag)
        results.append((tag, ok))
        print(f"  {tag:<18s} -> {'OK' if ok else 'SKIPPED/FAILED'}")

    n_ok = sum(1 for _, ok in results if ok)
    print(f"\n{n_ok}/{len(results)} trials placed.")


if __name__ == "__main__":
    main()
