"""lab.harness Cfg.regime -- daily-SMA regime gate for NEW entries, look-ahead safe.

State at an entry time is taken from the newest DAILY bar that has already CLOSED
(bar time + 1 day <= now; the cache's daily bars are midnight-UTC aligned), compared to
the SMA of the last `regime_sma` closes ending at that bar. "above_sma20" admits entries
only while close > SMA; "below_sma20" only while close <= SMA. Warm-up days (SMA undefined)
admit nothing. Like `sides`, the gate is applied after get_signal(), so only invariants are
asserted on real replays."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

_STRAT_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "strategies"))
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from lab import harness as H                       # noqa: E402
from lab.harness import CACHE, Cfg, load_bars, replay   # noqa: E402

_has_cache = (CACHE / "is_XAU_USD_1m.parquet").exists()


def _daily(closes, start="2026-01-01"):
    t = pd.date_range(start, periods=len(closes), freq="1D")     # tz-naive UTC, midnight
    c = np.asarray(closes, float)
    return pd.DataFrame({"time": t, "open": c, "high": c + 1, "low": c - 1, "close": c})


def test_regime_state_uses_only_closed_daily_bars():
    # 25 rising closes -> above SMA20 from the 20th bar on
    d = _daily(np.arange(100, 125))
    rg = H.RegimeGate(d, sma=20, mode="above_sma20")
    day20_open = datetime(2026, 1, 20, 0, 0, tzinfo=timezone.utc)     # bar #19 (0-based) opens
    # during day 20 the newest CLOSED bar is #18 -> SMA20 undefined -> nothing admitted
    assert rg.admits(day20_open) is False
    assert rg.admits(datetime(2026, 1, 20, 23, 59, tzinfo=timezone.utc)) is False
    # from day 21 00:00 bar #19 has closed: 20 closes available, rising -> above
    assert rg.admits(datetime(2026, 1, 21, 0, 0, tzinfo=timezone.utc)) is True
    assert H.RegimeGate(d, sma=20, mode="below_sma20").admits(
        datetime(2026, 1, 21, 0, 0, tzinfo=timezone.utc)) is False


def test_regime_flips_with_the_series():
    up = list(np.arange(100, 130)); down = list(np.arange(129, 90, -1))
    d = _daily(up + down)
    above = H.RegimeGate(d, sma=20, mode="above_sma20")
    below = H.RegimeGate(d, sma=20, mode="below_sma20")
    t_up = datetime(2026, 1, 31, 12, tzinfo=timezone.utc)      # in the rising leg
    t_dn = datetime(2026, 3, 5, 12, tzinfo=timezone.utc)       # well into the falling leg
    assert above.admits(t_up) and not below.admits(t_up)
    assert below.admits(t_dn) and not above.admits(t_dn)
    # the two modes partition every timestamp once the SMA exists
    for k in range(21, 68):
        t = datetime(2026, 1, 1, tzinfo=timezone.utc) + pd.Timedelta(days=k, hours=7)
        assert above.admits(t) != below.admits(t)


def test_unknown_mode_rejected():
    with pytest.raises(ValueError, match="regime"):
        H.RegimeGate(_daily(np.arange(30)), sma=20, mode="sideways")


@pytest.mark.skipif(not _has_cache, reason="bars cache not present")
def test_replay_with_regime_partitions_entries_by_state():
    bars = load_bars(tfs=("1m", "5m", "15m", "1d"))
    W = dict(start="2026-04-01", end="2026-04-20")
    both = replay("s99_mss_fvg", bars, cfg=Cfg(), **W)
    ab = replay("s99_mss_fvg", bars, cfg=Cfg(regime="above_sma20"), **W)
    be = replay("s99_mss_fvg", bars, cfg=Cfg(regime="below_sma20"), **W)
    assert ab["n"] + be["n"] <= both["n"] + 5          # near-partition (concurrency shifts a few)
    assert ab["n"] > 0 and be["n"] > 0
    gate = H.RegimeGate(bars["1d"], sma=20, mode="above_sma20")
    assert all(gate.admits(t.to_pydatetime()) for t in ab["trades"].entry_time)
    assert not any(gate.admits(t.to_pydatetime()) for t in be["trades"].entry_time)
    assert ab["regime"] == "above_sma20" and both["regime"] is None


@pytest.mark.skipif(not _has_cache, reason="bars cache not present")
def test_regime_requires_daily_frame():
    with pytest.raises(ValueError, match="1d"):
        replay("s99_mss_fvg", load_bars(), cfg=Cfg(regime="above_sma20"),
               start="2026-04-01", end="2026-04-03")
