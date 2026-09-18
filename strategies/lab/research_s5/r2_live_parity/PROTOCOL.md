# r2_live_parity — PROTOCOL (pre-registered 2026-09-18 11:52 UTC, before any result number)

## Question
Measure the live book (2026-07-01 → 2026-09-18) against the harness on the same dates and
bars with the execution facts now known, and say what the book should look like. Four
sub-questions, each with its own pre-declared definitions and read-out:

(a) per strategy: live PF / points vs harness on the same window; the share of the sim→live
    gap attributable to each entry gate, from the live rejection record;
(b) execution: live fill vs signal price and live stop-out slippage vs the execution study's
    gap-through model, by UTC hour;
(c) risk rails: how often the kill-switch and the soft brake fired, what they saved or cost
    against the counterfactual, and whether the concurrency cap binds under the 09-18 roster;
(d) book construction: with measured live per-strategy edges and correlations, what sizing /
    concurrency / roster the evidence supports; which legs are negative-expectancy live and
    since when.

This is a measurement study, not a hypothesis test with a control. Where a claim is made
("gate X is protective", "leg Y is negative"), the pre-declared read-out below says what
number decides it and what sample floor applies. No number is interpreted below the floor.

## Data sources (all read-only, extracted once to `results/live_*.parquet` by `00_extract.py`)
- `apis_strategysignal`: every generated signal, `status` PLACED/REJECTED, `rejection_reason`,
  nominal `entry_price/stop_loss/take_profit`, `signal_at`.
- `apis_position` + `apis_order` (ENTRY / TARGET / STOPLOSS / TIME_EXIT) + `apis_trigger`.
- `broker_deals`: MetaAPI history-deals (USD `profit`, `commission`, `swap`, fill `price`,
  `raw.reason`, `raw.comment`), keyed by broker `position_id` = ENTRY `Order.broker_order_id`.
- `apis_manageraction` (KILL_SWITCH / INFO rows), `apis_managerconfig`.
- Harness bars: `backtest/results/bars_cache_2y` (M1/M5/M15, to 2026-09-17) and the S5
  quote cache (to 2026-09-18) via `lab.s5exit.load_s5`.

## Definitions (fixed before computing)
D1. **Live window**: signals with `signal_at` in [2026-07-01 00:00 UTC, 2026-09-18 12:00 UTC);
    positions whose ENTRY order `created_at` is in the same interval. Harness replays run
    2026-07-01 → 2026-09-17 23:59 (the M1 cache end); live trades on 09-18 are reported but
    not replayed (c03 n=2, s14 n=1 — below every floor anyway).
D2. **Time base**: every DB timestamp column is `timestamptz`; read with session TZ = UTC they
    are correct UTC instants (verified: signal_at 09:55:05Z ↔ broker deal_time 09:55:06Z for
    position 119222811). The "−5:30" convention in `Units and Conventions.md` is a Django
    `USE_TZ=False` read artefact and does NOT apply to this extraction. Harness `entry_time`
    is the M1 bar OPEN time; the live runner evaluates the same closed bar at wall-clock
    ≈ bar open + 60–90 s (5 s poll + 20 s OANDA TTL). Kill-switch days are UTC days (the
    manager's convention).
D3. **Live trade** (engine): an `apis_position` row with `symbol='XAU_USD'`, `quantity=0`
    (closed), whose UserStrategy maps to one of S93 / S94 / S99 / S100 / S95 / ORB / c03 / s14,
    with an ENTRY order carrying a non-empty `broker_order_id` (a real broker position; this
    excludes DRY_RUN rows, which have no position_id on the signal, and the single
    `TEST_FILL_VALIDATION` order). Copy-trader rows (`symbol='XAUUSD'`) enter only the book-
    level risk-rail accounting (c), in USD from `broker_deals`.
D4. **Points per live trade**: primary = broker truth from `broker_deals` for that position:
    entry = DEAL_ENTRY_IN price (volume-weighted if several), exit = volume-weighted
    DEAL_ENTRY_OUT price, `pts = (exit − entry)` for BUY, `(entry − exit)` for SELL;
    `usd = Σ profit + commission + swap`. Fallback when no deals exist = `realized_profit_loss /
    ENTRY lots` (points × lots → points) and `usd = realized × 100`. Both are computed; the
    report states how many trades needed the fallback and the agreement between them.
    Live `R = pts / |signal entry − signal stop|` (the nominal stop distance, the same
    denominator the harness uses).
D5. **Harness trade**: `lab.harness.replay` output row; `pts` is net of `cost_pts`. Costs per
    the 09-18 convention: mid-M1 0.75 base / 1.00 stress; the same trades re-resolved by
    `lab.s5exit.resolve` on the quote at 0.45 / 0.70 (declared per table). Live points are
    already net of spread and slippage (broker entry fill → broker exit fill), so the
    like-for-like comparator for live is **quote-S5 at 0.45** (entry half-spread + drift +
    stop slippage, no commission on this account).
D6. **Live configuration reproduced in the harness** (per-strategy `Cfg.env/patch`):
    - S93: `_HOURS=(7,8,9,12,13,14)` for signals before 2026-09-02 00:00 UTC, `(13,14)` after
      (shipped 09-01 22:52 UTC); `S93_SOFT_VETO`/gap cap at code defaults (on) — for
      07-01→07-31 live ran the pre-opt15 module without them; this is recorded as known
      config drift and the July S93 rows are ALSO replayed with `S93_SOFT_VETO=off`.
    - S94: `S94_SIDES=BUY,SELL`, `S94_SD_MULT=2.0` (the live values until 09-18).
    - S99, S100: current module constants (no shipped change in the window; `S100_ER_GATE=off`).
    - `min_sl_dist_pts=1.5`, `news_blackout="12:25-12:45"`, per-module `max_concurrent=1`,
      compose windows (harness `WINDOWS`).
    - c03 / s14: replayed over the window for the prospective book analysis (c) and (d) only
      (s14 with `min_sl_dist_pts=3.0`, the value shipped 09-18 11:11).
D7. **Signal matching** (harness ↔ live): same strategy, same side, harness bar-open time in
    [signal_at − 180 s, signal_at + 60 s]; nearest in time wins, each row used once (the
    `backtest/parity_harness.match_trades` greedy rule, window 180 s, plus the side key).
    Unmatched live signals = "live-only"; unmatched harness signals = "sim-only".
D8. **Gap decomposition** (per strategy, in points at the D5 comparator):
    `sim_total − live_total = Σ sim pts of [matched → live REJECTED, by reason]
                             + Σ sim pts of [sim-only]
                             − Σ live pts of [live-only]
                             + Σ (sim − live) over [matched → PLACED]  (execution/fill term)`.
    Each rejected live signal ALSO gets a counterfactual outcome from its own nominal levels,
    resolved on quote-S5 from `signal_at` + 5 s with the entry at the ask/bid at that bar
    (`04_gates.py`): that is "what the gate refused", independent of harness matching.
D9. **Kill-switch / soft-brake reconstruction**: daily realized USD by exit `deal_time` (UTC
    day) over the LIVE roster (engine strategies + both copy slots) from `broker_deals`;
    trip = first time the running day total ≤ −threshold, with the threshold history read
    from the KILL_SWITCH `ManagerAction.reason` strings (200 → 150 → 250). Cross-check the
    count against the 8 KILL_SWITCH rows. Saved/cost = Σ counterfactual pts×lots of (i) the
    engine harness trades on that UTC day after the trip time and (ii) the live REJECTED
    `soft_daily_brake` signals resolved per D8; both reported at their live sizing (lots
    from the risk-sizing rule $38 / stop / 100, cap 0.10) so the number is in USD.
D10. **Concurrency**: (i) live: count of `open_position_cap` rejections per strategy and the
    D8 counterfactual of those signals; (ii) prospective: replay the 09-18 roster (S93 13-14h,
    S94 long-only SD 2.5, S99, S100, c03, s14@3.0) over 2026-07-01 → 09-17, merge the trade
    intervals, and report the distribution of simultaneous open positions per minute, the
    number of entries that a book cap of 3 / 5 would have refused (first-come order), and the
    points those refused entries carried.
D11. **Live edge per leg**: mean R with a 10,000-draw bootstrap 90 % CI, PF, SQN
    (mean R / sd R × √n), and the point at which the cumulative live points (broker truth)
    last crossed its running maximum ("negative since"). Floors: no verdict below n = 40
    (CONTEXT §6 bar 1); n 40–99 reported as "indicative"; a leg is called **negative-
    expectancy live** only if its bootstrap 90 % CI upper bound on mean R is < 0.
D12. **Book construction**: daily-R correlation matrix across legs (live, and harness for the
    full 09-18 roster); equal-risk vs edge-weighted book compared on the HARNESS 24-month
    TRAIN half (weights fitted on TRAIN < 2025-12-01, read once on TEST ≥ 2025-12-01 and on
    the live window), never on the live sample itself; weights = clip(mean R_TRAIN / var R_TRAIN,
    0, quarter-Kelly-equivalent cap) normalised to the same total risk as equal-weight.
    Read-out: TEST net R, max DD in R, and worst UTC day, equal vs edge-weighted.

## Comparisons and multiplicity
No parameter search. 6 strategies × 2 cost conventions in (a); 24 hours × 2 metrics in (b);
2 rails + 2 caps in (c); 2 weightings × 2 splits in (d). Everything is reported; nothing is
selected by its result.

## Scripts (in order) and outputs
00_extract.py → results/live_signals.parquet, live_trades.parquet, deals.parquet, actions.csv
01_harness_replay.py → results/sim_<strategy>_<seg>.parquet (+ quote-S5 resolution)
02_parity.py → results/parity_<strategy>.csv, results/02_parity.txt
03_execution.py → results/exec_by_hour.csv, results/03_execution.txt
04_gates.py → results/gates_counterfactual.csv, results/04_gates.txt
05_rails.py → results/rails_days.csv, results/05_rails.txt
06_book.py → results/book_*.csv, results/06_book.txt
REPORT.md copies numbers only from results/*.txt.

## Deviations
(appended below with timestamps; history above is never rewritten)

- 2026-09-18 12:40 UTC — Deviation 1 (addition, after 02_parity ran): the harness produces
  trades in periods when the live runner was PAUSED (kill-switch days after the trip, manager
  pauses). To attribute "sim-only" harness trades correctly, `00b_extract_actions.py` pulls all
  `apis_manageraction` rows (PAUSE/START per managed strategy) and 02b classifies each sim-only
  trade as inside-a-live-pause or not. No read-out changes.
- 2026-09-18 12:40 UTC — Observation recorded before 03 runs (not a change): matched trades
  show live entry fills systematically BETTER than the nominal signal level (S100 SL/SL pairs:
  mean adverse fill −0.42 pt). 03_execution will report the signed fill distribution by
  strategy and hour as pre-declared in (b); the interpretation (drift-gate truncation) is
  a hypothesis to be checked against the REJECTED entry_drift detail strings, not assumed.
- 2026-09-18 13:35 UTC — Deviation 2 (addition, after 02–05 ran): the harness books entries at the
  nominal level and charges a flat cost; live fills at the market ~65 s after the bar close and
  the drift gate truncates the adverse side. `02c_drift_model.py` post-processes every harness
  trade: fill = sided OANDA S5 quote at bar-open + 66 s, reject if adverse drift > min(0.5, 0.25 ×
  stop) (the real `gate_rules.drift_budget_pts`), otherwise resolve SL/TP on the quote from that
  fill (no flat cost; the measured live stop slip 0.26 added on SL). Cascade effects (a rejected
  signal freeing the slot) are NOT modelled. Read-out for (a): per strategy on live-active
  windows, "sim-with-drift-gate" PF/pts vs live PF/pts and vs the plain harness. This is the
  comparator the mandate asked for ("the harness does not model entry_drift"); it was not in the
  original script list.
