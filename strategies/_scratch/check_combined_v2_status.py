"""READ-ONLY: verify whether the Combined Suite v2 strategy exists and is live.

Only SELECT queries — no inserts, no updates, no commit. Safe to run.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.models import Session, Strategy, UserStrategy, UserBroker  # noqa: E402

VARIATION = "KRONOS_COMBINED_V2"


def main() -> int:
    sess = Session()
    try:
        from shared.models import HOST, NAME  # report which DB we hit
        print(f"DB: {NAME} @ {HOST}\n")

        # Sanity: confirm we're on a populated apis_strategy and the reference exists.
        total = sess.query(Strategy).count()
        ref = sess.query(Strategy).filter_by(name="ICT Breaker Block (M15)").first()
        print(f"SANITY: total Strategy rows = {total}; "
              f"reference 'ICT Breaker Block (M15)' present = {ref is not None}\n")

        # Match by name (expected 'Kronos Combined Suite v2') OR by json_data variation.
        by_name = sess.query(Strategy).filter(Strategy.name.ilike("%combined%")).all()
        seen = {s.id for s in by_name}
        all_strats = sess.query(Strategy).all()
        by_var = [
            s for s in all_strats
            if s.id not in seen
            and isinstance(s.json_data, dict)
            and s.json_data.get("variation") == VARIATION
        ]
        strats = by_name + by_var

        if not strats:
            print("RESULT: no Strategy row matches name ~'combined' or "
                  f"json_data.variation == {VARIATION!r}.")
            print("=> Combined Suite v2 is NOT in the DB (never deployed).")
            return 0

        print(f"Found {len(strats)} candidate Strategy row(s):\n")
        for s in strats:
            print(f"  Strategy id={s.id}")
            print(f"    name           = {s.name!r}")
            print(f"    is_active      = {s.is_active}")
            print(f"    entry_quantity = {s.entry_quantity}")
            print(f"    json_data      = {s.json_data}")
            uss = sess.query(UserStrategy).filter_by(strategy_id=s.id).all()
            if not uss:
                print("    UserStrategy   = (none) -> not deployable yet")
            for us in uss:
                ub = sess.query(UserBroker).filter_by(id=us.user_broker_id).first()
                live = bool(s.is_active and us.is_active and us.deployed)
                print(f"    UserStrategy id={us.id}")
                print(f"      name       = {us.name!r}")
                print(f"      deployed   = {us.deployed}")
                print(f"      is_active  = {us.is_active}")
                print(f"      multiplyer = {us.multiplyer}")
                print(f"      broker     = {us.user_broker_id} "
                      f"({ub.status if ub else 'broker row missing'})")
                print(f"      >>> LIVE (strategy.is_active & us.is_active & deployed) = {live}")
            print()
        return 0
    finally:
        sess.close()


if __name__ == "__main__":
    raise SystemExit(main())
