"""H-live (extension): signal-level parity on the May-June 2026 live record of Research
OB_MIT_BIAS (780 signal rows, runner BEFORE the 2026-07-06 fix that dropped the newest
closed bar of every frame). For every M1 bar in 2026-05-19 .. 2026-06-18 07-16 UTC, call the
real get_signal under three window conventions and match to live rows on
(bar minute, side, stop_loss to the cent):
  open       lab/harness.py convention (forming M5/M15 bar included)
  closed     bars closed at the M1 close (today's runner)
  closed_lag old runner: every frame one extra closed bar behind (M1 ends at i-1)
A live row at signal_at HH:MM:SS is attributed to bar HH:(MM-1) for open/closed, and to bar
HH:(MM-2) for closed_lag.
"""
import sys
from pathlib import Path
from datetime import timezone
import numpy as np, pandas as pd
_HERE = Path(__file__).resolve().parent; _STRAT = _HERE.parents[2]
sys.path.insert(0, str(_STRAT)); sys.path.insert(0, str(_HERE))
from harness_closed import load_bars
from backtest_strategies import s14_ob_mit_bias as mod

bars = load_bars(tfs=("1m", "5m", "15m"), cache=_STRAT / "backtest/results/bars_cache_2y")
m1, m5, m15 = bars["1m"], bars["5m"], bars["15m"]
t1 = m1.time.to_numpy("datetime64[ns]"); t5 = m5.time.to_numpy("datetime64[ns]"); t15 = m15.time.to_numpy("datetime64[ns]")
i0 = int(np.searchsorted(t1, np.datetime64("2026-05-19"))); i1 = int(np.searchsorted(t1, np.datetime64("2026-06-19")))
rows = []
for i in range(i0, i1):
    h = pd.Timestamp(t1[i]).hour
    if not (7 <= h < 16):
        continue
    now = pd.Timestamp(t1[i]).to_pydatetime().replace(tzinfo=timezone.utc)
    for conv in ("open", "closed", "closed_lag"):
        if conv == "open":
            k = i; j5 = int(np.searchsorted(t5, t1[i], "right")); j15 = int(np.searchsorted(t15, t1[i], "right"))
        elif conv == "closed":
            k = i; j5 = int(np.searchsorted(t5, t1[i] - np.timedelta64(4, "m"), "right")); j15 = int(np.searchsorted(t15, t1[i] - np.timedelta64(14, "m"), "right"))
        else:
            k = i - 1; j5 = int(np.searchsorted(t5, t1[i] - np.timedelta64(9, "m"), "right")); j15 = int(np.searchsorted(t15, t1[i] - np.timedelta64(29, "m"), "right"))
        w1m = m1.iloc[max(0, k - 700 + 1): k + 1]; w5m = m5.iloc[max(0, j5 - 160): j5]; w15m = m15.iloc[max(0, j15 - 100): j15]
        sig = mod.get_signal(w1m, w5m, w15m, now)
        if sig:
            rows.append(dict(conv=conv, bar=pd.Timestamp(t1[i]), side=sig.side, sl=round(sig.stop_loss, 2), entry=sig.entry_price))
sim = pd.DataFrame(rows)
live = pd.read_csv(_HERE / "results/live_signals.csv", parse_dates=["signal_at"])
live = live[(live.signal_at >= "2026-05-19") & (live.signal_at < "2026-06-19")].copy()
live["minute"] = live.signal_at.dt.tz_convert(None).dt.floor("min")
live["sl"] = live.stop_loss.round(2)
print(f"live rows: {len(live)}  (placed {int((live.status=='PLACED').sum())})")
for conv in ("open", "closed", "closed_lag"):
    s = sim[sim.conv == conv].copy()
    # the bar the live row reacted to: closed/open -> the minute before signal_at; lag -> two before
    lag = 2 if conv == "closed_lag" else 1
    s["minute"] = s.bar + pd.Timedelta(minutes=lag)
    key = ["minute", "side", "sl"]
    m = live.merge(s[key].drop_duplicates(), on=key, how="left", indicator=True)
    hit = (m._merge == "both").mean()
    # also match on (minute, side) only and on entry price to the cent
    m2 = live.merge(s[["minute", "side", "entry"]].drop_duplicates(), left_on=["minute", "side", "entry_price"], right_on=["minute", "side", "entry"], how="left", indicator=True)
    print(f"{conv:11s} sim signals {len(s):5d} | live rows reproduced (minute+side+sl) {100*hit:5.1f}% | (minute+side+entry) {100*(m2._merge=='both').mean():5.1f}%")
    # reverse: sim signals that live never logged (live logs a row per get_signal hit unless cooldown/position blocked -> loose)
sim.to_csv(_HERE / "results/parity_mayjune_sim.csv", index=False)
