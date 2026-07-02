"""
Kronos S95 -- London/NY Opening-Range Breakout (M5)
---------------------------------------------------
Session breakout child strategy for the Strategy Manager roster
(docs/superpowers/specs/2026-07-02-strategy-manager-design.md §4).

Design (zero discretion), evaluated on the last CLOSED M5 bar:
  range : the opening range = first 15 minutes from session open
          (London 07:00 UTC, New York 13:30 UTC) = three M5 bars.
  entry : within the 2 hours after the range completes, an M5 CLOSE
          beyond the range high (BUY) / range low (SELL), with-breakout
          direction only. Range width must be between 2 and 12 pts.
  stop  : structural -- just beyond the OPPOSITE range extreme
          (small buffer). Risk capped at 8 pts: wider -> skip the trade.
  target: 1.5R. Time exit after 240 minutes.

One trade per session window per direction (module-level dedup) plus the
runner-level CONFIG.cooldown_s to avoid same-bar refires. Session gating is
enforced both by CONFIG hours (runner) and inside get_signal (defense in
depth). Every signal carries a hard stop; no averaging.

Manager gating policy (spec §4): run only 06:45-10:00 & 13:15-16:00 UTC and
vol_regime in {NORMAL, HIGH} -- that is the manager's job, not this module's.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig

NAME = "KRONOS_S95_SESSION_BREAKOUT"
CONFIG = StrategyConfig(
    name=NAME,
    description="London/NY 15-min opening-range breakout on M5 close, "
                "structural SL beyond opposite extreme (<=8 pts), TP 1.5R, "
                "240-min time exit. One trade per session per direction.",
    cooldown_s=300,            # one M5 bar; per-session dedup is stricter
    session_start_hour=7,      # covers both entry windows (07:15..15:45 UTC)
    session_end_hour=16,
    max_concurrent_positions=1,
)

# ── Knobs ─────────────────────────────────────────────────────────────────────
_SESSIONS = {                  # session name -> open minute-of-day (UTC)
    "LONDON": 7 * 60,          # 07:00
    "NY":     13 * 60 + 30,    # 13:30
}
_RANGE_MIN        = 15         # opening range = first 15 min (3 x M5)
_ENTRY_WINDOW_MIN = 120        # entries allowed for 2h after range completes
_MIN_RANGE_PTS    = 2.0
_MAX_RANGE_PTS    = 12.0
_MAX_RISK_PTS     = 8.0        # skip if structural risk wider than this
_SL_BUFFER        = 0.10       # "just beyond" the opposite extreme
_RR               = 1.5
_MAX_HOLD_MIN     = 240

# One trade per (utc-date, session, side). Best-effort in-process dedup --
# a restart clears it, but cooldown_s + entry_manager's open-position cap
# keep a duplicate from stacking risk.
_fired: set[tuple] = set()


def reset_state() -> None:
    """Clear the per-session dedup memory (used by tests)."""
    _fired.clear()


def _active_session(now_utc: datetime) -> tuple[str, int] | tuple[None, None]:
    """Return (session_name, open_minute) if now_utc is inside a session's
    entry window: [open + 15min, open + 15min + 2h)."""
    m = now_utc.hour * 60 + now_utc.minute
    for name, open_m in _SESSIONS.items():
        if open_m + _RANGE_MIN <= m < open_m + _RANGE_MIN + _ENTRY_WINDOW_MIN:
            return name, open_m
    return None, None


def get_signal(w1m, w5m: pd.DataFrame, w15m, now_utc: datetime) -> Signal | None:
    if w5m is None or len(w5m) < 4:
        return None

    sess_name, open_m = _active_session(now_utc)
    if sess_name is None:
        return None

    t = pd.to_datetime(w5m["time"], utc=True)
    mod = t.dt.hour * 60 + t.dt.minute
    today = now_utc.date()
    same_day = (t.dt.date == today).to_numpy()

    # Opening range: the three M5 bars in [open, open+15) today. All three
    # must be present and closed, else the range is not yet defined.
    range_mask = same_day & (mod >= open_m).to_numpy() & (mod < open_m + _RANGE_MIN).to_numpy()
    rbars = w5m[range_mask]
    if len(rbars) < 3:
        return None
    r_hi = float(rbars["high"].max())
    r_lo = float(rbars["low"].min())
    width = r_hi - r_lo
    if not (_MIN_RANGE_PTS <= width <= _MAX_RANGE_PTS):
        return None

    # Breakout bar = the last CLOSED M5 bar; must be today, after the range.
    if not same_day[-1] or int(mod.iloc[-1]) < open_m + _RANGE_MIN:
        return None
    close = float(w5m["close"].iloc[-1])

    if close > r_hi:
        sl = round(r_lo - _SL_BUFFER, 2)
        risk = close - sl
        if risk <= 0 or risk > _MAX_RISK_PTS:
            return None
        key = (today, sess_name, "BUY")
        if key in _fired:
            return None
        _fired.add(key)
        return Signal(
            side="BUY",
            entry_price=close,
            stop_loss=sl,
            take_profit=round(close + _RR * risk, 2),
            reason=f"S95_{sess_name}_ORB_LONG",
            max_hold_min=_MAX_HOLD_MIN,
        )

    if close < r_lo:
        sl = round(r_hi + _SL_BUFFER, 2)
        risk = sl - close
        if risk <= 0 or risk > _MAX_RISK_PTS:
            return None
        key = (today, sess_name, "SELL")
        if key in _fired:
            return None
        _fired.add(key)
        return Signal(
            side="SELL",
            entry_price=close,
            stop_loss=sl,
            take_profit=round(close - _RR * risk, 2),
            reason=f"S95_{sess_name}_ORB_SHORT",
            max_hold_min=_MAX_HOLD_MIN,
        )

    return None
