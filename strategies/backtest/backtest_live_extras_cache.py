"""
backtest_live_extras_cache.py
-----------------------------
Backtest the live strategies that were NOT in last night's research-family
run (`all_strategies_cache_mp_*.csv`):

  VAR1       liquidity_scalper (5m)
  VAR2       liquidity_sweep   (15m/5m/1m)
  VAR3       micro_scalper     (1m/5m)
  ICT_S2     xauusd_strategies/s02_fvg_fill   (15m)
  ICT_S4     xauusd_strategies/s04_breaker    (15m)
  ICT_S6     xauusd_strategies/s06_daily_crt  (1d)

Loads ticks once via cache_loader (.history_data/XAU_USD/*.json), resamples
to each TF, runs all six, writes a single combined CSV that matches the
research CSV schema:
  strategy, entry_time, side, entry_px, sl, tp, exit_px, exit_time,
  outcome, pnl_pts, reason

Run:  python backtest/backtest_live_extras_cache.py
"""
from __future__ import annotations

import csv
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, ROOT)

from backtest.cache_loader import load_ticks, resample
from backtest.backtest_engine import replay_var1, replay_var2
from xauusd_strategies import s02_fvg_fill, s04_breaker, s06_daily_crt
from xauusd_strategies.engine import run_backtest as ict_run, atr as ict_atr

SYMBOL = "XAU_USD"

# ─── VAR1 / VAR2 / VAR3 params (mirror live runners) ─────────────────────────
VAR1_PARAMS = dict(
    fixed_tp=4.0, sl_buffer=1.0, dynamic_range=15.0, min_wick_ratio=2.0,
    n_pools=3, pool_lookback=3, cooldown_s=30,
)
VAR2_PARAMS = dict(
    fixed_tp=5.0, sl_buffer=1.0, dynamic_range=15.0, min_wick_ratio=2.0,
    n_pools=3, pool_lookback=3, entry_window=3, cooldown_s=60, pool_tp_only=True,
)
VAR3_PARAMS = dict(
    tp_points=1.5, sl_points=1.0, cooldown_s=60,
    min_wick_ratio=1.3, min_avg_range=0.5, counter_trend_max_dist=4.0,
    n_pools=5, pool_lookback=2,
    equal_pools_only=False, kill_zones_only=False,
    daily_loss_limit=-15.0, daily_profit_target=50.0,
)


# ─── VAR3 replay (mirrors backtest_micro.replay, but parameterized) ──────────
def _to_utc(ts):
    if isinstance(ts, pd.Timestamp):
        ts = ts.to_pydatetime()
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


def replay_var3(c1m, c5m, p=VAR3_PARAMS):
    from strategy.scalper_engine import get_micro_signal, is_session_active
    trades = []
    last_entry_ts = None
    daily_realized = {}
    c5m = c5m.reset_index(drop=True)
    c5m_times = c5m["time"]
    n = len(c1m)
    for i in range(30, n - 1):
        candle = c1m.iloc[i]
        ct = candle["time"]
        cdt = _to_utc(ct)
        sdate = cdt.date()
        if not is_session_active(cdt, kill_zones_only=p["kill_zones_only"]):
            continue
        if last_entry_ts is not None and (cdt - last_entry_ts).total_seconds() < p["cooldown_s"]:
            continue
        realized = daily_realized.get(sdate, 0.0)
        if realized <= p["daily_loss_limit"] or realized >= p["daily_profit_target"]:
            continue
        w1 = c1m.iloc[max(0, i - 60):i + 1]
        cutoff = c5m_times.searchsorted(ct, side="right")
        w5 = c5m.iloc[max(0, cutoff - 30):cutoff]
        sig = get_micro_signal(
            w1, w5,
            tp_points=p["tp_points"], sl_points=p["sl_points"],
            min_wick_ratio=p["min_wick_ratio"], min_avg_range=p["min_avg_range"],
            counter_trend_max_dist=p["counter_trend_max_dist"],
            n_pools=p["n_pools"], pool_lookback=p["pool_lookback"],
            equal_pools_only=p["equal_pools_only"],
            kill_zones_only=p["kill_zones_only"],
            now_utc=cdt, require_session=False,
        )
        if sig is None:
            continue
        future = c1m.iloc[i + 1:]
        outcome, exit_px, exit_time = _sim_exit_1m(sig.side, sig.entry_price, sig.take_profit, sig.stop_loss, future)
        pnl = (exit_px - sig.entry_price) if sig.side == "BUY" else (sig.entry_price - exit_px)
        trades.append({
            "strategy": "VAR3",
            "entry_time": cdt,
            "side": sig.side,
            "entry_px": sig.entry_price,
            "sl": sig.stop_loss,
            "tp": sig.take_profit,
            "exit_px": round(float(exit_px), 2),
            "exit_time": exit_time,
            "outcome": outcome,
            "pnl_pts": round(float(pnl), 2),
            "reason": "VAR3_MICRO",
        })
        daily_realized[sdate] = realized + pnl
        last_entry_ts = cdt
    return trades


def _sim_exit_1m(direction, entry, tp, sl, future):
    for _, row in future.iterrows():
        if direction == "BUY":
            if row["low"] <= sl:
                return "SL", sl, row["time"]
            if row["high"] >= tp:
                return "TP", tp, row["time"]
        else:
            if row["high"] >= sl:
                return "SL", sl, row["time"]
            if row["low"] <= tp:
                return "TP", tp, row["time"]
    last = future.iloc[-1] if not future.empty else None
    return ("OPEN",
            float(last["close"]) if last is not None else entry,
            last["time"] if last is not None else datetime.now(timezone.utc))


# ─── ICT (S2/S4/S6) backtest: use xauusd_strategies.engine.run_backtest ──────
def _ict_to_rows(strategy_name: str, trades_obj, reason: str):
    rows = []
    for t in trades_obj:
        side = "BUY" if t.side == "long" else "SELL"
        pnl = (t.exit - t.entry) if t.side == "long" else (t.entry - t.exit)
        rows.append({
            "strategy": strategy_name,
            "entry_time": t.entry_time.to_pydatetime() if hasattr(t.entry_time, "to_pydatetime") else t.entry_time,
            "side": side,
            "entry_px": round(float(t.entry), 2),
            "sl": round(float(t.stop), 2),
            "tp": round(float(t.tp), 2),
            "exit_px": round(float(t.exit), 2),
            "exit_time": t.exit_time.to_pydatetime() if hasattr(t.exit_time, "to_pydatetime") else t.exit_time,
            "outcome": "TP" if t.reason == "tp" else ("SL" if t.reason == "sl" else "OPEN"),
            "pnl_pts": round(float(pnl), 2),
            "reason": reason,
        })
    return rows


def run_ict_s2(c15m_indexed):
    df = s02_fvg_fill.build_features(c15m_indexed, s02_fvg_fill.DEFAULTS["h4_ema"])
    det = s02_fvg_fill.make_detector(**{**s02_fvg_fill.DEFAULTS, "stop_buffer_atr": 0.3})
    return _ict_to_rows("ICT_S2_FVG", ict_run(df, det, valid_bars=96), "ICT_S2_FVG")


def run_ict_s4(c15m_indexed):
    det = s04_breaker.make_detector(**{**s04_breaker.DEFAULTS, "stop_buffer_atr": 0.3})
    return _ict_to_rows("ICT_S4_BREAKER", ict_run(c15m_indexed, det, valid_bars=96), "ICT_S4_BREAKER")


def run_ict_s6(c1d_indexed):
    det = s06_daily_crt.make_detector(**{**s06_daily_crt.DEFAULTS, "stop_buffer_atr": 0.0})
    return _ict_to_rows("ICT_S6_DAILY_CRT", ict_run(c1d_indexed, det, valid_bars=1), "ICT_S6_DAILY_CRT")


# ─── normalize VAR1/VAR2 TradeRecord → dict ──────────────────────────────────
def _var12_to_rows(trades_obj, strategy_label: str):
    rows = []
    for t in trades_obj:
        d = asdict(t)
        rows.append({
            "strategy": strategy_label,
            "entry_time": d["entry_time"],
            "side": "BUY" if d["direction"] in ("long", "BUY") else "SELL",
            "entry_px": round(float(d["entry_price"]), 2),
            "sl": round(float(d["stop_loss"]), 2),
            "tp": round(float(d["take_profit"]), 2),
            "exit_px": round(float(d["exit_price"]), 2),
            "exit_time": d["exit_time"],
            "outcome": d["outcome"],
            "pnl_pts": round(float(d["pnl_points"]), 2),
            "reason": f"{strategy_label}_{d.get('pool_type','') or ''}".strip("_"),
        })
    return rows


def _to_indexed(df: pd.DataFrame) -> pd.DataFrame:
    """Convert flat tsdb-shaped df into DatetimeIndex df (UTC) for ICT engine."""
    if df.empty:
        return df
    out = df.copy()
    out["time"] = pd.to_datetime(out["time"], utc=True)
    out = out.set_index("time").sort_index()
    return out[["open", "high", "low", "close"]].astype(float)


def main():
    t0 = time.time()
    print(f"Loading cached ticks for {SYMBOL} …", flush=True)
    ticks = load_ticks(SYMBOL)
    if ticks.empty:
        print("No ticks in cache — abort.")
        return
    print(f"  loaded {len(ticks):,} ticks  ({ticks['time'].iloc[0]} → {ticks['time'].iloc[-1]})  [{time.time()-t0:.1f}s]")

    print("Resampling to 1m / 5m / 15m / 1d …", flush=True)
    c1m  = resample(ticks, "1m")
    c5m  = resample(ticks, "5m")
    c15m = resample(ticks, "15m")
    # Daily via pandas direct (cache_loader only supports up to 1h)
    series = ticks.set_index("time")["price"].sort_index()
    daily = pd.DataFrame({
        "open":   series.resample("1D").first(),
        "high":   series.resample("1D").max(),
        "low":    series.resample("1D").min(),
        "close":  series.resample("1D").last(),
        "volume": series.resample("1D").count(),
    }).dropna(subset=["open","close"]).reset_index()
    daily["time"] = daily["time"].dt.tz_convert(None).astype("datetime64[ns]")
    print(f"  1m={len(c1m):,}  5m={len(c5m):,}  15m={len(c15m):,}  1d={len(daily):,}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_dir = os.path.join(HERE, "results")
    os.makedirs(out_dir, exist_ok=True)
    CSV_HEADER = ["strategy","entry_time","side","entry_px","sl","tp",
                  "exit_px","exit_time","outcome","pnl_pts","reason"]

    def _write_partial(label, rows):
        path = os.path.join(out_dir, f"live_extras_{stamp}_{label}.csv")
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(CSV_HEADER)
            for r in rows:
                w.writerow([r[k] for k in CSV_HEADER])
        print(f"  [CSV] {path}  ({len(rows)} rows)", flush=True)
        return path

    all_rows = []

    # Run fast strategies first so partials land quickly; VAR3 (slowest) last.
    print("\n[ICT_S6_DAILY_CRT] replay …", flush=True)
    d_idx = _to_indexed(daily)
    s6_rows = run_ict_s6(d_idx); del d_idx
    print(f"  → {len(s6_rows)} trades  [{time.time()-t0:.1f}s]")
    _write_partial("ICT_S6", s6_rows); all_rows.extend(s6_rows)

    print("[ICT_S2_FVG] replay …", flush=True)
    c15m_idx = _to_indexed(c15m)
    s2_rows = run_ict_s2(c15m_idx)
    print(f"  → {len(s2_rows)} trades  [{time.time()-t0:.1f}s]")
    _write_partial("ICT_S2", s2_rows); all_rows.extend(s2_rows)

    print("[ICT_S4_BREAKER] replay …", flush=True)
    s4_rows = run_ict_s4(c15m_idx); del c15m_idx
    print(f"  → {len(s4_rows)} trades  [{time.time()-t0:.1f}s]")
    _write_partial("ICT_S4", s4_rows); all_rows.extend(s4_rows)

    print("[VAR1] replay …", flush=True)
    var1 = replay_var1(c5m, **VAR1_PARAMS)
    v1_rows = _var12_to_rows(var1, "VAR1"); del var1
    print(f"  → {len(v1_rows)} trades  [{time.time()-t0:.1f}s]")
    _write_partial("VAR1", v1_rows); all_rows.extend(v1_rows)

    print("[VAR2] replay …", flush=True)
    var2 = replay_var2(c15m, c5m, c1m, **VAR2_PARAMS)
    v2_rows = _var12_to_rows(var2, "VAR2"); del var2
    print(f"  → {len(v2_rows)} trades  [{time.time()-t0:.1f}s]")
    _write_partial("VAR2", v2_rows); all_rows.extend(v2_rows)

    print("[VAR3] replay …", flush=True)
    var3 = replay_var3(c1m, c5m)
    print(f"  → {len(var3)} trades  [{time.time()-t0:.1f}s]")
    _write_partial("VAR3", var3); all_rows.extend(var3)

    out_path = os.path.join(out_dir, f"live_extras_{stamp}.csv")
    with open(out_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_HEADER)
        for r in all_rows:
            w.writerow([r[k] for k in CSV_HEADER])
    print(f"\n[CSV] Saved {len(all_rows):,} trades -> {out_path}")
    print(f"[done] {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
