# SESSION_BREAKOUT Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate the two live challenge bots into ONE live strategy — SESSION_BREAKOUT, an M5 bias-filtered opening-range breakout (~4 trades/day, static SL/TP) — ported onto the existing `research_runner` engine path and deployed to the FundingPips challenge account.

**Architecture:** New `research_runner` module `kronos_session_breakout.py` with a pure, unit-tested `get_signal`. Orders route through the existing `entry_manager` static-SL/TP path (no trailing) with a 3h `max_hold_min` time-close. Repoint the box's `challenge_xau` compose service to run it; remove `challenge_xau_h4`.

**Tech Stack:** Python 3.12, pandas, pytest, Docker Compose (`-p kronos`) on the algorobos AWS box, Postgres (Lightsail `kronos-strategies-db`), OANDA M5 candle feed.

## Global Constraints

- Signal fields available in this repo (frozen dataclass `backtest_strategies.base.Signal`): `side` ('BUY'|'SELL'), `entry_price`, `stop_loss`, `take_profit`, `reason`, `max_hold_min`. **There is NO `trailing` field** — do not pass one (it TypeErrors). Static SL/TP is the default.
- Params are OOS-selected; **do not re-tune**: `SESSION_HOURS=(1,7,12,13,14)` UTC, `or_min=30`, `tp_mult=1.5`, `hold_bars=36`, `n_long=240`, `slope_lk=48`.
- Fixed lot per strategy via DB `Strategy.entry_quantity=0.02` (spec path a). Engine does NOT range-size.
- Strictly causal: evaluate only CLOSED M5 bars (drop the last forming bar); OR must be complete (`minute >= 30`) before any entry.
- DB `Strategy.name` MUST equal `entry_manager._VARIATION_STRATEGY_NAME["SESSION_BREAKOUT"]`.
- Tests run under the repo venv: `python -m pytest` (Windows dev box) or `.venv/Scripts/python.exe -m pytest`.
- Live deploy = box `ubuntu@13.126.204.82`, key `~/.ssh/algobet-ssh.pem`, always `ssh -F /dev/null`, compose `-p kronos`, box checkout `/home/ubuntu/KronosStrategies` (NOT a git repo → scp). NEVER scp `.env`/`*.session`.
- Canonical signal spec: `SESSION_BREAKOUT_STRATEGY_SPEC.md`. Design: `docs/superpowers/specs/2026-07-01-session-breakout-design.md`.
- **Known fidelity caveat:** per spec §8.1 the engine port gates on the *current bar's* hour ∈ SESSION_HOURS (narrower than the backtest's 3h forward-scan for isolated London hours). Implement as specified; measure true frequency in Task 7.

---

### Task 1: Pure bias + opening-range helpers

**Files:**
- Create: `strategies/backtest_strategies/kronos_session_breakout.py`
- Test: `tests/test_session_breakout.py`

**Interfaces:**
- Produces: `bias_series(closes: pd.Series, n_long=240, slope_lk=48) -> list[int]` (each ∈ {−1,0,1}); `opening_range(bars: pd.DataFrame, day, sh: int, or_min=30) -> tuple[float,float,int] | None` returning `(rng_hi, rng_lo, last_or_index)`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_session_breakout.py
from __future__ import annotations
import pandas as pd
import pytest
from strategies.backtest_strategies.kronos_session_breakout import (
    bias_series, opening_range,
)

def _m5(times, o, h, l, c):
    return pd.DataFrame({"time": pd.to_datetime(times, utc=True),
                         "open": o, "high": h, "low": l, "close": c,
                         "volume": [1.0]*len(c)})

def test_bias_up_when_price_above_rising_ema():
    # 400 bars: long flat warmup then a clean rising ramp -> bias +1 at the end
    n = 400
    closes = pd.Series([2000.0]*300 + [2000.0 + i for i in range(1, n-300+1)])
    b = bias_series(closes, n_long=240, slope_lk=48)
    assert b[-1] == 1

def test_bias_down_when_price_below_falling_ema():
    n = 400
    closes = pd.Series([2000.0]*300 + [2000.0 - i for i in range(1, n-300+1)])
    b = bias_series(closes, n_long=240, slope_lk=48)
    assert b[-1] == -1

def test_bias_undefined_is_zero_during_warmup():
    closes = pd.Series([2000.0]*100)
    b = bias_series(closes, n_long=240, slope_lk=48)
    assert set(b) == {0}

def test_opening_range_from_first_30min_bars():
    # session hour 7: bars at :00,:05,:10,:15,:20,:25 form the OR; :30 is outside
    times = [f"2026-06-15T07:{m:02d}:00Z" for m in (0,5,10,15,20,25,30)]
    o = [2000]*7; h = [2001,2003,2002,2004,2001,2000,2010]
    l = [1999,1998,1997,1999,2000,1999,1995]; c = [2000]*7
    bars = _m5(times, o, h, l, c)
    res = opening_range(bars, bars["time"].iloc[0].date(), 7, or_min=30)
    assert res is not None
    rng_hi, rng_lo, or_last = res
    assert rng_hi == 2004.0 and rng_lo == 1997.0
    assert or_last == 5                      # index of the :25 bar (last OR bar)

def test_opening_range_none_when_fewer_than_two_bars():
    times = ["2026-06-15T07:00:00Z"]
    bars = _m5(times, [2000],[2001],[1999],[2000])
    assert opening_range(bars, bars["time"].iloc[0].date(), 7, or_min=30) is None
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `python -m pytest tests/test_session_breakout.py -q`
Expected: FAIL — `ModuleNotFoundError`/`ImportError` (module/functions don't exist yet).

- [ ] **Step 3: Create the module with the helpers**

```python
# strategies/backtest_strategies/kronos_session_breakout.py
"""
Kronos SESSION_BREAKOUT — M5 bias-filtered opening-range breakout (XAUUSD)
--------------------------------------------------------------------------
Port of strat_orb_biased (research: s5_intraday_research2.py). See
SESSION_BREAKOUT_STRATEGY_SPEC.md for the canonical spec. TAKER-compatible.

Design (zero discretion), evaluated on the last CLOSED M5 bar:
  bias  : EMA(240) level + 48-bar slope -> +1 up / -1 down / 0 flat
  window: session-open hours [1,7,12,13,14] UTC, first 30 min = opening range
  entry : break of OR boundary in the bias direction (long@hi / short@lo)
  stop  : opposite OR side (static)   tp: entry +/- 1.5 * OR width (static)
  exit  : static broker SL/TP + 3h (36-bar) max-hold time-close
"""
from __future__ import annotations

import pandas as pd

from backtest_strategies.base import Signal, StrategyConfig
from backtest_strategies._kronos_indicators import ema

NAME = "SESSION_BREAKOUT"
CONFIG = StrategyConfig(
    name=NAME,
    description="M5 opening-range breakout, EMA240 bias, sessions [1,7,12,13,14] UTC, "
                "static OR-width stop + 1.5x-OR target. Port of strat_orb_biased.",
    cooldown_s=1800,               # >= OR length; per-session guard does the fine-grained work
    session_start_hour=None,       # FIVE discrete hours -> gate inside get_signal
    session_end_hour=None,
    max_concurrent_positions=1,
)

SESSION_HOURS = (1, 7, 12, 13, 14)
_OR_MIN, _TP_MULT, _HOLD_BARS, _N_LONG, _SLOPE_LK = 30, 1.5, 36, 240, 48
_MAX_HOLD_MIN = 180.0                     # 36 M5 bars = 3h time-stop
USD_PER_POINT_PER_0_1_LOT = 10.0

# One-entry-per-session guard: (utc_date, session_hour) keys that already fired.
# Persists across research_runner ticks in the running process.
_fired_sessions: set = set()


def bias_series(closes: pd.Series, n_long: int = _N_LONG, slope_lk: int = _SLOPE_LK) -> list[int]:
    """Per-bar trend proxy: +1 (price>EMA & EMA rising), -1 (price<EMA & EMA falling),
    else 0. Undefined (0) until i >= n_long + slope_lk."""
    e = ema(closes, n_long)
    n = len(closes)
    out = [0] * n
    for i in range(n):
        if i < n_long + slope_lk:
            continue
        ci = float(closes.iloc[i]); ei = float(e.iloc[i]); ep = float(e.iloc[i - slope_lk])
        if ci > ei and ei > ep:
            out[i] = 1
        elif ci < ei and ei < ep:
            out[i] = -1
    return out


def opening_range(bars: pd.DataFrame, day, sh: int, or_min: int = _OR_MIN):
    """(rng_hi, rng_lo, last_or_index) for session-hour `sh` on `day`, or None when
    < 2 OR bars or a non-positive range. OR bars = same day, hour==sh, minute<or_min."""
    t = bars["time"]
    idx = [k for k in range(len(bars))
           if t.iloc[k].date() == day and t.iloc[k].hour == sh and t.iloc[k].minute < or_min]
    if len(idx) < 2:
        return None
    rng_hi = float(bars["high"].iloc[idx].max())
    rng_lo = float(bars["low"].iloc[idx].min())
    if rng_hi - rng_lo <= 0:
        return None
    return rng_hi, rng_lo, idx[-1]
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python -m pytest tests/test_session_breakout.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add strategies/backtest_strategies/kronos_session_breakout.py tests/test_session_breakout.py
git commit -m "feat(session_breakout): pure bias + opening-range helpers (TDD)"
```

---

### Task 2: `get_signal` — session gate, causality, breakout, static SL/TP, per-session guard

**Files:**
- Modify: `strategies/backtest_strategies/kronos_session_breakout.py`
- Test: `tests/test_session_breakout.py`

**Interfaces:**
- Consumes: `bias_series`, `opening_range` (Task 1).
- Produces: `get_signal(w1m, w5m, w15m, now_utc) -> Signal | None`; module helper `_closed_m5(w5m) -> pd.DataFrame`; module global `_fired_sessions: set`.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_session_breakout.py`:

```python
from datetime import datetime, timezone
from strategies.backtest_strategies import kronos_session_breakout as sb

def _uptrend_m5_frame(session_hour=7, break_out=True):
    """Build a CLOSED-bar M5 frame that (a) warms the EMA into +1 bias, (b) forms an
    OR in `session_hour`, (c) ends on a bar that breaks (or not) the OR high.
    The frame ends with one EXTRA still-forming bar (get_signal drops it)."""
    rows = []
    base = pd.Timestamp("2026-06-01T00:00:00Z")
    price = 2000.0
    # 320 warmup bars, gently rising so EMA240 slopes up and price>EMA (bias +1)
    for k in range(320):
        price += 0.5
        rows.append((base + pd.Timedelta(minutes=5*k), price, price+0.4, price-0.4, price))
    # OR bars for the session hour on a fresh day at :00.. :25 (6 bars), tight range
    day = pd.Timestamp("2026-06-05T00:00:00Z")
    or_hi, or_lo = price + 2.0, price - 2.0
    for m in (0,5,10,15,20,25):
        ts = day + pd.Timedelta(hours=session_hour, minutes=m)
        rows.append((ts, price, or_hi-0.5, or_lo+0.5, price))
    # breakout bar at :30 (closed) — high pierces or_hi if break_out else stays inside
    bh = or_hi + 3.0 if break_out else or_hi - 0.5
    rows.append((day + pd.Timedelta(hours=session_hour, minutes=30), price, bh, price-0.5, price+0.2))
    # one extra still-forming bar to be dropped
    rows.append((day + pd.Timedelta(hours=session_hour, minutes=35), price, price+0.1, price-0.1, price))
    return pd.DataFrame(rows, columns=["time","open","high","low","close"])

def setup_function(_):
    sb._fired_sessions.clear()

def test_long_signal_on_bias_aligned_breakout():
    f = _uptrend_m5_frame(session_hour=7, break_out=True)
    sig = sb.get_signal(None, f, None, datetime(2026,6,5,7,30,tzinfo=timezone.utc))
    assert sig is not None and sig.side == "BUY"
    assert sig.reason == "SESSION_BREAKOUT_LONG"
    assert sig.max_hold_min == 180.0
    # static sl/tp: sl == OR low, tp == entry + 1.5*OR width
    assert sig.stop_loss < sig.entry_price < sig.take_profit

def test_no_signal_when_break_absent():
    f = _uptrend_m5_frame(session_hour=7, break_out=False)
    assert sb.get_signal(None, f, None, datetime(2026,6,5,7,30,tzinfo=timezone.utc)) is None

def test_no_signal_outside_session_hours():
    f = _uptrend_m5_frame(session_hour=9, break_out=True)   # 9 not in SESSION_HOURS
    assert sb.get_signal(None, f, None, datetime(2026,6,5,9,30,tzinfo=timezone.utc)) is None

def test_no_signal_before_or_complete():
    # truncate so the last closed bar is at :20 (minute<30 -> OR incomplete)
    f = _uptrend_m5_frame(session_hour=7, break_out=True)
    f = f.iloc[:-3]   # drop :30 closed bar, forming bar, and one OR bar -> last closed :20
    assert sb.get_signal(None, f, None, datetime(2026,6,5,7,25,tzinfo=timezone.utc)) is None

def test_one_entry_per_session_guard():
    f = _uptrend_m5_frame(session_hour=7, break_out=True)
    first = sb.get_signal(None, f, None, datetime(2026,6,5,7,30,tzinfo=timezone.utc))
    second = sb.get_signal(None, f, None, datetime(2026,6,5,7,30,tzinfo=timezone.utc))
    assert first is not None and second is None      # same (date,hour) suppressed

def test_bias_gate_blocks_counter_trend_long():
    # force bias down by flipping the warmup to a downtrend but keep an up-break
    f = _uptrend_m5_frame(session_hour=7, break_out=True)
    f.loc[:319, "close"] = [2000.0 - 0.5*k for k in range(320)]
    f.loc[:319, "high"] = f.loc[:319, "close"] + 0.4
    f.loc[:319, "low"] = f.loc[:319, "close"] - 0.4
    sig = sb.get_signal(None, f, None, datetime(2026,6,5,7,30,tzinfo=timezone.utc))
    assert sig is None or sig.side != "BUY"
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `python -m pytest tests/test_session_breakout.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'get_signal'`.

- [ ] **Step 3: Add `_closed_m5` and `get_signal`**

Append to `strategies/backtest_strategies/kronos_session_breakout.py`:

```python
def _closed_m5(w5m) -> pd.DataFrame:
    """UTC-normalise, sort, and DROP the still-forming last M5 bar (strictly causal)."""
    if w5m is None or len(w5m) == 0:
        return pd.DataFrame()
    df = w5m.copy()
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.sort_values("time").reset_index(drop=True)
    return df.iloc[:-1].reset_index(drop=True)


def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    bars = _closed_m5(w5m)
    n = len(bars)
    if n < _N_LONG + _SLOPE_LK + 2:
        return None
    i = n - 1
    last = bars["time"].iloc[i]
    sh = int(last.hour)
    if sh not in SESSION_HOURS:                      # spec §8.1 current-bar-hour gate
        return None
    if int(last.minute) < _OR_MIN:                   # OR must be complete
        return None
    day = last.date()
    key = (day, sh)
    if key in _fired_sessions:                       # one entry per (date, session hour)
        return None
    orr = opening_range(bars, day, sh, _OR_MIN)
    if orr is None:
        return None
    rng_hi, rng_lo, or_last = orr
    if i - or_last > _HOLD_BARS:                      # past the 3h hold window
        return None
    rng = rng_hi - rng_lo
    b = bias_series(bars["close"])[i]
    hi = float(bars["high"].iloc[i]); lo = float(bars["low"].iloc[i])
    if hi >= rng_hi and b == 1:
        _fired_sessions.add(key)
        return Signal(side="BUY", entry_price=rng_hi, stop_loss=rng_lo,
                      take_profit=round(rng_hi + _TP_MULT * rng, 2),
                      reason="SESSION_BREAKOUT_LONG", max_hold_min=_MAX_HOLD_MIN)
    if lo <= rng_lo and b == -1:
        _fired_sessions.add(key)
        return Signal(side="SELL", entry_price=rng_lo, stop_loss=rng_hi,
                      take_profit=round(rng_lo - _TP_MULT * rng, 2),
                      reason="SESSION_BREAKOUT_SHORT", max_hold_min=_MAX_HOLD_MIN)
    return None
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python -m pytest tests/test_session_breakout.py -q`
Expected: PASS (all Task 1 + Task 2 tests green).

- [ ] **Step 5: Commit**

```bash
git add strategies/backtest_strategies/kronos_session_breakout.py tests/test_session_breakout.py
git commit -m "feat(session_breakout): get_signal with session/causality/bias gates + static SL/TP (TDD)"
```

---

### Task 3: `position_size` helper + fixed-lot risk test

**Files:**
- Modify: `strategies/backtest_strategies/kronos_session_breakout.py`
- Test: `tests/test_session_breakout.py`

**Interfaces:**
- Produces: `position_size(equity, or_width_points, *, risk_pct=0.008, risk_floor=40.0, min_lot=0.01, max_lot=0.50, lot_step=0.01) -> tuple[float,float]` returning `(lot, actual_risk_dollars)`.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_session_breakout.py`:

```python
def test_position_size_zero_on_nonpositive_width():
    assert sb.position_size(5000, 0.0) == (0.0, 0.0)

def test_fixed_002_lot_stop_stays_within_daily_limit():
    # At the fixed 0.02 lot, a full-OR-width stop for a wide (8pt) OR must risk
    # well under the $150 daily kill-switch: 0.02 lot = $2/pt -> 8pt = $16.
    or_pts = 8.0
    usd_per_pt_at_002 = 0.02 * (sb.USD_PER_POINT_PER_0_1_LOT / 0.1)   # $2.00
    assert or_pts * usd_per_pt_at_002 <= 150.0
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `python -m pytest tests/test_session_breakout.py -k "position_size or daily_limit" -q`
Expected: FAIL — `AttributeError: ... 'position_size'`.

- [ ] **Step 3: Add `position_size`**

Append to `strategies/backtest_strategies/kronos_session_breakout.py`:

```python
def position_size(equity, or_width_points, *, risk_pct=0.008, risk_floor=40.0,
                  min_lot=0.01, max_lot=0.50, lot_step=0.01):
    """Lots so a full-OR-width stop risks ~max(risk_floor, risk_pct*equity). NOTE:
    the Kronos engine uses a FIXED lot (Strategy.entry_quantity); this is provided for
    parity with the spec and offline sizing checks, not wired into live sizing."""
    risk_dollars = max(risk_floor, risk_pct * equity)
    if or_width_points <= 0:
        return 0.0, 0.0
    raw = risk_dollars / (or_width_points * (USD_PER_POINT_PER_0_1_LOT / 0.1))
    lot = max(min_lot, min(max_lot, round(raw / lot_step) * lot_step))
    actual = or_width_points * (USD_PER_POINT_PER_0_1_LOT / 0.1) * lot
    return round(lot, 2), round(actual, 2)
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python -m pytest tests/test_session_breakout.py -q`
Expected: PASS (all green).

- [ ] **Step 5: Commit**

```bash
git add strategies/backtest_strategies/kronos_session_breakout.py tests/test_session_breakout.py
git commit -m "feat(session_breakout): OR-width position_size helper + fixed-lot risk test"
```

---

### Task 4: Register SESSION_BREAKOUT in entry_manager

**Files:**
- Modify: `strategies/strategy/entry_manager.py` (the `_VARIATION_STRATEGY_NAME` dict, ~line 60)

**Interfaces:**
- Produces: `_VARIATION_STRATEGY_NAME["SESSION_BREAKOUT"] == "Session Breakout M5 ORB"` (the DB `Strategy.name` Task 5 binds to).

- [ ] **Step 1: Add the registration**

In `strategies/strategy/entry_manager.py`, inside the `_VARIATION_STRATEGY_NAME` dict, after the `"KRONOS_COMBINED_V2"` line, add:

```python
    # Session-breakout M5 ORB (backtest_strategies/kronos_session_breakout.py).
    # Static SL/TP (no trailing) + 3h max-hold via Signal.max_hold_min.
    "SESSION_BREAKOUT": "Session Breakout M5 ORB",
```

- [ ] **Step 2: Verify it imports and resolves**

Run:
```bash
cd strategies && python -c "from strategy.entry_manager import _VARIATION_STRATEGY_NAME as m; print(m['SESSION_BREAKOUT'])"
```
Expected: `Session Breakout M5 ORB`

- [ ] **Step 3: Commit**

```bash
git add strategies/strategy/entry_manager.py
git commit -m "feat(session_breakout): register SESSION_BREAKOUT variation in entry_manager"
```

---

### Task 5: DB deploy script (`deploy_session_breakout.py`)

**Files:**
- Create: `strategies/db/deploy_session_breakout.py` (mirror `deploy_challenge_xau.py`)

**Interfaces:**
- Produces: idempotent `apis_strategy` (name `"Session Breakout M5 ORB"`, `entry_quantity=0.02`) + `apis_userstrategy` (deployed, active) bound to the challenge `UserBroker`. Reads `SESSION_BREAKOUT_LOT` (default `0.02`) and `SESSION_BREAKOUT_USER_BROKER_ID` (explicit binding).

- [ ] **Step 1: Create the deploy script**

```python
# strategies/db/deploy_session_breakout.py
"""
Deploy the SESSION_BREAKOUT strategy into the live Kronos DB (idempotent).

  - apis_strategy row  name="Session Breakout M5 ORB"
        (MUST match entry_manager._VARIATION_STRATEGY_NAME["SESSION_BREAKOUT"])
  - apis_userstrategy row (deployed=True, is_active=True, multiplyer=1)

Binds to the challenge FundingPips UserBroker. Prefer the explicit
SESSION_BREAKOUT_USER_BROKER_ID (the verified challenge account's UserBroker);
otherwise mirror the existing "Challenge XAU H4 Trend" strategy's UserBroker so
the new strategy trades the SAME account the retired H4 bot did.

Fixed lot: engine uses Strategy.entry_quantity (SESSION_BREAKOUT_LOT, default 0.02).

Run (from strategies/, DB_* env pointing at the app DB):
  python -m db.deploy_session_breakout            # dry-run (plan + resolved ids)
  python -m db.deploy_session_breakout --commit   # write rows
"""
from __future__ import annotations

import os
import sys
import uuid
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.models import Session, Strategy, UserStrategy, CurrencyPair, UserBroker

SYMBOL = "XAU_USD"
STRATEGY_NAME = "Session Breakout M5 ORB"
VARIATION = "SESSION_BREAKOUT"
DESCRIPTION = (
    "M5 opening-range breakout, EMA240 bias, sessions [1,7,12,13,14] UTC, static "
    "OR-width stop + 1.5x-OR target, 3h max-hold. Port of strat_orb_biased."
)
# Mirror the retired H4 bot's account when no explicit override is given.
REFERENCE_STRATEGY_NAME = "Challenge XAU H4 Trend"
ENTRY_QTY = Decimal(os.getenv("SESSION_BREAKOUT_LOT", "0.02"))


def main(commit: bool) -> int:
    sess = Session()
    try:
        cp = sess.query(CurrencyPair).filter_by(symbol=SYMBOL).first()
        if cp is None:
            print(f"FATAL: CurrencyPair symbol='{SYMBOL}' not found."); return 1
        print(f"[OK]  CurrencyPair {SYMBOL} -> id={cp.id}")

        override_ub_id = os.getenv("SESSION_BREAKOUT_USER_BROKER_ID", "").strip()
        if override_ub_id:
            user_broker = sess.query(UserBroker).filter_by(id=override_ub_id).first()
            if user_broker is None:
                print(f"FATAL: SESSION_BREAKOUT_USER_BROKER_ID={override_ub_id} not found."); return 1
            print(f"[OK]  Explicit UserBroker override -> id={user_broker.id} status={user_broker.status}")
        else:
            ref = sess.query(Strategy).filter_by(name=REFERENCE_STRATEGY_NAME, currencypair_id=cp.id).first()
            if ref is None:
                print(f"FATAL: reference Strategy '{REFERENCE_STRATEGY_NAME}' not found -- "
                      f"set SESSION_BREAKOUT_USER_BROKER_ID explicitly."); return 1
            ref_us = sess.query(UserStrategy).filter_by(strategy_id=ref.id).first()
            if ref_us is None:
                print(f"FATAL: no UserStrategy on '{REFERENCE_STRATEGY_NAME}'."); return 1
            user_broker = sess.query(UserBroker).filter_by(id=ref_us.user_broker_id).first()
            if user_broker is None:
                print(f"FATAL: UserBroker id={ref_us.user_broker_id} not found."); return 1
        print(f"[OK]  Binding to UserBroker={user_broker.id} ({user_broker.status}) "
              f"-- verify this is the challenge account.")
        print(f"[OK]  Fixed entry_quantity = {ENTRY_QTY} lot")

        strat = sess.query(Strategy).filter_by(name=STRATEGY_NAME).first()
        if strat is None:
            strat = Strategy(
                id=uuid.uuid4(), name=STRATEGY_NAME, description=DESCRIPTION,
                is_active=True, capital_required="5000.00",
                json_data={"variation": VARIATION, "deployed_via": "deploy_session_breakout"},
                params={}, entry_quantity=ENTRY_QTY, currencypair_id=cp.id,
            )
            sess.add(strat); sess.flush()
            print(f"[NEW] Strategy '{STRATEGY_NAME}' id={strat.id} qty={strat.entry_quantity}")
        else:
            if strat.entry_quantity != ENTRY_QTY:
                print(f"[UPD] entry_quantity {strat.entry_quantity} -> {ENTRY_QTY}")
                strat.entry_quantity = ENTRY_QTY
            if not strat.is_active:
                strat.is_active = True
            print(f"[OK]  Strategy present id={strat.id}")

        us = sess.query(UserStrategy).filter_by(strategy_id=strat.id, user_broker_id=user_broker.id).first()
        if us is None:
            us = UserStrategy(id=uuid.uuid4(), name=f"{STRATEGY_NAME} live", is_active=True,
                              multiplyer=1, deployed=True, strategy_id=strat.id,
                              user_broker_id=user_broker.id)
            sess.add(us); sess.flush()
            print(f"[NEW] UserStrategy id={us.id} deployed=True active=True")
        else:
            us.is_active = True; us.deployed = True
            print(f"[OK]  UserStrategy id={us.id} deployed/active")

        if commit:
            sess.commit(); print("\nCOMMITTED.")
        else:
            sess.rollback(); print("\nDRY-RUN (no writes). Re-run with --commit to persist.")
        return 0
    except Exception as e:
        sess.rollback(); print(f"FATAL: {type(e).__name__}: {e}"); raise
    finally:
        sess.close()


if __name__ == "__main__":
    raise SystemExit(main(commit="--commit" in sys.argv))
```

- [ ] **Step 2: Byte-compile check (no DB needed)**

Run: `python -m py_compile strategies/db/deploy_session_breakout.py`
Expected: no output (compiles).

- [ ] **Step 3: Commit**

```bash
git add strategies/db/deploy_session_breakout.py
git commit -m "feat(session_breakout): idempotent DB deploy script (0.02 lot, challenge broker)"
```

---

### Task 6: Compose — add session_breakout service, drop the H4 bots (repo)

**Files:**
- Modify: `compose.yml` (repo)

**Interfaces:**
- Produces: a `session_breakout` service (research_runner, M5 windows) in the tracked compose; removal of `challenge_xau_h4`. (The box compose is reconciled in Task 8.)

- [ ] **Step 1: Replace the `challenge_xau_h4` service block with `session_breakout`**

In `compose.yml`, replace the entire `challenge_xau_h4:` service block (and its preceding comment banner) with:

```yaml
  # ───────────────────────────────────────────────────────────────────────────
  # SESSION_BREAKOUT — the SINGLE live challenge strategy (replaces the two H4
  # bots as of 2026-07-01). M5 bias-filtered opening-range breakout via the
  # generic research_runner; orders route through entry_manager -> the DB-bound
  # challenge UserBroker; static broker SL/TP + 3h max-hold. ~4 trades/day target.
  # ───────────────────────────────────────────────────────────────────────────
  session_breakout:
    build: ./strategies
    restart: always
    command: python research_runner.py
    env_file: .env
    environment:
      TSDB_TARGET:      ${TSDB_TARGET:-tigerdata}
      TIGERDATA_URL:    ${TIGERDATA_URL:-}
      RESEARCH_STRATEGY: kronos_session_breakout
      RESEARCH_WIN_5M:  "600"      # >= n_long+slope_lk+2 = 290 closed M5 bars, with headroom
      RESEARCH_DAYS_5M: "5"        # ~2 trading days of M5 across weekend gaps
      META_REGION:      ${CHALLENGE_META_REGION:-london}
```

- [ ] **Step 2: Validate compose syntax**

Run: `python -c "import yaml; yaml.safe_load(open('compose.yml')); print('compose OK')"`
Expected: `compose OK`

- [ ] **Step 3: Commit**

```bash
git add compose.yml
git commit -m "feat(session_breakout): compose service; retire challenge_xau_h4 (repo)"
```

---

### Task 7: Live-M5 frequency validation (substitute for oracle parity)

**Files:**
- Create (scratch, not committed): `scratchpad/replay_session_breakout.py`

**Interfaces:**
- Consumes: `kronos_session_breakout.get_signal`, `shared.tsdb_reader.fetch_candles`.

- [ ] **Step 1: Write the replay script**

```python
# scratchpad/replay_session_breakout.py — run INSIDE a strategies container on the box
import pandas as pd
from shared.tsdb_reader import fetch_candles
from backtest_strategies import kronos_session_breakout as sb

raw = fetch_candles("5m", 30, symbol="XAU_USD")     # ~30 days of M5
df = raw.copy(); df["time"] = pd.to_datetime(df["time"], utc=True)
df = df.sort_values("time").reset_index(drop=True)

fires = []
WARM = sb._N_LONG + sb._SLOPE_LK + 2
for j in range(WARM + 1, len(df)):
    sb._fired_sessions.clear()                       # replay: judge each bar independently...
    window = df.iloc[:j+1]                            # ...but include one forming bar for get_signal to drop
    window = pd.concat([window, window.iloc[[-1]]], ignore_index=True)
    sig = sb.get_signal(None, window, None, None)
    if sig is not None:
        fires.append((str(df["time"].iloc[j])[:16], sig.side))

# de-dup to one entry per (date, session_hour) as the live guard would enforce
seen = set(); trades = []
for t, side in fires:
    d = t[:10]; hr = int(t[11:13]); k = (d, hr)
    if k in seen:
        continue
    seen.add(k); trades.append((t, side))

span_days = (df["time"].iloc[-1] - df["time"].iloc[0]).total_seconds() / 86400
print(f"M5 bars: {len(df)}  span: {span_days:.1f} days")
print(f"SESSION_BREAKOUT entries (deduped 1/session): {len(trades)}")
print(f"  per calendar day: {len(trades)/span_days:.2f}")
print(f"  per trading day : {len(trades)/(span_days*5/7):.2f}")
from collections import Counter
print("  by side:", Counter(s for _, s in trades))
print("  last 8:", trades[-8:])
```

- [ ] **Step 2: Run it on the box (after Task 8 image build) and record the numbers**

Run:
```bash
scp -F /dev/null -i ~/.ssh/algobet-ssh.pem scratchpad/replay_session_breakout.py \
  ubuntu@13.126.204.82:/tmp/replay_session_breakout.py
ssh -F /dev/null -i ~/.ssh/algobet-ssh.pem ubuntu@13.126.204.82 \
  "sudo docker exec -i kronos-session_breakout-1 python - < /tmp/replay_session_breakout.py"
```
Expected: an entries/day figure. **Decision gate:** if `per trading day` is within ~1.5–5, proceed. If it is far below (< ~1/day), STOP and report to the operator before real orders — the current-bar-hour gate (Global Constraints caveat) may be too narrow and warrant the wider OR-hold implementation.

- [ ] **Step 3: No commit** (scratch validation only — record the numbers in the deploy notes / memory).

---

### Task 8: Deploy to algorobos, retire the H4 bots, verify

**Files:** none new (ops task). Box compose is reconciled by hand (box ≠ repo).

- [ ] **Step 1: Push the branch**

```bash
git push github fix/tg-copy-fidelity
```

- [ ] **Step 2: scp the changed source files to the box**

```bash
scp -F /dev/null -i ~/.ssh/algobet-ssh.pem \
  strategies/backtest_strategies/kronos_session_breakout.py \
  ubuntu@13.126.204.82:/home/ubuntu/KronosStrategies/strategies/backtest_strategies/
scp -F /dev/null -i ~/.ssh/algobet-ssh.pem \
  strategies/strategy/entry_manager.py \
  ubuntu@13.126.204.82:/home/ubuntu/KronosStrategies/strategies/strategy/
scp -F /dev/null -i ~/.ssh/algobet-ssh.pem \
  strategies/db/deploy_session_breakout.py \
  ubuntu@13.126.204.82:/home/ubuntu/KronosStrategies/strategies/db/
# md5-verify each matches local (repeat md5sum on box)
```

- [ ] **Step 2b: Edit the BOX compose.yml** (box has an extra `challenge_xau` service not in the repo; edit in place, don't scp the repo file). On the box, in `/home/ubuntu/KronosStrategies/compose.yml`: rename the `challenge_xau` service to `session_breakout`, set `RESEARCH_STRATEGY: kronos_session_breakout`, add `RESEARCH_WIN_5M: "600"` and `RESEARCH_DAYS_5M: "5"`, remove `RESEARCH_WIN_15M`/`RESEARCH_DAYS_15M`, and delete the whole `challenge_xau_h4` service block. Verify: `ssh ... "cd /home/ubuntu/KronosStrategies && python3 -c \"import yaml;yaml.safe_load(open('compose.yml'));print('ok')\""`.

- [ ] **Step 3: Deploy the DB row** (bind to the challenge account)

```bash
# Resolve the challenge UserBroker id first (from the existing H4 Trend strategy) and
# pass it explicitly, then dry-run, inspect, and commit ON THE BOX (has DB creds in .env):
ssh -F /dev/null -i ~/.ssh/algobet-ssh.pem ubuntu@13.126.204.82 \
  "cd /home/ubuntu/KronosStrategies && sudo docker compose -p kronos run --rm \
   -w /app session_breakout python -m db.deploy_session_breakout"      # dry-run
# review the resolved UserBroker, then:
ssh -F /dev/null -i ~/.ssh/algobet-ssh.pem ubuntu@13.126.204.82 \
  "cd /home/ubuntu/KronosStrategies && sudo docker compose -p kronos run --rm \
   -w /app session_breakout python -m db.deploy_session_breakout --commit"
```

- [ ] **Step 4: Build + bring up the one service, stop/remove the old ones**

```bash
ssh -F /dev/null -i ~/.ssh/algobet-ssh.pem ubuntu@13.126.204.82 \
  "cd /home/ubuntu/KronosStrategies && \
   sudo docker compose -p kronos build session_breakout && \
   sudo docker compose -p kronos up -d session_breakout && \
   sudo docker compose -p kronos stop challenge_xau challenge_xau_h4 && \
   sudo docker compose -p kronos rm -f challenge_xau challenge_xau_h4"
```

- [ ] **Step 5: Retire the two old strategies in the DB** (so the platform shows exactly one)

```bash
# Deactivate the retired strategies' UserStrategies (reuse the existing delete/deactivate
# pattern; do NOT delete the SESSION_BREAKOUT rows). Set is_active=false + deployed=false
# for "Challenge XAU H4 Trend" and "Challenge XAU H4 (standalone)".
ssh -F /dev/null -i ~/.ssh/algobet-ssh.pem ubuntu@13.126.204.82 \
  "cd /home/ubuntu/KronosStrategies && sudo docker compose -p kronos run --rm -w /app session_breakout python -c \"
import os,psycopg2
c=psycopg2.connect(host=os.getenv('DB_HOST'),port=int(os.getenv('DB_PORT','5432')),dbname=os.getenv('DB_NAME','tsdb'),user=os.getenv('DB_USER','tsdbadmin'),password=os.getenv('DB_PASSWORD'),sslmode=os.getenv('DB_SSLMODE','require'))
cur=c.cursor()
for nm in ['Challenge XAU H4 Trend','Challenge XAU H4 (standalone)']:
    cur.execute('UPDATE apis_userstrategy us SET is_active=false, deployed=false FROM apis_strategy s WHERE us.strategy_id=s.id AND s.name=%s',(nm,))
    cur.execute('UPDATE apis_strategy SET is_active=false WHERE name=%s',(nm,))
    print(nm, cur.rowcount)
c.commit(); print('done')
\""
```

- [ ] **Step 6: Verify (do not claim success without this)**

```bash
ssh -F /dev/null -i ~/.ssh/algobet-ssh.pem ubuntu@13.126.204.82 \
  "cd /home/ubuntu/KronosStrategies && sudo docker compose -p kronos ps && \
   sudo docker compose -p kronos logs --tail=30 session_breakout"
```
Expected: `session_breakout` running; `challenge_xau`/`challenge_xau_h4` gone; log shows `research_runner starting: module=kronos_session_breakout NAME=SESSION_BREAKOUT`, no traceback. Prove new code runs:
```bash
ssh -F /dev/null -i ~/.ssh/algobet-ssh.pem ubuntu@13.126.204.82 \
  "sudo docker exec kronos-session_breakout-1 python -c \"from backtest_strategies.kronos_session_breakout import CONFIG; print(CONFIG.name)\""
```
Expected: `SESSION_BREAKOUT`. Confirm the platform shows exactly one challenge strategy.

- [ ] **Step 7: Update memory** — record the consolidation (SESSION_BREAKOUT live, two H4 bots retired, frequency measured in Task 7, oracle parity not run locally).

---

## Self-Review notes
- Spec coverage: §3/§5 signal logic → Tasks 1–2; §4 params → Global Constraints; §7 sizing/kill-switch → Task 3 + Task 5 (fixed lot) [daily kill-switch flagged as follow-up in the design]; §8.1/§8.2 port → Tasks 1–2/4; §8.4 registrations/files → Tasks 4–6, 8; §10 tests → Tasks 1–3 + Task 7 (live-M5 substitute for §10.1). 
- §10.1 oracle parity is explicitly NOT reproducible here (no research data) — Task 7 substitutes a live-M5 frequency check with a decision gate.
- Daily kill-switch (§7) is NOT enforced by the engine in v1; bounded instead by 0.02 lot + `max_concurrent_positions=1`. Called out in the design as a follow-up, not silently dropped.
