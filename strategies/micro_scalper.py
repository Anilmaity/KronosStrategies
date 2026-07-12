"""
micro_scalper.py — Variation 3
------------------------------
1m micro liquidity scalper. Delegates the full signal pipeline to
strategy.scalper_engine.get_micro_signal.

Adds daily P&L caps on top — once cumulative realized P&L crosses the loss
limit or profit target for the calendar day (UTC), trading halts until the
next UTC day.

Run:  python micro_scalper.py
"""

from __future__ import annotations

import os
import sys
import time
import logging
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shared.tsdb_reader import fetch_candles
from shared.market_timing import is_market_closed_utc
from shared.models import Session, Position, UserStrategy, Strategy, CurrencyPair
from strategy.ict_engine import EntrySignal
from strategy.scalper_engine import get_micro_signal
from strategy.entry_manager import place_entry, _VARIATION_STRATEGY_NAME

SYMBOL              = os.getenv("XAUUSD_SYMBOL", "XAU_USD")
POLL_INTERVAL       = int(os.getenv("VAR3_POLL_SEC", "5"))
COOLDOWN            = int(os.getenv("VAR3_COOLDOWN_S", "60"))
TP_POINTS           = float(os.getenv("VAR3_TP", "1.5"))
SL_POINTS           = float(os.getenv("VAR3_SL", "1.0"))
MIN_WICK_RATIO      = float(os.getenv("VAR3_MIN_WICK", "1.3"))
MIN_AVG_RANGE       = float(os.getenv("VAR3_MIN_AVG_RANGE", "0.5"))
COUNTER_TREND_MAX   = float(os.getenv("VAR3_COUNTER_TREND_MAX", "4.0"))
N_POOLS             = int(os.getenv("VAR3_N_POOLS", "5"))
POOL_LOOKBACK       = int(os.getenv("VAR3_POOL_LB", "2"))
KILL_ZONES_ONLY     = os.getenv("VAR3_KILL_ZONES_ONLY", "false").lower() == "true"
DAILY_LOSS_LIMIT    = float(os.getenv("VAR3_DAILY_LOSS_LIMIT", "-15.0"))
DAILY_PROFIT_TARGET = float(os.getenv("VAR3_DAILY_PROFIT_TARGET", "50.0"))
LOOKBACK_DAYS_1M    = 1
LOOKBACK_DAYS_5M    = 1

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("micro_scalper")

_last_1m_time:  object          = None
_last_entry_ts: datetime | None = None


def reset_state() -> None:
    """Reset module-level runner state (test hook; mirrors the sNN modules)."""
    global _last_1m_time, _last_entry_ts
    _last_1m_time = None
    _last_entry_ts = None


def _is_new_1m(candles) -> bool:
    global _last_1m_time
    if len(candles) < 2:
        return False
    t = candles.iloc[-2]["time"]
    if t == _last_1m_time:
        return False
    _last_1m_time = t
    return True


def _cooldown_active() -> bool:
    if _last_entry_ts is None:
        return False
    return (datetime.now(timezone.utc) - _last_entry_ts).total_seconds() < COOLDOWN


def _today_realized_pnl_points() -> float:
    """Sum realized P&L (in points) across VAR3 positions closed today (UTC).
    Reads Position.realized_profit_loss, which position_monitor sets on exit."""
    sess = Session()
    try:
        cp = sess.query(CurrencyPair).filter_by(symbol=SYMBOL).first()
        if cp is None:
            return 0.0
        strat = sess.query(Strategy).filter_by(
            currencypair_id=cp.id,
            name=_VARIATION_STRATEGY_NAME["VAR3"],
        ).first()
        if not strat:
            return 0.0
        us = sess.query(UserStrategy).filter_by(
            strategy_id=strat.id, is_active=True, deployed=True
        ).first()
        if not us:
            return 0.0
        # Filter to today's (UTC) closes in SQL — modified_at is timestamptz,
        # so an instant-range compare equals the old per-row UTC-date check
        # without loading every closed position the strategy ever had.
        today = datetime.now(timezone.utc).date()
        day_start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
        positions = sess.query(Position).filter(
            Position.user_strategy_id == us.id,
            Position.quantity == 0,  # closed only
            Position.modified_at >= day_start,
            Position.modified_at < day_start + timedelta(days=1),
        ).all()
        total_cash = sum(float(p.realized_profit_loss or 0.0) for p in positions)
        # realized_profit_loss is cash; convert to points using the configured
        # entry quantity (uniform across VAR3 trades).
        qty = float(strat.entry_quantity or 0.05) or 0.05
        return total_cash / qty
    finally:
        sess.close()


def _daily_cap_hit() -> bool:
    pnl = _today_realized_pnl_points()
    if pnl <= DAILY_LOSS_LIMIT:
        log.info("[VAR3] Daily loss limit hit (%.2f <= %.2f) — pausing", pnl, DAILY_LOSS_LIMIT)
        return True
    if pnl >= DAILY_PROFIT_TARGET:
        log.info("[VAR3] Daily profit target hit (%.2f >= %.2f) — pausing", pnl, DAILY_PROFIT_TARGET)
        return True
    return False


def run():
    global _last_entry_ts
    log.info("=== MicroScalper (VAR3) started | symbol=%s | cooldown=%ds | TP/SL=%.2f/%.2f ===",
             SYMBOL, COOLDOWN, TP_POINTS, SL_POINTS)

    while True:
        try:
            if is_market_closed_utc():
                time.sleep(POLL_INTERVAL)
                continue

            candles_1m = fetch_candles("1m", days=LOOKBACK_DAYS_1M, symbol=SYMBOL)
            candles_5m = fetch_candles("5m", days=LOOKBACK_DAYS_5M, symbol=SYMBOL)
            if candles_1m.empty or not _is_new_1m(candles_1m):
                time.sleep(POLL_INTERVAL); continue
            if _cooldown_active():
                time.sleep(POLL_INTERVAL); continue
            if _daily_cap_hit():
                time.sleep(POLL_INTERVAL); continue

            completed_1m = candles_1m.iloc[:-1].copy()
            completed_5m = candles_5m.iloc[:-1].copy() if not candles_5m.empty else candles_5m

            sig = get_micro_signal(
                completed_1m, completed_5m,
                tp_points=TP_POINTS,
                sl_points=SL_POINTS,
                min_wick_ratio=MIN_WICK_RATIO,
                min_avg_range=MIN_AVG_RANGE,
                counter_trend_max_dist=COUNTER_TREND_MAX,
                n_pools=N_POOLS,
                pool_lookback=POOL_LOOKBACK,
                kill_zones_only=KILL_ZONES_ONLY,
            )
            if sig is None:
                time.sleep(POLL_INTERVAL); continue

            entry = EntrySignal(
                side=sig.side,
                entry_price=sig.entry_price,
                stop_loss=sig.stop_loss,
                take_profit=sig.take_profit,
                reason=sig.reason,
                zone_low=sig.pool_level,
                zone_high=sig.pool_level,
            )
            log.info(
                "[VAR3 SIGNAL] %s @ %.2f | SL=%.2f TP=%.2f | pool=%s level=%.2f | %s",
                sig.side, sig.entry_price, sig.stop_loss, sig.take_profit,
                sig.pool_type, sig.pool_level, sig.reason,
            )
            placed = place_entry(entry, symbol=SYMBOL, variation="VAR3")
            if placed:
                _last_entry_ts = datetime.now(timezone.utc)
                log.info("[VAR3 ORDER] Placed | cooldown starts (%ds)", COOLDOWN)
            else:
                log.info("[VAR3 ORDER] Skipped (open position / cap / DB error)")

        except KeyboardInterrupt:
            log.info("=== MicroScalper stopped ===")
            break
        except Exception:
            log.exception("[VAR3 LOOP ERROR] continuing")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
