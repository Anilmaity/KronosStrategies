# Neymar VIP — second Telegram source, phase 1: history export

**Date:** 2026-08-23
**Branch:** `feat/strategy-manager`
**Status:** design — awaiting review

---

## Why this exists

The operator wants to add a second Telegram signal source, "Neymar | VIP", alongside the
existing `@NeymarGoldTrader` copy-trader. The decision of *how* to trade it is deliberately
deferred: the channel's real message history is the evidence needed to make that call, and
we do not have it yet.

Phase 1 therefore acquires the history and nothing else. It is read-only by construction —
it never imports the trading code, so it cannot place an order.

## Decisions already taken

These were settled in the brainstorming session and are not reopened by this spec:

- **A second Telegram login**, not the existing `kronos_tg` session. Own phone number, own
  session file, own app credentials (`api_id` 35440199, a fresh registration distinct from
  the `30334024` hardcoded at `live_trader.py:57`). Rationale: a ban or rate-limit on one
  account must not be able to stop the other.
- **When trading eventually happens it runs on the same Winprofx-Demo account**
  (meta `3eefc570`) as the rest of the book, but under **its own platform `Strategy` row**
  so its P&L, signals and kill-switch contribution stay attributable.
- **The message format is assumed to differ** from `@NeymarGoldTrader`, so the eventual
  parser is per-source rather than a modification of `parse_signal()`.
- **Approach A** for the eventual live bot: a second container from the same image, own
  session volume, own state prefix, parser selected by env. Rejected alternatives were a
  multi-source refactor of `live_trader.py` (couples the two sources' fate; a 44 KB
  money-handling module with module-level globals) and forking the directory (duplicates
  the slice reconciler corrected on 08-11, guaranteeing divergence).

None of that is built in phase 1.

## Scope

**In scope**

1. A one-time interactive login for the second Telegram account, producing a local,
   gitignored session file.
2. Resolving the channel "Neymar | VIP" by display title and **recording its numeric ID**.
3. Exporting the channel's full message history to JSONL — lossless, one record per
   message.
4. Credentials moved out of the plaintext workspace-root file into a gitignored `.env`.

**Explicitly out of scope**

No parser. No order placement. No writes to Postgres. No `Strategy` / `UserStrategy` /
`ManagedStrategy` rows. No compose service. No deployment to the `algorobos` box. The
exporter does not import `metaapi_orders`, `db_persist`, or `apis_persist`.

## Design

### Where it runs

Locally, not on the box. The box is a 2 GB instance that wedged from memory pressure on
2026-08-12; a full-history crawl there buys nothing and risks production. Running locally
also keeps the session file off the box, which matters because the deploy skill forbids
moving `*.session` files (they can clobber the live login).

The session created here is **local only**. When the bot eventually runs on the box, that
login is made separately, on the box, into its own named volume.

### Components

**`Telegram_Bot/.env`** (gitignored, new) — holds `TGVIP_API_ID`, `TGVIP_API_HASH`,
`TGVIP_PHONE`. No hardcoded defaults anywhere in code; the exporter fails loudly with a
clear message if a value is missing rather than falling back. Once written, the plaintext
`E:\Projects\Kronos\telegram_channel_ids` file is deleted.

> The prefix is `TGVIP_`, **not** `TG2_`. `TG2_*` is already taken and means something
> different: `TG2_META_ACCOUNT_ID` / `TG2_META_API_TOKEN` / `TG2_RISK_USD` configure a
> second *broker* account fed by the *same* Telegram channel (`live_trader.py:114-127`).
> Reusing that prefix for a second *Telegram* account would collide in meaning inside one
> `.env` file.

**`Telegram_Bot/.gitignore`** — gains `exports/`. Full-history JSONL is regenerable bulk
data and must not be committable; the 5s campaign already had to retrofit this after
`git add -A` nearly dragged in gigabytes.

**`Telegram_Bot/export_channel_history.py`** (new, standalone) — the whole of phase 1.

Its responsibilities, in order:

*Login.* Constructs a Telethon client against the session name **`kronos_tg_vip`**, distinct
from the live bot's `kronos_tg` (fixed at `live_trader.py:64`) so the two logins can never
overwrite each other. The phone number is supplied from env so Telethon only prompts for the
login code, and the 2FA password if the account has one. This step is interactive and cannot
be automated.

*Resolve.* Telethon cannot resolve a display title the way it resolves a username or ID, so
the script iterates the account's dialogs and matches on title. On a match it prints and
persists the **numeric channel ID**, which every later phase pins to. Titles are editable —
`Neymar | VIP` contains a `|` that is exactly the sort of thing that gets tidied one day —
so title matching happens once, at discovery, and never again. If the title matches zero
dialogs the script lists what it *did* find and exits non-zero; the usual cause is the
account not having joined the channel. If it matches more than one, it refuses to guess.

*Export.* Walks history oldest-to-newest via `iter_messages(reverse=True)`, writing one
JSONL record per message: message id, UTC timestamp, raw text, edit timestamp, `reply_to`
message id, sender id, and whether the post was forwarded. Raw text is stored **unmodified**
— no normalisation, no emoji stripping. Phase 2 needs to see exactly what the channel sends,
because the 08-08 outage was caused by emoji decoration that a normalising exporter would
have hidden.

*Survive rate limits.* Telegram throws `FloodWaitError` with a required sleep on long
crawls. The script catches it, sleeps the demanded interval, and resumes. It is restartable:
it records the highest message id already written and resumes above it, so a kill mid-crawl
costs only the current batch.

### Data flow

```
second TG account  --(Telethon, interactive login once)-->  local session file (gitignored)
                                    |
                          resolve title -> numeric channel id
                                    |
                     iter_messages(reverse=True), FloodWait-aware
                                    |
                                    v
        Telegram_Bot/exports/neymar_vip_<channel_id>.jsonl   (raw, lossless)
```

Nothing downstream of the JSONL is in this phase.

### Error handling

Missing credential, channel not found, ambiguous title, and not-a-member all fail fast with
a message naming the actual cause, and a non-zero exit. `FloodWaitError` is the one error
that is retried rather than fatal. Any other Telethon exception is allowed to propagate —
the export is restartable, so failing loudly is cheaper than a partial file that looks
complete.

### Testing

The parts worth testing are the ones that do not need Telegram: record shaping (a Telethon
message object to a JSONL record, including a reply and an edited message), resume logic
(given an existing file, resume above the highest id), and title resolution against a fake
dialog list covering the zero-match, one-match and multi-match cases. These run against
fixtures with no network, in `Telegram_Bot/tests/`, alongside the existing 34 bot tests.

The login and the crawl itself are verified by running them — an export that produces a
plausible message count and parses back as valid JSONL is the check.

## Phase 2 (next spec, not this one)

Analysis over the exported JSONL, answering: what the message format actually is, how many
signals per week, how replies resolve outcomes, and how consistent the structure is.

Plus, importantly, an **overlap measurement against the existing `@NeymarGoldTrader`
signals**. "Neymar | VIP" is very likely the paid tier of the channel already being copied.
If the two feeds carry the same calls and both trade the same Winprofx-Demo account, the
account takes each trade twice — double size, double exposure, and the shared $150/day
kill-switch drains at twice the rate, while looking like ordinary trading. The free
channel's history is already in `tg_signals`, so matching VIP posts against it by time and
price is cheap, and it decides **supplement versus replace** before any money moves.

That measurement is a gate on phase 3, not a nice-to-have.

## Phase 3 (later, only if phase 2 justifies it)

The per-source parser, the second container, the `Strategy` / `UserStrategy` /
`ManagedStrategy` rows, and the box deploy — i.e. approach A as described above.

## Open questions, deferred by design

Risk per trade for the new source (the existing copy slot runs `RISK_USD=100` against the
strategies' $38), the dashboard display name, whether it launches DRY_RUN or armed, and
whether the channel posts anything other than XAUUSD (the current bot hard-filters
`ALLOWED_INSTRUMENTS = {"XAUUSD"}` and silently drops the rest). All of these are phase-3
decisions and all of them are better answered with the history in hand.

## Adjacent, not in scope

The stale `Neymar Telegram Copy (Account 2)` row is still `deployed=True` against a dead
MetaAPI account, so `fill_reconciler._accounts_in_scope` polls it every cycle and gets a
404. Cheap to clean up while in this code, but it is a separate change.
