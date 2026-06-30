"""
Deploy the DEDICATED dashboard strategy for the standalone challenge_xau_h4 bot.

Creates (idempotently) a Strategy + UserStrategy row purely so the standalone bot's
trades show on the Kronos dashboard under their OWN strategy, separate from the
integrated `challenge_xau` bot's "Challenge XAU H4 Trend" rows. Unlike
deploy_challenge_xau.py this is NOT wired into entry_manager — the standalone bot
places via its own ChallengeBroker and writes the dashboard via
strategies/challenge/dashboard.py. These rows are for attribution/display only.

Binds to an existing XAU_USD UserBroker (the same "Fundingpips Anil" account the bot
trades) so the apis_order rows attribute correctly. Override via env.

Run (from strategies/):
  python -m db.deploy_challenge_xau_h4_dashboard            # dry-run (print plan + ids)
  python -m db.deploy_challenge_xau_h4_dashboard --commit   # write the rows

After --commit, copy the printed ids into the challenge_xau_h4 service env:
  CHALLENGE_DASH_STRATEGY_ID, CHALLENGE_DASH_USER_STRATEGY_ID,
  CHALLENGE_DASH_USER_BROKER_ID, CHALLENGE_DASH_CURRENCYPAIR_ID
"""
from __future__ import annotations

import os
import sys
import uuid
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.models import Session, Strategy, UserStrategy, CurrencyPair, UserBroker

SYMBOL = "XAU_USD"
STRATEGY_NAME = "Challenge XAU H4 (standalone)"
DESCRIPTION = (
    "Standalone H4 Donchian(20) trend-follow bot (strategies/challenge): own MetaAPI "
    "broker + ChallengeGuard. Dashboard-only strategy, separate from the integrated "
    "'Challenge XAU H4 Trend'."
)
ENTRY_QTY = Decimal(os.getenv("CHALLENGE_H4_DASH_LOT", "0.01"))
# Default to the "Fundingpips Anil" UserBroker (the account the bot trades).
DEFAULT_USER_BROKER_ID = "2acd477c-d51a-4884-b73a-d6d688767b32"


def main(commit: bool) -> int:
    sess = Session()
    try:
        cp = sess.query(CurrencyPair).filter_by(symbol=SYMBOL).first()
        if cp is None:
            print(f"FATAL: CurrencyPair symbol='{SYMBOL}' not found.")
            return 1
        print(f"[OK]  CurrencyPair {SYMBOL} -> id={cp.id}")

        ub_id = os.getenv("CHALLENGE_DASH_USER_BROKER_ID", DEFAULT_USER_BROKER_ID).strip()
        user_broker = sess.query(UserBroker).filter_by(id=ub_id).first()
        if user_broker is None:
            print(f"FATAL: UserBroker id={ub_id} not found (set CHALLENGE_DASH_USER_BROKER_ID).")
            return 1
        print(f"[OK]  UserBroker -> id={user_broker.id} status={user_broker.status}")

        strat = sess.query(Strategy).filter_by(name=STRATEGY_NAME).first()
        if strat is None:
            strat = Strategy(
                id=uuid.uuid4(), name=STRATEGY_NAME, description=DESCRIPTION,
                is_active=True, capital_required="5000.00",
                json_data={"deployed_via": "deploy_challenge_xau_h4_dashboard",
                           "kind": "standalone-dashboard"},
                params={}, entry_quantity=ENTRY_QTY, currencypair_id=cp.id,
            )
            sess.add(strat); sess.flush()
            print(f"[NEW] Strategy '{STRATEGY_NAME}' id={strat.id}")
        else:
            print(f"[SKIP] Strategy '{STRATEGY_NAME}' already present id={strat.id}")

        us = (sess.query(UserStrategy)
              .filter_by(strategy_id=strat.id, user_broker_id=user_broker.id).first())
        if us is None:
            us = UserStrategy(
                id=uuid.uuid4(), name=f"{STRATEGY_NAME} live", is_active=True,
                multiplyer=1, deployed=True, strategy_id=strat.id,
                user_broker_id=user_broker.id,
            )
            sess.add(us); sess.flush()
            print(f"[NEW] UserStrategy id={us.id} deployed=True active=True")
        else:
            print(f"[SKIP] UserStrategy id={us.id} already present")

        print("\n--- env for the challenge_xau_h4 service ---")
        print(f"CHALLENGE_DASH_STRATEGY_ID={strat.id}")
        print(f"CHALLENGE_DASH_USER_STRATEGY_ID={us.id}")
        print(f"CHALLENGE_DASH_USER_BROKER_ID={user_broker.id}")
        print(f"CHALLENGE_DASH_CURRENCYPAIR_ID={cp.id}")

        if commit:
            sess.commit(); print("\nCOMMITTED.")
        else:
            sess.rollback(); print("\nDRY-RUN (no writes). Re-run with --commit to persist.")
        return 0
    except Exception as e:
        sess.rollback()
        print(f"FATAL: {type(e).__name__}: {e}")
        raise
    finally:
        sess.close()


if __name__ == "__main__":
    raise SystemExit(main(commit="--commit" in sys.argv))
