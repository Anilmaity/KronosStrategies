# Reports Tab — PnL Calendar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `/reports` dashboard tab with an account-wise monthly realized-PnL calendar
(green/red day cells, weekly totals, month summary strip).

**Architecture:** One read-only GraphQL query (`pnlCalendar`) does the whole aggregation
server-side using the platform's canonical exit-day attribution; the frontend renders a
CSS-grid calendar with the TV design tokens. No migration, no strategies-repo change.
Spec: `docs/superpowers/specs/2026-08-01-reports-pnl-calendar-design.md`.

**Spec deviation (deliberate):** the spec said chips reuse `allUserBrokers`; that query is
`@admin_authenticate` and its resolver orders by the dead `broker__name` FK path — not
usable from a user-facing tab. Instead `pnlCalendar` embeds an `accounts` list (brokers
owning >= 1 closed position ever, so chips stay stable across months). `UserBroker` has no
`archived` field — `is_active=False` is the archived signal (the 2026-07-30 WinProFx
migration archived "Funding Pips 2 anil" via flags), and the chip label falls back
`label -> meta_account_id -> "unnamed"`.

## Global Constraints (binding for every task)

1. **Data definition is the spec's "must not drift" section, verbatim**: closed =
   `quantity == 0`; exit day = max `created_at` of the position's non-ENTRY Orders,
   Coalesce-fallback `modified_at`; USD = `realized_profit_loss x 100.0`; day boundaries
   are the STORED wall-clock dates (IST by the platform quirk) — Django reads the stored
   values as aware-UTC datetimes CARRYING IST wall values, so month bounds are built as
   `datetime(year, month, 1, tzinfo=timezone.utc)` and `.date()` on a row's exit_at IS
   the IST day. Tests pin this; do not "fix" the timezone.
2. Backend branch `feat/reports-pnl` off `feat/strategy-manager` (@ current HEAD);
   frontend branch `feat/reports-tab` off `main`. Frontend is NEVER pushed by the
   implementer (Netlify ships on push; controller pushes after review).
3. Tests offline: `manage.py test apis` (in-memory SQLite); frontend `npm run lint` +
   `npm run build`. Backend tree has a pre-existing modified `Kronos_Backend/settings.py`
   — never touch or commit it.
4. Commit discipline: `git add` only explicit paths; one commit per task, prefix
   `rpt(taskN):`.
5. New GraphQL fields must be registered in `apis/test_schema_surface.py`
   (`pnlCalendar` in EXPECTED_QUERY_FIELDS, alphabetical position).
6. ASCII-only code/console output; frontend styling uses the TV tokens
   (`--tv-surface`, `--tv-border`, `--tv-text-soft`; green `#089981`, red `#F23645`).

---

### Task 1: `pnlCalendar` query (backend)

**Files:** Create `apis/schema/query/pnl_calendar.py` (class `PnlCalendar` — CamelCase of
filename, auto-discovered). Modify `apis/test_schema_surface.py` (register `pnlCalendar`).
Test: append to `apis/tests.py`.

**Interfaces produced (consumed verbatim by Task 2):**

```graphql
pnlCalendar(year: Int!, month: Int!, userBrokerId: UUID) {
  days { date pnlUsd trades }          # only days with activity, ascending
  monthPnlUsd monthTrades winDays lossDays
  accounts { id label isActive }       # chip list, unfiltered by month
}
```

- [ ] **Step 1: failing tests** — append to `apis/tests.py` a `PnlCalendarTests(TestCase)`
  with a `setUp` reusing `_mk_user_strategy()` (returns a UserStrategy; its
  `user_broker.user` is the auth user) and helpers:

```python
def _close(self, us, realized, exit_dt, entry_shift_h=2, with_order=True):
    """Closed position realized at exit_dt (stored wall clock). The closing
    Order carries exit_dt; created_at/modified_at are decoys."""
    pos = _mk_position(us, qty=0, avg="3300", realized=str(realized),
                       created_at=exit_dt - timedelta(hours=entry_shift_h))
    if with_order:
        o = Order.objects.create(symbol="XAU_USD", position=pos,
                                 condition="EXIT", side="SELL",
                                 user_broker=us.user_broker)
        Order.objects.filter(id=o.id).update(created_at=exit_dt)
    # reconciler-touch decoy: modified_at lands days later
    Position.objects.filter(id=pos.id).update(
        modified_at=exit_dt + timedelta(days=3))
    return pos
```

  Tests (all calling `PnlCalendar.resolve_pnlCalendar(None, _auth_info(user), ...)`
  directly, the established direct-resolver style):
  - `test_day_bucketing_and_usd`: two closes on stored-date 2026-07-10 (realized 0.5 and
    -0.2 units) + one on 07-11 → days == [{07-10, +30.0 USD, 2}, {07-11, ...}]
    (0.3 units x 100 = 30.0), monthTrades 3.
  - `test_exit_order_beats_modified_at`: a close whose Order sits on 07-10 but
    modified_at on 07-13 lands on 07-10 (the reconciler-leak trap).
  - `test_fallback_without_exit_order`: `with_order=False` close attributes to its
    modified_at date.
  - `test_month_window_excludes_neighbors`: closes on 06-30 and 08-01 absent from July.
  - `test_all_accounts_vs_filter`: second UserBroker via `_mk_user_strategy()`;
    unfiltered sums both, `userBrokerId=` filters to one; `accounts` lists both with
    `isActive` flags.
  - `test_open_positions_excluded`: qty=1 position never counted.
  - `test_win_loss_days`: winDays/lossDays computed from day nets (a 0.0-net day counts
    as neither).
  - `test_validation`: month 13 and year 2019 raise `GraphQLError`.

  Run `manage.py test apis -k PnlCalendar` → fails (module missing).

- [ ] **Step 2: implement** `apis/schema/query/pnl_calendar.py`:

```python
from datetime import date, datetime, timezone

import graphene
from graphql import GraphQLError
from django.db.models import F, Max, OuterRef, Subquery
from django.db.models.functions import Coalesce

from apis.models import Order, Position, UserBroker
from apis.schema.utils import user_authenticate

_USD_PER_PNL_UNIT = 100.0   # keep in sync with entry_manager / manager


class PnlDayType(graphene.ObjectType):
    date = graphene.Date()
    pnlUsd = graphene.Float()
    trades = graphene.Int()


class PnlAccountType(graphene.ObjectType):
    id = graphene.UUID()
    label = graphene.String()
    isActive = graphene.Boolean()


class PnlCalendarType(graphene.ObjectType):
    days = graphene.List(PnlDayType)
    monthPnlUsd = graphene.Float()
    monthTrades = graphene.Int()
    winDays = graphene.Int()
    lossDays = graphene.Int()
    accounts = graphene.List(PnlAccountType)


def _month_bounds(year: int, month: int):
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end = (datetime(year + 1, 1, 1, tzinfo=timezone.utc) if month == 12
           else datetime(year, month + 1, 1, tzinfo=timezone.utc))
    return start, end


class PnlCalendar(graphene.ObjectType):
    pnlCalendar = graphene.Field(
        PnlCalendarType, year=graphene.Int(required=True),
        month=graphene.Int(required=True), userBrokerId=graphene.UUID())

    @user_authenticate
    def resolve_pnlCalendar(self, info, year, month, userBrokerId=None):
        if not 1 <= month <= 12:
            raise GraphQLError("month must be 1..12")
        if not 2020 <= year <= date.today().year + 1:
            raise GraphQLError("year out of range")
        start, end = _month_bounds(year, month)

        exit_sq = (Order.objects.filter(position=OuterRef("pk"))
                   .exclude(condition="ENTRY").order_by()
                   .values("position")
                   .annotate(m=Max("created_at")).values("m"))
        qs = (Position.objects.filter(quantity=0)
              .annotate(exit_at=Coalesce(Subquery(exit_sq), F("modified_at")))
              .filter(exit_at__gte=start, exit_at__lt=end))
        if userBrokerId:
            qs = qs.filter(user_strategy__user_broker_id=userBrokerId)

        by_day: dict = {}
        for realized, exit_at in qs.values_list("realized_profit_loss", "exit_at"):
            d = exit_at.date()   # stored wall clock == IST day (platform quirk)
            agg = by_day.setdefault(d, {"pnl": 0.0, "n": 0})
            agg["pnl"] += float(realized or 0) * _USD_PER_PNL_UNIT
            agg["n"] += 1

        days = [PnlDayType(date=d, pnlUsd=round(v["pnl"], 2), trades=v["n"])
                for d, v in sorted(by_day.items())]
        accounts = [
            PnlAccountType(id=b.id,
                           label=b.label or b.meta_account_id or "unnamed",
                           isActive=b.is_active)
            for b in UserBroker.objects.filter(
                userstrategy__position__quantity=0).distinct().order_by("label")
        ]
        return PnlCalendarType(
            days=days,
            monthPnlUsd=round(sum(v["pnl"] for v in by_day.values()), 2),
            monthTrades=sum(v["n"] for v in by_day.values()),
            winDays=sum(1 for v in by_day.values() if v["pnl"] > 0),
            lossDays=sum(1 for v in by_day.values() if v["pnl"] < 0),
            accounts=accounts,
        )
```

  NOTE: verify the reverse-relation name in the accounts queryset
  (`userstrategy__position` — check `UserStrategy.user_broker`'s and
  `Position.user_strategy`'s `related_name`; if unset Django uses the lowercase model
  name as above). Adjust to the actual related names after reading `apis/models.py`.

- [ ] **Step 3: register** `"pnlCalendar"` in `EXPECTED_QUERY_FIELDS`
  (`apis/test_schema_surface.py`). Run the surface test first — its assertion diff prints
  the introspected sorted list; insert `pnlCalendar` at exactly the position the diff
  shows (do not guess the sort).
- [ ] **Step 4:** `manage.py test apis` → all green. **Step 5: commit**
  `rpt(task1): pnlCalendar query + tests`.

### Task 2: Reports tab — chips, month nav, fetch (frontend)

**Files:** Create `GraphQL/reportsControls.ts`, `app/(main)/reports/page.tsx`,
`app/(main)/reports/_components/main.tsx`, `app/(main)/reports/_components/types.ts`.
Modify `app/(main)/_components/constants.ts` (sidebar entry **Reports**, path `/reports`,
icon `HiDocumentText` — already imported there).

**Interfaces:** consumes Task 1's query verbatim (single `PNL_CALENDAR` gql document —
query fields are camelCase attributes on the ObjectType, NO alias needed; only mutations
need the CamelCase alias trick). Produces `CalendarPayload` state consumed by Task 3's
grid component: `{days, monthPnlUsd, monthTrades, winDays, lossDays, accounts}` per
`types.ts` mirroring the GraphQL shape.

- [ ] **Step 1: `reportsControls.ts`** — the one document with all response fields.
- [ ] **Step 2: `types.ts`** — `PnlDay {date, pnlUsd, trades}`, `PnlAccount {id, label,
  isActive}`, `CalendarPayload`.
- [ ] **Step 3: `main.tsx`** — state `{year, month}` (init: current IST month via
  `new Date()`), `selectedBrokerId: string | null` (null = All). One
  `client.query({fetchPolicy: "no-cache"})` on mount and on any of the three changing
  (no polling — historical data). Header row: title "Reports", subtitle
  "Realized PnL per account - days in IST". Chips: `All accounts` first (selected style
  `border-color: #089981`), then one per `accounts` (archived `isActive=false` at 50%
  opacity with an "archived" suffix). Month nav `<` / `>` buttons flanking
  "July 2026"-style label; clamp navigation to 2020-01 .. (current year+1)-12.
  Renders Task 3's `<MonthGrid payload={...} year={} month={}/>` (placeholder `null`
  until Task 3, as with the MBT ResultsPanel pattern).
- [ ] **Step 4:** `page.tsx` shell (metadata title "Reports - Kronos") + sidebar entry.
- [ ] **Step 5:** `npm run lint` + `npm run build` green. **Step 6: commit**
  `rpt(task2): reports tab, account chips, month navigation`.

### Task 3: Calendar grid + month strip (frontend)

**Files:** Create `app/(main)/reports/_components/MonthGrid.tsx`; modify
`app/(main)/reports/_components/main.tsx` (render it).

- [ ] **Step 1: `MonthGrid`** — pure component, no fetching:
  - Build the month's weeks: cells from `new Date(Date.UTC(year, month-1, 1))` through
    month end; week starts Monday (`(getUTCDay() + 6) % 7`); leading/trailing cells
    outside the month render dimmed and excluded from weekly totals.
  - Day lookup map keyed by the `days[].date` string (`YYYY-MM-DD`).
  - Cell: day number (top-left, `--tv-text-soft`), then `+$42` (green `#089981` > 0,
    red `#F23645` < 0) and `3t` beneath; flat/no-trade days show just the day number.
  - 8th column "Week": sum of the row's in-month `pnlUsd`, same coloring, `—` when 0
    trades.
  - Month strip above the grid: `net +$212 · 41 trades · 14W / 6L` from the payload
    (already server-computed — do NOT recompute win/loss client-side).
  - Grid via CSS grid `grid-template-columns: repeat(7, 1fr) 88px`, cells
    `min-height 72px`, `panelStyle` borders per the MBT components.
- [ ] **Step 2:** loading / empty states: payload null → "Loading..."; `days` empty →
  grid still renders (all-quiet month) with a soft "no closed trades this month" note.
- [ ] **Step 3:** `npm run lint` + `npm run build` green. **Step 4: commit**
  `rpt(task3): calendar grid, weekly totals, month strip`.

---

## Execution notes for the controller

- Task 1 alone is backend; 2-3 sequential on the frontend branch.
- Final review: two packages (backend base = feat/strategy-manager HEAD at branch time;
  frontend base = main HEAD). Then rollout per spec: 1 backend file via bind-mount (no
  migration), frontend push after review, live spot-check July 2026 vs the known book.
