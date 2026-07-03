"""
entry_manager.py
----------------
Creates Position, Order, and Trigger records in the Kronos DB (PostgreSQL)
via SQLAlchemy when an ICT entry signal fires.
"""

from __future__ import annotations

import uuid
import logging
from decimal import Decimal

from shared.models import (
    Session,
    Position, Order, Trigger,
    UserStrategy, Strategy, CurrencyPair,
    StrategySignal,
)
from shared.metaapi_client import client_for_broker
from strategy.ict_engine import EntrySignal

log = logging.getLogger(__name__)
SYMBOL = "XAU_USD"


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


def _has_open_position(user_strategy_id: uuid.UUID) -> bool:
    """Return True if there is already an open (quantity > 0) position."""
    return _open_position_count(user_strategy_id) > 0


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

    qty = ctx["quantity"]

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

    sess = Session()
    try:
        # ── 1. Position ───────────────────────────────────────────────────────
        is_long = signal.side == "BUY"
        position = Position(
            id=uuid.uuid4(),
            symbol=symbol,
            avg_buy_price=Decimal(str(signal.entry_price)) if is_long else Decimal("0"),
            avg_sell_price=Decimal("0") if is_long else Decimal(str(signal.entry_price)),
            total_buy_quantity=Decimal(str(qty)) if is_long else Decimal("0"),
            quantity=Decimal(str(qty)),
            ltp=Decimal(str(signal.entry_price)),
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
            price=Decimal(str(signal.entry_price)),
            condition="ENTRY",
            side=signal.side,
            quantity=Decimal(str(qty)),
            amount=Decimal(str(round(signal.entry_price * qty, 2))),
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
            signal.entry_price, signal.stop_loss, signal.take_profit,
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
