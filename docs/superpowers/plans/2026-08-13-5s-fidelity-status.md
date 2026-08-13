# 5s backtest fidelity — status & resume notes

**Date:** 2026-08-13
**Spec:** `docs/superpowers/specs/2026-08-12-5s-backtest-fidelity-design.md`
**Branch:** `feat/strategy-manager` — committed as `8a8e3b9` (not pushed)
**Tests:** 1019 passing, 0 failures (`pytest tests/ -q`)

---

## Where this got to

> [!warning] SUPERSEDED by the out-of-sample run (2026-08-14)
> The n=6 pass below **did not generalise**. Over the full 242-trade window the
> parity **FAILS** 4 of 7 checks — match rate 66.9%, entry Δ median 0.205 /
> p90 0.699, USD 51.7% off. Execution modelling holds up (outcome agreement
> 100%, exit Δ median 0.100); **trade selection does not**. See
> "Parity run — DONE, and it FAILS out-of-sample" below before relying on any
> sim P&L. All six 08-12 trades still match — that day was unrepresentative.

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

Steps 1–4 are **CLOSED** (2026-08-13/14) — see "Session log 2026-08-14".
The out-of-sample parity ran and **FAILED**, which changes what comes next:

1. **Do not run P5/P6 roster keep/retire off this sim yet.** The sim misses
   predominantly *losing* trades (80 misses netting −905 USD), so its P&L is
   biased optimistic by selection, not just noisy.
2. **Fix trade selection, starting with S100** — it produced 112 of the 152
   invented trades. Then the S93/S99 blindness (26 misses each).
3. Leave the fill model alone: exit Δ and outcome agreement already pass.

---

## Session log 2026-08-14

### Box outage — RESOLVED (step 1, 2)

The box was **never STOPPED**; Lightsail reported `running` throughout. It was
**wedged**: `StatusCheckFailed=1` and `NetworkOut=0` at every datapoint from
**2026-08-12 ~19:00 UTC to 2026-08-13 18:27 UTC** — ~23 h of dark production.
Root cause was **DNS death** (`Temporary failure in name resolution` for both
the RDS host and OANDA) plus an OOM-kill of a python process at 08-13 01:25 UTC.
The kernel stayed alive (sshd logged a connection at 19:03), which is exactly why
it looked stopped from outside. Fixed with `aws lightsail reboot-instance`; SSH
returned in ~2 min.

Post-reboot verification: 12 containers up (11 under `-p kronos` + the backend,
**no duplicate project**), manager `armed=5/7 open_pos=0/3`, kill-switch
auto-reset (it had tripped 08-12). `open_pos=0` means the 23 h without
`position_manager` left **no** position hanging without TIME_EXIT enforcement.

**AWS creds are usable** — the earlier "41 chars, invalid" note was wrong.
`.env_aws` is NOT `KEY=value`; it is freeform (`access id : AKIA…` / `token : …`),
so naive parsing loads nothing and the CLI silently falls back to the `[default]`
profile in `~/.aws/credentials`, **which is the invalid one**. Split on the first
`:`. Verified via `sts get-caller-identity` (account 086769945463, user `anil`).

**Prophylactic:** the box had **zero swap** on 2 GB RAM — that is what turns
memory pressure into a total wedge rather than one dead process. Added a 2 GB
swapfile (`dd`, not `fallocate`, to avoid a holey file), `chmod 600`, persisted
in `/etc/fstab` (backed up first) and **verified by `swapoff` + `swapon -a`** so
a reboot is guaranteed to pick it up. `vm.swappiness=10` in
`/etc/sysctl.d/99-kronos-swap.conf`.

### Live windows RECONFIRMED — the harness was right (step 3)

The running containers set the windows under a **`RESEARCH_` prefix**:
S93 `RESEARCH_WIN_5M=160`, S94 `1500`, S99 `160`, S100 `RESEARCH_WIN_1M=700` —
**exactly `LIVE_WINDOWS`**. Also live: `RESEARCH_DAYS_5M` 5 (S93/S99) vs 11 (S94),
`RESEARCH_DAYS_15M=5` all, S100 `RESEARCH_DAYS_1M=3`. And `DRY_RUN=false` on all
four, so the repo `compose.yml` (which says `DRY_RUN=true`) is confirmed stale —
the box has drifted, but **in the harness's favour**. The caveat in this doc's
"Honest caveats" about LIVE_WINDOWS being unverified is now discharged.

> **Never grep container env with a loose substring.** `grep -iE "win|days"`
> matches `WINPROFX_META_TOKEN` and prints the live MetaAPI JWT. Anchor it:
> `grep -E "^(DRY_RUN|RESEARCH_)"`. A token was exposed this way on 08-13 and
> should be rotated.

### Ground truth EXPORTED (step 4) — 242, not 243

New `strategies/backtest/export_ground_truth.py` (the spec called for it; it did
not exist). Runs on the box inside `backtest_worker`:

```bash
# stage (avoids snap-docker's inability to docker cp from /tmp)
ssh ... 'sudo docker exec -i kronos-backtest_worker-1 sh -c "cat > /tmp/export_ground_truth.py"' \
    < strategies/backtest/export_ground_truth.py
ssh ... 'sudo docker exec -e PYTHONPATH=/app kronos-backtest_worker-1 \
    python /tmp/export_ground_truth.py --start 2026-07-06 --end 2026-08-13 \
    --out /tmp/live_trades_2026-07-06_2026-08-12.csv \
    --external-out /tmp/external_pnl_2026-07-06_2026-08-12.csv'
```

Output (now in `backtest/results/parity/`): **242 roster trades** —
S100 97, S99 55, S94 47, **S93 43** — plus 63 external-P&L rows (+595.59 USD).

Three things the export settled:

1. **The pre-registered 243 includes a non-trade.** Ticket `23594170` is a
   `TEST_FILL_VALIDATION` probe (2026-07-07, 0.01 lots, 4 s, no exit order at
   all). Not a strategy trade, unreproducible by the sim; excluded and reported.
2. **Strategy naming would have matched ZERO trades.** `parity_harness` compares
   names by equality; the sim uses module constants (`KRONOS_S100_M3_COMBO`) but
   `Strategy.name` is the human label (`S100 M3 Combo Scalper`). The authoritative
   mapping is in the DB: **`Strategy.json_data['variation']`**, stamped by the
   deploy scripts. The exporter emits that, not a hardcoded table.
3. **The old ground truth's exit times were our DB write time, not the fill.**
   Cross-validating the 08-12 overlap: all 6 tickets present, identical strategy,
   side, entry time/px, exit px, outcome, lots, and an exactly matching USD total
   (−134.45) — but three exit times differ. Verified on ticket `110770242`:
   hand-built used `Order.created_at` 03:43:16.69 while the broker
   `DEAL_ENTRY_OUT` filled at 03:43:03.38, **13.3 s earlier**. The new export
   takes broker truth throughout. This does **not** disturb the 08-12 pass (exit
   *timing* was never a pre-registered tolerance — only exit price in points, USD,
   match rate and outcome agreement), but the doc's descriptive "every exit lands
   within 16 s of live" was measured against a proxy that runs late and will move
   when recomputed.

Design notes now baked into the exporter: the window is filtered on
`broker_deals.deal_time` (real UTC) so `Position.created_at`'s IST-labelled
+5:30 never enters; linkage is the ENTRY order's ticket
(`Order.broker_order_id` == MetaAPI `positionId`); `fill_reconciler`'s
sliced-position volume guard is reused (0 sliced rows in this window); `usd` is
profit + all commissions + swap. Segments: Funding Pips `97fab5dc` n=126
(−408.77 USD, 07-07→07-30 01:42) and Winprofx `3eefc570` n=116 (+676.52 USD,
07-30 07:21→08-12). Outcomes: 158 SL / 80 TP / 4 TIME.

### Parity run — DONE, and it **FAILS** out-of-sample

Report: `results/parity/live_parity_2026-07-06_2026-08-13.md`.
S5 backfilled locally (**never on the box**): 2026-07 + 2026-08 partitions,
458,763 bars, wall-clock coverage 72.7% / 63.8%.

| check | measured | threshold | |
|---|---|---|---|
| match rate | **66.9%** (162/242) | ≥90% | **FAIL** |
| outcome agreement | 100% | ≥90% | PASS |
| entry Δ median / p90 | **0.205** / **0.699** pt | ≤0.15 / ≤0.40 | **FAIL** |
| exit Δ median / p90 | 0.100 / 0.615 pt | ≤0.30 / ≤0.80 | PASS |
| aggregate USD | **51.7%** off | ≤10% | **FAIL** |

**The n=6 result did not generalise.** All six 08-12 trades still match — that day
was simply not representative of the 5.5-week window.

**Where the failure lives: trade SELECTION, not execution.** On the 162 trades
both sides agree to take, outcome agreement is **100%** and exit Δ median is
0.100 pt. The sim models *a trade it takes* well. It just takes a different set:
80 live trades missed, **152 invented** (sim fired 314 vs live's 242).

**The USD number is worse than it looks — read the aggregation.** `summarise()`
sums `usd_live`/`usd_sim` over **matched pairs only**, so the tolerance compares
+1172.96 (matched live) against +1779.95 (matched sim). But the *full* 242-trade
live book is **+267.75**. The gap is the misses:

* **80 missed live trades net −905.21 USD** — 59 losses vs 21 wins.
* 152 invented sim trades net −193.95 USD — 107 losses vs 45 wins.

So the sim is **skipping predominantly losing trades**, which inflates the
matched-subset live figure to 4.4× the real book. Any roster P&L read off this
sim is flattered by a selection bias, not merely noisy. **This is the single most
decision-relevant finding of the campaign** and it cuts against trusting sim P&L
for keep/retire calls until selection is fixed.

**Divergence is concentrated per strategy:**

| strategy | missed | invented |
|---|---|---|
| S100 M3 Combo | 14 | **112** |
| S99 MSS FVG | 26 | 20 |
| S93 FVG Scalp | 26 | 11 |
| S94 Sweep Reversal | 14 | 9 |

S100 accounts for **74% of all inventions** — the sim massively over-fires it,
while S93/S99 are where the sim goes blind (26 misses each). Sim gate rejects
this window: S100 `entry_drift` 74 / `sl_too_tight` 54; S99 `entry_drift` 25 /
`sl_too_tight` 15; S94 `sl_too_tight` 29; S93 `entry_drift` 18. Attribution on
matched pairs: `entry_fill` 146, `exit_fill` 15.

**Next investigation** (P5 blocker): the S100 over-firing is the biggest single
lever — 112 invented trades. Start there, then the S93/S99 blindness. Do NOT
tune the fill model; exit deltas and outcome agreement already pass.

Re-run with:

```bash
cd strategies
PYTHONPATH="<repo>;<repo>/strategies" python -m backtest.run_live_parity \
  --start 2026-07-06 --end 2026-08-13 \
  --live backtest/results/parity/live_trades_2026-07-06_2026-08-12.csv \
  --spread 0.62 --model-entry-drift --sided-fills --exit-slippage-only \
  --entry-time-at-close --latency 0 \
  --external-pnl backtest/results/parity/external_pnl_2026-07-06_2026-08-12.csv
```

Note `--spread` barely matters in this flag set: `--sided-fills` crosses the real
bid/ask carried in the S5 data (so each account segment's true spread is already
in the data) and `--exit-slippage-only` charges only `slippage_pts` on exits.
`spread_pts` survives only as the fallback when sided fills are unavailable —
which is why a single full-window run is defensible and per-segment stats can be
split out of the matched output afterwards.

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

### Repo hygiene (resolved in `8a8e3b9`)

`.history_data/` (2.5 GB), `strategies/backtest/results/bars_cache/`, run logs
(`*.log`/`*.err`) and per-bar `*_regime.jsonl` dumps (36 MB) are now gitignored,
so `git add -A` can no longer drag in gigabytes. These are all regenerable:
`backtest.s5_backfill`, `backtest.backfill_history_cache --target`, or
`backtest.tick_s5` from the tick archive.

---

## Files (committed in `8a8e3b9` on `feat/strategy-manager`, not pushed)

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
