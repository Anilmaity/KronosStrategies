"""
entry_manager.py
----------------
Creates Position, Order, and Trigger records in the Kronos DB (PostgreSQL)
via SQLAlchemy when an ICT entry signal fires.
"""

from __future__ import annotations

import os
import uuid
import logging
from datetime import datetime, time as dtime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func

from shared.models import (
    Session,
    Position, Order, Trigger,
    UserStrategy, Strategy, CurrencyPair,
    StrategySignal, ManagedStrategy, ManagerConfig,
)
from shared.metaapi_client import client_for_broker
from shared.tsdb_reader import fetch_latest_ltp
from strategy.ict_engine import EntrySignal

log = logging.getLogger(__name__)
SYMBOL = "XAU_USD"

# ── Risk-normalized sizing (Phase-1 manager redesign, 2026-07-06) ─────────────
# When RISK_PER_TRADE_USD is set (>0), lot size derives from the signal's stop
# distance so every loser costs ~the same dollars regardless of which strategy
# fired (an H4 trend stop no longer risks 4x an ORB stop). Fixed
# Strategy.entry_quantity x multiplyer remains the behaviour when unset.
RISK_PER_TRADE_USD = float(os.getenv("RISK_PER_TRADE_USD", "0") or 0)
MIN_LOT = 0.01
MAX_LOT = float(os.getenv("MAX_LOT", "0.20"))
# If even the minimum lot risks more than this multiple of the budget, the
# trade is rejected (a freak-wide stop must not blow the daily brake solo).
_MIN_LOT_RISK_TOLERANCE = 1.5
# $ per point per 1.0 lot. XAUUSD: 1 lot = 100 oz -> $100/pt. Extend this map
# when XAG_USD / BTC_USD join the roster (Phase 2) — contract sizes differ.
_USD_PER_PT_PER_LOT = {"XAU_USD": 100.0}

# Never stack risk onto a losing open position: a new entry in the SAME symbol
# and direction is rejected while an open position there is underwater.
NO_ADD_TO_LOSER = os.getenv("NO_ADD_TO_LOSER", "true").strip().lower() \
    not in ("false", "0", "no")

# ── Entry-quality gates (2026-07-11, post double-kill-switch RCA) ─────────────
# The 2026-07-09/10 kill-switch days traced mostly to execution artifacts:
# market fills drifting past the signal level (mean +0.67pt adverse, max +6pt),
# stops tighter than live friction, sibling strategies doubling the same setup,
# and entries walked into the 12:30 UTC US-data candle. Each gate below rejects
# the signal with an auditable StrategySignal.rejection_reason.
#
# UTC windows "HH:MM-HH:MM[,HH:MM-HH:MM...]"; no new entries inside them.
NEWS_BLACKOUT_UTC = os.getenv("NEWS_BLACKOUT_UTC", "12:25-12:45")
# Stops tighter than live round-trip friction (~1.5pt spread+slippage+fees on
# XAUUSD) are negative-EV regardless of direction.
MIN_SL_DIST_PTS = float(os.getenv("MIN_SL_DIST_PTS", "1.5"))
# Reject when the market already ran past the signal level by more than
# min(MAX_ENTRY_DRIFT_PTS, MAX_ENTRY_DRIFT_FRAC x stop distance): the backtest
# fills AT the level, so chasing beyond that is unmodelled risk.
MAX_ENTRY_DRIFT_PTS = float(os.getenv("MAX_ENTRY_DRIFT_PTS", "0.5"))
MAX_ENTRY_DRIFT_FRAC = float(os.getenv("MAX_ENTRY_DRIFT_FRAC", "0.25"))
# Cross-strategy duplicate guard: an open same-broker+symbol+side position
# created within DUP_GUARD_MIN minutes whose entry sits within
# DUP_GUARD_PROX_PTS is the same setup fired by a sibling (S93/S99 both trade
# FVG retraces and routinely emit identical levels; no_add_to_loser only
# catches the second one once the first is already underwater). 0 disables.
DUP_GUARD_MIN = float(os.getenv("DUP_GUARD_MIN", "15"))
DUP_GUARD_PROX_PTS = float(os.getenv("DUP_GUARD_PROX_PTS", "2.0"))
# LIVE-armed managed strategies also respect the manager's soft daily brake at
# ENTRY time — the manager itself only gates STARTs, so an already-running
# always_on child would otherwise trade straight through it.
SOFT_BRAKE_AT_ENTRY = os.getenv("SOFT_BRAKE_AT_ENTRY", "true").strip().lower() \
    not in ("false", "0", "no")


def _parse_utc_windows(spec: str) -> list[tuple[dtime, dtime]]:
    """'12:25-12:45,13:55-14:05' -> [(time(12,25), time(12,45)), ...].
    Malformed parts are logged and skipped, never fatal."""
    wins: list[tuple[dtime, dtime]] = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            a, b = part.split("-")
            ah, am = a.split(":")
            bh, bm = b.split(":")
            wins.append((dtime(int(ah), int(am)), dtime(int(bh), int(bm))))
        except ValueError:
            log.warning("[GATE] bad blackout window %r — ignored", part)
    return wins


_BLACKOUT_WINDOWS = _parse_utc_windows(NEWS_BLACKOUT_UTC)


def _in_news_blackout(now_utc: datetime) -> bool:
    t = now_utc.time()
    return any(a <= t <= b for a, b in _BLACKOUT_WINDOWS)


def _drift_budget_pts(entry_price: float, stop_loss: float | None) -> float:
    """Max tolerated adverse move past the signal level before entry."""
    budget = MAX_ENTRY_DRIFT_PTS
    if stop_loss is not None:
        sl_dist = abs(float(entry_price) - float(stop_loss))
        if sl_dist > 0:
            budget = min(budget, MAX_ENTRY_DRIFT_FRAC * sl_dist)
    return budget


def _entry_drift_exceeded(side: str, entry_price: float, stop_loss: float | None,
                          symbol: str) -> tuple[bool, str]:
    """(exceeded, detail). Positive drift = the market already ran in the trade
    direction (a BUY costs more / a SELL sells for less than modelled).
    Measured live 2026-07-08..10 this cost ~$65/day. Fails OPEN on feed errors
    so a price-feed hiccup can't halt trading."""
    ltp = fetch_latest_ltp(symbol)
    if ltp is None:
        log.warning("[GATE] no live price for drift check (%s) — allowing entry", symbol)
        return False, "no_ltp"
    drift = (float(ltp) - float(entry_price)) if side == "BUY" \
        else (float(entry_price) - float(ltp))
    budget = _drift_budget_pts(entry_price, stop_loss)
    detail = f"drift {drift:+.2f}pt vs budget {budget:.2f}pt (ltp {float(ltp):.2f})"
    return drift > budget, detail


def _duplicate_open_same_side(user_broker_id, symbol: str, side: str,
                              entry_price: float) -> bool:
    """True when the account already holds an OPEN same-side position in
    `symbol` opened within DUP_GUARD_MIN minutes and DUP_GUARD_PROX_PTS of this
    signal's entry — a sibling strategy just took the same setup."""
    if DUP_GUARD_MIN <= 0:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=DUP_GUARD_MIN)
    sess = Session()
    try:
        rows = (
            sess.query(Position)
            .join(UserStrategy, Position.user_strategy_id == UserStrategy.id)
            .filter(
                UserStrategy.user_broker_id == user_broker_id,
                Position.symbol == symbol,
                Position.quantity > 0,
            )
            .all()
        )
        for p in rows:
            # Age filter in Python: created_at is tz-aware from Postgres but
            # the column default is Kolkata wall-time — normalise defensively.
            created = p.created_at
            if created is None:
                continue
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created < cutoff:
                continue
            p_side = "BUY" if float(p.avg_buy_price or 0) > 0 else "SELL"
            if p_side != side:
                continue
            p_entry = float(p.avg_buy_price if p_side == "BUY" else p.avg_sell_price)
            if abs(p_entry - float(entry_price)) <= DUP_GUARD_PROX_PTS:
                return True
        return False
    finally:
        sess.close()


# Position P&L storage = points x lots (USD / 100 for XAUUSD). Keep in sync
# with strategy_manager.manager._USD_PER_PNL_UNIT and the backend resolvers.
_USD_PER_PNL_UNIT = 100.0


def _todays_realized_usd(sess, us_ids) -> float:
    """Realized P&L (USD) for the current UTC day across `us_ids`, attributed
    by EXIT time (the closing Order's created_at) — NOT Position.modified_at,
    which fill_reconciler re-touches for days after a close (the leak behind
    the 2026-07-09 false kill-switch trip: -225 'today' vs a true -168)."""
    if not us_ids:
        return 0.0
    day_start = datetime.combine(datetime.now(timezone.utc).date(),
                                 dtime.min, tzinfo=timezone.utc)
    pos_ids = [r[0] for r in
               sess.query(Order.position_id)
               .filter(Order.condition != "ENTRY",
                       Order.created_at >= day_start).all() if r[0]]
    if not pos_ids:
        return 0.0
    total = (
        sess.query(func.coalesce(func.sum(Position.realized_profit_loss), 0))
        .filter(Position.user_strategy_id.in_(list(us_ids)),
                Position.id.in_(pos_ids))
        .scalar()
    )
    return float(total or 0) * _USD_PER_PNL_UNIT


def _managed_soft_brake(user_strategy_id) -> tuple[bool, str]:
    """(blocked, detail). No NEW entries for a LIVE-armed managed strategy
    while today's realized P&L across the LIVE-armed roster sits at/under
    -soft_brake_usd (ManagerConfig.state). Open positions are untouched —
    this only stops adding fresh risk, mirroring the manager's soft-brake
    semantics at the one layer the manager can't reach."""
    if not SOFT_BRAKE_AT_ENTRY:
        return False, ""
    sess = Session()
    try:
        mine = (sess.query(ManagedStrategy)
                .filter_by(user_strategy_id=user_strategy_id).first())
        if mine is None or (mine.arm_mode or "OFF") != "LIVE":
            return False, ""
        cfg = sess.query(ManagerConfig).first()
        if cfg is None:
            return False, ""
        soft = float((cfg.state or {}).get("soft_brake_usd") or 0)
        if soft <= 0:
            return False, ""
        live_us_ids = [m.user_strategy_id
                       for m in sess.query(ManagedStrategy).all()
                       if (m.arm_mode or "OFF") == "LIVE"]
        pnl = _todays_realized_usd(sess, live_us_ids)
        if pnl <= -soft:
            return True, f"daily P&L {pnl:+.2f} USD <= -{soft:.0f} soft brake"
        return False, ""
    finally:
        sess.close()


def _risk_sized_qty(entry_price: float, stop_loss: float | None,
                    symbol: str, default_qty: float) -> tuple[float | None, str]:
    """(lots, reason). lots=None -> reject (reason says why). Falls back to
    default_qty when risk sizing is disabled or the signal has no stop."""
    if RISK_PER_TRADE_USD <= 0 or stop_loss is None:
        return default_qty, "fixed"
    sl_dist = abs(float(entry_price) - float(stop_loss))
    if sl_dist <= 0:
        return default_qty, "fixed (degenerate stop)"
    usd_pp = _USD_PER_PT_PER_LOT.get(symbol, 100.0)
    min_lot_risk = MIN_LOT * sl_dist * usd_pp
    if min_lot_risk > RISK_PER_TRADE_USD * _MIN_LOT_RISK_TOLERANCE:
        return None, (f"risk_too_wide: min lot risks {min_lot_risk:.0f} USD "
                      f"> {_MIN_LOT_RISK_TOLERANCE}x budget {RISK_PER_TRADE_USD:.0f}")
    raw = RISK_PER_TRADE_USD / (sl_dist * usd_pp)
    lots = max(MIN_LOT, min(MAX_LOT, int(raw / MIN_LOT) * MIN_LOT))
    return round(lots, 2), f"risk {RISK_PER_TRADE_USD:.0f} USD / {sl_dist:.2f} pts"


def _losing_open_same_side(user_broker_id, symbol: str, side: str) -> bool:
    """True when the account already holds an open position in `symbol` on the
    same side with negative unrealized P&L (position_monitor keeps
    Position.profit_loss current every second)."""
    sess = Session()
    try:
        rows = (
            sess.query(Position)
            .join(UserStrategy, Position.user_strategy_id == UserStrategy.id)
            .filter(
                UserStrategy.user_broker_id == user_broker_id,
                Position.symbol == symbol,
                Position.quantity > 0,
                Position.profit_loss < 0,
            )
            .all()
        )
        for p in rows:
            p_side = "BUY" if float(p.avg_buy_price or 0) > 0 else "SELL"
            if p_side == side:
                return True
        return False
    finally:
        sess.close()


# Variation tag → DB Strategy.name. Runners pass `variation="VAR2"` etc; we use
# this to select the correct Strategy row (each variation has its own
# entry_quantity and its own deployed UserStrategy).
_VARIATION_STRATEGY_NAME = {
    # VAR1 (Liquidity Scalper) retired 2026-05-18 — unprofitable in 16mo backtest.
    # VAR2 (Liquidity Sweep) retired 2026-05-19 — UserStrategy removed from DB.
    "VAR3":           "Micro Scalper VAR3",
    # ICT_S2_FVG retired 2026-05-19 — UserStrategy removed from DB.
    "ICT_S4_BREAKER": "ICT Breaker Block (M15)",
    # ICT_S6_DAILY_CRT retired 2026-05-18 — 71 trades over 16mo, sample too small.
    # Research-strategy variations (backtest_strategies/sNN_*.py). Name = NAME
    # constant in the source module; description tracks the file.
    "OB_MIT":      "Research OB_MIT",
    "BB":          "Research BB",
    "FVG_MID":     "Research FVG_MID",
    "M90_FADE":    "Research M90_FADE",
    "M90_BIAS":    "Research M90_BIAS",
    "OB_MIT_BIAS": "Research OB_MIT_BIAS",
    # Concept-strategy variations (concept_strategies/cNN_*.py).
    "C03_FVG_FILL": "Concept C03_FVG_FILL",
    # Kronos v3 port (XAU/USD scalping suite) with v15e production gates --
    # TNX |z_20d| >= 0.5 + event-window E3 (+/-2h major events).
    # SH (momo short) was dropped from deployment per v15e
    # (anti-predictive depth + DD penalty).
    "KRONOS_S02_STOCH":      "Kronos S02 Stoch Revert",
    "KRONOS_S05_THREEBAR":   "Kronos S05 Threebar Pull",
    "KRONOS_S06_SWEEP":      "Kronos S06 Session Sweep",
    "KRONOS_S07_CRT":        "Kronos S07 CRT",
    "KRONOS_S14_M5_STRETCH": "Kronos S14 M5 EMA Stretch",
    # Combined Suite v2 — the whole two-sided XAU/USD scalping book as ONE
    # strategy (multi-leg, concurrent positions via max_concurrent_positions,
    # per-leg time-exit via Signal.max_hold_min). Ported from TradingSkills
    # backtest/combined_suite_v2.py.
    "KRONOS_COMBINED_V2":    "Kronos Combined Suite v2",
    # CHALLENGE_XAU — H4 Donchian trend-follow with a chandelier TRAILING stop
    # (backtest_strategies/kronos_challenge_xau.py). The deployable answer to the
    # $5000->$5500 FundingPips challenge research. First strategy to use the
    # Signal.trailing exit (TRAILING_STOPLOSS_POINTS trigger, ratcheted in
    # position_monitor).
    "CHALLENGE_XAU":         "Challenge XAU H4 Trend",
    # Strategy Manager v1 children (backtest_strategies/s9N_*.py; design spec
    # docs/superpowers/specs/2026-07-02-strategy-manager-design.md §4).
    # S97 (KRONOS_S97_SNAP_SCALPER) was retired from the roster 2026-07-03.
    # Its intended replacement S98 (M15 z-score MR) FAILED train validation
    # (spec 2026-07-03) and was never shipped -- the scalper slot is empty.
    # Both name mappings are kept for historical Position/Order rows and
    # inert wiring only; deploy_manager de-deploys S97 so no new signals route.
        # Live M5 ORB deployed directly on the box (pre-manager lineage).
    "SESSION_BREAKOUT": "Session Breakout M5 ORB",
    "KRONOS_S95_SESSION_BREAKOUT": "S95 Session Breakout",
    "KRONOS_S96_H1_MOMENTUM":      "S96 H1 Momentum",
    "KRONOS_S97_SNAP_SCALPER":     "S97 Snap Scalper M5 (paper)",
    "KRONOS_S98_ZSCORE_MR":        "S98 ZScore MR M15 (paper)",
    # Reversal category (2026-07-06): ICT MSS+FVG — the first strategy to pass
    # held-out validation outside the trend family (train PF 1.29 / test 1.19).
    "KRONOS_S99_MSS_FVG":          "S99 MSS FVG Reversal",
    # Scalping category (2026-07-06): FVG continuation scalp — first scalp to
    # pass validation after five failed campaigns (train PF 1.30 / test 1.24).
    "KRONOS_S93_FVG_SCALP":        "S93 FVG Scalp",
    # Trend category (2026-07-07): liquidity-sweep reversal riding the
    # distribution leg; full-year OOS-validated (static exits PF 1.82,
    # 12/13 months positive — ClaudeTradingRD/validate_oos.py).
    "KRONOS_S94_SWEEP_REVERSAL":   "S94 Sweep Reversal",
    # Scalping category (2026-07-23): M3 combo (FVG+OB edge+RSI3-momentum,
    # EMA20/200 gate, TP 2.5R) — 3y-validated spec v3, deployed PAUSED
    # (arm OFF, paper) pending live-fill measurement.
    "KRONOS_S100_M3_COMBO":        "S100 M3 Combo Scalper",
}


# ──────────────────────────────────────────────────────────────────────────────
# Context lookup
# ──────────────────────────────────────────────────────────────────────────────

def _get_context(symbol: str = SYMBOL, variation: str | None = None) -> dict | None:
    """
    Return the active trading context for the given symbol + variation.
    Finds: UserStrategy (deployed + active) → Strategy → CurrencyPair.
    Returns dict with user_strategy_id, user_broker_id, currency_pair_id, quantity.
    """
    sess = Session()
    try:
        cp = sess.query(CurrencyPair).filter_by(symbol=symbol).first()
        if not cp:
            log.warning("[CTX] CurrencyPair '%s' not found in DB", symbol)
            return None

        q = sess.query(Strategy).filter_by(currencypair_id=cp.id, is_active=True)
        if variation:
            name = _VARIATION_STRATEGY_NAME.get(variation)
            if not name:
                log.warning("[CTX] Unknown variation tag '%s'", variation)
                return None
            q = q.filter_by(name=name)
        strategy = q.first()
        if not strategy:
            log.warning("[CTX] No active Strategy for symbol='%s' variation='%s'",
                        symbol, variation)
            return None

        us = (
            sess.query(UserStrategy)
            .filter_by(strategy_id=strategy.id, is_active=True, deployed=True)
            .first()
        )
        if not us:
            log.warning("[CTX] No deployed UserStrategy for strategy '%s'", strategy.name)
            return None

        qty = float(strategy.entry_quantity) * int(us.multiplyer)

        return {
            "strategy_id": strategy.id,
            "user_strategy_id": us.id,
            "user_broker_id": us.user_broker_id,
            "currency_pair_id": cp.id,
            "quantity": qty,
        }
    finally:
        sess.close()


def _log_signal_fired(strategy_id, symbol, signal: EntrySignal) -> uuid.UUID | None:
    """Write a StrategySignal(status='FIRED') row and return its id.
    Failures here must not abort entry — logged and swallowed.
    """
    sess = Session()
    try:
        row = StrategySignal(
            id=uuid.uuid4(),
            strategy_id=strategy_id,
            symbol=symbol,
            side=signal.side,
            entry_price=Decimal(str(signal.entry_price)),
            stop_loss=Decimal(str(signal.stop_loss)) if signal.stop_loss is not None else None,
            take_profit=Decimal(str(signal.take_profit)) if signal.take_profit is not None else None,
            reason=signal.reason,
            status="FIRED",
        )
        sess.add(row)
        sess.commit()
        return row.id
    except Exception:
        sess.rollback()
        log.exception("[SIGNAL] Failed to log FIRED signal — continuing")
        return None
    finally:
        sess.close()


def _update_signal_status(signal_log_id, status, *, rejection_reason=None, position_id=None):
    """Update an existing StrategySignal row's status and related fields."""
    if signal_log_id is None:
        return
    sess = Session()
    try:
        row = sess.query(StrategySignal).filter_by(id=signal_log_id).first()
        if row is None:
            return
        row.status = status
        if rejection_reason is not None:
            row.rejection_reason = rejection_reason[:500]
        if position_id is not None:
            row.position_id = position_id
        sess.commit()
    except Exception:
        sess.rollback()
        log.exception("[SIGNAL] Failed to update signal status — continuing")
    finally:
        sess.close()


def _open_position_count(user_strategy_id: uuid.UUID) -> int:
    """Number of currently-open (quantity > 0) positions for this UserStrategy."""
    sess = Session()
    try:
        return (
            sess.query(Position)
            .filter(
                Position.user_strategy_id == user_strategy_id,
                Position.quantity > 0,
            )
            .count()
        )
    finally:
        sess.close()


# ──────────────────────────────────────────────────────────────────────────────
# Entry placement
# ──────────────────────────────────────────────────────────────────────────────

def place_entry(signal: EntrySignal, symbol: str = SYMBOL, variation: str | None = None,
                max_concurrent: int = 1) -> bool:
    """
    Persist an entry into the DB:
      1. Position record
      2. ENTRY Order
      3. STOPLOSS Trigger
      4. TARGET Trigger
      5. (optional) TIME_EXIT trigger when signal.max_hold_min is set

    `variation` selects which Strategy row (and therefore which UserStrategy /
    entry_quantity) the trade belongs to. Required when multiple strategies
    share a symbol — otherwise the first active strategy for the symbol wins.

    `max_concurrent` caps how many positions this UserStrategy may hold open at
    once (default 1 = legacy single-position behaviour). A portfolio strategy
    passes its CONFIG.max_concurrent_positions so several legs run concurrently.

    Returns True on success, False if skipped or failed.
    """
    ctx = _get_context(symbol, variation=variation)
    if not ctx:
        # No strategy_id known -> can't log; the misconfiguration is the bug.
        return False

    # Persist the signal as FIRED before anything else can fail.
    signal_log_id = _log_signal_fired(ctx["strategy_id"], symbol, signal)

    open_n = _open_position_count(ctx["user_strategy_id"])
    if open_n >= max(1, int(max_concurrent)):
        _update_signal_status(signal_log_id, "REJECTED",
                              rejection_reason="open_position_cap")
        log.info("[ENTRY] Concurrency cap reached (%d/%d open) — skipping new entry",
                 open_n, max_concurrent)
        return False

    # ── No-add-to-loser guard (Phase-1 redesign) ──────────────────────────────
    if NO_ADD_TO_LOSER and _losing_open_same_side(
            ctx["user_broker_id"], symbol, signal.side):
        _update_signal_status(signal_log_id, "REJECTED",
                              rejection_reason="no_add_to_loser")
        log.info("[ENTRY] blocked: open %s position on %s is underwater — "
                 "not adding to a loser", signal.side, symbol)
        return False

    # ── Entry-quality gates (post 2026-07-09/10 kill-switch RCA) ─────────────
    if _in_news_blackout(datetime.now(timezone.utc)):
        _update_signal_status(signal_log_id, "REJECTED",
                              rejection_reason="news_blackout")
        log.info("[ENTRY] blocked: news blackout window (%s UTC)",
                 NEWS_BLACKOUT_UTC)
        return False

    sl_dist = (abs(float(signal.entry_price) - float(signal.stop_loss))
               if signal.stop_loss is not None else None)
    if sl_dist is not None and 0 < sl_dist < MIN_SL_DIST_PTS:
        _update_signal_status(
            signal_log_id, "REJECTED",
            rejection_reason=f"sl_too_tight: {sl_dist:.2f}pt < {MIN_SL_DIST_PTS}pt")
        log.info("[ENTRY] blocked: stop %.2fpt < %.2fpt friction floor",
                 sl_dist, MIN_SL_DIST_PTS)
        return False

    if _duplicate_open_same_side(ctx["user_broker_id"], symbol,
                                 signal.side, signal.entry_price):
        _update_signal_status(signal_log_id, "REJECTED",
                              rejection_reason="duplicate_entry")
        log.info("[ENTRY] blocked: sibling strategy already holds this setup "
                 "(same side within %.0f min / %.1f pt)",
                 DUP_GUARD_MIN, DUP_GUARD_PROX_PTS)
        return False

    brake_hit, brake_detail = _managed_soft_brake(ctx["user_strategy_id"])
    if brake_hit:
        _update_signal_status(signal_log_id, "REJECTED",
                              rejection_reason=f"soft_daily_brake: {brake_detail}"[:200])
        log.info("[ENTRY] blocked: %s", brake_detail)
        return False

    drift_bad, drift_detail = _entry_drift_exceeded(
        signal.side, signal.entry_price, signal.stop_loss, symbol)
    if drift_bad:
        _update_signal_status(signal_log_id, "REJECTED",
                              rejection_reason=f"entry_drift: {drift_detail}"[:200])
        log.info("[ENTRY] blocked: %s", drift_detail)
        return False

    # ── Risk-normalized sizing (Phase-1 redesign) ─────────────────────────────
    qty, sizing_reason = _risk_sized_qty(
        signal.entry_price, signal.stop_loss, symbol, ctx["quantity"])
    if qty is None:
        _update_signal_status(signal_log_id, "REJECTED",
                              rejection_reason=sizing_reason[:200])
        log.info("[ENTRY] rejected by risk sizing: %s", sizing_reason)
        return False
    if sizing_reason != "fixed":
        log.info("[SIZING] %s -> qty=%.2f lots", sizing_reason, qty)

    # Fire MetaAPI market order FIRST. If broker rejects, we don't pollute the
    # DB with phantom positions. On success we record the broker positionId on
    # the Order so position_monitor / reconciliation can correlate.
    # Per-account: open on the deployed account's own MetaAPI creds. Refuse
    # (never fall back to the env account) if the account has no usable creds.
    _sess = Session()
    try:
        _client = client_for_broker(_sess, ctx["user_broker_id"])
    finally:
        _sess.close()
    if _client is None:
        _update_signal_status(signal_log_id, "REJECTED",
                              rejection_reason="no_account_credentials")
        log.warning("[ENTRY] account %s has no usable MetaAPI creds — refusing to open",
                    ctx["user_broker_id"])
        return False
    broker_position_id = _client.place_market_order(
        side=signal.side,
        symbol=symbol,
        volume=qty,
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit,
        entry_price=signal.entry_price,
    )
    if not broker_position_id:
        _update_signal_status(signal_log_id, "REJECTED",
                              rejection_reason="metaapi_rejection")
        log.warning("[ENTRY] MetaAPI rejected order — skipping DB write")
        return False

    # ── Book at BROKER TRUTH (2026-07-07): fetch the real fill immediately
    # (market fills land <1s). Falls back to the signal price on dry-run /
    # timeout; fill_reconciler trues it up later either way. SL/TP trigger
    # LEVELS stay signal-based; StrategySignal keeps the signal price audit.
    fill_px = _client.get_position_fill(broker_position_id)
    entry_px = float(fill_px) if fill_px else float(signal.entry_price)
    if fill_px and abs(entry_px - float(signal.entry_price)) > 0.005:
        log.info("[ENTRY] booked at broker fill %.2f (signal %.2f, drift %+.2f)",
                 entry_px, float(signal.entry_price),
                 entry_px - float(signal.entry_price))

    sess = Session()
    try:
        # ── 1. Position ───────────────────────────────────────────────────────
        is_long = signal.side == "BUY"
        position = Position(
            id=uuid.uuid4(),
            symbol=symbol,
            avg_buy_price=Decimal(str(entry_px)) if is_long else Decimal("0"),
            avg_sell_price=Decimal("0") if is_long else Decimal(str(entry_px)),
            total_buy_quantity=Decimal(str(qty)) if is_long else Decimal("0"),
            quantity=Decimal(str(qty)),
            ltp=Decimal(str(entry_px)),
            profit_loss=Decimal("0"),
            profit_loss_percentage=Decimal("0"),
            realized_profit_loss=Decimal("0"),
            user_strategy_id=ctx["user_strategy_id"],
            currencypair_id=ctx["currency_pair_id"],
        )
        sess.add(position)
        sess.flush()  # populate position.id before FK references

        # ── 2. Entry Order ────────────────────────────────────────────────────
        entry_order = Order(
            id=uuid.uuid4(),
            symbol=symbol,
            price=Decimal(str(entry_px)),          # broker fill (signal px lives on StrategySignal)
            condition="ENTRY",
            side=signal.side,
            quantity=Decimal(str(qty)),
            amount=Decimal(str(round(entry_px * qty, 2))),
            order_type="MARKET",
            status="EXECUTED",
            reason=signal.reason,
            broker_order_id=str(broker_position_id),
            position_id=position.id,
            user_broker_id=ctx["user_broker_id"],
        )
        sess.add(entry_order)

        # ── 3. SL Trigger ─────────────────────────────────────────────────────
        # LONG: SL fires when price <= sl  (greater_than=False)
        # SHORT: SL fires when price >= sl (greater_than=True)
        close_side = "SELL" if is_long else "BUY"
        is_trailing = bool(getattr(signal, "trailing", False))

        if is_trailing:
            # ── 3+4. TRAIL Trigger (chandelier trailing stop) ─────────────────
            # Replaces the static SL + fixed TP for a trend-follow leg. The
            # trigger starts at signal.stop_loss (== entry -/+ k*ATR) and carries
            # the trail DISTANCE in trail_points; position_monitor ratchets
            # trigger_price off the high/low-water mark each tick, then fires on
            # the usual price-cross and actively closes the broker position (the
            # ratcheted level has no broker-side equivalent). The broker still
            # holds the initial stop_loss (and the far take_profit) as an offline
            # backstop. No TARGET trigger: the right tail is never capped.
            trail_distance = abs(float(signal.entry_price) - float(signal.stop_loss))
            trail_trigger = Trigger(
                id=uuid.uuid4(),
                symbol=symbol,
                trigger_price=Decimal(str(signal.stop_loss)),   # initial stop level
                order_type="MARKET",
                side=close_side,
                greater_than=not is_long,    # LONG fires on price<=stop; SHORT on price>=stop
                quantity=Decimal(str(qty)),
                trigger_type="TRAILING_STOPLOSS_POINTS",
                trail_points=Decimal(str(round(trail_distance, 2))),
                status="PENDING",
                position_id=position.id,
            )
            sess.add(trail_trigger)
        else:
            # ── 3. SL Trigger ─────────────────────────────────────────────────
            sl_trigger = Trigger(
                id=uuid.uuid4(),
                symbol=symbol,
                trigger_price=Decimal(str(signal.stop_loss)),
                order_type="MARKET",
                side=close_side,
                greater_than=not is_long,
                quantity=Decimal(str(qty)),
                trigger_type="STOPLOSS",
                status="PENDING",
                position_id=position.id,
            )
            sess.add(sl_trigger)

            # ── 4. TP Trigger ─────────────────────────────────────────────────
            # LONG: TP fires when price >= tp (greater_than=True)
            # SHORT: TP fires when price <= tp (greater_than=False)
            tp_trigger = Trigger(
                id=uuid.uuid4(),
                symbol=symbol,
                trigger_price=Decimal(str(signal.take_profit)),
                order_type="MARKET",
                side=close_side,
                greater_than=is_long,
                quantity=Decimal(str(qty)),
                trigger_type="TARGET",
                status="PENDING",
                position_id=position.id,
            )
            sess.add(tp_trigger)

        # ── 5. TIME_EXIT Trigger (optional per-leg max-hold) ──────────────────
        # The Trigger.trigger_type enum has no TIMEOUT value, so we reuse CUSTOM
        # and tag it order_type="TIME_EXIT". trigger_price holds the absolute
        # UNIX expiry epoch (seconds) — NOT a price. position_monitor must
        # special-case TIME_EXIT (fire on wall-clock), never on price.
        max_hold_min = getattr(signal, "max_hold_min", None)
        if max_hold_min:
            import time as _time
            expiry_epoch = _time.time() + float(max_hold_min) * 60.0
            time_trigger = Trigger(
                id=uuid.uuid4(),
                symbol=symbol,
                trigger_price=Decimal(str(round(expiry_epoch, 2))),  # epoch, not price
                order_type="TIME_EXIT",
                side=close_side,
                greater_than=False,        # unused for TIME_EXIT; monitor ignores it
                quantity=Decimal(str(qty)),
                trigger_type="CUSTOM",
                status="PENDING",
                position_id=position.id,
            )
            sess.add(time_trigger)

        sess.commit()

        _update_signal_status(signal_log_id, "PLACED", position_id=position.id)

        log.info(
            "[ENTRY] %s %s qty=%.2f @ %.2f | SL=%.2f TP=%.2f | %s | pos_id=%s",
            signal.side, symbol, qty,
            entry_px, signal.stop_loss, signal.take_profit,
            signal.reason, position.id,
        )
        return True

    except Exception as e:
        sess.rollback()
        log.exception("[ENTRY] Failed to persist entry signal")
        _update_signal_status(signal_log_id, "REJECTED",
                              rejection_reason=f"db_error: {type(e).__name__}: {e}")
        return False
    finally:
        sess.close()
