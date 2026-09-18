"""parity_s14.py -- H8c: s14_ob_mit_bias live signals on 2026-09-18 vs the module driven over the same
bars under both window alignments (same method as parity.py; bars built the same way)."""
from __future__ import annotations
import sys
from datetime import timezone
from pathlib import Path
import numpy as np
import pandas as pd
HERE = Path(__file__).resolve().parent; STRAT = HERE.parents[2]; sys.path.insert(0, str(STRAT))
SCRATCH = Path("/private/tmp/claude-501/-Users-anil-Projects-Kronos/979109dc-9856-4311-91b0-14d2dda5b135/scratchpad")
CACHE = STRAT / "backtest" / "results" / "bars_cache_2y"; DAY = "2026-09-18"
from backtest_strategies import s14_ob_mit_bias as mod
s5 = pd.read_parquet(STRAT / "backtest/results/bars_cache/s5/XAU_USD/2026-09.parquet")
s5["time"] = pd.to_datetime(s5.time, utc=True).dt.tz_convert(None); s5 = s5[s5.time >= DAY].set_index("time").sort_index()
def resample(tf):
    r = s5.resample(tf, label="left", closed="left").agg(o=("o","first"), h=("h","max"), l=("l","min"), c=("c","last"), volume=("volume","sum")).dropna()
    r = r.rename(columns={"o":"open","h":"high","l":"low","c":"close"}).reset_index()
    return r[r.time + pd.Timedelta(tf) <= s5.index.max() + pd.Timedelta(seconds=5)]
bars = {}
for tf, fn, rule in (("1m","is_XAU_USD_1m.parquet","1min"),("5m","is_XAU_USD_5m.parquet","5min"),("15m","is_XAU_USD_15m.parquet","15min")):
    d = pd.read_parquet(CACHE / fn); d["time"] = pd.to_datetime(d.time, utc=True).dt.tz_convert(None)
    bars[tf] = pd.concat([d[["time","open","high","low","close","volume"]], resample(rule)], ignore_index=True).sort_values("time").reset_index(drop=True)
m1, m5, m15 = bars["1m"], bars["5m"], bars["15m"]
t1 = m1.time.to_numpy("datetime64[ns]"); t5 = m5.time.to_numpy("datetime64[ns]"); t15 = m15.time.to_numpy("datetime64[ns]")
WIN = dict(w1m=60, w5m=80, w15m=100)
def drive(closed_only):
    rows = []; lag5 = np.timedelta64(4 if closed_only else 0, "m"); lag15 = np.timedelta64(14 if closed_only else 0, "m")
    for i in range(int(np.searchsorted(t1, np.datetime64(f"{DAY}T07:00"))), len(m1)):
        now = (pd.Timestamp(t1[i]) + pd.Timedelta(minutes=1)).to_pydatetime().replace(tzinfo=timezone.utc)
        j5 = int(np.searchsorted(t5, t1[i] - lag5, side="right")); j15 = int(np.searchsorted(t15, t1[i] - lag15, side="right"))
        sig = mod.get_signal(m1.iloc[max(0, i-WIN["w1m"]+1): i+1].reset_index(drop=True), m5.iloc[max(0, j5-WIN["w5m"]): j5].reset_index(drop=True), m15.iloc[max(0, j15-WIN["w15m"]): j15].reset_index(drop=True), now)
        if sig: rows.append(dict(bar=pd.Timestamp(t1[i]), side_sim=sig.side, entry=sig.entry_price, sl=sig.stop_loss, tp=sig.take_profit))
    return pd.DataFrame(rows)
leaky, sim = drive(False), drive(True)
live = pd.read_csv(SCRATCH / "s14_live_signals_today.csv", parse_dates=["signal_at"]); live = live[live.signal_at < f"{DAY} 10:31"].copy()
live["bar"] = live.signal_at.dt.tz_convert(None).dt.floor("min") - pd.Timedelta(minutes=1)
pd.set_option("display.width", 250)
for name, s in (("shared-harness alignment", leaky), ("closed-bar alignment", sim)):
    m = live.merge(s, on="bar", how="outer", indicator=True)
    print(f"\n=== s14 {name}: sim signals in 07:00-10:30 = {len(s)}; live rows = {len(live)} ===")
    print(m[["bar","_merge","side","side_sim","entry_price","entry","stop_loss","sl","take_profit","tp","status","rejection_reason"]].to_string(index=False))
