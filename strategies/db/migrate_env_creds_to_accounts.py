"""One-time: copy the env MetaAPI creds into designated accounts (encrypted).

Reads META_ACCOUNT_ID + META_API_TOKEN + FIELD_ENCRYPTION_KEY + DB_* from env,
encrypts the token, and writes meta_account_id / meta_api_token_enc onto the
named UserBroker rows. Dry-run by default; --commit to write. Explicit target
ids only — never a blanket update. Idempotent.

Run (from strategies/):
  python -m db.migrate_env_creds_to_accounts <broker-id> [<broker-id> ...]
  python -m db.migrate_env_creds_to_accounts <broker-id> --commit
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.crypto import encrypt_token  # noqa: E402  (DB-free)


def build_cred_payload(account_id: str, token: str) -> dict:
    """Pure: the column values to set for one account. Encrypts the token."""
    return {
        "meta_account_id": account_id,
        "meta_api_token_enc": encrypt_token(token),
    }


def main(target_ids: list[str], commit: bool) -> int:
    account_id = os.getenv("META_ACCOUNT_ID", "")
    token = os.getenv("META_API_TOKEN", "")
    if not account_id or not token:
        print("[ERR] META_ACCOUNT_ID / META_API_TOKEN not set")
        return 2
    if not os.getenv("FIELD_ENCRYPTION_KEY", ""):
        print("[ERR] FIELD_ENCRYPTION_KEY not set")
        return 2
    if not target_ids:
        print("[ERR] no target UserBroker ids given")
        return 2

    from shared.models import Session, UserBroker  # lazy (needs DB)

    payload = build_cred_payload(account_id, token)
    last4 = token[-4:]
    sess = Session()
    updated = missing = 0
    try:
        for bid in target_ids:
            broker = sess.query(UserBroker).filter_by(id=bid).first()
            if broker is None:
                print(f"[MISS] UserBroker {bid} not found")
                missing += 1
                continue
            print(
                f"[SET ] {bid}: meta_account_id={account_id} "
                f"token=<{len(payload['meta_api_token_enc'])} bytes, last4 {last4}>"
            )
            if commit:
                broker.meta_account_id = payload["meta_account_id"]
                broker.meta_api_token_enc = payload["meta_api_token_enc"]
                updated += 1
        if commit:
            sess.commit()
            print(f"[DONE] committed {updated} updated, {missing} missing")
        else:
            print(f"[DRY ] would update {len(target_ids) - missing}, {missing} missing "
                  f"(re-run with --commit to apply)")
    finally:
        sess.close()
    return 1 if missing else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("broker_ids", nargs="+")
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()
    raise SystemExit(main(args.broker_ids, args.commit))
