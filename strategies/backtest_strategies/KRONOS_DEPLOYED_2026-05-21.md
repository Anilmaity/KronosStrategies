# Kronos v3 + v15e — DEPLOYED 2026-05-21

Five XAU/USD scalping strategies ported from the TradingSkills 16-month backtest
campaign (v13→v15) and registered in the Kronos PostgreSQL DB. Routes through
the existing MetaAPI cloud → MT5 account binding (`META_ACCOUNT_ID` env var on
the runner containers).

## Live registry — what's in the DB

| Variation tag | Strategy.name | strategy_id | user_strategy_id |
|---|---|---|---|
| `KRONOS_S02_STOCH`      | Kronos S02 Stoch Revert     | 77c8f2ec-c7ec-45f0-a555-b8a3bea6fc19 | 1a818e73-db55-45f9-8aaf-846832fb805d |
| `KRONOS_S05_THREEBAR`   | Kronos S05 Threebar Pull    | d74c86af-edf3-4022-9ebd-d4f1c81bc921 | b0ce9820-58e8-4e49-ba38-bee9345b3c17 |
| `KRONOS_S06_SWEEP`      | Kronos S06 Session Sweep    | 42d47090-b95c-41b1-a0dd-8bad79c8f7d6 | 8b521cc0-3a92-468a-b718-a4855733898b |
| `KRONOS_S07_CRT`        | Kronos S07 CRT              | 4a96d93b-5648-4749-bd54-6708a38966fb | 21308bb1-e538-4343-9d51-bfc705e8b007 |
| `KRONOS_S14_M5_STRETCH` | Kronos S14 M5 EMA Stretch   | e91a41a6-6a82-492e-8f07-72ef35018061 | ab1c86ca-df74-4e6f-96ee-fc37bdab2004 |

- All 5 UserStrategy rows: `is_active=True, deployed=True, multiplyer=1, entry_quantity=0.01`
- `user_broker_id = e673869c-8c56-4521-9a49-ac62f07d7da9` (the live MetaAPI broker — same one used by `ICT Breaker Block` and every other XAU strategy)
- `currency_pair_id = 9c5fde6d-93b6-4ebf-b84e-65de748ba94a` (XAU_USD)

The chain `place_entry(KRONOS_S0X_...) → _get_context → place_market_order →
MetaAPI cloud → MT5 account d9d3abef-3f33-400d-958b-2933297ca21b` is verified
working end-to-end against the live DB.

## Smoke tests run today

```
KRONOS_S02_STOCH         CONFIG: cd=900s session=7..21h
KRONOS_S05_THREEBAR      CONFIG: cd=900s session=7..21h
KRONOS_S06_SWEEP         CONFIG: cd=900s session=7..21h
KRONOS_S07_CRT           CONFIG: cd=900s session=7..21h
KRONOS_S14_M5_STRETCH    CONFIG: cd=300s session=7..21h

tnx_gate_open(now)=True
event_window_open(now, 2.0)=True

DB context for KRONOS_S02_STOCH:
  strategy_id: 77c8f2ec-...
  user_strategy_id: 1a818e73-...
  user_broker_id: e673869c-...      ← live MetaAPI broker
  currency_pair_id: 9c5fde6d-...
  quantity: 0.01
```

TNX daily parquet cached at
`KronosStrategies/strategies/shared/data/tnx_daily.parquet` (138 rows
warmup, refreshes via `python -m shared.macro_gate refresh` or auto on stale).

## Bring up the runner containers

On the production host (where the existing `ict_s4_breaker` / `research_*`
services already run):

```bash
cd /path/to/KronosStrategies
git pull
docker compose build           # picks up yfinance addition
docker compose up -d kronos_s02 kronos_s05 kronos_s06 kronos_s07 kronos_s14

# Verify they're listening
docker compose ps | grep kronos_

# Prime TNX cache inside the container (optional — first signal will trigger it)
docker compose exec kronos_s02 python -m shared.macro_gate refresh
```

## Expected production performance

Per `TradingSkills/backtest/reports/EXP_V15E_FINAL_REPORT.md`:

- OOS win-day rate: **78.3%** at 5.02 trades/day across the suite
- Wilson 95% CI: [68.3%, 85.8%]
- Total OOS P&L: +2,418 pts over 78 fire-days (5-fold WF, 155 OOS days)
- Worst day: -72.2 pts
- Max DD: 94 pts (HALVED vs v15d alone — event-window filter halves DD)

## Production gates (baked into every `get_signal`)

1. **TNX |z_20d| ≥ 0.5** — yfinance daily 10Y yield z-score regime gate
   (fails OPEN on network/yfinance error → trades resume rather than freeze)
2. **Event window ±2h** — drop signals within ±2 hours of any major US/EU
   macro release (FOMC/CPI/NFP/PCE/GDP/ECB, importance ≥ 2, 102 events
   in the 2025-26 calendar) — fails OPEN on calendar parse error

## What's NOT deployed (intentional)

- **SH momo short** — v13c proved depth is anti-predictive on SH, and v15e
  showed dropping it halves the DD with no loss of win-rate. Re-add if/when
  the structural reason for the inversion changes.

## Rollback

If anything misfires, pause the new strategies in seconds:

```sql
UPDATE apis_userstrategy SET is_active=false
WHERE id IN (
    '1a818e73-db55-45f9-8aaf-846832fb805d',
    'b0ce9820-58e8-4e49-ba38-bee9345b3c17',
    '8b521cc0-3a92-468a-b718-a4855733898b',
    '21308bb1-e538-4343-9d51-bfc705e8b007',
    'ab1c86ca-df74-4e6f-96ee-fc37bdab2004'
);
```

`_get_context` will then return `None` for the variation tags, blocking all
further `place_entry` calls without stopping the runner processes. Pair with
`docker compose stop kronos_s02 ...` if you also want to free the polling
load.
