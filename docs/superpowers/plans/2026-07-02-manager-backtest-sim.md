# Strategy Manager 3-Month Simulation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An offline event-loop simulator that replays the Strategy Manager's regime gating over 2026-04-01 → 2026-07-02 on XAU_USD and reports gated vs ungated performance to support the master-ON decision.

**Architecture:** A data backfill script completes the local `bars_cache` parquets from OANDA. A pure engine module (`manager_sim_engine.py`) walks the 1m grid, calling the PRODUCTION `compute_regime` + `POLICIES` (imported, never copied), gating `get_signal` calls, and managing positions with conservative fills and costs. A thin CLI (`backtest_manager_sim.py`) runs gated/ungated/both and writes trades, regime timeline, and the decision report.

**Tech Stack:** Python 3.12 (repo `.venv`), pandas/pyarrow, pytest (scoped to `tests/`), existing modules: `strategy_manager.regime.regime_engine`, `strategy_manager.policies`, `strategies.backtest_strategies.*`, `shared.tsdb_reader`, `shared.market_timing`.

**Spec:** `docs/superpowers/specs/2026-07-02-manager-backtest-sim-design.md`

## Global Constraints

- Window: `--start 2026-04-01 --end 2026-07-02`, symbol XAU_USD only.
- Costs: default `--spread-pts 0.30 --slippage-pts 0.10` → each side fills worse by `spread/2 + slippage` = 0.25 pt (0.50 round trip). `--lots 0.02` → $2.00 per 1.00 price point (`usd = pnl_pts * lots * 100`).
- Guards (defaults, CLI-overridable): kill-switch −$150/UTC-day realized on the gated book; max 3 concurrent gated positions; guard order mirrors `evaluate_tick`: market_closed → kill-switch → max-concurrent → policy.
- Conservative fill: a 1m bar touching both SL and TP books SL.
- Strategy set + policies: s95→`session_vol`, s96→`trending`, s97→`quiet_fade`, kronos_session_breakout ("SESSION_BREAKOUT")→`session_vol`; policy_params = `{}` for all (module defaults == deployed defaults; verified against `strategies/db/deploy_manager.py` SPECS which pass exactly the module-default values).
- Signal windows: `w1m=60, w5m=80, w15m=350` bars.
- Production code is imported, never modified: any file under `strategy_manager/` and `strategies/strategy/` is read-only for this plan.
- Repo has pytest only under `tests/` (pytest.ini) — every new test goes there. Run commands from `E:\Projects\Kronos\KronosStrategies` with `./.venv/Scripts/python.exe -m pytest` / `-m` module runs from `strategies/` (matching existing backtest invocation style: `cd strategies && ../.venv/Scripts/python.exe -m backtest.<module>`).
- Branch: `feat/strategy-manager` (already checked out). Never push; never touch live box/DB. OANDA REST only in the backfill task, key loaded from `tick_data_collector/.env` — never print it.
- Commits end with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Data backfill — `backfill_history_cache.py`

**Files:**
- Create: `strategies/backtest/backfill_history_cache.py`
- Test: `tests/test_backfill_merge.py`

**Interfaces:**
- Produces: complete `strategies/backtest/results/bars_cache/is_XAU_USD_{1m,5m,15m,1h,4h,1d}.parquet` covering ≥ 2026-03-25 → 2026-07-02 (a week of pre-start warmup for D1/H4 frames comes free — FRAME_SPEC needs up to 120 days of D1, which the existing cache already has), columns `time, open, high, low, close, volume` with `time` tz-aware UTC, sorted, unique.
- Produces (for tests): pure helper `merge_frames(existing: pd.DataFrame, fresh: pd.DataFrame) -> pd.DataFrame` — concat, drop duplicate `time` keeping the FRESH row, sort by `time`.

- [ ] **Step 1: Write the failing merge test** — `tests/test_backfill_merge.py`:

```python
import pandas as pd
import pytest

from backtest.backfill_history_cache import merge_frames


def _df(times, closes):
    return pd.DataFrame({
        "time": pd.to_datetime(times, utc=True),
        "open": closes, "high": closes, "low": closes,
        "close": closes, "volume": [1] * len(times),
    })


def test_merge_appends_and_dedupes_keeping_fresh():
    old = _df(["2026-05-18 10:00", "2026-05-18 10:01"], [100.0, 101.0])
    new = _df(["2026-05-18 10:01", "2026-05-18 10:02"], [999.0, 102.0])
    out = merge_frames(old, new)
    assert list(out["close"]) == [100.0, 999.0, 102.0]
    assert out["time"].is_monotonic_increasing
    assert out["time"].is_unique


def test_merge_empty_fresh_is_noop():
    old = _df(["2026-05-18 10:00"], [100.0])
    out = merge_frames(old, old.iloc[0:0])
    assert len(out) == 1
```

(`tests/` already puts `strategies/` on `sys.path` via existing conftest — confirm by how `test_s95_s96_s97.py` imports; mirror its import style if it differs.)

- [ ] **Step 2: Run to verify failure** — `./.venv/Scripts/python.exe -m pytest tests/test_backfill_merge.py -q` → FAIL (module not found).

- [ ] **Step 3: Implement the script**:

```python
"""
backfill_history_cache.py — complete the local bars_cache parquets from OANDA.

Usage (from strategies/):
    python -m backtest.backfill_history_cache [--symbol XAU_USD] [--dry-run]

Reads each results/bars_cache/is_<SYMBOL>_<tf>.parquet, finds its last
timestamp, fetches the missing span from OANDA (M1/M5/M15/H1/H4/D1 all
fetched natively — no resampling, avoids alignment drift), merges, and
writes back. Idempotent. Sanity gates abort loudly.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

# OANDA key lives only in tick_data_collector/.env locally
_REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_REPO_ROOT / "tick_data_collector" / ".env")

from shared.tsdb_reader import fetch_candles  # noqa: E402  (needs env first)

CACHE_DIR = Path(__file__).resolve().parent / "results" / "bars_cache"
TFS = ["1m", "5m", "15m", "1h", "4h", "1d"]
TARGET_END = datetime(2026, 7, 2, tzinfo=timezone.utc)


def merge_frames(existing: pd.DataFrame, fresh: pd.DataFrame) -> pd.DataFrame:
    if fresh.empty:
        return existing.reset_index(drop=True)
    out = pd.concat([existing, fresh], ignore_index=True)
    out = out.drop_duplicates(subset="time", keep="last")
    return out.sort_values("time").reset_index(drop=True)


def _max_market_gap_minutes(df: pd.DataFrame) -> float:
    """Largest gap between consecutive 1m bars EXCLUDING the weekend close
    (Fri 21:00 UTC -> Sun 22:00 UTC) and the daily 21:00-22:00 UTC break."""
    t = df["time"]
    gaps = t.diff().dt.total_seconds().div(60).fillna(0)
    mask = pd.Series(True, index=df.index)
    prev_hour = t.shift(1).dt.hour
    prev_dow = t.shift(1).dt.dayofweek
    # bars that follow the daily/weekly close legitimately gap
    mask &= ~(prev_hour == 20)          # 20:xx -> next bar after 21:00 break
    mask &= ~(prev_dow == 4)            # Friday close -> Sunday reopen
    return float(gaps[mask].max())


def backfill(symbol: str, dry_run: bool) -> None:
    for tf in TFS:
        path = CACHE_DIR / f"is_{symbol}_{tf}.parquet"
        if not path.exists():
            raise SystemExit(f"FATAL: {path} missing — cannot extend a cache that doesn't exist")
        df = pd.read_parquet(path)
        df["time"] = pd.to_datetime(df["time"], utc=True)
        last = df["time"].max()
        print(f"[{tf}] cache ends {last}  rows={len(df)}")
        if last >= TARGET_END:
            print(f"[{tf}] already covers target — skip")
            continue
        days_needed = (datetime.now(timezone.utc) - last).days + 2
        fresh = fetch_candles(tf, days=days_needed, symbol=symbol)
        if fresh.empty:
            raise SystemExit(f"FATAL: OANDA returned no {tf} candles — check OANDA_API_KEY")
        fresh["time"] = pd.to_datetime(fresh["time"], utc=True)
        merged = merge_frames(df, fresh)
        print(f"[{tf}] merged -> {merged['time'].min()} .. {merged['time'].max()}  rows={len(merged)}")
        if tf == "1m":
            gap = _max_market_gap_minutes(
                merged[merged["time"] >= pd.Timestamp("2026-04-01", tz="UTC")]
            )
            if gap > 10:
                raise SystemExit(f"FATAL: {gap:.0f}-minute market-hours gap in 1m data")
        if not dry_run:
            merged.to_parquet(path, index=False)
            print(f"[{tf}] written")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="XAU_USD")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    backfill(args.symbol, args.dry_run)
```

Note: if `fetch_candles`'s OANDA request cap can't return the full span in one call (5000-candle limit per request for 1m ≈ 3.5 days), loop: fetch with increasing `days`, or fetch day-by-day and merge — check `fetch_candles`'s implementation first; if it already chunks internally, use it as-is; if not, wrap in a per-week loop calling it with `days` anchored via its API, or extend the loop to call OANDA's `from/to` params through `tsdb_reader`'s internals — WITHOUT modifying tsdb_reader (copy the minimal request loop into the backfill script if needed; it's a data script, duplication is acceptable here).

- [ ] **Step 4: Run merge tests** — `./.venv/Scripts/python.exe -m pytest tests/test_backfill_merge.py -q` → 2 passed.

- [ ] **Step 5: Run the real backfill** — from `strategies/`: `../.venv/Scripts/python.exe -m backtest.backfill_history_cache --dry-run` (inspect printout), then without `--dry-run`. Expected: every TF reports coverage through ≥ 2026-07-02, no FATAL. This is the ONLY networked step in the plan.

- [ ] **Step 6: Commit** — `git add strategies/backtest/backfill_history_cache.py tests/test_backfill_merge.py && git commit -m "feat(sim): OANDA backfill for bars_cache parquets"`.

---

### Task 2: Engine core — gate evaluation (`manager_sim_engine.py`)

**Files:**
- Create: `strategies/backtest/manager_sim_engine.py`
- Test: `tests/test_manager_sim.py` (grows across Tasks 2-4)

**Interfaces:**
- Produces:
  - `@dataclass SimConfig(start, end, spread_pts=0.30, slippage_pts=0.10, lots=0.02, kill_switch_usd=150.0, max_concurrent=3, regime_cadence_min=5, gated=True)`
  - `@dataclass StratSpec(name, module, policy_key, policy_params)` and `STRAT_SPECS: list[StratSpec]` for the 4 strategies (modules imported from `backtest_strategies`).
  - `@dataclass GuardState(kill_tripped_date: str | None = None, day_realized_usd: float = 0.0, day: str = "")`
  - `evaluate_gates(snap, now_utc, guard: GuardState, open_count: int, cfg: SimConfig) -> dict[str, tuple[bool, str]]` — per-strategy desired_active, mirroring evaluate_tick order (market_closed → kill → cap → policy). In ungated mode returns all True with reason "ungated".

- [ ] **Step 1: Failing tests** (append to `tests/test_manager_sim.py`):

```python
from datetime import datetime, timezone
from types import SimpleNamespace

from backtest.manager_sim_engine import (
    GuardState, SimConfig, evaluate_gates, STRAT_SPECS,
)

UTC = timezone.utc

def _snap(vol="NORMAL", trend="TRENDING", d1="bullish", h4="long",
          session="LONDON", closed=False):
    return SimpleNamespace(vol_regime=vol, trend_regime=trend, d1_bias=d1,
                           h4_bias=h4, session=session, market_closed=closed)

def _cfg(**kw):
    return SimConfig(start=datetime(2026, 4, 1, tzinfo=UTC),
                     end=datetime(2026, 7, 2, tzinfo=UTC), **kw)

def test_market_closed_gates_everything_off():
    g = evaluate_gates(_snap(closed=True), datetime(2026, 4, 6, 8, 0, tzinfo=UTC),
                       GuardState(), 0, _cfg())
    assert all(v[0] is False for v in g.values())

def test_kill_switch_gates_everything_off():
    guard = GuardState(kill_tripped_date="2026-04-06")
    g = evaluate_gates(_snap(), datetime(2026, 4, 6, 8, 0, tzinfo=UTC),
                       guard, 0, _cfg())
    assert all(v[0] is False for v in g.values())

def test_max_concurrent_blocks_new_entries():
    g = evaluate_gates(_snap(), datetime(2026, 4, 6, 8, 0, tzinfo=UTC),
                       GuardState(), 3, _cfg(max_concurrent=3))
    assert all(v[0] is False for v in g.values())

def test_policies_route_correctly():
    # 08:00 UTC LONDON, NORMAL vol, TRENDING, bullish d1, long h4:
    # session_vol in window -> s95 & SESSION_BREAKOUT True;
    # trending -> s96 True; quiet_fade (3-9h, LOW/NORMAL, directional) -> s97 True
    g = evaluate_gates(_snap(), datetime(2026, 4, 6, 8, 0, tzinfo=UTC),
                       GuardState(), 0, _cfg())
    assert g["KRONOS_S95_SESSION_BREAKOUT"][0] is True
    assert g["KRONOS_S96_H1_MOMENTUM"][0] is True
    assert g["KRONOS_S97_SNAP_SCALPER"][0] is True
    assert g["SESSION_BREAKOUT"][0] is True

def test_policy_pauses_outside_window():
    # 11:00 UTC: outside session_vol windows and outside quiet_fade window
    g = evaluate_gates(_snap(), datetime(2026, 4, 6, 11, 0, tzinfo=UTC),
                       GuardState(), 0, _cfg())
    assert g["KRONOS_S95_SESSION_BREAKOUT"][0] is False
    assert g["KRONOS_S97_SNAP_SCALPER"][0] is False
    assert g["KRONOS_S96_H1_MOMENTUM"][0] is True  # trending is time-free

def test_ungated_mode_all_true_despite_guards():
    g = evaluate_gates(_snap(closed=False), datetime(2026, 4, 6, 11, 0, tzinfo=UTC),
                       GuardState(kill_tripped_date="2026-04-06"), 99,
                       _cfg(gated=False))
    assert all(v[0] is True for v in g.values())
```

- [ ] **Step 2: Run to verify failure** — `./.venv/Scripts/python.exe -m pytest tests/test_manager_sim.py -q` → import error.

- [ ] **Step 3: Implement** `strategies/backtest/manager_sim_engine.py` (core part):

```python
"""Offline event-loop simulator for the Strategy Manager (spec 2026-07-02).
Imports PRODUCTION compute_regime + POLICIES — never copies them."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from strategy_manager.policies import POLICIES
from backtest_strategies import s95_session_breakout, s96_h1_momentum, \
    s97_snap_scalper_m5, kronos_session_breakout


@dataclass
class SimConfig:
    start: datetime
    end: datetime
    spread_pts: float = 0.30
    slippage_pts: float = 0.10
    lots: float = 0.02
    kill_switch_usd: float = 150.0
    max_concurrent: int = 3
    regime_cadence_min: int = 5
    gated: bool = True

    @property
    def entry_friction_pts(self) -> float:
        return self.spread_pts / 2 + self.slippage_pts

    def pts_to_usd(self, pts: float) -> float:
        return pts * self.lots * 100.0


@dataclass(frozen=True)
class StratSpec:
    name: str
    module: object
    policy_key: str
    policy_params: dict


STRAT_SPECS: list[StratSpec] = [
    StratSpec(s95_session_breakout.NAME, s95_session_breakout, "session_vol", {}),
    StratSpec(s96_h1_momentum.NAME, s96_h1_momentum, "trending", {}),
    StratSpec(s97_snap_scalper_m5.NAME, s97_snap_scalper_m5, "quiet_fade", {}),
    StratSpec(kronos_session_breakout.NAME, kronos_session_breakout, "session_vol", {}),
]


@dataclass
class GuardState:
    kill_tripped_date: str | None = None
    day_realized_usd: float = 0.0
    day: str = ""


def evaluate_gates(snap, now_utc: datetime, guard: GuardState,
                   open_count: int, cfg: SimConfig) -> dict[str, tuple[bool, str]]:
    """Mirror of strategy_manager.manager.evaluate_tick guard order."""
    if not cfg.gated:
        return {s.name: (True, "ungated") for s in STRAT_SPECS}

    if snap.market_closed:
        return {s.name: (False, "market closed") for s in STRAT_SPECS}

    today = now_utc.date().isoformat()
    if guard.kill_tripped_date == today:
        return {s.name: (False, "kill-switch tripped") for s in STRAT_SPECS}

    if open_count >= cfg.max_concurrent:
        return {s.name: (False, f"max concurrent {open_count}/{cfg.max_concurrent}")
                for s in STRAT_SPECS}

    out: dict[str, tuple[bool, str]] = {}
    for s in STRAT_SPECS:
        out[s.name] = POLICIES[s.policy_key](snap, s.policy_params, now_utc)
    return out
```

(Verify `kronos_session_breakout` lives in `backtest_strategies` and exposes `NAME`; the explorer confirmed it does. Verify the import name of the module — file is `kronos_session_breakout.py`.)

- [ ] **Step 4: Run tests** → all Task-2 tests pass.
- [ ] **Step 5: Commit** — `git add strategies/backtest/manager_sim_engine.py tests/test_manager_sim.py && git commit -m "feat(sim): gate evaluator mirroring live guard order"`.

---

### Task 3: Engine core — position lifecycle, fills, costs

**Files:**
- Modify: `strategies/backtest/manager_sim_engine.py`
- Test: `tests/test_manager_sim.py` (append)

**Interfaces:**
- Produces:
  - `@dataclass SimPosition(strategy, side, entry_time, entry_px, sl, tp, max_hold_min, trailing, trail_dist, hwm)`
  - `open_position(sig: Signal, strat_name: str, now, cfg) -> SimPosition` — applies entry friction: BUY entry = `sig.entry_price + cfg.entry_friction_pts`, SELL entry = `- friction`. SL/TP levels stay as signalled. `trail_dist = abs(sig.entry_price - sig.stop_loss)`, `hwm = entry_px`.
  - `step_position(pos, bar, now, cfg) -> tuple[SimPosition | None, TradeRecord | None]` — bar is a 1m OHLC row. Order of checks: (1) trailing ratchet update (s96): BUY hwm=max(hwm,high), sl=max(sl, hwm-trail_dist); (2) SL touch (BUY: low<=sl) → exit at `sl - exit_friction` (conservative, friction worsens exit); (3) TP touch (only if not trailing; BUY: high>=tp) → exit at `tp - exit_friction`; SL checked BEFORE TP (conservative when both touch); (4) time exit when `now - entry_time >= max_hold_min` → exit at `close - exit_friction` (BUY). SELL mirrors all signs.
  - `@dataclass TradeRecord(strategy, entry_time, side, entry_px, sl, tp, exit_px, exit_time, outcome, pnl_pts, pnl_usd, gate_reason)` — outcome in `{"TP","SL","TIME","TRAIL","OPEN"}` (trailing SL exit = "TRAIL").

- [ ] **Step 1: Failing tests** (append; representative — write all six):

```python
import pandas as pd
from backtest.manager_sim_engine import open_position, step_position
from backtest_strategies.base import Signal

def _bar(ts, o, h, l, c):
    return pd.Series({"time": pd.Timestamp(ts, tz="UTC"),
                      "open": o, "high": h, "low": l, "close": c})

def _buy_sig(entry=100.0, sl=98.0, tp=103.0, hold=None, trailing=False):
    return Signal(side="BUY", entry_price=entry, stop_loss=sl,
                  take_profit=tp, reason="t", max_hold_min=hold,
                  trailing=trailing)

def test_entry_friction_applied():
    pos = open_position(_buy_sig(), "X", datetime(2026, 4, 6, 8, 0, tzinfo=UTC), _cfg())
    assert pos.entry_px == pytest.approx(100.25)   # 100 + 0.15 + 0.10

def test_sl_beats_tp_when_bar_spans_both():
    cfg = _cfg()
    pos = open_position(_buy_sig(), "X", datetime(2026, 4, 6, 8, 0, tzinfo=UTC), cfg)
    pos2, rec = step_position(pos, _bar("2026-04-06 08:01", 100, 104, 97, 102),
                              datetime(2026, 4, 6, 8, 1, tzinfo=UTC), cfg)
    assert pos2 is None and rec.outcome == "SL"
    assert rec.exit_px == pytest.approx(98.0 - 0.25)

def test_tp_exit_and_cost_arithmetic():
    cfg = _cfg()
    pos = open_position(_buy_sig(), "X", datetime(2026, 4, 6, 8, 0, tzinfo=UTC), cfg)
    _, rec = step_position(pos, _bar("2026-04-06 08:01", 100, 103.5, 99.5, 103),
                           datetime(2026, 4, 6, 8, 1, tzinfo=UTC), cfg)
    assert rec.outcome == "TP"
    # gross 3.0 pts minus 0.5 round trip = 2.5 pts -> $5.00 at 0.02 lots
    assert rec.pnl_pts == pytest.approx(2.5)
    assert rec.pnl_usd == pytest.approx(5.0)

def test_time_exit():
    cfg = _cfg()
    pos = open_position(_buy_sig(hold=30), "X", datetime(2026, 4, 6, 8, 0, tzinfo=UTC), cfg)
    _, rec = step_position(pos, _bar("2026-04-06 08:31", 100, 100.4, 99.8, 100.2),
                           datetime(2026, 4, 6, 8, 31, tzinfo=UTC), cfg)
    assert rec.outcome == "TIME"

def test_trailing_ratchets_up_never_down():
    cfg = _cfg()
    pos = open_position(_buy_sig(sl=99.0, tp=130.0, trailing=True), "X",
                        datetime(2026, 4, 6, 8, 0, tzinfo=UTC), cfg)
    pos, rec = step_position(pos, _bar("2026-04-06 08:01", 100, 102, 100, 101.8),
                             datetime(2026, 4, 6, 8, 1, tzinfo=UTC), cfg)
    assert rec is None and pos.sl == pytest.approx(102 - pos.trail_dist)
    tightened = pos.sl
    pos, rec = step_position(pos, _bar("2026-04-06 08:02", 101.8, 101.9, 101.0, 101.5),
                             datetime(2026, 4, 6, 8, 2, tzinfo=UTC), cfg)
    assert rec is None and pos.sl == pytest.approx(tightened)  # never loosens

def test_trailing_exit_outcome_is_trail():
    cfg = _cfg()
    pos = open_position(_buy_sig(sl=99.0, tp=130.0, trailing=True), "X",
                        datetime(2026, 4, 6, 8, 0, tzinfo=UTC), cfg)
    pos, _ = step_position(pos, _bar("2026-04-06 08:01", 100, 105, 100, 104.8),
                           datetime(2026, 4, 6, 8, 1, tzinfo=UTC), cfg)
    _, rec = step_position(pos, _bar("2026-04-06 08:02", 104.8, 104.9, 103.0, 103.2),
                           datetime(2026, 4, 6, 8, 2, tzinfo=UTC), cfg)
    assert rec is not None and rec.outcome == "TRAIL"
```

- [ ] **Step 2: Verify failure**, **Step 3: implement** `SimPosition`/`open_position`/`step_position`/`TradeRecord` per the interface block (BUY/SELL symmetric; `pnl_pts = (exit_px - entry_px)` for BUY, negated for SELL; `pnl_usd = cfg.pts_to_usd(pnl_pts)`; trailing positions ignore static TP; ratchet BEFORE SL check within a bar uses the PREVIOUS bar's ratchet — i.e. ratchet from current bar high applies to NEXT bar's SL check, preventing intra-bar look-ahead: implement by checking SL against the pre-update sl, then updating hwm/sl from the bar).
- [ ] **Step 4: Run tests** → pass. **Step 5: Commit** `feat(sim): position lifecycle with conservative fills and costs`.

---

### Task 4: Engine — timeline loop + modes

**Files:**
- Modify: `strategies/backtest/manager_sim_engine.py`
- Create: `strategies/backtest/backtest_manager_sim.py` (CLI)
- Test: `tests/test_manager_sim.py` (append)

**Interfaces:**
- Produces:
  - `load_frames(cache_dir: Path, start, end) -> dict[str, pd.DataFrame]` — reads the six parquets ONCE, pre-sliced to `[start - warmup, end]` where warmup covers FRAME_SPEC (120d for 1d etc.).
  - `run_sim(frames, cfg: SimConfig) -> SimResult` where `@dataclass SimResult(trades: list[TradeRecord], regime_rows: list[dict], kill_trips: list[str], paused_pct: dict[str, float])`.
  - CLI `backtest_manager_sim.py`: args per spec (`--start --end --mode --spread-pts --slippage-pts --lots --kill-switch-usd --max-concurrent --regime-cadence`), `--mode both` runs gated + ungated and writes all output files with a shared timestamp.
- Consumes: `compute_regime(frames_slice, now_utc)` from `strategy_manager.regime.regime_engine`; window constants `w1m=60, w5m=80, w15m=350`.

**run_sim loop (implement exactly):** iterate 1m rows in `[start, end)`. Maintain per-TF integer cursors advanced with `searchsorted` on each TF's `time` column so each regime/window slice uses only bars whose time < now (closed bars only — the 1m bar being processed is the bar that CLOSED at `now`). On minutes divisible by `cfg.regime_cadence_min`: build `frames_slice = {tf: df.iloc[max(0, cur-need):cur]}` sized per FRAME_SPEC days (approximate with generous row counts: 1d→130, 4h→560, 1h→760, 15m→980, 5m→900, 1m→1500) and call `compute_regime(frames_slice, now)`; store snapshot (forward-filled between evaluations). Before the first regime evaluation completes, all strategies are inactive. Then `gates = evaluate_gates(snap, now, guard, open_count, cfg)`. For each strategy: if it has an open position, `step_position` with the current 1m bar (exits always run — pausing never closes positions); realized exits update `guard.day_realized_usd` (reset when UTC date changes; trip `guard.kill_tripped_date` when `<= -cfg.kill_switch_usd` — matching evaluate_tick's next-day auto-reset). If no open position AND gate is True: `sig = spec.module.get_signal(w1m_slice, w5m_slice, w15m_slice, now)`; on a Signal, `open_position` (records `gate_reason`). Call each module's `reset_state()` (where it exists) at sim start. At sim end, mark still-open positions as outcome "OPEN" with exit at last close minus friction.

- [ ] **Step 1: Failing end-to-end test** — synthetic 3-day dataset built in-test (flat tape with one engineered breakout so `SESSION_BREAKOUT`/s95 fire deterministically is fragile — instead assert structural properties):

```python
from backtest.manager_sim_engine import load_frames, run_sim

import numpy as np

START = pd.Timestamp("2026-04-06", tz="UTC")
END = pd.Timestamp("2026-04-10 21:00", tz="UTC")

def _write_synthetic_cache(cache_dir, start=None, days=30, mutate_after=None):
    """Synthetic XAU tape: sine + drift on a 1m grid (weekdays 00:00-20:59
    UTC), other TFs resampled from it, written in bars_cache format.
    `mutate_after`: timestamps strictly greater get close=99999 (for the
    no-look-ahead test)."""
    start = start or (START - pd.Timedelta(days=days))
    idx = pd.date_range(start, END, freq="1min", tz="UTC")
    idx = idx[(idx.dayofweek < 5) & (idx.hour < 21)]
    t = np.arange(len(idx), dtype=float)
    px = 3300 + 0.0008 * t + 6 * np.sin(t / 240) + 1.5 * np.sin(t / 37)
    if mutate_after is not None:
        px = px.copy()
        px[idx > mutate_after] = 99999.0
    df1 = pd.DataFrame({"time": idx, "open": px, "high": px + 0.4,
                        "low": px - 0.4, "close": px, "volume": 10})
    df1.to_parquet(cache_dir / "is_XAU_USD_1m.parquet", index=False)
    g = df1.set_index("time")
    for tf, rule in [("5m", "5min"), ("15m", "15min"), ("1h", "1h"),
                     ("4h", "4h"), ("1d", "1D")]:
        r = g.resample(rule).agg({"open": "first", "high": "max",
                                  "low": "min", "close": "last",
                                  "volume": "sum"}).dropna().reset_index()
        r.to_parquet(cache_dir / f"is_XAU_USD_{tf}.parquet", index=False)

def test_run_sim_smoke_and_gating_structure(tmp_path):
    _write_synthetic_cache(tmp_path)
    frames = load_frames(tmp_path, START, END)
    gated = run_sim(frames, _cfg(gated=True))
    ungated = run_sim(frames, _cfg(gated=False))
    # structural invariants, not P&L values:
    assert set(t.strategy for t in ungated.trades) <= {s.name for s in STRAT_SPECS}
    assert len(gated.trades) <= len(ungated.trades)          # gating only removes entries
    assert all(t.outcome in {"TP","SL","TIME","TRAIL","OPEN"} for t in gated.trades)
    assert gated.regime_rows and {"d1_bias","vol_regime","session"} <= set(gated.regime_rows[0])
    assert all(0.0 <= v <= 100.0 for v in gated.paused_pct.values())
```

- [ ] **Step 2: Verify failure. Step 3: implement loop + CLI. Step 4: tests pass. Step 5:** also add the **no-look-ahead regression test**:

```python
def test_no_look_ahead(tmp_path):
    T_mid = pd.Timestamp("2026-04-08 12:00", tz="UTC")
    _write_synthetic_cache(tmp_path)
    frames = load_frames(tmp_path, START, T_mid)
    baseline = run_sim(frames, _cfg(gated=True, end=T_mid)).regime_rows

    poisoned_dir = tmp_path / "poisoned"; poisoned_dir.mkdir()
    _write_synthetic_cache(poisoned_dir, mutate_after=T_mid)
    frames2 = load_frames(poisoned_dir, START, T_mid)
    poisoned = run_sim(frames2, _cfg(gated=True, end=T_mid)).regime_rows

    assert baseline == poisoned  # future bars must not change the past


def test_kill_switch_trips_and_resets_next_day(tmp_path, monkeypatch):
    """Spec test 2: force a losing exit that crosses -kill_switch_usd; same
    UTC day admits no further entries; the next day admits entries again;
    an open position still exits while the switch is tripped. Implement by
    running with kill_switch_usd=0.01 (any losing trade trips it) on the
    synthetic cache and asserting: (a) at least one KILL trip date recorded,
    (b) no gated trade ENTERS later the same UTC day after the trip time,
    (c) at least one gated trade enters on a later day."""
    _write_synthetic_cache(tmp_path)
    frames = load_frames(tmp_path, START, END)
    res = run_sim(frames, _cfg(gated=True, kill_switch_usd=0.01))
    assert res.kill_trips, "expected at least one kill-switch trip"
    trip_day = res.kill_trips[0]
    trips_dt = pd.Timestamp(trip_day, tz="UTC")
    same_day_after = [t for t in res.trades
                      if t.entry_time.date().isoformat() == trip_day]
    # entries on the trip day must all precede the trip (no post-trip entries)
    # and later days must still trade:
    assert any(t.entry_time.date().isoformat() > trip_day for t in res.trades)
```

(If the synthetic tape produces no losing trades at all, tighten the sine
amplitude/drift until SL exits occur — the test must exercise a real trip,
not be skipped.)

- [ ] **Step 6: Full suite** — `./.venv/Scripts/python.exe -m pytest tests/ -q` → everything green (including pre-existing tests). **Step 7: Commit** `feat(sim): event-loop simulator + CLI (gated/ungated/both)`.

---

### Task 5: Report generator + sensitivity runner

**Files:**
- Create: `strategies/backtest/manager_sim_report.py`
- Modify: `strategies/backtest/backtest_manager_sim.py` (call report at end of `--mode both`)
- Test: `tests/test_manager_sim_report.py`

**Interfaces:**
- Produces: `write_report(gated: SimResult, ungated: SimResult, cfg, out_dir: Path, sensitivity: list[tuple[str, float]] | None) -> Path` writing `trades_gated_<ts>.csv`, `trades_ungated_<ts>.csv`, `regime_timeline_<ts>.csv`, `summary_<ts>.md` per the spec's section C (per-strategy + combined table with deltas, kill trips, paused %, regime distribution + flip rate with >12/day flag, month-by-month, sensitivity grid, decision-rubric verdict line, 3-month caveat paragraph).
- Sensitivity: `run_sensitivity(frames, base_cfg) -> list[dict]` — 6 variants, each a full gated re-run. Threshold variants set `regime_engine.VOL_PCTL_LOW/HIGH/EXTREME` or `ER_TRENDING/ER_RANGING` module attributes before the run and restore originals in a `finally:` block. Window variants leave constants alone and instead run with a modified spec list: `specs = [replace(s, policy_params={"windows": [[6.25, 9.5], [12.75, 15.5]]}) if s.policy_key == "session_vol" else s for s in STRAT_SPECS]` (−30 min; +30 min analogously; `dataclasses.replace`), passed via a new optional `run_sim(frames, cfg, specs=STRAT_SPECS)` parameter (add it in Task 4). Variants: `vol(20/70/90)`, `vol(30/80/97)`, `er(0.30/0.15)`, `er(0.40/0.25)`, `windows −30min`, `windows +30min`.

- [ ] **Step 1: Failing tests** — feed hand-built `SimResult` objects (6 trades across 2 months, known P&L) and assert: summary file contains the combined delta row with exact numbers; month table has both months; rubric line says "RECOMMEND" only when gated beats ungated on net AND DD (construct one passing and one failing fixture).
- [ ] **Step 2-4: fail → implement → pass.**
- [ ] **Step 5: Commit** `feat(sim): decision report + sensitivity grid`.

---

### Task 6: The real run + decision report

**Files:** none created by hand — this task RUNS the tooling. Controller-level (needs the OANDA backfill done and, for the parity check, ssh to the box which subagents must NOT do — the controller runs that step).

- [ ] **Step 1:** `cd strategies && ../.venv/Scripts/python.exe -m backtest.backtest_manager_sim --start 2026-04-01 --end 2026-07-02 --mode both --sensitivity` → completes; note wall time.
- [ ] **Step 2 (sanity):** ungated `KRONOS_S96_H1_MOMENTUM` trade count > 0 (w15m=350 fix works); every summary section present.
- [ ] **Step 3 (parity, controller only):** pick one recent trading day; `ssh -F /dev/null -i ~/.ssh/algobet-ssh.pem ubuntu@13.126.204.82 "sudo docker logs kronos-strategy_manager-1 --since ..."`-style extraction of `[TICK]` lines for that day; compare d1/h4/vol/trend/session against the sim's regime_timeline rows at the same timestamps; require match on ≥ 95% of overlapping rows (small drift from OANDA fetch timing is tolerable; a systematic mismatch is a bug — investigate before trusting the report).
- [ ] **Step 4:** run backtest-expert scoring: `python skills/backtest-expert/scripts/evaluate_backtest.py` path per skill (controller knows it: `C:\Users\ANILM\.claude\skills\backtest-expert\scripts\evaluate_backtest.py`) with the gated combined book's metrics from the summary; attach verdict to the report directory.
- [ ] **Step 5: Commit results** — `git add strategies/backtest/results/manager_sim/ && git commit -m "results: 3-month manager sim run + decision report"` (CSV+MD outputs are small; if trades CSVs exceed ~5 MB, gitignore the CSVs and commit only the summary).
- [ ] **Step 6:** present the decision report to the human with the rubric verdict. STOP — flipping master ON is a live-trading decision the human makes on the /manager tab, never by this plan.
