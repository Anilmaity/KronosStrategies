"""READ-ONLY: confirm which broker the deploy fallback will bind to, and that it
is the live shared MetaAPI bridge used by the proven strategies. Only SELECTs."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.models import Session, Strategy, UserStrategy, UserBroker, CurrencyPair  # noqa: E402

BROKER_ID = "e673869c-8c56-4521-9a49-ac62f07d7da9"
SYMBOL = "XAU_USD"


def main() -> int:
    sess = Session()
    try:
        ub = sess.query(UserBroker).filter_by(id=BROKER_ID).first()
        print(f"Broker {BROKER_ID}: status={ub.status if ub else 'NOT FOUND'}\n")

        uss = sess.query(UserStrategy).filter_by(user_broker_id=BROKER_ID).all()
        print(f"{len(uss)} UserStrategy row(s) bound to this broker:")
        for us in uss:
            s = sess.query(Strategy).filter_by(id=us.strategy_id).first()
            name = s.name if s else "?"
            print(f"  - {name!r:42} deployed={us.deployed} is_active={us.is_active} "
                  f"strat.is_active={s.is_active if s else '?'}")

        # Reproduce the exact fallback the deploy script uses, to show what it picks.
        cp = sess.query(CurrencyPair).filter_by(symbol=SYMBOL).first()
        fallback = (
            sess.query(UserStrategy)
            .join(Strategy, UserStrategy.strategy_id == Strategy.id)
            .filter(Strategy.currencypair_id == cp.id)
            .first()
        )
        if fallback:
            fs = sess.query(Strategy).filter_by(id=fallback.strategy_id).first()
            print(f"\nDeploy fallback (.first() XAU_USD UserStrategy) -> "
                  f"strategy={fs.name!r} broker={fallback.user_broker_id}")
            print(f"  => matches e673869c? {str(fallback.user_broker_id) == BROKER_ID}")
        return 0
    finally:
        sess.close()


if __name__ == "__main__":
    raise SystemExit(main())
