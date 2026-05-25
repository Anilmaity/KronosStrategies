"""READ-ONLY: quantify what DELETING the 5 standalone Kronos legs would cascade.

Because every FK uses ondelete=CASCADE, deleting a Strategy row removes its
UserStrategies -> Positions -> Orders/Triggers, plus Signals/Actions/
StrategySignals/BacktestReports. This script only COUNTS; it writes nothing.

'OPEN positions' = Positions that still have PENDING triggers (the SL/TP the
monitor is watching) -> deleting these orphans real-money broker positions.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.models import (  # noqa: E402
    Session, Strategy, UserStrategy, Position, Order, Trigger,
    Signal, StrategySignal, Action, BacktestReport,
)

LEG_NAMES = [
    "Kronos S02 Stoch Revert",
    "Kronos S05 Threebar Pull",
    "Kronos S06 Session Sweep",
    "Kronos S07 CRT",
    "Kronos S14 M5 EMA Stretch",
]


def main() -> int:
    sess = Session()
    try:
        tot_open = 0
        print(f"{'strategy':<28} {'US':>3} {'pos':>5} {'OPEN':>5} {'ord':>5} "
              f"{'trig':>5} {'pend':>5} {'sig':>6} {'ssig':>6} {'act':>4} {'rpt':>4}")
        print("-" * 100)
        for name in LEG_NAMES:
            s = sess.query(Strategy).filter_by(name=name).first()
            if s is None:
                print(f"{name:<28} (Strategy not found)")
                continue
            us = sess.query(UserStrategy).filter_by(strategy_id=s.id).all()
            us_ids = [u.id for u in us]
            pos = (sess.query(Position).filter(Position.user_strategy_id.in_(us_ids)).all()
                   if us_ids else [])
            pos_ids = [p.id for p in pos]
            if pos_ids:
                orders = sess.query(Order).filter(Order.position_id.in_(pos_ids)).count()
                trigs = sess.query(Trigger).filter(Trigger.position_id.in_(pos_ids)).count()
                pend_rows = (sess.query(Trigger)
                             .filter(Trigger.position_id.in_(pos_ids),
                                     Trigger.status == "PENDING").all())
                pend = len(pend_rows)
                open_pos = len({t.position_id for t in pend_rows})
            else:
                orders = trigs = pend = open_pos = 0
            sig = sess.query(Signal).filter_by(strategy_id=s.id).count()
            ssig = sess.query(StrategySignal).filter_by(strategy_id=s.id).count()
            act = sess.query(Action).filter_by(strategy_id=s.id).count()
            rpt = sess.query(BacktestReport).filter_by(strategy_id=s.id).count()
            tot_open += open_pos
            print(f"{name:<28} {len(us):>3} {len(pos):>5} {open_pos:>5} {orders:>5} "
                  f"{trigs:>5} {pend:>5} {sig:>6} {ssig:>6} {act:>4} {rpt:>4}")
        print("-" * 100)
        print(f"\nTOTAL OPEN positions (PENDING triggers) across the 5 legs: {tot_open}")
        if tot_open:
            print("  *** WARNING: deleting now would orphan live broker positions. ***")
        print("\nLegend: US=UserStrategy pos=Position OPEN=pos w/ PENDING trigger "
              "ord=Order trig=Trigger pend=PENDING trig sig=Signal ssig=StrategySignal "
              "act=Action rpt=BacktestReport — ALL of these cascade-delete.")
        return 0
    finally:
        sess.close()


if __name__ == "__main__":
    raise SystemExit(main())
