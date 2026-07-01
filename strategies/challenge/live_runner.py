"""
live_runner.py
--------------
Live H4 trend-follow runner for a single funded/prop challenge account.

Loop (per closed H4 bar):
  1. Roll the day over at a UTC date change -> ChallengeGuard.start_new_day(equity).
  2. Reconcile with the broker: if our tracked position is gone, it hit the stop ->
     record realized PnL into the guard and go flat.
  3. If in a position: ratchet the 3xATR chandelier stop on the broker (never loosen).
  4. If flat and the guard allows: check the just-closed bar for a breakout signal,
     size from live equity, and place a market order with the initial hard stop.

State is in-process; on restart the runner adopts any open broker position so it
keeps trailing it. Every decision reuses the unit-tested pure logic in
challenge_xau / risk. Set CHALLENGE_DRY_RUN=true to run the full loop without
placing real orders.

Required env:
  CHALLENGE_META_TOKEN (or META_API_TOKEN), CHALLENGE_META_ACCOUNT
Key optional env (defaults in CONFIG below):
  CHALLENGE_SYMBOL, CHALLENGE_POLL_SEC, CHALLENGE_RISK_PCT, CHALLENGE_INITIAL_BALANCE,
  CHALLENGE_DRY_RUN, plus per-param overrides CHALLENGE_DONCHIAN_N / _EMA_FAST / etc.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from datetime import date

import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # strategies/ on path

# Imports are relative to the strategies/ dir (prepended above) so this runs both
# in-repo and in the container built from ./strategies (where /app == strategies/).
from shared.tsdb_reader import fetch_candles            # noqa: E402
from shared.market_timing import is_market_closed_utc   # noqa: E402

from challenge.challenge_xau import (                    # noqa: E402
    DEFAULTS, signal_at_last_bar, chandelier_trail, atr as compute_atr,
)
from challenge.risk import position_size, ChallengeGuard  # noqa: E402
from challenge.broker import ChallengeBroker             # noqa: E402
from challenge.dashboard import build_dashboard          # noqa: E402

load_dotenv()
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("challenge-runner")


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


SYMBOL = os.getenv("CHALLENGE_SYMBOL", "XAU_USD")
POLL_SEC = _i("CHALLENGE_POLL_SEC", 60)
TF = os.getenv("CHALLENGE_TF", "4h")
DAYS = _i("CHALLENGE_DAYS", 400)            # H4 history fetched each poll (warmup + buffer)
RISK_PCT = _f("CHALLENGE_RISK_PCT", 0.01)
# When 1% sizing rounds below the broker minimum lot (high vol / small account),
# allow the min lot only if its risk stays within this cap of equity. At $4k gold
# with a 3xATR (~$127) stop, one 0.01 lot risks ~2.5% of $5k — set to 0 to forbid.
MAX_TRADE_RISK_PCT = _f("CHALLENGE_MAX_TRADE_RISK_PCT", 0.025)
INITIAL_BALANCE = _f("CHALLENGE_INITIAL_BALANCE", 5000.0)
DRY_RUN = os.getenv("CHALLENGE_DRY_RUN", "false").lower() == "true"

PARAMS = {
    "donchian_n": _i("CHALLENGE_DONCHIAN_N", DEFAULTS["donchian_n"]),
    "ema_fast":   _i("CHALLENGE_EMA_FAST",   DEFAULTS["ema_fast"]),
    "ema_slow":   _i("CHALLENGE_EMA_SLOW",   DEFAULTS["ema_slow"]),
    "atr_n":      _i("CHALLENGE_ATR_N",      DEFAULTS["atr_n"]),
    "atr_mult":   _f("CHALLENGE_ATR_MULT",   DEFAULTS["atr_mult"]),
}

# FundingPips $5k 2-step phase-1 rails (override via env if needed).
GUARD_KW = dict(
    profit_target_pct=_f("CHALLENGE_PROFIT_TARGET_PCT", 0.10),
    max_daily_dd_pct=_f("CHALLENGE_MAX_DAILY_DD_PCT", 0.05),
    max_overall_dd_pct=_f("CHALLENGE_MAX_OVERALL_DD_PCT", 0.10),
    max_consec_losses=_i("CHALLENGE_MAX_CONSEC_LOSSES", 2),
)


def _prepare_bars(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalize raw OANDA candles into the frame the strategy evaluates.

    `tsdb_reader.fetch_candles` returns ONLY completed candles (it filters out the
    still-forming one), so we keep ALL rows — the newest is the just-closed H4 bar
    we must act on. Do NOT drop it: an earlier `iloc[:-1]` here double-dropped
    (OANDA already excludes the forming bar), which evaluated signals one H4 bar
    (~4h) late and gave back the breakout edge.
    """
    if raw is None or raw.empty:
        return pd.DataFrame()
    df = raw.copy()
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.sort_values("time").reset_index(drop=True)


class NoopDashboard:
    """Dashboard that records nothing — used when DB sync is off / not injected, so
    the runner's dashboard calls are always safe to make."""
    def open_position(self, *a, **k): return None
    def update_live(self, *a, **k): return None
    def conclude_position(self, *a, **k): return None
    def record_signal(self, *a, **k): return None
    def find_open_position_id(self, *a, **k): return None


class Runner:
    def __init__(self, broker: ChallengeBroker, guard: ChallengeGuard, dashboard=None):
        self.broker = broker
        self.guard = guard
        # Best-effort dashboard writer (apis_position / StrategySignal). Decoupled
        # and never raises, so a DB issue can't block trading. Defaults to no-op.
        self.dashboard = dashboard if dashboard is not None else NoopDashboard()
        self.position: dict | None = None      # {id, dash_id, direction, stop, extreme, volume}
        self.last_bar_seen: pd.Timestamp | None = None
        self.current_day: date | None = None

    def _equity(self) -> float | None:
        eq = self.broker.account_equity()
        if eq is None and self.broker.dry_run:
            return INITIAL_BALANCE
        return eq

    def _completed_bars(self) -> pd.DataFrame:
        return _prepare_bars(fetch_candles(TF, DAYS, symbol=SYMBOL))

    def _adopt_broker_position(self, pos: dict) -> None:
        """On restart, take over an existing broker position so we keep trailing it."""
        direction = "BUY" if str(pos.get("type", "")).endswith("BUY") else "SELL"
        entry = float(pos.get("openPrice", 0.0) or 0.0)
        stop = float(pos.get("stopLoss", 0.0) or 0.0) or entry
        self.position = {"id": pos.get("id"), "direction": direction, "stop": stop,
                         "extreme": entry, "volume": float(pos.get("volume", 0.0) or 0.0),
                         "dash_id": self.dashboard.find_open_position_id()}
        log.info("adopted existing broker position %s %s entry=%.2f stop=%.2f",
                 self.position["id"], direction, entry, stop)

    def _on_position_closed(self) -> None:
        pid = self.position["id"] if self.position else None
        pnl = self.broker.position_realized_pnl(pid) if pid else None
        if pnl is None:
            pnl = 0.0
            log.warning("position %s closed but realized PnL unreadable — recording 0", pid)
        self.guard.record_trade(pnl)
        self.dashboard.conclude_position(self.position.get("dash_id") if self.position else None,
                                         pnl, side=self.position.get("direction") if self.position else None,
                                         volume=self.position.get("volume") if self.position else None)
        log.info("position %s closed | realized=%.2f | consec_losses=%d day_realized=%.2f",
                 pid, pnl, self.guard.consec_losses, self.guard.day_realized)
        self.position = None

    def _trail(self, last_bar: pd.Series, current_atr: float) -> None:
        d = self.position["direction"]
        if d == "BUY":
            self.position["extreme"] = max(self.position["extreme"], float(last_bar["high"]))
        else:
            self.position["extreme"] = min(self.position["extreme"], float(last_bar["low"]))
        # Classic chandelier uses the CURRENT ATR each bar; fall back to the ATR
        # captured at entry if the live ATR is unavailable (e.g. NaN warmup).
        atr_val = current_atr if current_atr and current_atr == current_atr else self.position["atr"]
        new_stop = chandelier_trail(d, prev_stop=self.position["stop"],
                                    extreme_since_entry=self.position["extreme"],
                                    atr=atr_val, mult=PARAMS["atr_mult"])
        if (d == "BUY" and new_stop > self.position["stop"]) or \
           (d == "SELL" and new_stop < self.position["stop"]):
            if self.broker.modify_sl(self.position["id"], new_stop):
                log.info("trailed stop %s -> %.2f", self.position["id"], new_stop)
                self.position["stop"] = new_stop
        self.dashboard.update_live(self.position.get("dash_id"), ltp=float(last_bar["close"]))

    def _maybe_enter(self, completed: pd.DataFrame, equity: float) -> None:
        # Evaluate the signal FIRST so guard/sizing rejections of a REAL signal are
        # recorded on the dashboard (a quiet no-signal bar records nothing).
        sig = signal_at_last_bar(completed, **PARAMS)
        if sig is None:
            return
        tp = None  # trend-follow has no fixed TP (exit is the chandelier trail)

        def _reject(reason: str) -> None:
            self.dashboard.record_signal(sig.direction, sig.trigger_close, sig.sl, tp,
                                         status="REJECTED", rejection_reason=reason)

        ok, reason = self.guard.can_trade(equity)
        if not ok:
            log.info("guard blocks entry: %s (equity=%.2f)", reason, equity)
            _reject(f"guard:{reason}")
            return
        vol = position_size(equity, RISK_PCT, sig.risk_per_unit,
                            max_trade_risk_pct=MAX_TRADE_RISK_PCT)
        if vol <= 0:
            log.warning("signal %s but sized volume is 0 (equity=%.2f risk_unit=%.2f, "
                        "even min lot exceeds the %.1f%% cap) — skip",
                        sig.direction, equity, sig.risk_per_unit, MAX_TRADE_RISK_PCT * 100)
            _reject("sized_volume_0 (risk cap)")
            return
        # Don't open a trade whose worst-case (-1R) loss would breach the room left
        # in today's daily limit or the overall floor.
        trade_risk = vol * 100.0 * sig.risk_per_unit
        budget = self.guard.risk_budget(equity)
        if trade_risk > budget:
            log.info("signal %s skipped: -1R risk $%.0f exceeds remaining budget $%.0f",
                     sig.direction, trade_risk, budget)
            _reject("risk_exceeds_remaining_budget")
            return
        side = "buy" if sig.direction == "BUY" else "sell"
        tid = self.broker.place_market(side, SYMBOL, vol, sig.sl, comment="challenge-h4")
        if not tid:
            log.error("entry order failed for %s", sig.direction)
            _reject("broker_rejected")
            return
        entry_ref = sig.trigger_close
        dash_id = self.dashboard.open_position(side, entry_ref, vol, tid)
        self.dashboard.record_signal(sig.direction, entry_ref, sig.sl, tp, status="PLACED",
                                     reason=f"challenge-h4 breakout | SL {sig.sl}",
                                     position_id=dash_id)
        self.position = {"id": tid, "dash_id": dash_id, "direction": sig.direction,
                         "stop": sig.sl, "extreme": entry_ref, "volume": vol, "atr": sig.atr}
        log.info("ENTER %s vol=%.2f stop=%.2f risk_unit=%.2f ticket=%s dash=%s",
                 sig.direction, vol, sig.sl, sig.risk_per_unit, tid, dash_id)

    def tick(self) -> None:
        completed = self._completed_bars()
        if completed.empty or len(completed) < 60:
            log.debug("not enough bars yet (%d)", len(completed))
            return
        last_time = completed["time"].iloc[-1]
        if self.last_bar_seen is not None and last_time <= self.last_bar_seen:
            return                                       # no new closed bar
        self.last_bar_seen = last_time
        last_bar = completed.iloc[-1]

        equity = self._equity()
        if equity is None:
            log.warning("equity unreadable — skipping this bar (fail safe)")
            return

        # Day rollover
        bar_day = pd.Timestamp(last_time).date()
        if self.current_day != bar_day:
            self.guard.start_new_day(equity)
            self.current_day = bar_day
            log.info("new trading day %s | equity=%.2f | day budget reset", bar_day, equity)

        # Reconcile broker truth
        broker_pos = self.broker.open_position(SYMBOL)
        if self.position and broker_pos is None and not self.broker.dry_run:
            self._on_position_closed()
        elif self.position is None and broker_pos is not None:
            self._adopt_broker_position(broker_pos)

        if self.position:
            current_atr = float(compute_atr(completed, PARAMS["atr_n"]).iloc[-1])
            self._trail(last_bar, current_atr)
        else:
            self._maybe_enter(completed, equity)

        # Heartbeat once per closed bar so an idle-but-healthy runner is visibly
        # distinct from a dead one in the logs (a quiet no-signal bar otherwise
        # logs nothing at all). Reflects post-decision state.
        log.info("heartbeat | bar=%s | equity=%.2f | in_position=%s | consec_losses=%d",
                 pd.Timestamp(last_time).strftime("%Y-%m-%d %H:%M"), equity,
                 "yes" if self.position else "no", self.guard.consec_losses)

    def run(self) -> None:
        log.info("challenge-runner start | symbol=%s tf=%s risk=%.2f%% dry_run=%s params=%s",
                 SYMBOL, TF, RISK_PCT * 100, self.broker.dry_run, PARAMS)
        log.info("guard | initial=%.0f target=+%.0f%% daily_dd=%.0f%% overall_dd=%.0f%% consec=%d",
                 self.guard.initial_balance, self.guard.profit_target_pct * 100,
                 self.guard.max_daily_dd_pct * 100, self.guard.max_overall_dd_pct * 100,
                 self.guard.max_consec_losses)
        while True:
            try:
                if is_market_closed_utc():
                    log.debug("market closed — skip")
                else:
                    self.tick()
            except KeyboardInterrupt:
                log.info("shutdown")
                return
            except Exception:
                log.exception("tick failed (continuing)")
            try:
                time.sleep(POLL_SEC)
            except KeyboardInterrupt:
                log.info("shutdown")
                return


def main() -> None:
    token = os.getenv("CHALLENGE_META_TOKEN") or os.getenv("META_API_TOKEN", "")
    account = os.getenv("CHALLENGE_META_ACCOUNT", "")
    if not DRY_RUN and (not token or not account):
        sys.exit("CHALLENGE_META_TOKEN and CHALLENGE_META_ACCOUNT must be set "
                 "(or CHALLENGE_DRY_RUN=true for a no-broker smoke run)")
    region = os.getenv("CHALLENGE_META_REGION", "")
    broker = ChallengeBroker(token, account, dry_run=DRY_RUN, label="challenge", region=region)
    guard = ChallengeGuard(INITIAL_BALANCE, **GUARD_KW)
    dashboard = build_dashboard()
    log.info("dashboard sync: %s", "ON" if dashboard.enabled else "OFF (inert)")
    Runner(broker, guard, dashboard=dashboard).run()


if __name__ == "__main__":
    main()
