# SESSION_BREAKOUT — integration & deployment design (2026-07-01)

Adapts `SESSION_BREAKOUT_STRATEGY_SPEC.md` (the canonical signal spec) to the Kronos
Strategies repo, consolidating the two live challenge bots into a **single** live
strategy. The signal logic is defined verbatim in the source spec §3/§5 — this
document covers the port, engine wiring, sizing, validation, and the retire/deploy
plan, plus the three decisions the operator made.

## Operator decisions (2026-07-01)

1. **Consolidate to one.** Retire BOTH current challenge bots (`challenge_xau`
   integrated H4 trend-follow + `challenge_xau_h4` standalone) and replace them with a
   single SESSION_BREAKOUT bot. The platform should show exactly one challenge strategy.
2. **Fixed lot.** Launch at `SESSION_BREAKOUT_LOT=0.02` (spec path a) — worst week
   ≈ −$200 on $5k, inside the FundingPips limits. No engine range-sizing for v1.
3. **Straight to live.** Deploy `dry_run=False` once unit tests pass and the live-M5
   frequency check looks right (~4/day). No dry-run observation window.

## Architecture

SESSION_BREAKOUT runs on the existing generic `research_runner` path (the same path
the integrated CHALLENGE_XAU used), NOT the standalone `challenge/live_runner.py`
path. This reuses the `entry_manager` → DB-`UserBroker` order routing and the
automatic dashboard writes, and keeps the "make it one" surface minimal.

Data flow:

```
research_runner (RESEARCH_STRATEGY=kronos_session_breakout)
  → fetch_candles 1m/5m/15m from OANDA
  → kronos_session_breakout.get_signal(w1m, w5m, w15m, now_utc)  → Signal | None
  → entry_manager.place_entry(...)  (static SL/TP path, max_hold_min=180)
  → order routed to challenge UserBroker (account 6c7ce166)
  → apis_position / apis_order / dashboard rows written
```

### Components

- **NEW `strategies/backtest_strategies/kronos_session_breakout.py`** — the port.
  Exports `NAME="SESSION_BREAKOUT"`, `CONFIG` (StrategyConfig), and
  `get_signal(w1m, w5m, w15m, now_utc) -> Signal | None`. Uses `w5m` directly (drop
  the still-forming last bar; no resample). Emits `Signal(trailing=False,
  stop_loss=..., take_profit=..., max_hold_min=180, reason="SESSION_BREAKOUT_LONG/SHORT")`.
  Pure helpers `ema`, `bias_long_short`, and the OR/entry logic are transcribed
  verbatim from the spec §5 so they are unit-testable offline.
- **`entry_manager` registration** — add
  `"SESSION_BREAKOUT" -> "Session Breakout M5 ORB"` to `_VARIATION_STRATEGY_NAME`,
  routed down the existing static-SL/TP path (NOT the trailing-ratchet path).
- **`compose.yml`** — repoint the existing `challenge_xau` service to run
  SESSION_BREAKOUT: `RESEARCH_STRATEGY=kronos_session_breakout`,
  `RESEARCH_WIN_5M`/`RESEARCH_DAYS_5M` sized for ≥ `n_long+slope_lk+2 = 290` closed
  M5 bars (~2 trading days of headroom across weekend gaps), `SESSION_BREAKOUT_LOT=0.02`.
  Remove the `challenge_xau_h4` service.
- **`strategies/db/deploy_session_breakout.py`** — mirror `deploy_challenge_xau.py`;
  create the `apis_strategy` + `apis_userstrategy` rows bound to the challenge
  `UserBroker` (account 6c7ce166), idempotent, raw SQL.
- **`tests/test_session_breakout.py`** — NEW unit tests (see Validation).

### One-entry-per-session

`get_signal` is evaluated every research_runner tick. To enforce "one position per
session window" (spec §3.3, §8.3.3):
- `CONFIG.max_concurrent_positions = 1` (engine blocks overlapping fills),
- `CONFIG.cooldown_s = 1800` (coarse re-entry guard),
- **explicit module-level `(date, session_hour)` guard** inside
  `kronos_session_breakout`: once a signal has been emitted for a given
  `(utc_date, session_hour)`, return `None` for that key for the rest of the day, so
  a session that already fired cannot re-arm within its own 3h hold window. State is a
  module-level set that persists across ticks in the running process (reset naturally
  on restart; a restart mid-session could in principle re-arm once — acceptable for v1
  and bounded by `max_concurrent_positions=1`).

### Exits — no new engine code

Static broker SL/TP + `max_hold_min=180` (3h time-close), already supported by
`entry_manager` (`TIME_EXIT`). Last session hour is 14:00 UTC and hold is 3h, so all
positions close by ~17:00 UTC — "flat by EOD" needs no separate mechanism.

## Signal logic (canonical: SESSION_BREAKOUT_STRATEGY_SPEC.md §3, §5)

- **Bias:** `e = EMA(close, 240)`; `up = close>e and e>e[-48]`; `dn = close<e and
  e<e[-48]`; else 0. Undefined until `i >= 288`.
- **Sessions:** open hours `[1,7,12,13,14]` UTC; OR = bars with `hour==sh and
  minute<30` (≥2 bars); `rng_hi=max(high)`, `rng_lo=min(low)`, `rng>0`.
- **Entry (first bias-aligned break within 36 bars of OR close):**
  long if `high>=rng_hi and bias==+1` (entry `rng_hi`, sl `rng_lo`, tp `rng_hi+1.5*rng`);
  short if `low<=rng_lo and bias==-1` (entry `rng_lo`, sl `rng_hi`, tp `rng_lo-1.5*rng`).
- **Params (defaults, do not re-tune):** `or_min=30, tp_mult=1.5, hold_bars=36,
  n_long=240, slope_lk=48`.

## Sizing

Fixed `SESSION_BREAKOUT_LOT=0.02` env (spec path a). The engine applies a fixed lot
per strategy; the spec's OR-width `position_size()` is ported into the pure module and
covered by a unit test (worst-case full-OR-width stop at 0.02 lot stays within the
daily loss limit), but is NOT wired into live order sizing for v1.

Daily kill-switch: stop after 2 consecutive losers or −$150 realized (consistent with
the challenge doctrine). Confirm whether the research_runner/entry_manager path already
enforces a per-day loss guard; if not, this is a follow-up (v1 relies on the small
fixed lot + `max_concurrent_positions=1` to bound daily loss).

## Validation

Unit tests (spec §10.2–§10.6), run under repo venv:
1. **Causality** — no signal uses the forming bar; none before OR complete (`minute>=30`).
2. **Session gate** — entries only in hours {1,7,12,13,14}; ≤1 per `(date, hour)`.
3. **Bias gate** — no long when bias≤0; no short when bias≥0.
4. **Static exits** — `trailing=False`; sl==opposite OR side; tp==entry±1.5×OR.
5. **Sizing** — full-stop loss at 0.02 lot within the daily loss limit.
6. **OR construction** — `rng_hi/rng_lo` from the correct bars; skip when `rng<=0` or <2 OR bars.

**Oracle parity (§10.1) is NOT reproducible here** — `s5_intraday_research*.py` and
`reports/xau_m5_3y.csv` are not in this repo. Substitute: pull live OANDA M5 history
and replay the ported `get_signal` to confirm ~4 trades/day and sane long/short splits.
Show the operator these numbers before flipping to real orders. If frequency is far
from ~4/day, pause before live.

## Deploy & retire

1. Commit the port + tests; push branch.
2. `deploy_session_breakout.py --commit` (DB rows for the one strategy).
3. scp changed files to algorobos; rebuild the `./strategies` image.
4. Repoint/recreate the (former `challenge_xau`) service as the SESSION_BREAKOUT
   runner; **stop `challenge_xau_h4`**; retire its dashboard rows.
5. Verify: heartbeat + a live `get_signal` evaluation on the box; confirm exactly one
   challenge strategy shows on the platform.

## Risks & failure modes

- **No local oracle** — confidence rests on the spec's reported OOS numbers + unit
  tests + a live-M5 frequency check, not a re-run of the 3-year backtest.
- **Fill assumption** — backtest fills at the OR boundary (stop order); the live path
  enters market on the break bar (engine is market-based), adding ~half-spread of
  slippage. The spec's 0.80pt cost stress covers this, but verify live XAU spread in
  the session hours stays ≤ ~0.45pt.
- **Trend-regime dependent** — in chop the bias sits at 0 (few trades) or whipsaws; a
  losing quarter is possible. Expect red weeks (6-in-a-row historically).
- **Retiring a validated edge** — this stops the PF-1.83 H4 trend-follow. Recreatable
  from git if the operator wants it back.
