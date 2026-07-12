"""
research_runner.py
------------------
Generic 1m-driven live runner for any strategy module under
`backtest_strategies/sNN_*.py`. Each module exports:

    NAME    : str
    CONFIG  : StrategyConfig  (cooldown_s, session_start_hour, session_end_hour)
    get_signal(w1m, w5m, w15m, now_utc) -> Signal | None

Dispatch:  RESEARCH_STRATEGY=s01_ote_fib  → loads backtest_strategies.s01_ote_fib
           RESEARCH_STRATEGY=c03_fvg_fill → loads concept_strategies.c03_fvg_fill
(module name starting with 'c' resolves to concept_strategies; otherwise backtest_strategies.)

Per tick (5s default):
  - Fetch 1m / 5m / 15m candles from tsdb
  - On a new completed 1m bar, build windows
  - Honour CONFIG session hours + cooldown_s
  - Call get_signal; if a signal fires, route to place_entry with variation=NAME
"""
from __future__ import annotations

import importlib
import logging
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shared.tsdb_reader import fetch_candles
from shared.market_timing import is_market_closed_utc
from strategy.ict_engine import EntrySignal
from strategy.entry_manager import place_entry, _VARIATION_STRATEGY_NAME
from backtest_strategies.base import in_session

MODULE         = os.getenv("RESEARCH_STRATEGY")
SYMBOL         = os.getenv("XAUUSD_SYMBOL", "XAU_USD")
POLL_INTERVAL  = int(os.getenv("RESEARCH_POLL_SEC", "5"))
WIN_1M         = int(os.getenv("RESEARCH_WIN_1M",  "60"))
WIN_5M         = int(os.getenv("RESEARCH_WIN_5M",  "80"))
WIN_15M        = int(os.getenv("RESEARCH_WIN_15M", "100"))
# How many days of candles to pull (defaults preserve prior behaviour). A
# strategy whose legs need a long lookback (e.g. kronos_combined_v2's MR legs
# need 205 M15/M5 bars) raises these so the tail() window can actually fill,
# including across weekend gaps.
DAYS_1M        = int(os.getenv("RESEARCH_DAYS_1M",  "1"))
DAYS_5M        = int(os.getenv("RESEARCH_DAYS_5M",  "2"))
DAYS_15M       = int(os.getenv("RESEARCH_DAYS_15M", "3"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("research_runner")

# Idle-liveness heartbeat: the loop below `continue`s silently on every quiet
# reason (no new bar, out of session, cooldown, no signal), so a healthy idle
# runner otherwise looks dead. Emit a throttled heartbeat instead of nothing.
HEARTBEAT_SEC = int(os.getenv("RESEARCH_HEARTBEAT_SEC", "900"))

_last_1m_time:      object          = None
_last_entry_ts:     datetime | None = None
_last_heartbeat_ts: datetime | None = None


def reset_state() -> None:
    """Reset module-level runner state (test hook; mirrors the sNN modules)."""
    global _last_1m_time, _last_entry_ts, _last_heartbeat_ts
    _last_1m_time = None
    _last_entry_ts = None
    _last_heartbeat_ts = None


def _should_heartbeat(now: datetime, last: datetime | None, interval_s: int) -> bool:
    """True when a liveness heartbeat is due: never logged before, or at least
    `interval_s` seconds have elapsed since the last one."""
    if last is None:
        return True
    return (now - last).total_seconds() >= interval_s


def _is_new_1m(candles) -> bool:
    """True when the newest CLOSED 1m bar changed. fetch_candles returns only
    complete candles (tsdb_reader filters on OANDA's `complete` flag), so the
    last row IS the newest closed bar — keying off iloc[-2] would react a full
    minute late."""
    global _last_1m_time
    if len(candles) < 1:
        return False
    t = candles.iloc[-1]["time"]
    if t == _last_1m_time:
        return False
    _last_1m_time = t
    return True


def _build_windows(c1m, c5m, c15m):
    """Tail the strategy windows from the OANDA frames AS-IS.

    fetch_candles already returns only CLOSED candles (the `complete` filter in
    tsdb_reader._fetch_oanda_ohlc), so the newest row is the newest closed bar.
    An extra iloc[:-1] here would hide that bar until the NEXT bar closes —
    the stale-fill bug behind the 2026-07-06 SESSION_BREAKOUT losses (every
    live entry fired exactly one M5 bar late, 5-15 min after the OR touch).
    """
    w1m = c1m.tail(WIN_1M).reset_index(drop=True)
    w5m = c5m.tail(WIN_5M).reset_index(drop=True) if not c5m.empty else c5m
    w15m = c15m.tail(WIN_15M).reset_index(drop=True) if not c15m.empty else c15m
    return w1m, w5m, w15m


def main():
    global _last_entry_ts, _last_heartbeat_ts

    if not MODULE:
        sys.exit("RESEARCH_STRATEGY env var required (e.g., s01_ote_fib)")

    pkg = "concept_strategies" if MODULE.lower().startswith("c") else "backtest_strategies"
    mod = importlib.import_module(f"{pkg}.{MODULE}")
    name = mod.NAME
    cfg = mod.CONFIG
    get_signal = mod.get_signal

    if name not in _VARIATION_STRATEGY_NAME:
        sys.exit(f"NAME='{name}' not registered in entry_manager._VARIATION_STRATEGY_NAME")

    log.info(
        "research_runner starting: module=%s NAME=%s cd=%ds session=%s..%s",
        MODULE, name, cfg.cooldown_s, cfg.session_start_hour, cfg.session_end_hour,
    )

    while True:
        try:
            if is_market_closed_utc():
                time.sleep(POLL_INTERVAL)
                continue

            now_utc = datetime.now(timezone.utc)
            if _should_heartbeat(now_utc, _last_heartbeat_ts, HEARTBEAT_SEC):
                _last_heartbeat_ts = now_utc
                log.info("[%s HEARTBEAT] alive | last_entry=%s | cooldown=%ds",
                         name,
                         _last_entry_ts.strftime("%Y-%m-%d %H:%M") if _last_entry_ts else "none",
                         cfg.cooldown_s)

            # Fetch the 1m frame first and gate on it — on idle ticks (no new
            # closed bar / out of session / cooldown) the 5m/15m fetches would
            # be wasted OANDA calls once per cache TTL, per runner service.
            c1m = fetch_candles("1m", days=DAYS_1M, symbol=SYMBOL)

            if c1m.empty or not _is_new_1m(c1m):
                time.sleep(POLL_INTERVAL)
                continue

            if not in_session(now_utc, cfg):
                time.sleep(POLL_INTERVAL)
                continue

            if _last_entry_ts and (now_utc - _last_entry_ts).total_seconds() < cfg.cooldown_s:
                time.sleep(POLL_INTERVAL)
                continue

            c5m  = fetch_candles("5m",  days=DAYS_5M,  symbol=SYMBOL)
            c15m = fetch_candles("15m", days=DAYS_15M, symbol=SYMBOL)

            # Feed is closed-bars-only; no dropping here (see _build_windows).
            w1m, w5m, w15m = _build_windows(c1m, c5m, c15m)

            if len(w1m) < 30:
                time.sleep(POLL_INTERVAL)
                continue

            sig = get_signal(w1m, w5m, w15m, now_utc)
            if sig is None:
                time.sleep(POLL_INTERVAL)
                continue

            entry = EntrySignal.from_signal(sig)
            log.info(
                "[%s SIGNAL] %s @ %.2f | SL=%.2f TP=%.2f | maxhold=%s | %s",
                name, sig.side, sig.entry_price, sig.stop_loss, sig.take_profit,
                getattr(sig, "max_hold_min", None), sig.reason,
            )
            placed = place_entry(
                entry, symbol=SYMBOL, variation=name,
                max_concurrent=getattr(cfg, "max_concurrent_positions", 1),
            )
            if placed:
                _last_entry_ts = now_utc
                log.info("[%s ORDER] Placed | cooldown=%ds", name, cfg.cooldown_s)
            else:
                log.info("[%s ORDER] Skipped (open position / cap / DB error)", name)

        except KeyboardInterrupt:
            log.info("=== research_runner stopped ===")
            return
        except Exception:
            log.exception("[%s LOOP ERROR] continuing", MODULE)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
