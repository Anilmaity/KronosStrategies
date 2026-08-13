# 5s backtest fidelity — status & resume notes

**Date:** 2026-08-13
**Spec:** `docs/superpowers/specs/2026-08-12-5s-backtest-fidelity-design.md`
**Branch:** `feat/strategy-manager` (all work UNCOMMITTED)
**Tests:** 1019 passing, 0 failures (`pytest tests/ -q`)

---

## Where this got to

**Live parity PASSES all seven pre-registered checks** against the 6 real,
broker-verified trades of 2026-08-12:

| check | measured | threshold |
|---|---|---|
| match rate | 100% (6/6) | ≥90% |
| outcome agreement | 100% | ≥90% |
| entry delta median / p90 | 0.110 / 0.185 pt | ≤0.15 / ≤0.40 |
| exit delta median / p90 | 0.100 / 0.197 pt | ≤0.30 / ≤0.80 |
| aggregate USD | 6.5% | ≤10% |

Every exit lands within 16 s of live; every entry within 0.22 pt.

### Reproduce

```bash
cd strategies
PYTHONPATH="<repo_root>;<repo_root>/strategies" python -m backtest.run_live_parity \
  --start 2026-08-12 --end 2026-08-13 \
  --live backtest/results/parity/live_trades_2026-08-12.csv \
  --spread 0.62 --model-entry-drift --sided-fills --exit-slippage-only \
  --entry-time-at-close --latency 0 \
  --external-pnl backtest/results/parity/external_pnl_2026-08-12.csv
```

---

## The five fidelity defects found (all ship DEFAULT-OFF)

1. **Per-strategy lookback windows.** The sim used one global `_WIN_5M=300`;
   live sets depth per strategy in compose (S94 `WIN_5M=1500`, S93/S99 `160`,
   S100 `WIN_1M=700`). S94 was simulated on a fifth of its live level history.
   → `StratSpec.win_1m/win_5m/win_15m`, `windows_for()`, `LIVE_WINDOWS`.
2. **`entry_drift` unmodelled** (`cfg.model_entry_drift`). Live's sub-minute
   gate. **Timing is load-bearing:** the fill uses `bar["close"]` (END of
   `[now, now+60)`), so the LTP must come from the **next** minute's S5 slice.
   Reading the current minute rejected a real trade (match rate → 66.7%).
3. **Kill-switch blind to unsimulated P&L** (`cfg.external_pnl`). Live's daily
   loss included the Telegram copy's −$76.50 and stopped trading at 09:11; the
   sim traded on and invented 4 post-kill-switch trades.
4. **Exit friction double-charged** (`cfg.exit_slippage_only`). A stop/target
   LEVEL is already executable, so subtracting spread/2 again over-penalises.
   Live's stops filled AT or slightly BETTER than the level.
   → exit median 0.375 → 0.100 pt, USD 11.2% → 2.6%.
5. **`entry_time` stamped at bar OPEN while filling at bar CLOSE**
   (`cfg.entry_time_at_bar_close`). **A genuine bug**, not a modelling choice:
   `max_hold` ran from a minute too early so EVERY TIME exit fired ~60 s
   premature (sim 08:47:00 vs live 08:48:06). Price-triggered exits are
   unaffected, which is why it stayed invisible until exit *times* were compared.
   Recommend making this the default after a wider validation run.

Also: `entry_drift` was lifted out of `entry_manager` into `shared.gate_rules`
as a pure predicate (ltp passed in) so sim and live cannot diverge;
`entry_manager` delegates and reads its constants at call time so existing
monkeypatching tests still bind.

---

## Honest caveats (read before trusting this)

- **n = 6 trades, one day.** Thresholds were designed for a 243-trade sample.
- **`latency=0` was chosen after seeing a sensitivity sweep** (0/5/10/15/20/30 s;
  degradation was monotonic and 0 s is the minimum-assumption value — the first
  observable price after the bar closes). It is still a parameter fitted with
  results visible and needs out-of-sample confirmation.
- **`LIVE_WINDOWS` comes from a demonstrably stale `compose.yml`** — it marks
  S93/S94/S99 `DRY_RUN=true`, yet all four traded live on 08-12 with real
  tickets. The box has drifted from the repo. Reconfirm against the running
  services before any roster decision rests on these numbers.
- Ground-truth `exit_px` must be derived from **broker USD**, not the DB's
  `Order.price` — that column holds the stop LEVEL, not a fill.
- Prod DB `Position.created_at` is **IST wall-clock labelled UTC** (5:30 ahead).
  Parity times must be real UTC.

---

## Findings on the actual question (profitability)

5s resolution is worth **~3.8% of the loss** — and the intrabar SL-before-TP
ordering I predicted would dominate flipped **1 trade in 646** over 2.5 months,
with **zero** 5s bars ever touching both levels. The fidelity story was real but
small; the edge is the problem.

Roster at 5s, live windows, realistic 0.76 spread, 0.10 lots:

| strategy | 2025-09..11 | 2026-03..05 |
|---|---|---|
| S100 M3 Combo | −225.3 pts, PF 0.79 (419 tr) | −165.0, PF 0.87 (377 tr) |
| S94 Sweep Reversal | −159.0, PF 0.56 (84 tr) | −143.4, PF 0.66 (77 tr) |
| S93 FVG Scalp | +20.1, PF 1.09 (91 tr) | −162.4, PF 0.44 (56 tr) |
| S99 MSS FVG | +76.4, PF 1.25 (130 tr) | −182.9, PF 0.57 (102 tr) |
| **book** | **−287.8, PF 0.85** | **−653.8, PF 0.73** |

S100 and S94 are negative in BOTH periods; S100 is ~62% of all trades. S93/S99
flip sign between periods. Nothing is profitable at realistic friction.

---

## Next steps

1. **Start the box** (`aws lightsail start-instance --instance-name algorobos
   --region ap-south-1`). It is STOPPED — TCP does not connect. Everything below
   depends on it.
2. Verify the stack: 12 containers under a single `-p kronos` project (no
   duplicate), `dmesg` for OOM kills, manager `armed=5/7`, **and confirm the
   fill_reconciler's dead-account 404s are gone** (that fix was never verified —
   the watch process died with the box).
3. Confirm the live windows + `DRY_RUN` flags the running services actually use.
4. Export DB ground truth for 2026-07-06..08-12 (243 trades) and re-run parity
   out-of-sample with the same flags. That is what makes this decision-grade.
5. Then P5/P6: roster re-validation and any keep/retire action.

---

## Data & environment notes

- **Local OANDA key EXISTS** at `tick_data_collector/.env` (379 B). The claim in
  CLAUDE.md that all local `.env` files are empty is wrong for this one. The
  whole pipeline therefore runs locally with no box.
- `price=MBA` works (mid+bid+ask in one request). Measured XAU spread 0.62–0.76
  vs the sim's 0.30 default.
- `.history_data/XAU_USD/` = 431 daily files of **1-second** ticks (D_M_YYYY
  names), 2025-01-01..2026-05-19 — finer than OANDA S5.
- `bars_cache` parquets now extended to 2026-08-12 via
  `backfill_history_cache.py --target YYYY-MM-DD` (new flag).
- **NEVER run the S5 backfill on the production box** — 1910 MB total / ~200 MB
  free. A month-window fetch wedged it for ~45 min on 2026-08-12 and took the
  live API down. `stream_s5` is now page-bounded and a 400 MB RAM floor guard
  refuses to run, but the box still must not be the host.
- Docker on the box is the **snap** build: `docker cp` cannot read `/tmp`; stage
  under `/home/ubuntu`.

### ⚠ Repo hygiene

`.history_data/` (**2.5 GB**) and `strategies/backtest/results/bars_cache/`
(21 MB, includes the new `s5/` parquets) are **NOT gitignored**. A `git add -A`
would attempt to commit gigabytes. Add ignore entries before staging anything.

---

## Files (all uncommitted, branch `feat/strategy-manager`)

**New**
```
strategies/backtest/s5_cache.py          S5 fetch/parse/merge/validate/partition (37 tests)
strategies/backtest/s5_exec.py           walk_exit: sequence-faithful exits (22 tests)
strategies/backtest/s5_backfill.py       CLI, streams to month partitions
strategies/backtest/tick_s5.py           local 1s ticks -> 5s bars (11 tests)
strategies/backtest/parity_harness.py    matcher/deltas/attribution/tolerance (22 tests)
strategies/backtest/run_5s_ab.py         1m-vs-5s A/B + per-strategy report
strategies/backtest/run_live_parity.py   THE parity measurement
tests/test_s5_cache.py  test_s5_exec.py  test_tick_s5.py
tests/test_parity_harness.py  test_sim_windows.py
docs/superpowers/specs/2026-08-12-5s-backtest-fidelity-design.md
strategies/backtest/results/parity/      ground truth CSVs + reports
```

**Modified**
```
strategies/backtest/manager_sim_engine.py   5 new default-OFF cfg flags, windows_for,
                                            step_exit, ltp_at_order_time, sided_fill_price
strategies/shared/gate_rules.py             entry_drift predicates lifted in
strategies/strategy/entry_manager.py        delegates to the shared predicate
strategies/backtest/backfill_history_cache.py  --target flag
tests/test_gate_rules.py                    +10 entry_drift tests
```
