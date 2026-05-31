# BTC_USD Tick Data → `ltp` — Design

**Date:** 2026-05-31
**Author:** Anil (with Claude)
**Status:** Approved (design)

## Goal

Get `BTC_USD` ticks into the shared TigerData `ltp` hypertable: **deep history
(from 2025-01-01, ~17 months) + ongoing live collection**, matching exactly how
the existing `XAU_USD` and `XAG_USD` collectors already work.

## Context / findings

Verified against the live OANDA practice account and TigerData store:

- **Source available as-is.** `BTC_USD` returns `200 OK` live S5 candles from the
  *same* OANDA practice account/API key the gold & silver collectors use
  (`ETH_USD` also works; `BTC_USDT` / `XBT_USD` do not). No new credentials.
- **History depth confirmed.** OANDA serves `BTC_USD` S5 candles back past 2024,
  so a 2025-01-01 start is safe.
- **OANDA's BTC is a forex-hours CFD, not true 24/7.** Probes showed **zero**
  candles on Saturday 2026-05-30, the last Friday candle at 20:59 UTC (right
  before the 21:00 UTC weekend close), and reopen Sunday evening. This matches
  XAU/XAG exactly, so the existing `is_market_closed_utc()` gate is **correct for
  BTC unchanged** — no collector code changes needed.
- **`ltp` schema:** `(time timestamptz, symbol text, ltp double precision)`,
  primary key `(symbol, time)` + unique `(time, symbol)`. The collector's
  `INSERT ... ON CONFLICT DO NOTHING` already dedups; BTC rows are simply
  distinguished by `symbol = 'BTC_USD'`.
- **Existing coverage:** `XAU_USD` and `XAG_USD` both span 2025-12-02 → present
  (~8M rows each) on TigerData.

## Scope

Two pieces of work. **No** schema change, **no** new table, **no** backend /
frontend / GraphQL wiring (raw tick storage only, same as silver), **no** change
to `is_market_closed_utc()`.

### 1. Live collector — new compose service (only persistent change)

Add a third instance of the existing collector image to `compose.yml`, identical
to `tick_data_collector_xagusd` but with `OANDA_INSTRUMENT: BTC_USD`:

```yaml
  tick_data_collector_btcusd:
    build: ./tick_data_collector
    restart: always
    env_file: .env
    environment:
      TSDB_TARGET:      ${TSDB_TARGET:-tigerdata}
      TIGERDATA_URL:    ${TIGERDATA_URL:-}
      OANDA_INSTRUMENT: BTC_USD
```

Reuses `main.py` / `oanda_tick_lib` verbatim. Deployed on the **remote host by
the operator** (this dev machine has no Docker).

### 2. Historical backfill — one-off, run from the dev machine

The original `ltp` backfill script is not in the repo, so add a small, reusable
`tick_data_collector/backfill_ltp.py`:

- CLI args: `--instrument` (default `BTC_USD`), `--start` (default `2025-01-01`),
  `--end` (default = today).
- For each calendar day, reuse the tested `OandaTickFetcher.fetch_day` (S5 candles
  → 4-point synthetic tick split — identical to the live path), flatten the
  candle groups, and bulk-insert into `ltp` via `psycopg2.extras.execute_values`
  with `ON CONFLICT DO NOTHING` (same pattern as `main.py:insert_ticks`).
- **Idempotent / resumable** — re-running skips already-present rows via the
  conflict clause; OANDA day cache makes re-fetch cheap.
- Logs per-day progress and a final summary (days fetched, rows inserted).

**Volume estimate:** BTC on forex hours ≈ ~350 trading days × ~23 h × 720 S5/h ×
4 ticks ≈ **~20–25M rows** (consistent with gold/silver density). OANDA fetch is
light (~4 throttled calls/day); the DB insert is the bottleneck — expected on the
order of tens of minutes. Run in the background from this machine.

## Execution plan

1. Add `tick_data_collector_btcusd` service to `compose.yml`.
2. Write `tick_data_collector/backfill_ltp.py`.
3. Run the backfill from the dev machine (`--instrument BTC_USD --start
   2025-01-01`) in the background.
4. **Verify:** query `ltp` for `symbol='BTC_USD'` row count + min/max time;
   spot-check density against `XAU_USD`.
5. Operator deploys the new compose service on the remote host for live
   collection going forward.

## Verification / success criteria

- `backfill_ltp.py` exits cleanly; final summary shows ~20–25M BTC rows.
- `SELECT symbol, count(*), min(time), max(time) FROM ltp GROUP BY symbol`
  shows a third row, `BTC_USD`, spanning 2025-01-01 → ~present (forex hours).
- Per-day BTC row density is comparable to `XAU_USD` on matching trading days.
- `compose.yml` parses (`docker compose config`) with the new service.

## Out of scope / non-goals

- No `is_market_closed_utc()` change (OANDA BTC is forex-hours).
- No new DB table, schema migration, or index.
- No Django/GraphQL/frontend exposure of BTC ticks.
- No live strategy consuming BTC (storage only for now).
