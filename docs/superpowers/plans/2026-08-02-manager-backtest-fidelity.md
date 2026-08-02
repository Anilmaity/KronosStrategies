# Manager Backtest Fidelity Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Manager Backtest tab an apples-to-apples sim-vs-live comparator — points-primary + matched-USD, deterministic entry gates modeled in-sim, and the residual trade-count gap reconciled against the `StrategySignal` audit.

**Architecture:** Extend the deployed `audit_worker` in place. Sizing/points/reconciliation live in the comparison layer (`results.py`, `live_deltas.py`, new `reconcile.py`, new `sizing.py`); the two deterministic gates live in the sim engine (they change which trades the sim takes). Pure gate helpers are shared with live via a dependency-free `shared/gate_rules.py`. Frontend gets display-only additions. Backend untouched.

**Tech Stack:** Python 3.12, SQLAlchemy ORM mirror (`shared/models.py`), pandas, pytest (scoped to `tests/` via `pytest.ini`); Next.js 14 + Apollo (frontend).

## Global Constraints

- The `backtest_worker` image must NEVER import `shared.metaapi_client` (directly or transitively) — enforced by `tests/test_mbt_worker.py`. New shared modules must be dependency-free of it.
- `Position.created_at` and `StrategySignal.signal_at` are stored as naive IST wall-clock (−5:30 vs UTC). Every window-bound comparison shifts the UTC bound by `+5:30` (`_IST_SKEW`), exactly as `live_deltas.live_summary` already does.
- No backend GraphQL change and NO Django migration — `ManagerBacktestRun.result` is a JSON blob; new keys ride inside it.
- Live points recovery uses the ENTRY `Order.quantity` (`Order.condition == "ENTRY"`, join on `position_id`) — NOT `total_buy_quantity` (0 for shorts) and NOT `quantity` (zeroes on close).
- `StrategySignal.status` ∈ {`FIRED`, `PLACED`, `REJECTED`}; `rejection_reason` set only on REJECTED.
- `Position.realized_profit_loss` is stored in PnL units (points × lots); USD = units × 100 (`_USD_PER_PNL_UNIT`).
- Run from `strategies/` (the package root the worker uses). Tests live under `strategies/tests/`.
- Scope: current live roster only. No trailing-exit preview. No strategy logic changes; the `entry_manager` refactor in Task 1 must be behavior-preserving.

---

## File Structure

- Create `strategies/shared/gate_rules.py` — pure gate predicates (parse windows, news-blackout, sl-too-tight). No DB/metaapi imports.
- Modify `strategies/strategy/entry_manager.py` — import the pure helpers instead of its local copies (behavior-preserving).
- Modify `strategies/backtest/manager_sim_engine.py` — `SimConfig` gate fields + model the two gates in the entry loop; expose sim gate-reject counts.
- Create `strategies/audit_worker/sizing.py` — infer live risk budget + matched-USD re-pricing.
- Modify `strategies/audit_worker/live_deltas.py` — points + usd blocks; live points via ENTRY-order lots.
- Modify `strategies/audit_worker/results.py` — sim points blocks; assemble new keys.
- Create `strategies/audit_worker/reconcile.py` — StrategySignal aggregation.
- Modify `strategies/audit_worker/worker.py` — call reconcile; pass new data to assemble.
- Tests: `strategies/tests/test_gate_rules.py`, `test_sim_entry_gates.py`, `test_live_points.py`, `test_sizing_matched_usd.py`, `test_reconcile.py`, `test_mbt_result_assembly.py`.
- Frontend: modify the Backtest-tab detail component in `kronos_frontend` (display-only).

---

### Task 1: Shared gate rules module + entry_manager refactor

**Files:**
- Create: `strategies/shared/gate_rules.py`
- Modify: `strategies/strategy/entry_manager.py:122-146` (replace local `_parse_utc_windows` / `_in_news_blackout`)
- Test: `strategies/tests/test_gate_rules.py`

**Interfaces:**
- Produces:
  - `parse_utc_windows(spec: str) -> list[tuple[time, time]]`
  - `in_news_blackout(now_utc: datetime, windows: list[tuple[time, time]]) -> bool`
  - `sl_too_tight(entry_price: float, stop_loss: float | None, min_dist_pts: float) -> bool`

- [ ] **Step 1: Write the failing test**

```python
# strategies/tests/test_gate_rules.py
from datetime import datetime, time
from shared.gate_rules import parse_utc_windows, in_news_blackout, sl_too_tight


def test_parse_and_blackout():
    wins = parse_utc_windows("12:25-12:45,13:55-14:05")
    assert wins == [(time(12, 25), time(12, 45)), (time(13, 55), time(14, 5))]
    assert in_news_blackout(datetime(2026, 7, 1, 12, 30), wins) is True
    assert in_news_blackout(datetime(2026, 7, 1, 12, 50), wins) is False


def test_parse_skips_malformed():
    assert parse_utc_windows("bad,12:25-12:45") == [(time(12, 25), time(12, 45))]
    assert parse_utc_windows("") == []


def test_sl_too_tight():
    assert sl_too_tight(2000.0, 1999.0, 1.5) is True    # 1.0 < 1.5
    assert sl_too_tight(2000.0, 1997.0, 1.5) is False   # 3.0 >= 1.5
    assert sl_too_tight(2000.0, None, 1.5) is False     # no stop -> never too tight
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd strategies && python -m pytest tests/test_gate_rules.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shared.gate_rules'`

- [ ] **Step 3: Write minimal implementation**

```python
# strategies/shared/gate_rules.py
"""Pure entry-gate predicates shared by live entry_manager and the offline
manager sim so the two can never drift. NO DB / broker / metaapi imports —
the Manager Backtest worker imports this transitively and must stay clean of
shared.metaapi_client (tests/test_mbt_worker.py)."""
from __future__ import annotations

from datetime import datetime, time as dtime


def parse_utc_windows(spec: str) -> list[tuple[dtime, dtime]]:
    """'12:25-12:45,13:55-14:05' -> [(time(12,25), time(12,45)), ...].
    Malformed parts are skipped, never fatal."""
    wins: list[tuple[dtime, dtime]] = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            a, b = part.split("-")
            ah, am = a.split(":")
            bh, bm = b.split(":")
            wins.append((dtime(int(ah), int(am)), dtime(int(bh), int(bm))))
        except ValueError:
            continue
    return wins


def in_news_blackout(now_utc: datetime, windows: list[tuple[dtime, dtime]]) -> bool:
    t = now_utc.time()
    return any(a <= t <= b for a, b in windows)


def sl_too_tight(entry_price: float, stop_loss: float | None,
                 min_dist_pts: float) -> bool:
    if stop_loss is None:
        return False
    return abs(float(entry_price) - float(stop_loss)) < min_dist_pts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd strategies && python -m pytest tests/test_gate_rules.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Refactor entry_manager to import the shared helpers (behavior-preserving)**

In `strategies/strategy/entry_manager.py`, add to the imports near the top:
```python
from shared.gate_rules import parse_utc_windows, in_news_blackout as _shared_in_blackout
```
Replace the local `_parse_utc_windows` definition (lines ~122-137) and `_in_news_blackout` (lines ~143-145). Keep the module-level `_BLACKOUT_WINDOWS` and preserve the existing call sites:
```python
_BLACKOUT_WINDOWS = parse_utc_windows(NEWS_BLACKOUT_UTC)


def _in_news_blackout(now_utc) -> bool:
    return _shared_in_blackout(now_utc, _BLACKOUT_WINDOWS)
```
Leave the `log.warning` on malformed windows out of the shared helper (it silently skips); that WARN was cosmetic. Do not change any call site of `_in_news_blackout`.

- [ ] **Step 6: Verify entry_manager still imports and its tests pass**

Run: `cd strategies && python -c "import strategy.entry_manager" && python -m pytest tests/ -k "entry" -q`
Expected: imports clean; existing entry tests PASS.

- [ ] **Step 7: Commit**

```bash
git add strategies/shared/gate_rules.py strategies/tests/test_gate_rules.py strategies/strategy/entry_manager.py
git commit -m "$(printf 'feat(mbt): shared gate_rules; entry_manager uses them\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 2: Model deterministic entry gates in the sim

**Files:**
- Modify: `strategies/backtest/manager_sim_engine.py` (`SimConfig` @ 24-44; entry branch @ ~546-588; `SimResult` @ ~326-332; `run_sim` return)
- Test: `strategies/tests/test_sim_entry_gates.py`

**Interfaces:**
- Consumes: `shared.gate_rules.in_news_blackout`, `sl_too_tight` (Task 1).
- Produces: `SimConfig` gains `model_entry_gates: bool = True`, `min_sl_dist_pts: float = 1.5`, `news_blackout_utc: str = "12:25-12:45"`. `SimResult` gains `entry_gate_rejects: dict[str, dict[str, int]]` — `{strategy: {"sl_too_tight": n, "news_blackout": n}}`.

- [ ] **Step 1: Write the failing test**

```python
# strategies/tests/test_sim_entry_gates.py
from datetime import datetime
from backtest.manager_sim_engine import SimConfig
from shared.gate_rules import in_news_blackout, sl_too_tight, parse_utc_windows


def test_simconfig_has_gate_fields():
    cfg = SimConfig(start=datetime(2026, 7, 1), end=datetime(2026, 7, 2))
    assert cfg.model_entry_gates is True
    assert cfg.min_sl_dist_pts == 1.5
    assert cfg.news_blackout_utc == "12:25-12:45"


def test_gate_predicates_used_by_sim_match_shared():
    # The sim must reject exactly what the shared predicates say.
    wins = parse_utc_windows("12:25-12:45")
    assert in_news_blackout(datetime(2026, 7, 1, 12, 30), wins) is True
    assert sl_too_tight(2000.0, 1999.5, 1.5) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd strategies && python -m pytest tests/test_sim_entry_gates.py -v`
Expected: FAIL — `AttributeError: 'SimConfig' object has no attribute 'model_entry_gates'`

- [ ] **Step 3: Add SimConfig fields**

In `SimConfig` (after `gated: bool = True`, before `slice_rows`):
```python
    model_entry_gates: bool = True
    min_sl_dist_pts: float = 1.5
    news_blackout_utc: str = "12:25-12:45"
```

- [ ] **Step 4: Run the field test to verify it passes**

Run: `cd strategies && python -m pytest tests/test_sim_entry_gates.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Model the gates in the entry loop**

At the top of `manager_sim_engine.py` add:
```python
from shared.gate_rules import in_news_blackout, sl_too_tight, parse_utc_windows
```
In `run_sim`, before the main bar loop, parse the windows once and init the counter:
```python
    _blackout = parse_utc_windows(cfg.news_blackout_utc) if cfg.model_entry_gates else []
    entry_gate_rejects = {s.name: {"sl_too_tight": 0, "news_blackout": 0} for s in specs}
```
In the entry branch, immediately after `if sig is not None:` and BEFORE `last_entry[spec.name] = now`, insert:
```python
                    if cfg.model_entry_gates:
                        if in_news_blackout(now, _blackout):
                            entry_gate_rejects[spec.name]["news_blackout"] += 1
                            continue
                        if sl_too_tight(sig.entry_price, sig.stop_loss,
                                        cfg.min_sl_dist_pts):
                            entry_gate_rejects[spec.name]["sl_too_tight"] += 1
                            continue
```
(Placing it before the `last_entry` stamp mirrors live: a signal rejected by a quality gate is not counted as a placed entry and does not start the cooldown clock.)

- [ ] **Step 6: Thread the counter into SimResult**

Add `entry_gate_rejects: dict[str, dict[str, int]]` to the `SimResult` dataclass, and pass `entry_gate_rejects=entry_gate_rejects` in the `return SimResult(...)`. Grep for other `SimResult(` constructions (e.g. the early empty-return at ~410) and add `entry_gate_rejects={}` there.

- [ ] **Step 7: Add a behavior test and run the full suite**

Append to `tests/test_sim_entry_gates.py` a small replay assertion using the existing fixture bars if one is available (see `tests/test_mbt_worker.py` for the fixture pattern); otherwise assert that a `run_sim` over a tiny synthetic frame set with `model_entry_gates=False` produces `entry_gate_rejects` all-zero and with `True` is a dict keyed by strategy. Then:

Run: `cd strategies && python -m pytest tests/ -q`
Expected: PASS (full suite green, including the 35 existing tests).

- [ ] **Step 8: Commit**

```bash
git add strategies/backtest/manager_sim_engine.py strategies/tests/test_sim_entry_gates.py
git commit -m "$(printf 'feat(mbt): model sl-too-tight + news-blackout gates in sim\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 3: Points blocks (sim + live via ENTRY-order lots)

**Files:**
- Modify: `strategies/audit_worker/live_deltas.py`
- Modify: `strategies/audit_worker/results.py` (`sim_per_strategy`)
- Test: `strategies/tests/test_live_points.py`

**Interfaces:**
- Produces:
  - `live_deltas.live_summary(session, names, start_utc, end_utc) -> {name: {"points": {...}, "usd": {...}}}` where `points = {pnl_pts, trades, win_rate, profit_factor}` and `usd = {pnl_usd, trades, win_rate}`.
  - `results.sim_per_strategy(trades, cfg) -> {name: {"points": {...}}}` (usd added in Task 4).
- Consumes: `shared.models.Position, Order, Strategy, UserStrategy`.

- [ ] **Step 1: Write the failing test** (uses an in-memory SQLite session like the backend tests)

```python
# strategies/tests/test_live_points.py
from datetime import datetime, timedelta
import shared.models as m
from audit_worker.live_deltas import live_summary

_IST = timedelta(hours=5, minutes=30)


def _mk(session, strat_name, realized_units, lots, created_utc):
    strat = m.Strategy(name=strat_name); session.add(strat); session.flush()
    us = m.UserStrategy(strategy_id=strat.id); session.add(us); session.flush()
    pos = m.Position(user_strategy_id=us.id, quantity=0,
                     realized_profit_loss=realized_units,
                     created_at=created_utc + _IST)   # stored naive IST
    session.add(pos); session.flush()
    session.add(m.Order(position_id=pos.id, condition="ENTRY", quantity=lots))
    session.flush()
    return strat


def test_live_points_recovered_from_entry_order(sqlite_session):
    # 10-pt win at 0.10 lots -> realized units = 1.0 ; points = 1.0/0.10 = 10
    _mk(sqlite_session, "S93 FVG Scalp", realized_units=1.0, lots=0.10,
        created_utc=datetime(2026, 7, 10, 12, 0))
    out = live_summary(sqlite_session, ["S93 FVG Scalp"],
                       datetime(2026, 7, 1), datetime(2026, 7, 31))
    blk = out["S93 FVG Scalp"]
    assert round(blk["points"]["pnl_pts"], 4) == 10.0
    assert blk["usd"]["pnl_usd"] == 100.0        # 1.0 units * 100
    assert blk["points"]["trades"] == 1
```

Add a `sqlite_session` fixture to `tests/conftest.py` if one does not exist (mirror the backend's in-memory SQLite pattern: `create_engine("sqlite://")`, `m.Base.metadata.create_all`, scoped session). If a shared fixture already exists, reuse it.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd strategies && python -m pytest tests/test_live_points.py -v`
Expected: FAIL — `KeyError: 'points'` (current `live_summary` returns flat `pnl_usd`).

- [ ] **Step 3: Rewrite `live_summary` to emit points + usd**

```python
# strategies/audit_worker/live_deltas.py  (live_summary body)
from shared.models import Position, Strategy, UserStrategy, Order

def _pf(gross_win: float, gross_loss: float):
    return round(gross_win / gross_loss, 4) if gross_loss > 0 else None

def live_summary(session, strategy_names, start_utc, end_utc):
    lo = start_utc.replace(tzinfo=None) + _IST_SKEW
    hi = end_utc.replace(tzinfo=None) + _IST_SKEW
    rows = (
        session.query(Position.id, Position.realized_profit_loss, Strategy.name,
                      Order.quantity)
        .join(UserStrategy, Position.user_strategy_id == UserStrategy.id)
        .join(Strategy, UserStrategy.strategy_id == Strategy.id)
        .join(Order, (Order.position_id == Position.id) &
                     (Order.condition == "ENTRY"))
        .filter(Strategy.name.in_(strategy_names), Position.quantity == 0,
                Position.created_at >= lo, Position.created_at <= hi)
        .all()
    )
    acc: dict[str, dict] = {}
    for _pid, realized, name, lots in rows:
        realized = float(realized or 0.0)
        lots = float(lots or 0.0)
        if lots <= 0:
            continue  # cannot recover points; skip (noted upstream)
        pts = realized / lots
        a = acc.setdefault(name, {"pts": 0.0, "usd": 0.0, "n": 0, "w": 0,
                                  "gw": 0.0, "gl": 0.0})
        a["pts"] += pts
        a["usd"] += realized * _USD_PER_PNL_UNIT
        a["n"] += 1
        if pts > 0:
            a["w"] += 1; a["gw"] += pts
        elif pts < 0:
            a["gl"] += -pts
    out: dict[str, dict] = {}
    for name, a in acc.items():
        out[name] = {
            "points": {"pnl_pts": round(a["pts"], 4), "trades": a["n"],
                       "win_rate": _win_rate(a["w"], a["n"]),
                       "profit_factor": _pf(a["gw"], a["gl"])},
            "usd": {"pnl_usd": round(a["usd"], 2), "trades": a["n"],
                    "win_rate": _win_rate(a["w"], a["n"])},
        }
    return out
```

- [ ] **Step 4: Rewrite `results.sim_per_strategy` to emit a points block**

```python
# strategies/audit_worker/results.py
def sim_per_strategy(trades, cfg):
    acc: dict[str, dict] = {}
    for t in trades:
        a = acc.setdefault(t.strategy, {"pts": 0.0, "n": 0, "w": 0,
                                        "gw": 0.0, "gl": 0.0})
        a["pts"] += t.pnl_pts
        a["n"] += 1
        if t.pnl_pts > 0:
            a["w"] += 1; a["gw"] += t.pnl_pts
        elif t.pnl_pts < 0:
            a["gl"] += -t.pnl_pts
    out = {}
    for name, a in acc.items():
        out[name] = {"points": {
            "pnl_pts": round(a["pts"], 4), "trades": a["n"],
            "win_rate": _win_rate(a["w"], a["n"]),
            "profit_factor": (round(a["gw"] / a["gl"], 4) if a["gl"] > 0 else None)}}
    return out
```

- [ ] **Step 5: Run tests**

Run: `cd strategies && python -m pytest tests/test_live_points.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add strategies/audit_worker/live_deltas.py strategies/audit_worker/results.py strategies/tests/test_live_points.py strategies/tests/conftest.py
git commit -m "$(printf 'feat(mbt): sizing-invariant points blocks (live via ENTRY-order lots)\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 4: Matched-USD via inferred live risk budget

**Files:**
- Create: `strategies/audit_worker/sizing.py`
- Modify: `strategies/audit_worker/live_deltas.py` (add usd matching in `deltas`), `results.py`
- Test: `strategies/tests/test_sizing_matched_usd.py`

**Interfaces:**
- Produces:
  - `sizing.infer_live_risk_usd(live_usd_losses: list[float], floor: int = 5) -> float | None` — median absolute USD of live losers, or None if fewer than `floor`.
  - `sizing.matched_usd(pnl_pts: float, sl_dist_pts: float, risk_usd: float, min_lot: float = 0.01, max_lot: float = 0.20) -> float` — re-price a sim trade at live's risk-sizing.
- Consumes: `TradeRecord.pnl_pts`, `TradeRecord.entry_px`, `TradeRecord.sl` (Task 2 engine).

- [ ] **Step 1: Write the failing test**

```python
# strategies/tests/test_sizing_matched_usd.py
from audit_worker.sizing import infer_live_risk_usd, matched_usd


def test_infer_uses_median_abs_of_losers():
    # losers cost ~ -R each; median |usd| == 38
    assert infer_live_risk_usd([-36.0, -38.0, -40.0, -38.0, -37.0]) == 38.0
    assert infer_live_risk_usd([-38.0, -40.0]) is None          # below floor
    assert infer_live_risk_usd([], floor=5) is None


def test_matched_usd_prices_sim_trade_like_live():
    # 3pt stop, $38 budget -> lots = 38/(3*100)=0.1267 -> clamp/round to 0.12
    # a +6pt winner -> 6 * 0.12 * 100 = $72
    usd = matched_usd(pnl_pts=6.0, sl_dist_pts=3.0, risk_usd=38.0)
    assert round(usd, 2) == 72.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd strategies && python -m pytest tests/test_sizing_matched_usd.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'audit_worker.sizing'`

- [ ] **Step 3: Implement `sizing.py`** (mirrors `entry_manager._risk_sized_qty` clamp/round)

```python
# strategies/audit_worker/sizing.py
"""Matched-USD re-pricing for the Manager Backtest fidelity comparison.

Live sizes each leg by risk (entry_manager._risk_sized_qty); the sim runs flat
lots. To compare dollars fairly, infer live's per-trade risk budget from the
window's live losers, then re-price each sim trade with the SAME clamp/round."""
from __future__ import annotations

from statistics import median

_USD_PER_PT_PER_LOT = 100.0   # XAUUSD


def infer_live_risk_usd(live_usd_losses: list[float], floor: int = 5):
    losers = [abs(x) for x in live_usd_losses if x < 0]
    if len(losers) < floor:
        return None
    return round(float(median(losers)), 2)


def matched_usd(pnl_pts: float, sl_dist_pts: float, risk_usd: float,
                min_lot: float = 0.01, max_lot: float = 0.20) -> float:
    if sl_dist_pts <= 0 or risk_usd <= 0:
        lots = min_lot
    else:
        raw = risk_usd / (sl_dist_pts * _USD_PER_PT_PER_LOT)
        lots = max(min_lot, min(max_lot, int(raw / min_lot) * min_lot))
    return round(pnl_pts * lots * _USD_PER_PT_PER_LOT, 2)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd strategies && python -m pytest tests/test_sizing_matched_usd.py -v`
Expected: PASS.

- [ ] **Step 5: Add sim matched-USD into the comparison**

Extend `results.sim_per_strategy` to also carry, per strategy, the list of `(pnl_pts, sl_dist)` needed for matching — OR (simpler) add a helper `results.add_matched_usd(sim_map, trades, risk_usd)` that walks `trades`, computes `matched_usd(t.pnl_pts, abs(t.entry_px - t.sl), risk_usd)`, and writes a `usd` block per strategy `{pnl_usd, trades, win_rate}`. Return `None` risk → omit `usd` blocks and let the caller add a note. Add a unit assertion that with `risk_usd=38` the sim `usd` block sums the per-trade `matched_usd`.

- [ ] **Step 6: Run tests + commit**

Run: `cd strategies && python -m pytest tests/test_sizing_matched_usd.py tests/test_live_points.py -q`
Expected: PASS.

```bash
git add strategies/audit_worker/sizing.py strategies/audit_worker/results.py strategies/audit_worker/live_deltas.py strategies/tests/test_sizing_matched_usd.py
git commit -m "$(printf 'feat(mbt): matched-USD via inferred live risk budget\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 5: Reconciliation vs StrategySignal

**Files:**
- Create: `strategies/audit_worker/reconcile.py`
- Test: `strategies/tests/test_reconcile.py`

**Interfaces:**
- Produces: `reconcile.reconcile(session, strategy_names, start_utc, end_utc, sim_counts: dict[str, int]) -> dict[str, dict | str]` — per name: `{"live_generated", "live_placed", "rejected": {reason: n}, "sim_trades"}`, or the string `"unavailable"` when no rows exist for the window.
- Consumes: `shared.models.StrategySignal, Strategy`.

- [ ] **Step 1: Write the failing test**

```python
# strategies/tests/test_reconcile.py
from datetime import datetime, timedelta
import shared.models as m
from audit_worker.reconcile import reconcile

_IST = timedelta(hours=5, minutes=30)


def _sig(session, strat, status, reason, when_utc):
    session.add(m.StrategySignal(
        symbol="XAU_USD", side="BUY", entry_price=2000, status=status,
        rejection_reason=reason, strategy_id=strat.id, signal_at=when_utc + _IST))
    session.flush()


def test_reconcile_groups_by_reason(sqlite_session):
    s = m.Strategy(name="S93 FVG Scalp"); sqlite_session.add(s); sqlite_session.flush()
    w = datetime(2026, 7, 10, 10, 0)
    _sig(sqlite_session, s, "PLACED", None, w)
    _sig(sqlite_session, s, "REJECTED", "entry_drift", w)
    _sig(sqlite_session, s, "REJECTED", "entry_drift", w)
    _sig(sqlite_session, s, "REJECTED", "sl_too_tight", w)
    out = reconcile(sqlite_session, ["S93 FVG Scalp"],
                    datetime(2026, 7, 1), datetime(2026, 7, 31),
                    sim_counts={"S93 FVG Scalp": 3})
    blk = out["S93 FVG Scalp"]
    assert blk["live_generated"] == 4
    assert blk["live_placed"] == 1
    assert blk["rejected"] == {"entry_drift": 2, "sl_too_tight": 1}
    assert blk["sim_trades"] == 3


def test_reconcile_unavailable_when_empty(sqlite_session):
    m_s = m.Strategy(name="S99 MSS FVG"); sqlite_session.add(m_s); sqlite_session.flush()
    out = reconcile(sqlite_session, ["S99 MSS FVG"],
                    datetime(2026, 7, 1), datetime(2026, 7, 31), sim_counts={})
    assert out["S99 MSS FVG"] == "unavailable"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd strategies && python -m pytest tests/test_reconcile.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'audit_worker.reconcile'`

- [ ] **Step 3: Implement `reconcile.py`**

```python
# strategies/audit_worker/reconcile.py
"""Explain the sim-vs-live trade-count gap from the StrategySignal audit."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from shared.models import StrategySignal, Strategy

_IST_SKEW = timedelta(hours=5, minutes=30)


def reconcile(session, strategy_names, start_utc, end_utc, sim_counts):
    lo = start_utc.replace(tzinfo=None) + _IST_SKEW
    hi = end_utc.replace(tzinfo=None) + _IST_SKEW
    rows = (
        session.query(Strategy.name, StrategySignal.status,
                      StrategySignal.rejection_reason)
        .join(Strategy, StrategySignal.strategy_id == Strategy.id)
        .filter(Strategy.name.in_(strategy_names),
                StrategySignal.signal_at >= lo, StrategySignal.signal_at <= hi)
        .all()
    )
    by_name: dict[str, list] = {}
    for name, status, reason in rows:
        by_name.setdefault(name, []).append((status, reason))
    out: dict[str, dict | str] = {}
    for name in strategy_names:
        recs = by_name.get(name)
        if not recs:
            out[name] = "unavailable"
            continue
        placed = sum(1 for st, _ in recs if st == "PLACED")
        rej = Counter(r or "unknown" for st, r in recs if st == "REJECTED")
        out[name] = {
            "live_generated": len(recs),
            "live_placed": placed,
            "rejected": dict(rej),
            "sim_trades": int(sim_counts.get(name, 0)),
        }
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd strategies && python -m pytest tests/test_reconcile.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add strategies/audit_worker/reconcile.py strategies/tests/test_reconcile.py
git commit -m "$(printf 'feat(mbt): reconcile sim trades vs StrategySignal audit\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 6: Wire into the worker + assemble the result JSON

**Files:**
- Modify: `strategies/audit_worker/worker.py` (`process_run` "comparing" phase @ ~167-176)
- Modify: `strategies/audit_worker/results.py` (`assemble` signature)
- Test: `strategies/tests/test_mbt_result_assembly.py`

**Interfaces:**
- Consumes: Tasks 3-5 (`sim_per_strategy`, `add_matched_usd`, `live_summary`, `infer_live_risk_usd`, `reconcile`).
- Produces: `results.assemble(...)` emits `per_strategy` (points+usd+reconciliation), top-level `live_risk_usd_inferred`, extended `notes`.

- [ ] **Step 1: Write the failing test**

```python
# strategies/tests/test_mbt_result_assembly.py
from audit_worker import results

def test_assemble_carries_points_and_reconciliation():
    per_strategy = {
        "S93 FVG Scalp": {
            "sim": {"points": {"pnl_pts": 12.0, "trades": 5, "win_rate": 40.0,
                               "profit_factor": 1.2}},
            "live": {"points": {"pnl_pts": -3.0, "trades": 3, "win_rate": 33.0,
                                "profit_factor": 0.8},
                     "usd": {"pnl_usd": -30.0, "trades": 3, "win_rate": 33.0}},
            "reconciliation": {"live_generated": 6, "live_placed": 3,
                               "rejected": {"entry_drift": 3}, "sim_trades": 5},
        }
    }
    out = results.assemble_v2(per_strategy=per_strategy,
                              summary={"gated": {}}, curves={}, s5_report={},
                              notes=["x"], trades_csv="/tmp/t.csv",
                              live_risk_usd_inferred=38.0,
                              kill_trips=[], paused_pct={})
    assert out["per_strategy"]["S93 FVG Scalp"]["reconciliation"]["sim_trades"] == 5
    assert out["live_risk_usd_inferred"] == 38.0
    assert "trades_csv" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd strategies && python -m pytest tests/test_mbt_result_assembly.py -v`
Expected: FAIL — `AttributeError: module 'audit_worker.results' has no attribute 'assemble_v2'`

- [ ] **Step 3: Add `assemble_v2` (keep old `assemble` callers working)**

```python
# strategies/audit_worker/results.py
def assemble_v2(*, per_strategy, summary, curves, s5_report, notes, trades_csv,
                live_risk_usd_inferred, kill_trips, paused_pct, ungated=None):
    out = {
        "summary": summary,
        "per_strategy": per_strategy,
        "equity_curve": curves,
        "s5_resolution": s5_report,
        "trades_csv": trades_csv,
        "notes": notes,
        "live_risk_usd_inferred": live_risk_usd_inferred,
        "kill_trips": kill_trips,
        "paused_pct": paused_pct,
    }
    return out
```

- [ ] **Step 4: Wire `process_run`'s comparing phase**

Replace the `progress.phase("comparing")` block (worker.py ~167-176) with:
```python
        progress.phase("comparing")
        sim_map = results.sim_per_strategy(gated.trades, cfg)
        live_map = live_deltas.live_summary(
            session, [s.name for s in specs],
            start_utc.to_pydatetime(), end_utc.to_pydatetime())
        # infer live risk from this window's live losers (usd blocks)
        live_losses = [b["usd"]["pnl_usd"] for b in live_map.values()
                       if b["usd"]["pnl_usd"] < 0]
        risk_usd = sizing.infer_live_risk_usd(live_losses)
        if risk_usd is not None:
            results.add_matched_usd(sim_map, gated.trades, risk_usd)
        else:
            notes.append("matched-USD omitted: too few live losers to infer risk")
        per_strategy = live_deltas.deltas(sim_map, live_map)   # points+usd sub-blocks
        recon = reconcile.reconcile(
            session, [s.name for s in specs],
            start_utc.to_pydatetime(), end_utc.to_pydatetime(),
            sim_counts={n: sim_map[n]["points"]["trades"] for n in sim_map})
        for name, blk in per_strategy.items():
            blk["reconciliation"] = recon.get(name, "unavailable")
```
Import `sizing, reconcile` alongside the existing `from audit_worker import ...` line. Update the `run.result = results.assemble(...)` call to `results.assemble_v2(per_strategy=per_strategy, summary=..., curves=..., s5_report=s5_report, notes=notes, trades_csv=csv_path, live_risk_usd_inferred=risk_usd, kill_trips=gated.kill_trips, paused_pct=gated.paused_pct, ungated=ungated)` — reuse the existing `summary`/`curves` construction from `assemble` (lift it into `worker.py` or keep a thin `assemble` that builds them and delegates).

- [ ] **Step 5: Update `live_deltas.deltas` to diff both sub-blocks**

`deltas(sim, live)` must produce, per name, `{"sim": s, "live": l, "delta": {...}}` where `delta` carries a `points` block (`sim.points - live.points`) and, when both have `usd`, a `usd` block. Guard `live=None` (sim knew a strategy live never traded) → `delta=None`.

- [ ] **Step 6: Run the full suite + import-cleanliness guard**

Run: `cd strategies && python -m pytest tests/ -q && python -m pytest tests/test_mbt_worker.py -q`
Expected: PASS — including the guard proving the worker imports no `shared.metaapi_client`.

- [ ] **Step 7: Commit**

```bash
git add strategies/audit_worker/worker.py strategies/audit_worker/results.py strategies/audit_worker/live_deltas.py strategies/tests/test_mbt_result_assembly.py
git commit -m "$(printf 'feat(mbt): wire points/matched-usd/reconciliation into worker result\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 7: Frontend — surface points, matched-USD, reconciliation

**Files:**
- Modify: the Backtest-tab run-detail component in `kronos_frontend` (locate via `grep -rl "per_strategy\|sim_vs_live\|ManagerBacktest\|backtest" app components GraphQL`)
- Test: manual (Netlify preview) — this repo has no unit-test harness for these views.

**Interfaces:**
- Consumes: `run.result.per_strategy[name].{sim,live,delta}.{points,usd}`, `.reconciliation`, `run.result.live_risk_usd_inferred`, `run.result.notes`.

- [ ] **Step 1: Locate the component**

Run: `cd kronos_frontend && grep -rl "per_strategy" app components GraphQL 2>/dev/null`
Read the current per-strategy table renderer.

- [ ] **Step 2: Render points as the primary table**

Replace the current per-strategy USD delta table so each row shows `sim.points.pnl_pts / pf / wr` vs `live.points.pnl_pts / pf / wr` and the points delta. Label the section "Fidelity (points — sizing-invariant)".

- [ ] **Step 3: Add matched-USD as a secondary column/toggle**

Show `sim.usd.pnl_usd` vs `live.usd.pnl_usd` under a "Money (matched to inferred live risk `${live_risk_usd_inferred}`)" heading; hide/disable when `live_risk_usd_inferred` is null and show the omission note instead.

- [ ] **Step 4: Add a reconciliation row per strategy**

For each strategy render `live_generated → placed M / rejected {reason:n…}` vs `sim_trades N`, or "reconciliation unavailable" when the value is the string.

- [ ] **Step 5: Build + visual check**

Run: `cd kronos_frontend && npm run build`
Expected: build succeeds. Verify on a local `npm run dev` against a DONE run (or Netlify preview) that the three sections render.

- [ ] **Step 6: Commit (do NOT push yet — frontend push ships live)**

```bash
git add -A && git commit -m "$(printf 'feat(mbt): show points/matched-usd/reconciliation in Backtest tab\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

### Task 8: Deploy to box + run July (operator-gated)

**Files:** none (operational). Uses the algorobos box; see the `manager-backtest-tab` memory + Deploy Gotchas.

- [ ] **Step 1: Merge `feat/mbt-fidelity` → `feat/strategy-manager`** (strategies) after review; keep frontend on its own branch until Step 4.

- [ ] **Step 2: Sync strategies to the box + rebuild the worker only**

Repo-root build context (pyarrow + strategy_manager imports):
```bash
docker compose -p kronos up -d --build backtest_worker
docker compose -p kronos logs -f backtest_worker | head -40   # expect "audit_worker starting"
```
Do NOT touch other services. Do NOT overwrite the box `compose.yml` (it has box-only services).

- [ ] **Step 3: Run July 2026 through the tab** (period_start 2026-07-01, period_end 2026-08-01), current roster. Confirm status DONE and that `result.per_strategy` carries `points`, `usd` (or the omission note), and `reconciliation`.

- [ ] **Step 4: Push frontend** → Netlify. Verify the three sections render against the July run.

- [ ] **Step 5: Read the verdict** — the **points** PF/WR per strategy is the fidelity truth; the **reconciliation** explains the trade-count gap; matched-USD is the money view. Record the numbers in the vault (`20 Strategies/Backtest Fidelity - Live vs Sim.md`) and update memory.

---

## Self-Review

**Spec coverage:** Component 1 (points+matched-USD) → Tasks 3-4, 6; Component 2 (deterministic gates) → Tasks 1-2; Component 3 (reconciliation) → Task 5; Component 4 (surfacing) → Tasks 6-7; deploy → Task 8. All spec sections covered.

**Placeholder scan:** No "TBD/TODO/handle edge cases" left; every code step carries concrete code. Task 7 is manual-test by necessity (no frontend unit harness) and says so explicitly.

**Type consistency:** `live_summary`/`sim_per_strategy` both return `{name: {"points": {...}, "usd"?: {...}}}`; `deltas` consumes that shape; `reconcile` returns `{name: dict | "unavailable"}` consumed in Task 6 Step 4; `infer_live_risk_usd`→`float|None` gates `add_matched_usd`; `assemble_v2` kwargs match the Task 6 call site. `entry_gate_rejects` shape is `{strategy: {reason: int}}` in both engine and result.

**Open follow-ups (tracked, not blocking):** `add_matched_usd` helper is specified by behavior in Task 4 Step 5 — implementer writes it against the Task 4 unit assertion; if the existing `assemble` builds `summary`/`curves`, lift that into `worker.py` or a thin wrapper (Task 6 Step 4).
