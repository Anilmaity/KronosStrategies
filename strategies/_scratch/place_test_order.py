"""
place_test_order.py
-------------------
Non-interactive smoke test: place ONE real MetaAPI market order to verify the
live broker leg from this machine.

Self-contained: calls metaapi_client.place_market_order directly (broker only,
no DB rows, no strategy attribution). SL/TP are sent with the order so the
broker enforces them server-side. Returns the broker positionId.

Defaults: BUY 0.01 XAU_USD, SL = mid - 3.0, TP = mid + 3.0 (~$3 risk at 0.01 lot).

Usage (from KronosStrategies root):
    # preview only — prints the plan, sends NOTHING:
    ./.venv/Scripts/python.exe strategies/_scratch/place_test_order.py

    # actually place it (real money on the configured account):
    ./.venv/Scripts/python.exe strategies/_scratch/place_test_order.py --yes

    # override side / volume / stop distance:
    ./.venv/Scripts/python.exe strategies/_scratch/place_test_order.py --yes --side SELL --volume 0.01 --dist 3.0

Note: requires DRY_RUN=false in .env to hit the broker; with DRY_RUN=true it
just logs the payload and returns the sentinel 'dry-run'.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))  # strategies/

from dotenv import load_dotenv

_ROOT_ENV = os.path.join(os.path.dirname(__file__), os.pardir, os.pardir, ".env")
load_dotenv(_ROOT_ENV)

from shared.tsdb_reader import fetch_candles            # noqa: E402
from shared import metaapi_client as mc                 # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="actually send the order")
    ap.add_argument("--side", default="BUY", choices=["BUY", "SELL"])
    ap.add_argument("--symbol", default="XAU_USD")
    ap.add_argument("--volume", type=float, default=0.01)
    ap.add_argument("--dist", type=float, default=3.0, help="SL/TP distance in price units")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    c = fetch_candles("1m", days=1, symbol=args.symbol)
    if c.empty:
        print("FAIL: no 1m candles returned (TSDB unreachable or no data)")
        return 1
    last = c.iloc[-1]
    mid = float(last["close"])

    if args.side == "BUY":
        sl, tp = round(mid - args.dist, 2), round(mid + args.dist, 2)
    else:
        sl, tp = round(mid + args.dist, 2), round(mid - args.dist, 2)

    print(f"account   : {os.getenv('META_ACCOUNT_ID')}")
    print(f"DRY_RUN   : {os.getenv('DRY_RUN')}")
    print(f"price     : latest 1m close @ {last['time']}  mid={mid:.2f}")
    print(f"order plan: {args.side} {args.volume} {args.symbol}  SL={sl:.2f}  TP={tp:.2f}")

    if not args.yes:
        print("\nPREVIEW ONLY — re-run with --yes to send the order.")
        return 0

    print("\nsending...")
    pid = mc.place_market_order(args.side, args.symbol, args.volume, sl, tp, entry_price=mid)
    print(f"\nRESULT positionId = {pid!r}")
    if pid and pid != "dry-run":
        print(f"To close it:  ./.venv/Scripts/python.exe -c \"import sys; sys.path.insert(0,'strategies'); "
              f"from shared import metaapi_client as mc; print(mc.close_position_by_id('{pid}'))\"")
    return 0 if pid else 1


if __name__ == "__main__":
    sys.exit(main())
