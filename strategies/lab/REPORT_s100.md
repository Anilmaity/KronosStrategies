# S100 M3 Combo — Optimization Report (2026-09-02)

> Authored by the S100 sub-agent; persisted here by the coordinator because the
> sub-agent harness blocks report-file writes. Content is the agent's, verbatim in
> substance. The coordinator's own follow-up analysis is appended at the end.

## 0. How this was produced

Cut short of its original ~30-replay plan: two background sweeps were killed by the
environment before producing any arm results. The coordinator had already established the
baseline and was running the hours question independently, and directed this report be
written **without further replays**, built on (1) the coordinator's baseline, (2) live-book
hour evidence, (3) an entry-model P&L breakdown computed from the existing saved trade
list — no replay needed, (4) an explicit note that the ER gate was not retested.

**Harness caveat:** `lab/harness.py` had a warm-up bug fixed today (`replay()` guaranteed
only `win_1m` lookback and ignored M5/M15, under-warming S100 at 705 bars instead of
`max(705,805,1505)=1505`). The inherited baseline CSV reproduces the coordinator's supplied
numbers exactly, and the fix affects ~1 of 585 days, so the delta is immaterial here — but
no table below should be compared against arms run under a different harness version
without re-establishing the baseline.

## 1. Baseline

`_HOURS=(1..8,13,14,15)`, cost 0.45, min_sl 1.5, `_TP_R=2.5`, ER gate off.
TRAIN 2025-01-05→2026-02-01, TEST 2026-02-01→2026-08-12.

| half | n | pts | PF | WR% | maxDD |
|---|---|---|---|---|---|
| TRAIN | 2276 | +12.6 | 1.002 | 33.7 | −377.0 |
| TEST | 1656 | +152.2 | 1.027 | 33.7 | −265.5 |

**Framing, with due skepticism:** S100 is the only roster member positive in both halves and
the backtest ranking matches the live ranking. But TRAIN PF 1.002 is essentially a coin flip
(0.006R/trade over 2276 trades). S100 reads as the *least broken* roster member, not one
with an obviously robust full-sample edge.

## 2. Live-hour evidence (context, not backtest-confirmed)

| hour | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 13 | 14 | 15 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| n | 10 | 11 | 7 | 9 | 13 | 11 | 9 | 10 | 8 | 5 | 4 |
| USD | −157 | −148 | −74 | +190 | +68 | +68 | +12 | +47 | +398 | +96 | −14 |
| WR | 10% | 9% | 14% | 44% | 31% | 36% | 33% | 30% | 63% | 40% | 25% |

Drop hours 1–3: n=69, +$865.7, PF 1.83, DD −181.2 (vs as-traded +$485.8, PF 1.30, −375.5).

**Caveat:** hours 1–3 (n=28, ~11% WR vs ~30% baseline) is ~p 2–3% alone, but was found by
scanning 11 buckets after seeing which looked bad. Multiple-comparison risk, not
pre-registered. Open, well-motivated hypothesis — **not validated**.

## 3. Entry-model breakdown — the headline finding

**TRAIN** — FVG n=1097 −166.6 PF 0.938 · OB n=707 −4.7 PF 0.997 · RSI n=472 +184.0 PF 1.172
**TEST** — FVG n=817 +226.7 PF 1.080 · OB n=567 −72.9 PF 0.957 · RSI n=272 −1.6 PF 0.998
**Full** — FVG n=1914 +60.1 PF 1.011 · **OB n=1274 −77.6 PF 0.976** · RSI n=744 +182.4 PF 1.086

OB edge retest is the one model negative in **both** halves and over the full period, with
ample sample. FVG and RSI mirror each other — FVG loses on TRAIN and wins on TEST, RSI the
reverse. So S100's "both-halves-positive" story is partly a coincidence of which model was
strong in which half, **not** three uniformly-positive engines.

**Verdict: OB is a plausible dead-weight candidate — INCONCLUSIVE, not shippable.** This is
a decomposition of one static trade list, not a comparative replay. OB shares the
single-slot pending-retrace machine (`_pending`) with FVG, so disabling OB changes which
FVG/RSI signals get a chance to fire afterward — a filtered CSV cannot measure that, only a
filtered replay can.

## 4. Dimensions not completed

`_HOURS` (running elsewhere), `min_sl_dist_pts`, `_TP_R` (but the same sweep **refuted**
raising it on S93 and S99), `S100_ER_GATE` (do-not-arm verdict stands unrevisited), and any
combined config. No verdicts on these.

## 5. Cost realism

All numbers use cost 0.45. The campaign's own measurement puts realistic round-trip cost at
0.6–0.8 pt. A strategy at PF 1.00–1.03 pre-stress has little room before an extra
0.15–0.35 pt/trade flips it negative.

## 6. Recommendation

**No change shipped this round.** Nothing meets the pre-registered bar. What this adds:
(1) S100's both-halves-positive headline is real but thin and partly a FVG/RSI hand-off;
(2) OB edge retest is the strongest evidence-backed lead, worth a dedicated disable-OB
replay; (3) hours/min_sl/TP_R/ER-gate remain open.

---

## Coordinator's follow-up (post-filter, same limitation applies)

Isolating each candidate on the saved trade list — **approximation only**, for exactly the
concurrency reason the agent gives above:

| config | TRAIN pts / PF | TEST pts / PF |
|---|---|---|
| baseline | +12.6 / 1.002 | +152.2 / 1.027 |
| drop OB model only | +17.3 / 1.005 | +225.1 / 1.058 |
| drop hours 1–3 only | +0.6 / 1.000 | +387.4 / 1.096 |
| drop OB **and** hours 1–3 | +99.6 / **1.036** | +431.9 / **1.160** |

At cost 0.80: baseline TRAIN 0.867 / TEST 0.928; drop-both TRAIN 0.899 / TEST **1.056**.

Note the asymmetry the agent's framing predicts: **dropping hours 1–3 does not improve
TRAIN** (1.002 → 1.000) while improving TEST a lot — the pattern you would expect if the
hour choice were partly fitted to the test half and the live sample. Dropping OB improves
**both** halves, modestly. Combining them improves both.

By the pre-registered bar, S100 hours is therefore **weaker evidence than S93's**, where
both halves improved and a 5-arm plateau held. The disable-OB replay is the cleaner next
experiment.
