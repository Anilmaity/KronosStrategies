"""DELETE the four heavy live strategies from the DB (operator decision
2026-06-24): Combined Suite v2 + the two research-bias strategies + the C03
concept. Removes the apis_strategy rows AND everything that hangs off them, in
explicit child -> parent order (works whether or not the DB FK constraints
cascade):

    Order, Trigger        (children of Position)
    StrategySignal        (by strategy_id; also FK-> Position via SET NULL)
    Position              (children of UserStrategy)
    Signal, Action, BacktestReport, UserStrategy   (children of Strategy)
    Strategy

Does NOT touch UserBroker — C03 shares its broker (e673869c) with ICT FVG Fill /
Breaker / VAR3, which must keep working.

DRY-RUN by default: it performs the deletes inside the transaction (proving they
succeed and printing the blast radius) and then ROLLS BACK — nothing is written.
Re-run with --commit to persist.

SAFETY: refuses if any target strategy still has an OPEN position (PENDING
trigger). Re-run after flattening/closing those if it aborts.

After --commit, on the host: stop + remove the four containers and drop their
services from compose.yml (already removed in VCS — redeploy/`docker compose up`
will not recreate them):
    docker compose stop kronos_combined_v2 research_m90_bias research_ob_mit_bias concept_c03_fvg_fill
    docker compose rm -f kronos_combined_v2 research_m90_bias research_ob_mit_bias concept_c03_fvg_fill

Run:
  cd C:\\Projects\\PycharmProjects\\personal\\KronosStrategies\\strategies
  python -m db.delete_heavy_strategies            # dry-run (deletes+rollback)
  python -m db.delete_heavy_strategies --commit   # persist the deletes
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.models import (  # noqa: E402
    Session, Strategy, UserStrategy, Position, Order, Trigger,
    Signal, StrategySignal, Action, BacktestReport,
)

# DB Strategy.name for each (variation NAME -> _VARIATION_STRATEGY_NAME):
#   KRONOS_COMBINED_V2 -> "Kronos Combined Suite v2"  (kronos_combined_v2)
#   M90_BIAS           -> "Research M90_BIAS"         (research_m90_bias)
#   OB_MIT_BIAS        -> "Research OB_MIT_BIAS"       (research_ob_mit_bias)
#   C03_FVG_FILL       -> "Concept C03_FVG_FILL"       (concept_c03_fvg_fill)
STRATEGY_NAMES = [
    "Kronos Combined Suite v2",
    "Research M90_BIAS",
    "Research OB_MIT_BIAS",
    "Concept C03_FVG_FILL",
]


def _del(q):
    """Bulk-delete a query, bypassing ORM-level cascade; return rows affected."""
    return q.delete(synchronize_session=False)


def main(commit: bool) -> int:
    sess = Session()
    try:
        # Safety re-check: refuse if any strategy has an OPEN position (PENDING trigger).
        blocking = []
        for name in STRATEGY_NAMES:
            s = sess.query(Strategy).filter_by(name=name).first()
            if s is None:
                continue
            us_ids = [u.id for u in sess.query(UserStrategy).filter_by(strategy_id=s.id)]
            if not us_ids:
                continue
            pos_ids = [p.id for p in sess.query(Position)
                       .filter(Position.user_strategy_id.in_(us_ids))]
            if not pos_ids:
                continue
            open_n = (sess.query(Trigger)
                      .filter(Trigger.position_id.in_(pos_ids),
                              Trigger.status == "PENDING").count())
            if open_n:
                blocking.append((name, open_n))
        if blocking:
            print("ABORT: open positions (PENDING triggers) found — refusing to delete:")
            for name, n in blocking:
                print(f"  {name}: {n} PENDING trigger(s)")
            print("Flatten/close those positions, then re-run.")
            return 1

        totals = {k: 0 for k in
                  ("ord", "trig", "ssig", "pos", "sig", "act", "rpt", "us", "strat")}
        for name in STRATEGY_NAMES:
            s = sess.query(Strategy).filter_by(name=name).first()
            if s is None:
                print(f"[MISS] {name!r}: Strategy not found — skipping")
                continue
            us_ids = [u.id for u in sess.query(UserStrategy).filter_by(strategy_id=s.id)]
            pos_ids = ([p.id for p in sess.query(Position)
                        .filter(Position.user_strategy_id.in_(us_ids))]
                       if us_ids else [])

            n_ord = _del(sess.query(Order).filter(Order.position_id.in_(pos_ids))) if pos_ids else 0
            n_trig = _del(sess.query(Trigger).filter(Trigger.position_id.in_(pos_ids))) if pos_ids else 0
            n_ssig = _del(sess.query(StrategySignal).filter_by(strategy_id=s.id))
            n_pos = _del(sess.query(Position).filter(Position.user_strategy_id.in_(us_ids))) if us_ids else 0
            n_sig = _del(sess.query(Signal).filter_by(strategy_id=s.id))
            n_act = _del(sess.query(Action).filter_by(strategy_id=s.id))
            n_rpt = _del(sess.query(BacktestReport).filter_by(strategy_id=s.id))
            n_us = _del(sess.query(UserStrategy).filter_by(strategy_id=s.id))
            n_strat = _del(sess.query(Strategy).filter_by(id=s.id))

            for k, v in (("ord", n_ord), ("trig", n_trig), ("ssig", n_ssig),
                         ("pos", n_pos), ("sig", n_sig), ("act", n_act),
                         ("rpt", n_rpt), ("us", n_us), ("strat", n_strat)):
                totals[k] += v
            print(f"[DEL] {name!r}: strat={n_strat} us={n_us} pos={n_pos} ord={n_ord} "
                  f"trig={n_trig} ssig={n_ssig} sig={n_sig} act={n_act} rpt={n_rpt}")

        print(f"\nTOTAL deleted: {totals}")
        if commit:
            sess.commit()
            print("\nCOMMITTED — 4 heavy strategies deleted.")
            print("Next: stop + remove their containers on the host:")
            print("  docker compose stop kronos_combined_v2 research_m90_bias "
                  "research_ob_mit_bias concept_c03_fvg_fill")
            print("  docker compose rm -f kronos_combined_v2 research_m90_bias "
                  "research_ob_mit_bias concept_c03_fvg_fill")
        else:
            sess.rollback()
            print("\nDRY-RUN: deletes executed inside the transaction then ROLLED BACK "
                  "(nothing written). Re-run with --commit to persist.")
        return 0

    except Exception as e:
        sess.rollback()
        print(f"FATAL: {type(e).__name__}: {e}")
        raise
    finally:
        sess.close()


if __name__ == "__main__":
    raise SystemExit(main(commit="--commit" in sys.argv))
