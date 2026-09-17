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
full year 2025-07..2026-07, $0.30 cost):
  886 trades (~73/mo) WR 29% avgR +0.70 netR +622.7R PF 1.82 maxDD -38.8R,
  12 of 13 months positive. Params chosen on Apr-Jul 2026; the 9 months
  before are out-of-sample and score BETTER (PF ~2.1).
NOTE: winners pay 3-6R by design; expect ~29% WR.

CORRECTION 2026-09-02 -- the line above previously read "STATIC SL/TP exits as
deployed here". That was WRONG and materially so. validate_oos.py actually calls

    simulate(df, sweeps, single_pos=False, be=True, allow=c15 | w15,
             sd_mult=2.0, legs=legs)

so the PF 1.82 headline was produced with (a) `be=True`, a BREAK-EVEN STOP MOVE at
+1R, and (b) `single_pos=False`, UNLIMITED overlapping positions -- neither of which
this module has. It emits a static stop and target, entry_manager turns those into
static STOPLOSS/TARGET triggers, and CONFIG caps concurrency at 3. There is no
break-even logic anywhere on the live path.

That gap explains the strategy's record. Deployed as-is it has produced:
  * live, all time            : +$22 over 66 trades -- flat
  * offline replay, 19.5mo    : PF 0.851 (train 0.843 / test 0.858), static exits,
                                cost 0.45, max_concurrent 3
against a published 1.82. Three different exit/concurrency/cost models, three
different answers, and the docstring attributed the most favourable one to the
least favourable configuration.

Do NOT cite PF 1.82 for the shipped configuration. Whether adding a real
break-even rule recovers it is a live open question, not an established result.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

try:
    from numba import njit as _njit
    _NUMBA = True
except ImportError:                                 # pragma: no cover
    _NUMBA = False

    def _njit(*a, **k):                             # same source, interpreted
        return a[0] if a and callable(a[0]) else (lambda f: f)

from backtest_strategies._shared_ta import ensure_utc_ts
from backtest_strategies.base import Signal, StrategyConfig

log = logging.getLogger(__name__)
if not _NUMBA:                                      # pragma: no cover
    log.warning("S94: numba not installed -- level machine runs interpreted "
                "(~14x slower per rebuild; results identical). pip install numba.")

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
# M5 bars a swing/session level stays active. env-tunable (opt15 Task 10):
# S94_LEVEL_TTL_BARS. 1440 (default, unchanged) is the FULL backtest level-
# universe depth; live windows are usually shorter and see a truncated set of
# old swing/session levels -- the module's known live<->backtest divergence
# (a one-time WARN fires in _detect when the window is shorter than this).
_LEVEL_TTL   = int(os.getenv("S94_LEVEL_TTL_BARS", "1440"))
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

# ── Incremental level/sweep machine state (opt15 Task 10) ─────────────────────
# The level/sweep walk is INCREMENTAL: instead of rebuilding the whole window
# from born=0 every closed M5 bar (O(n x levels) each tick), the machine state
# is persisted here and only bars newer than `last_ts` are stepped. Full-rebuild
# fallback fires when the window origin changes (window slid past the state
# origin) or on any inconsistency -- so the result is always identical to the
# old full-window walk (guarded byte-for-byte by tests/test_s94_parity.py).
#
# Indices stored in the machine (born / break_i) are 0-based OFFSETS FROM
# `origin_ts` -- while the origin is stable they equal the current window
# position, so every `i - born` / `i - break_i` bar-count comparison keeps the
# exact semantics of the old window-relative walk. When the origin moves the
# whole machine is discarded and rebuilt, so no stale offset ever survives.
_state: dict | None = None
_ttl_warned = False        # one-time WARN when window shorter than _LEVEL_TTL


def reset_state() -> None:
    """Clear armed setups + the incremental level machine (used by tests)."""
    global _pending, _state, _ttl_warned
    _pending = []
    _state = None
    _ttl_warned = False


# Level kinds / sides inside the compiled machine (ints, not strings).
_K_PD, _K_SESSION, _K_SWING = 0, 1, 2
_SIDE_HIGH, _SIDE_LOW = 1, -1
_SESS_START = np.array([s[1] for s in _SESSIONS], dtype=np.int64)
_SESS_END = np.array([s[2] for s in _SESSIONS], dtype=np.int64)
_GROW = 6          # max level births per stepped bar: 2 PD + 2 session + 2 swing


def _new_state(origin_ts, capacity: int = 64) -> dict:
    """Fresh (empty) level/sweep machine anchored at `origin_ts`.

    Levels live in parallel numpy arrays (insertion order == the old list order,
    removals compact in place) so the per-bar walk can be compiled; `n_lv` is
    the live count. Day/session trackers use sentinels (-1 day, NaN price,
    sess_on flag) in place of the old None / missing-key states."""
    return {
        "origin_ts": origin_ts,
        "last_ts": None,
        "last_idx": None,
        "n_lv": 0,
        "lv_price": np.empty(capacity, np.float64),
        "lv_side": np.empty(capacity, np.int64),
        "lv_kind": np.empty(capacity, np.int64),
        "lv_born": np.empty(capacity, np.int64),
        "lv_break": np.empty(capacity, np.int64),      # -1 == not broken yet
        "lv_extreme": np.empty(capacity, np.float64),
        "cur_day": np.int64(-1),
        "day_hi": np.nan,
        "day_lo": np.nan,
        "sess_hi": np.full(len(_SESSIONS), np.nan),
        "sess_lo": np.full(len(_SESSIONS), np.nan),
        "sess_on": np.zeros(len(_SESSIONS), np.bool_),
    }


def _ensure_capacity(st: dict, n_new_bars: int) -> None:
    need = st["n_lv"] + _GROW * n_new_bars
    cap = len(st["lv_price"])
    if need <= cap:
        return
    new_cap = max(need, 2 * cap)
    for k in ("lv_price", "lv_side", "lv_kind", "lv_born", "lv_break", "lv_extreme"):
        arr = np.empty(new_cap, st[k].dtype)
        arr[:st["n_lv"]] = st[k][:st["n_lv"]]
        st[k] = arr


@_njit(cache=False)
def _run_bars(start, n, h, l, c, day, hour, hmax, lmin,
              swing_k, level_ttl, confirm_n, sess_start, sess_end,
              lv_price, lv_side, lv_kind, lv_born, lv_break, lv_extreme, n_lv,
              cur_day, day_hi, day_lo, sess_hi, sess_lo, sess_on,
              hit_price, hit_side, hit_break, hit_extreme):
    """Step the level/sweep machine over window bars [start, n).

    This is the old per-bar loop body (`_step_bar`, opt15 Task 10) written on
    arrays so numba can compile it; the walk order, the same-bar evaluation of
    freshly born levels, the kill rules and the confirmation harvest are
    unchanged (tests/test_s94_kernel_golden.py pins it against a trace recorded
    from the pure-Python version). Confirmations are harvested only on the last
    bar (n-1) into hit_* ; returns (n_lv, cur_day, day_hi, day_lo, n_hits)."""
    n_hits = 0
    for a in range(start, n):
        # -- prior-day levels roll at the UTC day boundary
        if day[a] != cur_day:
            if not np.isnan(day_hi):
                w = 0
                for i in range(n_lv):
                    if lv_kind[i] != 0:
                        if w != i:
                            lv_price[w] = lv_price[i]; lv_side[w] = lv_side[i]
                            lv_kind[w] = lv_kind[i]; lv_born[w] = lv_born[i]
                            lv_break[w] = lv_break[i]; lv_extreme[w] = lv_extreme[i]
                        w += 1
                n_lv = w
                lv_price[n_lv] = day_hi; lv_side[n_lv] = 1; lv_kind[n_lv] = 0
                lv_born[n_lv] = a; lv_break[n_lv] = -1; lv_extreme[n_lv] = 0.0
                n_lv += 1
                lv_price[n_lv] = day_lo; lv_side[n_lv] = -1; lv_kind[n_lv] = 0
                lv_born[n_lv] = a; lv_break[n_lv] = -1; lv_extreme[n_lv] = 0.0
                n_lv += 1
            cur_day = day[a]
            day_hi = h[a]
            day_lo = l[a]
            for si in range(sess_on.shape[0]):
                sess_on[si] = False
        else:
            day_hi = max(day_hi, h[a])
            day_lo = min(day_lo, l[a])

        # -- completed-session levels
        for si in range(sess_on.shape[0]):
            if sess_start[si] <= hour[a] < sess_end[si]:
                if not sess_on[si]:
                    sess_on[si] = True
                    sess_hi[si] = h[a]
                    sess_lo[si] = l[a]
                else:
                    sess_hi[si] = max(sess_hi[si], h[a])
                    sess_lo[si] = min(sess_lo[si], l[a])
            elif sess_on[si] and hour[a] >= sess_end[si]:
                sess_on[si] = False
                lv_price[n_lv] = sess_hi[si]; lv_side[n_lv] = 1; lv_kind[n_lv] = 1
                lv_born[n_lv] = a; lv_break[n_lv] = -1; lv_extreme[n_lv] = 0.0
                n_lv += 1
                lv_price[n_lv] = sess_lo[si]; lv_side[n_lv] = -1; lv_kind[n_lv] = 1
                lv_born[n_lv] = a; lv_break[n_lv] = -1; lv_extreme[n_lv] = 0.0
                n_lv += 1

        # -- swing fractals, confirmed swing_k bars later
        j = a - swing_k
        if j >= swing_k:
            if h[j] == hmax[j]:
                lv_price[n_lv] = h[j]; lv_side[n_lv] = 1; lv_kind[n_lv] = 2
                lv_born[n_lv] = a; lv_break[n_lv] = -1; lv_extreme[n_lv] = 0.0
                n_lv += 1
            if l[j] == lmin[j]:
                lv_price[n_lv] = l[j]; lv_side[n_lv] = -1; lv_kind[n_lv] = 2
                lv_born[n_lv] = a; lv_break[n_lv] = -1; lv_extreme[n_lv] = 0.0
                n_lv += 1

        # -- sweep / confirm walk, compacting killed levels in place
        last = a == n - 1
        n_hits = 0
        w = 0
        for i in range(n_lv):
            keep = True
            if lv_kind[i] != 0 and a - lv_born[i] > level_ttl:
                keep = False
            elif lv_break[i] < 0:
                if lv_side[i] == 1 and h[a] > lv_price[i]:
                    lv_break[i] = a; lv_extreme[i] = h[a]
                elif lv_side[i] == -1 and l[a] < lv_price[i]:
                    lv_break[i] = a; lv_extreme[i] = l[a]
            else:
                if lv_side[i] == 1:
                    lv_extreme[i] = max(lv_extreme[i], h[a])
                    back = c[a] < lv_price[i]
                else:
                    lv_extreme[i] = min(lv_extreme[i], l[a])
                    back = c[a] > lv_price[i]
                if back:
                    if last:
                        hit_price[n_hits] = lv_price[i]; hit_side[n_hits] = lv_side[i]
                        hit_break[n_hits] = lv_break[i]; hit_extreme[n_hits] = lv_extreme[i]
                        n_hits += 1
                    keep = False
                elif a - lv_break[i] >= confirm_n:
                    keep = False
            if keep:
                if w != i:
                    lv_price[w] = lv_price[i]; lv_side[w] = lv_side[i]
                    lv_kind[w] = lv_kind[i]; lv_born[w] = lv_born[i]
                    lv_break[w] = lv_break[i]; lv_extreme[w] = lv_extreme[i]
                w += 1
        n_lv = w
    return n_lv, cur_day, day_hi, day_lo, n_hits


def _detect(w5m: pd.DataFrame, now_utc: datetime) -> None:
    """Advance the level/sweep state machine and arm a setup for any sweep whose
    CONFIRMATION lands on the last closed bar.

    INCREMENTAL (opt15 Task 10): the machine (`_state`) is persisted across
    ticks. When the window origin is unchanged and the last-processed bar is
    still in the window, only bars newer than `last_ts` are stepped; otherwise
    the machine is rebuilt from born=0 over the whole window (window slid /
    first call / any inconsistency). Confirmations are harvested ONLY at the
    window's last bar -- exactly as the old full-window walk did -- so the armed
    pendings are identical either way (tests/test_s94_parity.py is the gate)."""
    global _pending, _state, _ttl_warned

    t = w5m["time"]
    bar_time = t.iloc[-1]
    origin_ts = t.iloc[0]
    n = len(w5m)
    last = n - 1

    # one-time WARN: a window shorter than the full level-universe depth sees a
    # TRUNCATED level set vs the offline backtest (fewer old swing/session
    # levels) -- the module's conceded live<->backtest divergence.
    if not _ttl_warned and n < _LEVEL_TTL:
        _ttl_warned = True
        log.warning(
            "S94 window %d M5 bars < level TTL %d: level universe truncated vs "
            "backtest (older swing/session levels missing). Raise "
            "RESEARCH_WIN_5M toward %d for full parity.",
            n, _LEVEL_TTL, _LEVEL_TTL,
        )

    # dedup: this exact closed bar (same origin) was already processed
    if (_state is not None and _state["last_ts"] is not None
            and _state["origin_ts"] == origin_ts and bar_time == _state["last_ts"]):
        return

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

    # swing fractals, confirmed _SWING_K bars later (no look-ahead). Recomputed
    # over the whole (stable-origin) window each call -- a cheap vectorized pass;
    # a bar's centered value depends only on [j-K, j+K], which are stable closed
    # bars, so it equals the full-rebuild value for every stepped bar.
    roll = 2 * _SWING_K + 1
    hmax = pd.Series(h).rolling(roll, center=True).max().to_numpy()
    lmin = pd.Series(l).rolling(roll, center=True).min().to_numpy()

    # Incremental if the origin is unchanged AND the last-processed bar is still
    # at its stored position with the stored timestamp AND at least one new bar
    # exists; else full rebuild from scratch.
    start_idx = None
    if (_state is not None and _state["origin_ts"] == origin_ts
            and _state["last_idx"] is not None):
        li = _state["last_idx"]
        if 0 <= li < last and t.iloc[li] == _state["last_ts"]:
            start_idx = li + 1
    if start_idx is None:
        _state = _new_state(origin_ts)
        start_idx = 0

    st = _state
    _ensure_capacity(st, n - start_idx)
    cap = len(st["lv_price"])
    hit_price = np.empty(cap, np.float64)
    hit_side = np.empty(cap, np.int64)
    hit_break = np.empty(cap, np.int64)
    hit_extreme = np.empty(cap, np.float64)
    (st["n_lv"], st["cur_day"], st["day_hi"], st["day_lo"], n_hits) = _run_bars(
        start_idx, n, h, l, c, day, hour, hmax, lmin,
        _SWING_K, _LEVEL_TTL, _CONFIRM_N, _SESS_START, _SESS_END,
        st["lv_price"], st["lv_side"], st["lv_kind"], st["lv_born"], st["lv_break"],
        st["lv_extreme"], st["n_lv"], st["cur_day"], st["day_hi"], st["day_lo"],
        st["sess_hi"], st["sess_lo"], st["sess_on"],
        hit_price, hit_side, hit_break, hit_extreme)
    confirmed = [dict(price=float(hit_price[i]),
                      side="high" if hit_side[i] == _SIDE_HIGH else "low",
                      break_i=int(hit_break[i]), extreme=float(hit_extreme[i]))
                 for i in range(n_hits)]
    _state["last_ts"] = bar_time
    _state["last_idx"] = last

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
