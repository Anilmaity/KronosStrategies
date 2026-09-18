# s14 minimum-stop grid — result (2026-09-18)

Pre-registration: `lab/campaigns/s14_minstop.py` (written before any arm ran). Baseline: the
Stage-1 arm (floor 1.5 = live `MIN_SL_DIST_PTS`). Harness arms in `results/s14_minstop/`
(`SCORE.csv` = bars 1–5 by `campaign_score`), live-trigger resolution of every arm in
`results/s5exit/`, the joint table in `results/s14_minstop/QUOTE_S5_SUMMARY.csv`. The gate
rejects the signal the way `entry_manager` does (a later signal can take the slot) — it is
not a subset filter, and the trade counts show it (floor 2.0 drops 899 trades yet makes
more mid-model points than the baseline).

## The grid at 0.80 cost (quote_S5 = stop on bid/ask, the live convention)

| floor | n | mid PF / pts | **quote PF / pts** | quote TRAIN PF / pts | quote TEST PF / pts |
|---|---|---|---|---|---|
| 1.5 (live) | 3970 | 1.311 / 1993.6 | 1.114 / 792.4 | 1.057 / 248.5 | 1.210 / 543.9 |
| 2.0 | 3071 | 1.377 / 2078.5 | 1.168 / 1004.2 | 1.072 / 261.2 | 1.315 / 743.0 |
| 2.5 | 2248 | 1.386 / 1758.0 | 1.177 / 870.1 | 1.100 / 281.9 | 1.279 / 588.2 |
| **3.0** | 1624 | 1.408 / 1483.3 | **1.227 / 879.4** | **1.133 / 282.6** | **1.341 / 596.9** |

At 0.45 the ordering is the same (quote PF 1.352 → 1.385 → 1.369 → 1.405; TRAIN 1.295 →
1.279 → 1.285 → 1.300).

## Bars

- **1–5**: PASS for every floor at both costs (`SCORE.csv`: TEST n 753–1218, TEST PF > 1 at
  both costs, TRAIN PF 1.28–1.32 at 0.80, 90–100 % TEST months positive, max month ≤ 16 %,
  positive in gold-up and gold-down months).
- **6 plateau**: on TRAIN under live triggers both PF (1.057 → 1.072 → 1.100 → 1.133) and
  points (248.5 → 261.2 → 281.9 → 282.6) rise monotonically with the floor — a trend, not a
  spike; points flatten between 2.5 and 3.0.
- **7 beat the baseline at 0.80 on quote TEST PF AND points**: every floor does (2.0:
  1.315/743.0; 2.5: 1.279/588.2; 3.0: 1.341/596.9 vs 1.210/543.9).
- **TRAIN-justified selection** (declared: the floor with the best TRAIN quote PF, TEST read
  once): **floor 3.0** — TRAIN 1.133 (also the best TRAIN points, 282.6). Its TEST read:
  **1.341 / 596.9**, passing bar 7.

## Verdict — SHIP floor 3.0 for s14, with one caveat stated up front

`MIN_SL_DIST_PTS=3.0` on the `research_ob_mit_bias` container only (the env is read per
process by `shared/gate_rules.py`; c03 and the roster are untouched). Under live triggers
at ordinary cost that turns s14 from PF 1.11 (TRAIN 1.06) into **1.23 (TRAIN 1.13 / TEST
1.34)** with 11 % more points on 41 % of the trades — fewer, better trades, and ~3.3/day
instead of ~8, which also eases the book's concurrency cap and kill-switch load. Live, the
rejected signals will show as `sl_too_tight` on the Signals tab; that is the gate working.

**Caveat: 3.0 is the edge of the pre-declared grid and the TRAIN trend is still rising into
it.** 3.5 / 4.0 were not run and cannot be added now without a new pre-registration; the
right move is to ship 3.0 (fully justified on TRAIN, confirmed on TEST) and, if wanted,
pre-register a 3.0 / 3.5 / 4.0 extension as a separate campaign. Floor 2.0 makes the most
TEST points (743) but was not the TRAIN pick and is not adopted — reading it as better
would be selecting on TEST.

Also worth noting: the mid-M1 harness alone would have ranked these floors almost the same
way (PF 1.311 → 1.408). The S5 cache did not change the decision; it changed the *size* of
the problem it fixes (the baseline's [1.5, 2) trades were +225 pts in the harness and −102
under live triggers).

## Deployed 2026-09-18 11:11 UTC

`MIN_SL_DIST_PTS: '3.0'` added to the `research_ob_mit_bias` block of the box `compose.yml`
(additive, one line; backup `compose.yml.bak.s14minstop.20260918`), service recreated, verified
by importing `shared.gate_rules` inside the running container (3.0; c03 and s93 still 1.5).
The first signal after the restart was gated: `[ENTRY] blocked: stop 1.29pt < 3.00pt
friction floor`. The s14 service block exists only on the box (compose drift), so this line
has no counterpart in the repo compose; this note is its record.
