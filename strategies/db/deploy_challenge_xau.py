"""
Deploy the CHALLENGE_XAU strategy into the live Kronos DB.

Inserts (idempotently):
  - apis_strategy row  name="Challenge XAU H4 Trend"
        (must match entry_manager._VARIATION_STRATEGY_NAME["CHALLENGE_XAU"])
  - apis_userstrategy row (deployed=True, is_active=True, multiplyer=1)

The strategy is bot/challenge_xau.py ported to the Kronos engine: an H4
Donchian(20) trend-follow with a chandelier TRAILING stop (Signal.trailing ->
TRAILING_STOPLOSS_POINTS trigger, ratcheted by position_monitor). It is the
deployable answer to the $5000 -> $5500 FundingPips challenge research.

Broker binding
--------------
Like deploy_kronos_v15e, this mirrors an existing XAU_USD UserBroker so the new
strategy trades through an already-configured MetaAPI account binding. If you
are running the actual FundingPips challenge account, point REFERENCE_STRATEGY_NAME
(or the resolved UserBroker) at THAT account's binding before --commit, and
verify with the dry-run output which user_broker it resolved.

Position sizing
---------------
The Kronos engine uses a FIXED lot per strategy (Strategy.entry_quantity), not
the source repo's equity/ATR-scaled position_size(). ENTRY_QTY defaults to 0.01
lot, which on XAUUSD is ~$1 per 1.0 price-point. With the H4 stop ~3*ATR
(~25-45 pts) that risks ~$25-45 per trade ~= 0.5-0.9% of a $5,000 account, well
inside the 5%/10% challenge drawdown limits. Raise CHALLENGE_XAU_LOT to size up
(at your own risk) — this script does not give financial advice.

Run (from strategies/):
  python -m db.deploy_challenge_xau            # dry-run (print plan, no writes)
  python -m db.deploy_challenge_xau --commit   # actually write the rows
  CHALLENGE_XAU_LOT=0.02 python -m db.deploy_challenge_xau --commit
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

# DB Strategy.name MUST equal entry_manager._VARIATION_STRATEGY_NAME["CHALLENGE_XAU"].
STRATEGY_NAME = "Challenge XAU H4 Trend"
VARIATION = "CHALLENGE_XAU"
DESCRIPTION = (
    "H4 Donchian(20) trend-follow, EMA20/50 bias, 3xATR chandelier trailing "
    "stop. Port of bot/challenge_xau.py (the $5000->$5500 FundingPips answer)."
)

# Reference strategy whose UserBroker (live MetaAPI account binding) we mirror.
REFERENCE_STRATEGY_NAME = "ICT Breaker Block (M15)"

# Fixed lot per trade. See module docstring for the risk math. Override via env.
ENTRY_QTY = Decimal(os.getenv("CHALLENGE_XAU_LOT", "0.01"))


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
            print(f"FATAL: reference Strategy '{REFERENCE_STRATEGY_NAME}' not "
                  f"found -- can't infer the UserBroker binding.")
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
        print(f"[OK]  Binding to UserBroker={user_broker.id} ({user_broker.status}) "
              f"-- verify this is the intended challenge account.")
        print(f"[OK]  Fixed entry_quantity = {ENTRY_QTY} lot")

        # Strategy row
        strat = sess.query(Strategy).filter_by(name=STRATEGY_NAME).first()
        if strat is None:
            strat = Strategy(
                id=uuid.uuid4(),
                name=STRATEGY_NAME,
                description=DESCRIPTION,
                is_active=True,
                capital_required="5000.00",
                json_data={"variation": VARIATION, "deployed_via": "deploy_challenge_xau"},
                params={},
                entry_quantity=ENTRY_QTY,
                currencypair_id=cp.id,
            )
            sess.add(strat)
            sess.flush()
            print(f"[NEW] Strategy '{STRATEGY_NAME}' id={strat.id} qty={strat.entry_quantity}")
        else:
            if strat.entry_quantity != ENTRY_QTY:
                print(f"[UPD] Strategy '{STRATEGY_NAME}' entry_quantity "
                      f"{strat.entry_quantity} -> {ENTRY_QTY}")
                strat.entry_quantity = ENTRY_QTY
            else:
                print(f"[SKIP] Strategy '{STRATEGY_NAME}' already present id={strat.id}")

        # UserStrategy row
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
                multiplyer=1,
                deployed=True,
                strategy_id=strat.id,
                user_broker_id=user_broker.id,
            )
            sess.add(us)
            sess.flush()
            print(f"[NEW] UserStrategy id={us.id} deployed=True active=True")
        else:
            changed = False
            if not existing_us.is_active:
                existing_us.is_active = True
                changed = True
            if not existing_us.deployed:
                existing_us.deployed = True
                changed = True
            print(f"[{'UPD' if changed else 'SKIP'}] UserStrategy id={existing_us.id} "
                  f"deployed/active")

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
    raise SystemExit(main(commit="--commit" in sys.argv))
