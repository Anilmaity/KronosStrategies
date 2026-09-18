"""parity.py -- H7b: c03's first live day (2026-09-18) vs the strategy driven over the same bars.

Bars: the 2y cache through 2026-09-17 plus 2026-09-18 00:00-10:30 UTC derived from the QA'd S5
cache (mid o/h/l/c resampled to M1/M5/M15, label = bar open, closed bars only). The variant
module at shipped constants (== shipped c03, H0) is called on EVERY closed M1 bar in the London
killzone with the live runner's windows (RESEARCH_WIN_1M 60 / 5M 80 / 15M 100 -- the defaults;
the box's values are not readable from here), exactly as research_runner does: no cooldown or
position cap, so the list is the full set of signals the runner would have evaluated, which is
what apis_strategysignal records (PLACED and REJECTED alike).
Live rows: scratchpad c03_live_signals.csv / c03_live_positions.csv (pulled read-only).
"""
from __future__ import annotations

import sys
from datetime import timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
STRAT = HERE.parents[2]
sys.path.insert(0, str(STRAT))
sys.path.insert(0, str(HERE))
SCRATCH = Path("/private/tmp/claude-501/-Users-anil-Projects-Kronos/979109dc-9856-4311-91b0-14d2dda5b135/scratchpad")
CACHE = STRAT / "backtest" / "results" / "bars_cache_2y"
DAY = "2026-09-18"

import c03r2  # noqa: E402

s5 = pd.read_parquet(STRAT / "backtest/results/bars_cache/s5/XAU_USD/2026-09.parquet")
s5["time"] = pd.to_datetime(s5.time, utc=True).dt.tz_convert(None)
s5 = s5[s5.time >= DAY].set_index("time").sort_index()
print(f"S5 today: {len(s5)} bars {s5.index.min()} -> {s5.index.max()}")


def resample(tf: str) -> pd.DataFrame:
    r = s5.resample(tf, label="left", closed="left").agg(o=("o", "first"), h=("h", "max"), l=("l", "min"), c=("c", "last"), volume=("volume", "sum")).dropna()
    r = r.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close"}).reset_index()
    # closed bars only: a bar is closed when its end <= the last S5 time + 5 s
    end = r.time + pd.Timedelta(tf)
    return r[end <= s5.index.max() + pd.Timedelta(seconds=5)]


bars = {}
for tf, fn, rule in (("1m", "is_XAU_USD_1m.parquet", "1min"), ("5m", "is_XAU_USD_5m.parquet", "5min"), ("15m", "is_XAU_USD_15m.parquet", "15min")):
    d = pd.read_parquet(CACHE / fn)
    d["time"] = pd.to_datetime(d.time, utc=True).dt.tz_convert(None)
    t = resample(rule)
    bars[tf] = pd.concat([d[["time", "open", "high", "low", "close", "volume"]], t], ignore_index=True).sort_values("time").reset_index(drop=True)
    print(tf, "last bar", bars[tf].time.iloc[-1])

m1, m5, m15 = bars["1m"], bars["5m"], bars["15m"]
WIN = dict(w1m=60, w5m=80, w15m=100)
t1 = m1.time.to_numpy("datetime64[ns]"); t5 = m5.time.to_numpy("datetime64[ns]"); t15 = m15.time.to_numpy("datetime64[ns]")
def drive(closed_only: bool) -> pd.DataFrame:
    """closed_only=False: the shared harness's alignment (M5/M15 bar in progress included);
    True: the runner's `complete` filter (bar included once its close <= this M1 bar's close)."""
    rows = []
    lag5 = np.timedelta64(4, "m") if closed_only else np.timedelta64(0, "m")
    lag15 = np.timedelta64(14, "m") if closed_only else np.timedelta64(0, "m")
    for i in range(int(np.searchsorted(t1, np.datetime64(f"{DAY}T06:55"))), len(m1)):
        now = (pd.Timestamp(t1[i]) + pd.Timedelta(minutes=1)).to_pydatetime().replace(tzinfo=timezone.utc)
        j5 = int(np.searchsorted(t5, t1[i] - lag5, side="right")); j15 = int(np.searchsorted(t15, t1[i] - lag15, side="right"))
        w1m = m1.iloc[max(0, i - WIN["w1m"] + 1): i + 1].reset_index(drop=True)
        w5m = m5.iloc[max(0, j5 - WIN["w5m"]): j5].reset_index(drop=True)
        w15m = m15.iloc[max(0, j15 - WIN["w15m"]): j15].reset_index(drop=True)
        sig = c03r2.get_signal(w1m, w5m, w15m, now)
        if sig:
            rows.append(dict(bar=pd.Timestamp(t1[i]), side=sig.side, entry=sig.entry_price, sl=sig.stop_loss, tp=sig.take_profit,
                             last5=pd.Timestamp(t5[j5 - 1]).strftime("%H:%M")))
    return pd.DataFrame(rows)


leaky = drive(False); sim = drive(True)
print("\n=== shared-harness alignment (M5 bar in progress included) ===")
print(leaky.to_string(index=False) if len(leaky) else "none")
print("\n=== closed-bar alignment (runner's complete filter) ===")
print(sim.to_string(index=False) if len(sim) else "none")

live = pd.read_csv(SCRATCH / "c03_live_signals.csv", parse_dates=["signal_at"])
live = live[live.signal_at >= DAY].copy()
live["bar"] = live.signal_at.dt.tz_convert(None).dt.floor("min") - pd.Timedelta(minutes=1)
print("\n=== live apis_strategysignal rows today ===")
print(live[["signal_at", "bar", "side", "entry_price", "stop_loss", "take_profit", "status", "rejection_reason"]].to_string(index=False))

m = live.merge(sim.rename(columns={"side": "side_sim"}), on="bar", how="outer", indicator=True)
print("\n=== minute-by-minute match ===")
m["d_entry"] = m.entry_price - m.entry; m["d_sl"] = m.stop_loss - m.sl; m["d_tp"] = m.take_profit - m.tp
print(m[["bar", "_merge", "side", "side_sim", "entry_price", "entry", "d_entry", "stop_loss", "sl", "d_sl", "take_profit", "tp", "d_tp", "status"]].to_string(index=False))

pos = pd.read_csv(SCRATCH / "c03_live_positions.csv", parse_dates=["created_at", "modified_at"])
pos = pos[pos.created_at >= DAY]
print("\n=== live positions today (realized_profit_loss is price x lots; x100 = USD) ===")
print(pos[["created_at", "modified_at", "avg_buy_price", "total_buy_quantity", "realized_profit_loss"]].to_string(index=False))
# what the harness would have booked for the two placed signals, on S5 quote (long: bid), from the S5 cache
from quote import resolve_frame  # noqa: E402
placed = live[live.status == "PLACED"].merge(sim.drop(columns=["side"]), on="bar")
if len(placed):
    tr = pd.DataFrame(dict(entry_time=pd.to_datetime(placed.bar, utc=True), side=placed.side, entry_px=placed.entry, sl=placed.sl, tp=placed.tp))
    q = resolve_frame(tr)
    q["live_fill"] = pos.avg_buy_price.to_numpy()[: len(q)]
    q["live_pts_per_lot"] = (pos.realized_profit_loss / pos.total_buy_quantity).to_numpy()[: len(q)]
    print("\n=== placed signals: harness quote-S5 resolution vs live ===")
    print(q[["entry_time", "side", "quote_outcome", "quote_pts0", "m1c", "nominal_gap", "drift_5s", "drift_15s", "drift_30s", "spread_entry", "live_fill", "live_pts_per_lot"]].to_string(index=False))
    print("harness entry_px:", tr.entry_px.tolist(), "live fills:", q.live_fill.tolist(), "-> live fill vs harness entry:", (q.live_fill - tr.entry_px).round(2).tolist())
