# Reports Tab — Account-wise PnL Calendar (2026-08-01)

## Purpose

A dashboard **Reports** tab showing a trading-journal-style monthly calendar of realized
PnL per broker account: green/red day cells with the day's realized USD and trade count,
weekly totals, and a month summary strip.

Decisions fixed during brainstorming:
- **Day cells: realized only.** A day's cell = realized PnL of positions CLOSED that day
  + the count of positions closed that day. Open/floating PnL never appears; past cells
  never change retroactively.
- **Placement:** new sidebar tab `/reports`. Account chips across the top (one per
  UserBroker with any closed trades; archived accounts greyed but selectable), plus an
  **All accounts** chip that sums them. Month navigation with `<` / `>`.
- **Extras:** month summary strip (net USD, trade count, win-days/loss-days) and a
  weekly-total cell at the end of each calendar row.

## Data definition (the part that must not drift)

- **Closed position** = `Position.quantity == 0`.
- **Exit-day attribution**: the date of the position's **latest non-ENTRY Order**
  (`Order.condition != "ENTRY"`, max `created_at` per position) — the same definition
  `entry_manager._todays_realized_usd` uses for the manager kill-switch, and for the same
  reason: `Position.modified_at` is re-touched by fill_reconciler days after a close
  (the 2026-07-09 false-kill-trip leak). **Fallback**: a closed position with NO exit
  Order rows (pre-dates exit-order recording) attributes to `modified_at`'s date —
  dropping those trades silently would be worse for a historical report than slight day
  drift; documented here, not surfaced in the UI.
- **USD conversion**: `Position.realized_profit_loss` is stored in PnL units
  (points x lots); USD = units x 100.0 (`_USD_PER_PNL_UNIT` — keep in sync with
  `entry_manager` and `strategy_manager.manager`).
- **Day boundaries are IST days**: `created_at`/`modified_at` are stored as naive IST
  wall-clock (the platform-wide -5:30 storage quirk), so bucketing on the STORED date
  gives IST calendar days with no conversion. The UI notes "days in IST" in the header.
- **Account scoping**: `Position -> UserStrategy -> UserBroker`. `userBrokerId` filter
  omitted = all accounts summed.

## GraphQL API (auto-discovery conventions, `@user_authenticate`)

`apis/schema/query/pnl_calendar.py` → class `PnlCalendar`:

```
pnlCalendar(year: Int!, month: Int!, userBrokerId: UUID) -> {
  days: [{ date: Date, pnlUsd: Float, trades: Int }]   # only days with activity
  monthPnlUsd: Float
  monthTrades: Int
  winDays: Int
  lossDays: Int
}
```

Validation: `1 <= month <= 12`, `2020 <= year <= current year + 1`.
Account chips reuse the existing `allUserBrokers` query (it already carries labels and
archived state) — no new accounts query.

Implementation shape: one queryset for the month's closed positions with an annotated
exit date (Subquery of max non-ENTRY order `created_at`, Coalesce onto `modified_at`),
filtered to the month window on the annotated date, aggregated in Python per day (row
counts are small). No migration — read-only feature.

## Frontend (`/reports`)

- Sidebar entry **Reports** (icon consistent with neighbors) →
  `app/(main)/reports/page.tsx` + `_components/main.tsx`; ops in
  `GraphQL/reportsControls.ts` (`PNL_CALENDAR` + reuse of the existing user-brokers
  document from `accountControls`).
- **Account chips**: All accounts | one chip per broker (label, archived greyed).
  Selected chip + selected month drive one `pnlCalendar` fetch (`no-cache`, refetch on
  chip/month change; no polling — historical data).
- **Calendar grid**: CSS grid, Mon-Sun columns + a highlighted "Week" column; day cells
  show `+$42` / `3t`, green `#089981` positive, red `#F23645` negative, neutral
  `--tv-text-soft` for flat/no-trade days; days outside the month dimmed. TV design
  tokens (`--tv-surface`, `--tv-border`) like `/manager-backtest`.
- **Month strip**: `July 2026 · net +$212 · 41 trades · 14W / 6L` + the IST note.
  `<` / `>` month navigation (clamped to the validation range).
- Weekly totals and the strip's W/L derive client-side from `days`.

## Testing (offline)

- Django (`apis/tests.py`): seed User/UserBroker/Strategy/UserStrategy + positions with
  exit Orders across a day boundary; assert (a) day bucketing on stored dates,
  (b) exit-order attribution beats a reconciler-touched `modified_at` (trap fixture),
  (c) the no-exit-order fallback, (d) x100 USD conversion, (e) all-accounts summing vs
  per-broker filter, (f) month filter excludes neighbors, (g) validation rejects bad
  month/year. Schema-surface test gains `pnlCalendar`.
- Frontend: `npm run lint` + `npm run build`.

## Rollout

1. Backend branch off `feat/strategy-manager`: query file + tests → box bind-mount copy
   (1 new file) → runserver autoreload. **No migration.**
2. Frontend branch off `main`: tab → lint/build → push ships via Netlify.
3. Verify live: chips render real accounts; July 2026 calendar matches the known July
   book (spot-check a few days against the dashboard).

## Out of scope (v1)

Day-cell drill-down (per-trade list on click), equity-change mode, floating PnL on
today's cell, CSV export, per-strategy filtering inside the calendar, retention/caching.
