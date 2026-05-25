"""Pause the 5 standalone Kronos MR legs (S02/S05/S06/S07/S14) on the live broker
so Combined Suite v2 can take over their signals WITHOUT double-firing on the
same MetaAPI account.

What it does (idempotent):
  - For each of the 5 named strategies, finds its UserStrategy on broker
    e673869c (the live shared bridge) that is currently is_active=True, and sets
    is_active = False.
  - deployed is LEFT True; this is a reversible PAUSE. To restore a leg later,
    flip its UserStrategy.is_active back to True.
  - Scoped to broker e673869c ONLY — never touches a leg running on another
    account.

After committing, stop the containers on the host so they stop generating
signals (entry_manager already skips placement once is_active=False):
  docker compose stop kronos_s02 kronos_s05 kronos_s06 kronos_s07 kronos_s14

Dry-run by default; --commit to apply.

Run:
  cd C:\\Projects\\PycharmProjects\\personal\\KronosStrategies\\strategies
  python -m db.disable_standalone_kronos_legs            # dry-run (no writes)
  python -m db.disable_standalone_kronos_legs --commit   # apply
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.models import Session, Strategy, UserStrategy  # noqa: E402

# The live shared MetaAPI bridge CV2 will trade on (confirmed via check_broker.py).
BROKER_ID = "e673869c-8c56-4521-9a49-ac62f07d7da9"

LEG_NAMES = [
    "Kronos S02 Stoch Revert",
    "Kronos S05 Threebar Pull",
    "Kronos S06 Session Sweep",
    "Kronos S07 CRT",
    "Kronos S14 M5 EMA Stretch",
]


def main(commit: bool) -> int:
    sess = Session()
    try:
        to_change = []
        for name in LEG_NAMES:
            strat = sess.query(Strategy).filter_by(name=name).first()
            if strat is None:
                print(f"[MISS] Strategy {name!r} not found — skipping")
                continue
            active = (
                sess.query(UserStrategy)
                .filter_by(strategy_id=strat.id, user_broker_id=BROKER_ID, is_active=True)
                .all()
            )
            if not active:
                print(f"[SKIP] {name!r}: no active UserStrategy on this broker "
                      f"(already paused)")
                continue
            for u in active:
                print(f"[PAUSE] {name!r}  UserStrategy id={u.id}  "
                      f"is_active True->False  (deployed={u.deployed} kept)")
                to_change.append(u)

        print(f"\n{len(to_change)} UserStrategy row(s) to pause on broker {BROKER_ID}.")
        if not to_change:
            print("Nothing to do.")
            return 0

        if commit:
            for u in to_change:
                u.is_active = False
            sess.commit()
            print("\nCOMMITTED — standalone Kronos legs paused (is_active=False, "
                  "deployed kept True for easy revert).")
            print("Next, stop their containers on the host:")
            print("  docker compose stop kronos_s02 kronos_s05 kronos_s06 kronos_s07 kronos_s14")
        else:
            sess.rollback()
            print("\nDRY-RUN (no writes). Re-run with --commit to apply.")
        return 0

    except Exception as e:
        sess.rollback()
        print(f"FATAL: {type(e).__name__}: {e}")
        raise
    finally:
        sess.close()


if __name__ == "__main__":
    raise SystemExit(main(commit="--commit" in sys.argv))
