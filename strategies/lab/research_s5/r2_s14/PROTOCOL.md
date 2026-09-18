# r2_s14 — s14_ob_mit_bias improvement programme — pre-registrations (2026-09-18)

Rules: CONTEXT.md §6. Window 2024-09-01 → 2026-09-17, TRAIN < 2025-12-01 ≤ TEST. Mid-M1
harness costs 0.75 base / 1.00 stress; quote-S5 resolution (`lab/s5exit.py`, start offset 60 s,
max-hold 1000 min) 0.45 / 0.70. The incumbent is s14 with `MIN_SL_DIST_PTS=3.0`
(`lab/results/s14_minstop/`, live since 11:11 UTC). Decisions on TRAIN; TEST read once per
hypothesis. Every hypothesis is appended here BEFORE its numbers exist; deviations are
appended with timestamps, never edited in.

---

## H0 — Harness fidelity: the forming higher-timeframe bar (registered 2026-09-18 ~13:05 UTC)

**Observation that triggered it (diagnostic only, not a decision):** `lab/harness.py` builds
`w5m`/`w15m` with `searchsorted(t5, t1[i], side="right")`, and the 2y cache labels bars by
their START (verified: the M5 bar `2026-03-10 10:00` aggregates M1 10:00–10:04). So at M1 bar
`10:03` (closed 10:04) the harness hands `get_signal` the M5 bar `10:00` — which does not close
until 10:05 — with its full OHLC, and the M15 bar `10:00` (closes 10:15) likewise. Live,
`research_runner` uses only OANDA `complete` candles (`tsdb_reader` filters the flag), so the
live M5 window at 10:04 ends at the `09:55` bar. s03 detects order blocks on `w5m.tail(60)`
including that forming bar (an OB whose displacement candle is the *current* bar, whose body
is not yet known live), and s14's `htf_bias` reads the forming M15 bar's close (up to 14 min
ahead). In the incumbent trade list (`s14_ob_mit_bias_minsl3.0_c0.80.trades.parquet`) mean pts
per trade by entry-minute phase within the M5 bar: min 0 → 1.58, 1 → 1.19, 2 → 0.71, 3 → 0.55,
4 (the only phase with no M5 look-ahead) → 0.12. That is the signature of look-ahead, not of a
market effect, and it is the reason this study starts with the harness rather than the strategy.

**Question.** How much of s14's harness edge (mid-M1 PF 1.408 / 1483 pts at 0.80, floor 3.0;
quote-S5 1.227 / 879) survives when `get_signal` sees only bars that were CLOSED at the moment
the live runner would call it?

**Rule.** A copy of the harness (`r2_s14/harness_closed.py`, otherwise byte-identical logic)
with one extra `Cfg` field `closed_frames: bool`. When True, the M5 window ends at the last bar
whose start ≤ `t1[i] − 4 min` and the M15 window at the last bar whose start ≤ `t1[i] − 14 min`
(i.e. bars closed by the M1 close at `t1[i] + 1 min`). Nothing else changes: M1 window, gates,
exits, costs, cooldown identical.

**Control.** The same copied harness with `closed_frames=False` must reproduce the incumbent
arm exactly (n 1624, pts 1483.3, PF 1.408 at 0.80, floor 3.0) — if it does not, the copy is
wrong and nothing below is read.

**Arms** (4 replays): floor 3.0 × cost 0.75 and 1.00, `closed_frames` False (control) and True.
Then quote-S5 resolution of the closed-frames arm at 0.45 / 0.70.

**Prediction / decision.** If the closed-frames arm loses more than a third of the incumbent's
mid-M1 TRAIN points, the incumbent's harness numbers are not a valid baseline and every later
hypothesis in this folder is judged against the closed-frames incumbent instead (the live
parity read from the prod DB, H-live below, decides which harness matches the box). If the
loss is < 10 %, the harness stays as is and this is recorded as a fidelity note.
Bars: none of 1–7 apply — this is a measurement of the instrument, not of a change.

---

## H-live — Harness-vs-live parity on the first live half-day (registered with H0)

Read-only query of the prod DB for strategy `Research OB_MIT_BIAS`: every `apis_strategysignal`
row today (status, rejection reason, side, levels, time) and every `apis_position`. Rebuild M1/M5/
M15 for 2026-09-18 from the S5 cache (mid), replay s14 under both harness variants over today
with the floor that applied at each time (1.5 before 11:11 UTC, 3.0 after), and report: which
harness reproduces the live signal times/sides/levels, the count of live signals not produced by
either, and the count produced by the harness but absent live. Descriptive — no bar.

---

## Deviation note (2026-09-18 ~13:40 UTC, before H0's numbers exist)

H-live's today check has been run (descriptive; `parity_today.py`, output in REPORT.md): the
three live signal rows of 09:26–09:28 are reproduced to the cent by BOTH conventions, but the
open convention also emits signals at 08:50/08:51 that the live runner never logged, and the
closed convention does not. Live therefore = closed frames, independent of H0's magnitude.
**Decision taken now:** every hypothesis below is run with `closed_frames=True` and judged
against the closed-frame incumbent (s14, floor 3.0, closed frames — H0's arm), whatever H0's
size turns out to be. The open-frame incumbent is reported alongside for reference only.

Cost handling: `cost_pts` is charged once per trade at entry and touches nothing else in the
harness (gates, exits, cooldown are cost-free), so one replay per configuration is run at
cost 0 and the 0.75 / 1.00 mid-M1 numbers are derived by subtracting the cost per trade;
quote-S5 resolution takes its own `--cost` (0.45 / 0.70). This is arithmetic, not a re-run.

Variant module `r2mod/s14v.py` (parameterised s14; identity with the live module verified on
3,000 M1 bars / 16 signals, 0 differences) is the vehicle for H2–H5; its knobs are reached via
`Cfg.patch`. Trades are always produced by re-running `get_signal`, never by filtering.

---

## H1 — Stop-floor extension 3.5 / 4.0 / 5.0 (registered before running)

**Question.** The minstop grid ended at 3.0 with TRAIN still rising. Does the plateau
continue? **Rule.** `min_sl_dist_pts` ∈ {3.5, 4.0, 5.0} (gate, live semantics), closed
frames, everything else as live. **Grid fixed** (3 arms + the 3.0 incumbent from H0 + the
1.5 arm from H0 as context). **Judged** on quote-S5 at 0.70 (stress): bars 1–5 by mid-M1
at 0.75/1.00; bar 6 plateau across 3.0/3.5/4.0/5.0; bar 7 beat the closed-frame 3.0 incumbent
on TEST quote-S5 PF AND points at 0.70; **selection on TRAIN quote-S5 PF at 0.70** (best
TRAIN PF wins; TEST read once for that floor). Comparisons: 3. Control: the incumbent itself
(a floor is a subset gate; the S5-programme geometry result says tighter stops pay a flat
0.275 pts of trigger cost, so a floor can only win by removing negative-EV geometry — no
concept is being credited).

## H2 — Order-block quality gates (registered before running)

**Question.** Is a *better* block a better trade? Three TTrades-derived gates and one
detector-strength gate, each a separate arm on the closed-frame 3.0 incumbent:
  H2a `FIRST_TOUCH=True` — reject a block already mitigated by a closed M5 bar after its
      displacement bar (a spent block);
  H2b `MAX_AGE_BARS=12` — block no older than 1 h (freshness);
  H2c `DISP_MULT=2.5` — stronger displacement away from the block (vs 1.8);
  H2d `ZONE='body'` — the block is the candle body, not the wick range (ict-smc skill spec).
**Control (for H2a, the gate with the largest expected rejection rate):** `RANDOM_GATE=p`
with p set to H2a's realised acceptance fraction (raw-signal level, measured after H2a runs
and declared in the report before the control runs) — a concept-free gate with the same
thinning. The concept is credited only with what the random gate cannot explain. Bars 1–7 vs
the closed-frame incumbent at 0.70 quote-S5, TRAIN-justified; comparisons: 4 (+1 control).

## H3 — Stop at the protected swing (registered before running)

**Rule.** `SL_MODE='swing', SWING_N=5` — stop beyond the extreme of the 5 M5 bars ending at
the OB candle (the swing the displacement left from) − 0.3, instead of the block edge. TP
stays 2R of the new risk. Also `SWING_N=3`. Floor 3.0 stays (it is the live gate).
Comparisons: 2. Judged as H2.

## H4 — Target structure (registered before running)

**Rule.** `TP_R` ∈ {1.0, 1.5, 3.0} vs incumbent 2.0. Plateau bar across 1.0/1.5/2.0/3.0;
selection on TRAIN quote-S5 PF at 0.70 with the points condition of bar 7. Comparisons: 3.
(A liquidity-target variant is not run: the S5 programme found level touches are re-pokes
with continuation drift; a fixed-R grid is the honest first test of "target structure".)

## H5 — The bias filter (registered before running)

**Question.** 15m EMA21 is the strategy (s03 alone fails). Is it the optimum, and is a
higher-timeframe alignment additive?
  H5a `BIAS_LEN` ∈ {9, 50} on 15m;  H5b `BIAS_TF='5m'` (EMA21);  H5c `BIAS_TF='1h'`
  (EMA21 on closed H1 bars resampled from the M15 window — 100 M15 bars = 25 H1 bars, so
  the EMA is seeded short; declared as such);
  H5d daily alignment: harness `regime_align=True` (a small extension of the study harness:
  BUY admitted only while the newest CLOSED daily close > SMA20, SELL only while ≤ SMA20 —
  the TTrades "no entry without a higher-timeframe reason", using the frame the harness
  already has). Comparisons: 5. Judged as H2; for H5d the Stage-2 xau2y arms (s14 above/below
  SMA alone) are the context.

**Compute plan:** ~17 replays × ~7.5 min on ≤ 3 workers. Multiple comparisons across the
folder: 17 arms + 1 control. With ~750 TEST trades per arm a PF difference of ~0.1 is
roughly one standard error; anything adopted must clear bar 7 by more than that and be
TRAIN-justified, or it is reported as a lead, not a change.

> Clock correction (appended 11:58 UTC by `date -u`): the "~13:05" and "~13:40" stamps above
> were written from a wrong mental clock; the true registration times were ~11:30 UTC (H0,
> H-live) and ~11:50 UTC (deviation note, H1–H5). Order is unchanged: all were written before
> the corresponding numbers existed (H0's replays were still running at 11:57 UTC).

---

## H0 result (12:20 UTC) and H0-ext (registered 12:25 UTC, before running)

H0 control reproduced the incumbent exactly (n 1624, 1483.3 pts, PF 1.408). Closed frames:
n 1499, **−1153.8 pts, PF 0.740, WR 34.0 %** at 0.80 (TRAIN 0.735 / TEST 0.746). The loss
exceeds the one-third trigger by a wide margin; per the H0 decision rule every hypothesis is
judged against the closed-frame incumbent (already decided in the deviation note).

**H0-ext (mechanism and blast radius), 3 replays, descriptive:**
  (i)  s14 floor 3.0, M5 closed / M15 forming — isolates the s03 order-block look-ahead;
  (ii) s14 floor 3.0, M5 forming / M15 closed — isolates the EMA21 bias look-ahead;
  (iii) `c03_fvg_fill` at its live config (floor 1.5, closed frames) vs its Stage-1 arm
        (`lab/results/xau2y_stage1/c03_fvg_fill_c0.80`) — c03 reads `w5m.iloc[-1]` as the
        "5m candle that closed back outside the gap" and is live since 05:00 UTC today. Out of
        this study's mandate to change; in it to measure, because the same harness produced
        its deployment numbers.
Implementation: `Cfg.closed_frames` becomes a tri-state in the study harness: True (both),
"m5" (only M5 closed), "m15" (only M15 closed); False unchanged.

> Clock correction 2 (appended 12:02 by `date -u`): the "12:20 / 12:25" stamps in the H0-result/H0-ext block were again estimates; the true times were ~11:58 (H0 result read) and ~12:01 UTC (H0-ext registered, before its arms ran — `results/h0_ext.log` shows the launch). From here on every stamp is taken from `date -u`.

> Run note (12:25 UTC): the H1–H5 pool driver died with BrokenProcessPool after arms h1_floor3.5 / h1_floor4.0 finished (a worker was terminated externally — the Mac is shared with other studies). Restarted as two in-process drivers (`R2_SLICE=0/2`, `1/2`, workers 1 each; + the H0-ext driver = 3 processes). Resumable: finished arms are skipped, nothing is re-run or changed.

## H2a control — declared 12:46 UTC, before it runs
H2a (FIRST_TOUCH) produced 381 trades vs the closed incumbent's 1499 → acceptance 0.254 at the trade level (deviation: the protocol said raw-signal level; measuring that needs a second full signal walk, so the trade-level ratio is used and declared here). Control arm: `RANDOM_GATE=0.254`, everything else the incumbent, closed frames, floor 3.0. Credit FIRST_TOUCH only with what this arm cannot explain.

---

## Coordinator instruction received 13:26 UTC — grid closed

The coordinator independently confirmed the H0 defect (r2_c03 agent: closed-bar alignment
reproduces 6/6 live c03 signals; shared harness 2/6) and instructed: use
`r2_c03/harness_fixed.py` for anything still run, re-run baseline + best arm under it, run
no more of the grid, state which numbers came from the leaky harness.

Status at this instruction: `harness_closed.py` with `closed_frames=True` applies the
identical rule to `harness_fixed.py` (both: `searchsorted(t5, t1[i] − 4 min, "right")`,
`searchsorted(t15, t1[i] − 14 min, "right")`; verified by grep of both files) and every
arm in this folder except the H0 control was run with it — so the baseline (H0 closed, floor
3.0) and all H1–H5 arms already are "under the fixed harness". Arms finished: H0 (4),
H0-ext (3), H1 (3), H2 (4 + control), H3 (2), H4 (3), H5a-ema50, H5b, H5d. In flight at the
instruction: h5a_ema9 and h5c_1h (they complete within the 30-min budget and are reported if
they finish; nothing else is started). **Grid closed; no further arms.**

Leaky-harness numbers in this folder: only the H0 control arm
(`h0_closed_frames/s14_f3.0_c0.80_open`) and the two H0-ext mechanism arms
(`m5closed_only`, `m15closed_only`), all deliberately so — they measure the defect.
Also the diagnostic minute-phase table quoted in H0, which is read off the leaky incumbent's
trade list. Every other number is closed-bar.

> 13:38 UTC: h5a_ema9 finished and is reported; h5c_1h was killed unfinished on the coordinator's instruction (no result file; not reported). All study processes stopped.
