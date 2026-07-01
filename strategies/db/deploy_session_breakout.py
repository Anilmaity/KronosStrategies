# strategies/db/deploy_session_breakout.py
"""
Deploy the SESSION_BREAKOUT strategy into the live Kronos DB (idempotent).

  - apis_strategy row  name="Session Breakout M5 ORB"
        (MUST match entry_manager._VARIATION_STRATEGY_NAME["SESSION_BREAKOUT"])
  - apis_userstrategy row (deployed=True, is_active=True, multiplyer=1)

Binds to the challenge FundingPips UserBroker. Prefer the explicit
SESSION_BREAKOUT_USER_BROKER_ID (the verified challenge account's UserBroker);
otherwise mirror the existing "Challenge XAU H4 Trend" strategy's UserBroker so
the new strategy trades the SAME account the retired H4 bot did.

Fixed lot: engine uses Strategy.entry_quantity (SESSION_BREAKOUT_LOT, default 0.02).

Run (from strategies/, DB_* env pointing at the app DB):
  python -m db.deploy_session_breakout            # dry-run (plan + resolved ids)
  python -m db.deploy_session_breakout --commit   # write rows
"""
from __future__ import annotations

import os
import sys
import uuid
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from shared.models import Session, Strategy, UserStrategy, CurrencyPair, UserBroker

SYMBOL = "XAU_USD"
STRATEGY_NAME = "Session Breakout M5 ORB"
VARIATION = "SESSION_BREAKOUT"
DESCRIPTION = (
    "M5 opening-range breakout, EMA240 bias, sessions [1,7,12,13,14] UTC, static "
    "OR-width stop + 1.5x-OR target, 3h max-hold. Port of strat_orb_biased."
)
# Mirror the retired H4 bot's account when no explicit override is given.
REFERENCE_STRATEGY_NAME = "Challenge XAU H4 Trend"
ENTRY_QTY = Decimal(os.getenv("SESSION_BREAKOUT_LOT", "0.02"))


def main(commit: bool) -> int:
    sess = Session()
    try:
        cp = sess.query(CurrencyPair).filter_by(symbol=SYMBOL).first()
        if cp is None:
            print(f"FATAL: CurrencyPair symbol='{SYMBOL}' not found."); return 1
        print(f"[OK]  CurrencyPair {SYMBOL} -> id={cp.id}")

        override_ub_id = os.getenv("SESSION_BREAKOUT_USER_BROKER_ID", "").strip()
        if override_ub_id:
            user_broker = sess.query(UserBroker).filter_by(id=override_ub_id).first()
            if user_broker is None:
                print(f"FATAL: SESSION_BREAKOUT_USER_BROKER_ID={override_ub_id} not found."); return 1
            print(f"[OK]  Explicit UserBroker override -> id={user_broker.id} status={user_broker.status}")
        else:
            ref = sess.query(Strategy).filter_by(name=REFERENCE_STRATEGY_NAME, currencypair_id=cp.id).first()
            if ref is None:
                print(f"FATAL: reference Strategy '{REFERENCE_STRATEGY_NAME}' not found -- "
                      f"set SESSION_BREAKOUT_USER_BROKER_ID explicitly."); return 1
            ref_us = sess.query(UserStrategy).filter_by(strategy_id=ref.id).first()
            if ref_us is None:
                print(f"FATAL: no UserStrategy on '{REFERENCE_STRATEGY_NAME}'."); return 1
            user_broker = sess.query(UserBroker).filter_by(id=ref_us.user_broker_id).first()
            if user_broker is None:
                print(f"FATAL: UserBroker id={ref_us.user_broker_id} not found."); return 1
        print(f"[OK]  Binding to UserBroker={user_broker.id} ({user_broker.status}) "
              f"-- verify this is the challenge account.")
        print(f"[OK]  Fixed entry_quantity = {ENTRY_QTY} lot")

        strat = sess.query(Strategy).filter_by(name=STRATEGY_NAME).first()
        if strat is None:
            strat = Strategy(
                id=uuid.uuid4(), name=STRATEGY_NAME, description=DESCRIPTION,
                is_active=True, capital_required="5000.00",
                json_data={"variation": VARIATION, "deployed_via": "deploy_session_breakout"},
                params={}, entry_quantity=ENTRY_QTY, currencypair_id=cp.id,
            )
            sess.add(strat); sess.flush()
            print(f"[NEW] Strategy '{STRATEGY_NAME}' id={strat.id} qty={strat.entry_quantity}")
        else:
            if strat.entry_quantity != ENTRY_QTY:
                print(f"[UPD] entry_quantity {strat.entry_quantity} -> {ENTRY_QTY}")
                strat.entry_quantity = ENTRY_QTY
            if not strat.is_active:
                strat.is_active = True
            print(f"[OK]  Strategy present id={strat.id}")

        us = sess.query(UserStrategy).filter_by(strategy_id=strat.id, user_broker_id=user_broker.id).first()
        if us is None:
            # Raw SQL: apis_userstrategy has a NOT NULL `archived` column the
            # UserStrategy ORM model does not map, so an ORM insert omits it and
            # violates the constraint. Set archived=FALSE explicitly.
            new_us_id = uuid.uuid4()
            sess.execute(text(
                """
                INSERT INTO apis_userstrategy
                    (id, created_at, modified_at, name, is_active, multiplyer,
                     deployed, strategy_id, user_broker_id, archived)
                VALUES (:id, NOW(), NOW(), :name, TRUE, 1, TRUE, :sid, :ubid, FALSE)
                """),
                {"id": str(new_us_id), "name": f"{STRATEGY_NAME} live",
                 "sid": str(strat.id), "ubid": str(user_broker.id)})
            print(f"[NEW] UserStrategy id={new_us_id} deployed=True active=True archived=False")
        else:
            us.is_active = True; us.deployed = True
            print(f"[OK]  UserStrategy id={us.id} deployed/active")

        if commit:
            sess.commit(); print("\nCOMMITTED.")
        else:
            sess.rollback(); print("\nDRY-RUN (no writes). Re-run with --commit to persist.")
        return 0
    except Exception as e:
        sess.rollback(); print(f"FATAL: {type(e).__name__}: {e}"); raise
    finally:
        sess.close()


if __name__ == "__main__":
    raise SystemExit(main(commit="--commit" in sys.argv))
