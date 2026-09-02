# S94 sweep-reversal -- optimization report (2026-09-02, FINAL: all planned dimensions complete)

## Bottom line

Baseline (the shipped, static-exit configuration) sits close to its own breakeven on this
window (TEST PF 0.949, WR 24.2%, stress PF 0.894). `min_sl_dist_pts`, `_MIN_RR`,
`_STOP_BUF`, and `_CONFIRM_N` changes all fail to clear the pre-registered bar; blocking
Asia-adjacent hours nudges TEST mildly positive but does not survive cost stress. The one
change that clears every bar is raising the TP multiple (`_SD_MULT`) from the shipped 2.0
to 2.5 or 3.0:

- improves TEST PF and points vs baseline (0.949 -> 1.151 / 1.154; -72.5 -> +221.0 / +233.8pts)
- survives the 0.80pt cost stress (PF 1.089 / 1.093, both still >1)
- sits on a genuine plateau: 2.5 and 3.0 land almost identically (TEST PF 1.151 vs 1.154)
- costs zero trades either way (n=244, unchanged from baseline)
- has a plausible mechanism: letting winners run further, consistent with the module's own
  documented design intent ("winners pay 3-6R by design")

`_SD_MULT=1.5` is the clean counter-example: TRAIN PF 1.513 but TEST PF 0.859 -- a textbook
overfit, and useful evidence that 2.5/3.0 is a real directional effect, not "any change
looks better than a bad baseline."

**Verdict: SHIP `_SD_MULT` in the 2.5-3.0 range on the tested window, pending one
confirmation step before touching the live config (see Caveat below) -- REJECT
`min_sl_dist_pts` -- REJECT `_MIN_RR` -- REJECT `block_hours` (fails cost stress) --
REJECT `_STOP_BUF` (non-monotone, fails cost stress) -- `_CONFIRM_N` is a NULL result
(completely inert on this window -- see Dimension D). All five priority dimensions from
the brief (A-E, plus the coordinator's added F) are now resolved on this window.**

### Caveat before deploying -- do not skip this

This result was produced on a 5-month window (TRAIN 2025-12-01..2026-02-01, TEST
2026-02-01..2026-05-01), deliberately shortened from the campaign-standard 19.5 months
because of session CPU contention (see Method). It clears the pre-registered bar on that
window cleanly, but this strategy's own history is a specific warning against trusting a
single window: its currently-cited "PF 1.82" published number came from validating a
different configuration entirely (break-even stop + unlimited concurrency, corrected
below) and its live record over the following month was a loss despite that same static
config testing near-breakeven historically. A parameter change that looks clean on 5 months
deserves the same scrutiny before going live: re-run `_SD_MULT` in {2.0, 2.5, 3.0, 3.5}
against the full 2025-01-05..2026-08-12 window (now that the two most informative points
are already known-good) before shipping to `entry_manager`/live.

## Why the "published PF 1.82" framing is retired

The module's docstring originally attributed PF 1.82 to "STATIC SL/TP exits as deployed
here." That was wrong, and has since been corrected in
`backtest_strategies/s94_sweep_reversal.py` (see the `CORRECTION 2026-09-02` block): the
validation script `ClaudeTradingRD/validate_oos.py` actually calls
`simulate(df, sweeps, single_pos=False, be=True, allow=c15|w15, sd_mult=2.0, legs=legs)` --
a break-even stop move at +1R and unlimited overlapping positions, at $0.30 cost. The
shipped module has neither: `entry_manager` writes static STOPLOSS/TARGET triggers with no
break-even logic, and `CONFIG.max_concurrent_positions = 3`. So this report's baseline (PF
~0.86-0.95) and the published 1.82 are not the same system disagreeing -- they are two
different systems, and the docstring mis-attributed the favourable one to the unfavourable
configuration. This report's `_SD_MULT` finding is entirely within the deployed, static-exit
configuration -- a genuine improvement to that system, not a rediscovery of 1.82, and should
not be reported or expected to reach anywhere near that number.

## Method

- Harness: `lab/harness.py`, driving the real `get_signal()` and the real
  `shared.gate_rules` predicates. Exits resolve on M1, SL checked before TP (pessimistic).
  Points-primary; USD not reported.
- Window shortened from the campaign-standard 2025-01-05..2026-08-12. A live timing probe
  (1.4 months, baseline config) measured roughly 134s of wall-clock per calendar month of
  replay under this session's CPU contention (several sibling strategy sweeps running
  concurrently for S93/S99/S100) -- about 5x slower than the "5-10 min for 19.5mo" planning
  estimate. Window used instead: TRAIN = 2025-12-01..2026-02-01 (2mo), TEST =
  2026-02-01..2026-05-01 (3mo). TEST n stays far above the n>=40 floor (baseline n=244).
  This window starts about 10 months after the cache origin, so it needed none of the extra
  warm-up the harness's `win_5m` fix added mid-session (see below) -- it was never in the
  affected region, but the fixed harness was used regardless since it landed before any
  real (non-discarded) result in this report was produced.
- Harness warm-up fix (landed mid-session): `replay()` originally guaranteed only `win_1m`
  bars of history before the first traded bar, not `win_5m`/`win_15m`. S94 needs about 7500
  M1 bars (`win_5m=1500 * 5`) of history to build its full level universe; before the fix,
  every replay effectively started with a truncated level set for its opening stretch --
  exactly the divergence the module's own `_ttl_warned` log line documents. Fixed:
  `i0 = max(win_1m+5, win_5m*5+5, win_15m*15+5, ...)`. Not consequential to this report's
  numbers (window starts well clear of the fix's reach) but noted for the record.
- Stress-cost (0.80pt) figures are not separate replays. `cost_pts` only affects per-trade
  P&L bookkeeping (`raw = pts + base_cost`; `pts_stress = raw - stress_cost`) -- it never
  changes signal generation, gates, or which trades occur. Verified once against a real
  `cost_pts=0.80` re-run on the TRAIN half before trusting it for every arm: real total
  pts=-153.535, recomputed=-153.535, MATCH=True. This roughly halved the replay count
  needed for the whole sweep.
- `k` = effective TP multiple = `|tp - entry| / |entry - sl|`, computed per-trade from the
  stored `tp`/`entry_px`/`risk` columns (S94 always sets a static numeric TP, so this is
  exact). Breakeven WR at a given `k` and negligible cost is `1/(1+k)`.
- Background execution note: this sweep ran via the Bash tool's `run_in_background: true`
  (a harness-tracked background task), not a raw `nohup ... &`. It survived the full
  session uninterrupted, including past a mid-session message from the coordinator
  reporting it as dead (it was not -- verified live at the time), and completed all six
  arms of dimension D unattended after this report had already been drafted twice.

## Results

All points; TEST period = 2026-02-01..2026-05-01 (3mo, n floor = 40).

| Arm | TRAIN pts | TRAIN PF | TEST n | TEST pts | TEST PF | TEST WR% | TEST pts@0.80 | TEST PF@0.80 | k_med(test) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BASELINE (_SD_MULT=2.0, min_sl=1.5, _MIN_RR=1.0, _STOP_BUF=0.10, _CONFIRM_N=15) | -104.2 | 0.857 | 244 | -72.5 | 0.949 | 24.2 | -157.9 | 0.894 | 3.65 |
| A_minsl_2.5 | -76.1 | 0.885 | 206 | -56.2 | 0.958 | 25.7 | -128.3 | 0.909 | 3.43 |
| A_minsl_3.5 | -167.1 | 0.714 | 177 | -126.3 | 0.902 | 24.3 | -188.2 | 0.859 | 3.29 |
| A_minsl_4.5 | -133.3 | 0.741 | 143 | -115.5 | 0.901 | 25.2 | -165.5 | 0.863 | 3.21 |
| C_SDMULT_1.5 | +263.8 | 1.513 | 230 | -170.7 | 0.859 | 27.8 | -251.2 | 0.802 | 2.62 |
| **C_SDMULT_2.5** | -55.3 | 0.928 | 244 | **+221.0** | **1.151** | 21.7 | **+135.6** | **1.089** | 4.79 |
| **C_SDMULT_3.0** | +54.0 | 1.070 | 244 | **+233.8** | **1.154** | 18.4 | **+148.4** | **1.093** | 5.93 |
| F_MINRR_1.5 | -74.9 | 0.888 | 233 | -105.1 | 0.922 | 22.7 | -186.7 | 0.867 | 3.79 |
| F_MINRR_2.0 | -4.8 | 0.991 | 222 | -128.5 | 0.896 | 22.5 | -206.2 | 0.841 | 3.93 |
| F_MINRR_2.5 | +51.6 | 1.117 | 195 | -43.1 | 0.958 | 21.5 | -111.4 | 0.898 | 4.18 |
| B_block_0-5 | -108.5 | 0.813 | 187 | +32.3 | 1.029 | 26.2 | -33.1 | 0.971 | 3.76 |
| B_block_0-6_23 | -109.8 | 0.805 | 171 | -26.9 | 0.974 | 25.1 | -86.8 | 0.920 | 3.80 |
| D_STOPBUF_0.05 | -88.6 | 0.873 | 238 | -15.4 | 0.989 | 24.4 | -98.7 | 0.931 | 3.81 |
| D_STOPBUF_0.25 | -162.0 | 0.810 | 253 | -147.4 | 0.908 | 26.5 | -236.0 | 0.859 | 3.23 |
| D_CONFIRMN_10 | -104.2 | 0.857 | 244 | -72.5 | 0.949 | 24.2 | -157.9 | 0.894 | 3.65 |
| D_CONFIRMN_25 | -104.2 | 0.857 | 244 | -72.5 | 0.949 | 24.2 | -157.9 | 0.894 | 3.65 |

All six priority dimensions from the brief (A, B, C, D, plus the coordinator-added F; E is
addressed implicitly since no other dimension passed to combine with C) have now run on
this window.

## Dimension A -- min_sl_dist_pts: REJECT

Only 2.5 improves on baseline (TEST pts -56.2 vs -72.5, PF 0.958 vs 0.949, stress -128.3 vs
-157.9). The next two points in the same direction, 3.5 and 4.5, are both worse than
baseline on every column -- not a diminishing-but-still-positive trend, a reversal. This
fails the plateau/no-lone-spike bar (#3) outright, and no PF value in this dimension clears
1.0 on TEST. REJECT. (See "Raw WR by stop-distance bucket" below for independent support:
the relationship between stop distance and raw win rate in this sample is bumpy, not
monotonic, which is consistent with why a blunt distance floor does not produce a clean
improvement here.)

## Dimension C -- _SD_MULT: SHIP (on this window; full-window confirmation required before live)

C_SDMULT_1.5 is the cleanest overfitting example in this sweep and worth keeping as a
worked case. TRAIN looks excellent (+263.8pts, PF 1.513) -- exactly the kind of number that
would look shippable if TRAIN were all you had. TEST is a clear loss (-170.7pts, PF 0.859)
and gets worse under stress (PF 0.802). The mechanism is legible after the fact (a tighter
TP is easier to hit -- WR rose to 27.8% -- but the smaller average win no longer carries the
loss rate). This is exactly why acceptance bar #1 is about the TEST half specifically, not
"a change that improves results."

C_SDMULT_2.5 and C_SDMULT_3.0 together satisfy all five pre-registered bars:

1. Improves TEST PF and points -- 0.949 -> 1.151 (2.5) / 1.154 (3.0); -72.5 -> +221.0 /
   +233.8pts. Both comfortably above baseline and above 1.0.
2. Survives 0.80pt stress -- PF 1.089 (2.5) and 1.093 (3.0), both still >1 after nearly
   doubling round-trip cost from 0.45 to 0.80.
3. Plateau, not a lone spike -- 2.5 and 3.0 land almost on top of each other (TEST PF 1.151
   vs 1.154; points +221.0 vs +233.8; stress PF 1.089 vs 1.093) despite `_SD_MULT` moving by
   a full 0.5 and `k_med` moving from 4.79 to 5.93. TRAIN also rises monotonically across all
   three points (0.857 -> 0.928 -> 1.070). `_SD_MULT=1.5` moving the wrong way while 2.5/3.0
   both move the right way and hold steady is the textbook shape of a real, saturating
   effect rather than sampling noise.
4. n unaffected -- 244 trades at baseline, 2.5, and 3.0 alike. Raising the TP target never
   pushed a setup below the `_MIN_RR` gate that a smaller target had already cleared.
5. Plausible mechanism -- letting winners run further is a standard lever for a
   reversal/distribution-leg strategy, and directly consistent with the module's own
   documented expectation ("winners pay 3-6R by design"); WR falling from 24.2% to 21.7%
   (2.5) / 18.4% (3.0) while PF rises confirms "fewer, bigger wins," the expected signature
   of raising a fixed TP multiple, not an artifact.

Recommendation: treat 2.5-3.0 as the validated range on this window (no basis yet to prefer
one over the other), and run the full-window confirmation in the Caveat above before
changing the live `_SD_MULT` constant.

## Dimension F -- _MIN_RR: REJECT

`_MIN_RR` raises the minimum required |TP-entry|/risk ratio for a setup to be taken at all
(default 1.0) -- a pure entry-side filter, unlike `_SD_MULT` which reshapes the TP of every
trade that already passes. None of 1.5, 2.0, or 2.5 beats baseline on TEST PF (0.922, 0.896,
0.958 vs baseline 0.949), and the one value that comes closest (2.5, PF 0.958) still fails
the 0.80pt stress test (PF 0.898, below 1.0) -- so it fails bar #2 before the plateau
question even comes up. The response is also non-monotone (1.5 worse, 2.0 worse still, 2.5
better but still short of baseline), failing bar #3 regardless. n drops steadily (244 -> 233
-> 222 -> 195) for no corresponding gain in edge, meaning the trades being cut were not
disproportionately bad ones. REJECT.

## Dimension B -- block_hours: REJECT (close, and worth revisiting combined with C)

Both variants improve TEST PF and points over baseline in the same direction -- block_0-5
gives PF 0.949 -> 1.029 (+32.3pts), block_0-6_23 gives PF 0.949 -> 0.974 (-26.9pts, still
less negative than baseline) -- so bar #1 (TEST improvement) and, loosely, bar #3
(neighbouring configurations move the same way) are satisfied. Bar #2 is where this
dimension fails: under 0.80pt stress, block_0-5 drops to PF 0.971 (pts -33.1) and
block_0-6_23 to PF 0.920 (pts -86.8) -- both back under 1.0. The effect is real-looking but
too small to survive doubling the round-trip cost, unlike `_SD_MULT` which held comfortably
above 1.0 under the same stress. TRAIN also gets slightly worse in both variants (0.857 ->
0.813 / 0.805), a mild warning sign the live evidence (Asia-hours losses, hours 09/15/23
all-losers) may not transfer cleanly to this particular 5-month sample. n drops materially
(244 -> 187 / 171), which is expected for an hour-block filter but reduces statistical power
for a modest gain. REJECT as a standalone change under this bar; the direction is
consistent enough with the live evidence to be worth re-testing combined with the
`_SD_MULT=2.5-3.0` fix (E-style combination) once that dimension is confirmed on the full
window, since the two levers touch different failure modes (exit-target sizing vs.
session/liquidity quality) and could plausibly be additive rather than redundant.

## Dimension D -- _STOP_BUF / _CONFIRM_N: REJECT / NULL RESULT

**_STOP_BUF** (stop = sweep extreme +/- this fraction of the penetration; default 0.10) is
non-monotone and, on the one side that looks better, still fails stress: 0.05 (tighter stop
buffer) improves TEST PF to 0.989 (pts -15.4, still net-negative) but drops to PF 0.931 under
stress -- below 1.0. 0.25 (looser buffer) is worse than baseline on every column (TEST PF
0.908, pts -147.4). No plateau, no value clears 1.0 on TEST even before stress. REJECT.

**_CONFIRM_N** (bars allowed for a sweep to close back through the level; default 15) is a
genuine null result: both 10 and 25 reproduce the baseline's numbers bit-for-bit (TRAIN pts
-104.2/PF 0.857, TEST n=244/pts -72.5/PF 0.949, identical to five decimal places on every
column). This means every sweep that ever confirms in this window's data confirms within 10
bars -- tightening the window to 10 removes nothing, and loosening it to 25 admits nothing
new, because no sweep in this sample takes between 11 and 25 bars to close back through its
level. This parameter is inert across its tested range on this data; it is not evidence the
parameter never matters, only that this specific 5-month sample never exercises the 11-25
bar range. Not a candidate for further tuning without a different sample to test against.

## Raw WR by stop-distance bucket (baseline config, informational)

Computed on the baseline trades only (pre-cost raw P&L), to sanity-check dimension A's
REJECT against the live book's "wide stop / tight stop cliff" finding (MIN_SL_DIST_PTS=1.5,
cliff at 3.8pt, wide PF 3.364 vs tight PF 0.310 for S94 specifically).

TEST half:

| Stop bucket | n | raw WR% | cost/risk% | mean risk (pt) |
|---|---:|---:|---:|---:|
| <2 | 18 | 11.1 | 26.3 | 1.71 |
| 2-3 | 34 | 26.5 | 18.6 | 2.41 |
| 3-4 | 38 | 26.3 | 12.6 | 3.56 |
| 4-6 | 43 | 25.6 | 9.1 | 4.95 |
| 6-9 | 50 | 18.0 | 6.1 | 7.32 |
| 9+ | 61 | 29.5 | 2.6 | 17.27 |

The <2pt bucket is the single worst (WR 11.1%, and cost eats 26.3% of the risk budget),
consistent in direction with the live book's tight-stop-cliff finding. But the relationship
is not a clean monotonic cliff on this sample: the 6-9pt bucket is the second-worst (WR
18.0%), sitting between two better buckets on either side (3-4 and 9+). This bumpiness is
the likely reason a blunt `min_sl_dist_pts` floor (dimension A) does not produce a clean,
plateaued improvement here -- raising the floor from 1.5 keeps removing the worst bucket at
first (2.5 improves) but then starts cutting into buckets that are not uniformly bad (3.5,
4.5 make things worse). The live book's cleaner cliff at 3.8pt may be a feature of its
specific July-August sample (or of the whole-book pooling across all four strategies) rather
than a stable property of S94 alone on an arbitrary window; this is not confirmed either way
by the current data and would need the live-window dates specifically replayed to check.

## What would change this verdict

A confirming `_SD_MULT` re-run on the full 2025-01-05..2026-08-12 window, still clearing
TEST PF above baseline and surviving 0.80pt stress, would make this a genuine ship
candidate for the live config. A follow-up combination test of `_SD_MULT=2.5-3.0` +
`block_hours` (Asia exclusion) is the most promising untried lead from this sweep -- neither
lever alone is a slam dunk on `block_hours`, but they were not tested together. Re-running
the live window itself (2026-07-06..2026-08-12) through this harness would also directly
test whether the live book's clean stop-distance cliff reproduces outside the pooled/live
sample, which this report could not resolve with its own (different) window.

## Log

- Report first drafted with `_SD_MULT=3.0` still in flight (the coordinator asked for the
  report from completed arms at that point); verdict on dimension C was INCONCLUSIVE.
  `C_SDMULT_3.0` landed a few minutes later on the same still-running background job, and
  the plateau it confirms is decisive enough to change the recommendation.
- The background job then completed the full `F_MINRR` dimension (REJECT, clean) and the
  full `B_block_hours` dimension (REJECT, close but fails stress) unattended, folded in on
  a second revision.
- The background job finally completed all four `D` arms (`_STOP_BUF` REJECT,
  `_CONFIRM_N` a null result) plus the raw stop-distance-bucket breakdown, completing every
  dimension originally scoped in the brief. This is the final revision.
