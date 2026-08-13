# 5-second backtest fidelity — design

**Date:** 2026-08-12
**Status:** P1–P4 IMPLEMENTED; parity PASSES all 7 checks on a 6-trade sample
(2026-08-12). P5–P6 outstanding. **Current status, caveats and resume steps:
`../plans/2026-08-13-5s-fidelity-status.md`.**
**Goal:** make the offline sim reproduce live trades closely enough that its
P&L numbers can be trusted for roster decisions.

> **Outcome note (2026-08-13).** The central hypothesis in §1 — that intrabar
> SL-before-TP ordering is the dominant divergence — was REFUTED by measurement:
> over 646 matched trades it flipped 1 (0.2%), and zero 5s bars ever touched
> both levels. The real divergences turned out to be per-strategy lookback
> windows, the unmodelled `entry_drift` gate, a kill-switch blind to copy-trade
> P&L, double-charged exit friction, and an `entry_time` bug that fired every
> TIME exit ~60 s early. See the status doc for details.

---

## 1. Problem

`manager_sim_engine.run_sim` is a **1-minute engine**. It steps one M1 bar at a
time: `get_signal()` on the M1 close, entry filled at that bar's close plus a
scalar friction, and exits resolved against the bar's `high`/`low` in a **fixed
order — SL before TP** (`manager_sim_engine.py:217`).

For strategies on 1.5–3 pt stops that check order is the dominant divergence:
any M1 bar touching both levels is booked a loss in sim, while the live outcome
depended on which level came first. Three further gaps compound it:

| # | Gap | Evidence |
|---|-----|----------|
| 1 | Intrabar TP/SL ordering assumed, not observed | `step_position` checks SL first, always |
| 2 | Spread modelled as a constant 0.30 pt (friction 0.25 pt) | measured live XAU spread 2026-08-12: **0.76 pt** → real friction ≈0.48 pt, applied at entry *and* exit |
| 3 | Entry latency not modelled | live fills at market some seconds after the M1 close; sim fills at the close itself |
| 4 | `entry_drift` gate not modelled at all | 2026-08-12: 2 of 11 live signals rejected on drift (`+1.40pt vs budget 0.50pt`); inherently sub-minute, unmodelable on M1 bars |

Gap 4 matters most for interpretation: the prior fidelity audit concluded the
live/sim difference was "trade selection, not execution". 5s data is what makes
selection modelable.

## 2. Non-goals

- Signals are **not** moved to a 5s clock. Live runners evaluate closed M1
  candles, so an M1 signal clock is faithful; a 5s signal clock would generate
  trades live could never have taken.
- Telegram copy trades are out of parity scope (external signal source, not a
  `get_signal()` strategy).
- No change to any strategy's entry logic.

## 3. Constraints

- **Production box RAM:** 1910 MB total, ~200 MB available (live stack uses the
  rest). Multi-year 5s frames in pandas there would OOM and could kill live
  trading containers. Compute must not run on the box.
- **Secrets stay on the box:** OANDA key and DB credentials live only in on-box
  `.env`. They are never copied off, and the running containers' env is never
  read. The box therefore acts as a data *source* only.
- Local `.env` files are 0 bytes, so local code can never fetch directly.
- OANDA S5 for XAU_USD is available back to at least **2020-01**, 5000 candles
  per request (~6.94 h), bid/ask available via `price=BA`.

## 4. Architecture

```
BOX   s5_backfill.py         -> monthly parquet partitions (streaming, ~50 MB peak)
BOX   export_ground_truth.py -> live_trades.csv (no credentials in output)
         | scp down
LOCAL run_parity.py          -> sim + matching + report (needs neither key nor DB)
```

### 4.1 Data layer — `backtest/s5_cache.py`

- Storage: `results/bars_cache/s5/XAU_USD/YYYY-MM.parquet`, float32 + zstd.
- Columns: `time, o, h, l, c, bid_c, ask_c, volume`.
- Fetch `price="MBA"` for mid+bid+ask in one pass. **Verify OANDA accepts the
  combined form; fall back to two passes (M then BA) if rejected.**
- Own paging loop (the `fetch_candles` 60-page cap = 15.6 days of S5 is too
  small). Streaming: page → append to partition → free. Resumable: complete
  months are skipped, so an interrupted backfill restarts cheaply.

> **Correction (2026-08-12 incident).** An earlier version of this section
> claimed the backfill was "memory-bounded because each month window is fetched,
> validated, written and freed". That was wrong: a month is ~380–450k candles,
> which as Python dicts with mid+bid+ask is ~250 MB — against the ~227 MB the
> production box had free. Running it there thrashed the box into
> unreachability and took the live API down for ~30 min.
>
> The unit of boundedness is the **page**, not the month. `stream_s5` hands each
> page to a sink and never retains the window; `fetch_s5` (whole-window) is now
> documented as small-window-only. A `memory_is_sufficient` guard refuses to
> backfill below `MEMORY_FLOOR_MB` (400 MB) unless explicitly overridden, and
> `--page-size` defaults to 2000 rather than the 5000 maximum.
>
> Standing rule: **the backfill never runs on the production box.** Even
> page-bounded, it competes for RAM with the live trading stack.
- Validation gates that abort loudly: duplicate timestamps, non-monotonic time,
  and any market-hours gap **> 5 minutes** (60 consecutive missing S5 bars),
  excluding the daily 21:00–22:00 UTC break and the Fri 21:00 → Sun 22:00
  weekend close, following `_max_market_gap_minutes`. Sub-5-minute gaps are
  normal at S5 (thin quote periods) and are recorded in the coverage metric
  rather than treated as corruption.
- Additive: `tsdb_reader._TF_GRAN["5s"] = "S5"`.
- Size: ~200–270 MB per year uncompressed shape; float32+zstd expected well
  under that.

### 4.2 Execution model — `backtest/s5_exec.py`

`walk_exit(pos, s5_slice, now, cfg)` replaces the single-bar check. For each S5
bar within the minute, **in sequence**:

1. SL touch (`low < sl` for BUY, `high > sl` for SELL) — pre-ratchet level
2. TP touch (non-trailing positions)
3. TIME exit when `elapsed >= max_hold_min`, at that bar's close
4. Trailing ratchet from this bar's high/low, applied to subsequent bars

Residual ambiguity: when a *single* S5 bar touches both levels, keep SL-first
(conservative) **and count the event**. The count is reported, not hidden — it
is the honest floor on achievable fidelity.

Entry fill:

- Fill bar = the S5 bar containing `m1_close + measured_latency`.
- BUY fills at that bar's `ask_c`, SELL at `bid_c` — replacing the symmetric
  `spread/2 + slippage` abstraction with the sided reality.
- Latency and spread are **measured per account segment**, never assumed:
  - **Latency** = median over the parity window of
    `broker_deals.deal_time(DEAL_ENTRY_IN) − m1_close(signal bar)`, where the
    signal bar is derived from `StrategySignal.signal_at`. Reported with p10/p90
    so a wide spread of latencies is visible rather than averaged away.
  - **Spread** = median `ask_c − bid_c` of the S5 fill bars actually used,
    computed per account segment and per session (ASIA/LONDON/NY), since XAU
    spread widens materially outside London/NY.

All of it behind `cfg.exec_resolution`, default `"1m"`.

### 4.3 Shared gate — `entry_drift`

`shared/gate_rules.py` currently exposes `sl_too_tight`, `in_news_blackout`,
`parse_utc_windows`. `entry_drift` is still inline in
`entry_manager.place_entry`. Lift it into `gate_rules` as a pure predicate and
have `entry_manager` import it — the pattern the repo already established for
the other gates, so sim and live can never drift apart. Behaviour-preserving;
verified by testing the extracted predicate against live's recorded
`entry_drift` rejections.

### 4.4 Parity harness — `backtest/parity_harness.py`

Ground truth per live trade, assembled on the box: `Position` + ENTRY `Order`
(ticket) + `StrategySignal` (signal price/time, rejection reason) +
`broker_deals` (real fill time, fill price, realised USD incl. commission/swap).

Matching: pair sim ↔ live on `(strategy, entry_time ± 90 s)`. Classify every
row as **matched**, **live-only** (sim missed a real trade) or **sim-only** (sim
invented one). For matched rows report entry Δpt, exit Δpt, outcome agreement,
USD Δ.

Each mismatch is attributed to a mechanism: `spread`, `latency`,
`intrabar_order`, `gate_not_modelled`, `warmup`, `data_gap`. This attribution is
the deliverable — it converts "the sim is wrong" into "wrong here, by this much".

Output: markdown + CSV under `results/parity/`.

### 4.5 Pre-registered tolerance

Fixed before any result is seen (per the ARM_SL_FLOOR discipline):

- ≥90% of live trades matched
- median |entry Δ| ≤ 0.15 pt, p90 ≤ 0.40 pt
- median |exit Δ| ≤ 0.30 pt, p90 ≤ 0.80 pt
- outcome-label agreement ≥90%
- aggregate USD within ±10% of live over the window

## 5. Parity window

**2026-07-06 → 2026-08-12** — bounded by `broker_deals` coverage (raw fills
exist only from 2026-07-06). 243 strategy trades: S100 97, S99 55, S94 47,
S93 44.

The book moved from Funding Pips 2 (`97fab5dc`) to Winprofx-Demo (`3eefc570`)
on 2026-07-30, so spread/latency are measured and parity evaluated **per
account segment**; the two brokers share no execution profile.

## 6. Phases

| Phase | Work | Exit criterion |
|-------|------|----------------|
| P1 | S5 pipeline; measure latency + spread per account | parity-window data cached and validated |
| P2 | `s5_exec` + unit tests | M1 golden test byte-identical; 5s tests pass |
| P3 | Parity harness, first measurement | per-trade diff + mechanism attribution produced |
| P4 | Iterate sim until tolerance passes | §4.5 met, or documented as unreachable |
| P5 | Re-validate S93/S94/S99/S100 at 5s over 2–3 y | corrected PF/expectancy per strategy |
| P6 | Roster action | applied via dry-run → `--commit` after numbers presented |

**P4 failure mode, named up front:** if parity still fails after modelling
spread, latency, ordering and drift, that is evidence the residual gap is
genuine sub-5s noise — meaning these scalpers' edges are smaller than their
execution uncertainty. That is a profitability answer too, and it must be
reported as such rather than tuned away.

## 7. Testing

Unit tests in `tests/` (pytest.ini is scoped there), synthetic S5 fixtures, no
network:

- TP-first vs SL-first ordering resolved from sequence
- both-levels-in-one-S5-bar counted as ambiguous
- TIME exit at 5s granularity
- trailing ratchet applied per 5s bar
- bid/ask fill sides (BUY at ask, SELL at bid)
- `entry_drift` predicate, incl. parity against live's recorded rejections
- latency → fill-bar selection
- **M1 golden regression:** `exec_resolution="1m"` output byte-identical to the
  current engine (proves the flag is inert by default)
- data validation: gap detection, dedupe, monotonicity

## 8. Error handling

- OANDA paging reuses the existing `Retry` adapter (429/5xx). Partition-level
  resumability means a failed run never corrupts cached months.
- A minute with missing S5 data falls back to its M1 bar for that minute and is
  **counted**; coverage % is reported. Never silent.
- Sanity-gate failures abort with a non-zero exit rather than writing
  questionable data.
