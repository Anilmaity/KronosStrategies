"""READ-ONLY: list every Strategy and whether it is LIVE in the DB.

LIVE := Strategy.is_active AND >=1 UserStrategy with (deployed=True, is_active=True).
Only SELECTs."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.models import Session, Strategy, UserStrategy  # noqa: E402


def main() -> int:
    sess = Session()
    try:
        strats = sess.query(Strategy).order_by(Strategy.name).all()
        print(f"{len(strats)} Strategy rows:\n")
        print(f"{'name':<34} {'s.active':>8} {'#US':>4} {'#live_US':>8} {'LIVE':>5}")
        print("-" * 64)
        for s in strats:
            uss = sess.query(UserStrategy).filter_by(strategy_id=s.id).all()
            live_us = [u for u in uss if u.is_active and u.deployed]
            live = bool(s.is_active and live_us)
            print(f"{s.name:<34} {str(s.is_active):>8} {len(uss):>4} "
                  f"{len(live_us):>8} {('YES' if live else 'no'):>5}")
        return 0
    finally:
        sess.close()


if __name__ == "__main__":
    raise SystemExit(main())
