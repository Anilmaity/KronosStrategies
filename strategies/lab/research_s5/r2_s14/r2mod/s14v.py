"""r2mod/s14v.py -- parameterised copy of s14_ob_mit_bias (= s03_ob_mitigation + 15m EMA21 bias).

At the defaults below it is logic-identical to backtest_strategies.s14_ob_mit_bias (verified
by replay, see REPORT.md). Every knob is a module constant so the harness's Cfg.patch reaches it.
Knobs (default = live):
  DISP_MULT      1.8    displacement body multiple for detect_order_blocks
  OB_LOOKBACK    60     M5 bars scanned for OBs
  OB_LAST_N      10     only the newest N OBs are candidates (s03: obs[-10:])
  MIN_RISK/MAX_RISK 0.5/5  OB zone height admitted
  FIRST_TOUCH    False  True -> reject an OB whose zone was already entered by a CLOSED M5 bar
                        after its displacement bar (TTrades: a mitigated block is spent)
  MAX_AGE_BARS   None   reject OBs older than this many M5 bars (None = no limit)
  ZONE           'wick' 'wick' = OB candle [low, high] (live); 'body' = [min(o,c), max(o,c)]
  SL_MODE        'edge' 'edge' = zone edge -/+ SL_BUF (live); 'swing' = extreme of the SWING_N M5
                        bars ending at the OB candle -/+ SL_BUF (protected swing)
  SL_BUF         0.3
  SWING_N        5
  TP_R           2.0    target = TP_R x (entry - stop)
  BIAS_TF        '15m'  '15m' = EMA on w15m closes (live); '5m' = on w5m closes; '1h' = on w15m
                        resampled to H1 (closed H1 bars only)
  BIAS_LEN       21
  RANDOM_GATE    None   control: accept each raw s14 signal with this probability (seeded by the
                        signal bar time) -- a concept-free gate with a matched rejection rate
"""
from __future__ import annotations
import hashlib
import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig
from strategy.ict_engine import detect_order_blocks

NAME = "OB_MIT_BIAS_R2"
CONFIG = StrategyConfig(name=NAME, description="r2_s14 parameterised s14", cooldown_s=300,
                        session_start_hour=7, session_end_hour=16)

DISP_MULT = 1.8
OB_LOOKBACK = 60
OB_LAST_N = 10
MIN_RISK = 0.5
MAX_RISK = 5.0
FIRST_TOUCH = False
MAX_AGE_BARS = None
ZONE = "wick"
SL_MODE = "edge"
SL_BUF = 0.3
SWING_N = 5
TP_R = 2.0
BIAS_TF = "15m"
BIAS_LEN = 21
RANDOM_GATE = None


def _bias(w5m, w15m):
    if BIAS_TF == "15m":
        c = w15m
    elif BIAS_TF == "5m":
        c = w5m
    elif BIAS_TF == "1h":
        if w15m is None or len(w15m) < 8:
            return None
        d = w15m.set_index("time")["close"].astype(float)
        h = d.resample("1h", label="left", closed="left").last().dropna()
        # drop the trailing H1 bucket unless it is complete (4 M15 bars in it)
        cnt = d.resample("1h", label="left", closed="left").count()
        if len(h) and cnt.loc[h.index[-1]] < 4:
            h = h.iloc[:-1]
        c = pd.DataFrame({"close": h.values})
    else:
        raise ValueError(BIAS_TF)
    if c is None or len(c) < BIAS_LEN + 1:
        return None
    closes = c["close"].astype(float)
    ema = closes.ewm(span=BIAS_LEN, adjust=False).mean().iloc[-1]
    last = float(closes.iloc[-1])
    if last > ema:
        return "BULL"
    if last < ema:
        return "BEAR"
    return None


def _ob_signal(w1m, w5m):
    if w5m is None or len(w5m) < 20 or len(w1m) < 5:
        return None
    tail = w5m.tail(OB_LOOKBACK)
    obs = detect_order_blocks(tail, displacement_mult=DISP_MULT)
    if not obs:
        return None
    current_px = float(w1m.iloc[-1]["close"])
    t5 = tail["time"].to_numpy()
    lo5 = tail["low"].to_numpy(float); hi5 = tail["high"].to_numpy(float)
    op5 = tail["open"].to_numpy(float); cl5 = tail["close"].to_numpy(float)
    n5 = len(tail)
    for ob in reversed(obs[-OB_LAST_N:]):
        # index of the OB candle inside `tail`
        k = int((t5 == ob.time).nonzero()[0][0]) if (t5 == ob.time).any() else None
        if ZONE == "body" and k is not None:
            z_lo, z_hi = min(op5[k], cl5[k]), max(op5[k], cl5[k])
        else:
            z_lo, z_hi = ob.zone_low, ob.zone_high
        if not (z_lo <= current_px <= z_hi):
            continue
        risk_unit = abs(ob.zone_high - ob.zone_low)          # s03 sizes the zone on the wick range
        if risk_unit < MIN_RISK or risk_unit > MAX_RISK:
            continue
        if k is not None:
            age = n5 - 1 - k                                   # bars since the OB candle (displacement = k+1)
            if MAX_AGE_BARS is not None and age > MAX_AGE_BARS:
                continue
            if FIRST_TOUCH and k + 2 < n5:
                later_lo, later_hi = lo5[k + 2:], hi5[k + 2:]
                touched = (later_lo <= z_hi).any() if ob.type == "bullish" else (later_hi >= z_lo).any()
                if touched:
                    continue
        if SL_MODE == "swing" and k is not None:
            a = max(0, k - SWING_N + 1)
            ref_lo, ref_hi = float(lo5[a:k + 1].min()), float(hi5[a:k + 1].max())
        else:
            ref_lo, ref_hi = ob.zone_low, ob.zone_high
        if ob.type == "bullish":
            sl = round(ref_lo - SL_BUF, 2)
            tp = round(current_px + TP_R * (current_px - sl), 2)
            return Signal("BUY", round(current_px, 2), sl, tp, "OB_BULL")
        sl = round(ref_hi + SL_BUF, 2)
        tp = round(current_px - TP_R * (sl - current_px), 2)
        return Signal("SELL", round(current_px, 2), sl, tp, "OB_BEAR")
    return None


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    sig = _ob_signal(w1m, w5m)
    if sig is None:
        return None
    bias = _bias(w5m, w15m)
    if bias is None:
        return None
    if sig.side == "BUY" and bias != "BULL":
        return None
    if sig.side == "SELL" and bias != "BEAR":
        return None
    if RANDOM_GATE is not None:
        h = hashlib.md5(str(w1m.iloc[-1]["time"]).encode()).hexdigest()
        if int(h[:8], 16) / 0xFFFFFFFF >= RANDOM_GATE:
            return None
    return Signal(side=sig.side, entry_price=sig.entry_price, stop_loss=sig.stop_loss,
                  take_profit=sig.take_profit, reason=f"OB_BIAS_{sig.side}")
