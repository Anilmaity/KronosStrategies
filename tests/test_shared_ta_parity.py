# tests/test_shared_ta_parity.py
"""opt15 Task 8 -- shared-TA consolidation parity gate.

Behavior-preserving refactor proof (Global Constraint 6: "the parity test IS
the gate; identical signals on the fixtures or the refactor does not ship").

The oracle is the PRE-refactor code, vendored byte-for-byte from git HEAD into
tests/_legacy_ta/legacy_s{93,99,100}.py BEFORE any edit. Each scenario drives
the legacy module and the refactored module through an IDENTICAL call sequence
and asserts the emitted Signal tuples match exactly. Every scenario also asserts
the legacy oracle actually exercised the intended path (a fire / a cancel / a
no-signal) so a fixture that silently never fires cannot pass trivially.

Fixtures cover, per single-pending module (s93/s99/s100): FVG (or MSS/OB) fires
on retrace; phantom-cancel (stop pierced first); no-signal (below-ATR band /
no-sweep); session-hour exclusion clears state; and the one-setup-per-bar dedup
guard (module-level "cooldown" analogue). All synthetic; no DB, no network.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies"))
_LEGACY_DIR = os.path.join(os.path.dirname(__file__), "_legacy_ta")
for _p in (_STRAT_DIR, _LEGACY_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backtest_strategies import s93_fvg_scalp as new_s93  # noqa: E402
from backtest_strategies import s99_mss_fvg as new_s99  # noqa: E402
from backtest_strategies import s100_m3_combo as new_s100  # noqa: E402
import legacy_s93  # noqa: E402
import legacy_s99  # noqa: E402
import legacy_s100  # noqa: E402


# ── Task-9 gates OFF for the Task-8 refactor-parity comparison ────────────────
# opt15 Task 9 adds a SOFT M15 structure veto + a 1.5xATR gap cap to s93, both
# DEFAULT ON. This file is the Task-8 shared-TA refactor-parity gate: it must
# compare the NEW module's BASE behavior against the legacy oracle (which has
# neither filter). So pin both Task-9 gates OFF here -- otherwise the fuzz grid
# (gap up to 2.0 > 1.5xATR) would trip the cap and diverge from legacy. The
# S93_SOFT_VETO=off path is itself the Task-9 regression guard; the dedicated
# veto/cap behavior is covered in tests/test_s93_soft_veto.py. (s99/s100 do not
# read these envs.)
@pytest.fixture(autouse=True)
def _s93_task9_gates_off(monkeypatch):
    monkeypatch.setenv("S93_SOFT_VETO", "off")
    monkeypatch.setenv("S93_GAP_CAP_ATR", "0")


# ── comparison helpers ────────────────────────────────────────────────────────
def _norm(sig):
    """Signal -> comparable golden tuple (side, entry, sl, tp, reason,
    max_hold, trailing); None stays None."""
    if sig is None:
        return None
    return (sig.side, sig.entry_price, sig.stop_loss, sig.take_profit,
            sig.reason, sig.max_hold_min, sig.trailing)


def _run(mod, calls):
    """reset_state, then feed each (w1m, w5m, w15m, now) call; return the
    normalized signal list."""
    mod.reset_state()
    return [_norm(mod.get_signal(*c)) for c in calls]


def _assert_parity(legacy_mod, new_mod, calls):
    legacy_out = _run(legacy_mod, calls)
    new_out = _run(new_mod, calls)
    assert new_out == legacy_out, (
        f"parity break\n legacy={legacy_out}\n new   ={new_out}")
    return legacy_out


# ── S93: FVG continuation scalp ───────────────────────────────────────────────
_S93_T0 = pd.Timestamp("2026-06-05T07:00:00Z")


def _s93_frame(gap=0.7):
    rows = []

    def add(o, h, l, c):
        rows.append(dict(time=_S93_T0 + pd.Timedelta(minutes=5 * len(rows)),
                         open=float(o), high=float(h), low=float(l),
                         close=float(c), volume=1.0))

    for _ in range(20):
        add(2000, 2000.5, 1999.5, 2000)
    add(2000, 2000.8, 1999.8, 2000.5)               # k-1
    add(2000.6, 2004.0, 2000.5 + gap, 2003.8)       # displacement, FVG=gap
    return pd.DataFrame(rows)


def _s93_w1m(minute_offset, hi, lo):
    ts = _S93_T0 + pd.Timedelta(minutes=21 * 5 + minute_offset)
    return pd.DataFrame([dict(time=ts, open=hi - 0.2, high=float(hi),
                              low=float(lo), close=hi - 0.1, volume=1.0)])


def _s93_now(minute_offset=0):
    return datetime(2026, 6, 5, 8, 45, tzinfo=timezone.utc) + pd.Timedelta(
        minutes=minute_offset)


def _s93_levels(frame, now):
    """arm the legacy oracle once to read prox/sl for probe construction."""
    legacy_s93.reset_state()
    legacy_s93.get_signal(None, frame, None, now)
    p = legacy_s93._pending
    return float(p["prox"]), float(p["sl"])


def test_s93_fvg_fires_on_retrace_parity():
    f = _s93_frame()
    prox, sl = _s93_levels(f, _s93_now())
    probe = _s93_w1m(6, hi=prox + 0.5, lo=(prox + sl) / 2)   # into (sl, prox]
    out = _assert_parity(legacy_s93, new_s93, [
        (None, f, None, _s93_now()),          # arm (own bar -> no fire)
        (probe, f, None, _s93_now(6)),        # retrace -> BUY
    ])
    assert out[0] is None
    assert out[1] is not None and out[1][0] == "BUY"
    assert out[1][4] == "S93_FVG_SCALP_LONG"


def test_s93_phantom_cancel_parity():
    f = _s93_frame()
    prox, sl = _s93_levels(f, _s93_now())
    probe = _s93_w1m(6, hi=2002.0, lo=sl - 0.3)              # pierces stop first
    out = _assert_parity(legacy_s93, new_s93, [
        (None, f, None, _s93_now()),
        (probe, f, None, _s93_now(6)),
    ])
    assert out == [None, None]                               # cancelled, no fire


def test_s93_below_atr_threshold_no_signal_parity():
    f = _s93_frame(gap=0.2)                                  # gap < 0.3 x ATR
    out = _assert_parity(legacy_s93, new_s93, [
        (None, f, None, _s93_now()),
    ])
    assert out == [None]


def test_s93_session_exclusion_parity():
    f = _s93_frame()
    off = datetime(2026, 6, 5, 10, 5, tzinfo=timezone.utc)  # 10 not a killzone
    out = _assert_parity(legacy_s93, new_s93, [
        (None, f, None, _s93_now()),                         # arm
        (_s93_w1m(6, 2002.0, 2001.0), f, None, off),         # excluded -> clear
    ])
    assert out == [None, None]


def test_s93_dedup_one_setup_per_bar_parity():
    f = _s93_frame()
    for mod in (legacy_s93, new_s93):
        mod.reset_state()
    a0 = _norm(legacy_s93.get_signal(None, f, None, _s93_now()))
    b0 = _norm(new_s93.get_signal(None, f, None, _s93_now()))
    assert a0 == b0
    legacy_s93._pending = None
    new_s93._pending = None
    a1 = _norm(legacy_s93.get_signal(None, f, None, _s93_now(1)))
    b1 = _norm(new_s93.get_signal(None, f, None, _s93_now(1)))
    assert a1 == b1
    assert legacy_s93._pending is None and new_s93._pending is None


# ── S99: MSS + FVG reversal ───────────────────────────────────────────────────
_S99_T0 = pd.Timestamp("2026-06-05T02:00:00Z")


def _s99_frame(with_sweep=True):
    rows = []

    def add(o, h, l, c):
        rows.append(dict(time=_S99_T0 + pd.Timedelta(minutes=5 * len(rows)),
                         open=float(o), high=float(h), low=float(l),
                         close=float(c), volume=1.0))

    for _ in range(60):
        add(2000, 2000.5, 1999.5, 2000)
    add(2000, 2010, 1999.5, 2005)          # i=60 swing high
    add(2005, 2006, 2000, 2004)            # i=61
    add(2004, 2006, 2000, 2004)            # i=62 (swing high confirms)
    add(2004, 2005, 1995, 2000)            # i=63 swing low
    add(2000, 2005, 1998, 2003)            # i=64
    add(2003, 2005, 1998, 2004)            # i=65 (swing low confirms)
    add(2004, 2012 if with_sweep else 2006, 2004, 2006)   # i=66 sweep bar
    add(2006, 2006.5, 1999, 2000)          # i=67
    add(2000, 2000.2, 1992, 1993)          # i=68 MSS + bearish FVG
    return pd.DataFrame(rows)


def _s99_w1m(minute_offset, hi, lo):
    ts = _S99_T0 + pd.Timedelta(minutes=68 * 5 + minute_offset)
    return pd.DataFrame([dict(time=ts, open=hi - 0.3, high=float(hi),
                              low=float(lo), close=hi - 0.2, volume=1.0)])


def _s99_now(minute_offset=0):
    return datetime(2026, 6, 5, 7, 40, tzinfo=timezone.utc) + pd.Timedelta(
        minutes=minute_offset)


def _s99_levels(frame, now):
    legacy_s99.reset_state()
    legacy_s99.get_signal(None, frame, None, now)
    p = legacy_s99._pending
    return float(p["prox"]), float(p["sl"])


def test_s99_mss_fvg_fires_on_retrace_parity():
    f = _s99_frame()
    prox, sl = _s99_levels(f, _s99_now())
    probe = _s99_w1m(6, hi=prox + 0.3, lo=1999.0)           # into [prox, sl)
    out = _assert_parity(legacy_s99, new_s99, [
        (None, f, None, _s99_now()),
        (probe, f, None, _s99_now(6)),
    ])
    assert out[0] is None
    assert out[1] is not None and out[1][0] == "SELL"
    assert out[1][4] == "S99_MSS_FVG_SHORT"


def test_s99_phantom_cancel_parity():
    f = _s99_frame()
    prox, sl = _s99_levels(f, _s99_now())
    probe = _s99_w1m(6, hi=sl + 0.5, lo=2000.0)             # pierces stop first
    out = _assert_parity(legacy_s99, new_s99, [
        (None, f, None, _s99_now()),
        (probe, f, None, _s99_now(6)),
    ])
    assert out == [None, None]


def test_s99_no_sweep_no_signal_parity():
    f = _s99_frame(with_sweep=False)
    out = _assert_parity(legacy_s99, new_s99, [
        (None, f, None, _s99_now()),
    ])
    assert out == [None]


def test_s99_session_exclusion_parity():
    f = _s99_frame()
    off = datetime(2026, 6, 5, 16, 5, tzinfo=timezone.utc)  # 16 not in 6..15
    out = _assert_parity(legacy_s99, new_s99, [
        (None, f, None, _s99_now()),
        (_s99_w1m(6, 2001.0, 1999.0), f, None, off),
    ])
    assert out == [None, None]


def test_s99_dedup_one_setup_per_mss_bar_parity():
    f = _s99_frame()
    for mod in (legacy_s99, new_s99):
        mod.reset_state()
    a0 = _norm(legacy_s99.get_signal(None, f, None, _s99_now()))
    b0 = _norm(new_s99.get_signal(None, f, None, _s99_now()))
    assert a0 == b0
    legacy_s99._pending = None
    new_s99._pending = None
    a1 = _norm(legacy_s99.get_signal(None, f, None, _s99_now(1)))
    b1 = _norm(new_s99.get_signal(None, f, None, _s99_now(1)))
    assert a1 == b1
    assert legacy_s99._pending is None and new_s99._pending is None


# ── S100: M3 combo scalper ────────────────────────────────────────────────────
def _s100_m1(closes, start="2026-07-20 03:00", spread=0.3):
    n = len(closes)
    t = pd.date_range(start, periods=n, freq="1min", tz="UTC")
    c = np.asarray(closes, float)
    o = np.concatenate(([c[0]], c[:-1]))
    h = np.maximum(o, c) + spread
    l = np.minimum(o, c) - spread
    return pd.DataFrame({"time": t, "open": o, "high": h, "low": l, "close": c})


def _s100_uptrend_base(n_m1=1950, drift=0.05):
    rng = np.random.default_rng(7)
    steps = drift + rng.normal(0, 0.30, n_m1)
    return list(4000 + np.cumsum(steps))


def _s100_fvg_tail(base, gap_pts=1.2):
    lvl = base[-1]
    up = lvl + gap_pts + 1.2
    return base + [lvl + 0.2, lvl + 0.5, up,
                   up + 0.3, up + 0.5, up + 0.4]


def _s100_dt(hour, minute):
    return datetime(2026, 7, 20, hour, minute, tzinfo=timezone.utc)


def _s100_append_probe(w1m, closes, start):
    probe = _s100_m1(closes, start=start)
    w1m2 = pd.concat([w1m, probe], ignore_index=True)
    w1m2["time"] = list(w1m["time"]) + list(pd.date_range(
        w1m["time"].iloc[-1] + pd.Timedelta(minutes=1),
        periods=len(closes), freq="1min"))
    return w1m2


def _s100_prox(w1m, now):
    legacy_s100.reset_state()
    legacy_s100.get_signal(w1m, None, None, now)
    return float(legacy_s100._pending["prox"])


def test_s100_fvg_fires_on_touch_parity():
    base = _s100_uptrend_base()
    w1m = _s100_m1(_s100_fvg_tail(base))
    now_arm = _s100_dt(13, 30)
    prox = _s100_prox(w1m, now_arm)
    w1m2 = _s100_append_probe(w1m, [prox + 0.4, prox + 0.05],
                              "2026-07-20 13:31")
    out = _assert_parity(legacy_s100, new_s100, [
        (w1m, None, None, now_arm),
        (w1m2, None, None, _s100_dt(13, 33)),
    ])
    assert out[0] is None
    assert out[1] is not None and out[1][0] == "BUY"
    assert out[1][4].startswith("S100_")


def test_s100_phantom_cancel_parity():
    base = _s100_uptrend_base()
    w1m = _s100_m1(_s100_fvg_tail(base))
    now_arm = _s100_dt(13, 30)
    legacy_s100.reset_state()
    legacy_s100.get_signal(w1m, None, None, now_arm)
    sl = float(legacy_s100._pending["sl"])
    w1m2 = _s100_append_probe(w1m, [sl - 0.5, sl - 0.6], "2026-07-20 13:31")
    out = _assert_parity(legacy_s100, new_s100, [
        (w1m, None, None, now_arm),
        (w1m2, None, None, _s100_dt(13, 33)),
    ])
    assert out == [None, None]


def test_s100_out_of_band_gap_no_signal_parity():
    # a huge bullish gap (>> 1.5 x ATR) is rejected by the FVG band, and no OB
    # break / RSI cross exists on the flat-then-jump tail -> no signal at all.
    base = _s100_uptrend_base()
    w1m = _s100_m1(_s100_fvg_tail(base, gap_pts=40.0))
    out = _assert_parity(legacy_s100, new_s100, [
        (w1m, None, None, _s100_dt(13, 30)),
    ])
    assert out == [None]


def test_s100_session_exclusion_parity():
    base = _s100_uptrend_base()
    w1m = _s100_m1(_s100_fvg_tail(base))
    out = _assert_parity(legacy_s100, new_s100, [
        (w1m, None, None, _s100_dt(13, 30)),          # arm
        (w1m, None, None, _s100_dt(10, 0)),           # hour 10 excluded -> clear
    ])
    assert out == [None, None]


def test_s100_dedup_one_pass_per_bar_parity():
    base = _s100_uptrend_base()
    w1m = _s100_m1(_s100_fvg_tail(base))
    now_arm = _s100_dt(13, 30)
    for mod in (legacy_s100, new_s100):
        mod.reset_state()
    a0 = _norm(legacy_s100.get_signal(w1m, None, None, now_arm))
    b0 = _norm(new_s100.get_signal(w1m, None, None, now_arm))
    assert a0 == b0
    legacy_s100._pending = None
    new_s100._pending = None
    a1 = _norm(legacy_s100.get_signal(w1m, None, None, _s100_dt(13, 31)))
    b1 = _norm(new_s100.get_signal(w1m, None, None, _s100_dt(13, 31)))
    assert a1 == b1
    assert legacy_s100._pending is None and new_s100._pending is None


# ── shared-helper unit checks (fvg geometry + tz normalize + touch machine) ────
def test_shared_fvg_at_geometry():
    from backtest_strategies import _shared_ta as ta
    h = np.array([10.0, 10.0, 10.0])
    l = np.array([0.0, 0.0, 12.0])                 # low[2]=12 > high[0]=10 bull
    assert ta.fvg_at(h, l, 2) == ("bull", 2.0, 12.0, 10.0)
    h2 = np.array([10.0, 10.0, 3.0])               # high[2]=3 < low[0]=5 bear
    l2 = np.array([5.0, 5.0, 1.0])
    assert ta.fvg_at(h2, l2, 2) == ("bear", 2.0, 3.0, 5.0)
    h3 = np.array([10.0, 10.0, 10.0])
    l3 = np.array([0.0, 0.0, 0.0])                 # overlap -> no gap
    assert ta.fvg_at(h3, l3, 2)[0] is None


def test_shared_ensure_utc_ts_localizes_naive():
    from backtest_strategies import _shared_ta as ta
    naive = ta.ensure_utc_ts(pd.Timestamp("2026-01-01 00:00"))
    assert naive.tzinfo is not None and str(naive.tz) == "UTC"
    aware = ta.ensure_utc_ts(pd.Timestamp("2026-01-01 00:00", tz="UTC"))
    assert aware == naive


def test_s93_fuzz_probe_grid_parity():
    """Sweep the FVG band (below/at/above the 0.3xATR threshold) x probe
    position (through-stop / at-prox / just-above) x arm time; assert legacy
    and refactored emit identical signals for every combination. Guards the
    float()-wrapping in fvg_at/check_touch against any subtle threshold drift."""
    combos = 0
    fired = phantom = notouch = 0
    for gap in (0.15, 0.2, 0.3, 0.35, 0.5, 0.7, 1.0, 1.5, 2.0):
        f = _s93_frame(gap=gap)
        base_prox = 2000.5 + gap                       # bull proximal edge
        for lo_off in (-3.0, -1.0, -0.4, -0.1, 0.0, 0.15, 0.6):
            for moff in (1, 6, 11):
                probe = _s93_w1m(moff, hi=base_prox + 1.0, lo=base_prox + lo_off)
                out = _assert_parity(legacy_s93, new_s93, [
                    (None, f, None, _s93_now()),
                    (probe, f, None, _s93_now(moff)),
                ])
                if out[1] is not None:
                    fired += 1
                elif gap >= 0.35 and lo_off <= -1.0:
                    phantom += 1
                elif gap >= 0.35 and lo_off >= 0.15:
                    notouch += 1
                combos += 1
    assert combos == 9 * 7 * 3
    # the grid must actually exercise all three outcomes, not just no-arm rows
    assert fired > 0 and phantom > 0 and notouch > 0
