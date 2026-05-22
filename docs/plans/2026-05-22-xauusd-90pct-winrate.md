# XAU/USD 90% Winrate Strategy — Implementation Plan

> **For agentic workers:** Execute task-by-task via `superpowers:subagent-driven-development`. Many of the research/backtest tasks are designed to be dispatched in parallel to subagents — each subagent owns one setup family, reports back with metrics, and the orchestrator picks winners.

**Date:** 2026-05-22
**Branch:** `strategycreation`
**Author:** Anil + Claude

---

## 1. Goal

Build an automated XAU/USD intraday system that:

- Hits a **≥ 90% winrate** measured on out-of-sample (OOS) data.
- Generates **5–20 closed trades per trading day** during London + New York sessions.
- Uses the existing tick database (TimescaleDB) as the single source of truth.
- Has a hard risk envelope — no single trade can blow up the account.

## 2. Reality check (read this before starting)

A 90% winrate is **two standard deviations above what most published XAU/USD systems achieve**. To get there honestly you have to accept *one* of these trade-offs:

| Lever | What it costs |
|---|---|
| Very tight TP / wide SL | R:R degrades — even 90% winrate can be net-flat |
| Strict confluence stack | Trade count drops — may fall below 5/day floor |
| Trailing breakeven early | Some "wins" are scratches (+0.1R) that pad winrate but add no equity |
| Trade-only-A+ setups | Long flat periods, equity curve looks stair-steppy |

**The honest target is: winrate ≥ 90% AND expectancy ≥ +0.4R per trade AND ≥5 trades/day, all on OOS.** If any of the three is violated, the system is rejected even if winrate hits 90%.

If the data says 90% is unattainable while keeping expectancy positive and trade count above 5, **the orchestrator must stop and report**, not silently lower the target.

## 3. Acceptance criteria (definition of done)

The strategy ships only when **all** of these hold on a held-out **3-month OOS window**:

1. Winrate ≥ 90% (with 95% Wilson-CI lower bound ≥ 85%).
2. Trade count between **5 and 20 per trading day** on average (London + NY only).
3. Expectancy ≥ **+0.4R** per trade.
4. Max consecutive losers ≤ 3.
5. Max daily drawdown ≤ 1.5% of account.
6. No look-ahead in any feature (verified by walk-forward + shuffled-time test).
7. Slippage + spread model is realistic for XAU/USD on the user's broker (≥ 25 cents typical spread, slip ≥ 5 cents).

## 4. Architecture overview

```
[TimescaleDB ticks] -> [tick→bar aggregator] -> [feature pipeline]
                                                       |
                  +------------------------------------+
                  |                                    |
              [strategy engine]                  [risk manager]
              (entry/exit logic)            (sizing, daily caps, kill switch)
                  |                                    |
                  +------------------+-----------------+
                                     |
                              [paper/live executor]
                                     |
                              [trade ledger + analytics]
```

### Data layer
- TimescaleDB `ticks_xauusd` (existing) — bid/ask/mid + timestamp.
- Pre-built bar tables: `bars_1s`, `bars_1m`, `bars_5m`, `bars_15m`, `bars_1h`, `bars_4h`, `bars_d` materialized from ticks.
- News calendar table (ForexFactory or DailyFX) for filtering high-impact red-folder events ±15 min.

### Strategy layer
A **portfolio of confluence-gated setups** rather than one mega-strategy. Each setup is:
- Independently backtested.
- Tagged with required confluences (HTF bias, session, liquidity, FVG, etc.).
- Activated only when its confluences all align.

The 90% winrate target is met **by union of high-conviction setups, not by averaging mediocre ones**.

### Risk layer (hard guardrails, non-bypassable)
- Per-trade risk: **0.25%** of equity (configurable; default conservative).
- Per-day max loss: **1.5%** → kill switch trips, no new entries today.
- Per-week max loss: **4%** → manual review required.
- Max concurrent open trades: **2**.
- No new entries within 15 min of red-folder news.
- No entries outside London (07:00–11:00 UTC) and NY (12:00–16:00 UTC) sessions.

---

# Phase A — Foundation (data + harness)

## Task 1: Verify tick data coverage

**Files:** Create `strategies/research/data_audit.py`, `tests/test_data_audit.py`.

- [ ] **Step 1: Failing test** — `test_data_audit.py` expects `audit_ticks(symbol, start, end)` to return `{rows, gaps_minutes, first_tick, last_tick, duplicate_count}`. Use a small fixture DB.
- [ ] **Step 2: Implement** — query `ticks_xauusd`, detect gaps > 60s during session hours, count exact-timestamp duplicates.
- [ ] **Step 3: Run on real DB** — print 6-month audit. Document gaps. **Stop and report if gaps total > 4 hours** in any rolling month — bad data invalidates 90% claims.

## Task 2: Tick → bar aggregator with realistic spread

**Files:** `strategies/research/bar_builder.py`, `tests/test_bar_builder.py`.

- [ ] Build OHLC bars from ticks for 1m / 5m / 15m / 1h / 4h / 1d.
- [ ] Bar OHLC must use **mid price**; spread is tracked as a separate column (max-spread-in-bar).
- [ ] Tests: synthetic tick stream → expected OHLC, spread column correctly populated.

## Task 3: Backtest engine v2 (execution-realistic)

**Files:** `strategies/research/exec_sim.py`, `tests/test_exec_sim.py`.

- [ ] Inputs: bar series + signal (BUY/SELL with entry, SL, TP).
- [ ] Execution model:
    - Entry: fill at `bar.open + half_spread + slip` (BUY) or `bar.open - half_spread - slip` (SELL).
    - SL hit detection: intra-bar via tick replay if available, else worst-case bar wick.
    - TP hit detection: must use **bid** for sell exits, **ask** for buy exits.
    - Slip default: 5 cents on XAU/USD; configurable.
- [ ] Tests: synthetic trades, verify entry/exit prices to the cent.
- [ ] **Critical**: SL-and-TP-hit-in-same-bar must default to SL (conservative).

## Task 4: News filter

**Files:** `strategies/research/news_filter.py`, `tests/test_news_filter.py`.

- [ ] Load ForexFactory CSV (or scrape; one-time fixture for now).
- [ ] Function `is_news_window(ts, lookahead=15min, lookback=15min)` → bool.
- [ ] Tests with synthetic event list.

---

# Phase B — Setup discovery (run in PARALLEL via subagents)

> **Orchestrator instruction:** Dispatch Tasks 5a–5g as **parallel subagents** using `superpowers:subagent-driven-development`. Each subagent owns one setup family, runs Phase A's harness on it, and returns a metrics block. The orchestrator collects all metrics, ranks, and picks winners.

For **each** setup below, the subagent must produce:
- `strategies/xauusd_strategies/s90_<family>.py` — the strategy class.
- `tests/test_s90_<family>.py` — unit tests on synthetic bars.
- Backtest report at `strategies/backtest/results/s90_<family>_<date>.json` with: trades, winrate, expectancy_R, avg_R, max_dd, trades_per_day, by-hour winrate heatmap.

### Setup family A — Liquidity sweep + FVG (Task 5a)
- HTF bias from 4H structure (BoS/CHOCH).
- Entry: 1m or 5m liquidity sweep of session high/low → return into nearest FVG → fade.
- TP: opposite session liquidity (PDH/PDL).
- SL: structural beyond the swept liquidity.

### Setup family B — Killzone OTE (Task 5b)
- Trade only London (08:00–10:00 UTC) and NY (13:30–15:30 UTC).
- HTF bias confirmed on 1H.
- Entry: retracement into 62–79% (OTE) of last swing in bias direction.
- Add confluence: must coincide with an FVG or order block.

### Setup family C — Asia range break-and-retest (Task 5c)
- Define Asia high/low (00:00–06:00 UTC).
- London opens, sweeps one side, retests the broken level, reverses.
- Entry on the retest; SL beyond sweep extreme; TP at opposite Asia extreme then PDH/PDL.

### Setup family D — News-fade (Task 5d, optional / experimental)
- Red folder USD news → wait 5 min → fade the initial spike if it tagged HTF liquidity.
- High-risk; only enable if it stands alone on OOS.

### Setup family E — Mean-reversion on multi-sigma move (Task 5e)
- 5m bar move > 3× ATR(20) → fade with tight SL beyond the bar.
- Filter out news windows.

### Setup family F — Breaker block reclaim (Task 5f)
- Existing `s04_breaker.py` exists in the codebase — refactor into the new harness, re-run with the strict 90% gate.

### Setup family G — Inverse FVG continuation (Task 5g)
- After HTF FVG inverts (gets filled then violated), enter continuation in the new direction.

---

# Phase C — Confluence stacking (sequential)

## Task 6: Confluence matrix builder

**Files:** `strategies/research/confluence.py`.

- [ ] Each setup defines a `confluences: list[Confluence]` requirement.
- [ ] Confluence types: `htf_bias`, `session_killzone`, `liquidity_sweep`, `fvg_present`, `ob_present`, `volume_anomaly`, `dxy_inverse_correlation_holding`.
- [ ] A trade only fires when ALL listed confluences are true at the entry tick.

## Task 7: Per-setup confluence tuning (subagents in parallel)

- [ ] For each surviving setup from Phase B (those with raw winrate ≥ 60%), spawn a subagent.
- [ ] Subagent's job: search over confluence stacks (2-of-N, 3-of-N) to find the combination that lifts winrate to **≥ 90% on the training window**, while keeping `trades_per_day ≥ 1` for that setup.
- [ ] Report the optimal stack + IS metrics.

## Task 8: Portfolio assembly

- [ ] Combine setups whose stacked confluences clear the 90% bar.
- [ ] Verify combined portfolio yields **5–20 trades/day** average. If under 5/day, relax confluence on the highest-edge setup; if over 20/day, tighten.
- [ ] Verify trade times don't overlap excessively (correlated entries hurt risk model).

---

# Phase D — Risk management

## Task 9: Position sizer

**Files:** `position_manager/sizing.py`, `tests/test_sizing.py`.

- [ ] `size(account_equity, risk_pct, entry, stop, contract_spec) -> lots`.
- [ ] XAU/USD: 1 lot = 100 oz, $1/pip per 0.01 lot @ standard broker; verify against actual broker.
- [ ] Tests cover small account, large account, tight SL, wide SL.

## Task 10: Daily / weekly risk caps

**Files:** `position_manager/risk_caps.py`, `tests/test_risk_caps.py`.

- [ ] State machine tracking realized PnL today / this week.
- [ ] `should_allow_new_entry(now) -> (bool, reason)`.
- [ ] Daily reset at 22:00 UTC (Friday close → Sunday open handling).
- [ ] Tests: simulate a losing day hitting 1.5%, verify lockout.

## Task 11: Kill switch + circuit breakers

- [ ] If 3 consecutive losses → halt for 4 hours.
- [ ] If broker connection drops mid-trade → flatten then halt.
- [ ] If realized DD > 1.5% in a day → halt until next day.
- [ ] Tests with mocked broker events.

---

# Phase E — Validation

## Task 12: Walk-forward harness

**Files:** `strategies/research/walk_forward.py`.

- [ ] Split: 12 months data → 6 IS, 1 OOS, roll forward by 1 month, repeat.
- [ ] For each window, refit any tunable thresholds on IS only, evaluate on OOS.
- [ ] Aggregate OOS metrics.

## Task 13: Shuffled-time / look-ahead test

- [ ] Run the strategy with **timestamps shuffled randomly within session**.
- [ ] If winrate stays ≥ 90% → there is look-ahead leakage. **Fail loud, halt.**

## Task 14: Monte Carlo bootstrap

- [ ] Resample OOS trade sequence 10,000 times.
- [ ] Report 5th/50th/95th percentile equity curves, max DD distribution.
- [ ] Ship only if 5th-percentile annual return > 0 and 95th-percentile DD < 5%.

## Task 15: Slippage stress test

- [ ] Re-run OOS with slippage = 2× default, 3× default.
- [ ] Strategy must still clear winrate ≥ 80% at 3× slippage to be considered robust.

---

# Phase F — Live rollout

## Task 16: Paper trade for 14 sessions

- [ ] Run live on broker demo, log every signal.
- [ ] Daily compare: live signals vs. backtest signals on same data. **Must match within 1-tick tolerance** on entry, SL, TP.
- [ ] Investigate every divergence. **Do not go live with real money until 14 clean sessions pass.**

## Task 17: Micro-size live (0.01 lot)

- [ ] Two weeks at minimum size.
- [ ] Daily review: realized vs. expected winrate, slippage actuals vs. model.
- [ ] Stop and report any deviation > 1σ.

## Task 18: Scale-up gate

- [ ] If 4 weeks live results match OOS within 1σ on winrate AND expectancy → scale to target size.
- [ ] Otherwise, return to Phase E.

---

# Subagent dispatch summary

The following tasks **MUST** be dispatched as parallel subagents (one subagent per item):

- **Task 5a–5g** (7 setup families): each subagent owns one family, runs backtest, returns metrics JSON.
- **Task 7** (confluence tuning): one subagent per surviving setup from Task 5.

The orchestrator collects, ranks, and decides. Subagents never edit each other's files.

For sequential tasks, the orchestrator works alone using the trading-knowledge skills as reference.

# Trading-skills knowledge to consult

When working on Phase B setups, the orchestrator and each subagent should consult (via the `Skill` tool):

- `trading-knowledge` — for ICT/SMC primitives (FVG, OB, liquidity, OTE, killzones).
- `trading-knowledge-map` and the relevant pillar:
    - `pillar-01-market-structure` — BoS/CHOCH, swing logic.
    - `pillar-02-time` — session boundaries, killzones.
    - `pillar-03-news` — news filter design.
    - `pillar-04-volume` — volume-anomaly confluence.
    - `pillar-05-key-zones` — FVG/OB definitions.
    - `pillar-06-key-levels` — PDH/PDL, weekly highs/lows.
    - `pillar-08-risk-sizing` — position sizing math.
    - `pillar-09-stats-quant` — walk-forward, Monte Carlo, look-ahead detection.

# Definition of done (recap)

- All Phase A tests green.
- Phase B winners identified, each with ≥ 60% raw winrate.
- Phase C portfolio clears 90% winrate on training window with 5–20 trades/day.
- Phase E walk-forward OOS clears all acceptance criteria (§3).
- Phase F paper + micro-live matches OOS within 1σ.
- A short ship report committed at `docs/reports/2026-XX-XX-xauusd-90pct-shipped.md` summarizing IS/OOS/live metrics.

# Stop-and-report triggers

The orchestrator MUST stop and ping the human if:

- Tick data gaps > 4 hours/month detected (Task 1).
- No setup family clears 60% raw winrate after Phase B.
- Confluence stacking can't reach 90% without dropping below 5 trades/day.
- Shuffled-time test does NOT degrade performance (look-ahead leak detected).
- Live paper-trade diverges from backtest by more than 1 tick on any signal.
- Realized winrate in live drops below 80% in any 2-week window after Task 17.

Do not silently lower the bar. The user owns the call on whether to relax the goal.
