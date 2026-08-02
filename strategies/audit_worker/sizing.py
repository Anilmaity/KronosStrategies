"""Matched-USD re-pricing for the Manager Backtest fidelity comparison.

Live sizes each leg by risk (entry_manager._risk_sized_qty); the sim runs flat
lots. To compare dollars fairly, infer live's per-trade risk budget from the
window's live losers, then re-price each sim trade with the SAME clamp/round."""
from __future__ import annotations

from statistics import median

_USD_PER_PT_PER_LOT = 100.0   # XAUUSD


def infer_live_risk_usd(live_usd_losses: list[float], floor: int = 5):
    losers = [abs(x) for x in live_usd_losses if x < 0]
    if len(losers) < floor:
        return None
    return round(float(median(losers)), 2)


def matched_usd(pnl_pts: float, sl_dist_pts: float, risk_usd: float,
                min_lot: float = 0.01, max_lot: float = 0.20) -> float:
    if sl_dist_pts <= 0 or risk_usd <= 0:
        lots = min_lot
    else:
        raw = risk_usd / (sl_dist_pts * _USD_PER_PT_PER_LOT)
        lots = max(min_lot, min(max_lot, int(raw / min_lot) * min_lot))
    return round(pnl_pts * lots * _USD_PER_PT_PER_LOT, 2)
