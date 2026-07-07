"""
manager.py
----------
Kronos Strategy Manager — the regime-aware meta-controller (design spec
2026-07-02). Every MANAGER_LOOP_SEC (default 60s):

  1. Fetch OANDA frames and compute the market regime (regime.regime_engine).
  2. Persist a RegimeSnapshot row (always — even with master OFF).
  3. Load ManagerConfig (auto-created with master_mode=OFF on first run)
     and all ManagedStrategy rows.
  4. master OFF  -> record snapshot + last_evaluated_at only; flip nothing.
     master ON   -> apply global guards, then the per-strategy gating policy,
                    and converge UserStrategy.is_active for ARMED strategies.

Guards (evaluated before policies):
  * market closed          -> all armed strategies desired False
  * daily kill-switch      -> today's summed realized P&L across managed
                              strategies <= -kill_switch_loss_usd pauses all
                              armed strategies until the next UTC day
  * max concurrent guard   -> at/over the open-position cap, no new STARTs
                              (already-running strategies are left running)

Hard rules:
  * arm_mode is USER-owned (backend API). The manager never writes it, and
    when arm_mode == OFF it never touches that UserStrategy at all.
  * ManagerAction rows are written only on transitions (desired flips), never
    per tick — no PAUSE spam while the market stays closed.
  * DRY_RUN=true logs intended is_active flips without writing them;
    snapshots, actions and ManagedStrategy bookkeeping still persist so a
    first deploy is fully observable.

Run:  python manager.py
Env:  MANAGER_LOOP_SEC (60) | SYMBOL (XAU_USD) | DRY_RUN (false)
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, time as dtime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from regime.regime_engine import (
    RegimeSnapshot as RegimeState,  # dataclass, not the DB row
    compute_regime,
    fetch_regime_frames,
)
from shared.models import (
    ManagedStrategy,
    ManagerAction,
    ManagerConfig,
    Position,
    RegimeSnapshot,   # SQLAlchemy row (apis_regimesnapshot)
    Session,
    UserStrategy,
)
from policies import POLICIES

from sqlalchemy import func

# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────

SYMBOL = os.getenv("SYMBOL", "XAU_USD")
LOOP_SEC = int(os.getenv("MANAGER_LOOP_SEC", "60"))
DRY_RUN = os.getenv("DRY_RUN", "false").strip().lower() in ("1", "true", "yes")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("strategy_manager")


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def regime_summary(snap: RegimeState) -> dict:
    """Compact JSON-able copy of the snapshot for ManagerAction.regime."""
    return {
        "symbol": snap.symbol,
        "d1_bias": snap.d1_bias,
        "h4_bias": snap.h4_bias,
        "vol_regime": snap.vol_regime,
        "trend_regime": snap.trend_regime,
        "session": snap.session,
        "market_closed": snap.market_closed,
    }


def _summary_str(snap: RegimeState) -> str:
    return (
        f"d1={snap.d1_bias} h4={snap.h4_bias} vol={snap.vol_regime} "
        f"trend={snap.trend_regime} session={snap.session} closed={snap.market_closed}"
    )


def _write_snapshot(sess, snap: RegimeState) -> None:
    sess.add(
        RegimeSnapshot(
            symbol=snap.symbol,
            d1_bias=snap.d1_bias,
            h4_bias=snap.h4_bias,
            vol_regime=snap.vol_regime,
            trend_regime=snap.trend_regime,
            session=snap.session,
            market_closed=snap.market_closed,
            details=snap.details,
        )
    )


def _get_or_create_config(sess) -> ManagerConfig:
    cfg = sess.query(ManagerConfig).first()
    if cfg is None:
        cfg = ManagerConfig(master_mode="OFF", state={})
        sess.add(cfg)
        sess.flush()
        log.info("[CONFIG] created default ManagerConfig (master_mode=OFF)")
    return cfg


def _action(sess, managed_id, action: str, reason: str, snap: RegimeState) -> None:
    sess.add(
        ManagerAction(
            managed_strategy_id=managed_id,
            action=action,
            reason=reason[:300],
            regime=regime_summary(snap),
        )
    )
    log.info("[ACTION] %s | %s", action, reason)


def _utc_day_start(now_utc: datetime) -> datetime:
    return datetime.combine(now_utc.date(), dtime.min, tzinfo=timezone.utc)


# Position.realized_profit_loss is stored in points x lots (= USD / 100 for
# XAUUSD, where 1.0 lot = 100 oz -> $100/pt). The backend's GraphQL resolvers
# multiply by the same contract factor for display; the manager must too, or
# the USD-denominated kill-switch/soft-brake thresholds are 100x off — found
# live on the FIRST real trade 2026-07-07 (realized -0.38 units == -$38).
_USD_PER_PNL_UNIT = 100.0


def _daily_realized_pnl(sess, us_ids, now_utc: datetime) -> float:
    """Sum of realized P&L in USD across managed strategies for the current
    UTC day.

    Positions realize P&L when closed (position_monitor stamps modified_at),
    so `modified_at >= today 00:00 UTC` captures today's closes; still-open
    positions contribute their (zero) realized_profit_loss harmlessly.
    """
    if not us_ids:
        return 0.0
    total = (
        sess.query(func.coalesce(func.sum(Position.realized_profit_loss), 0))
        .filter(
            Position.user_strategy_id.in_(us_ids),
            Position.modified_at >= _utc_day_start(now_utc),
        )
        .scalar()
    )
    return float(total or 0) * _USD_PER_PNL_UNIT


def _open_managed_positions(sess, us_ids) -> int:
    if not us_ids:
        return 0
    return int(
        sess.query(func.count(Position.id))
        .filter(Position.user_strategy_id.in_(us_ids), Position.quantity > 0)
        .scalar()
        or 0
    )


# ──────────────────────────────────────────────────────────────────────────────
# Core tick (pure w.r.t. wall clock & network — testable with any Session)
# ──────────────────────────────────────────────────────────────────────────────

def evaluate_tick(sess, snap: RegimeState, now_utc: datetime, dry_run: bool = False) -> None:
    """One manager evaluation pass. Caller owns commit/rollback."""
    _write_snapshot(sess, snap)
    cfg = _get_or_create_config(sess)
    managed = sess.query(ManagedStrategy).all()

    if cfg.master_mode != "ON":
        for m in managed:
            m.last_evaluated_at = now_utc
        log.info(
            "[TICK] master=OFF | snapshot recorded, nothing flipped | %s",
            _summary_str(snap),
        )
        return

    armed = [m for m in managed if (m.arm_mode or "OFF") != "OFF"]
    managed_us_ids = [m.user_strategy_id for m in managed]

    # ── Kill-switch state (auto-reset on the next UTC day) ──────────────────
    today = now_utc.date().isoformat()
    state = dict(cfg.state or {})
    tripped_date = state.get("kill_tripped_date")
    if tripped_date and tripped_date != today:
        state.pop("kill_tripped_date", None)
        cfg.state = state
        _action(sess, None, "INFO", f"kill-switch auto-reset (tripped {tripped_date})", snap)
        tripped_date = None
    kill_active = tripped_date == today

    daily_pnl = _daily_realized_pnl(sess, managed_us_ids, now_utc)
    if not kill_active and daily_pnl <= -float(cfg.kill_switch_loss_usd):
        state["kill_tripped_date"] = today
        cfg.state = state
        kill_active = True
        _action(
            sess, None, "KILL_SWITCH",
            f"daily realized P&L {daily_pnl:.2f} USD <= -{float(cfg.kill_switch_loss_usd):.2f} "
            f"— pausing all armed strategies until next UTC day",
            snap,
        )

    # ── Soft daily brake (Phase-1 redesign 2026-07-06) ───────────────────────
    # At/below -soft_brake_usd the manager stops STARTING strategies but lets
    # already-running ones finish their open positions; the hard kill above
    # remains the ceiling. Configured via ManagerConfig.state["soft_brake_usd"]
    # (0/absent = disabled). Auto-resets with the UTC day like the daily P&L.
    soft_brake = float((cfg.state or {}).get("soft_brake_usd") or 0)
    soft_active = soft_brake > 0 and daily_pnl <= -soft_brake

    open_count = _open_managed_positions(sess, managed_us_ids)
    cap = int(cfg.max_concurrent_positions or 0)

    log.info(
        "[TICK] master=ON armed=%d/%d open_pos=%d/%d pnl_today=%.2f kill=%s "
        "soft_brake=%s | %s",
        len(armed), len(managed), open_count, cap, daily_pnl, kill_active,
        soft_active, _summary_str(snap),
    )

    # ── Per-strategy evaluation & apply ──────────────────────────────────────
    for m in managed:
        m.last_evaluated_at = now_utc
        if (m.arm_mode or "OFF") == "OFF":
            continue   # user has not armed it — never touch its UserStrategy

        us = sess.get(UserStrategy, m.user_strategy_id)
        currently_active = bool(us.is_active) if us is not None else False

        if snap.market_closed:
            desired, reason = False, "market closed"
        elif kill_active:
            desired, reason = False, f"kill-switch active (daily P&L {daily_pnl:.2f} USD)"
        else:
            policy = POLICIES.get(m.policy_key)
            if policy is None:
                desired, reason = False, f"unknown policy_key '{m.policy_key}'"
            else:
                desired, reason = policy(snap, m.policy_params or {}, now_utc)
            # Max-concurrent guard: block new STARTs at/over the cap; running
            # strategies are left running (pausing not required by spec).
            if desired and not currently_active and open_count >= cap:
                desired = False
                reason = f"max concurrent positions reached ({open_count}>={cap})"
            # Soft daily brake: no new STARTs while today's realized P&L sits
            # at/below -soft_brake_usd; running strategies finish their trades.
            if desired and not currently_active and soft_active:
                desired = False
                reason = (f"soft daily brake: P&L {daily_pnl:.2f} <= "
                          f"-{soft_brake:.2f} USD — no new starts today")

        transitioned = desired != bool(m.desired_active)
        if transitioned:
            _action(
                sess, m.id, "START" if desired else "PAUSE",
                f"{m.slot or m.policy_key}: {reason} | {_summary_str(snap)}",
                snap,
            )

        if us is not None and currently_active != desired:
            if dry_run:
                log.info(
                    "[DRY_RUN] would set UserStrategy(%s).is_active=%s (%s)",
                    m.user_strategy_id, desired, reason,
                )
            else:
                us.is_active = desired
                log.info(
                    "[APPLY] UserStrategy(%s).is_active=%s (%s)",
                    m.user_strategy_id, desired, reason,
                )

        m.desired_active = desired
        m.last_reason = reason[:300]


# ──────────────────────────────────────────────────────────────────────────────
# Main loop
# ──────────────────────────────────────────────────────────────────────────────

def run() -> None:
    log.info(
        "=== Strategy Manager started | symbol=%s loop=%ds dry_run=%s ===",
        SYMBOL, LOOP_SEC, DRY_RUN,
    )
    while True:
        started = time.time()
        try:
            now_utc = datetime.now(timezone.utc)
            frames = fetch_regime_frames(SYMBOL)
            snap = compute_regime(frames, now_utc, symbol=SYMBOL)

            sess = Session()
            try:
                evaluate_tick(sess, snap, now_utc, dry_run=DRY_RUN)
                sess.commit()
            except Exception:
                sess.rollback()
                log.exception("[TICK ERROR] rolled back")
            finally:
                sess.close()
        except KeyboardInterrupt:
            log.info("=== Strategy Manager stopped by user ===")
            break
        except Exception:
            log.exception("[FATAL] unexpected error — continuing")

        try:
            time.sleep(max(1.0, LOOP_SEC - (time.time() - started)))
        except KeyboardInterrupt:
            log.info("=== Strategy Manager stopped by user ===")
            break


if __name__ == "__main__":
    run()
