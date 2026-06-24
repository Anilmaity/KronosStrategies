"""
Provision the platform (apis_*) rows so the SECOND copy-trade account shows on the
Kronos dashboard as its own strategy ('Neymar Telegram Copy (Account 2)') with its
own position / order history.

Creates, idempotently (ON CONFLICT (id) DO NOTHING), with FIXED ids that match the
defaults wired into live_trader.py / apis_persist.py:
  - apis_userbroker  45b0c6d8-90ed-4996-bbfd-a92a951966bb  (account c9bf3b9d…)
  - apis_strategy    30427449-9705-406c-820d-2b5ff9d8c003  'Neymar Telegram Copy (Account 2)'
  - apis_userstrategy 31c5b1cf-8a25-4f5a-983f-2207cceae4b8  deployed + active

Owner user_id and the XAU_USD currencypair_id are read from the existing primary
'Neymar Telegram Copy' rows (falling back to the confirmed constants). Connects via
the same DB_* env apis_persist uses (the Lightsail app DB), NOT TIGERDATA_URL.

Run inside the telegram_trader container (has psycopg2 + DB_* env):
  docker compose run --rm telegram_trader python deploy_dashboard_account2.py            # dry-run
  docker compose run --rm telegram_trader python deploy_dashboard_account2.py --commit   # write
"""
from __future__ import annotations

import os
import sys

import psycopg2

STRATEGY_ID     = "30427449-9705-406c-820d-2b5ff9d8c003"
USERSTRATEGY_ID = "31c5b1cf-8a25-4f5a-983f-2207cceae4b8"
USERBROKER_ID   = "45b0c6d8-90ed-4996-bbfd-a92a951966bb"
STRATEGY_NAME   = "Neymar Telegram Copy (Account 2)"
META_ACCOUNT_2  = "c9bf3b9d-b773-4c93-b387-af79f7a83f66"

# Confirmed fallbacks (read live first, then default to these).
PRIMARY_USERSTRATEGY = "4a4be335-9606-49cb-9658-4d10cfe064b1"
FALLBACK_USER_ID        = "2617fed2-9835-4f91-9d76-6452a4e21824"
FALLBACK_CURRENCYPAIR   = "9c5fde6d-93b6-4ebf-b84e-65de748ba94a"  # XAU_USD (Strategy.currencypair_id)


def _connect():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("DB_NAME", "tsdb"), user=os.getenv("DB_USER", "tsdbadmin"),
        password=os.getenv("DB_PASSWORD"), sslmode=os.getenv("DB_SSLMODE", "require"),
        connect_timeout=10,
    )


def main(commit: bool) -> int:
    conn = _connect()
    conn.autocommit = False
    cur = conn.cursor()

    # Derive owner + currencypair from the existing primary strategy.
    cur.execute(
        "SELECT us.user_broker_id, s.currencypair_id "
        "FROM apis_userstrategy us JOIN apis_strategy s ON s.id = us.strategy_id "
        "WHERE us.id = %s", (PRIMARY_USERSTRATEGY,))
    row = cur.fetchone()
    currencypair_id = FALLBACK_CURRENCYPAIR
    user_id = FALLBACK_USER_ID
    if row:
        currencypair_id = str(row[1]) or FALLBACK_CURRENCYPAIR
        cur.execute("SELECT user_id FROM apis_userbroker WHERE id = %s", (str(row[0]),))
        ub = cur.fetchone()
        if ub:
            user_id = str(ub[0])
    print(f"owner user_id   = {user_id}")
    print(f"currencypair_id = {currencypair_id} (XAU_USD)")

    cur.execute(
        """
        INSERT INTO apis_userbroker
            (id, created_at, modified_at, api_key, margin_available, margin_used,
             status, is_active, last_updated, user_id)
        VALUES (%s, NOW(), NOW(), %s, '', '0.00', 'ACTIVE', true, NOW(), %s)
        ON CONFLICT (id) DO NOTHING
        """,
        (USERBROKER_ID, f"neymar2-{META_ACCOUNT_2}", user_id))
    print(f"apis_userbroker  {USERBROKER_ID}  rows+={cur.rowcount}")

    cur.execute(
        """
        INSERT INTO apis_strategy
            (id, created_at, modified_at, name, description, is_active,
             capital_required, json_data, params, entry_quantity, currencypair_id)
        VALUES (%s, NOW(), NOW(), %s, %s, true, '100000.00', '{}', '{}', 0.01, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        (STRATEGY_ID, STRATEGY_NAME,
         "Telegram copy-trade of @NeymarGoldTrader, mirrored onto the 2nd MetaAPI "
         "account (c9bf3b9d) via single-listener fan-out.", currencypair_id))
    print(f"apis_strategy    {STRATEGY_ID}  '{STRATEGY_NAME}'  rows+={cur.rowcount}")

    cur.execute(
        """
        INSERT INTO apis_userstrategy
            (id, modified_at, name, is_active, created_at, multiplyer, deployed,
             strategy_id, user_broker_id)
        VALUES (%s, NOW(), %s, true, NOW(), 1, true, %s, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        (USERSTRATEGY_ID, STRATEGY_NAME, STRATEGY_ID, USERBROKER_ID))
    print(f"apis_userstrategy {USERSTRATEGY_ID}  deployed=true active=true  rows+={cur.rowcount}")

    if commit:
        conn.commit()
        print("\nCOMMITTED — account 2 now appears as its own strategy on the platform.")
    else:
        conn.rollback()
        print("\nDRY-RUN (no writes). Re-run with --commit to persist.")
    cur.close(); conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(commit="--commit" in sys.argv))
