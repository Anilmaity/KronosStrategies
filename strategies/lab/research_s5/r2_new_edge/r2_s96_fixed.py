"""r2_s96_fixed.py -- s96_h1_momentum with the nominal-entry defect removed (PROTOCOL §2).

Two replacement `get_signal` functions, applied to the real module through
`Cfg.patch={"get_signal": ...}` so `lab.harness.replay` drives `s96_h1_momentum` with the
same CONFIG / cooldown / session handling and only the signal function swapped:

  get_signal_fixed  (arm A)  -- identical H1 logic (Donchian 24 on closed H1 bars, EMA20/50
      bias, ATR(14) stop 3.0x, TP 0.4R) but (i) at most ONE signal per closed H1 bar and
      (ii) entry_price = the newest M1 close (the market at signal time). SL/TP stay anchored
      to the H1 close, as the live bot would place them from the module's own numbers.
  get_signal_stop   (arm B)  -- during the forming hour, the first M1 close beyond the
      Donchian(24) extreme of the prior 24 CLOSED H1 bars, in the direction of the closed-H1
      EMA20/50 bias, fires at that M1 close; stop 3.0xATR(14, closed H1) and TP 0.4R from the
      entry; one signal per H1 bar.

State: the H1 bar timestamp of the last signal. A replay starts from 2024 again, so the
state is reset whenever time moves backwards (the harness has no reset hook for s96).
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from backtest_strategies import s96_h1_momentum as S
from backtest_strategies.base import Signal

_state = {"last_h1": None, "last_now": None}


def _reset_if_rewound(now_utc: datetime) -> None:
    if _state["last_now"] is not None and now_utc < _state["last_now"]:
        _state["last_h1"] = None
    _state["last_now"] = now_utc


def _h1_frame(w15m):
    h1 = S._resample_h1(w15m)
    if len(h1) < S._MIN_H1:
        return None
    return h1


def _levels(h1):
    c = h1["close"].reset_index(drop=True)
    h = h1["high"].reset_index(drop=True)
    lo = h1["low"].reset_index(drop=True)
    ef = S.ema(c, S._EMA_FAST)
    es = S.ema(c, S._EMA_SLOW)
    a = S.atr(h1.reset_index(drop=True), S._ATR_N)
    i = len(c) - 1
    A = float(a.iloc[i])
    if not (A > 0) or pd.isna(ef.iloc[i]) or pd.isna(es.iloc[i]):
        return None
    return dict(A=A, up=float(ef.iloc[i]) > float(es.iloc[i]), close=float(c.iloc[i]),
                donch_hi=float(h.iloc[i - S._N:i].max()), donch_lo=float(lo.iloc[i - S._N:i].min()),
                # for the stop-entry arm: the extreme of the last 24 CLOSED bars (incl. bar i)
                donch_hi_next=float(h.iloc[i - S._N + 1:i + 1].max()),
                donch_lo_next=float(lo.iloc[i - S._N + 1:i + 1].min()),
                bar_time=h1.index[-1])


def get_signal_fixed(w1m, w5m, w15m: pd.DataFrame, now_utc: datetime) -> Signal | None:
    _reset_if_rewound(now_utc)
    if w15m is None or len(w15m) < S._MIN_H1 * 4 or w1m is None or len(w1m) == 0:
        return None
    h1 = _h1_frame(w15m)
    if h1 is None:
        return None
    L = _levels(h1)
    if L is None or L["bar_time"] == _state["last_h1"]:
        return None
    mkt = float(w1m["close"].iloc[-1])
    risk = S._K_ATR * L["A"]
    sig = None
    if L["close"] > L["donch_hi"] and L["up"]:
        sl, tp = round(L["close"] - risk, 2), round(L["close"] + S._TP_R * risk, 2)
        if sl < mkt < tp:
            sig = Signal(side="BUY", entry_price=mkt, stop_loss=sl, take_profit=tp, reason="S96_FIX_LONG")
    elif L["close"] < L["donch_lo"] and not L["up"]:
        sl, tp = round(L["close"] + risk, 2), round(L["close"] - S._TP_R * risk, 2)
        if tp < mkt < sl:
            sig = Signal(side="SELL", entry_price=mkt, stop_loss=sl, take_profit=tp, reason="S96_FIX_SHORT")
    # the closed bar is consumed whether or not it produced a tradeable signal
    _state["last_h1"] = L["bar_time"]
    return sig


def get_signal_stop(w1m, w5m, w15m: pd.DataFrame, now_utc: datetime) -> Signal | None:
    _reset_if_rewound(now_utc)
    if w15m is None or len(w15m) < S._MIN_H1 * 4 or w1m is None or len(w1m) == 0:
        return None
    h1 = _h1_frame(w15m)
    if h1 is None:
        return None
    L = _levels(h1)
    if L is None:
        return None
    # the forming hour is the one after the newest closed bar; one signal per forming hour
    forming = L["bar_time"] + pd.Timedelta(hours=1)
    if forming == _state["last_h1"]:
        return None
    mkt = float(w1m["close"].iloc[-1])
    risk = S._K_ATR * L["A"]
    if mkt > L["donch_hi_next"] and L["up"]:
        _state["last_h1"] = forming
        return Signal(side="BUY", entry_price=mkt, stop_loss=round(mkt - risk, 2),
                      take_profit=round(mkt + S._TP_R * risk, 2), reason="S96_STOP_LONG")
    if mkt < L["donch_lo_next"] and not L["up"]:
        _state["last_h1"] = forming
        return Signal(side="SELL", entry_price=mkt, stop_loss=round(mkt + risk, 2),
                      take_profit=round(mkt - S._TP_R * risk, 2), reason="S96_STOP_SHORT")
    return None
