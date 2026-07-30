"""
Kronos S94 -- Liquidity-sweep reversal, HTF15 wick-validated (M5)
-----------------------------------------------------------------
TREND-category child strategy (user request 2026-07-07): manipulation ->
distribution. Price sweeps a liquidity level (prior-day H/L, completed-session
H/L, fractal swing), CLOSES back through it within 15 M5 bars (failed breakout
= manipulation), and the distribution leg is traded back the other way.

Setup, short case (long is the mirror):
  level  : active buy-side liquidity (PDH / session high / swing high)
  sweep  : a bar's high breaks the level; extreme = max excursion beyond
  confirm: a bar CLOSES back below the level within 15 bars of the break
  valid  : HTF15 wick — the whole sweep sits inside ONE 15m candle (it prints
           there as a wick), OR the previous COMPLETED 15m candle already
           swept the level and closed back through it
  entry  : first retrace back UP to the swept level within 30 M5 bars,
           detected on the newest CLOSED 1m bar (phantom guard: a probe bar
           that already crossed the stop cancels instead of filling)
  stop   : sweep extreme + 10% of the penetration
  tp     : extreme - 2.0 x the BREAK-BAR manipulation leg (break bar's low ->
           extreme; measured without pre-break run-up — leg definition
           validated 2026-07-07, ClaudeTradingRD/leg_analysis.py)
  time   : 1200-min backstop (240 M5 bars)

Validation 2026-07-07 (ClaudeTradingRD/validate_oos.py; OANDA XAU_USD M1->M5,
full year 2025-07..2026-07, $0.30 cost, STATIC SL/TP exits as deployed here):
  886 trades (~73/mo) WR 29% avgR +0.70 netR +622.7R PF 1.82 maxDD -38.8R,
  12 of 13 months positive. Params chosen on Apr-Jul 2026; the 9 months
  before are out-of-sample and score BETTER (PF ~2.1 static).
NOTE: winners pay 3-6R by design; expect ~29% WR with static exits.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from backtest_strategies._shared_ta import ensure_utc_ts
from backtest_strategies.base import Signal, StrategyConfig

NAME = "KRONOS_S94_SWEEP_REVERSAL"
CONFIG = StrategyConfig(
    name=NAME,
    description="Liquidity-sweep reversal (M5): sweep of PD/session/swing "
                "level closing back within 15 bars, HTF15 wick-validated, "
                "retest entry, SL beyond sweep extreme, TP 2x break-bar leg, "
                "1200-min backstop. Full-year static-exit validation: PF 1.82, "
                "+623R, 12/13 months positive (OOS better than IS).",
    cooldown_s=60,
    session_start_hour=None,      # 24h — timing filters tested and rejected
    session_end_hour=None,
    max_concurrent_positions=3,   # distribution legs overlap (~20h holds)
)

# ── Knobs (validated 2026-07-07; mirror ClaudeTradingRD backtest_compare) ─────
_SWING_K     = 10      # fractal half-width for swing levels
_CONFIRM_N   = 15      # bars allowed to close back through the level
_ENTRY_TTL   = 30      # M5 bars the retest entry stays armed
_LEVEL_TTL   = 1440    # M5 bars a swing/session level stays active
_STOP_BUF    = 0.10    # stop: extreme + 10% of penetration
_SD_MULT     = 2.0     # TP: extreme + 2 x break-bar leg (reversal direction)
_MIN_RR      = 1.0     # skip if |TP-entry| < |stop-entry|
_MAX_HOLD_MIN = 1200   # 240 M5 bars
_SESSIONS    = (("asia", 0, 7), ("london", 7, 12), ("ny", 12, 21))  # UTC
_MIN_M5      = 24 * 12 + _SWING_K  # need at least ~1 day of M5 for PD levels

# Runner MIN_BARS contract (opt15 Task 7): smallest M5 window get_signal
# tolerates. research_runner asserts RESEARCH_WIN_5M >= this at startup so an
# undersized window fails LOUD instead of silently no-trading (CHALLENGE_XAU
# defect class). Derived from _MIN_M5 -- never a second hardcoded literal.
# (Note: _LEVEL_TTL=1440 is the FULL level-universe depth; the module runs on
# shorter windows with a truncated universe -- _MIN_M5 is the hard floor.)
MIN_BARS_5M = _MIN_M5

# ── Pending-setup state (persists across runner ticks in-process) ─────────────
# Each armed setup: {side, level, stop, tp, armed_after, expires_at}
_pending: list[dict] = []
_last_conf_bar = None      # dedup: one detection pass per closed M5 bar


def reset_state() -> None:
    """Clear armed setups + dedup memory (used by tests)."""
    global _pending, _last_conf_bar
    _pending = []
    _last_conf_bar = None


def _detect(w5m: pd.DataFrame, now_utc: datetime) -> None:
    """Walk the M5 window with the level/sweep state machine and arm a setup
    for any sweep whose CONFIRMATION lands on the last closed bar. The walk is
    deterministic over the window, so re-running it each new bar reproduces
    the same live levels the offline backtest saw (window >= _LEVEL_TTL bars
    gives exact parity; shorter windows just see fewer old swing levels)."""
    global _pending, _last_conf_bar

    t = w5m["time"]
    bar_time = t.iloc[-1]
    if bar_time == _last_conf_bar:
        return
    _last_conf_bar = bar_time

    # epoch seconds regardless of the frame's datetime unit (s/us/ns, tz-aware
    # or naive) — an int64 cast alone mis-scales non-ns units and collapses
    # every 15m bucket into one
    epoch = pd.Timestamp("1970-01-01", tz=getattr(t.dt, "tz", None) or None)
    if t.dt.tz is None:
        epoch = pd.Timestamp("1970-01-01")
    ts = ((t - epoch) // pd.Timedelta(seconds=1)).to_numpy()
    day = ts // 86400
    hour = (ts // 3600) % 24
    b15 = ts // 900               # 15m epoch bucket (HTF wick containment)
    h = w5m["high"].to_numpy(float)
    l = w5m["low"].to_numpy(float)
    c = w5m["close"].to_numpy(float)
    n = len(w5m)
    last = n - 1

    # swing fractals, confirmed _SWING_K bars later (no look-ahead)
    roll = 2 * _SWING_K + 1
    hmax = pd.Series(h).rolling(roll, center=True).max().to_numpy()
    lmin = pd.Series(l).rolling(roll, center=True).min().to_numpy()

    levels: list[dict] = []       # {price, side, kind, born, break_i, extreme}
    confirmed: list[dict] = []
    cur_day = None
    day_hi = day_lo = None
    sess_state: dict = {}

    for i in range(n):
        if day[i] != cur_day:
            if day_hi is not None:
                levels = [lv for lv in levels if lv["kind"] != "PD"]
                levels.append({"price": day_hi, "side": "high", "kind": "PD",
                               "born": i, "break_i": None, "extreme": 0.0})
                levels.append({"price": day_lo, "side": "low", "kind": "PD",
                               "born": i, "break_i": None, "extreme": 0.0})
            cur_day, day_hi, day_lo = day[i], h[i], l[i]
            sess_state = {}
        else:
            day_hi, day_lo = max(day_hi, h[i]), min(day_lo, l[i])

        for name, start, end in _SESSIONS:
            if start <= hour[i] < end:
                st = sess_state.setdefault(name, [h[i], l[i]])
                st[0], st[1] = max(st[0], h[i]), min(st[1], l[i])
            elif name in sess_state and hour[i] >= end:
                st = sess_state.pop(name)
                levels.append({"price": st[0], "side": "high", "kind": "session",
                               "born": i, "break_i": None, "extreme": 0.0})
                levels.append({"price": st[1], "side": "low", "kind": "session",
                               "born": i, "break_i": None, "extreme": 0.0})

        j = i - _SWING_K
        if j >= _SWING_K:
            if h[j] == hmax[j]:
                levels.append({"price": h[j], "side": "high", "kind": "swing",
                               "born": i, "break_i": None, "extreme": 0.0})
            if l[j] == lmin[j]:
                levels.append({"price": l[j], "side": "low", "kind": "swing",
                               "born": i, "break_i": None, "extreme": 0.0})

        hits: list[dict] = []
        kill: list[dict] = []
        for lv in levels:
            if lv["kind"] != "PD" and i - lv["born"] > _LEVEL_TTL:
                kill.append(lv)
                continue
            if lv["break_i"] is None:
                if lv["side"] == "high" and h[i] > lv["price"]:
                    lv["break_i"], lv["extreme"] = i, h[i]
                elif lv["side"] == "low" and l[i] < lv["price"]:
                    lv["break_i"], lv["extreme"] = i, l[i]
            else:
                lv["extreme"] = max(lv["extreme"], h[i]) if lv["side"] == "high" \
                    else min(lv["extreme"], l[i])
                back = c[i] < lv["price"] if lv["side"] == "high" \
                    else c[i] > lv["price"]
                if back:
                    hits.append(lv)
                    kill.append(lv)
                elif i - lv["break_i"] >= _CONFIRM_N:
                    kill.append(lv)
        for lv in kill:
            levels.remove(lv)

        if hits and i == last:
            confirmed = hits

    if not confirmed:
        return
    # deepest penetration wins when several levels confirm on the same bar
    lv = max(confirmed, key=lambda x: abs(x["extreme"] - x["price"]))
    m_pen = abs(lv["extreme"] - lv["price"])
    if m_pen <= 0:
        return
    brk = lv["break_i"]

    # HTF15 wick validity
    contained = b15[brk] == b15[last]
    wick = False
    prev_mask = b15 == (b15[last] - 1)
    if prev_mask.any():
        ph, pl = h[prev_mask].max(), l[prev_mask].min()
        pc = c[np.where(prev_mask)[0][-1]]
        wick = (ph > lv["price"] and pc < lv["price"]) if lv["side"] == "high" \
            else (pl < lv["price"] and pc > lv["price"])
    if not (contained or wick):
        return

    sign = -1 if lv["side"] == "high" else 1
    stop = lv["extreme"] - sign * _STOP_BUF * m_pen
    # break-bar manipulation leg: break candle's opposite extreme -> sweep extreme
    leg = abs(lv["extreme"] - (l[brk] if sign < 0 else h[brk]))
    tp = lv["extreme"] + sign * _SD_MULT * leg
    entry = lv["price"]
    risk = abs(stop - entry)
    if risk <= 0 or abs(tp - entry) / risk < _MIN_RR:
        return

    armed_after = ensure_utc_ts(bar_time)
    _pending.append({
        "side": sign,
        "level": float(entry),
        "stop": round(float(stop), 2),
        "tp": round(float(tp), 2),
        # the retest must come AFTER the confirmation bar closes (backtest
        # parity: the fill scan starts at conf_i + 1)
        "armed_after": armed_after + timedelta(minutes=5),
        "expires_at": now_utc + timedelta(minutes=5 * _ENTRY_TTL),
    })


def _touch(probe_time, probe_hi: float, probe_lo: float) -> Signal | None:
    """Fire the oldest armed setup whose level the probe bar retested; cancel
    setups the probe has already blown through (phantom guard)."""
    global _pending
    t = ensure_utc_ts(probe_time)
    for p in list(_pending):
        if t < p["armed_after"]:
            continue
        if p["side"] < 0:
            if probe_hi >= p["stop"]:
                _pending.remove(p)
                continue
            if probe_hi >= p["level"]:
                _pending.remove(p)
                return Signal(side="SELL", entry_price=p["level"],
                              stop_loss=p["stop"], take_profit=p["tp"],
                              reason="S94_SWEEP_REVERSAL_SHORT",
                              max_hold_min=_MAX_HOLD_MIN)
        else:
            if probe_lo <= p["stop"]:
                _pending.remove(p)
                continue
            if probe_lo <= p["level"]:
                _pending.remove(p)
                return Signal(side="BUY", entry_price=p["level"],
                              stop_loss=p["stop"], take_profit=p["tp"],
                              reason="S94_SWEEP_REVERSAL_LONG",
                              max_hold_min=_MAX_HOLD_MIN)
    return None


def get_signal(w1m, w5m: pd.DataFrame, w15m, now_utc: datetime) -> Signal | None:
    global _pending
    if w5m is None or len(w5m) < _MIN_M5:
        return None
    _pending = [p for p in _pending if now_utc < p["expires_at"]]

    _detect(w5m, now_utc)
    if not _pending:
        return None

    # Probe the freshest CLOSED 1m bar (fill ~1 min after the touch);
    # M5 fallback for 1m-less offline replays.
    if w1m is not None and len(w1m) > 0:
        r = w1m.iloc[-1]
        return _touch(r["time"], float(r["high"]), float(r["low"]))
    r = w5m.iloc[-1]
    return _touch(r["time"], float(r["high"]), float(r["low"]))
