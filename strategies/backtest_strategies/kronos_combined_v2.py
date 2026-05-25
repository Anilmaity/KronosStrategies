r"""
kronos_combined_v2.py
=====================
Combined Suite v2 — the WHOLE two-sided XAU/USD scalping book, deployed as ONE
strategy (per the user's request), ported from
``TradingSkills/backtest/combined_suite_v2.py`` (+ ``dev/mom_scalping_final.py``).

ARCHITECTURE (one ``get_signal`` multiplexes every leg)
-------------------------------------------------------
LONG book
  * MR base   — the project's proven mean-reversion portfolio, REUSED verbatim
                from the already-live Kronos legs (S02/S05/S06/S07/S14). Each is
                called and its Signal re-tagged with this suite's per-leg max-hold.
  * Fades     — the momentum-EXHAUSTION long fades E1/E2/E3 (ported from
                dev/mom_fade_suite.py): buy deep downside extensions, wide target.
  Counter-trend LONGS are DROPPED on a confirmed-downtrend day
  (break-of-structure daily bias, N=LONG_GATE_N == -1).
SHORT side
  * SE1 failed-breakout fade : close > prior-20-bar high  -> SELL
  * SE2 Bollinger-upper fade : close > SMA20 + 2.1*std20  -> SELL
  Fired ONLY on a DURABLE downtrend day (BOS bias N=SHORT_GATE_N == -1).

EXITS  (this framework is SL/TP-trigger + the new TIME_EXIT trigger)
  * Per-leg ATR-scaled stop/target (computed at entry -> fixed price levels).
  * Per-leg max-hold (minutes) carried on Signal.max_hold_min -> place_entry
    creates a CUSTOM/TIME_EXIT trigger; position_monitor closes on elapsed time.
  Per-leg max-holds (TradingSkills dev/per_leg_maxhold.py): fades 120, S02/S05/S14
  480, S06/S07 240, SE1/SE2 480.

CONCURRENCY
  CONFIG.max_concurrent_positions lets this one strategy hold several legs open at
  once (the validated book is concurrent). The runner emits at most one NEW entry
  per 1m bar; per-leg dedup (below) prevents a leg re-firing within the same HTF
  bar; legs that lose a same-minute collision stay eligible next minute.

DELIBERATE DIVERGENCES FROM THE TradingSkills BACKTEST  (read before sizing up)
------------------------------------------------------------------------------
1. MR base reuses the LIVE Kronos legs, which carry the v15e PRODUCTION gates
   (TNX |z|>=0.5, ±2h event window, D1/H4 bias, liquidity zones). The v2 backtest
   figure (+4869 / PF 1.70) used the UN-gated MR legs, so expect the live MR
   sub-book to trade LESS and differ from that number. This is the safer choice
   (battle-tested code) but it is a real fidelity gap.
2. archD (TNX-calm ROC-thrust continuation) and SH (momo short) are OFF by
   default: archD was ABANDONED in research (no config net-positive in both
   segments) and its TNX-calm gate was later shown to be in-sample bias; SH was
   deliberately dropped from live in v15e. Flip ENABLE_* to include them.
3. Live fills are MARKET-at-signal vs the backtest's next-bar-open tick fill, and
   there is no EOD auto-flat (session gating + max-holds bound overnight risk).
4. One NEW entry per 1m bar (vs backtest concurrent same-bar entries) — a minor
   frequency haircut on busy bars.

Run live:  research_runner with RESEARCH_STRATEGY=kronos_combined_v2 and
           RESEARCH_WIN_15M>=210, RESEARCH_WIN_5M>=210 (legs need 205 bars).
"""
from __future__ import annotations

import logging
import time as _time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig
from backtest_strategies import _kronos_indicators as ki

# Reuse the already-live Kronos MR legs verbatim.
from backtest_strategies import kronos_s02_stoch_revert as _s02
from backtest_strategies import kronos_s05_threebar_pull as _s05
from backtest_strategies import kronos_s06_session_sweep as _s06
from backtest_strategies import kronos_s07_crt as _s07
from backtest_strategies import kronos_s14_m5_ema_stretch as _s14

log = logging.getLogger("kronos_combined_v2")

# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────
NAME = "KRONOS_COMBINED_V2"
CONFIG = StrategyConfig(
    name=NAME,
    description=(
        "Combined Suite v2 — full two-sided XAU/USD book as one strategy: MR base "
        "(S02/S05/S06/S07/S14, reused live legs) + long fades (E1/E2/E3) + bear-day "
        "short fades (SE1/SE2); BOS daily-bias gate (N20 longs / N30 shorts); "
        "per-leg ATR exits + max-holds. Port of TradingSkills combined_suite_v2.py."
    ),
    cooldown_s=20,                 # low: legs may enter on successive 1m bars
    session_start_hour=7,          # 07:00 UTC  == TradingSkills SESSION (420,1260)
    session_end_hour=21,           # entries allowed for hour < 21 (i.e. <= 20:59)
    max_concurrent_positions=12,   # the book holds several legs open at once
)

# Leg toggles ------------------------------------------------------------------
ENABLE_MR     = True
ENABLE_FADES  = True
ENABLE_SHORTS = True
ENABLE_ARCHD  = False   # abandoned in research (no both-segment-+ config) + TNX in-sample bias
ENABLE_SH     = False   # dropped from live in v15e (anti-predictive depth + DD penalty)

# BOS daily-bias gates ---------------------------------------------------------
LONG_GATE_N  = 20       # drop counter-trend LONGS on BOS(N)=-1 days
SHORT_GATE_N = 30       # fire SHORTS only on BOS(N)=-1 days (durable downtrend)

# Per-leg max-hold (minutes) ---------------------------------------------------
MAXHOLD = {
    "S02": 480, "S05": 480, "S06": 240, "S07": 240, "S14": 480,
    "E1": 120, "E2": 120, "E3": 120,
    "SE1": 480, "SE2": 480,
}

# Fade / short geometry (TradingSkills FADE_PICKS / combined_suite_v2 shorts) ---
_FADE_STOP_ATR = 2.0
_FADE_TGT_ATR  = 2.5
_BB_N, _BB_K   = 20, 2.1
_E1_LOOKBACK   = 20
_E3_STOCH_N, _E3_STOCH_LVL = 14, 5
_SE_STOP_ATR   = 2.0
_SE_TGT_ATR    = 2.5
_ATR_N         = 14

# ──────────────────────────────────────────────────────────────────────────────
# Per-leg dedup: a leg may fire at most once per HTF bar.
# Bucket = now_utc floored to the leg's timeframe. Only the RETURNED leg's bucket
# is recorded, so legs that lose a same-minute collision stay eligible next minute.
# ──────────────────────────────────────────────────────────────────────────────
_last_fire_bucket: dict[str, str] = {}


def _bucket(now_utc: datetime, tf_min: int) -> str:
    m = (now_utc.minute // tf_min) * tf_min
    return now_utc.strftime(f"%Y%m%d%H") + f"{m:02d}" + f"_{tf_min}"


def _eligible(leg: str, now_utc: datetime, tf_min: int) -> bool:
    return _last_fire_bucket.get(leg) != _bucket(now_utc, tf_min)


def _mark(leg: str, now_utc: datetime, tf_min: int) -> None:
    _last_fire_bucket[leg] = _bucket(now_utc, tf_min)


# ──────────────────────────────────────────────────────────────────────────────
# BOS daily bias — live equivalent of TradingSkills ms_bias(N).
# Fetches daily XAU/USD candles, drops the still-forming day, and reads the
# prior-N-day high/low break regime as of the last CLOSED day (causal == today's
# bias). Cached once per UTC date. Fails to NEUTRAL (0) on any error.
# ──────────────────────────────────────────────────────────────────────────────
_bias_cache: dict[str, object] = {"date": None, "n20": 0, "n30": 0}

# Offline-backtest hook ONLY: when set to a callable now_utc -> (b20, b30) the
# live TSDB daily fetch is bypassed (a historical replay must use AS-OF bias, not
# today's). Always None in production — never set by the live runner.
_BIAS_OVERRIDE = None


def _regime_last(daily: pd.DataFrame, n: int) -> int:
    """Regime as of the last CLOSED daily bar: +1 break above prior-N high,
    -1 break below prior-N low, else carry forward (ffill). 0 if undecided."""
    if daily is None or len(daily) < n + 2:
        return 0
    c = daily["close"].astype(float)
    prior_hh = daily["high"].astype(float).rolling(n).max().shift(1)
    prior_ll = daily["low"].astype(float).rolling(n).min().shift(1)
    reg = np.where(c > prior_hh, 1.0, np.where(c < prior_ll, -1.0, np.nan))
    reg = pd.Series(reg, index=daily.index).ffill().fillna(0.0)
    return int(reg.iloc[-1])


def _daily_bias(now_utc: datetime) -> tuple[int, int]:
    """Return (bias_N20, bias_N30) for today. Cached per UTC date."""
    if _BIAS_OVERRIDE is not None:          # offline backtest only
        b20, b30 = _BIAS_OVERRIDE(now_utc)
        return int(b20), int(b30)
    today = now_utc.date().isoformat()
    if _bias_cache["date"] == today:
        return int(_bias_cache["n20"]), int(_bias_cache["n30"])
    try:
        from shared.tsdb_reader import fetch_candles
        d = fetch_candles("1d", days=160)
        if d is None or d.empty:
            raise ValueError("no daily candles")
        # Drop the still-forming current UTC day so we only read CLOSED bars.
        d = d.copy()
        d["d"] = pd.to_datetime(d["time"]).dt.date
        d = d[d["d"] < now_utc.date()]
        b20 = _regime_last(d, LONG_GATE_N)
        b30 = _regime_last(d, SHORT_GATE_N)
        _bias_cache.update(date=today, n20=b20, n30=b30)
        log.info("[CV2] daily BOS bias for %s: N20=%+d N30=%+d (%d closed days)",
                 today, b20, b30, len(d))
        return b20, b30
    except Exception:
        log.exception("[CV2] daily-bias fetch failed — defaulting NEUTRAL (longs "
                      "unrestricted, no shorts)")
        _bias_cache.update(date=today, n20=0, n30=0)
        return 0, 0


# ──────────────────────────────────────────────────────────────────────────────
# Indicator helpers (M5), matching TradingSkills dev/mom_fade_suite.py
# ──────────────────────────────────────────────────────────────────────────────
def _atr_m5(df: pd.DataFrame) -> pd.Series:
    return ki.atr(df, _ATR_N).clip(lower=0.1)


def _tag(side: str, entry: float, sl: float, tp: float, leg: str, why: str) -> Signal:
    return Signal(
        side=side,
        entry_price=round(float(entry), 2),
        stop_loss=round(float(sl), 2),
        take_profit=round(float(tp), 2),
        reason=f"CV2_{leg}:{why}",
        max_hold_min=MAXHOLD.get(leg),
    )


# ── Long fades E1/E2/E3 (M5, LONG only) ───────────────────────────────────────
def _fade_signal(w5m: pd.DataFrame, leg: str) -> Signal | None:
    if w5m is None or len(w5m) < max(_BB_N, _E1_LOOKBACK, _E3_STOCH_N) + 3:
        return None
    d = w5m.reset_index(drop=True)
    atr = _atr_m5(d)
    a = float(atr.iloc[-1])
    if not np.isfinite(a) or a <= 0:
        return None
    close = float(d["close"].iloc[-1])

    fire = False
    if leg == "E1":   # failed-breakout fade: close below prior-20-bar low (excl. signal bar)
        ll_prev = d["low"].rolling(_E1_LOOKBACK, min_periods=_E1_LOOKBACK).min().shift(1)
        fire = bool(close < float(ll_prev.iloc[-1])) if pd.notna(ll_prev.iloc[-1]) else False
    elif leg == "E2":  # Bollinger lower-band fade
        mid = ki.sma(d["close"], _BB_N)
        sd = d["close"].rolling(_BB_N, min_periods=_BB_N).std()
        if pd.notna(mid.iloc[-1]) and pd.notna(sd.iloc[-1]):
            fire = bool(close < float(mid.iloc[-1]) - _BB_K * float(sd.iloc[-1]))
    elif leg == "E3":  # stochastic-extreme fade
        st = ki.stoch(d, _E3_STOCH_N)
        fire = bool(float(st.iloc[-1]) < _E3_STOCH_LVL) if pd.notna(st.iloc[-1]) else False

    if not fire:
        return None
    sl = close - _FADE_STOP_ATR * a
    tp = close + _FADE_TGT_ATR * a
    return _tag("BUY", close, sl, tp, leg, "long_fade")


# ── Short fades SE1/SE2 (M5, SHORT only) ──────────────────────────────────────
def _short_signal(w5m: pd.DataFrame, leg: str) -> Signal | None:
    if w5m is None or len(w5m) < _BB_N + 3:
        return None
    d = w5m.reset_index(drop=True)
    atr = _atr_m5(d)
    a = float(atr.iloc[-1])
    if not np.isfinite(a) or a <= 0:
        return None
    close = float(d["close"].iloc[-1])

    fire = False
    if leg == "SE1":   # failed-breakout fade: close above prior-20-bar high (excl. signal bar)
        hh_prev = d["high"].rolling(20, min_periods=20).max().shift(1)
        fire = bool(close > float(hh_prev.iloc[-1])) if pd.notna(hh_prev.iloc[-1]) else False
    elif leg == "SE2":  # Bollinger upper-band fade
        mid = ki.sma(d["close"], _BB_N)
        sd = d["close"].rolling(_BB_N, min_periods=_BB_N).std()
        if pd.notna(mid.iloc[-1]) and pd.notna(sd.iloc[-1]):
            fire = bool(close > float(mid.iloc[-1]) + _BB_K * float(sd.iloc[-1]))

    if not fire:
        return None
    sl = close + _SE_STOP_ATR * a
    tp = close - _SE_TGT_ATR * a
    return _tag("SELL", close, sl, tp, leg, "short_fade")


# ── MR base: reuse the live Kronos legs, re-tag with this suite's max-hold ─────
_MR_LEGS = [
    ("S02", _s02.get_signal, 15),
    ("S05", _s05.get_signal, 15),
    ("S06", _s06.get_signal, 15),
    ("S07", _s07.get_signal, 15),
    ("S14", _s14.get_signal, 5),
]


def _retag(base: Signal, leg: str) -> Signal:
    return Signal(
        side=base.side,
        entry_price=base.entry_price,
        stop_loss=base.stop_loss,
        take_profit=base.take_profit,
        reason=f"CV2_{leg}:{base.reason}",
        max_hold_min=MAXHOLD.get(leg),
    )


# Evaluation order (priority on same-minute collisions): rarer/bear-regime legs
# first so they are not starved by the frequent long fades.
_EVAL_ORDER = ["SE1", "SE2", "S06", "S07", "S02", "S05", "S14", "E1", "E2", "E3"]
_LEG_TF = {"S02": 15, "S05": 15, "S06": 15, "S07": 15, "S14": 5,
           "E1": 5, "E2": 5, "E3": 5, "SE1": 5, "SE2": 5}


def get_signal(w1m, w5m, w15m, now_utc: datetime) -> Signal | None:
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    bias20, bias30 = _daily_bias(now_utc)

    for leg in _EVAL_ORDER:
        tf = _LEG_TF[leg]
        if not _eligible(leg, now_utc, tf):
            continue

        sig: Signal | None = None

        if leg in ("S02", "S05", "S06", "S07", "S14"):
            if not ENABLE_MR:
                continue
            legfn = dict((k, fn) for k, fn, _ in _MR_LEGS)[leg]
            base = legfn(w1m, w5m, w15m, now_utc)
            if base is not None:
                sig = _retag(base, leg)
        elif leg in ("E1", "E2", "E3"):
            if not ENABLE_FADES:
                continue
            sig = _fade_signal(w5m, leg)
        elif leg in ("SE1", "SE2"):
            if not ENABLE_SHORTS:
                continue
            if bias30 != -1:          # shorts only on a durable downtrend day
                continue
            sig = _short_signal(w5m, leg)

        if sig is None:
            continue

        # LONG gate: drop counter-trend BUYS on a confirmed-downtrend day.
        if sig.side == "BUY" and bias20 == -1:
            continue

        _mark(leg, now_utc, tf)
        log.info("[CV2 FIRE] %s %s entry=%.2f sl=%.2f tp=%.2f mh=%s (bias N20=%+d N30=%+d)",
                 leg, sig.side, sig.entry_price, sig.stop_loss, sig.take_profit,
                 sig.max_hold_min, bias20, bias30)
        return sig

    return None
