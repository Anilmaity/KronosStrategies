"""r2_htf_gate.py -- the TTrades higher-timeframe gate as an overlay (PROTOCOL §5).

Wrappers around the REAL get_signal of c03_fvg_fill / s14_ob_mit_bias, applied through
`Cfg.patch={"get_signal": <wrapper>}`. The wrapper calls the original and admits the signal
only if its side agrees with:
  G1 -- the CURRENT UTC daily candle so far: newest M1 close vs today's open (first M15 bar
        at/after 00:00 UTC in w15m). "Do not fade the current daily candle."
  G2 -- the PREVIOUS UTC daily candle: yesterday's close vs open (from the M15 bars of that
        day in w15m). "Trade in the direction of the previous candle."
Equal / undefined -> reject. Needs win_15m >= 200 (48 h of M15).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from concept_strategies import c03_fvg_fill as _C03
from backtest_strategies import s14_ob_mit_bias as _S14

_ORIG = {"c03": _C03.get_signal, "s14": _S14.get_signal}


def _daily_dirs(w1m, w15m):
    """(current-day direction, previous-day direction) as +1 / -1 / 0 (0 = undefined/flat)."""
    t = pd.to_datetime(w15m["time"]).to_numpy("datetime64[ns]")
    day = t.astype("datetime64[D]")
    today = day[-1]
    yday = today - np.timedelta64(1, "D")
    o = w15m["open"].to_numpy(float); c = w15m["close"].to_numpy(float)
    cur = 0
    m = day == today
    if m.any():
        open_today = float(o[np.argmax(m)])
        last = float(w1m["close"].iloc[-1])
        cur = int(np.sign(last - open_today))
    prev = 0
    # previous trading day = the newest day in the window strictly before today (skips weekends)
    before = day[day < today]
    if len(before):
        pd_day = before[-1]
        mp = day == pd_day
        if mp.sum() >= 4:
            prev = int(np.sign(float(c[np.where(mp)[0][-1]]) - float(o[np.argmax(mp)])))
    return cur, prev


def _gate(orig, which, w1m, w5m, w15m, now_utc):
    sig = orig(w1m, w5m, w15m, now_utc)
    if sig is None:
        return None
    cur, prev = _daily_dirs(w1m, w15m)
    want = cur if which == "G1" else prev
    side = 1 if sig.side == "BUY" else -1
    return sig if want == side else None


def c03_g1(w1m, w5m, w15m, now_utc):
    return _gate(_ORIG["c03"], "G1", w1m, w5m, w15m, now_utc)


def c03_g2(w1m, w5m, w15m, now_utc):
    return _gate(_ORIG["c03"], "G2", w1m, w5m, w15m, now_utc)


def s14_g1(w1m, w5m, w15m, now_utc):
    return _gate(_ORIG["s14"], "G1", w1m, w5m, w15m, now_utc)


def s14_g2(w1m, w5m, w15m, now_utc):
    return _gate(_ORIG["s14"], "G2", w1m, w5m, w15m, now_utc)
