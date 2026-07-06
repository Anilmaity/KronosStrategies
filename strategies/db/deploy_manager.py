"""
Deploy the Strategy Manager v1 roster into the live Kronos DB.

Design spec: docs/superpowers/specs/2026-07-02-strategy-manager-design.md (§4, §5).

The scalper slot is intentionally EMPTY: S98 ZScore MR M15 was pulled after its
train-set validation FAILED (spec 2026-07-03), and no strategy replaced it. The
quiet_mr policy stays registered but unused; the S98 module/tests remain in-tree.

Inserts (idempotently):
  - apis_strategy + apis_userstrategy rows for the two new child strategies
        "S95 Session Breakout"        (backtest_strategies/s95_session_breakout.py)
        "S96 H1 Momentum"             (backtest_strategies/s96_h1_momentum.py)
    Names MUST match entry_manager._VARIATION_STRATEGY_NAME values.
    UserStrategy rows are seeded deployed=True but **is_active=False** — the
    Strategy Manager starts them when (and only when) the user arms them and
    the gating policy says go. Nothing trades on --commit alone.
  - apis_managedstrategy rows placing each UserStrategy under manager control:
        s95 -> slot=session  policy=session_vol   live_eligible=False
        s96 -> slot=momentum policy=trending      live_eligible=False
        (scalper slot empty -- S98 failed validation, spec 2026-07-03)
        challenge_xau -> slot=trend policy=always_on live_eligible=True
          (only if a deployed UserStrategy for "Challenge XAU H4 Trend" exists;
           its is_active is NOT touched — it keeps trading exactly as today).
    live_eligible stays False for the two new ones until the held-out
    backtest (spec §8) produces a positive test-set expectancy.
  - apis_managerconfig default row (master_mode=OFF, kill $150, max 3 open).

Rollout env knobs (2026-07-06 live-account deployment):
  MANAGER_USER_BROKER_ID  bind everything to this UserBroker (fresh account).
  MANAGER_ARM_MODE        arm_mode for NEWLY created managed rows (OFF|PAPER|LIVE).
  MANAGER_LIVE_ELIGIBLE   "true" -> children live_eligible=True (held-out
                          backtest passed 2026-07-06). CHALLENGE_XAU is created
                          on the resolved broker when absent, so a fresh account
                          gets the full 3-strategy roster (scalper slot empty).
  Pausing is enforced by master_mode=OFF + is_active=False regardless.

Broker binding
--------------
Like deploy_challenge_xau: mirrors an existing XAU_USD UserBroker so the new
strategies trade through an already-configured MetaAPI binding. Resolution
order: MANAGER_USER_BROKER_ID env override -> the deployed UserStrategy of
REFERENCE_STRATEGY_NAME -> any deployed XAU_USD UserStrategy. Verify the
dry-run output before --commit. (The compose services also run DRY_RUN=true,
so no broker order can be placed regardless of the binding.)

Run (from strategies/):
  python -m db.deploy_manager            # dry-run (print plan, no writes)
  python -m db.deploy_manager --commit   # actually write the rows
"""
from __future__ import annotations

import os
import sys
import uuid
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.models import (
    CurrencyPair,
    ManagedStrategy,
    ManagerConfig,
    Session,
    Strategy,
    UserBroker,
    UserStrategy,
)

SYMBOL = "XAU_USD"

# Reference strategy whose UserBroker (live MetaAPI account binding) we mirror.
# Deliberately NOT the FundingPips challenge account.
REFERENCE_STRATEGY_NAME = "ICT Breaker Block (M15)"

# Existing validated strategy adopted under manager control (spec §4 row 1).
CHALLENGE_STRATEGY_NAME = "Challenge XAU H4 Trend"

# Fixed lot per trade for the new children. Small on purpose: these are
# paper/probation strategies.
ENTRY_QTY = Decimal(os.getenv("MANAGER_CHILD_LOT", "0.01"))


def _arm_mode() -> str:
    """arm_mode applied to NEWLY created ManagedStrategy rows only (re-runs
    never touch an existing row's arm). Default OFF; the 2026-07-06 live
    account rollout passes MANAGER_ARM_MODE=LIVE with everything paused via
    master_mode=OFF + is_active=False."""
    mode = os.getenv("MANAGER_ARM_MODE", "OFF").strip().upper() or "OFF"
    if mode not in ("OFF", "PAPER", "LIVE"):
        raise SystemExit(f"MANAGER_ARM_MODE must be OFF|PAPER|LIVE, got '{mode}'")
    return mode


def _live_eligible_override() -> bool:
    """MANAGER_LIVE_ELIGIBLE=true marks the child strategies live-eligible at
    seed time. Justified only once the held-out backtest passed — it did on
    2026-07-06 (optimize_manager_strategies: s95 test WR 72.3%/PF 1.57,
    s96 test WR 79.8%/PF 1.36, challenge test WR 86.2%/PF 1.90)."""
    return os.getenv("MANAGER_LIVE_ELIGIBLE", "").strip().lower() in ("1", "true", "yes")

# (strategy name, variation tag, slot, policy_key, policy_params, description)
# Names must equal entry_manager._VARIATION_STRATEGY_NAME[variation].
# policy_params mirror the spec §4 defaults explicitly so the frontend can
# render them read-only; policies.py falls back to the same values if empty.
# Slot taxonomy simplified 2026-07-06 (user decision): every strategy is one
# of THREE categories — "trend" (breakout/continuation: S95 ORB, S96 H1
# Donchian, Challenge XAU H4), "reversal" (S99 MSS+FVG), "scalping"
# (EMPTY — four validation campaigns failed after costs: S97, S98, M5 z-fade
# sweep, M1 liquidity-sweep sweep). _ensure_managed syncs slot/policy renames
# onto existing rows.
ROSTER = [
    (
        "S95 Session Breakout",
        "KRONOS_S95_SESSION_BREAKOUT",
        "trend",
        "session_vol",
        # Entry windows follow the ORB session hours [1,7,12,13,14] UTC: the OR
        # completes at :30, entries run to the top of the hour (h12-14 merge).
        {"windows": [[1.0, 2.0], [7.0, 8.0], [12.0, 15.0]], "vol_regimes": ["NORMAL", "HIGH"]},
        "Session ORB (delegate of kronos_session_breakout): 30-min OR, sessions "
        "[1,7,12,13,14] UTC, EMA240 bias, SL 2.0xOR, TP 0.8xOR, 180-min time "
        "exit. High-WR geometry validated 2026-07-06. Category: trend "
        "(backtest_strategies/s95_session_breakout.py).",
    ),
    (
        "S96 H1 Momentum",
        "KRONOS_S96_H1_MOMENTUM",
        "trend",
        "trending",
        {},
        "H1 Donchian(24) continuation, EMA20/50 bias, static SL 3xATR(14,H1), "
        "static TP 0.4R (1.2xATR). High-WR geometry validated 2026-07-06. "
        "Category: trend "
        "(backtest_strategies/s96_h1_momentum.py).",
    ),
    (
        "S99 MSS FVG Reversal",
        "KRONOS_S99_MSS_FVG",
        "reversal",
        "always_on",
        {},
        "ICT MSS+FVG reversal (M5): liquidity sweep -> structure shift -> FVG "
        "retrace entry, SL beyond distal edge, TP 1.5R, hours 1-15 UTC. "
        "Validated 2026-07-06 (train PF 1.29 / test PF 1.19, ~7.6 trades/day). "
        "Category: reversal (backtest_strategies/s99_mss_fvg.py).",
    ),
]

# Strategies pulled from the roster: their UserStrategy is de-deployed and the
# ManagedStrategy row deleted so the manager never gates them again. Identified
# by strategy name (this file's canonical lookup key; the variation tag lives
# in Strategy.json_data, not a column). (name, variation) for readable prints.
RETIRED_STRATEGIES = [
    ("S97 Snap Scalper M5 (paper)", "KRONOS_S97_SNAP_SCALPER"),
]


def _resolve_user_broker(sess, cp) -> "UserBroker | None":
    """MANAGER_USER_BROKER_ID override -> reference strategy -> any deployed
    XAU_USD UserStrategy. Returns None if nothing resolves."""
    override = os.getenv("MANAGER_USER_BROKER_ID", "").strip()
    if override:
        ub = sess.query(UserBroker).filter_by(id=override).first()
        if ub is None:
            print(f"FATAL: MANAGER_USER_BROKER_ID={override} not found.")
            return None
        print(f"[OK]  Explicit UserBroker override -> id={ub.id} status={ub.status}")
        return ub

    ref_us = None
    ref_strat = (
        sess.query(Strategy)
        .filter_by(name=REFERENCE_STRATEGY_NAME, currencypair_id=cp.id)
        .first()
    )
    if ref_strat is not None:
        ref_us = (
            sess.query(UserStrategy)
            .filter_by(strategy_id=ref_strat.id, deployed=True)
            .first()
        )
    if ref_us is None:
        print(f"[WARN] no deployed UserStrategy on '{REFERENCE_STRATEGY_NAME}', "
              f"falling back to any deployed XAU_USD UserStrategy.")
        ref_us = (
            sess.query(UserStrategy)
            .join(Strategy, UserStrategy.strategy_id == Strategy.id)
            .filter(Strategy.currencypair_id == cp.id, UserStrategy.deployed == True)  # noqa: E712
            .first()
        )
    if ref_us is None:
        print("FATAL: no deployed UserStrategy on any XAU_USD strategy -- "
              "no UserBroker to bind to.")
        return None
    ub = sess.query(UserBroker).filter_by(id=ref_us.user_broker_id).first()
    if ub is None:
        print(f"FATAL: UserBroker id={ref_us.user_broker_id} not found.")
        return None
    return ub


def _ensure_strategy(sess, cp, name, variation, description) -> Strategy:
    strat = sess.query(Strategy).filter_by(name=name).first()
    if strat is None:
        strat = Strategy(
            id=uuid.uuid4(),
            name=name,
            description=description,
            is_active=True,
            capital_required="5000.00",
            json_data={"variation": variation, "deployed_via": "deploy_manager"},
            params={},
            entry_quantity=ENTRY_QTY,
            currencypair_id=cp.id,
        )
        sess.add(strat)
        sess.flush()
        print(f"[NEW] Strategy '{name}' id={strat.id} qty={strat.entry_quantity}")
    else:
        print(f"[SKIP] Strategy '{name}' already present id={strat.id}")
    return strat


def _ensure_user_strategy(sess, strat, user_broker) -> UserStrategy:
    us = (
        sess.query(UserStrategy)
        .filter_by(strategy_id=strat.id, user_broker_id=user_broker.id)
        .first()
    )
    if us is None:
        us = UserStrategy(
            id=uuid.uuid4(),
            name=f"{strat.name} (managed)",
            is_active=False,   # the manager starts it when armed + policy says go
            multiplyer=1,
            deployed=True,
            strategy_id=strat.id,
            user_broker_id=user_broker.id,
        )
        sess.add(us)
        sess.flush()
        print(f"[NEW] UserStrategy id={us.id} deployed=True active=False (manager-gated)")
    else:
        if not us.deployed:
            us.deployed = True
            print(f"[UPD] UserStrategy id={us.id} deployed -> True")
        else:
            print(f"[SKIP] UserStrategy id={us.id} already deployed")
        # is_active is intentionally left alone on re-runs — it's the
        # manager's (or the operator's) to flip, not this script's.
    return us


def _ensure_managed(sess, us, slot, policy_key, policy_params,
                    live_eligible=False) -> ManagedStrategy:
    m = sess.query(ManagedStrategy).filter_by(user_strategy_id=us.id).first()
    if m is None:
        arm = _arm_mode()
        m = ManagedStrategy(
            id=uuid.uuid4(),
            user_strategy_id=us.id,
            slot=slot,
            policy_key=policy_key,
            policy_params=policy_params,
            arm_mode=arm,              # default OFF; env override for rollouts
            live_eligible=live_eligible,
            desired_active=False,
            last_reason="",
        )
        sess.add(m)
        sess.flush()
        print(f"[NEW] ManagedStrategy slot={slot} policy={policy_key} "
              f"arm={arm} live_eligible={live_eligible}")
    else:
        # Sync category/policy renames onto existing rows (2026-07-06 slot
        # taxonomy: trend | scalping | reversal). arm_mode / live_eligible /
        # desired_active remain the operator's — never touched on re-runs.
        changed = []
        if m.slot != slot:
            changed.append(f"slot {m.slot}->{slot}")
            m.slot = slot
        if m.policy_key != policy_key:
            changed.append(f"policy {m.policy_key}->{policy_key}")
            m.policy_key = policy_key
        if changed:
            print(f"[UPD] ManagedStrategy for UserStrategy {us.id}: "
                  + ", ".join(changed))
        else:
            print(f"[SKIP] ManagedStrategy for UserStrategy {us.id} already present "
                  f"(slot={m.slot} policy={m.policy_key} arm={m.arm_mode})")
    return m


def _ensure_config(sess) -> ManagerConfig:
    cfg = sess.query(ManagerConfig).first()
    if cfg is None:
        cfg = ManagerConfig(
            id=uuid.uuid4(),
            master_mode="OFF",
            kill_switch_loss_usd=Decimal("150.00"),
            max_concurrent_positions=3,
            state={},
        )
        sess.add(cfg)
        sess.flush()
        print("[NEW] ManagerConfig master_mode=OFF kill=-$150 max_open=3")
    else:
        print(f"[SKIP] ManagerConfig already present (master={cfg.master_mode})")
    return cfg


def seed(sess) -> int:
    """Idempotent seeding pass on an open session. Caller owns commit/rollback.
    Returns 0 on success, 1 on a fatal precondition failure."""
    cp = sess.query(CurrencyPair).filter_by(symbol=SYMBOL).first()
    if cp is None:
        print(f"FATAL: CurrencyPair symbol='{SYMBOL}' not found.")
        return 1
    print(f"[OK]  CurrencyPair {SYMBOL} -> id={cp.id}")

    user_broker = _resolve_user_broker(sess, cp)
    if user_broker is None:
        return 1
    print(f"[OK]  Binding to UserBroker={user_broker.id} ({user_broker.status}) "
          f"-- verify this is the intended account.")
    print(f"[OK]  Fixed entry_quantity = {ENTRY_QTY} lot per child strategy")

    # ── Retire strategies pulled from the roster ──────────────────────────────
    # De-deploy the UserStrategy and delete its ManagedStrategy so the manager
    # never gates it again. Lookup by name (this file's canonical key), then
    # UserStrategy by strategy_id and ManagedStrategy by user_strategy_id --
    # exactly the query style of _ensure_user_strategy / _ensure_managed.
    for name, variation in RETIRED_STRATEGIES:
        strat = sess.query(Strategy).filter_by(name=name).first()
        if strat is None:
            print(f"[RETIRE] {variation}: Strategy '{name}' not found (ok)")
            continue
        us_rows = sess.query(UserStrategy).filter_by(strategy_id=strat.id).all()
        if not us_rows:
            print(f"[RETIRE] {variation}: no UserStrategy found (ok)")
            continue
        for us in us_rows:
            m = (sess.query(ManagedStrategy)
                 .filter_by(user_strategy_id=us.id).first())
            if m is not None:
                sess.delete(m)
                print(f"[RETIRE] {variation}: ManagedStrategy removed "
                      f"(UserStrategy {us.id})")
            if us.deployed or us.is_active:
                us.deployed = False
                us.is_active = False
                print(f"[RETIRE] {variation}: UserStrategy {us.id} de-deployed")

    # ── The two new children ──────────────────────────────────────────────────
    child_live_eligible = _live_eligible_override()
    for name, variation, slot, policy_key, policy_params, description in ROSTER:
        strat = _ensure_strategy(sess, cp, name, variation, description)
        us = _ensure_user_strategy(sess, strat, user_broker)
        _ensure_managed(sess, us, slot, policy_key, policy_params,
                        live_eligible=child_live_eligible)

    # ── CHALLENGE_XAU fills the trend slot ────────────────────────────────────
    # Prefer an existing deployed UserStrategy on the RESOLVED broker; adopt an
    # existing one on any broker second (pre-2026-07-06 behaviour); create the
    # Strategy/UserStrategy pair on the resolved broker when nothing exists, so
    # a fresh-account rollout gets the full 3-strategy roster in one pass.
    ch_strat = (
        sess.query(Strategy)
        .filter_by(name=CHALLENGE_STRATEGY_NAME, currencypair_id=cp.id)
        .first()
    )
    if ch_strat is None:
        ch_strat = _ensure_strategy(
            sess, cp, CHALLENGE_STRATEGY_NAME, "CHALLENGE_XAU",
            "H4 Donchian(20) trend-follow, EMA20/50 bias, static SL 4xATR / "
            "static TP 0.4R (1.6xATR). High-WR geometry validated 2026-07-06 "
            "(train WR 81.0%/PF 1.73, test WR 86.2%/PF 1.90).",
        )
    ch_us = (
        sess.query(UserStrategy)
        .filter_by(strategy_id=ch_strat.id, user_broker_id=user_broker.id,
                   deployed=True)
        .first()
    ) or (
        sess.query(UserStrategy)
        .filter_by(strategy_id=ch_strat.id, deployed=True)
        .first()
    )
    if ch_us is not None:
        _ensure_managed(sess, ch_us, "trend", "always_on", {},
                        live_eligible=True)
        print(f"[OK]  '{CHALLENGE_STRATEGY_NAME}' adopted (UserStrategy {ch_us.id}); "
              f"is_active untouched — the manager only acts on it once armed.")
    else:
        ch_us = _ensure_user_strategy(sess, ch_strat, user_broker)
        _ensure_managed(sess, ch_us, "trend", "always_on", {},
                        live_eligible=True)
        print(f"[OK]  '{CHALLENGE_STRATEGY_NAME}' created on the resolved broker "
              f"(UserStrategy {ch_us.id}, is_active=False).")

    # ── Global config ─────────────────────────────────────────────────────────
    _ensure_config(sess)
    return 0


def main(commit: bool) -> int:
    sess = Session()
    try:
        rc = seed(sess)
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
