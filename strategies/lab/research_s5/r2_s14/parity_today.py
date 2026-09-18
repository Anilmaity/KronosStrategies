"""H-live: harness-vs-live signal parity for 2026-09-18 (first live half-day of the 3.0 floor).

Builds M1/M5/M15 for 2026-09-18 from the S5 cache (mid o/h/l/c, aggregated exactly like the
2y cache: start-labelled), appends them to the 2y cache frames, and walks every M1 bar from
07:00 UTC calling the REAL s14 get_signal under both window conventions:
  open   = lab/harness.py (forming M5/M15 bar included with full OHLC)
  closed = only bars closed at the M1 close (research_runner's complete-candle feed)
Lists every raw signal (before gates) and compares to the prod DB signal rows saved in
results/live_signals.csv. S5 coverage ends 10:30:55 UTC, so live signals after that cannot
be replayed -- they are reported as 'no data'.
"""
import sys, os
from pathlib import Path
from datetime import timezone
import numpy as np, pandas as pd
_HERE = Path(__file__).resolve().parent; _STRAT = _HERE.parents[2]
sys.path.insert(0, str(_STRAT)); sys.path.insert(0, str(_HERE))
import importlib
from harness_closed import load_bars
from backtest_strategies import s14_ob_mit_bias as mod

CACHE = _STRAT / "backtest/results/bars_cache_2y"
bars = load_bars(tfs=("1m", "5m", "15m"), cache=CACHE)
s5 = pd.read_parquet(_STRAT / "backtest/results/bars_cache/s5/XAU_USD/2026-09.parquet")
s5["time"] = pd.to_datetime(s5.time, utc=True).dt.tz_convert(None)
s5 = s5[s5.time > bars["1m"].time.max()]            # only what the 2y cache lacks


def agg(df, rule):
    g = df.set_index("time").resample(rule, label="left", closed="left")
    out = pd.DataFrame({"open": g["o"].first(), "high": g["h"].max(), "low": g["l"].min(),
                        "close": g["c"].last(), "volume": g["volume"].sum()}).dropna(subset=["open"])
    return out.reset_index()

m1x, m5x, m15x = agg(s5, "1min"), agg(s5, "5min"), agg(s5, "15min")
# the 2y cache's last M5/M15 bars may be partial vs the S5 tail: keep cache rows, add only later starts
for tf, x in (("1m", m1x), ("5m", m5x), ("15m", m15x)):
    x = x[x.time > bars[tf].time.max()]
    bars[tf] = pd.concat([bars[tf][["time", "open", "high", "low", "close", "volume"]], x], ignore_index=True)
    bars[tf]["time"] = bars[tf]["time"].astype("datetime64[ns]")
print("bars end:", {tf: str(bars[tf].time.max()) for tf in bars})

m1, m5, m15 = bars["1m"], bars["5m"], bars["15m"]
t1 = m1.time.to_numpy("datetime64[ns]"); t5 = m5.time.to_numpy("datetime64[ns]"); t15 = m15.time.to_numpy("datetime64[ns]")
i0 = int(np.searchsorted(t1, np.datetime64("2026-09-18T07:00"))); i1 = len(m1)
rows = []
for conv in ("open", "closed"):
    for i in range(i0, i1):
        now = pd.Timestamp(t1[i]).to_pydatetime().replace(tzinfo=timezone.utc)
        if conv == "closed":
            j5 = int(np.searchsorted(t5, t1[i] - np.timedelta64(4, "m"), "right"))
            j15 = int(np.searchsorted(t15, t1[i] - np.timedelta64(14, "m"), "right"))
        else:
            j5 = int(np.searchsorted(t5, t1[i], "right")); j15 = int(np.searchsorted(t15, t1[i], "right"))
        w1m = m1.iloc[max(0, i - 700 + 1): i + 1]; w5m = m5.iloc[max(0, j5 - 160): j5]; w15m = m15.iloc[max(0, j15 - 100): j15]
        sig = mod.get_signal(w1m, w5m, w15m, now)
        if sig:
            rows.append(dict(conv=conv, bar=str(t1[i])[:16], side=sig.side, entry=sig.entry_price, sl=sig.stop_loss, tp=sig.take_profit,
                             dist=round(abs(sig.entry_price - sig.stop_loss), 2)))
sim = pd.DataFrame(rows)
pd.set_option("display.width", 200)
print("\n=== harness raw signals (before gates), both conventions, 2026-09-18 07:00 -> data end ===")
print(sim.to_string(index=False) if len(sim) else "none")
live = pd.read_csv(_HERE / "results/live_signals.csv", parse_dates=["signal_at"])
live = live[live.signal_at >= "2026-09-18"]
print("\n=== live signals (prod DB) ===")
print(live[["signal_at", "side", "entry_price", "stop_loss", "take_profit", "status", "rejection_reason"]].to_string(index=False))
sim.to_csv(_HERE / "results/parity_today_sim.csv", index=False)
