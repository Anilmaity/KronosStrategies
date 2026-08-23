"""
Provision the platform rows for the "Neymar VIP" Telegram copy source.

Creates, idempotently (ON CONFLICT (id) DO NOTHING), with FIXED ids that match
the defaults wired into the telegram_trader_vip compose service:

  - apis_strategy         c708a216-5c5f-41b4-a63b-7e13d15ce090  'Neymar VIP'
  - apis_userstrategy     5394830e-20b5-4966-8470-0e8b7de87484  deployed + active
  - apis_managedstrategy  4f9c459a-2fd0-4289-b413-c367496b6019  slot=copy

It does NOT create a UserBroker. The VIP source trades the SAME Winprofx-Demo
account as the rest of the book (UserBroker 43e48e58), by operator decision, so
it reuses that row — its P&L stays attributable through its own Strategy while
its fills land on the existing account.

  >>> READ THIS BEFORE PASSING --arm LIVE <<<
  Measured 2026-08-23 against the full exported channel history: 91% of the free
  @NeymarGoldTrader signals (31 of 34 in the overlapping window) have an
  IDENTICAL twin in this VIP channel — median time offset 0 minutes, median price
  difference 0.00 points. They are the same calls posted to both channels at
  once; VIP simply carries about 3x more of them (108 vs 34 in that window).

  So arming this LIVE while 'Neymar Telegram Copy' is also LIVE makes the account
  take every shared call TWICE, at double size, against ONE shared $150/day
  kill-switch. That is why --arm defaults to OFF. The two sensible configurations
  are (a) arm VIP and retire the free copy, or (b) leave VIP OFF. Running both
  armed is the one combination that is very likely a mistake.

Run inside the telegram_trader container (has psycopg2 + DB_* env):
  docker compose -p kronos run --rm telegram_trader python deploy_neymar_vip.py
  docker compose -p kronos run --rm telegram_trader python deploy_neymar_vip.py --commit
  ... --commit --arm LIVE      # only after reading the note above
"""
from __future__ import annotations

import argparse
import os
import sys

import psycopg2

STRATEGY_ID        = "c708a216-5c5f-41b4-a63b-7e13d15ce090"
USERSTRATEGY_ID    = "5394830e-20b5-4966-8470-0e8b7de87484"
MANAGEDSTRATEGY_ID = "4f9c459a-2fd0-4289-b413-c367496b6019"
STRATEGY_NAME      = "Neymar VIP"

# The existing roster's broker (Winprofx-Demo, meta 3eefc570) and the primary
# Neymar copy, from which owner + currencypair are derived.
WINPROFX_USERBROKER  = "43e48e58-aec5-468e-9fb7-db36a6c846a1"
PRIMARY_USERSTRATEGY = "4a4be335-9606-49cb-9658-4d10cfe064b1"
FALLBACK_CURRENCYPAIR = "9c5fde6d-93b6-4ebf-b84e-65de748ba94a"  # XAU_USD

DESCRIPTION = (
    "Telegram copy-trade of the 'Neymar | VIP' channel (numeric id "
    "-1002776523643), mirrored onto the Winprofx-Demo account by the "
    "telegram_trader_vip listener. Distinct from 'Neymar Telegram Copy', which "
    "reads the free @NeymarGoldTrader channel; the two feeds overlap ~91%.")


def _connect():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("DB_NAME", "tsdb"), user=os.getenv("DB_USER", "tsdbadmin"),
        password=os.getenv("DB_PASSWORD"), sslmode=os.getenv("DB_SSLMODE", "require"),
        connect_timeout=10,
    )


def main(commit: bool, arm: str) -> int:
    conn = _connect()
    conn.autocommit = False
    cur = conn.cursor()

    cur.execute("SELECT currencypair_id FROM apis_strategy s "
                "JOIN apis_userstrategy us ON us.strategy_id = s.id WHERE us.id = %s",
                (PRIMARY_USERSTRATEGY,))
    row = cur.fetchone()
    currencypair_id = str(row[0]) if row and row[0] else FALLBACK_CURRENCYPAIR
    print(f"currencypair_id = {currencypair_id} (XAU_USD)")

    cur.execute("SELECT id FROM apis_userbroker WHERE id = %s", (WINPROFX_USERBROKER,))
    if not cur.fetchone():
        print(f"ABORT: UserBroker {WINPROFX_USERBROKER} (Winprofx-Demo) not found. "
              f"This script reuses the existing account and will not create one.")
        conn.rollback(); cur.close(); conn.close()
        return 2
    print(f"user_broker_id  = {WINPROFX_USERBROKER} (Winprofx-Demo, reused)")

    cur.execute(
        """
        INSERT INTO apis_strategy
            (id, created_at, modified_at, name, description, is_active,
             capital_required, json_data, params, entry_quantity, currencypair_id)
        VALUES (%s, NOW(), NOW(), %s, %s, true, '100000.00',
                '{"variation": "NEYMAR_VIP"}', '{}', 0.01, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        (STRATEGY_ID, STRATEGY_NAME, DESCRIPTION, currencypair_id))
    print(f"apis_strategy        {STRATEGY_ID}  '{STRATEGY_NAME}'  rows+={cur.rowcount}")

    cur.execute(
        """
        INSERT INTO apis_userstrategy
            (id, created_at, modified_at, name, is_active, multiplyer, deployed,
             archived, strategy_id, user_broker_id)
        VALUES (%s, NOW(), NOW(), %s, true, 1, true, false, %s, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        (USERSTRATEGY_ID, STRATEGY_NAME, STRATEGY_ID, WINPROFX_USERBROKER))
    print(f"apis_userstrategy    {USERSTRATEGY_ID}  deployed=true  rows+={cur.rowcount}")

    cur.execute(
        """
        INSERT INTO apis_managedstrategy
            (id, created_at, modified_at, slot, policy_key, policy_params,
             arm_mode, live_eligible, desired_active, last_reason, user_strategy_id)
        VALUES (%s, NOW(), NOW(), 'copy', 'always_on', '{}', %s, true, false,
                'seeded by deploy_neymar_vip', %s)
        ON CONFLICT (id) DO NOTHING
        """,
        (MANAGEDSTRATEGY_ID, arm, USERSTRATEGY_ID))
    print(f"apis_managedstrategy {MANAGEDSTRATEGY_ID}  slot=copy arm={arm}  rows+={cur.rowcount}")

    # Re-runs never change an existing row's arm — flipping a live strategy on or
    # off is an operator action through the dashboard, not a side effect of
    # re-running a deploy script.
    if arm != "OFF":
        cur.execute("SELECT arm_mode FROM apis_managedstrategy WHERE id = %s",
                    (MANAGEDSTRATEGY_ID,))
        got = cur.fetchone()
        if got and got[0] != arm:
            print(f"NOTE: existing managed row already has arm_mode={got[0]}; "
                  f"not changed to {arm}. Flip it from the dashboard.")

    if commit:
        conn.commit()
        print(f"\nCOMMITTED — '{STRATEGY_NAME}' is on the platform with arm_mode={arm}.")
        if arm == "LIVE":
            print("WARNING: armed LIVE. If 'Neymar Telegram Copy' is also LIVE, the "
                  "account will take every shared call twice — see the module docstring.")
    else:
        conn.rollback()
        print("\nDRY-RUN (no writes). Re-run with --commit to persist.")
    cur.close(); conn.close()
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--commit", action="store_true", help="write (default: dry-run)")
    p.add_argument("--arm", default="OFF", choices=["OFF", "PAPER", "LIVE"],
                   help="arm_mode for the NEW managed row (default OFF — read the docstring)")
    a = p.parse_args()
    raise SystemExit(main(a.commit, a.arm))
