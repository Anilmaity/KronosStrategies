# Combined Suite v2 — live deployment runbook

Deploys the **whole two-sided XAU/USD scalping book as ONE strategy**
(`KRONOS_COMBINED_V2`), ported from `TradingSkills/backtest/combined_suite_v2.py`.
Real-money, MetaAPI. **Read the fidelity section before sizing up.**

---

## 1. What was built

### New strategy module
- `strategies/backtest_strategies/kronos_combined_v2.py` — one `get_signal` that
  multiplexes every leg and applies the BOS daily-bias gate:
  - **MR base** — *reuses the already-live* `kronos_s02/s05/s06/s07/s14` legs
    (battle-tested code), re-tagged with this suite's per-leg max-holds.
  - **Long fades** E1 (failed-break), E2 (Bollinger-lower), E3 (stoch-extreme),
    ported from `TradingSkills dev/mom_fade_suite.py`.
  - **Short fades** SE1 (failed-break-up), SE2 (Bollinger-upper) — fire only on a
    durable-downtrend day.
  - **BOS daily bias**: drop counter-trend LONGS on `BOS(N=20) == -1`; fire SHORTS
    only on `BOS(N=30) == -1`. Computed live from daily TSDB candles, cached/day.
  - `archD` and `SH` legs are **OFF by default** (see fidelity §4).

### Engine changes (backward-compatible — existing strategies unaffected)
| File | Change |
|------|--------|
| `backtest_strategies/base.py` | `Signal.max_hold_min` (default None); `StrategyConfig.max_concurrent_positions` (default 1) |
| `strategy/ict_engine.py` | `EntrySignal.max_hold_min` (default None) |
| `strategy/entry_manager.py` | concurrency cap is now a **count** check (`max_concurrent`); creates a **TIME_EXIT** trigger when `max_hold_min` is set; registered `KRONOS_COMBINED_V2` |
| `position_manager/position_monitor.py` | special-cases TIME_EXIT triggers (fire on wall-clock, **actively closes the MetaAPI position** via `close_position_by_id`) |
| `research_runner.py` | threads `max_hold_min` + `max_concurrent`; fetch-depth now env-configurable (`RESEARCH_DAYS_*`) |

Defaults preserve current behaviour, so the 5 live kronos legs and other
strategies are untouched until they pick up the new code on redeploy.

### Wiring
- `compose.yml` → new `kronos_combined_v2` service (enlarged windows + fetch depth).
- `strategies/db/deploy_combined_v2.py` → idempotent DB seed (dry-run by default).
- `strategies/backtest/backtest_combined_v2.py` → offline parity backtest.

---

## 2. Go-live runbook

> **Pre-req:** `position_monitor` must be running — it's what fires SL/TP **and**
> the new TIME_EXIT. SL/TP are also attached at the broker (backstop); TIME_EXIT
> is **only** enforced by the monitor.

```powershell
cd C:\Projects\PycharmProjects\personal\KronosStrategies\strategies

# 1. Preview the DB rows (no writes):
python -m db.deploy_combined_v2

# 2. (RECOMMENDED) smoke-test first — logs the exact orders, places NONE.
#    Edit compose.yml: uncomment  DRY_RUN: "true"  under kronos_combined_v2, then:
cd C:\Projects\PycharmProjects\personal\KronosStrategies
docker compose up -d --build kronos_combined_v2 position_monitor
docker compose logs -f kronos_combined_v2     # watch [CV2 FIRE] + [DRY_RUN] lines

# 3. Go live (real money):
cd C:\Projects\PycharmProjects\personal\KronosStrategies\strategies
python -m db.deploy_combined_v2 --commit        # writes Strategy + deployed UserStrategy
# re-comment DRY_RUN in compose.yml, then:
cd C:\Projects\PycharmProjects\personal\KronosStrategies
docker compose up -d --build kronos_combined_v2 position_monitor
```

Sizing is **0.01 lot/leg** (`entry_quantity=0.01`, `multiplyer=1`); the book holds
up to **12 legs concurrently** (`max_concurrent_positions=12`) → ~0.12 lot
aggregate worst case. Scale later by raising `UserStrategy.multiplyer`.

### Kill switch / rollback
```powershell
docker compose stop kronos_combined_v2          # stop new entries immediately
# then in the DB: set UserStrategy.deployed = False (or is_active = False)
```
Open positions keep their broker SL/TP; let them close or close manually in MT5.
The engine changes are backward-compatible, so no rollback of shared files is
needed to disable this one strategy.

---

## 3. Monitoring (first days)
- `docker compose logs -f kronos_combined_v2` → `[CV2 FIRE]` lines (leg, side, SL/TP,
  max-hold, bias) and the once-daily `[CV2] daily BOS bias` line.
- `[TRIGGER] TIME_EXIT fired` in `position_monitor` logs → max-holds working.
  A `[TIME_EXIT] broker close not confirmed` warning means the active close failed
  (position still protected by attached SL/TP) — investigate MetaAPI.
- DB `apis_strategysignal` rows: `FIRED → PLACED/REJECTED` (rejection
  `open_position_cap` = concurrency cap hit, expected when 12 legs are open).

---

## 4. Fidelity — how live differs from the +4869 / PF 1.70 backtest

**The deployed book is NOT identical to the TradingSkills figure. Expect divergence.**

1. **MR base carries the v15e production gates.** The live kronos legs apply
   TNX (`|z|>=0.5`), ±2h event-window, D1/H4 bias and liquidity-zone gates that the
   v2 backtest's MR legs did **not**. So the MR sub-book trades **less** and differs
   from the ungated v2 number. This was a deliberate safety choice (reuse tested
   code over re-porting 6 builders into a real-money path).
2. **`archD` and `SH` are off.** `archD` was `ABANDON` in research (no config
   net-positive in both segments) and its TNX-calm gate was later shown to be
   in-sample bias; `SH` was dropped from live in v15e. Flip `ENABLE_ARCHD` /
   `ENABLE_SH` in the module to include them (archD also needs a TNX feed).
3. **One NEW entry per 1m bar.** The backtest opened concurrent same-bar legs; live
   the runner emits one entry/min (per-leg dedup keeps each leg to one fire per HTF
   bar; collided legs enter on the next minute). Minor frequency haircut.
4. **Market fills vs next-bar-open tick fills**, and **no EOD auto-flat** (session
   gating 07–21 UTC + per-leg max-holds bound overnight risk instead).
5. **In-sample.** The underlying strategy's formal verdict was **Refine**, tuned on
   ~1.4yr. Treat live as forward-test: start at 0.01 lot, compare to the harness.

**Validate before/after go-live (needs TSDB):**
```powershell
cd C:\Projects\PycharmProjects\personal\KronosStrategies\strategies
python -m backtest.backtest_combined_v2 --days 540
```
This replays the *live-shape* book (enlarged windows, dedup, max-hold exits, as-of
bias) — the right baseline to compare live fills against, not the tick-engine
figure.

---

## 5. Tested here / not tested here
- ✅ All 6 changed/added Python files compile; combined module imports cleanly.
- ✅ `get_signal` logic: long-fade fires + dropped on bear day; short-fade fires
  only on `N30=-1`; SL/TP geometry; per-leg max-hold; per-leg dedup; bias gate.
- ✅ Harness helpers (as-of bias, SL/TP/TIME exit sim) unit-tested.
- ⚠️ **Not run here:** full historical parity backtest and live/DRY_RUN smoke test —
  both need TSDB/DB access (not touched from this session to avoid poking
  production). Run §2 step 2 and §4 yourself before trusting size.
