"""
fill_reconciler.py
------------------
Broker-fill reconciliation service (built 2026-07-07 after the first live
manager trade showed DB -$38 vs broker -$67.4: mid-price accounting misses
spread, stop slippage and commission).

Every RECON_LOOP_SEC seconds, for each broker account that has deployed
UserStrategies:

  1. Pull MetaAPI history-deals for the last RECON_LOOKBACK_H hours.
  2. Match deals to our Position rows via the ENTRY Order's broker_order_id
     (== MetaAPI positionId).
  3. True-up, idempotently (write only when values differ):
       - Position.avg_buy_price / avg_sell_price -> the REAL entry fill
         (DEAL_ENTRY_IN price), so live unrealized P&L is broker-based too.
       - For CLOSED positions: Position.realized_profit_loss -> broker truth
         (sum of OUT-deal profits + ALL commissions + swap), converted into
         the DB's storage units (points x lots = USD / contract factor; the
         backend's resolvers and the manager multiply back by the same
         factor).

The signal-price audit trail is preserved: Order.price and StrategySignal
rows keep the signal values; only the Position's P&L basis is corrected.
The manager's kill-switch/soft-brake sum realized_profit_loss, so after
reconciliation the daily brakes see REAL losses, not mid-price optimism.

Run (compose service):  python fill_reconciler.py
Env: RECON_LOOP_SEC (60), RECON_LOOKBACK_H (48)
"""
from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shared.models import Session, Order, Position, UserStrategy  # noqa: E402
from shared.metaapi_client import client_for_broker  # noqa: E402
from shared.market_timing import is_market_closed_utc  # noqa: E402

LOOP_SEC = int(os.getenv("RECON_LOOP_SEC", "60"))
LOOKBACK_H = int(os.getenv("RECON_LOOKBACK_H", "48"))

# DB storage convention: Position P&L fields hold points x lots = USD /
# contract factor (XAUUSD: 1.0 lot = 100 oz -> factor 100). Keep in sync with
# strategy_manager.manager._USD_PER_PNL_UNIT and the backend resolvers.
_USD_PER_PNL_UNIT = {"XAU_USD": 100.0}
_EPS_PX = 0.005          # ignore sub-half-cent price diffs
_EPS_PNL = 0.006         # storage-column granularity is 2dp (= $1 steps)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("fill_reconciler")


def fetch_deals(client, hours: int) -> list[dict]:
    """History deals for one account over the lookback window ([] on error)."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    url = (f"{client._trading_url()}/users/current/accounts/{client.account_id}"
           f"/history-deals/time/{start.isoformat().replace('+00:00', 'Z')}/"
           f"{end.isoformat().replace('+00:00', 'Z')}")
    try:
        resp = requests.get(url, headers=client._headers(), timeout=20)
        if resp.status_code != 200:
            log.warning("history-deals HTTP %s for account %s",
                        resp.status_code, client.account_id)
            return []
        return resp.json() or []
    except Exception as exc:
        log.warning("history-deals fetch failed: %s", exc)
        return []


def group_deals(deals: list[dict]) -> dict[str, dict]:
    """positionId -> {entry_px, entry_side, out_volume, out_px_vwap,
    total_usd (profit + all commissions + swap)}."""
    out: dict[str, dict] = {}
    for d in deals:
        pid = str(d.get("positionId") or "")
        if not pid:
            continue
        g = out.setdefault(pid, {"entry_px": None, "entry_side": None,
                                 "out_vol": 0.0, "out_pxvol": 0.0,
                                 "total_usd": 0.0})
        g["total_usd"] += float(d.get("profit") or 0)
        g["total_usd"] += float(d.get("commission") or 0)
        g["total_usd"] += float(d.get("swap") or 0)
        etype = d.get("entryType")
        if etype == "DEAL_ENTRY_IN":
            g["entry_px"] = float(d.get("price") or 0)
            g["entry_side"] = "BUY" if d.get("type") == "DEAL_TYPE_BUY" else "SELL"
        elif etype == "DEAL_ENTRY_OUT":
            v = float(d.get("volume") or 0)
            g["out_vol"] += v
            g["out_pxvol"] += v * float(d.get("price") or 0)
    return out


def apply_deals(sess, grouped: dict[str, dict]) -> int:
    """True-up Position rows against grouped broker deals. Returns number of
    positions changed. Caller owns commit/rollback."""
    if not grouped:
        return 0
    changed = 0
    orders = (
        sess.query(Order)
        .filter(Order.condition == "ENTRY",
                Order.broker_order_id.in_(list(grouped)))
        .all()
    )
    for eo in orders:
        g = grouped[str(eo.broker_order_id)]
        pos = sess.query(Position).filter_by(id=eo.position_id).first()
        if pos is None:
            continue
        touched = []

        # ── entry-fill true-up (open and closed positions alike) ─────────────
        if g["entry_px"]:
            real = g["entry_px"]
            if g["entry_side"] == "BUY":
                booked = float(pos.avg_buy_price or 0)
                if booked > 0 and abs(booked - real) > _EPS_PX:
                    pos.avg_buy_price = Decimal(str(round(real, 2)))
                    touched.append(f"entry {booked:.2f}->{real:.2f}")
            else:
                booked = float(pos.avg_sell_price or 0)
                if booked > 0 and abs(booked - real) > _EPS_PX:
                    pos.avg_sell_price = Decimal(str(round(real, 2)))
                    touched.append(f"entry {booked:.2f}->{real:.2f}")

        # ── realized P&L true-up (closed positions only) ──────────────────────
        # Require the ENTRY deal in-window too: once the sliding lookback ages
        # past the open, total_usd silently loses the entry-deal commission and
        # the row gets rewritten a few cents off on every pass — bumping
        # modified_at for days (the leak behind the 2026-07-09 false
        # kill-switch trip) and corrupting realized by the entry commission.
        if (float(pos.quantity or 0) == 0 and g["out_vol"] > 0
                and g["entry_px"]):
            unit = _USD_PER_PNL_UNIT.get(pos.symbol, 100.0)
            real_units = g["total_usd"] / unit
            booked_units = float(pos.realized_profit_loss or 0)
            if abs(booked_units - real_units) > _EPS_PNL:
                pos.realized_profit_loss = Decimal(str(round(real_units, 2)))
                touched.append(
                    f"realized {booked_units * unit:+.2f}->"
                    f"{g['total_usd']:+.2f} USD (incl. commission/swap)")

        if touched:
            changed += 1
            log.info("[RECON] position %s (%s): %s",
                     pos.id, eo.broker_order_id, "; ".join(touched))
    return changed


def _accounts_in_scope(sess) -> list:
    """Distinct user_broker ids across deployed UserStrategies."""
    rows = (
        sess.query(UserStrategy.user_broker_id)
        .filter(UserStrategy.deployed == True)  # noqa: E712
        .distinct()
        .all()
    )
    return [r[0] for r in rows if r[0] is not None]


def run_once() -> None:
    sess = Session()
    try:
        for ub_id in _accounts_in_scope(sess):
            client = client_for_broker(sess, ub_id)
            if client is None:
                continue
            grouped = group_deals(fetch_deals(client, LOOKBACK_H))
            n = apply_deals(sess, grouped)
            if n:
                sess.commit()
                log.info("[RECON] account %s: %d position(s) trued-up", ub_id, n)
    except Exception:
        sess.rollback()
        log.exception("[RECON] pass failed — rolled back")
    finally:
        sess.close()


def main() -> None:
    log.info("=== fill_reconciler started | loop=%ds lookback=%dh ===",
             LOOP_SEC, LOOKBACK_H)
    while True:
        started = time.time()
        try:
            # Reconcile through the maintenance window too (cheap, and deals
            # can land right at the close) — only skip weekends.
            run_once()
        except KeyboardInterrupt:
            log.info("=== fill_reconciler stopped ===")
            return
        except Exception:
            log.exception("[LOOP ERROR] continuing")
        time.sleep(max(1.0, LOOP_SEC - (time.time() - started)))


if __name__ == "__main__":
    main()
