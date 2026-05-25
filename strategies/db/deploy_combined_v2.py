"""
Deploy the Combined Suite v2 strategy into the live Kronos DB.

Inserts (idempotently):
  - apis_strategy  row  name="Kronos Combined Suite v2"
      (matches entry_manager._VARIATION_STRATEGY_NAME["KRONOS_COMBINED_V2"]),
      entry_quantity = ENTRY_QTY (0.01 lot/leg — the user-chosen size; EVERY leg
      this one strategy places is one order of this size).
  - apis_userstrategy row  deployed=True, is_active=True, multiplyer=MULTIPLYER,
      bound to the SAME live UserBroker (MetaAPI account) as the reference
      strategy, so it trades through the same bridge as the proven legs.

This is the REAL-MONEY ENABLE step. It is dry-run by default (prints the plan,
writes nothing). Re-run with --commit to persist. After committing, start the
runner:  docker compose up -d kronos_combined_v2   (and ensure the position_manager service
is running for the SL/TP/TIME_EXIT triggers).

Run:
  cd C:\\Projects\\PycharmProjects\\personal\\KronosStrategies\\strategies
  python -m db.deploy_combined_v2            # dry-run (print plan, no writes)
  python -m db.deploy_combined_v2 --commit   # actually write the rows (go live)
"""
from __future__ import annotations

import os
import sys
import uuid
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.models import (
    Session,
    Strategy,
    UserStrategy,
    CurrencyPair,
    UserBroker,
)

SYMBOL = "XAU_USD"

# Strategy.name MUST equal entry_manager._VARIATION_STRATEGY_NAME["KRONOS_COMBINED_V2"].
STRATEGY_NAME = "Kronos Combined Suite v2"
VARIATION     = "KRONOS_COMBINED_V2"
DESCRIPTION   = (
    "Combined Suite v2 — full two-sided XAU/USD book as ONE strategy: MR base "
    "(S02/S05/S06/S07/S14) + long fades (E1/E2/E3) + bear-day short fades "
    "(SE1/SE2); BOS daily-bias gate (N20 longs / N30 shorts); per-leg ATR exits "
    "+ max-holds (TIME_EXIT); holds several legs open at once."
)

# 0.01 lot per leg (user-chosen). multiplyer 1 => qty per order = 0.01 * 1.
ENTRY_QTY  = Decimal("0.01")
MULTIPLYER = 1

# Mirror this strategy's UserBroker binding (the live MetaAPI account).
REFERENCE_STRATEGY_NAME = "ICT Breaker Block (M15)"


def main(commit: bool) -> int:
    sess = Session()
    try:
        cp = sess.query(CurrencyPair).filter_by(symbol=SYMBOL).first()
        if cp is None:
            print(f"FATAL: CurrencyPair symbol='{SYMBOL}' not found.")
            return 1
        print(f"[OK]  CurrencyPair {SYMBOL} -> id={cp.id}")

        ref_strat = (
            sess.query(Strategy)
            .filter_by(name=REFERENCE_STRATEGY_NAME, currencypair_id=cp.id)
            .first()
        )
        if ref_strat is None:
            print(f"FATAL: reference Strategy '{REFERENCE_STRATEGY_NAME}' not found "
                  f"-- can't infer the live UserBroker binding.")
            return 1
        ref_us = (
            sess.query(UserStrategy)
            .filter_by(strategy_id=ref_strat.id, deployed=True)
            .first()
        )
        if ref_us is None:
            print(f"[WARN] no deployed UserStrategy on '{REFERENCE_STRATEGY_NAME}', "
                  f"falling back to any XAU_USD UserStrategy for the broker binding.")
            ref_us = (
                sess.query(UserStrategy)
                .join(Strategy, UserStrategy.strategy_id == Strategy.id)
                .filter(Strategy.currencypair_id == cp.id)
                .first()
            )
        if ref_us is None:
            print("FATAL: no UserStrategy on any XAU_USD strategy -- no UserBroker "
                  "to bind to.")
            return 1
        user_broker = sess.query(UserBroker).filter_by(id=ref_us.user_broker_id).first()
        if user_broker is None:
            print(f"FATAL: UserBroker id={ref_us.user_broker_id} not found.")
            return 1
        print(f"[OK]  UserBroker {user_broker.id} ({user_broker.status})  "
              f"entry_qty={ENTRY_QTY}  multiplyer={MULTIPLYER}  "
              f"=> qty/order={ENTRY_QTY * MULTIPLYER}")

        strat = sess.query(Strategy).filter_by(name=STRATEGY_NAME).first()
        if strat is None:
            strat = Strategy(
                id=uuid.uuid4(),
                name=STRATEGY_NAME,
                description=DESCRIPTION,
                is_active=True,
                capital_required="100000.00",
                json_data={"variation": VARIATION, "deployed_via": "deploy_combined_v2"},
                params={},
                entry_quantity=ENTRY_QTY,
                currencypair_id=cp.id,
            )
            sess.add(strat)
            sess.flush()
            print(f"[NEW] Strategy '{STRATEGY_NAME}' id={strat.id} qty={strat.entry_quantity}")
        else:
            print(f"[SKIP] Strategy '{STRATEGY_NAME}' already present id={strat.id} "
                  f"(entry_quantity={strat.entry_quantity}; not modified)")

        existing_us = (
            sess.query(UserStrategy)
            .filter_by(strategy_id=strat.id, user_broker_id=user_broker.id)
            .first()
        )
        if existing_us is None:
            us = UserStrategy(
                id=uuid.uuid4(),
                name=f"{STRATEGY_NAME} live",
                is_active=True,
                multiplyer=MULTIPLYER,
                deployed=True,
                strategy_id=strat.id,
                user_broker_id=user_broker.id,
            )
            sess.add(us)
            sess.flush()
            print(f"[NEW] UserStrategy id={us.id} deployed=True is_active=True")
        else:
            changed = False
            if not existing_us.is_active:
                existing_us.is_active = True; changed = True
            if not existing_us.deployed:
                existing_us.deployed = True; changed = True
            print(f"[{'UPD' if changed else 'SKIP'}] UserStrategy id={existing_us.id} "
                  f"deployed/active ensured")

        if commit:
            sess.commit()
            print("\nCOMMITTED — Combined Suite v2 is now LIVE-ENABLED in the DB.")
            print("Next: docker compose up -d kronos_combined_v2  (and position_manager).")
        else:
            sess.rollback()
            print("\nDRY-RUN (no writes). Re-run with --commit to go live.")
        return 0

    except Exception as e:
        sess.rollback()
        print(f"FATAL: {type(e).__name__}: {e}")
        raise
    finally:
        sess.close()


if __name__ == "__main__":
    raise SystemExit(main(commit="--commit" in sys.argv))
