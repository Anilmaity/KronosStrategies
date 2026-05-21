# Kronos v3-port Deployment

Five XAU/USD scalping strategies ported from the TradingSkills v3 suite,
gated by the v15d TNX macro filter and the v15e event-window E3 filter.

## Provenance

- **Source:** TradingSkills 16-month tick backtest (Jan 2025 -- May 2026).
- **Strategy bodies:** `backtest/dev/stratset_meanrev.py`,
  `backtest/dev/stratset_session.py`, `backtest/dev/stratset_m5.py`.
- **HTF / liquidity overlays:** `backtest/dev/htfgate_s02.py`,
  `htfgate_s05.py`, `htfgate_s06.py`, `htfgate_s07.py`, `htfgate_s14.py`.
- **Macro gate (TNX):** `backtest/reports/EXP_V15D_TNX_OPTSTACK_REPORT.md`
  -- selected `TNX |z_20d| >= 0.5` as the in-band ceiling.
- **DD overlay (E3):** `backtest/reports/EXP_V15E_FINAL_REPORT.md` --
  best deployable variant
  `T_baseline | ANY weekday | drop=noSH | TNX_z20d_abs >= 0.50 | E3` at
  78.3% OOS win-day rate, 5.02 tpd, DD 94 over 78 OOS fire-days
  (cf. v15d's `TWThF | TNXz20_ge0.50` at 78.2% / DD 222 -- E3 halves the DD).

## Strategies deployed

| NAME                     | Variation tag           | TF  | Direction | Rule (one-line)                                                                                  |
|--------------------------|-------------------------|-----|-----------|--------------------------------------------------------------------------------------------------|
| `KRONOS_S02_STOCH`       | `KRONOS_S02_STOCH`      | M15 | Long      | Stoch(14) < 15 dip in EMA100/200 uptrend, within 2 ATR of prior-day liquidity (PDH/PDL/POC).     |
| `KRONOS_S05_THREEBAR`    | `KRONOS_S05_THREEBAR`   | M15 | Long      | Three consecutive down-bars in EMA100/200 uptrend; skip when broken below prior-day level.       |
| `KRONOS_S06_SWEEP`       | `KRONOS_S06_SWEEP`      | M15 | Two-way   | Poke prior session H/L, close back inside -> fade; HTF-bias OR veto; no-fade-into-trend overlay. |
| `KRONOS_S07_CRT`         | `KRONOS_S07_CRT`        | M15 | Two-way   | Prior bar = range; current bar sweeps + closes back in the back half -> fade; both-HTF-oppose veto. |
| `KRONOS_S14_M5_STRETCH`  | `KRONOS_S14_M5_STRETCH` | M5  | Long      | EMA20 stretch <= -1.5 ATR dip in EMA100/200 uptrend; falling-knife veto (>= 3.5 ATR below EMA200). |

NOT deployed:
- **SH (momo short)** -- v13c/v15e showed anti-predictive depth + heavy DD
  contribution. The `drop=noSH` set is the deployable winner.

## Production gates (baked into every `get_signal`)

1. **TNX macro gate** (`shared/macro_gate.py::tnx_gate_open`)
   - Drop a signal day when `|z_20d|` of yesterday's TNX close < 0.5.
   - Cached daily TNX parquet under `shared/data/tnx_daily.parquet`,
     refreshed at most once per process per day via yfinance.
   - **Fails OPEN** on any data error (better to trade and reconcile than
     strand the whole suite on a network outage).

2. **Event window E3** (`shared/event_gate.py::event_window_open`)
   - Drop a signal when within +/-2h of any major US macro event:
     FOMC, CPI, NFP, PCE, GDP, SPEECH (Powell / Jackson Hole), ECB.
   - "Major" = `importance >= 2` in the hand-encoded calendar mirrored from
     `TradingSkills/backtest/macro_events.py`.
   - Cached at module load. **Fails OPEN** on any error.

## Expected in-band performance

Per `EXP_V15E_FINAL_REPORT.md`, the deployable winner
`T_baseline | ANY weekday | drop=noSH | TNX_z20d_abs >= 0.50 | E3`:

| metric                | value                               |
|-----------------------|-------------------------------------|
| OOS win-day rate      | **78.3%** (Wilson 95% CI [68.3, 85.8]) |
| Trades / fire-day     | 5.02                                |
| Total points over OOS | +2418 over 78 OOS fire-days         |
| Worst day             | -72.2                               |
| Max drawdown          | 94                                  |

Note the Wilson 95% upper bound at 85.8% -- the true population win-day rate
could plausibly be >= 80% even though the point estimate is 78.3%. See the
report for sample-size caveats.

## Bring-up sequence

```bash
# 1. From the KronosStrategies repo root:
docker compose build

# 2. Start the five Kronos services
docker compose up -d kronos_s02 kronos_s05 kronos_s06 kronos_s07 kronos_s14

# 3. Tail logs to verify each runner connected and is firing get_signal
docker compose logs -f kronos_s02 kronos_s05 kronos_s06 kronos_s07 kronos_s14
```

The five services use the same `./strategies` image as the existing
`research_*` services -- the only difference is the `RESEARCH_STRATEGY`
env var. `research_runner.py` dispatches the work.

## Manual DB inserts (must run before docker compose up)

The Kronos `entry_manager` looks up `Strategy` rows by **name** and only
fires when a deployed `UserStrategy` row points at it. Each Kronos
strategy needs its own pair of rows.

### Schema reference

From `strategies/shared/models.py`:

- `apis_strategy(currencypair_id, name, entry_quantity, is_active, description)`
- `apis_userstrategy(strategy_id, user_broker_id, multiplyer, is_active, deployed)`

Foreign keys:
- `currencypair_id` -> `apis_currencypair.id` (the `XAU_USD` row).
- `user_broker_id`  -> `apis_userbroker.id` (the live broker connection).

The `entry_manager._VARIATION_STRATEGY_NAME` map drives the `Strategy.name`
lookups (already updated in this change):

| variation tag           | Strategy.name              |
|-------------------------|----------------------------|
| KRONOS_S02_STOCH        | `Kronos S02 Stoch Revert`  |
| KRONOS_S05_THREEBAR     | `Kronos S05 Threebar Pull` |
| KRONOS_S06_SWEEP        | `Kronos S06 Session Sweep` |
| KRONOS_S07_CRT          | `Kronos S07 CRT`           |
| KRONOS_S14_M5_STRETCH   | `Kronos S14 M5 EMA Stretch`|

### SQL stub -- run inside the Kronos PostgreSQL DB

Replace `<XAU_USD_CP_ID>` and `<LIVE_USER_BROKER_ID>` with the real UUIDs
from your environment. Replace `<ENTRY_QTY>` with the per-strategy notional
your sizing model dictates (start small; the v15e DD is 94 pts but live
slippage may differ).

```sql
-- 1. Confirm the XAU_USD currency pair UUID
SELECT id, symbol FROM apis_currencypair WHERE symbol = 'XAU_USD';

-- 2. Confirm the live UserBroker UUID
SELECT id, name FROM apis_userbroker WHERE is_active = TRUE;

BEGIN;

-- 3. Insert the five Strategy rows (uuid_generate_v4 needs uuid-ossp;
--    substitute gen_random_uuid() if pgcrypto is enabled instead).
INSERT INTO apis_strategy (id, name, description, entry_quantity, is_active, currencypair_id)
VALUES
  (gen_random_uuid(), 'Kronos S02 Stoch Revert',
   'Stoch(14)<15 dip-buy in EMA100/200 uptrend; v15e TNX+E3 gates.',
   <ENTRY_QTY>, TRUE, '<XAU_USD_CP_ID>'),
  (gen_random_uuid(), 'Kronos S05 Threebar Pull',
   '3-bar down pullback in EMA100/200 uptrend; v15e TNX+E3 gates.',
   <ENTRY_QTY>, TRUE, '<XAU_USD_CP_ID>'),
  (gen_random_uuid(), 'Kronos S06 Session Sweep',
   'Prior-session H/L sweep fade (two-way); v15e TNX+E3 gates.',
   <ENTRY_QTY>, TRUE, '<XAU_USD_CP_ID>'),
  (gen_random_uuid(), 'Kronos S07 CRT',
   'Prior-bar CRT sweep fade (two-way); v15e TNX+E3 gates.',
   <ENTRY_QTY>, TRUE, '<XAU_USD_CP_ID>'),
  (gen_random_uuid(), 'Kronos S14 M5 EMA Stretch',
   'M5 EMA20 stretch <= -1.5 ATR dip-buy + falling-knife veto; v15e TNX+E3.',
   <ENTRY_QTY>, TRUE, '<XAU_USD_CP_ID>');

-- 4. Insert one deployed UserStrategy per Strategy.
INSERT INTO apis_userstrategy (id, name, strategy_id, user_broker_id,
                               multiplyer, is_active, deployed)
SELECT
    gen_random_uuid(),
    s.name,                 -- mirror the strategy name for clarity
    s.id,
    '<LIVE_USER_BROKER_ID>',
    1,
    TRUE,
    TRUE
FROM apis_strategy s
WHERE s.name IN (
    'Kronos S02 Stoch Revert',
    'Kronos S05 Threebar Pull',
    'Kronos S06 Session Sweep',
    'Kronos S07 CRT',
    'Kronos S14 M5 EMA Stretch'
);

-- 5. Verify
SELECT s.name, s.entry_quantity, us.deployed, us.multiplyer
FROM   apis_strategy s
JOIN   apis_userstrategy us ON us.strategy_id = s.id
WHERE  s.name LIKE 'Kronos %';

-- COMMIT;  -- uncomment after verifying
```

If `gen_random_uuid()` is not available, enable pgcrypto
(`CREATE EXTENSION IF NOT EXISTS pgcrypto;`) or use `uuid_generate_v4()` from
`uuid-ossp`.

## Caveats

- **SH not deployed.** v13c showed anti-predictive depth on SH; v15e
  confirmed that the `drop=noSH` set has lower DD at equal win-rate.
- **TNX gate fails OPEN.** If yfinance is down or the parquet is missing,
  every strategy will treat the gate as OPEN -- they will trade through
  what should have been quiet rates regimes. Reconcile daily P&L against
  the gate-decision log to spot any drift.
- **E3 fails OPEN.** Same rationale -- a date-list import error must not
  silently block all strategies. The calendar is hand-coded and module-
  cached, so failure is unlikely once the image is built.
- **M5 strategy (S14).** Reads `w5m`, so the runner must supply a 5-min
  candle window. Other Kronos research strategies already use this
  pattern (see `s10_90min_fade.py`, etc.).
- **Two-way fades (S06, S07).** Stop is *wider* than the target by design
  (1.0/1.5 for S06, 1.5/1.3 for S07). Live exits must honour the SL/TP
  triggers exactly -- do not round through one before placing the other.
- **TNX cache priming.** First run will hit yfinance to populate
  `shared/data/tnx_daily.parquet`. Run `python -m shared.macro_gate
  refresh` once inside the strategies container before going live to
  avoid the cold-start fail-open window.
- **.env additions.** None required -- TNX/E3 gates need only the existing
  `TIGERDATA_URL` for the tsdb_reader and outbound HTTPS for the yfinance
  refresh. Confirm the container can reach `query1.finance.yahoo.com`.
