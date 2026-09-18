# Protocol — S99 (MSS+FVG reversal) and S100 (M3 combo) : fix or retire (pre-registered 2026-09-18 11:50 UTC)

Written BEFORE any arm of this study ran. Anything that changes after a number exists is
appended under "Deviations" with a timestamp; nothing above that line is rewritten.

## 0. Question

Both strategies are live on the demo book and both fail the 24-month screen at their shipped
configuration (Stage 1, `lab/REPORT_xau2y_2026-09-18.md`: S99 TEST PF 0.925 / 0.835 at 0.45 / 0.80
mid-M1; S100 1.065 / 0.959). Neither has had a Stage-2 grid. **Can a single, mechanistically
motivated configuration change make either one pass bars 1–7 under live triggers (quote-S5) at
stress cost, chosen on TRAIN alone — or should the strategy be retired?**

The known defects the project has already recorded, which the grids below address directly:
- S99 has **no minimum FVG size** and sizes its stop off structure (distal FVG edge + 0.2×ATR)
  with **no absolute floor** → tight stops, 54 `sl_too_tight` rejects roster-wide in 14 days
  (Timeline 08-04). Its stop distance is *exactly* `gap + 0.2·ATR(14, M5)` (see `_detect_mss`),
  so "minimum FVG size" and "stop floor in ATR units" are the same knob written two ways.
- S100 floors its **target** at 1.5 pt but not its **stop**; the ARM_SL_FLOOR fix (widen the
  stop to the floor at arm time) failed its A/B 3/3 on points (Timeline 08-05). Widening is
  refuted; *rejecting* tight stops the way `entry_manager` does (`MIN_SL_DIST_PTS`) is untested
  for S100 — it worked for s14 (`REPORT_s14_minstop_2026-09-18.md`).
- Prior negative results that are NOT re-run here (established, see CONTEXT §3): S99 `_TP_R`
  2.0–3.5 (monotone worse), S99 absolute `min_sl_dist_pts` 2.0–4.0 on the 19.5-mo window at
  0.45 (TEST never recovers), S99 hour restriction (hour PF flips between halves), S100
  disable-OB (shared `_pending` inverts the CSV sign), break-even at 1R, book-wide stop floor.

## 1. Data, window, split, costs, resolution

- Bars: `backtest/results/bars_cache_2y/` (QA'd), `LAB_BARS_CACHE=backtest/results/bars_cache_2y`.
  Replay window **2024-09-01 → 2026-09-17**. **TRAIN < 2025-12-01 ≤ TEST** (by entry time).
- Harness: `lab.harness.replay` — the REAL `get_signal()`, real `shared.gate_rules`
  (`sl_too_tight` at the arm's floor, news blackout 12:25–12:45), SL-before-TP on M1, exits on mid.
  Windows: harness `WINDOWS` for each module (S99 win_5m 160; S100 win_1m 700).
- Costs, new convention (CONTEXT §3): **mid-M1 0.75 base / 1.00 stress; quote-S5 0.45 / 0.70.**
- **One replay per arm, at cost 0.75.** Cost enters the harness only as a flat per-trade
  subtraction after the trade list is fixed (`pts = raw − cost_pts`; nothing upstream reads it —
  asserted by the execution study on every Stage-1 pair). The 1.00 series is therefore derived
  exactly: `pts_1.00 = pts_0.75 − 0.25`. To prove this rather than assume it, **both baselines are
  replayed at 0.75 AND at 1.00** and the derived 1.00 must equal the replayed 1.00 to 1e-6 on
  every trade; if it does not, every arm is re-run at 1.00 (deviation logged).
- Quote-S5 resolution: every arm's trades re-resolved on the S5 cache with the vectorised replica
  of `lab/s5exit.resolve()` from `research_s5/execution/03_geometry.py` (mode `quote_s5`: long
  exits on the bid, short on the ask; SL before TP within a bar; walk starts 60 s after the
  entry bar's open; horizon = the module's own `_MAX_HOLD_MIN`: S99 480, S100 72). Costs 0.45 /
  0.70 applied to the raw quote points. The replica is validated first by reproducing
  `lab/results/s5exit/s14_ob_mit_bias_c0.80.parquet` outcomes (≥ 99.9 %) before any S99/S100
  number is read.
- Live record (prod DB, read-only, `PGSSLMODE=require`, `default_transaction_read_only=on`):
  `apis_strategysignal` (PLACED/REJECTED + `rejection_reason`) and `apis_position` (realized
  P&L, ×100 = USD) for 'S99 MSS FVG Reversal' and 'S100 M3 Combo Scalper' since 2026-07-01.
  Descriptive; it does not select any arm. It is read AFTER the grids are pre-registered but is
  allowed to be read before the grids finish running (it cannot influence a fixed grid).

## 2. Baselines (re-established first, under the new convention)

`s99_mss_fvg` and `s100_m3_combo` at shipped config: mid-M1 0.75 and 1.00 (two real replays
each), quote-S5 0.45 / 0.70. These are the incumbents for bar 7. Expectation from Stage 1
re-costed: S99 TEST ≈ 0.86 / 0.80; S100 ≈ 0.99 / 0.90 — i.e. both fail bars 2 and 3 before any
change; the question is whether one change lifts TEST PF > 1 at both costs while TRAIN > 0.9.

## 3. Bars (the decision rule, identical for every arm)

Bars 1–5 of `PROTOCOL_xau2y_2026-09-18.md`, computed by `lab/tools/campaign_score.score_campaign`
logic on **quote-S5 trades at 0.45 (base) / 0.70 (stress)** — the tradeable claim — and reported
also on mid-M1 0.75 / 1.00 for comparability with every earlier report:
1. TEST n ≥ 40.
2. TEST PF > 1.0 at base AND stress.
3. TRAIN PF > 0.9 at base.
4. ≥ 55 % TEST months positive; no TEST month > 50 % of TEST net.
5. Regime independence: positive in gold-up AND gold-down months, or |corr| < 0.4.
Stage-2 bars for a change to an existing strategy:
6. Plateau: the neighbouring grid values move in the same direction (no lone spike). For a
   categorical gate (bias, ER mode, sides, regime) the "neighbour" is the second definition of
   the same concept (declared below) moving the same way.
7. Beat the incumbent at **stress cost (quote-S5 0.70) on TEST PF AND TEST points.**

**TRAIN-justified selection (fixed now).** Within each dimension a value *qualifies* only if it
beats the incumbent on **TRAIN quote-S5 PF and TRAIN quote-S5 points at 0.70**. Among qualifiers
the pick is the best TRAIN PF. TEST is read once, for the pick only. Non-qualifying values have
their TEST reported for the record (they were run) but cannot be adopted whatever TEST says.
A dimension with no qualifier is dead. **A dimension whose pick fails any of bars 1–7 is dead.**

**Combination stage (pre-declared).** If two or more dimensions of the same strategy produce a
pick that passes bars 1–7, the pairwise combination(s) of those picks are run as stage B (same
rules; the incumbent for bar 7 becomes the better single pick). Nothing else is added after
results exist.

**Verdict rule.** FIX = a pick passes bars 1–7 (TRAIN-justified) → report the exact env/config
change and its TEST numbers. RETIRE = no dimension produces such a pick. The live record is
then weighed as context (does live selection make it better or worse than the sim), never as a
substitute for the bars.

## 4. Controls

- **Identity control** (every variant mechanism): the variant with all knobs at their neutral
  value must reproduce the Stage-1 trade list of the original module exactly (same n, same
  entry times, same levels). Reported before any variant number.
- **Complement control** for every subset gate: the *rejected* subset is replayed as its own arm
  (against-bias, other sides, other regime, dropped hours) so the credit goes to the concept
  only if the removed trades are the losers, not merely dilution. An arm that raises PF but
  loses points fails bar 7 by design (the s94 long-only lesson).
- **Beta control** for any bias/regime gate: bar 5 plus the daily-SMA20 × sides cross-check —
  a gate that only wins in gold-up months is beta, not edge.
- **Cost control**: the same arm at both costs, always.

## 5. S99 grid (module `s99_mss_fvg`; variant mechanics in `s99v.py` in this folder)

The variant patches `get_signal` (via `Cfg.patch`) with a function that is a line-for-line copy
of the original detection/arming logic reading four extra knobs from `s99v`; with the knobs at
their neutral values it must satisfy the identity control. `_pending` / `_last_mss_bar` are
the ORIGINAL module's globals (single shared pending slot preserved).

| dim | knob | values (shipped in bold) | mechanism |
|---|---|---|---|
| A. minimum FVG size | `MIN_GAP_ATR` (gap ≥ k·ATR14 M5 at arm time) | **0**, 0.25, 0.5, 1.0 | a sub-ATR FVG is a weak displacement and, via risk = gap + 0.2·ATR, a cost-dominated stop |
| B. stop buffer | `BUF_ATR` | 0.1, **0.2**, 0.5, 1.0 | gold "sweeps" poke 0.5 pt past levels (S5 sweeps study); 0.2·ATR(M5) ≈ 0.4–0.8 pt sits inside the poke; TP = 1.5R scales with it |
| C. HTF bias gate | `BIAS` ∈ {**off**, with, against} × `BIAS_DEF` ∈ {ema21_15m (house `htf_bias`, s14), ema50_h1 (H1 close vs EMA50 from M15 resampled, skill convention)} | 4 gated arms | July drawdown diagnosis ("counter-HTF-bias filter"), s14 = s03 + bias, TTrades "no entry without HTF reason". `with` = BUY only when bias BULL / SELL only when BEAR. `against` is the complement control. ema50_h1 needs win_15m 400 (S99 ignores w15m, so the baseline is unaffected). |
| D. liquidity memory | `_SWEEP_N` (patch) | 24, **48**, 96 | never tested (REPORT_s99 C2 zero data); the docstring claims a 24–96 plateau |
| E. retrace window | `_RETRACE_W` (patch) | 12, **24**, 48 | never tested (D1); a stale FVG is a different trade |
| F. Stage-2 harness gates | sides BUY / SELL; regime above_sma20 / below_sma20; block London (7–11) / block NY (12–20) | 6 arms | the pre-declared xau2y Stage-2 grid, for comparability |

Arms: baseline ×2 costs + A 3 + B 3 + C 4 + D 2 + E 2 + F 6 = **22 replays**. Bias arms with
`BIAS_DEF=ema50_h1` have their own identity control (baseline at win_15m 400, must equal the
baseline). Comparisons: 20 non-baseline arms → with a 5 % per-bar false-positive rate ~1
spurious single-bar pass is expected; the joint 7-bar rule and the TRAIN-only pick are the guard.

## 6. S100 grid (module `s100_m3_combo`; patch/env only — no variant needed)

| dim | knob | values (shipped in bold) | mechanism |
|---|---|---|---|
| A. stop floor (reject) | `Cfg.min_sl_dist_pts` | **1.5**, 2.0, 2.5, 3.0 | live `MIN_SL_DIST_PTS` semantics: the tight signal is rejected and the slot freed; spec says sub-0.5×ATR stops are cost fodder; s14 precedent |
| B. hours | `_HOURS` (patch) | **(1..8,13,14,15)**; drop 1; drop 1–2; drop 1–3; NY-only (13,14,15); Asia/London-only (1..8) | live hours 1–3 were −$379 on n 28 (found post hoc in Sept; failed the TRAIN bar on the 19.5-mo window). Confirmatory: the 2024-09→2025-11 TRAIN half now includes 4 months never seen by that read; the arm must qualify on TRAIN to be adopted |
| C. ER trend-persistence gate | env `S100_ER_GATE` ∈ {**off**, ranging, strict} | 2 gated arms + control `off` at win_1m 1600 | the module's own default-OFF gate, never run in this harness on 24 months. Gate on raises MIN_BARS_1M to 1560 → win_1m 1600; EMA warm-up over a longer window changes signals slightly, so the `off @1600` control is the incumbent for these two arms |
| D. TP multiple | `_TP_R` (patch) | 2.0, **2.5**, 3.0 | the spec's claimed 2.0–3.0 plateau; the one-module-constant bracket |
| E. Stage-2 harness gates | sides BUY / SELL; regime above_sma20 / below_sma20 | 4 arms | as S99 F |

Arms: baseline ×2 + A 3 + B 5 + C 3 + D 2 + E 4 = **19 replays**. Shared `_pending` slot: every
arm is a full re-run of `get_signal` (never a filter of a saved list).

## 7. Reporting

- Every number from script output (`results/*.csv`, `results/*.txt`); nothing hand-typed.
- Per arm: n, mid-M1 PF/pts at 0.75/1.00, quote-S5 PF/pts at 0.45/0.70, TRAIN/TEST halves,
  bars 1–7, qualifies-on-TRAIN flag, TEST read flag.
- Negative results in full. Identity controls first. Live record as its own section with the
  rejection-reason table and live PF/USD by month.
- Final: per strategy FIX (exact change + TEST numbers) or RETIRE (numbers), and what the book
  loses/gains (trades/day, points, correlation with the survivors c03/s14 at daily-R level from
  the Stage-3 daily series if cheap; otherwise stated as not computed).

## 8. Compute plan

≤ 3 worker processes (`lab.sweep`, `--workers 3`). Expected ~150–170 s per replay → ~40 min
wall-clock for both grids. S5 resolution from the session's `s5_window.npz` mirror.

## Deviations (append only, with timestamps)

- 2026-09-18 ~12:21 UTC (resumed 12:23; timestamp corrected from a mis-typed 12:31 at 12:27) — the S99 sweep's process pool lost a worker (`BrokenProcessPool`) after 21
  of 24 arms; the campaign was resumed unchanged (lab.sweep skips finished arms) for the three
  missing arms (`s99_below`, `s99_nolondon`, `s99_nony`), then S100 started. No arm, knob or rule
  changed. Noted because the S99 log now shows a traceback between two batches of arms.
