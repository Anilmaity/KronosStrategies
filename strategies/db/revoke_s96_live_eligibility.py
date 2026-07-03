"""
Revoke S96's live-eligibility after the 2026-07-03 in-place logic rewrite.

The momentum child ("S96 H1 Momentum") was granted live_eligible=True on
2026-07-02 on the strength of the H1 Donchian backtest (test PF 1.445).
On 2026-07-03 its logic was rewritten in place to a pure M5 EMA9/21
crossover (spec: docs/superpowers/specs/2026-07-03-s96-m5-ema-cross-design.md),
which voids that verdict. deploy_manager.seed() SKIPs existing
ManagedStrategy rows, so this one-off script does the flip:

  - ManagedStrategy.live_eligible -> False for every managed row bound to
    the "S96 H1 Momentum" strategy (arm_mode and desired_active untouched);
  - Strategy.description refreshed to describe the M5 EMA cross logic.

Idempotent; second runs print SKIPs. Re-grant live_eligible only after the
new logic passes a held-out backtest.

Run (from strategies/):
  python -m db.revoke_s96_live_eligibility            # dry-run (no writes)
  python -m db.revoke_s96_live_eligibility --commit   # actually write
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.models import ManagedStrategy, Session, Strategy, UserStrategy

STRATEGY_NAME = "S96 H1 Momentum"

# Keep in sync with the deploy_manager.ROSTER S96 description.
NEW_DESCRIPTION = (
    "Pure M5 EMA9/21 crossover momentum: cross event on the last closed "
    "M5 bar, either direction, 1.5xATR(14,M5) chandelier trailing stop, "
    "480-min backstop. Managed slot: momentum "
    "(backtest_strategies/s96_h1_momentum.py)."
)


def revoke(sess) -> int:
    """Idempotent revoke pass on an open session. Caller owns commit/rollback.
    Returns 0 on success (including nothing-to-do)."""
    strat = sess.query(Strategy).filter_by(name=STRATEGY_NAME).first()
    if strat is None:
        print(f"[SKIP] Strategy '{STRATEGY_NAME}' not found -- nothing to revoke.")
        return 0

    if strat.description != NEW_DESCRIPTION:
        strat.description = NEW_DESCRIPTION
        print("[UPD] Strategy.description refreshed to the M5 EMA cross text")
    else:
        print("[SKIP] Strategy.description already current")

    flipped = 0
    for us in sess.query(UserStrategy).filter_by(strategy_id=strat.id).all():
        m = sess.query(ManagedStrategy).filter_by(user_strategy_id=us.id).first()
        if m is None:
            continue
        if m.live_eligible:
            m.live_eligible = False
            flipped += 1
            print(f"[UPD] ManagedStrategy {m.id} (slot={m.slot}) "
                  f"live_eligible -> False (H1 Donchian verdict void)")
        else:
            print(f"[SKIP] ManagedStrategy {m.id} live_eligible already False")
    if flipped == 0:
        print("[OK]  no live_eligible flags needed flipping")
    return 0


def main(commit: bool) -> int:
    sess = Session()
    try:
        rc = revoke(sess)
        if rc != 0:
            sess.rollback()
            return rc
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
