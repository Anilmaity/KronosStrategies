# Strategy Manager 3-Month Simulation & Backtest

**Date:** 2026-07-02
**Repo:** KronosStrategies (branch: feat/strategy-manager)
**Status:** Approved by user (approach: fully integrated event-loop sim)

## Goal

Decide whether to flip the Strategy Manager master switch ON: simulate the
manager's regime gating over the last 3 months (2026-04-01 → 2026-07-02,
XAU_USD) and compare the gated portfolio against the same strategies ungated.

## Decisions (locked)

1. **Purpose:** go-live decision support — headline output is gated vs
   ungated P&L/drawdown per strategy and combined.
2. **Data:** backfill the missing window (2026-05-20 → 2026-07-02) from OANDA
   into the local `bars_cache` parquets; sim runs fully offline afterward.
3. **Architecture:** fully integrated event-loop simulator (not two-pass):
   one walk over the 1m timeline computing regime, evaluating policies,
   calling `get_signal` only for gated-active strategies, managing positions
   and global guards in-loop.
4. **Strategies under management (4):** `s95_session_breakout` (policy
   `session_vol`), `s96_h1_momentum` (`trending`), `s97_snap_scalper_m5`
   (`quiet_fade`), `kronos_session_breakout` "Session Breakout M5 ORB"
   (`session_vol`, arm=OFF live — simulated under `always_on` for the
   baseline AND under `session_vol` for the gated run, since adopting it
   under management is the open question).

## Components

### A. Data backfill — `strategies/backtest/backfill_history_cache.py`

- Verify actual `results/bars_cache/is_XAU_USD_*.parquet` coverage with
  pandas/pyarrow (explorer estimate: through ~2026-05-19; trust the file, not
  the estimate).
- Fetch from OANDA via `shared.tsdb_reader.fetch_candles` (needs
  `OANDA_API_KEY` — present in `tick_data_collector/.env`; load with
  python-dotenv from that path explicitly): M1 for the missing span in
  chunks; M5/M15 resampled from M1; H1, H4, D1 fetched directly from OANDA
  (native granularities, avoids alignment drift).
- Append + dedupe on timestamp, write back to the same parquet files.
  Idempotent: re-running fetches only what is still missing.
- Sanity gates: no gaps > 10 min during market hours in M1; bar counts per
  TF consistent (5×M1≈M5 etc.); abort loudly on empty fetch.

### B. Simulator — `strategies/backtest/backtest_manager_sim.py`

CLI (run from `strategies/`):
```
python -m backtest.backtest_manager_sim --start 2026-04-01 --end 2026-07-02 \
  --mode both --spread-pts 0.30 --slippage-pts 0.10 --lots 0.02 \
  [--kill-switch-usd 150] [--max-concurrent 3] [--regime-cadence 5]
```

**Timeline loop.** Iterate the 1m bar grid. On every `--regime-cadence`-minute
boundary (default 5): build `frames` dict (`1d/4h/1h/15m/5m/1m` slices ending
at the last CLOSED bar strictly before `now_utc` — the forming bar is
excluded on every timeframe) and call the production
`strategy_manager.regime.regime_engine.compute_regime(frames, now_utc)`.
Implementation must first verify whether `compute_regime` reads the `1m`/`5m`
frames for anything cadence-sensitive; if it does, drop cadence to 1 minute
(the flag exists for exactly this). Regime snapshots forward-fill between
evaluations.

**Gating.** Per strategy: `desired_active, reason =
POLICIES[policy_key](snap, params, now_utc)` using the production
`strategy_manager.policies` module and the live-deployed `policy_params`
(mirror the values seeded by `db/deploy_manager.py`: s97 `{"window":[3.0,9.0],
"vol_regimes":["LOW","NORMAL"]}`, s95 `{"windows":[[6.75,10.0],[13.25,16.0]],
"vol_regimes":["NORMAL","HIGH"]}`, s96/session_live per deploy script — read
the script, do not guess). Global guards run before policies in the same
order as `strategy_manager.manager.evaluate_tick` (read it and mirror
exactly): market closed → all inactive; kill-switch — when the gated book's
realized P&L for the UTC day ≤ −`--kill-switch-usd`, everything inactive
until the next UTC day; max-concurrent — while ≥ `--max-concurrent` gated
positions are open, no new entries.

**Signal generation.** Only for active strategies, call
`get_signal(w1m, w5m, w15m, now_utc)` with windows `w1m=60, w5m=80, w15m=350`
(≥ s96's 304-row minimum; the others tolerate larger windows). Paused
strategies are NOT called — this matches the live runner semantics and lets
s95's per-session dedup behave as it would live. Module state (`_fired` sets
etc.) is reset via each module's `reset_state()` at sim start.

**Position management (every 1m bar).** One position per strategy at a time
(live convention). SL/TP touch on bar high/low; if a single 1m bar spans both
SL and TP → count as SL (conservative). `max_hold_min` time exit at bar
close. s96 chandelier trailing per its CONFIG, evaluated on the same cadence
the live position manager would (1m). Costs: entry fills worse by
`(spread/2 + slippage)`, exits worse by the same → default 0.50 pt round
trip (~1.7× typical XAU friction, per punish-the-strategy doctrine).
$ conversion: XAU_USD, `--lots 0.02` → $2 per 1.00 price point.

**Modes.** `gated` (policies + guards live), `ungated` (all policies forced
True, guards off), `both` (runs both in one invocation on the same data —
the headline comparison). Same engine code path for both; the only
difference is the gate evaluation.

### C. Outputs — `strategies/backtest/results/manager_sim/`

- `trades_gated_<ts>.csv`, `trades_ungated_<ts>.csv` — columns: strategy,
  entry_time, side, entry_px, sl, tp, exit_px, exit_time, outcome
  (TP/SL/TIME/TRAIL/OPEN), pnl_pts, pnl_usd, gated_reason_at_entry.
- `regime_timeline_<ts>.csv` — one row per regime evaluation: ts, d1_bias,
  h4_bias, vol_regime, trend_regime, session, market_closed, and per-strategy
  desired_active + reason.
- `summary_<ts>.md` — the decision report:
  - Per-strategy and combined: net pts, net $, trades, win rate, PF, max DD
    ($ and %), avg hold — gated vs ungated side by side, with deltas.
  - Kill-switch trips (count + dates), % time each strategy was
    policy-paused, regime distribution (% of time per vol/trend/session
    bucket), regime flip rate (changes/day — flag if > 12/day average).
  - Month-by-month (Apr / May / Jun–Jul 2): gated vs ungated net $ per
    strategy — gating must help (or not hurt) in ≥ 2 of 3 months.
  - Sensitivity grid (each = full re-run, ~6 variants): vol thresholds
    (25/75/95 → 20/70/90 and 30/80/97), session windows ±30 min, ER
    thresholds (0.35/0.20 → 0.30/0.15 and 0.40/0.25). Report combined gated
    net $ per variant — looking for a plateau.
  - backtest-expert `evaluate_backtest.py` scoring of the gated combined
    book (trades, win rate, avg win/loss, max DD) with its verdict attached.
  - Printed caveat: 3 months ≈ one regime sample; supports arming
    PAPER/small-size, not a permanent verdict.

**Decision rubric (stated in the report header):** recommend master ON iff
gated ≥ ungated on net $ AND max DD is lower AND the edge holds in ≥ 2 of 3
months AND no single sensitivity variant flips the sign of the combined
gated-vs-ungated delta.

### D. Tests — `tests/test_manager_sim.py`

Offline, synthetic frames (style of `tests/test_s95_s96_s97.py`):
1. Gate application: a signal fired while policy-inactive is not entered; the
   same signal while active is entered.
2. Kill-switch: crossing −$150 intraday blocks subsequent entries same UTC
   day; entries resume next day; open positions still exit.
3. Max-concurrent: 4th signal rejected while 3 open, admitted after one closes.
4. Conservative fill: bar spanning SL and TP books SL.
5. Costs: pnl_pts of a known TP trade reduced by exactly the round-trip
   friction; $ conversion at 0.02 lots.
6. No look-ahead: mutating bars after time T does not change any regime
   snapshot ≤ T.
7. Time exit + trailing: max_hold_min honored; chandelier ratchet only
   tightens.

## Constraints

- Pure-offline sim: no DB, no MetaAPI, no live-box access. OANDA REST used
  ONLY by the backfill script.
- Production code reuse: `compute_regime`, `POLICIES`, ict_engine helpers are
  imported, never copied. Any API friction (e.g. frames dict shape) is
  adapted in the sim, not by editing strategy_manager/ modules.
- No changes to live services, compose, or deployed strategies.
- Branch: continue on `feat/strategy-manager` in KronosStrategies (local).
- Python 3.12 `.venv`; pytest scoped to `tests/` per pytest.ini.

## Out of scope

- Multi-symbol (XAG/BTC) simulation; equity-curve-based dynamic sizing;
  intra-bar tick-level fills; simulating the Telegram copy-trader or any
  non-managed strategy; changing policies/thresholds based on results
  (that's a follow-up decision after reading the report).

## Verification

- `pytest tests/test_manager_sim.py -q` green; full `pytest tests/ -q` still
  green.
- Backfill sanity gates pass; bars_cache coverage printout spans
  2026-04-01 → 2026-07-02 with no >10-min market-hours gaps.
- `--mode both` full run completes; summary_<ts>.md contains every section
  listed above; ungated s96 trade count > 0 (guards the w15m window fix).
- Spot parity check: one manually-picked day where the regime timeline is
  compared against the live service's logged `[TICK]` lines for the same day
  (the box logs d1/h4/vol/trend/session every 60s) — classifications must
  match on the overlapping fields.
