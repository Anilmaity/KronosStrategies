"""
One-shot smoke test: places a REAL MetaAPI market order tagged as M90_FADE
to verify the live entry pipeline (Strategy -> UserStrategy -> place_entry ->
MetaAPI -> Position/Order/Trigger rows -> dashboard).

Run from KronosStrategies root:
    cd C:/Projects/PycharmProjects/personal/KronosStrategies
    python strategies/_scratch/dummy_trade_m90_fade.py

Behavior:
  - BUY 0.01 XAU at mkt
  - TP = mid + 3.0  (~$3 win if TP hits)
  - SL = mid - 3.0  (~$3 loss if SL hits)
    (XAUUSD requires minimum stop distance from market — typically a few
    dollars at current price; <$1 stops will be rejected with
    TRADE_RETCODE_INVALID_STOPS.)
  - reason tag: DUMMY_TEST_M90_FADE_<utcnow>
  - Asks for confirmation before placing the order.

Cleanup (if needed):
  - HFT_Pos_Eval on the deploy host will manage TP/SL automatically.
  - Manual close from MetaAPI / MT5: search for position by comment/reason tag.
  - Or run cleanup SQL printed at the end.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from shared.tsdb_reader import fetch_candles  # noqa: E402
from shared.models import Session, Strategy, UserStrategy  # noqa: E402
from strategy.ict_engine import EntrySignal  # noqa: E402
from strategy.entry_manager import place_entry  # noqa: E402

SYMBOL = "XAU_USD"
VARIATION = "M90_FADE"
STRATEGY_NAME = "Research M90_FADE"


def main() -> int:
    # 1. Pre-flight: confirm strategy + user_strategy active
    s = Session()
    try:
        st = s.query(Strategy).filter_by(name=STRATEGY_NAME, is_active=True).first()
        if not st:
            print(f"FAIL: Strategy {STRATEGY_NAME!r} not active in DB")
            return 1
        us = (
            s.query(UserStrategy)
            .filter_by(strategy_id=st.id, is_active=True, deployed=True)
            .first()
        )
        if not us:
            print(f"FAIL: No active+deployed UserStrategy for {STRATEGY_NAME!r}")
            return 1
        print(f"OK  Strategy   id={st.id}  entry_qty={st.entry_quantity}")
        print(f"OK  UserStrategy id={us.id}  broker={us.user_broker_id}")
    finally:
        s.close()

    # 2. Fetch current price
    c = fetch_candles("1m", days=1, symbol=SYMBOL)
    if c.empty:
        print("FAIL: no 1m candles returned")
        return 1
    last = c.iloc[-1]
    mid = float(last["close"])
    print(f"OK  latest 1m close @ {last['time']}  mid={mid:.2f}")

    # 3. Build tight-RR entry signal
    entry_px = mid
    tp = mid + 3.0
    sl = mid - 3.0
    tag = f"DUMMY_TEST_{VARIATION}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    entry = EntrySignal(
        side="BUY",
        entry_price=entry_px,
        stop_loss=sl,
        take_profit=tp,
        reason=tag,
        zone_low=entry_px,
        zone_high=entry_px,
    )
    print()
    print(f"About to place: BUY {SYMBOL} 0.01 lot @ ~{entry_px:.2f}  "
          f"TP={tp:.2f}  SL={sl:.2f}  tag={tag}")
    print(f"Variation tag for entry_manager: {VARIATION!r}")
    print()
    ack = input("Type 'yes' to send the order, anything else to abort: ").strip().lower()
    if ack != "yes":
        print("aborted, no order placed")
        return 2

    # 4. Place
    placed = place_entry(entry, symbol=SYMBOL, variation=VARIATION)
    print()
    print(f"place_entry returned: {placed!r}")

    # 5. Read back what got written
    s = Session()
    try:
        from shared.models import Position, Order
        # most recent position for this user_strategy_id
        pos = (
            s.query(Position)
            .filter_by(user_strategy_id=us.id)
            .order_by(Position.created_at.desc())
            .first()
        )
        if pos:
            print(f"latest Position: id={pos.id}  qty={pos.quantity}  symbol={pos.symbol}  pnl={pos.profit_loss}  created={pos.created_at}")
            for o in pos.apis_orders:
                print(f"  Order id={o.id}  side={o.side}  price={o.price}  status={o.status}  reason={o.reason!r}  broker_order_id={o.broker_order_id!r}")
        else:
            print("no Position rows found for this user_strategy_id")
        print()
        print("Cleanup SQL (if you need to delete the row manually):")
        if pos:
            print(f"  DELETE FROM apis_order WHERE position_id = '{pos.id}';")
            print(f"  DELETE FROM apis_trigger WHERE position_id = '{pos.id}';")
            print(f"  DELETE FROM apis_position WHERE id = '{pos.id}';")
        print(f"  -- broker close is independent: do it via MT5 or MetaAPI dashboard, search for '{tag}' in comment")
    finally:
        s.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
