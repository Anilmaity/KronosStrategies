# CV2 PnL Optimization (Prune & Amplify) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Maximize Kronos Combined Suite v2 net PnL on the 126-day live-shape replay while holding maxDD ≤ −1,780 and book PF ≥ 1.26, by pruning drag legs and amplifying the highest-PF legs.

**Architecture:** Add reversible per-leg gating toggles to the reused MR leg modules and to CV2, refactor the parity backtest into a reusable `replay()`/`metrics()` pair, build a fetch-once sweep driver, then run a greedy phased sweep (prune → amplify → widen targets → optional bias-gate) keeping only changes that beat the running best within the risk bound, and finally bake the winner into CV2 defaults.

**Tech Stack:** Python 3.12, pandas/numpy, pytest, TimescaleDB (`shared.tsdb_reader`). Venv: `C:/Projects/PycharmProjects/personal/KronosStrategies/.venv/Scripts/python.exe`. All pytest runs from repo root (conftest puts repo root on `sys.path`).

**Spec:** `docs/superpowers/specs/2026-05-25-cv2-pnl-optimization-design.md`

**Baseline to beat:** net **+6,698** · PF **1.26** · maxDD **−1,621** · 26.5 tpd (126 trading days).

---

## File map

| File | Responsibility | Action |
|---|---|---|
| `strategies/backtest_strategies/kronos_s02_stoch_revert.py` | S02 MR leg | Modify: `_USE_TNX_GATE`, `_USE_D1_BIAS` toggles |
| `strategies/backtest_strategies/kronos_s05_threebar_pull.py` | S05 MR leg | Modify: `_USE_TNX_GATE`, `_USE_LIQ_GATE` toggles |
| `strategies/backtest_strategies/kronos_s06_session_sweep.py` | S06 MR leg | Modify: `_USE_TNX_GATE` toggle |
| `strategies/backtest_strategies/kronos_s07_crt.py` | S07 MR leg | Modify: `_USE_TNX_GATE` toggle |
| `strategies/backtest_strategies/kronos_s14_m5_ema_stretch.py` | S14 MR leg | Modify: `_USE_TNX_GATE` toggle |
| `strategies/backtest_strategies/kronos_combined_v2.py` | The book | Modify: `DISABLED_LEGS`, `SHORT_REQUIRE_BEARDAY`, disable TNX on MR legs at import |
| `strategies/backtest/backtest_combined_v2.py` | Parity replay | Modify: extract `replay()` + `metrics()` |
| `strategies/backtest/optimize_combined_v2.py` | Sweep driver | Create |
| `tests/test_cv2_optimize.py` | Tests for toggles/replay/driver | Create |

---

## Task 1: `_USE_TNX_GATE` toggle on all 5 MR legs

**Files:**
- Modify: `strategies/backtest_strategies/kronos_s02_stoch_revert.py`
- Modify: `strategies/backtest_strategies/kronos_s05_threebar_pull.py`
- Modify: `strategies/backtest_strategies/kronos_s06_session_sweep.py`
- Modify: `strategies/backtest_strategies/kronos_s07_crt.py`
- Modify: `strategies/backtest_strategies/kronos_s14_m5_ema_stretch.py`
- Test: `tests/test_cv2_optimize.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cv2_optimize.py`:

```python
"""
test_cv2_optimize.py
--------------------
TDD tests for the CV2 PnL-optimization changes: per-leg gating toggles,
the DISABLED_LEGS / SHORT_REQUIRE_BEARDAY controls, the extracted
replay()/metrics() pair, and the sweep driver's override mechanism.

Synthetic in-memory data only. No network, no disk, no TSDB.
"""
from __future__ import annotations

import os
import sys

# The runtime modules import their siblings BARE (e.g. `import backtest_strategies...`)
# with the strategies/ dir on sys.path. Import them the SAME way here so the test and the
# sweep driver share ONE module object. A `strategies.`-prefixed import would load a second,
# distinct copy (strategies/ has no __init__.py → namespace dir), and the override-roundtrip
# test would then silently mutate the wrong module.
_STRAT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "strategies"))
if _STRAT not in sys.path:
    sys.path.insert(0, _STRAT)

import pandas as pd
import pytest


def test_all_mr_legs_have_tnx_toggle_defaulting_true():
    """Each reused MR leg must expose _USE_TNX_GATE defaulting to True so its
    standalone behavior is unchanged; CV2 flips it off explicitly."""
    from backtest_strategies import (
        kronos_s02_stoch_revert as s02,
        kronos_s05_threebar_pull as s05,
        kronos_s06_session_sweep as s06,
        kronos_s07_crt as s07,
        kronos_s14_m5_ema_stretch as s14,
    )
    for mod in (s02, s05, s06, s07, s14):
        assert hasattr(mod, "_USE_TNX_GATE"), f"{mod.__name__} missing _USE_TNX_GATE"
        assert mod._USE_TNX_GATE is True, f"{mod.__name__}._USE_TNX_GATE must default True"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `"C:/Projects/PycharmProjects/personal/KronosStrategies/.venv/Scripts/python.exe" -m pytest tests/test_cv2_optimize.py::test_all_mr_legs_have_tnx_toggle_defaulting_true -v`
Expected: FAIL — `AttributeError`/assert: module missing `_USE_TNX_GATE`.

- [ ] **Step 3: Add the toggle to each leg**

In **each** of the 5 leg files, add the constant in the "Source-strategy knobs" block (next to the other `_` constants). For example in `kronos_s02_stoch_revert.py`, after the knob block (`_EMA_SLOW = 200`):

```python
# Optimization toggle (CV2 sets this False — the TNX feed is unavailable in the
# live replay and the gate throttles this leg; default True preserves standalone
# behavior). CV2 is the only runtime consumer of this module.
_USE_TNX_GATE = True
```

Then in each leg's `get_signal`, change the TNX guard. Find:

```python
    if not tnx_gate_open(now_utc):
        return None
```

Replace with:

```python
    if _USE_TNX_GATE and not tnx_gate_open(now_utc):
        return None
```

(S02's TNX guard is the commented `# 1. TNX ...` block — change the same `if not tnx_gate_open(now_utc):` line there.) Do this in all 5 files. Add the `_USE_TNX_GATE = True` constant to all 5 files.

- [ ] **Step 4: Run test to verify it passes**

Run: `"C:/Projects/PycharmProjects/personal/KronosStrategies/.venv/Scripts/python.exe" -m pytest tests/test_cv2_optimize.py::test_all_mr_legs_have_tnx_toggle_defaulting_true -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_cv2_optimize.py strategies/backtest_strategies/kronos_s02_stoch_revert.py strategies/backtest_strategies/kronos_s05_threebar_pull.py strategies/backtest_strategies/kronos_s06_session_sweep.py strategies/backtest_strategies/kronos_s07_crt.py strategies/backtest_strategies/kronos_s14_m5_ema_stretch.py
git commit -m "feat(cv2): add _USE_TNX_GATE toggle to MR legs (default True)"
```

---

## Task 2: S02 D1-bias + S05 liquidity-gate toggles (amplification knobs)

**Files:**
- Modify: `strategies/backtest_strategies/kronos_s02_stoch_revert.py`
- Modify: `strategies/backtest_strategies/kronos_s05_threebar_pull.py`
- Test: `tests/test_cv2_optimize.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cv2_optimize.py`:

```python
def test_s02_d1_bias_and_s05_liq_gate_toggles_default_true():
    from backtest_strategies import (
        kronos_s02_stoch_revert as s02,
        kronos_s05_threebar_pull as s05,
    )
    assert getattr(s02, "_USE_D1_BIAS") is True
    assert getattr(s05, "_USE_LIQ_GATE") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `"C:/Projects/PycharmProjects/personal/KronosStrategies/.venv/Scripts/python.exe" -m pytest tests/test_cv2_optimize.py::test_s02_d1_bias_and_s05_liq_gate_toggles_default_true -v`
Expected: FAIL — attributes missing.

- [ ] **Step 3: Add the toggles + guards**

In `kronos_s02_stoch_revert.py`, add to the knob block:

```python
_USE_D1_BIAS = True      # CV2 may relax: drop the D1 bias != -1 long gate
```

Find S02's D1 bias gate:

```python
    # HTF D1 bias gate -- drop the long when D1 bias == -1.
    d1_bias = htf_bias_from_window(w15m, "1D")
    if d1_bias == -1:
        return None
```

Replace with:

```python
    # HTF D1 bias gate -- drop the long when D1 bias == -1 (toggleable for tuning).
    if _USE_D1_BIAS:
        d1_bias = htf_bias_from_window(w15m, "1D")
        if d1_bias == -1:
            return None
```

In `kronos_s05_threebar_pull.py`, add to the knob block:

```python
_USE_LIQ_GATE = True     # CV2 may relax: drop the broken-support liquidity gate
```

Find S05's liquidity gate:

```python
    # Liquidity gate -- drop at-and-broken-below the nearest prior-day level.
    levels = [v for v in (pdh, pdl, pd_poc) if v is not None and np.isfinite(v)]
    if levels:
        nearest = min(levels, key=lambda v: abs(entry_px - v))
        zdist = abs(entry_px - nearest) / atr_val
        at_zone = (zdist <= _ZONE_ATR)
        below_level = entry_px < nearest
        if at_zone and below_level:
            return None  # broken support -- skip
```

Replace with:

```python
    # Liquidity gate -- drop at-and-broken-below the nearest prior-day level
    # (toggleable for tuning).
    if _USE_LIQ_GATE:
        levels = [v for v in (pdh, pdl, pd_poc) if v is not None and np.isfinite(v)]
        if levels:
            nearest = min(levels, key=lambda v: abs(entry_px - v))
            zdist = abs(entry_px - nearest) / atr_val
            at_zone = (zdist <= _ZONE_ATR)
            below_level = entry_px < nearest
            if at_zone and below_level:
                return None  # broken support -- skip
```

- [ ] **Step 4: Run test to verify it passes**

Run: `"C:/Projects/PycharmProjects/personal/KronosStrategies/.venv/Scripts/python.exe" -m pytest tests/test_cv2_optimize.py::test_s02_d1_bias_and_s05_liq_gate_toggles_default_true -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_cv2_optimize.py strategies/backtest_strategies/kronos_s02_stoch_revert.py strategies/backtest_strategies/kronos_s05_threebar_pull.py
git commit -m "feat(cv2): add S02 _USE_D1_BIAS and S05 _USE_LIQ_GATE toggles"
```

---

## Task 3: CV2 — `DISABLED_LEGS`, `SHORT_REQUIRE_BEARDAY`, TNX-off-at-import

**Files:**
- Modify: `strategies/backtest_strategies/kronos_combined_v2.py`
- Test: `tests/test_cv2_optimize.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cv2_optimize.py`:

```python
def _declining_5m(n: int = 35, base: float = 2000.0) -> pd.DataFrame:
    """Flat then a sharp final plunge -> fires the long fades (E1/E2/E3)."""
    rows = []
    t0 = pd.Timestamp("2026-01-13 10:00:00", tz="UTC")
    for i in range(n):
        c = base + (0.2 if i % 2 else -0.2)          # tiny noise
        if i == n - 1:
            c = base - 12.0                           # plunge on the signal bar
        o = base
        hi = max(o, c) + 0.3
        lo = min(o, c) - 0.3
        rows.append(dict(time=t0 + pd.Timedelta(minutes=5 * i),
                         open=o, high=hi, low=lo, close=c, volume=100))
    return pd.DataFrame(rows)


def test_disabled_legs_suppresses_long_fades():
    from backtest_strategies import kronos_combined_v2 as cv2
    now = pd.Timestamp("2026-01-13 10:00:00", tz="UTC").to_pydatetime()
    w5m = _declining_5m()
    w15m = w5m.head(5)                # too short for MR legs -> they skip
    cv2._BIAS_OVERRIDE = lambda _n: (0, 0)   # neutral bias: longs allowed, no shorts

    # Baseline: a long fade fires.
    cv2.DISABLED_LEGS = set()
    cv2._last_fire_bucket.clear()
    sig = cv2.get_signal(None, w5m, w15m, now)
    assert sig is not None and sig.side == "BUY" and sig.reason.startswith("CV2_E")

    # Disable all three long fades: nothing fires (no MR data, neutral bias = no shorts).
    cv2.DISABLED_LEGS = {"E1", "E2", "E3"}
    cv2._last_fire_bucket.clear()
    assert cv2.get_signal(None, w5m, w15m, now) is None

    cv2.DISABLED_LEGS = set()          # restore
    cv2._BIAS_OVERRIDE = None


def test_mr_legs_tnx_disabled_after_cv2_import():
    """Importing CV2 must turn the TNX gate OFF on every reused MR leg."""
    from backtest_strategies import kronos_combined_v2 as cv2  # noqa: F401
    from backtest_strategies import (
        kronos_s02_stoch_revert as s02,
        kronos_s05_threebar_pull as s05,
        kronos_s06_session_sweep as s06,
        kronos_s07_crt as s07,
        kronos_s14_m5_ema_stretch as s14,
    )
    for mod in (s02, s05, s06, s07, s14):
        assert mod._USE_TNX_GATE is False, f"{mod.__name__} TNX gate not disabled by CV2"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `"C:/Projects/PycharmProjects/personal/KronosStrategies/.venv/Scripts/python.exe" -m pytest tests/test_cv2_optimize.py::test_disabled_legs_suppresses_long_fades tests/test_cv2_optimize.py::test_mr_legs_tnx_disabled_after_cv2_import -v`
Expected: FAIL — `DISABLED_LEGS` missing / TNX still True.

- [ ] **Step 3: Add the CV2 controls**

In `kronos_combined_v2.py`, after the leg imports block (the `from backtest_strategies import kronos_s14...` line), add:

```python
# CV2 owns MR-leg gating. The TNX gate is unmeasurable (no TNX feed in the replay)
# and throttles the best legs, so bypass it for every reused MR leg. CV2 is the only
# runtime consumer of these modules (the standalone Kronos legs were deleted at the
# v2 cutover), so this does not affect any other live strategy.
for _legmod in (_s02, _s05, _s06, _s07, _s14):
    _legmod._USE_TNX_GATE = False
```

In the Config section, after `ENABLE_SH = False`, add:

```python
# Per-leg kill switch (takes precedence over ENABLE_*): leg codes to skip entirely,
# e.g. {"E3"} or {"E3", "S14"}. Used by the optimizer to prune drag legs.
DISABLED_LEGS: set[str] = set()

# When True, short fades (SE1/SE2) fire only on a durable-downtrend day (bias30 == -1).
# Set False to let them fire on any non-uptrend day (bias30 != +1) — optimizer Phase 4.
SHORT_REQUIRE_BEARDAY = True
```

In `get_signal`, at the very top of the `for leg in _EVAL_ORDER:` loop (before the `tf = _LEG_TF[leg]` line), add:

```python
        if leg in DISABLED_LEGS:
            continue
```

Then change the short-fade bias gate. Find:

```python
        elif leg in ("SE1", "SE2"):
            if not ENABLE_SHORTS:
                continue
            if bias30 != -1:          # shorts only on a durable downtrend day
                continue
            sig = _short_signal(w5m, leg)
```

Replace with:

```python
        elif leg in ("SE1", "SE2"):
            if not ENABLE_SHORTS:
                continue
            if SHORT_REQUIRE_BEARDAY:
                if bias30 != -1:      # shorts only on a durable downtrend day
                    continue
            elif bias30 == 1:         # relaxed: any non-uptrend day
                continue
            sig = _short_signal(w5m, leg)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `"C:/Projects/PycharmProjects/personal/KronosStrategies/.venv/Scripts/python.exe" -m pytest tests/test_cv2_optimize.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_cv2_optimize.py strategies/backtest_strategies/kronos_combined_v2.py
git commit -m "feat(cv2): DISABLED_LEGS + SHORT_REQUIRE_BEARDAY controls; bypass TNX on MR legs at import"
```

---

## Task 4: Extract `replay()` + `metrics()` from the parity backtest

**Files:**
- Modify: `strategies/backtest/backtest_combined_v2.py`
- Test: `tests/test_cv2_optimize.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cv2_optimize.py`:

```python
def test_metrics_aggregates_book_and_per_leg():
    from backtest import backtest_combined_v2 as bt
    df = pd.DataFrame([
        dict(day=pd.Timestamp("2026-01-13").date(), leg="CV2_E2", side="BUY",
             outcome="TP", pnl=10.0),
        dict(day=pd.Timestamp("2026-01-13").date(), leg="CV2_E2", side="BUY",
             outcome="SL", pnl=-5.0),
        dict(day=pd.Timestamp("2026-01-14").date(), leg="CV2_SE1", side="SELL",
             outcome="TP", pnl=4.0),
    ])
    m = bt.metrics(df)
    assert m["trades"] == 3
    assert m["net"] == pytest.approx(9.0)
    assert m["pf"] == pytest.approx(14.0 / 5.0)     # pos=14, neg=5
    assert m["maxdd"] <= 0
    assert m["per_leg"]["CV2_E2"]["net"] == pytest.approx(5.0)
    assert m["per_leg"]["CV2_SE1"]["net"] == pytest.approx(4.0)


def test_metrics_handles_empty():
    from backtest import backtest_combined_v2 as bt
    m = bt.metrics(pd.DataFrame(columns=["day", "leg", "side", "outcome", "pnl"]))
    assert m["trades"] == 0
    assert m["net"] == 0
    assert m["per_leg"] == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `"C:/Projects/PycharmProjects/personal/KronosStrategies/.venv/Scripts/python.exe" -m pytest tests/test_cv2_optimize.py::test_metrics_aggregates_book_and_per_leg tests/test_cv2_optimize.py::test_metrics_handles_empty -v`
Expected: FAIL — `module 'backtest_combined_v2' has no attribute 'metrics'`.

- [ ] **Step 3: Refactor `run()` into `replay()` + `metrics()`**

In `backtest_combined_v2.py`, replace the body of `run(days)` from the line `bias_map = build_asof_bias(cd)` through the end of the trade loop and metrics printing with calls to two new module-level functions. Add these functions ABOVE `run`:

```python
def replay(c1m, c5m, c15m, bias_map) -> pd.DataFrame:
    """Replay the CV2 book over pre-fetched candles using the module's CURRENT
    config. Returns a trades DataFrame (cols: day, leg, side, outcome, pnl)."""
    cv2._BIAS_OVERRIDE = lambda now: bias_map.get(now.date(), (0, 0))
    cv2._last_fire_bucket.clear()

    c5 = c5m.reset_index(drop=True)
    c15 = c15m.reset_index(drop=True)
    t5, t15 = c5["time"], c15["time"]
    H = c1m["high"].to_numpy(float); L = c1m["low"].to_numpy(float)
    C = c1m["close"].to_numpy(float); T = c1m["time"].to_numpy()
    n = len(c1m)

    trades = []
    for i in range(max(WIN_1M, 30), n - 1):
        t = c1m.iloc[i]["time"]; tdt = _to_utc(t)
        if not (cv2.CONFIG.session_start_hour <= tdt.hour < cv2.CONFIG.session_end_hour):
            continue
        w1m = c1m.iloc[max(0, i - WIN_1M):i + 1]
        i5 = t5.searchsorted(t, side="right");  w5w = c5.iloc[max(0, i5 - WIN_5M):i5]
        i15 = t15.searchsorted(t, side="right"); w15w = c15.iloc[max(0, i15 - WIN_15M):i15]
        try:
            sig = cv2.get_signal(w1m, w5w, w15w, tdt)
        except Exception as e:
            print(f"[err @ {tdt}] {type(e).__name__}: {e}")
            continue
        if sig is None:
            continue
        outcome, exit_px, _exit_t = simulate_exit(
            sig.side, sig.entry_price, sig.take_profit, sig.stop_loss,
            sig.max_hold_min, H, L, C, T, i + 1)
        pnl = (exit_px - sig.entry_price) if sig.side == "BUY" else (sig.entry_price - exit_px)
        trades.append(dict(day=tdt.date(), leg=sig.reason.split(":")[0], side=sig.side,
                           outcome=outcome, pnl=round(pnl, 2)))
    return pd.DataFrame(trades)


def metrics(df: pd.DataFrame) -> dict:
    """Aggregate book + per-leg stats from a trades DataFrame."""
    if df is None or len(df) == 0:
        return dict(trades=0, tpd=0.0, net=0.0, pf=float("inf"), maxdd=0.0,
                    win_days_pct=0.0, days=0, per_leg={})
    daily = df.groupby("day")["pnl"].sum()
    pos = df[df.pnl > 0]["pnl"].sum(); neg = -df[df.pnl < 0]["pnl"].sum()
    eq = daily.cumsum(); maxdd = float((eq - eq.cummax()).min())
    ndays = int(daily.shape[0])
    per_leg = {}
    for leg, g in df.groupby("leg"):
        gp = g[g.pnl > 0]["pnl"].sum(); gn = -g[g.pnl < 0]["pnl"].sum()
        per_leg[leg] = dict(trades=int(len(g)), net=float(g.pnl.sum()),
                            pf=float(gp / gn) if gn > 0 else float("inf"),
                            win_pct=float((g.pnl > 0).mean() * 100), side=g.side.iloc[0])
    return dict(trades=int(len(df)), tpd=float(len(df) / ndays), net=float(df.pnl.sum()),
                pf=float(pos / neg) if neg > 0 else float("inf"), maxdd=maxdd,
                win_days_pct=float((daily > 0).mean() * 100), days=ndays, per_leg=per_leg)
```

Now rewrite `run(days)` to use them (keep the fetch + prints identical in spirit):

```python
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
    df = replay(c1m, c5m, c15m, bias_map)
    if df.empty:
        print("No trades fired.")
        return
    m = metrics(df)
    print(f"\n{'='*70}\nCOMBINED v2 (LIVE-shape replay)  pts = $ at 0.01 lot/leg")
    print(f"  trades={m['trades']}  tpd={m['tpd']:.1f}  net={m['net']:.0f}  "
          f"PF={m['pf']:.2f}  maxDD={m['maxdd']:.0f}  "
          f"win-days={m['win_days_pct']:.0f}%  days={m['days']}")
    print("  per-leg:")
    for leg, s in sorted(m["per_leg"].items()):
        print(f"    {leg:10s} trd={s['trades']:>4} net={s['net']:>7.0f} "
              f"PF={s['pf']:>5.2f}  win%={s['win_pct']:>4.0f}  ({s['side']})")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results",
                       f"combined_v2_{datetime.now():%Y%m%d_%H%M}.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    df.to_csv(out, index=False)
    print(f"  wrote {out}")
```

- [ ] **Step 4: Run the unit tests + a parity smoke check**

Run: `"C:/Projects/PycharmProjects/personal/KronosStrategies/.venv/Scripts/python.exe" -m pytest tests/test_cv2_optimize.py -v`
Expected: all PASS.

Then verify the refactor preserved behavior (re-baseline; this is the same number as before the refactor):

Run: `"C:/Projects/PycharmProjects/personal/KronosStrategies/.venv/Scripts/python.exe" "C:/Projects/PycharmProjects/personal/KronosStrategies/strategies/backtest/backtest_combined_v2.py" --days 540`
Expected: `net=6698  PF=1.26  maxDD=-1621` (per-leg table matches the spec baseline). If it differs, the refactor changed behavior — fix before continuing.

- [ ] **Step 5: Commit**

```bash
git add tests/test_cv2_optimize.py strategies/backtest/backtest_combined_v2.py
git commit -m "refactor(cv2-bt): extract replay()/metrics() for reuse by the sweep driver"
```

---

## Task 5: Sweep driver `optimize_combined_v2.py`

**Files:**
- Create: `strategies/backtest/optimize_combined_v2.py`
- Test: `tests/test_cv2_optimize.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cv2_optimize.py`:

```python
def test_override_roundtrip():
    """apply_overrides sets module attrs; reset_defaults restores them exactly."""
    from backtest import optimize_combined_v2 as opt
    from backtest_strategies import kronos_combined_v2 as cv2
    from backtest_strategies import kronos_s02_stoch_revert as s02

    opt.reset_defaults()
    base_thr = s02._STOCH_THR
    base_disabled = set(cv2.DISABLED_LEGS)

    opt.apply_overrides({"cv2.DISABLED_LEGS": {"E3"}, "s02._STOCH_THR": 25})
    assert cv2.DISABLED_LEGS == {"E3"}
    assert s02._STOCH_THR == 25

    opt.reset_defaults()
    assert cv2.DISABLED_LEGS == base_disabled
    assert s02._STOCH_THR == base_thr
```

- [ ] **Step 2: Run test to verify it fails**

Run: `"C:/Projects/PycharmProjects/personal/KronosStrategies/.venv/Scripts/python.exe" -m pytest tests/test_cv2_optimize.py::test_override_roundtrip -v`
Expected: FAIL — module `optimize_combined_v2` does not exist.

- [ ] **Step 3: Create the driver**

Create `strategies/backtest/optimize_combined_v2.py`:

```python
"""
optimize_combined_v2.py
=======================
Fetch-once sweep driver for the CV2 Prune & Amplify optimization. Fetches the
1m/5m/15m/1d candles a single time, builds the as-of bias map once, then replays
the CV2 book under a list of named parameter configs (sequentially, in-process),
clearing per-leg dedup state between runs. Prints a ranked net/PF/maxDD table and
writes a JSON results file.

Run (needs TSDB):
  python optimize_combined_v2.py --days 540 --group prune
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from shared.tsdb_reader import fetch_candles
import backtest_strategies.kronos_combined_v2 as cv2
from backtest_strategies import kronos_s02_stoch_revert as s02
from backtest_strategies import kronos_s05_threebar_pull as s05
from backtest.backtest_combined_v2 import (
    SYMBOL, build_asof_bias, replay, metrics,
)

# Risk bound from the spec.
DD_FLOOR = -1780.0          # book maxDD must be >= this
PF_FLOOR = 1.26             # book PF must be >= this
BASELINE_NET = 6698.0

_MODS = {"cv2": cv2, "s02": s02, "s05": s05}

# Tunable (module_key, attr) pairs whose defaults we snapshot/restore.
_TUNABLES = [
    ("cv2", "DISABLED_LEGS"), ("cv2", "SHORT_REQUIRE_BEARDAY"),
    ("cv2", "_SE_TGT_ATR"), ("cv2", "_SE_STOP_ATR"),
    ("cv2", "_FADE_TGT_ATR"), ("cv2", "_FADE_STOP_ATR"),
    ("s02", "_STOCH_THR"), ("s02", "_ZONE_ATR"), ("s02", "_USE_D1_BIAS"),
    ("s05", "_USE_LIQ_GATE"),
]
_DEFAULTS = {f"{k}.{a}": copy.deepcopy(getattr(_MODS[k], a)) for k, a in _TUNABLES}

# Overrides already "locked in" from earlier phases. The executor edits this dict
# between phases to carry forward the running-best config.
LOCKED: dict = {}


def reset_defaults() -> None:
    for key, val in _DEFAULTS.items():
        mod_key, attr = key.split(".", 1)
        setattr(_MODS[mod_key], attr, copy.deepcopy(val))


def apply_overrides(overrides: dict) -> None:
    for key, val in overrides.items():
        mod_key, attr = key.split(".", 1)
        setattr(_MODS[mod_key], attr, copy.deepcopy(val))


# Named config groups. Each config is (label, overrides-on-top-of-LOCKED).
GROUPS: dict[str, list[tuple[str, dict]]] = {
    "prune": [
        ("baseline", {}),
        ("drop_E3", {"cv2.DISABLED_LEGS": {"E3"}}),
        ("drop_E3_S14", {"cv2.DISABLED_LEGS": {"E3", "S14"}}),
    ],
    "amplify": [
        ("locked", {}),
        ("s02_stoch20", {"s02._STOCH_THR": 20}),
        ("s02_stoch25", {"s02._STOCH_THR": 25}),
        ("s02_zone3", {"s02._ZONE_ATR": 3.0}),
        ("s02_zone_off", {"s02._ZONE_ATR": 999.0}),
        ("s02_no_d1", {"s02._USE_D1_BIAS": False}),
        ("s05_no_liq", {"s05._USE_LIQ_GATE": False}),
    ],
    "targets": [
        ("locked", {}),
        ("se_tgt30", {"cv2._SE_TGT_ATR": 3.0}),
        ("se_tgt35", {"cv2._SE_TGT_ATR": 3.5}),
        ("fade_tgt30", {"cv2._FADE_TGT_ATR": 3.0}),
    ],
    "bias": [
        ("locked", {}),
        ("shorts_any_nonup", {"cv2.SHORT_REQUIRE_BEARDAY": False}),
    ],
}


def _fmt(label: str, m: dict) -> str:
    ok = "OK " if (m["maxdd"] >= DD_FLOOR and m["pf"] >= PF_FLOOR
                   and m["net"] >= BASELINE_NET) else "-- "
    return (f"  {ok}{label:18s} net={m['net']:>7.0f}  PF={m['pf']:>5.2f}  "
            f"maxDD={m['maxdd']:>7.0f}  tpd={m['tpd']:>5.1f}  trades={m['trades']:>5}")


def run(days: int, group: str):
    print(f"Fetching {days}d candles once …", flush=True)
    c1m = fetch_candles("1m", days=days, symbol=SYMBOL)
    c5m = fetch_candles("5m", days=days, symbol=SYMBOL)
    c15m = fetch_candles("15m", days=days, symbol=SYMBOL)
    cd = fetch_candles("1d", days=days + 40, symbol=SYMBOL)
    if c1m.empty or cd.empty:
        print("No candle data — TSDB reachable / backfilled?")
        return
    print(f"Candles: 1m={len(c1m)} 5m={len(c5m)} 15m={len(c15m)} 1d={len(cd)}", flush=True)
    bias_map = build_asof_bias(cd)

    configs = GROUPS[group]
    print(f"\nGroup '{group}'  (LOCKED={LOCKED})  DD_FLOOR={DD_FLOOR} PF_FLOOR={PF_FLOOR}\n")
    results = []
    for label, ov in configs:
        reset_defaults()
        apply_overrides(LOCKED)
        apply_overrides(ov)
        df = replay(c1m, c5m, c15m, bias_map)
        m = metrics(df)
        results.append((label, ov, m))
        print(_fmt(label, m), flush=True)

    reset_defaults()
    results.sort(key=lambda r: r[2]["net"], reverse=True)
    print("\nRanked by net:")
    for label, _ov, m in results:
        print(_fmt(label, m))
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results",
                       f"cv2_opt_{group}_{datetime.now():%Y%m%d_%H%M}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump([{"label": l, "overrides": {k: list(v) if isinstance(v, set) else v
                                              for k, v in o.items()}, "metrics": m}
                   for l, o, m in results], f, indent=2, default=str)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=540)
    ap.add_argument("--group", default="prune", choices=list(GROUPS))
    a = ap.parse_args()
    run(a.days, a.group)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `"C:/Projects/PycharmProjects/personal/KronosStrategies/.venv/Scripts/python.exe" -m pytest tests/test_cv2_optimize.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_cv2_optimize.py strategies/backtest/optimize_combined_v2.py
git commit -m "feat(cv2): fetch-once sweep driver for Prune & Amplify"
```

---

## Task 6: Phase 1 — Prune (experiment, no code commit)

**Files:** none modified (experiment). Reads results into the spec.

- [ ] **Step 1: Run the prune sweep**

Run: `"C:/Projects/PycharmProjects/personal/KronosStrategies/.venv/Scripts/python.exe" "C:/Projects/PycharmProjects/personal/KronosStrategies/strategies/backtest/optimize_combined_v2.py" --days 540 --group prune`
Expected: a table for `baseline`, `drop_E3`, `drop_E3_S14`. `baseline` must reproduce net≈6698 / PF 1.26 / maxDD −1621.

- [ ] **Step 2: Decide the winner**

Decision rule: pick the highest-net config whose `maxDD ≥ −1780` AND `PF ≥ 1.26`. Expectation: dropping E3 raises net and PF (removes the −36/PF0.99 loser); dropping S14 too should cut maxDD with ~flat net. If `drop_E3_S14` net ≥ `drop_E3` net within ~1%, prefer it (lower exposure). Record the winner's overrides.

- [ ] **Step 3: Lock the winner into the driver**

Edit `optimize_combined_v2.py` — set `LOCKED` to the winning overrides, e.g.:

```python
LOCKED = {"cv2.DISABLED_LEGS": {"E3", "S14"}}
```

- [ ] **Step 4: Record in the spec**

Append the Phase 1 result table (net/PF/maxDD per config + chosen winner) to `docs/superpowers/specs/2026-05-25-cv2-pnl-optimization-design.md` §8.

- [ ] **Step 5: Commit**

```bash
git add strategies/backtest/optimize_combined_v2.py docs/superpowers/specs/2026-05-25-cv2-pnl-optimization-design.md
git commit -m "exp(cv2): Phase 1 prune sweep — lock winning DISABLED_LEGS"
```

---

## Task 7: Phase 2 — Amplify S02/S05 (experiment)

**Files:** none modified beyond `LOCKED` + spec.

- [ ] **Step 1: Run the amplify sweep**

Run: `"C:/Projects/PycharmProjects/personal/KronosStrategies/.venv/Scripts/python.exe" "C:/Projects/PycharmProjects/personal/KronosStrategies/strategies/backtest/optimize_combined_v2.py" --days 540 --group amplify`
Expected: a table for `locked` (the Phase-1 winner, the new reference) plus the S02/S05 relaxation variants.

- [ ] **Step 2: Decide the winner(s)**

Decision rule per variant: keep it only if book net rises vs `locked` AND `maxDD ≥ −1780` AND the relaxed leg's own PF (from the per-leg JSON) stays **≥ 1.3**. Multiple non-conflicting wins can be combined — re-run with the combined overrides as an extra ad-hoc config (add a line to the `amplify` group) to confirm they stack before locking. Reject any variant that pushes S02/S05 leg PF below 1.3 even if book net rises (avoids importing negative-edge volume).

- [ ] **Step 3: Lock + record + commit**

Update `LOCKED` with the surviving Phase-2 overrides (merged with Phase 1). Append the Phase-2 table to spec §8.

```bash
git add strategies/backtest/optimize_combined_v2.py docs/superpowers/specs/2026-05-25-cv2-pnl-optimization-design.md
git commit -m "exp(cv2): Phase 2 amplify sweep — lock S02/S05 relaxations"
```

---

## Task 8: Phase 3 — Widen workhorse targets (+ optional bias-gate)

**Files:** none modified beyond `LOCKED` + spec.

- [ ] **Step 1: Run the targets sweep**

Run: `"C:/Projects/PycharmProjects/personal/KronosStrategies/.venv/Scripts/python.exe" "C:/Projects/PycharmProjects/personal/KronosStrategies/strategies/backtest/optimize_combined_v2.py" --days 540 --group targets`
Expected: `locked` plus SE/E target-widen variants.

- [ ] **Step 2: (Optional) Run the bias sweep**

Run: `"C:/Projects/PycharmProjects/personal/KronosStrategies/.venv/Scripts/python.exe" "C:/Projects/PycharmProjects/personal/KronosStrategies/strategies/backtest/optimize_combined_v2.py" --days 540 --group bias`
Note: `shorts_any_nonup` is regime-sensitive (the sample was a gold sell-off). Accept ONLY if `maxDD ≥ −1780` still holds — it likely adds trades and DD.

- [ ] **Step 3: Decide, lock, record, commit**

Same decision rule (net up, DD ≥ −1780, PF ≥ 1.26). Update `LOCKED`, append the Phase-3 (+bias) tables to spec §8.

```bash
git add strategies/backtest/optimize_combined_v2.py docs/superpowers/specs/2026-05-25-cv2-pnl-optimization-design.md
git commit -m "exp(cv2): Phase 3 target-widen (+optional bias) sweep — lock winners"
```

---

## Task 9: Bake winner into CV2 defaults + final parity + last-40d glance + report

**Files:**
- Modify: `strategies/backtest_strategies/kronos_combined_v2.py` (and S02/S05 if their knobs changed)
- Modify: `docs/superpowers/specs/2026-05-25-cv2-pnl-optimization-design.md` (§8 report)

- [ ] **Step 1: Apply the final `LOCKED` config to module defaults**

Set the winning values as the module defaults so the live runner uses them with no env overrides. For example, if `LOCKED = {"cv2.DISABLED_LEGS": {"E3", "S14"}, "s02._STOCH_THR": 20, "cv2._SE_TGT_ATR": 3.0}`:
- In `kronos_combined_v2.py`: `DISABLED_LEGS: set[str] = {"E3", "S14"}` and `_SE_TGT_ATR = 3.0`.
- In `kronos_s02_stoch_revert.py`: `_STOCH_THR = 20`.

Use the exact winning values recorded in spec §8. Change only the knobs that won.

- [ ] **Step 2: Final parity backtest (defaults now reflect the winner)**

Run: `"C:/Projects/PycharmProjects/personal/KronosStrategies/.venv/Scripts/python.exe" "C:/Projects/PycharmProjects/personal/KronosStrategies/strategies/backtest/backtest_combined_v2.py" --days 540`
Expected: net **>** 6,698, PF ≥ 1.26, maxDD ≥ −1,780. This run uses the module defaults (no driver), so it confirms the live runner will behave the same.

- [ ] **Step 3: Last-40-day sanity glance**

Run: `"C:/Projects/PycharmProjects/personal/KronosStrategies/.venv/Scripts/python.exe" "C:/Projects/PycharmProjects/personal/KronosStrategies/strategies/backtest/backtest_combined_v2.py" --days 40`
Expected: net positive and PF ≥ ~1.2 on this recent slice. If the optimized config is net-negative or worse than a baseline-on-40d run here, **flag it in the report** — do not silently treat the change as validated.

- [ ] **Step 4: Run the full unit suite**

Run: `"C:/Projects/PycharmProjects/personal/KronosStrategies/.venv/Scripts/python.exe" -m pytest tests/test_cv2_optimize.py -v`
Expected: all PASS (the default-value changes must not break the toggle tests).

- [ ] **Step 5: Write the before/after report + commit**

Fill spec §8 with the before/after book table (baseline vs final), the per-leg deltas, the exact final config, the last-40d glance result, and any caveats (especially the TNX-bypass live-parity note and that this is a 6-month in-sample fit). Then:

```bash
git add strategies/backtest_strategies/kronos_combined_v2.py strategies/backtest_strategies/kronos_s02_stoch_revert.py strategies/backtest_strategies/kronos_s05_threebar_pull.py docs/superpowers/specs/2026-05-25-cv2-pnl-optimization-design.md
git commit -m "feat(cv2): bake Prune & Amplify winning config into defaults"
```

- [ ] **Step 6: Hand off deployment to the operator**

CV2 is live real-money. The agent does NOT deploy. Tell the operator the exact manual steps:
```powershell
cd C:\Projects\PycharmProjects\personal\KronosStrategies\strategies
python -m db.deploy_combined_v2 --commit            # refresh the Strategy/UserStrategy description if changed
cd C:\Projects\PycharmProjects\personal\KronosStrategies
docker compose up -d --build kronos_combined_v2 position_manager
```
Note: the code change alone (re-deployed compose) is what changes live behavior; the DB row only needs `--commit` if the description/params snapshot must be refreshed.

---

## Self-review notes

- **Spec coverage:** §1 baseline → Task 4 re-baseline; §2 acceptance criteria → driver `_fmt` flags + Task 6–8 decision rules; §3 TNX bypass → Tasks 1 & 3; kept gates untouched; §4 architecture → Tasks 1–5; §5 tuning sequence → Tasks 6–8; §6 deliverable → Task 9; §7 out-of-scope respected (no archD/SH configs, no instruments, no agent deploy, only last-40d glance).
- **Type consistency:** `replay(c1m,c5m,c15m,bias_map)->DataFrame` and `metrics(df)->dict` used identically in Task 4 tests, Task 5 driver, and Task 9. Override keys are dotted `"<modkey>.<attr>"` everywhere; `_MODS` covers cv2/s02/s05 (the only modules whose attrs are swept). `DISABLED_LEGS` is a `set[str]` in CV2, the test, and the driver.
- **No placeholders:** every code step shows complete code; every run step shows the exact command + expected output.
- **Known cost:** each `replay()` over ~126 trading days takes several minutes; run sweeps via `run_in_background` and read the JSON/stdout when done. If sequential proves too slow, parallelizing `GROUPS` across processes is a future enhancement (out of scope here).
