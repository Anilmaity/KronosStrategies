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
from shared.tsdb_reader import fetch_latest_ltp, fetch_latest_spread
from shared.event_gate import event_window_open
from shared.gate_rules import (
    parse_utc_windows, in_news_blackout as _shared_in_blackout, sl_too_tight,
    MIN_SL_DIST_PTS, NEWS_BLACKOUT_UTC,
)
from shared import obs
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
# Stops tighter than live round-trip friction (~1.5pt spread+slippage+fees on
# XAUUSD) are negative-EV regardless of direction.
# NEWS_BLACKOUT_UTC / MIN_SL_DIST_PTS are single-sourced from shared.gate_rules
# (same env var names/defaults, imported above) so the offline manager sim can
# never model a different gate than live even if the box overrides the env
# (2026-08 fidelity fix).
# Reject when the market already ran past the signal level by more than
# min(MAX_ENTRY_DRIFT_PTS, MAX_ENTRY_DRIFT_FRAC x stop distance): the backtest
# fills AT the level, so chasing beyond that is unmodelled risk.
MAX_ENTRY_DRIFT_PTS = float(os.getenv("MAX_ENTRY_DRIFT_PTS", "0.5"))
MAX_ENTRY_DRIFT_FRAC = float(os.getenv("MAX_ENTRY_DRIFT_FRAC", "0.25"))
# Fail mode for the drift gate when NO live price is available to check against:
#   "open"   (default) - allow the entry through, the current behaviour (a
#            price-feed hiccup must never halt trading).
#   "closed" - reject the entry with rejection_reason="entry_drift_noprice" so
#            an unpriced fill is never opened. Opt-in per opt15 task4 (Global
#            Constraint 1: a new trading-behaviour gate ships default-OFF).
ENTRY_DRIFT_FAIL_MODE = os.getenv("ENTRY_DRIFT_FAIL_MODE", "open").strip().lower()
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


def _flag_on(name: str) -> bool:
    """Env flag with default-OFF semantics (opt15 Global Constraint 1: every new
    trading-behaviour gate ships default-OFF). On only for on/true/1/yes."""
    return os.getenv(name, "off").strip().lower() in ("on", "true", "1", "yes")


# ── Market gates (2026-07-30, opt15 task5) — all DEFAULT OFF ──────────────────
# Three additional entry gates slotted after the static news blackout and before
# the broker call. Each is env-gated (default off) and records a distinct
# StrategySignal.rejection_reason. With none set, place_entry behaves exactly as
# the pre-task5 chain (no DB/network consult happens).
#
# 1) Correlation budget: reject a NEW entry when a DIFFERENT UserStrategy on the
#    SAME account already holds a same-side position whose entry sits within
#    CORR_GUARD_POINTS. _duplicate_open_same_side guards the account within a
#    time window; this is the cross-strategy price-proximity budget with no
#    window (a portfolio must not stack correlated risk across sibling books).
CORR_GUARD = _flag_on("CORR_GUARD")
CORR_GUARD_POINTS = float(os.getenv("CORR_GUARD_POINTS", "2.0"))
# 2) Event-gate news window: consult shared.event_gate.event_window_open (a
#    hard-coded 2025-2026 major-macro calendar, fail-open). Complements the
#    static NEWS_BLACKOUT_UTC clock window — both can be active at once.
NEWS_EVENT_GATE = _flag_on("NEWS_EVENT_GATE")
# 3) Spread gate: reject when the live ask-bid spread exceeds SPREAD_GATE_MAX_FRAC
#    of the stop distance (a fill paying >1/4 of the stop in spread is negative-EV
#    the same way a too-tight stop is). Missing spread data fails OPEN with one
#    WARN — a data hiccup must never halt trading.
SPREAD_GATE = _flag_on("SPREAD_GATE")
SPREAD_GATE_MAX_FRAC = float(os.getenv("SPREAD_GATE_MAX_FRAC", "0.25"))


# Backward-compat alias: tests/test_entry_gates.py calls this private name
# directly (em._parse_utc_windows(...)). Kept so that suite stays green
# unchanged; the real implementation now lives in shared.gate_rules.
_parse_utc_windows = parse_utc_windows

_BLACKOUT_WINDOWS = parse_utc_windows(NEWS_BLACKOUT_UTC)


def _in_news_blackout(now_utc) -> bool:
    return _shared_in_blackout(now_utc, _BLACKOUT_WINDOWS)


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
    Measured live 2026-07-08..10 this cost ~$65/day. Defaults to failing OPEN on
    feed errors so a price-feed hiccup can't halt trading; set
    ENTRY_DRIFT_FAIL_MODE=closed to reject an unpriced entry instead."""
    ltp = fetch_latest_ltp(symbol)
    if ltp is None:
        if ENTRY_DRIFT_FAIL_MODE == "closed":
            log.warning("[GATE] no live price for drift check (%s) - fail-closed, "
                        "rejecting entry", symbol)
            return True, "entry_drift_noprice"
        log.warning("[GATE] no live price for drift check (%s) — allowing entry", symbol)
        return False, "no_ltp"
    drift = (float(ltp) - float(entry_price)) if side == "BUY" \
        else (float(entry_price) - float(ltp))
    budget = _drift_budget_pts(entry_price, stop_loss)
    detail = f"drift {drift:+.2f}pt vs budget {budget:.2f}pt (ltp {float(ltp):.2f})"
    return drift > budget, detail


def _duplicate_open_same_side(user_broker_id, symbol: str, side: str,
                              entry_price: float, sess=None) -> bool:
    """True when the account already holds an OPEN same-side position in
    `symbol` opened within DUP_GUARD_MIN minutes and DUP_GUARD_PROX_PTS of this
    signal's entry — a sibling strategy just took the same setup.

    `sess` (opt15 task4): reuse place_entry's consolidated session when threaded
    in; otherwise open a short own session."""
    if DUP_GUARD_MIN <= 0:
        return False
    # opt15 task4: the age filter now runs IN SQL (created_at >= cutoff) instead
    # of pulling every open position and filtering in Python. `cutoff` is
    # computed IDENTICALLY to the old Python filter — an aware UTC instant
    # (now_utc - DUP_GUARD_MIN). created_at is stored -5:30 (naive IST) and the
    # prior code compared it likewise; this deliberately preserves the same
    # (uncorrected) timezone semantics rather than "fixing" them.
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=DUP_GUARD_MIN)
    own = sess is None
    if own:
        sess = Session()
    try:
        rows = (
            sess.query(Position)
            .join(UserStrategy, Position.user_strategy_id == UserStrategy.id)
            .filter(
                UserStrategy.user_broker_id == user_broker_id,
                Position.symbol == symbol,
                Position.quantity > 0,
                Position.created_at >= cutoff,
            )
            .all()
        )
        for p in rows:
            p_side = "BUY" if float(p.avg_buy_price or 0) > 0 else "SELL"
            if p_side != side:
                continue
            p_entry = float(p.avg_buy_price if p_side == "BUY" else p.avg_sell_price)
            if abs(p_entry - float(entry_price)) <= DUP_GUARD_PROX_PTS:
                return True
        return False
    finally:
        if own:
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


def _managed_soft_brake(user_strategy_id, sess=None) -> tuple[bool, str]:
    """(blocked, detail). No NEW entries for a LIVE-armed managed strategy
    while today's realized P&L across the LIVE-armed roster sits at/under
    -soft_brake_usd (ManagerConfig.state). Open positions are untouched —
    this only stops adding fresh risk, mirroring the manager's soft-brake
    semantics at the one layer the manager can't reach.

    `sess` (opt15 task4): reuse place_entry's consolidated session when threaded
    in; otherwise open a short own session."""
    if not SOFT_BRAKE_AT_ENTRY:
        return False, ""
    own = sess is None
    if own:
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
        if own:
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


def _losing_open_same_side(user_broker_id, symbol: str, side: str,
                           sess=None) -> bool:
    """True when the account already holds an open position in `symbol` on the
    same side with negative unrealized P&L (position_monitor keeps
    Position.profit_loss current every second).

    `sess` (opt15 task4): reuse place_entry's consolidated session when threaded
    in; otherwise open a short own session."""
    own = sess is None
    if own:
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
        if own:
            sess.close()


# ── Market gates (opt15 task5) ────────────────────────────────────────────────

def _correlated_open_other_strategy(user_broker_id, user_strategy_id, symbol: str,
                                    side: str, entry_price: float,
                                    sess=None) -> tuple[bool, str]:
    """(blocked, detail). True when a DIFFERENT UserStrategy on the SAME account
    (`user_broker_id`) holds an OPEN same-side position in `symbol` whose entry
    price is within CORR_GUARD_POINTS of this signal's entry — a cross-strategy
    correlation budget. Opposite-side and far-away positions are ignored, and the
    current UserStrategy is excluded (same-strategy dupes are
    _duplicate_open_same_side's job). DEFAULT OFF (CORR_GUARD): no DB read
    happens when the gate is off.

    `sess` (opt15 task4/5 pattern): reuse place_entry's consolidated session when
    threaded in; otherwise open a short own session."""
    if not CORR_GUARD:
        return False, ""
    own = sess is None
    if own:
        sess = Session()
    try:
        rows = (
            sess.query(Position)
            .join(UserStrategy, Position.user_strategy_id == UserStrategy.id)
            .filter(
                UserStrategy.user_broker_id == user_broker_id,
                UserStrategy.id != user_strategy_id,
                Position.symbol == symbol,
                Position.quantity > 0,
            )
            .all()
        )
        for p in rows:
            p_side = "BUY" if float(p.avg_buy_price or 0) > 0 else "SELL"
            if p_side != side:
                continue
            p_entry = float(p.avg_buy_price if p_side == "BUY" else p.avg_sell_price)
            if abs(p_entry - float(entry_price)) <= CORR_GUARD_POINTS:
                return True, (f"correlated: sibling {p_side} @ {p_entry:.2f} "
                              f"within {CORR_GUARD_POINTS:.1f}pt of {float(entry_price):.2f}")
        return False, ""
    finally:
        if own:
            sess.close()


def _event_gate_blocks(now_utc) -> bool:
    """True when NEWS_EVENT_GATE is on AND a major macro event is within the
    event_gate window of `now_utc`. event_window_open fails OPEN internally (a
    calendar build error returns True), so this gate can only ever ADD
    rejections when explicitly armed. DEFAULT OFF: the calendar is not consulted
    when the gate is off."""
    if not NEWS_EVENT_GATE:
        return False
    return not event_window_open(now_utc)


def _spread_gate_blocks(symbol: str, entry_price: float,
                        stop_loss: float | None) -> tuple[bool, str, float | None]:
    """(blocked, detail, observed_spread). When SPREAD_GATE is on, fetch the live
    ask-bid spread and reject when spread > SPREAD_GATE_MAX_FRAC x |entry - sl|.
    Missing spread data -> fail OPEN with one WARN (never block trading on a data
    hiccup). observed_spread is returned (when fetched) so the caller can stitch
    it into the audit row for later friction analysis. DEFAULT OFF: no spread is
    fetched when the gate is off."""
    if not SPREAD_GATE:
        return False, "", None
    spread = fetch_latest_spread(symbol)
    if spread is None:
        log.warning("[GATE] no live spread for %s -- spread gate fail-open, "
                    "allowing entry", symbol)
        return False, "no_spread", None
    spread = float(spread)
    sl_dist = (abs(float(entry_price) - float(stop_loss))
               if stop_loss is not None else 0.0)
    if sl_dist <= 0:
        # No usable stop distance to scale against — pass through but still
        # record the observed spread.
        return False, "no_sl", spread
    budget = SPREAD_GATE_MAX_FRAC * sl_dist
    detail = (f"spread {spread:.2f}pt vs budget {budget:.2f}pt "
              f"({SPREAD_GATE_MAX_FRAC:.2f} x {sl_dist:.2f}pt stop)")
    return spread > budget, detail, spread


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

def _get_context(symbol: str = SYMBOL, variation: str | None = None,
                 sess=None) -> dict | None:
    """
    Return the active trading context for the given symbol + variation.
    Finds: UserStrategy (deployed + active) → Strategy → CurrencyPair.
    Returns dict with user_strategy_id, user_broker_id, currency_pair_id, quantity.

    `sess` (opt15 task4): reuse place_entry's consolidated session when threaded
    in; otherwise open a short own session.
    """
    own = sess is None
    if own:
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
        if own:
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


def _update_signal_status(signal_log_id, status, *, rejection_reason=None,
                          position_id=None, note=None):
    """Update an existing StrategySignal row's status and related fields.

    `note` (opt15 task5): a short audit fragment appended to the row's `reason`
    (StrategySignal has no dedicated details column). Used to record the observed
    spread-at-entry on a placed trade so future friction analysis has real data.
    """
    if signal_log_id is None:
        return
    # opt15 task12: this is the ONE place every gate outcome is finalized, so it
    # is the single point to count rejections by reason (and placements). Counted
    # here -- before the DB read -- so the metric reflects the decision itself,
    # independent of whether the audit row can be loaded/written. Key on the
    # reason's leading token (before the first ':' / space) so variable detail
    # text does not fragment the counter (e.g. "sl_too_tight: 1.2pt < 1.5pt" ->
    # reject_sl_too_tight).
    if status == "REJECTED" and rejection_reason:
        head = str(rejection_reason).split(":", 1)[0].strip().split()
        key = (head[0][:40] if head else "unknown") or "unknown"
        obs.count("reject_" + key)
    elif status == "PLACED":
        obs.count("entry_placed")
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
        if note:
            base = row.reason or ""
            merged = f"{base} | {note}" if base else str(note)
            row.reason = merged[:500]
        sess.commit()
    except Exception:
        sess.rollback()
        log.exception("[SIGNAL] Failed to update signal status — continuing")
    finally:
        sess.close()


def _open_position_count(user_strategy_id: uuid.UUID, sess=None) -> int:
    """Number of currently-open (quantity > 0) positions for this UserStrategy.

    `sess` (opt15 task4): reuse place_entry's consolidated session when threaded
    in; otherwise open a short own session."""
    own = sess is None
    if own:
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
        if own:
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
    # opt15 task4: ONE session threaded through context -> gates -> persist,
    # committed once at the end of persist. Only the two audit helpers
    # (_log_signal_fired / _update_signal_status) keep their own short
    # sessions so the FIRED/REJECTED row survives a rollback here. Gate
    # ORDER and semantics are unchanged from the pre-opt15 chain.
    sess = Session()
    try:
        ctx = _get_context(symbol, variation=variation, sess=sess)
        if not ctx:
            # No strategy_id known -> can't log; the misconfiguration is the bug.
            return False

        # Persist the signal as FIRED before anything else can fail.
        signal_log_id = _log_signal_fired(ctx["strategy_id"], symbol, signal)

        # opt15 task5: spread-at-entry, recorded into the audit row on a placed
        # trade (None unless the spread gate is armed AND fetches a value).
        observed_spread = None

        open_n = _open_position_count(ctx["user_strategy_id"], sess=sess)
        if open_n >= max(1, int(max_concurrent)):
            _update_signal_status(signal_log_id, "REJECTED",
                                  rejection_reason="open_position_cap")
            log.info("[ENTRY] Concurrency cap reached (%d/%d open) — skipping new entry",
                     open_n, max_concurrent)
            return False

        # ── No-add-to-loser guard (Phase-1 redesign) ──────────────────────────────
        if NO_ADD_TO_LOSER and _losing_open_same_side(
                ctx["user_broker_id"], symbol, signal.side, sess=sess):
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

        # ── Market gate: event-window news (opt15 task5, DEFAULT OFF) ─────────────
        # Sibling of the static blackout: a hard-coded major-macro calendar. Both
        # can be active; either one rejecting is enough.
        if _event_gate_blocks(datetime.now(timezone.utc)):
            _update_signal_status(signal_log_id, "REJECTED",
                                  rejection_reason="news_event")
            log.info("[ENTRY] blocked: within a major macro-event window")
            return False

        sl_dist = (abs(float(signal.entry_price) - float(signal.stop_loss))
                   if signal.stop_loss is not None else None)
        if sl_too_tight(signal.entry_price, signal.stop_loss, MIN_SL_DIST_PTS):
            _update_signal_status(
                signal_log_id, "REJECTED",
                rejection_reason=f"sl_too_tight: {sl_dist:.2f}pt < {MIN_SL_DIST_PTS}pt")
            log.info("[ENTRY] blocked: stop %.2fpt < %.2fpt friction floor",
                     sl_dist, MIN_SL_DIST_PTS)
            return False

        if _duplicate_open_same_side(ctx["user_broker_id"], symbol,
                                     signal.side, signal.entry_price, sess=sess):
            _update_signal_status(signal_log_id, "REJECTED",
                                  rejection_reason="duplicate_entry")
            log.info("[ENTRY] blocked: sibling strategy already holds this setup "
                     "(same side within %.0f min / %.1f pt)",
                     DUP_GUARD_MIN, DUP_GUARD_PROX_PTS)
            return False

        # ── Market gate: correlation budget (opt15 task5, DEFAULT OFF) ────────────
        corr_bad, corr_detail = _correlated_open_other_strategy(
            ctx["user_broker_id"], ctx["user_strategy_id"], symbol,
            signal.side, signal.entry_price, sess=sess)
        if corr_bad:
            _update_signal_status(signal_log_id, "REJECTED",
                                  rejection_reason=corr_detail[:200])
            log.info("[ENTRY] blocked: %s", corr_detail)
            return False

        brake_hit, brake_detail = _managed_soft_brake(ctx["user_strategy_id"], sess=sess)
        if brake_hit:
            _update_signal_status(signal_log_id, "REJECTED",
                                  rejection_reason=f"soft_daily_brake: {brake_detail}"[:200])
            log.info("[ENTRY] blocked: %s", brake_detail)
            return False

        drift_bad, drift_detail = _entry_drift_exceeded(
            signal.side, signal.entry_price, signal.stop_loss, symbol)
        if drift_bad:
            # opt15 task4: a fail-closed no-price rejection carries the exact
            # rejection_reason "entry_drift_noprice"; a normal adverse-drift
            # rejection keeps the descriptive "entry_drift: ..." form.
            drift_reason = (drift_detail if drift_detail == "entry_drift_noprice"
                            else f"entry_drift: {drift_detail}")
            _update_signal_status(signal_log_id, "REJECTED",
                                  rejection_reason=drift_reason[:200])
            log.info("[ENTRY] blocked: %s", drift_detail)
            return False

        # ── Market gate: live spread vs stop distance (opt15 task5, DEFAULT OFF) ──
        # Last gate before the broker: fetches live spread, so it runs only once
        # every cheaper gate has passed. Fails OPEN on missing data. When it does
        # fetch a value the spread is stashed for the audit row regardless of the
        # verdict below (only reached on a pass).
        spread_bad, spread_detail, spread_val = _spread_gate_blocks(
            symbol, signal.entry_price, signal.stop_loss)
        if spread_val is not None:
            observed_spread = spread_val
        if spread_bad:
            _update_signal_status(signal_log_id, "REJECTED",
                                  rejection_reason=f"spread: {spread_detail}"[:200])
            log.info("[ENTRY] blocked: spread gate -- %s", spread_detail)
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
        _client = client_for_broker(sess, ctx["user_broker_id"])
        if _client is None:
            _update_signal_status(signal_log_id, "REJECTED",
                                  rejection_reason="no_account_credentials")
            log.warning("[ENTRY] account %s has no usable MetaAPI creds — refusing to open",
                        ctx["user_broker_id"])
            return False
        # opt15 task12: signal->broker-ack placement duration.
        with obs.timer("entry_placement_sec"):
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
        if fill_px:
            # opt15 task12: per-trade fill drift (broker fill vs signal price).
            drift_pts = entry_px - float(signal.entry_price)
            obs.observe("fill_drift_pts", drift_pts)
            if abs(drift_pts) > 0.005:
                log.info("[ENTRY] booked at broker fill %.2f (signal %.2f, drift %+.2f)",
                         entry_px, float(signal.entry_price), drift_pts)

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

            # opt15 task5: stitch the observed spread-at-entry into the audit row
            # (when the spread gate fetched one) for later friction analysis.
            placed_note = (f"spread={observed_spread:.2f}"
                           if observed_spread is not None else None)
            _update_signal_status(signal_log_id, "PLACED", position_id=position.id,
                                  note=placed_note)

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
