"""
backtest_combined_v2.py
=======================
Offline parity backtest for the kronos_combined_v2 single strategy, replaying
EXACTLY what the live runner does:
  * enlarged windows (WIN_15M / WIN_5M = 260) so the MR legs get their 205 bars,
  * per-leg dedup inside get_signal (one entry per 1m bar, the live cadence),
  * per-leg max-hold exits (Signal.max_hold_min) IN ADDITION to SL/TP,
  * AS-OF BOS daily bias (N20/N30) injected via the module's _BIAS_OVERRIDE hook
    (the live _daily_bias fetches *current* data, which is wrong for replay).

This measures the book you are actually deploying (one-entry-per-minute, with the
v15e-gated MR legs) — NOT the TradingSkills concurrent tick-engine figure
(+4869 / PF 1.70). Use it to sanity-check leg firing + sign before go-live, and
to re-validate per quarter.

Run (needs TSDB access):
  cd C:\\Projects\\PycharmProjects\\personal\\KronosStrategies\\strategies
  python -m backtest.backtest_combined_v2 --days 540
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from shared.tsdb_reader import fetch_candles
import backtest_strategies.kronos_combined_v2 as cv2

SYMBOL  = "XAU_USD"
WIN_1M  = 60
WIN_5M  = 260
WIN_15M = 260


def _to_utc(ts):
    if isinstance(ts, pd.Timestamp):
        ts = ts.to_pydatetime()
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


def build_asof_bias(daily: pd.DataFrame):
    """date -> (b20, b30): regime as of the PRIOR day's close (causal), matching
    TradingSkills ms_bias. Returns a dict for O(1) lookup during replay."""
    d = daily.copy()
    d["date"] = pd.to_datetime(d["time"]).dt.date
    out = {}
    for n, key in ((cv2.LONG_GATE_N, 0), (cv2.SHORT_GATE_N, 1)):
        c = d["close"].astype(float)
        prior_hh = d["high"].astype(float).rolling(n).max().shift(1)
        prior_ll = d["low"].astype(float).rolling(n).min().shift(1)
        reg = np.where(c > prior_hh, 1.0, np.where(c < prior_ll, -1.0, np.nan))
        reg = pd.Series(reg).ffill().fillna(0.0)
        bias = reg.shift(1).fillna(0.0)          # known at START of next day
        for dt_, b in zip(d["date"], bias.astype(int)):
            t = out.setdefault(dt_, [0, 0]); t[key] = int(b)
    return {k: (v[0], v[1]) for k, v in out.items()}


def simulate_exit(side, entry_px, tp, sl, max_hold_min,
                  highs, lows, closes, times, start_idx):
    """First of SL / TP / max-hold (in 1m bars) / end-of-data."""
    n = highs.shape[0]
    if start_idx >= n:
        return "OPEN", entry_px, times[-1]
    end = n if not max_hold_min else min(n, start_idx + int(max_hold_min))
    h = highs[start_idx:end]; lo = lows[start_idx:end]
    if side == "BUY":
        sl_hits, tp_hits = lo <= sl, h >= tp
    else:
        sl_hits, tp_hits = h >= sl, lo <= tp
    sl_idx = sl_hits.argmax() if sl_hits.any() else -1
    tp_idx = tp_hits.argmax() if tp_hits.any() else -1
    if sl_idx == -1 and tp_idx == -1:
        # no SL/TP within the hold window -> close at window end (timeout/EOD)
        j = end - 1
        reason = "TIME" if (max_hold_min and end == start_idx + int(max_hold_min)) else "OPEN"
        return reason, float(closes[j]), times[j]
    if sl_idx == -1:
        return "TP", tp, times[start_idx + tp_idx]
    if tp_idx == -1:
        return "SL", sl, times[start_idx + sl_idx]
    return ("SL", sl, times[start_idx + sl_idx]) if sl_idx <= tp_idx \
        else ("TP", tp, times[start_idx + tp_idx])


def run(days: int):
    print(f"Fetching {days}d of 1m/5m/15m/1d candles from TSDB …", flush=True)
    c1m  = fetch_candles("1m",  days=days, symbol=SYMBOL)
    c5m  = fetch_candles("5m",  days=days, symbol=SYMBOL)
    c15m = fetch_candles("15m", days=days, symbol=SYMBOL)
    cd   = fetch_candles("1d",  days=days + 40, symbol=SYMBOL)
    if c1m.empty or cd.empty:
        print("No candle data — is TSDB reachable / backfilled?")
        return
    print(f"Candles: 1m={len(c1m)} 5m={len(c5m)} 15m={len(c15m)} 1d={len(cd)}", flush=True)

    bias_map = build_asof_bias(cd)
    cv2._BIAS_OVERRIDE = lambda now: bias_map.get(now.date(), (0, 0))
    cv2._last_fire_bucket.clear()

    c5m = c5m.reset_index(drop=True); c15m = c15m.reset_index(drop=True)
    t5, t15 = c5m["time"], c15m["time"]
    H = c1m["high"].to_numpy(float); L = c1m["low"].to_numpy(float)
    C = c1m["close"].to_numpy(float); T = c1m["time"].to_numpy()
    n = len(c1m)

    trades = []
    for i in range(max(WIN_1M, 30), n - 1):
        t = c1m.iloc[i]["time"]; tdt = _to_utc(t)
        if not (cv2.CONFIG.session_start_hour <= tdt.hour < cv2.CONFIG.session_end_hour):
            continue
        w1m  = c1m.iloc[max(0, i - WIN_1M):i + 1]
        i5  = t5.searchsorted(t, side="right");  w5m  = c5m.iloc[max(0, i5 - WIN_5M):i5]
        i15 = t15.searchsorted(t, side="right");  w15m = c15m.iloc[max(0, i15 - WIN_15M):i15]
        try:
            sig = cv2.get_signal(w1m, w5m, w15m, tdt)
        except Exception as e:
            print(f"[err @ {tdt}] {type(e).__name__}: {e}")
            continue
        if sig is None:
            continue
        outcome, exit_px, exit_t = simulate_exit(
            sig.side, sig.entry_price, sig.take_profit, sig.stop_loss,
            sig.max_hold_min, H, L, C, T, i + 1)
        pnl = (exit_px - sig.entry_price) if sig.side == "BUY" else (sig.entry_price - exit_px)
        trades.append(dict(day=tdt.date(), leg=sig.reason.split(":")[0], side=sig.side,
                           outcome=outcome, pnl=round(pnl, 2)))

    if not trades:
        print("No trades fired.")
        return
    df = pd.DataFrame(trades)
    daily = df.groupby("day")["pnl"].sum()
    pos = df[df.pnl > 0]["pnl"].sum(); neg = -df[df.pnl < 0]["pnl"].sum()
    eq = daily.cumsum(); dd = float((eq - eq.cummax()).min())
    ndays = daily.shape[0]
    print(f"\n{'='*70}\nCOMBINED v2 (LIVE-shape replay)  pts = $ at 0.01 lot/leg")
    print(f"  trades={len(df)}  tpd={len(df)/ndays:.1f}  net={df.pnl.sum():.0f}  "
          f"PF={pos/neg if neg>0 else float('inf'):.2f}  maxDD={dd:.0f}  "
          f"win-days={(daily>0).mean()*100:.0f}%  days={ndays}")
    print("  per-leg:")
    for leg, g in df.groupby("leg"):
        gp = g[g.pnl > 0]["pnl"].sum(); gn = -g[g.pnl < 0]["pnl"].sum()
        print(f"    {leg:10s} trd={len(g):>4} net={g.pnl.sum():>7.0f} "
              f"PF={gp/gn if gn>0 else float('inf'):>5.2f}  "
              f"win%={(g.pnl>0).mean()*100:>4.0f}  ({g.side.iloc[0]})")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results",
                       f"combined_v2_{datetime.now():%Y%m%d_%H%M}.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    df.to_csv(out, index=False)
    print(f"  wrote {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=540)
    run(ap.parse_args().days)
