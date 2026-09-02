# S93 FVG Scalp -- optimization campaign report (2026-09-02)

Strategy: backtest_strategies/s93_fvg_scalp.py (KRONOS_S93_FVG_SCALP). Harness:
lab/harness.py. Data: 19.5 cached months of XAU_USD (2025-01-05 .. 2026-08-12).
Train = 2025-01-05..2026-02-01, Test = 2026-02-01..2026-08-12, split on entry_time.
Exits resolve on M1 with SL checked before TP (pessimistic). Points-primary.

Every table below is labelled by (a) which module config it ran under -- "6H" for the
original _HOURS=(7,8,9,12,13,14) or "2H" for the shipped _HOURS=(13,14) -- and (b)
whether the run is CLEAN or CONTAMINATED, per the incident log immediately below. Do not
compare a 6H number to a 2H number as if they were the same reference; they arent.

## Contamination log -- two incidents, both on the record

This campaign ran while the strategy file and the harness were both being edited
concurrently by another process (the coordinators own parallel confirmation work).
Two distinct bugs corrupted parts of the first two sweep attempts. Neither was caused by
this agents code; both are recorded here per instruction, with exact blast radius.

INCIDENT 1 -- mid-sweep _HOURS edit to the strategy file. The coordinator shipped
_HOURS: (7,8,9,12,13,14) -> (13,14) to backtest_strategies/s93_fvg_scalp.py at
2026-09-01 22:52 UTC, while my first sweep (lab/_sweep_s93_full.py) was mid-run. Because
harness.replay() does importlib.reload(mod) fresh on every call, any arm that ran
AFTER the edit and did NOT explicitly patch={"_HOURS": ...} silently picked up
the new default. Confirmed boundary: everything through C_MINFVGATR_0.6 ran under the old
6H default (verified by train-n matching the coordinators own independently-run numbers);
D_veto_off and D_gapcap_0 ran under the new 2H default (train n=369/378, also
independently confirmed). All six HOURS_* ladder arms explicitly patched _HOURS
themselves, so they are immune to this edit regardless of when they ran. The affected
process was killed before it reached its stress-test phase.

INCIDENT 2 -- Cfg.env never restored (lab/harness.py, now fixed). replay() wrote
cfg.env into os.environ but never restored it, so an env var set by one arm leaked into
every later arm IN THE SAME PROCESS. This was caught via a hard invariant violation:
raising min_sl_dist_pts can only ever remove trades, never add them, but a recovery-sweep
overlap arm showed a HIGHER train-n than its own looser-filter baseline. Root cause: an
earlier D_veto_off arm (S93_SOFT_VETO=off) leaked forward and silently disabled the
SOFT veto for every subsequent arm in that process -- more FVGs armed, so trade count could
rise even as the stop floor rose. The coordinator has since fixed this in harness.py
(env snapshotted and restored in a finally block).

Auditing my own two contaminated scripts against this mechanism turned up a SECOND,
PREVIOUSLY-UNFLAGGED CASUALTY: in both lab/_sweep_s93_full.py and
lab/_sweep_s93_recover.py, the D_veto_off arm ran immediately BEFORE every
D_gapcap_* arm in the same process. That means every D_gapcap_* number I collected --
in both scripts, at both cost levels -- has S93_SOFT_VETO leaked to off on top of
whatever gap-cap value was intended. THE GAP_CAP_ATR SUB-DIMENSION IS THEREFORE NOT
VALIDLY TESTED BY ANYTHING IN THIS CAMPAIGN -- see Dimension D below. This is distinct
from, and in addition to, the min_sl/overlap contamination the coordinator already caught.

What's clean: the _HOURS ladder (phase 1, ran before any env-setting arm -- this is
the evidence behind the shipped change), A (min_sl) and C (TP_R, MIN_FVG_ATR) at
cost 0.45 (phase 1, also pre-env), D_veto_off at both costs (it is the FIRST
env-setting call in both processes, so nothing leaked into it), and everything in the
final recovery pass lab/_sweep_s93_redo.py (rewritten to explicitly pin _HOURS,
_MIN_FVG_ATR, _TP_R, _BUF_ATR on every single call and to never touch Cfg.env at
all -- immune to both incidents by construction, and cross-checked with two live
sanity-assertions against the known-good baseline numbers plus a running monotonicity
check on every min_sl sweep).

---

## Baseline -- 6H module, CLEAN (phase 1, ran before any edit or env call)

| cost | half | n | pts | PF | WR% | maxDD |
|---|---|---|---|---|---|---|
| 0.45 | train | 629 | +8.4 | 1.006 | 42.9 | -198.4 |
| 0.45 | test | 361 | -97.9 | 0.931 | 41.6 | -221.8 |
| 0.80 stress | train | 629 | -211.8 | 0.869 | 42.6 | -345.4 |
| 0.80 stress | test | 361 | -224.2 | 0.851 | 41.6 | -254.7 |

The 6H config as originally validated is roughly breakeven on train and mildly negative on
test at 0.45, and decisively negative on both halves at the realistic 0.80 cost. This is the
reference every 6H-labelled table below compares against.

---

## Dimension B -- killzone hours (HIGH priority; this is the change already shipped)

6H module, phase-1, CLEAN -- every arm explicitly patches _HOURS, immune to Incident 1.

| _HOURS | train n/pts/PF | test n/pts/PF/WR%/maxDD |
|---|---|---|
| (7,8,9,12,13,14) baseline | 629/+8.4/1.006 | 361/-97.9/0.931/41.6/-221.8 |
| block_hours=(12,) gate-level | 575/-2.7/0.998 | 323/-56.6/0.956/42.4/-205.1 |
| _HOURS=(7,8,9,13,14) clean drop | 546/-22.2/0.983 | 309/-35.7/0.971/42.4/-171.0 |
| _HOURS=(8,9,13,14) | 429/+28.2/1.027 | 248/+140.3/1.150/45.6/-113.1 |
| _HOURS=(12,13,14) | 350/+125.1/1.147 | 184/+159.7/1.215/47.3/-86.0 |
| _HOURS=(9,13,14) | 329/+77.4/1.092 | 182/+227.4/1.323/49.5/-59.8 |
| _HOURS=(13,14) -- SHIPPED | 267/+94.6/1.139 | 132/+221.8/1.408/51.5/-43.3 |

Stress (0.80), CLEAN (confirmed independently twice -- recovery run and redo run agree bit
for bit) for baseline and the shipped arm:

| _HOURS | test n/pts/PF |
|---|---|
| baseline (6H) | 361/-224.2/0.851 |
| (13,14) SHIPPED | 132/+175.6/1.310 |

VERDICT: SHIP (already shipped 2026-09-01 22:52 UTC). Every restricted-hour arm beats
the 6H baseline on both halves at 0.45; the ladder degrades smoothly as morning hours are
added back in (a plateau, not a lone spike -- criterion 3 clearly satisfied); (13,14) also
clears the 0.80 stress on both points and PF (criterion 2); test n=132 clears the n>=60
floor (criterion 4) though narrowly by trade-count standards -- this is now a low-frequency
setup. Selection is train-justified (my own train-side ranking of individual hours from the
ladder above agrees with the coordinators: 13 > 14 > 12 > 7 > 9 > 8), not fitted to the
test window, which is the strongest defense against overfitting here. Independently
corroborated by the live broker record (S93 as traded: PF 0.82; its 13-14 subset: PF 1.22)
per the coordinator. CAVEAT: not yet exercised by live trading -- hours 13-14 UTC had
not recurred as of this report; treat as validated-offline-and-shipped, not
proven-in-production.

---

## Dimension A -- min_sl_dist_pts

### A1. Within the ORIGINAL 6H regime (now superseded in production, see A2)

6H module. Screen (0.45pt) CLEAN -- phase 1, pre-edit. Stress (0.80pt) CLEAN -- redo run,
fully pinned.

| min_sl | train n/pts/PF @0.45 | test n/pts/PF @0.45 | test n/pts/PF @0.80 stress |
|---|---|---|---|
| 1.5 baseline | 629/+8.4/1.006 | 361/-97.9/0.931 | 361/-224.2/0.851 |
| 2.0 | 515/+50.0/1.037 | 351/-88.4/0.937 | 351/-211.3/0.857 |
| 2.5 | 403/+69.0/1.058 | 329/-97.9/0.929 | not stressed, failed the 0.45 screen |
| 3.0 | 315/+111.6/1.11 | 304/-73.2/0.945 | 304/-179.6/0.871 |
| 3.5 | 248/+138.0/1.159 | 268/-38.0/0.969 | 268/-131.8/0.898 |
| 4.0 | 196/+163.8/1.222 | 232/-62.6/0.946 | 232/-143.8/0.882 |

VERDICT (6H context): mechanically SHIP, but the win is relative, not absolute.
2.0/3.0/3.5/4.0 all improve TEST points and PF vs baseline at both cost levels
(criterion 1 and 2 pass); 2.5 uniquely fails the 0.45 screen (pts tie, PF slightly down),
which is itself useful -- it shows the improvement isnt a monotone certainty at every
step, though 3.0-3.5-4.0 form a clean plateau around a peak at 3.5 (criterion 3: satisfied,
not a lone spike); n stays 232-351 on test (criterion 4 easily cleared); mechanism is
exactly the live H1 hypothesis -- tight stops lose disproportionately on this instrument
(criterion 5). BUT TEST POINTS REMAIN NEGATIVE AT EVERY MIN_SL VALUE TESTED, AT BOTH
COST LEVELS -- this reduces the bleed, it does not make the 6H config profitable.

### A2. Overlap probe -- min_sl swept WITHIN the SHIPPED (13,14) hours

This is the direct test of "do the hours and stop-floor effects overlap." 2H module,
CLEAN (redo run -- fully pinned to _HOURS=(13,14), _MIN_FVG_ATR=0.3, _TP_R=1.5,
_BUF_ATR=0.2 on every call; the recovery runs version of this probe was
env-leak-contaminated and is discarded, not shown).

| min_sl | train n/pts/PF | test n/pts/PF/WR%/maxDD |
|---|---|---|
| 1.5 shipped | 267/+94.6/1.139 | 132/+221.8/1.408/51.5/-43.3 |
| 2.0 | 237/+93.2/1.144 | 132/+221.8/1.408/51.5/-43.3 |
| 2.5 | 210/+74.7/1.121 | 131/+224.4/1.415/51.9/-43.3 |
| 3.0 | 181/+93.3/1.166 | 128/+227.1/1.424/52.3/-43.3 |

Monotonicity holds cleanly this time (train n falls 267->237->210->181 as the floor rises,
exactly as it must). Test PF creeps from 1.408 to 1.424 while test n falls from 132 to 128
-- a ~1% PF wobble on a ~3% trade-count change is noise, not a signal, and DD does not move
at all (-43.3 flat across every value, meaning the floor essentially never binds inside
these two hours).

VERDICT: NEUTRAL / REJECT the change. Agreeing with the coordinators read: the
stop-distance floor is neutral once the London/lunch hours are already excluded. Mechanism:
FVG-derived stops in the NY 13:00-15:00 window are apparently already comfortably wide in
almost every case, so a 1.5-3.0pt floor rarely rejects anything (test n barely moves).
THIS RESOLVES THE "DO HOURS AND STOP-FLOOR OVERLAP" QUESTION: yes, almost entirely -- the
stop-distance effect (real under A1) is CARRIED BY the same London-morning hours that the
hours change already removes. Once you fix the hours, there is essentially nothing left for
the stop floor to fix. Net practical recommendation: leave min_sl_dist_pts at its
current value; the A1 finding is real but superseded by the shipped hours change.

---

## Dimension C -- _TP_R

6H module, phase-1, CLEAN.

| _TP_R | train n/pts/PF | test n/pts/PF/WR% |
|---|---|---|
| 1.0 | 634/+20.3/1.017 | 376/-200.0/0.846/50.0 |
| 1.5 baseline | 629/+8.4/1.006 | 361/-97.9/0.931/41.6 |
| 2.0 | 618/-29.4/0.982 | 355/-217.6/0.861/34.1 |
| 2.5 | 602/-155.7/0.911 | 354/-314.7/0.811/28.8 |

VERDICT: REJECT. Every alternative is worse than baseline on TEST points and PF, in
both directions from 1.5R. No plateau to even evaluate -- 1.5R is a local optimum on both
halves of the data I have. Not stress-tested at 0.80 since criterion 1 already fails
outright; no basis to spend a replay on it.

---

## Dimension C -- _MIN_FVG_ATR

6H module. 0.45 screen CLEAN (phase 1, pre-edit). 0.2's 0.80 stress CLEAN (redo, fully
pinned). 0.6 was never stress-tested (see Limitations).

| _MIN_FVG_ATR | train n/pts/PF @0.45 | test n/pts/PF @0.45 | test n/pts/PF @0.80 stress |
|---|---|---|---|
| 0.2 | 691/+9.1/1.006 | 458/-76.7/0.952 | 458/-237.0/0.861 |
| 0.3 baseline | 629/+8.4/1.006 | 361/-97.9/0.931 | 361/-224.2/0.851 |
| 0.45 | 461/+89.4/1.076 | 247/-121.8/0.896 | worse at 0.45 already, not stressed |
| 0.6 | 127/+30.7/1.073 | 70/+131.6/1.353 WR51.4 | not tested, see Limitations |

VERDICT 0.2: REJECT. Passes the 0.45 screen (PF 0.952 > 0.931, pts -76.7 > -97.9) but
FAILS THE 0.80 STRESS ON POINTS (-237.0 vs baselines -224.2 -- worse, even though PF
ticks up to 0.861 > 0.851). Criterion 2 requires improving on both legs at stress; this
doesnt. Not robust to cost.

VERDICT 0.45: REJECT. Worse than baseline on both PF and points at 0.45; not
stress-tested since it already fails criterion 1.

VERDICT 0.6: INCONCLUSIVE, not SHIP. The single most eye-catching number in this whole
campaign -- test PF 1.353, +131.6 pts, WR 51.4%, n=70 -- but it fails criterion 3 as tested:
its only measured neighbor (0.45) is clearly WORSE than baseline, so the shape across
{0.3, 0.45, 0.6} is baseline -> worse -> much-better, not a plateau. That is exactly the
"lone spike" pattern the pre-registered bar exists to catch. n=70 clears the floor but only
barely (a swing of a handful of trades would drop it below 60). It was never stress-tested
at 0.80. I am not willing to call this a plateau on one flanking point in the wrong
direction -- it needs denser neighbors (0.5, 0.55, 0.65, 0.7) and a 0.80 run before it can
be judged either way.

---

## Dimension D -- SOFT_VETO / GAP_CAP_ATR

2H module (both sub-dimensions were run pinned/observed under the shipped _HOURS=(13,14)
default, reference = NEWBASE_13_14 above: test 132/+221.8/1.408 @0.45,
132/+175.6/1.310 @0.80).

### D1. SOFT_VETO on/off -- CLEAN

D_veto_off is the FIRST env-setting call in both scripts it appeared in, so nothing
leaked into it (Incident 2 only propagates forward from an env-setting arm, never backward).

| SOFT_VETO | test n/pts/PF/WR%/maxDD @0.45 | test n/pts/PF/WR%/maxDD @0.80 |
|---|---|---|
| ON default, shipped | 132/+221.8/1.408/51.5/-43.3 | 132/+175.6/1.310/51.5/-45.4 |
| OFF | 181/+199.3/1.244/46.4/-77.8 | 181/+135.9/1.160/46.4/-83.0 |

VERDICT: SHIP -- keep ON (no change). Turning the veto off adds trades (132->181) but
every one of points, PF, WR, and drawdown gets worse at both cost levels. The filter earns
its place under the new 2H hours exactly as it did under the original opt15 validation --
fewer, cleaner trades. This is a clean, trustworthy result.

### D2. GAP_CAP_ATR -- NOT VALIDLY TESTED (contaminated by Incident 2, distinct from A2)

Every D_gapcap_* number collected in this campaign -- phase 1's D_gapcap_0 and the
recovery runs D_gapcap_{0,1.0,2.5} at both costs -- ran AFTER D_veto_off in the
same process, so S93_SOFT_VETO=off was silently leaked into all of them on top of
whatever gap-cap value was actually intended. The numbers exist (e.g. gapcap=1.0 test
PF 0.82 @0.45, gapcap=2.5 test PF 1.19 @0.45) but they measure "gap cap X WITH THE VETO
ALSO FORCED OFF", not "gap cap X with the veto at its normal default" -- a different,
unrequested experiment. I am not willing to launder a confounded result into a verdict.

VERDICT: INCONCLUSIVE -- requires a clean rerun. Recommend re-running the four
GAP_CAP_ATR arms {0, 1.0, 1.5 (=baseline, already have via NEWBASE), 2.5} either in
separate processes, or -- now that lab/harness.py restores os.environ -- in one process
in any order, or defensively with env={"S93_SOFT_VETO": "on", "S93_GAP_CAP_ATR": g}
explicit on every call regardless. No conclusion is drawn here; the current default
(1.5) is left in place for lack of evidence to change it, not because it was shown to be
optimal.

---

## Final recommended config

NO CHANGE BEYOND WHAT IS ALREADY SHIPPED. The only dimension that cleared the full
pre-registered bar is the hours narrowing to _HOURS=(13,14), and that is already live in
backtest_strategies/s93_fvg_scalp.py as of 2026-09-01 22:52 UTC (not yet exercised by live
trading at the time of this report). Every other lever tested either:
- rejects outright (TP_R, MIN_FVG_ATR=0.2/0.45, SOFT_VETO=off), or
- shows a real effect that is superseded by the hours change (min_sl_dist_pts -- helps
  inside the old 6H regime, measurably neutral inside the shipped 2H regime), or
- could not be validly tested this campaign (GAP_CAP_ATR, confounded by the env leak;
  MIN_FVG_ATR=0.6, an untested lone-spike candidate).

Current full config to keep: _HOURS=(13,14), min_sl_dist_pts=1.5 (harness/live default),
_TP_R=1.5, _MIN_FVG_ATR=0.3, S93_SOFT_VETO=on, S93_GAP_CAP_ATR=1.5. There is no
"dimension E" combination to build -- nothing beyond the hours change survived the bar, so
combining winners means combining exactly one winner, which is already in production.

## What surprised me / what I could not test

- The stop-floor and hours effects overlap almost completely. I expected them to be
  roughly additive (H1 in CAMPAIGN.md reads as a strategy-agnostic, session-agnostic
  effect). Instead, once the London-morning hours are removed, the stop-floor lever goes
  essentially inert (DD literally does not move across min_sl 1.5-3.0 inside (13,14)). That
  the live H1 evidence and the S93-specific hours evidence may be THE SAME UNDERLYING
  EFFECT MEASURED TWO WAYS, not two independent effects, seems like the single most
  important structural finding here for whoever looks at min_sl next on S94/S99/S100 --
  worth checking on those before assuming H1 is additive on top of any hours cleanup done
  there too.
- MIN_FVG_ATR=0.6 (test PF 1.353, n=70) is unresolved. It is exactly the kind of
  result the plateau criterion is designed to withhold judgment on, and I did not have
  budget left (post-contamination cleanup) to run the denser neighbor sweep plus 0.80
  stress it needs. Flagging for a future pass rather than guessing.
- GAP_CAP_ATR is entirely unresolved, not by data quality but by a process bug (Incident
  2) that I did not catch until after both attempts to measure it had already run. The
  clean-arm-ordering invariant (a stricter filter must never increase n) is what caught the
  min_sl version of this bug; I did not think to apply the same check to the gap-cap arms
  until reconstructing the contamination timeline for this report, which is a process gap
  on my part worth naming rather than smoothing over.
- block_hours=(12,) and the "drop 12 cleanly" _HOURS=(7,8,9,13,14) arm were only run at
  0.45, not stressed at 0.80 -- both were clearly dominated by the stronger (9,13,14) and
  (13,14) results early on and I prioritized budget toward the winning branch of that
  ladder rather than fully closing out the dominated ones.
- I did not re-test TP_R or MIN_FVG_ATR WITHIN the shipped (13,14) hours the way I did
  for min_sl (the A2 overlap probe). It is plausible the FVG-size or TP-multiple optimum
  shifts once the London hours are gone, the same way the stop floors effect did. Untested.
