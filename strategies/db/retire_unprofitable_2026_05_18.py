"""
retire_unprofitable_2026_05_18.py
---------------------------------
HARD-DELETE the retired strategies and ALL their downstream data:
  - apis_strategy row(s) named in TARGETS
  - apis_userstrategy rows attached (CASCADE)
  - apis_position rows under those user strategies (CASCADE)
  - apis_trigger and apis_order rows under those positions (CASCADE)
  - apis_signal and apis_action rows attached to the strategy (CASCADE)

Usage:
  python db/retire_unprofitable_2026_05_18.py             # dry run (default)
  python db/retire_unprofitable_2026_05_18.py --commit    # actually delete

DESTRUCTIVE. Wipes order history. Run only after reviewing dry-run output.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from shared.models import Session, Strategy, UserStrategy, Position, Trigger, Order, Signal, Action

TARGETS = [
    "Liquidity Scalper VAR1",   # -582 pts / 7,337 trades over 16mo — unprofitable
    "ICT Daily CRT (D1)",       # +42 pts / 71 trades — sample too small
]


def main(commit: bool) -> int:
    sess = Session()
    try:
        strategies = sess.query(Strategy).filter(Strategy.name.in_(TARGETS)).all()
        if not strategies:
            print("[INFO] No matching strategy rows found. Nothing to do.")
            return 0

        print(f"[INFO] Found {len(strategies)} strategy row(s) to delete:")
        total_us = total_pos = total_trig = total_ord = total_sig = total_act = 0

        for s in strategies:
            us_rows = sess.query(UserStrategy).filter_by(strategy_id=s.id).all()
            us_ids = [u.id for u in us_rows]
            pos_rows = (sess.query(Position).filter(Position.user_strategy_id.in_(us_ids)).all()
                        if us_ids else [])
            pos_ids = [p.id for p in pos_rows]
            trig_n = (sess.query(Trigger).filter(Trigger.position_id.in_(pos_ids)).count()
                      if pos_ids else 0)
            ord_n = (sess.query(Order).filter(Order.position_id.in_(pos_ids)).count()
                     if pos_ids else 0)
            sig_n = sess.query(Signal).filter_by(strategy_id=s.id).count()
            act_n = sess.query(Action).filter_by(strategy_id=s.id).count()

            print(
                f"  • {s.name!r}  id={s.id}\n"
                f"      user_strategy={len(us_rows)}  position={len(pos_rows)}\n"
                f"      trigger={trig_n}  order={ord_n}  signal={sig_n}  action={act_n}"
            )
            total_us += len(us_rows)
            total_pos += len(pos_rows)
            total_trig += trig_n
            total_ord += ord_n
            total_sig += sig_n
            total_act += act_n

        print(
            f"\n[TOTAL] strategy={len(strategies)}  user_strategy={total_us}  "
            f"position={total_pos}  trigger={total_trig}  order={total_ord}  "
            f"signal={total_sig}  action={total_act}"
        )

        if not commit:
            print("\n[DRY RUN] Re-run with --commit to actually delete the above rows.")
            return 0

        # Explicit ordered DELETE — the DB column apis_userstrategy.strategy_id
        # is NOT NULL (despite the ORM saying nullable=True), so SQLAlchemy's
        # default relationship cascade (SET NULL) fails. Bulk-delete in FK
        # dependency order instead, inside one transaction.
        print("\n[COMMIT] Deleting in dependency order…")
        strat_ids = [s.id for s in strategies]
        us_ids = [u.id for u in sess.query(UserStrategy.id)
                                    .filter(UserStrategy.strategy_id.in_(strat_ids)).all()]
        pos_ids = ([p.id for p in sess.query(Position.id)
                                       .filter(Position.user_strategy_id.in_(us_ids)).all()]
                   if us_ids else [])

        def _delete(model, col, ids, label):
            if not ids:
                print(f"  - {label}: 0 (skip)")
                return
            n = sess.query(model).filter(col.in_(ids)).delete(synchronize_session=False)
            print(f"  - {label}: {n} deleted")

        _delete(Order,        Order.position_id,         pos_ids,  "apis_order")
        _delete(Trigger,      Trigger.position_id,       pos_ids,  "apis_trigger")
        _delete(Position,     Position.id,               pos_ids,  "apis_position")
        _delete(UserStrategy, UserStrategy.id,           us_ids,   "apis_userstrategy")
        _delete(Signal,       Signal.strategy_id,        strat_ids,"apis_signal")
        _delete(Action,       Action.strategy_id,        strat_ids,"apis_action")
        _delete(Strategy,     Strategy.id,               strat_ids,"apis_strategy")

        sess.commit()
        print("[DONE] Deletion committed.")
        return 0
    except Exception:
        sess.rollback()
        raise
    finally:
        sess.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true",
                    help="Actually delete. Without this flag, prints counts only.")
    args = ap.parse_args()
    sys.exit(main(commit=args.commit))
