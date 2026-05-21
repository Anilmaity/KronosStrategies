"""
Deploy the 5 v15e Kronos strategies into the live Kronos DB.

For each KRONOS_S0X variation, inserts:
  - apis_strategy row (name matches entry_manager._VARIATION_STRATEGY_NAME value)
  - apis_userstrategy row (deployed=True, is_active=True, multiplyer=1)

Reuses:
  - the XAU_USD currencypair row that already exists
  - the active UserBroker bound to the existing ICT Breaker Block (M15) strategy
    (so the new strategies trade through the same MetaAPI cloud bridge / MT5
     account that the proven ICT_S4 strategy is using).
  - the per-strategy entry_quantity is set to match the existing ICT_S4
    deployment so notionals are aligned.

Idempotent: if a Strategy with the target name already exists it is reused
(no duplicate insert). UserStrategy is created only if no
(strategy_id, user_broker_id) pair already exists.

Run:
  cd C:\\Projects\\PycharmProjects\\personal\\KronosStrategies\\strategies
  python -m db.deploy_kronos_v15e            # dry-run (print plan, no writes)
  python -m db.deploy_kronos_v15e --commit   # actually write the rows
"""
from __future__ import annotations

import os
import sys
import uuid
from decimal import Decimal

# Make the package layout work whether run as `python -m db.deploy_kronos_v15e`
# or `python deploy_kronos_v15e.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.models import (
    Session,
    Strategy,
    UserStrategy,
    CurrencyPair,
    UserBroker,
)

SYMBOL = "XAU_USD"

# Reference strategy whose UserBroker and entry_quantity we mirror.
REFERENCE_STRATEGY_NAME = "ICT Breaker Block (M15)"

# (DB Strategy.name, variation tag, one-line description)
TARGETS = [
    ("Kronos S02 Stoch Revert",
     "KRONOS_S02_STOCH",
     "v3 S02 stoch(14)<15 dip-buy in EMA100>200 uptrend + D1 bias + zone, "
     "with v15e TNX gate + event filter."),
    ("Kronos S05 Threebar Pull",
     "KRONOS_S05_THREEBAR",
     "v3 S05 three-bar pullback to EMA20 in uptrend, with v15e TNX gate + "
     "event filter."),
    ("Kronos S06 Session Sweep",
     "KRONOS_S06_SWEEP",
     "v3 S06 prior-session H/L sweep fade (M15, two-way), with v15e TNX gate "
     "+ event filter."),
    ("Kronos S07 CRT",
     "KRONOS_S07_CRT",
     "v3 S07 CRT rejection fade (M15, two-way), with v15e TNX gate + event "
     "filter."),
    ("Kronos S14 M5 EMA Stretch",
     "KRONOS_S14_M5_STRETCH",
     "v3 S14 M5 EMA20 stretch dip-buy with falling-knife veto, v15e TNX "
     "gate + event filter."),
]


def main(commit: bool) -> int:
    sess = Session()
    try:
        # 1. XAU_USD currencypair
        cp = sess.query(CurrencyPair).filter_by(symbol=SYMBOL).first()
        if cp is None:
            print(f"FATAL: CurrencyPair symbol='{SYMBOL}' not found.")
            return 1
        print(f"[OK]  CurrencyPair {SYMBOL} -> id={cp.id}")

        # 2. Reference Strategy + its UserBroker (the live MetaAPI account binding)
        ref_strat = (
            sess.query(Strategy)
            .filter_by(name=REFERENCE_STRATEGY_NAME, currencypair_id=cp.id)
            .first()
        )
        if ref_strat is None:
            print(f"FATAL: reference Strategy '{REFERENCE_STRATEGY_NAME}' not "
                  f"found -- can't infer UserBroker / entry_quantity.")
            return 1
        # Reference UserStrategy: ANY (even paused) — we only need the user_broker_id
        # so the new strategies trade through the same MetaAPI account binding.
        ref_us = (
            sess.query(UserStrategy)
            .filter_by(strategy_id=ref_strat.id, deployed=True)
            .first()
        )
        if ref_us is None:
            # fall back to ANY UserStrategy on XAU_USD strategies
            print(f"[WARN] no UserStrategy on '{REFERENCE_STRATEGY_NAME}', "
                  f"falling back to any XAU_USD UserStrategy for the broker binding.")
            ref_us = (
                sess.query(UserStrategy)
                .join(Strategy, UserStrategy.strategy_id == Strategy.id)
                .filter(Strategy.currencypair_id == cp.id)
                .first()
            )
        if ref_us is None:
            print("FATAL: no UserStrategy at all on any XAU_USD strategy "
                  "-- no UserBroker to bind new strategies to.")
            return 1
        user_broker = (
            sess.query(UserBroker).filter_by(id=ref_us.user_broker_id).first()
        )
        if user_broker is None:
            print(f"FATAL: UserBroker id={ref_us.user_broker_id} not found.")
            return 1
        ref_qty = ref_strat.entry_quantity
        print(f"[OK]  Reference '{REFERENCE_STRATEGY_NAME}' "
              f"entry_qty={ref_qty}, user_broker={user_broker.id} ({user_broker.status})")

        # 3. Plan + execute the inserts
        inserted_strategies = 0
        inserted_user_strategies = 0
        skipped_strategies = 0
        skipped_user_strategies = 0

        for name, variation, description in TARGETS:
            strat = sess.query(Strategy).filter_by(name=name).first()
            if strat is None:
                strat = Strategy(
                    id=uuid.uuid4(),
                    name=name,
                    description=description,
                    is_active=True,
                    capital_required="100000.00",
                    json_data={"variation": variation, "deployed_via": "deploy_kronos_v15e"},
                    params={},
                    entry_quantity=Decimal(str(ref_qty)),
                    currencypair_id=cp.id,
                )
                sess.add(strat)
                sess.flush()
                inserted_strategies += 1
                print(f"[NEW] Strategy '{name}' id={strat.id} qty={strat.entry_quantity}")
            else:
                skipped_strategies += 1
                print(f"[SKIP] Strategy '{name}' already present id={strat.id}")

            existing_us = (
                sess.query(UserStrategy)
                .filter_by(strategy_id=strat.id, user_broker_id=user_broker.id)
                .first()
            )
            if existing_us is None:
                us = UserStrategy(
                    id=uuid.uuid4(),
                    name=f"{name} live",
                    is_active=True,
                    multiplyer=1,
                    deployed=True,
                    strategy_id=strat.id,
                    user_broker_id=user_broker.id,
                )
                sess.add(us)
                sess.flush()
                inserted_user_strategies += 1
                print(f"[NEW] UserStrategy for '{name}' id={us.id}")
            else:
                # Make sure existing is_active + deployed are set.
                changed = False
                if not existing_us.is_active:
                    existing_us.is_active = True
                    changed = True
                if not existing_us.deployed:
                    existing_us.deployed = True
                    changed = True
                if changed:
                    print(f"[UPD] UserStrategy for '{name}' id={existing_us.id} -> active/deployed")
                else:
                    skipped_user_strategies += 1
                    print(f"[SKIP] UserStrategy for '{name}' already deployed id={existing_us.id}")

        print()
        print(f"Summary: new strategies={inserted_strategies} "
              f"skipped_strategies={skipped_strategies} "
              f"new_user_strategies={inserted_user_strategies} "
              f"skipped_user_strategies={skipped_user_strategies}")

        if commit:
            sess.commit()
            print("\nCOMMITTED.")
        else:
            sess.rollback()
            print("\nDRY-RUN (no writes). Re-run with --commit to persist.")
        return 0

    except Exception as e:
        sess.rollback()
        print(f"FATAL: {type(e).__name__}: {e}")
        raise
    finally:
        sess.close()


if __name__ == "__main__":
    commit = "--commit" in sys.argv
    raise SystemExit(main(commit=commit))
