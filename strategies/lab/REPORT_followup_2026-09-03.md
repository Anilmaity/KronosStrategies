# Follow-up replays — the two experiments the 2026-09-02 campaign left unrun

> Run 2026-09-03. These are the two experiments `CAMPAIGN.md` closed with under
> "Next session — two experiments, both ready to run". Neither could execute on 2026-09-02:
> multi-minute background jobs stopped surviving late in that session and every full replay
> exceeds the 120s foreground cap. Both are unchanged from how they were committed in
> `56b0f79` — no edit was made to either script or to any strategy module before running
> them, so these results test what was queued, not a revised version of it.

## Provenance and what was actually executed

| item | value |
|---|---|
| harness | `lab/harness.py` at `56b0f79`, unmodified |
| interpreter | `KronosStrategies/.venv` (pandas 2.2.2) |
| strategy modules | untouched; only `Cfg.patch` constants differ between arms |
| S100 window | 2025-01-05 → 2026-08-12, split 2026-02-01 |
| S94 window | 2025-11-01 → 2026-08-12, split 2026-02-01 (test half 6.5 months) |
| logs | `results/s100_ob.log`, `results/s100_ob_control.log`, `results/s94_decisive.log` |

`*.log` is gitignored (`.gitignore:35`), as it was for the 2026-09-02 campaign, so those three
files are local-only and this report is the durable record — every arm's n, points and PF
are transcribed verbatim below rather than referenced (win rate and max-drawdown are quoted
where they carry the argument).

### One control arm was added, and why

`_s100_ob.py` compares its OB-off arms against the stored baseline in
`results/s100_m3_combo_base.json`. That file was written at **03:32** on 2026-09-02;
`harness.py` was last modified at **05:18** — *after* it. The warm-up fix (`i0` must satisfy
every frame, not just `win_1m`, which for S100 moves the first traded bar from 705 to 1505)
and the env-restore fix both landed in that gap. Window and split are identical, so the
stored baseline is not wrong, but "OB-off under today's harness vs OB-on under yesterday's"
is a cross-version comparison, and the campaign's own process-failure list already contains
"edited a module mid-sweep" as a contamination source.

`lab/_s100_ob_control.py` therefore re-runs **OB-on at cost 0.45 under the current harness**,
in its own log file so it can run concurrently without two processes appending to one file.
The 0.80 comparison was already same-code inside `_s100_ob.py` (arms 2 and 3); this makes the
0.45 comparison same-code too. Any OB verdict below is stated against this control, not
against the stored baseline.

## Experiment 1 — S100: does disabling the OB entry model help?

**Question.** The S100 sub-agent's decomposition of the saved trade list found OB edge-retest
is the only one of the three entry models negative in both halves and over the full period
(TRAIN PF 0.997 / TEST 0.957 / full 0.976 over 1274 trades). It explicitly refused to call
that shippable, because OB shares the single-slot pending-retrace machine (`_pending`) with
FVG: removing OB changes which *later* signals get a chance to fire, an effect a filtered CSV
cannot measure. This is the filtered replay it asked for.

**Patch verification.** OB is disabled by setting `_OB_DISP` to 1e9 so the arming test
`body >= _OB_DISP * atr` can never pass. The log records the entry models present in each
arm: the OB-off arms report `[S100_FVG/S100_RSI3_MOMO]` with no OB reason string, so the
patch is verified from the output rather than assumed.

**Result — all four cells, OB-off is worse.**

| arm | TRAIN n | TRAIN pts | TRAIN PF | TEST n | TEST pts | TEST PF |
|---|---|---|---|---|---|---|
| OB **on** @0.45 (control) | 2276 | +12.6 | **1.002** | 1656 | +152.2 | **1.027** |
| OB **off** @0.45 | 2473 | −318.4 | 0.948 | 1664 | −68.7 | 0.989 |
| OB **on** @0.80 | 2276 | −784.0 | **0.867** | 1656 | −427.4 | **0.928** |
| OB **off** @0.80 | 2473 | −1183.9 | 0.824 | 1664 | −651.1 | 0.904 |

**The harness-version worry resolved itself.** The `obON_c0.45` control reproduces the stored
03:32 baseline *exactly* — n=2276/1656, pts +12.6/+152.2, PF 1.002/1.027, WR 33.7/33.7,
DD −377.0/−265.5, every figure identical. The warm-up fix therefore changed nothing for S100,
as `REPORT_s100.md` predicted ("the fix affects ~1 of 585 days"). Two consequences: the
stored baseline was safe to cite all along, and the trade-count difference in the OB-off arms
is entirely the mechanism, not harness drift. Separately, `obON_c0.80` (TRAIN 0.867 /
TEST 0.928) reproduces to three decimals the numbers the S100 coordinator derived by
cost-recompute rather than replay, which independently validates that shortcut.

**Verdict: REJECT — keep the OB entry model.** Disabling it costs 0.054 PF on TRAIN and
0.038 on TEST at 0.45, and 0.043/0.024 at 0.80. There is no cost level and no half at which
removing OB helps.

### Why the CSV said the opposite, and why that matters more than the verdict

The sub-agent's decomposition of the saved trade list, and the coordinator's post-filter on
top of it, both predicted the **opposite sign**:

| | TRAIN PF | TEST PF |
|---|---|---|
| baseline (OB on) | 1.002 | 1.027 |
| static filter, "drop OB model only" | 1.005 | 1.058 |
| **filtered replay, OB actually disabled** | **0.948** | **0.989** |

The static filter deletes OB rows from a fixed trade list. The replay lets the strategy run
without OB and discovers a *different* trade list: TRAIN n rises 2276 → 2473 rather than
falling to the 1569 that subtracting OB's 707 trades would give — about 900 trades that only
exist because OB is gone.

The mechanism is legible directly in `s100_m3_combo.py`, not merely inferred. `_arm()`
overwrites a single module-global `_pending` slot; FVG is tested first (step 1) and returns
early when it arms, so OB (step 2, the `body >= _OB_DISP * a` test at line 249) only ever
arms on bars where FVG did not. Remove OB and that slot is left free, so later FVG/RSI setups
arm where a pending OB would previously have been holding it. Those replacement trades are
worse than the OB trades they displace — enough to move both halves from marginally positive
to negative.

**This is the campaign's cleanest demonstration that filtered-CSV attribution can invert the
sign of an effect on a strategy with shared state.** The S100 sub-agent called exactly this
risk and refused to ship on the CSV; that refusal was correct, and the cost of ignoring it
would have been shipping a change that makes the roster's only clearly profitable strategy
worse. Any future "drop model/hour/filter X" claim on a strategy with a shared `_pending`
slot must be settled by replay.

## Experiment 2 — S94: does `_SD_MULT` 2.5/3.0 survive a wider window?

**Question.** `_SD_MULT` is the only S94 change from the whole campaign that cleared all five
pre-registered bars, but only on a 5-month window (TRAIN 2025-12-01..2026-02-01, TEST
2026-02-01..2026-05-01). `REPORT_s94.md` made a full-window confirmation an explicit
precondition for touching the live constant, and was blunt about why: this strategy's own
history is a warning against trusting a single window.

**Scope, stated plainly.** This runs 2025-11-01..2026-08-12 and judges the test half
2026-02-01..2026-08-12 — a 6.5-month held-out sample, more than twice the sub-agent's
3-month half, at roughly half the cost of the full 19.5 months. This is a genuine widening,
**not** the full-window confirmation originally demanded. It should not be described as one.

**Result — the direction replicates, the profitability does not.**

| arm | TRAIN n | TRAIN pts | TRAIN PF | TEST n | TEST pts | TEST PF | TEST WR |
|---|---|---|---|---|---|---|---|
| `_SD_MULT`=2.0 @0.45 (shipped, in-run control) | 197 | −47.7 | 0.947 | 469 | −320.6 | 0.858 | 22.4 |
| `_SD_MULT`=2.5 @0.45 | 197 | +6.1 | 1.006 | 469 | −33.0 | 0.986 | 18.8 |
| `_SD_MULT`=3.0 @0.45 | 197 | +74.4 | 1.076 | 469 | −27.6 | 0.989 | 15.8 |
| `_SD_MULT`=2.5 @0.80 | 197 | −62.9 | 0.937 | 469 | −197.1 | 0.920 | 18.6 |
| break-even @1R, 0.45 | 197 | −91.9 | 0.849 | 469 | +15.7 | 1.011 | 15.8 |
| break-even @1R, 0.80 | 197 | −160.8 | 0.759 | 469 | −148.5 | 0.904 | 15.8 |

The in-run control lands on TEST PF 0.858, matching the reference line the script wrote for
the shipped configuration — so the widened window is measuring the same system.

**`_SD_MULT` against the five pre-registered bars:**

1. *Improve TEST PF and points* — **partially**. PF 0.858 → 0.986 (2.5) / 0.989 (3.0);
   points −320.6 → −33.0 / −27.6. Both improve substantially, but neither clears 1.0. The
   5-month window gave 1.151/1.154. **FAIL** as written — the bar demanded above 1.0.
2. *Survive 0.80 stress* — **FAIL**. 2.5 falls to TEST PF 0.920. On the 5-month window it
   held at 1.089.
3. *Plateau* — **PASS**. 2.5 and 3.0 land almost on top of each other (0.986 vs 0.989), and
   TRAIN rises monotonically 0.947 → 1.006 → 1.076.
4. *n unaffected* — **PASS**, exactly: 197 TRAIN / 469 TEST across every `_SD_MULT` arm.
   Raising the TP never pushed a setup below the `_MIN_RR` gate.
5. *Plausible mechanism* — **PASS**. WR falls 22.4% → 18.8% → 15.8% while PF rises: the
   "fewer, bigger wins" signature expected from raising a fixed TP multiple.

**Verdict: DO NOT SHIP `_SD_MULT` to the live constant.** The effect is real — consistent
sign, monotone in the parameter, a genuine plateau, zero trade-count cost, and a mechanism
that matches the module's stated design intent. It is not *sufficient*: on a 6.5-month
held-out half it converts a large loss into a small one and does not reach break-even, and it
fails cost stress outright. `REPORT_s94.md` made full-window confirmation an explicit
precondition for touching the live config. This is the widening it asked for, and the honest
reading is that the 5-month TEST PF of 1.151 did **not** generalise.

**Break-even stop at 1R: REJECT.** It is the only arm here that puts TEST PF above 1.0
(1.011), and that is worth stating plainly because `be=True` is what the retired "PF 1.82"
number was actually produced with. But it fails on two counts: it *degrades* TRAIN sharply
(0.947 → 0.849) while improving TEST, which is the one-sided shape the campaign already
flagged as weak evidence when S100's hours showed it; and at 0.80 it collapses to 0.904.
A change that helps only the half you are judging on, and only at optimistic cost, is not
evidence of an edge.

## What this means for the roster

Neither experiment produced a shippable change, and no live configuration was touched. That
is the correct outcome for both: one refuted a lead that the CSV made look attractive, the
other refused a lead that a short window made look shippable.

The campaign's `roster-near-breakeven` diagnosis survives intact and is reinforced. Every
number above sits within a few points of PF 1.0, and the single largest mover in the whole
table is not any parameter — it is the cost assumption. Going from 0.45 to 0.80 costs S100
0.135 PF on TRAIN and 0.099 on TEST, which dwarfs every parameter effect measured in either
experiment. That is the same conclusion the campaign reached from a different direction:
**at this margin, cost reduction dominates parameter tuning.** The standing open lead —
limit-order entries instead of paying aggressor spread at a retrace level the strategy has
already computed in advance — remains the highest-value unstarted work, ahead of any further
sweep.

## Status of the campaign's queue

Both items under "Next session — two experiments, both ready to run" are now **closed**.
Still open and unstarted: **R4 limit-order entries**, and the standing question of why the
ungated harness baselines lose while the live gated book earns.
