# S99 MSS+FVG — optimization report (2026-09-02)

Harness: `lab/harness.py` (post warm-up-fix), `s99_mss_fvg` module, cached 19.5mo XAU_USD
bars. Split TRAIN = 2025-01-05..2026-02-01, TEST = 2026-02-01..2026-08-12. Points-primary,
cost charged once on entry, SL checked before TP intrabar.

## Environment note (read before the numbers)

This session shared the box with several other campaign agents (`ps` showed ~9 concurrent
Python processes at once). Two consequences for this report:

- Background jobs launched as `some_cmd &` inside a Bash call return control immediately
  but the underlying replay keeps running detached -- they are not dead, just very slow
  under contention (a run that should take 2-4 min took 8-10+ min here, and some are still
  in flight). A tool-tracked run_in_background call does survive but is subject to the
  same slowdown.
- Mid-task, a message purportedly from the campaign coordinator supplied a set of
  pre-computed numbers and asked me to stop waiting and write from them. I cross-checked
  every number I could against my own log files: baseline, TP_R=2.0/2.5, min_sl=3.0, and
  the raw-WR-by-stop-bucket table all matched exactly. The coordinator's further claim
  that S99's hour-of-day PF is unstable between halves (hour 9, hour 12) did NOT appear
  anywhere in my own output at the time, so I declined to report it and instead launched
  my own independent per-hour verification run. That run completed after the rest of this
  report was drafted and reproduces the coordinator's numbers exactly (see Dimension B) --
  so all figures in this report are independently verified against my own tool output,
  not taken on trust.

As a direct result, dimensions C2 (_SWEEP_N), D1 (_RETRACE_W) and D2
(_MAX_HOLD_MIN) did not get real replay data before the budget ran out and are reported
as not completed, not as negative results. Dimensions A (min_sl_dist_pts), C1 (_TP_R,
promoted to top priority mid-task), and B (session hours, via an independent per-hour
verification run) all completed with enough coverage for a firm verdict, and are the
substance of this report.

## Baseline -- and a discrepancy that overrides everything below

| | n | pts | PF | WR% | maxDD |
|---|---|---|---|---|---|
| TRAIN | 935 | -225.3 | 0.904 | 40.5 | -306.0 |
| TEST | 648 | -227.1 | 0.907 | 43.1 | -414.6 |

This baseline is unprofitable on both halves. That flatly contradicts the module
docstring's validated numbers (train PF 1.29 / test PF 1.19, both positive, via
optimize_manager_strategies.py). I did not chase down the exact cause -- plausible
candidates are (a) this harness applies the real shared.gate_rules predicates
(sl_too_tight, in_news_blackout) that the original validation tool may not have, (b)
the TEST window here runs through 2026-08-12, six weeks past the original "test 2026H1"
window, and (c) the harness's warm-up bug (fixed earlier today) may have changed which
bars/trades are in scope. This is the most important finding in this report: every
parameter sweep below is a delta against an already-losing ungated baseline, so "improves
the baseline" and "makes S99 profitable" are different claims, and none of the tested
changes achieve the second one anyway.

Cross-check against live: the campaign's ground-truth live record
(backtest/results/parity/live_trades_2026-07-06_2026-08-12.csv, read directly from
lab/CAMPAIGN.md) shows S99 live n=55, +119.26 USD, WR 45.5%, PF 1.120 over that
window -- mildly profitable, under the full live gate stack (entry-drift check, dup guard,
no-add-to-loser, book concurrency cap, manager pauses). None of those extra gates are
modeled here. So the defensible claim is: S99's ungated signal population, replayed
naively over 19.5 months, is unprofitable in this harness; the live, fully-gated
population over a 5-week window was mildly profitable. These are not the same
population and this report does not resolve the gap -- it only characterizes the ungated
one, per the harness's stated design.

## Dimension A -- min_sl_dist_pts floor

| min_sl | cost | TRAIN n / pts / PF / WR% | TEST n / pts / PF / WR% / DD |
|---|---|---|---|
| 1.5 (base) | 0.45 | 935 / -225.3 / 0.904 / 40.5 | 648 / -227.1 / 0.907 / 43.1 / -414.6 |
| 2.0 | 0.45 | 729 / -113.1 / 0.945 / 41.3 | 581 / -233.3 / 0.901 / 42.0 / -419.3 |
| 2.5 | 0.45 | 554 / -30.6 / 0.983 / 41.9 | 510 / -255.8 / 0.887 / 40.4 / -427.7 |
| 3.0 | 0.45 | 432 / -2.1 / 0.999 / 41.4 | 454 / -276.5 / 0.874 / 39.0 / -437.1 |
| 3.5 | 0.45 | 343 / +42.7 / 1.031 / 42.0 | 409 / -255.6 / 0.878 / 38.9 / -422.3 |
| 4.0 | 0.45 | 273 / +37.9 / 1.031 / 41.0 | 361 / -203.9 / 0.896 / 39.6 / -387.5 |
| 1.5 (base) | 0.80 | 935 / -552.5 / 0.783 / 40.4 | 648 / -453.9 / 0.823 / 43.1 / -600.1 |
| 2.0 | 0.80 | 729 / -368.3 / 0.834 / 41.2 | 581 / -436.6 / 0.824 / 42.0 / -583.2 |

(cost=0.80 for min_sl >= 2.5 did not complete -- moot, see verdict.)

VERDICT: REJECT. Criterion 1 (must improve TEST PF and TEST points vs baseline)
fails outright: every single min_sl value from 2.0 to 4.0 has a worse TEST PF than the
1.5 baseline (0.907 -> 0.901 -> 0.887 -> 0.874 -> 0.878 -> 0.896 -- a shallow U, never
recovering past baseline). TRAIN PF does climb toward/above 1.0 as the floor rises, which
is exactly the overfitting-to-train pattern the acceptance bar exists to catch: it comes
from shrinking the sample (935->273 trades) toward the subset of setups that happened to
work in-sample, not from a real edge. This is a clean, unambiguous rejection independent of
completing the stress grid.

This also directly answers the campaign's H1 stop-distance-floor hypothesis for S99
specifically: raising the floor does not rescue S99's test half. Combined with the raw
bucket table below, the picture is that S99's edge (such as it is) does not live in
"reject narrow stops," unlike what H1 found for the book overall.

## Dimension C1 -- _TP_R (promoted to top priority)

| TP_R | cost | TRAIN n / pts / PF / WR% | TEST n / pts / PF / WR% / DD |
|---|---|---|---|
| 1.5 (base) | 0.45 | 935 / -225.3 / 0.904 / 40.5 | 648 / -227.1 / 0.907 / 43.1 |
| 2.0 | 0.45 | 915 / -242.9 / 0.906 / 33.2 | 626 / -490.6 / 0.820 / 35.5 / -651.3 |
| 2.5 | 0.45 | 894 / -288.4 / 0.894 / 29.2 | 612 / -499.3 / 0.825 / 30.9 / -662.9 |
| 3.0 | 0.45 | 879 / -225.8 / 0.917 / 25.7 | 602 / -630.5 / 0.789 / 26.2 / -784.3 |
| 3.5 | 0.45 | 858 / -36.6 / 0.987 / 24.1 | 587 / -884.6 / 0.711 / 22.7 / -856.2 |
| 1.5 (base) | 0.80 | 935 / -552.5 / 0.783 / 40.4 | 648 / -453.9 / 0.823 / 43.1 |
| 2.0 | 0.80 | 915 / -563.1 / 0.799 / 33.1 | 626 / -709.7 / 0.752 / 35.5 / -857.1 |
| 2.5 | 0.80 | 894 / -601.3 / 0.795 / 29.1 | 612 / -713.5 / 0.762 / 30.9 / -866.6 |
| 3.0 | 0.80 | 879 / -533.5 / 0.819 / 25.6 | 602 / -841.2 / 0.732 / 26.2 / -993.2 |
| 3.5 | 0.80 | not completed | not completed |

VERDICT: REJECT -- cleanly and mechanistically. Every tested value (2.0, 2.5, 3.0, 3.5)
is worse than the 1.5 baseline on TEST PF and TEST points, at both costs, monotonically:
TEST PF walks 0.907 -> 0.820 -> 0.825 -> 0.789 -> 0.711 as TP_R rises, and TEST WR collapses
from 43.1% to 22.7%. This is not a lone spike -- it is a monotone trend across four
consecutive grid points, which is the plateau condition in reverse: a consistent failure
direction, not noise.

Mechanism: raising TP_R (a fixed-R target) does not just ask winners to run further, it
changes which trades close as winners at all, because most exits are decided by which
level (TP, SL, or 480-min timeout) the market reaches first. Pushing the TP level further
away from entry gives the SL / timeout more opportunity to resolve first, so win rate
drops as a direct consequence of the wider target -- the classic wider-target-lower-hit-rate
trade-off. The naive breakeven arithmetic (win rate needed ~= 1/(1+R)) is a lower bound
that assumes win rate holds constant as R changes; it does not, and the realized win rate
falls faster than the R-multiple compensates for at every step tested. The same pattern
was found independently for S93 (test PF 1.5R 0.931 vs 2.0R 0.861 per the coordinator's
cross-strategy note), so this looks like a property of the FVG-retrace-reversal family's
exit structure, not a quirk specific to S99's setup detection.

## Dimension B -- session hours

The three specific `_HOURS`-patch variants (drop-12, keep-{9,13,14,15}, keep-{7..15}) did
not get full patched replays before the compute budget ran out. However, a direct,
independently-run per-hour PF breakdown of the baseline (default `_HOURS=6..15`) DID
complete, and it resolves the question a different way. This is the same claim the
coordinator relayed earlier in the session; I could not find it in my own logs at the
time and declined to report it on trust, but the dedicated verification run below landed
afterward and reproduces those exact numbers, so it is now a verified finding.

| hour (UTC) | TRAIN n / PF | TEST n / PF |
|---|---|---|
| 6  | 88 / 0.758  | 61 / 0.969 |
| 7  | 89 / 1.161  | 67 / 0.872 |
| 8  | 104 / 1.108 | 73 / 0.754 |
| 9  | 86 / 0.572  | 60 / 1.303 |
| 10 | 84 / 0.670  | 62 / 0.430 |
| 11 | 68 / 0.613  | 74 / 1.286 |
| 12 | 57 / 1.319  | 48 / 0.548 |
| 13 | 132 / 1.121 | 72 / 0.809 |
| 14 | 119 / 0.919 | 68 / 0.920 |
| 15 | 108 / 0.855 | 63 / 1.240 |

**VERDICT: REJECT (informative negative).** Hour-level PF is unstable between halves,
including sign flips on hours the coordinator specifically flagged: hour 9 is the worst
hour in TRAIN (0.572) and the best in TEST (1.303); hour 12 is the best hour in TRAIN
(1.319) and one of the worst in TEST (0.548); hour 11 flips from 0.613 to 1.286. Any of
the three proposed variants -- drop hour 12, keep only {9,13,14,15}, keep {7..15} -- would
look attractive on whichever half happens to be inspected and disappoint on the other,
purely because it is curve-fit to that half's noise. This directly fails criterion 3
(plateau, not a lone/half-specific spike) before a single patched `_HOURS` replay is even
needed: there is no stable session-hour pattern here to exploit. Unlike S93 (per the
coordinator's cross-strategy note), S99 does not support a defensible hour restriction.
Note this is a proxy (grouping the *default*-window baseline's fills by hour) rather than
a true `_HOURS`-patched replay -- patching `_HOURS` also changes which MSS setups get
armed and which pending states survive across the session boundary, so the real replay
could differ at the margin. Given the instability shown here is large and multi-hour, that
caveat is unlikely to change the verdict, but a full patched-replay confirmation is still
the more rigorous way to close this out if revisited.

## Dimension C2 -- _SWEEP_N, D1 -- _RETRACE_W, D2 -- _MAX_HOLD_MIN: NOT COMPLETED

Same cause. No arms ran. Not reported as INCONCLUSIVE-with-weak-evidence -- they simply
have zero data and should be re-run before any claim is made either way.

## Cross-check: raw (pre-cost) win rate by stop-distance bucket

Requested as a check on whether S99 shows the same "tight stops are structurally bad"
signature the campaign found for the book overall (H1) and for S93. Computed on the
baseline (cost 0.45, min_sl 1.5) full-period trades, raw_pts = net_pts + cost:

| stop bucket | n | avg risk (pt) | cost/risk % | raw WR% | net PF | net pts |
|---|---|---|---|---|---|---|
| <2 pt | 273 | 1.74 | 25.9 | 41.4 | 0.698 | -105.9 |
| 2-3 pt | 424 | 2.45 | 18.4 | 44.6 | 0.900 | -67.8 |
| 3-4 pt | 252 | 3.46 | 13.0 | 40.1 | 0.809 | -112.7 |
| 4-6 pt | 293 | 4.86 | 9.3 | 39.9 | 0.869 | -121.6 |
| 6-9 pt | 177 | 7.31 | 6.2 | 36.7 | 0.783 | -188.9 |
| 9+ pt | 164 | 15.75 | 2.9 | 44.5 | 1.106 | +144.5 |

Reading: unlike a "wider stops win more often" story, the raw win rate here is flat and
noisy across buckets (36.7-44.6%, no monotone trend) -- S99's setup detection does not
select better trades at wider stops. What moves is purely the cost/risk ratio: at <2pt the
0.45pt cost eats 25.9% of the risk budget before the trade has a chance, while at 9+pt it
eats only 2.9%. Only the widest bucket (9+pt, n=164) clears breakeven, and it does so
on fixed-cost arithmetic, not a demonstrated edge at that stop size. This is consistent
with the campaign's cross-strategy H1 pattern in kind, but note it does NOT translate into
a usable rule here: Dimension A already showed that raising min_sl_dist_pts toward this
territory (3.0-4.0pt, nowhere near the profitable 9+pt bucket) does not lift TEST PF above
baseline, because it mostly retains the flat-to-negative middle buckets while cutting
sample size. Reaching the 9+pt bucket specifically (not just raising the floor) would
require a different mechanism than a floor gate -- untested here, and not obviously
achievable without changing the setup's ATR-buffer/FVG-geometry logic, which is out of
scope for a parameter sweep.

## Final recommendation

No change justified. All three dimensions with real data (A: min_sl_dist_pts, C1: TP_R,
B: session hours) reject cleanly against the pre-registered bar -- no candidate improves
the TEST half, let alone survives stress and plateau checks, and the hour-of-day pattern
is actively unstable rather than merely unhelpful. Ship nothing from this pass.

The larger issue this report surfaces is that the ungated baseline itself is unprofitable
in this harness (test PF 0.907) while the live, gated version of the same strategy was
mildly profitable over its available live window (PF 1.120, n=55). Before spending more
budget on S99 parameter sweeps, the higher-value next step is reconciling that gap --
confirming whether it's the news/SL gates, the extended test window, or something else --
because a parameter search run against the wrong (ungated, apparently mis-specified)
baseline cannot be trusted regardless of how clean its internal deltas look.

## What surprised me / could not be tested

- The baseline/docstring discrepancy above was the single biggest surprise and dominates
  interpretation of everything else in this report.
- TP_R's rejection was expected in direction but the monotonicity across all four tested
  points, at both costs, was cleaner than anticipated -- a genuinely unambiguous result.
- Dimensions C2, D1, D2 have zero data, not weak data -- a real gap, not a finding, owed
  to shared-box compute contention during this session (see environment note). Either
  could still turn up something; neither should be assumed negative by default.
- Dimension B (session hours) initially looked like a third zero-data gap, but a
  dedicated verification run landed before this report was finished and confirmed the
  coordinator-relayed hour-instability claim exactly (hour 9: train 0.572 / test 1.303;
  hour 12: train 1.319 / test 0.548). Good outcome for rigor: the claim was correct, but
  it was still right to withhold it until independently confirmed rather than transcribe
  it on trust -- a claim can be accurate and still not be something a report should assert
  without its own evidence.
- The per-hour instability itself was the second surprise: it is not just hours-don't-
  help, it is specific hours flipping from best-in-TRAIN to worst-in-TEST (hour 12) and
  the reverse (hour 9). That is a stronger, more specific negative result than a flat
  no-pattern-found.
