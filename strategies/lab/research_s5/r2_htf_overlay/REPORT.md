# The TTrades "no entry without a higher-timeframe reason" rule as an overlay on the book — report (2026-09-18)

Protocol: `PROTOCOL.md` (pre-registered before any number; one deviation D1, appended with
its reason). Every number below is copied from `results/*.csv` written by `run_overlay.py`
(nothing hand-typed). Data: the 15 Stage-1 modules' saved trades (45,537), NY-anchored daily
and weekly candles built from the QA'd M1 cache, the UTC daily cache for the SMA20 control,
and the QA'd S5 quotes for live-trigger exits. Costs: mid-M1 0.75 / **1.00**; quote-S5 0.45 /
**0.70** (stress values in bold are the ones quoted unless stated). TRAIN < 2025-12-01 ≤ TEST.

## Headline

**The corpus's governing rule does not hold on this book. Under six mechanical definitions of
"higher-timeframe reason" taken from the TTrades corpus — the previous-candle engine on the
daily (B1) and on the weekly (B4), premium/discount of the 3-day dealing range (B2), the
developing daily candle / "do not fade the daily candle" (B3), daily market structure (B5),
and the method's own stack B1∧B3 (B6) — trades taken *against* the HTF bias do not fail on
gold. On the four screen survivors (c03, s14, s95, s93) the against-bias subset is as
profitable as, or more profitable than, the aligned subset (quote-S5 0.70, TEST: B1 aligned
PF 1.238 vs against 1.315 pooled; per module c03 1.301 / 1.397, s14 1.378 / 1.173, s95 1.095 /
1.383, s93 1.087 / 1.301), none of the 24 confirmatory cells survives Holm (smallest raw
p = 0.07, and that cell says against is *better*), and no gated arm beats its incumbent on
bar 7 — every improvement in PF is bought with fewer points. On the pooled 15-module book the
raw aligned-minus-against gap runs from −0.04 to +0.07 R per trade across the six definitions, but once module composition is
removed (deviation D1) it is +0.02 R with a 95 % random-gate band of ±0.04 (B1, p = 0.30 ALL /
0.47 TEST), and the "dumb" SMA20 regime filter does the same (+0.025 / +0.034, n.s.). The
corpus's mechanism ("an LTF shift against the HTF is a retracement into an HTF PD array and
fails") predicts the losing modules' against-trades should be the worst; they are not — on the
11 non-survivors both halves lose equally (B1, quote-S5 0.70, TEST: aligned PF 0.782 vs against
0.765, demeaned ΔR +0.008, p = 0.77). The daily bias engine itself, read on 467 bias-days, predicts the next
NY day's close direction 47.8 % of the time against a 54.9 % base rate of up-days, with a
signed next-day return of −2.1 pts (t = −0.77): on 2024-09 → 2026-09 gold it carries no
directional information.**

## 1. What was run

- **Gates** (PROTOCOL §3; code `htf_bias.py`): each is a pure function of `(entry_time, side,
  entry_px)` and of HTF candles that closed before the fill, with the availability asserted per
  trade (`entry_time + 60 s ≥ candle close`; reference candle ≠ the trade's own trading day;
  today's open known from the day's first M1 bar). B1's engine was cross-checked against the
  RD repo's `detectors/bias.previous_candle_state`: **100 % agreement** on `implied_bias`
  over 535 valid NY days (`state` 99.8 %; the one difference is the undefined first candle).
  Join resolution: 0 `missing` for B1–B6 on every module; C2 is undefined for the first 20
  cache days (20–48 trades per module) exactly as `lab.harness.RegimeGate` is; B1's reference
  is stale (older than a weekend) on 0.8 % of trades (the 3 holiday / feed-hole days).
- **Costs and exits**: mid-M1 points re-costed exactly from the saved trades
  (`raw = pts + 0.80`); quote-S5 points from a vectorised resolver that reproduces
  `lab.s5exit.resolve` **with zero mismatches on all 6,610 c03 / s14 / s93 trades** and the
  published `results/s5exit` totals to the decimal (c03 1611.1, s14 792.4 at 0.80). S5 coverage
  45,425 / 45,537 trades (the last horizon-length of each module is dropped, as `s5exit` does).
- **Statistics**: ΔR = mean R(aligned) − mean R(against); day-block sign-flip null (2,000
  flips; C1), trade-level within-month permutation (500; C1b) — the two p-values correlate
  0.95; where they disagree at the 0.05 line the day-block null is the more conservative
  (C1 flags 26 cells C1b does not, C1b 4 that C1 does not); SMA20 regime (C2) as the dumb
  filter, judged with the same statistics and as a 2 × 2 conditional. Sanity floors tripped
  only on s97 (PF > 3 subsets) — the bar-artefact module the screen already disqualified.
- **Why filtering the saved list is legitimate here** (PROTOCOL §1): the gate never touches
  the strategy's state, stops, targets or the bars `get_signal()` sees, so the S100 `_pending`
  failure mode cannot occur; the one thing it cannot reproduce is `max_concurrent`/cooldown
  re-admission, which can only *add* aligned trades to a re-run. The appendix re-run (§7) puts
  a number on that.

## 2. Q1 — does trading against the HTF bias fail on gold, and by how much?

### 2.1 Pooled book (all 15 modules, `results/pooled.csv` and `pooled_demeaned.csv`)

Raw pooled numbers first (as pre-registered), then module-demeaned (D1) which is the honest
read — the raw version mixes the fact that c03 / s95 / s96 are profitable *and* 88–98 %
"aligned" under B3 with any within-module effect:

| gate | admitted | aligned PF / against PF, mid 1.00 ALL | TEST | raw ΔR ALL (p) | **demeaned ΔR ALL [band] (p)** | **demeaned TEST (p)** |
|---|---|---|---|---|---|---|
| B1 daily PCE | 0.457 | 0.831 / 0.787 | 0.848 / 0.827 | +0.035 (0.12) | **+0.021 [−0.040, +0.040] (0.30)** | **+0.021 (0.47)** |
| B2 P/D 3-day (classic) | 0.438 | 0.732 / 0.875 | 0.759 / 0.925 | −0.043 (0.05) | +0.009 (0.66) | +0.014 (0.65) |
| B3 developing daily candle | 0.604 | 0.875 / 0.716 | 0.928 / 0.737 | +0.052 (0.013) | −0.019 (0.35) | −0.013 (0.67) |
| B4 weekly PCE | 0.465 | 0.834 / 0.808 | 0.839 / 0.873 | +0.041 (0.065) | +0.034 (0.10) | +0.003 (0.92) |
| B5 daily structure | 0.518 | 0.824 / 0.808 | 0.868 / 0.843 | +0.003 (0.89) | −0.000 (1.00) | +0.005 (0.87) |
| B6 stack B1∧B3 | 0.273 | 0.887 / 0.774 | 0.904 / 0.808 | +0.066 (0.000) | +0.011 (0.55) | −0.001 (0.98) |
| C2 SMA20 (dumb) | 0.519 | 0.835 / 0.795 | 0.873 / 0.837 | +0.037 (0.10) | +0.025 (0.23) | +0.034 (0.26) |

Quote-S5 0.70 gives the same picture (demeaned B1 +0.022 ALL p 0.30, +0.016 TEST p 0.56;
B4 +0.043 ALL p 0.045, **+0.081 TRAIN p 0.0065, +0.006 TEST p 0.84** — a TRAIN-only effect;
B3 −0.023 / −0.018; C2 +0.028 / +0.038). The raw B3 / B6 "significance" is a Simpson effect,
not a market effect: it disappears when each module's mean R is removed.

By class:

| subset | B1 aligned / against PF (mid 1.00) ALL → TEST | demeaned ΔR ALL (p) → TEST (p) |
|---|---|---|
| survivors (7,623 trades) | 1.183 / 1.229 → 1.235 / **1.349** | +0.009 (0.82) → +0.038 (0.59) |
| non-survivors (37,914) | 0.756 / 0.698 → 0.780 / 0.740 | +0.023 (0.25) → +0.018 (0.51) |

**Module-level sign test** (14 modules, s98 excluded at n = 39): ΔR > 0 for B1 in 10/14 ALL,
10/14 TRAIN, **8/14 TEST**; B4 12/14 ALL (quote) → 7/14 TEST; B3 8/14 → 7/14; B6 8/14 → 5/14;
C2 8/14 → 6/14. Coin flips on TEST for every definition.

**Answer, by how much.** Against-HTF trades under the corpus's own daily bias (B1) earn
0.02 R per trade less than aligned trades on the book, inside a ±0.04 R random-gate band;
their PF is 0.79–0.83 where the aligned PF is 0.83–0.85 (both halves of a losing book lose);
on the profitable survivors the against subset earns *more* (TEST PF 1.35 vs 1.24 at mid,
1.32 vs 1.24 at quote-S5). "Fails" is not what the data says; "indistinguishable" is.

### 2.2 The bias engine on its own (day level, 467 bias-days of 522 in the trade window)

| | value |
|---|---|
| P(next NY day closes in the bias direction) | **0.478** (base rate of up-days 0.549) |
| P(predicted previous-candle side is taken next day) | 0.640 (opposite side 0.351; both 0.116) |
| mean signed next-day return in the bias direction | **−2.06 pts, t = −0.77** |
| by state: continuation (n 234) / reversal (159) / inside→trend (74) | dir-match 0.444 / 0.472 / 0.595; ret −3.5 / −3.7 / +6.0 pts |
| by direction: bullish (252) / bearish (215) | dir-match 0.524 / 0.423; ret +0.8 / −5.4 pts |

The one number that looks like a hit rate — 64 % of predicted sides taken — is the C2-wick
geometry of `Backtest Methodology Traps` §1: a continuation closure ends near the extreme it
predicts, so the next candle needs only a wick to "take" it. On direction and on signed
return the engine is at or below chance, and bearish reads averaged +5 pts *against* them
(the window is a gold bull market; see §6).

## 3. Per module — B1 (the corpus's primary definition), TEST (`results/cells.csv`)

| module | Stage-1 verdict | admitted | mid 1.00: base / aligned / against PF | quote 0.70: base / aligned / against PF | ΔR mid (p) | ΔR quote (p) |
|---|---|---|---|---|---|---|
| c03_fvg_fill | PASS | 0.50 | 1.400 / 1.299 / **1.501** | 1.377 / 1.301 / **1.397** | −0.002 (0.99) | +0.082 (0.65) |
| s14_ob_mit_bias | PASS | 0.44 | 1.267 / **1.338** / 1.207 | 1.272 / **1.378** / 1.173 | +0.103 (0.26) | +0.125 (0.20) |
| s95_session_breakout | PASS | 0.52 | 1.293 / 1.125 / **1.337** | 1.296 / 1.095 / **1.383** | −0.001 (0.99) | −0.019 (0.69) |
| s93_fvg_scalp | PASS | 0.40 | 1.184 / 1.041 / **1.274** | 1.202 / 1.087 / **1.301** | −0.173 (0.36) | −0.153 (0.42) |
| s97_snap_scalper_m5 | disqualified | 0.53 | 2.059 / 1.749 / 2.833 | 1.669 / 1.412 / 2.534 | −0.045 (0.61) | −0.090 (0.35) |
| s96_h1_momentum | NEAR | 0.65 | 1.326 / 1.206 / 1.282 | 1.325 / 1.124 / 1.369 | −0.001 (0.99) | −0.033 (0.66) |
| s100_m3_combo | FAIL | 0.46 | 0.904 / 0.893 / 0.864 | 0.893 / 0.876 / 0.869 | +0.033 (0.63) | +0.007 (0.92) |
| s12_m90_fade_bias | FAIL | 0.45 | 0.724 / 0.719 / 0.704 | 0.729 / 0.694 / 0.761 | +0.030 (0.61) | −0.016 (0.81) |
| s03_ob_mitigation | FAIL | 0.44 | 0.744 / 0.726 / 0.731 | 0.748 / 0.745 / 0.717 | +0.011 (0.86) | +0.034 (0.57) |
| s99_mss_fvg | FAIL | 0.45 | 0.788 / 0.674 / 0.786 | 0.767 / 0.623 / 0.795 | −0.086 (0.26) | −0.109 (0.17) |
| s04_breaker_block | FAIL | 0.45 | 0.691 / 0.686 / 0.701 | 0.730 / 0.732 / 0.740 | +0.015 (0.77) | +0.016 (0.75) |
| s94_sweep_reversal | FAIL | 0.48 | 0.772 / 0.822 / 0.727 | 0.813 / 0.895 / 0.741 | +0.084 (0.66) | +0.176 (0.34) |
| s11_m90_fade_ny | FAIL | 0.47 | 0.651 / 0.648 / 0.667 | 0.699 / 0.698 / 0.729 | +0.002 (0.99) | −0.025 (0.81) |
| s10_90min_fade | FAIL | 0.45 | 0.580 / 0.583 / 0.570 | 0.601 / 0.590 / 0.605 | +0.030 (0.53) | +0.009 (0.85) |
| s98_zscore_mr_m15 | FAIL (n) | 0.44 | 0.185 / 0.566 / 0.102 (n 7 / 9) | 0.194 / 0.631 / 0.105 | +0.039 (0.96) | +0.075 (0.91) |

No module separates. On the three survivors other than s14 the against subset is the better
half on TEST at both cost models; on s14 the aligned half is better on TEST but was *worse*
on TRAIN (mid 1.00 TRAIN 1.111 vs 1.152; quote 1.124 vs 1.131) — a sign flip across the
split. B1's anchor sensitivity (NY 18:00 vs the cache's UTC day) flips the sign of ΔR on s93
(−0.153 → −0.066) and lifts s95's aligned PF from 1.095 to 1.446 — the fingerprint of noise.

Across all six corpus gates and 14 modules (84 cells per split and cost model) the count of
raw p < 0.05 cells is 10 (ALL, mid) / 13 (ALL, quote) / 6 (TEST, mid) / 9 (TEST, quote)
against 4.2 expected — and the excess splits evenly in sign (TEST mid: 1 positive, 5 negative;
TEST quote: 4 / 5). The negative ones are interpretable: **B3 ("do not fade the developing
daily candle") is wrong-signed for the mean-reversion modules** — s14 ΔR −0.171 (p 0.001 ALL,
−0.179 p 0.035 TEST; its counter-candle OB mitigations are its best trades, PF 1.34 vs 1.10),
s12 −0.154 (p 0.000 / 0.006), s10 −0.058 — and by construction irrelevant for the momentum
modules (c03 88 %, s95 89 %, s96 98.5 % aligned; s96's 11 against-trades are the inf-PF cell).
B2's classic reading is right-signed on s14 (+0.130 p 0.01 ALL; +0.166 p 0.054 TEST — OB
mitigations bought in discount / sold in premium) and wrong-signed on the breakout module
s95 (−0.057 p 0.06), which is exactly the corpus's own §8.3 inversion ("EQ for expansions").
Which reading of premium/discount is "right" is a property of the strategy's mechanism, not
of the market.

## 4. The four survivors — the confirmatory family (`results/survivor_bars.csv`)

24 cells (6 gates × 4 survivors), quote-S5 0.70, TEST, Holm-corrected: **no cell is
significant; every p_holm = 1.0** (smallest raw p 0.0735: s14 × B3, ΔR −0.161 — against is
better). The random-gate band contains the aligned PF in all 24 cells (e.g. B1: c03 1.301 in
[1.091, 1.627], s14 1.378 in [1.114, 1.448], s95 1.095 in [0.919, 1.589], s93 1.087 in
[0.809, 1.739]).

**Nomination on TRAIN** (PROTOCOL §5 rule — largest TRAIN ΔR at stress among cells with C1
p < 0.05 and C2-conditional sign agreement): c03 none (best TRAIN p 0.23, C2 itself); s95
none; s93 none; **s14 → B4 weekly PCE** (TRAIN ΔR +0.214, p 0.002, aligned PF 1.337 vs against
0.987). Read on TEST once: ΔR +0.115 (p 0.22), aligned PF 1.374 / **378 pts** vs incumbent
1.272 / **686 pts** → **fails bar 7 on points**; bar 5 passes (gold-corr 0.40, up 3 /
down 3 months positive), bars 1–4 pass. Verdict: quality-up / profit-down, not adopted.

Bars 1–7 for every gated arm, quote-S5 (0.45 / 0.70), TEST vs incumbent at 0.70:

| module | incumbent TEST PF / pts | arms passing bars 1–5 | bar 7 (PF **and** pts) | best arm |
|---|---|---|---|---|
| c03 | 1.377 / 1302 | B1, B3, B4, B5, B6, C2 | **none** | B3 1.398 / 1224 (−78 pts), B5 1.508 / 824, C2 1.418 / 748 — all PF-up / pts-down |
| s14 | 1.272 / 686 | all seven | **none** | B2 1.388 / 409, B1 1.378 / 400, B4 1.374 / 378 — PF-up / pts-down |
| s95 | 1.296 / 746 | B3 only | **none** | B3 1.301 / 684 (PF +0.005, −62 pts); every other arm fails bar 4 (max-month share > 0.5) |
| s93 | 1.202 / 166 | none (all fail bar 4: one month > 50 % of TEST net) | **none** | B3 1.255 / 146 |

Same at mid-M1 (0.75 / 1.00): no arm passes bar 7; the plateau axes (B2 lookback 2/3/5, B3
deadband 0/0.05/0.10 ADR, B5 3-/5-bar) are flat within ±0.05 PF where they pass at all,
i.e. there is nothing to tune because there is no slope.

## 5. Controls

- **Random gate, same admitted fraction (C1)**: for every survivor cell the aligned arm's PF
  and points sit inside the 2.5–97.5 % band of day-block sign-flipped gates (§4). On the
  pooled book the demeaned ΔR of every corpus gate sits inside its band on TEST (§2.1).
- **The dumb filter (C2, daily SMA20)**: pooled demeaned ΔR +0.025 (ALL) / +0.034 (TEST),
  n.s.; per survivor it is the best "gate" on c03 (ALL ΔR +0.175, p 0.033; TEST +0.157,
  p 0.33; TEST arm 1.418 / 748 pts vs 1.377 / 1302) and nothing on the others. **No corpus
  gate beats it on TEST**: B1's demeaned TEST ΔR is +0.021 vs C2's +0.034 (all 15), +0.038 vs
  +0.010 on the survivors (both n.s.). Direction agreement between the corpus's daily bias and
  the SMA regime is only 66 %, so they are different objects that happen to be equally
  uninformative here.
- **Conditional on C2 (2 × 2)**: no corpus gate keeps a same-signed, significant ΔR in both
  C2 classes on TEST (`pooled_demeaned.csv`: B1 +0.045 / −0.022, B3 +0.034 / −0.065, B4
  +0.017 / −0.035, B6 +0.031 / −0.086, all15 mid 1.00). The one cell that is significant in a
  class is the post-hoc lead below.

## 6. What it does and does not establish

Established (24 months, 45k trades, matched controls, quote-level exits):
1. Six mechanical readings of "HTF reason" from the corpus, applied as overlays, do not
   separate the book's trades into a failing and a working half. The largest honest effect is
   +0.02 R per trade for the daily previous-candle engine, inside its random-gate band, and
   equal to a daily SMA20. The rule as stated ("expected to fail") is refuted on this book.
2. The direction of the effect depends on the strategy's mechanism, not the market:
   "do not fade the daily candle" hurts the mean-reversion modules (s14, s12, s10) and is
   vacuous for the continuation modules; classic premium/discount helps s14 and hurts s95.
   A single HTF rule cannot be right for both halves of a book.
3. The corpus's own daily bias engine is directionally uninformative on this window
   (47.8 % vs 54.9 %; −2.1 pts signed), and its apparent 64 % "side taken" rate is candle
   geometry.
4. Filtering a saved trade list with an entry-time gate is safe for this class of gate (§7).

Not established / caveats:
- **Regime.** The window is a gold bull market (2025 straight-line rally, 2026 topping
  range); bearish daily reads averaged +5.4 pts against them. A bias engine may behave
  differently in a two-sided market; nothing here says so either way. This is the same
  caveat the conjunction test carried (every project result comes from block B4).
- **Confirmed bias** (daily C2/C3 **plus** hourly CISD, spec §2.4) and the POI gate were
  out of scope by design; the conjunction test already found the day-layer stack leaves
  ~3–5 tradeable days a year and that no rung adds value over a matched control on the
  method's own events.
- Nothing about entries the strategies did not generate; an HTF overlay can only remove.
- **One post-hoc lead, reported as such**: s14 × B4 (weekly PCE) *within the SMA20-aligned
  class* — ΔR +0.33 on TRAIN (p 0.000) and +0.34 on TEST (p 0.013; aligned PF 1.453 vs
  0.996, n 422 / 221), but −0.05 in the SMA20-against class (p 0.72), so it fails the
  pre-registered (iii)-b, and its Holm-adjusted p within the 48 survivor × gate × class cells
  is 0.62. If anyone wants it, it needs its own pre-registration as a *conjunction*
  (weekly PCE ∧ daily SMA20) on s14, re-run through the harness, and it will still be a
  PF-up / points-down arm (378 pts vs 686 before conditioning).

## 7. Appendix — the in-harness re-run check (PROTOCOL §1)

B1 was also run *inside* a verbatim copy of the harness (`harness_gate.py`, one added `Cfg`
field) for the four survivors at 0.80 and compared with the filtered saved list
(`results/rerun_check.csv`):

| module | filtered list: n / pts / PF | in-harness re-run: n / pts / PF | common | re-admitted (only in re-run): n / pts | only in filtered: n / pts | incumbent at 0.80: n / pts |
|---|---|---|---|---|---|---|
| s93 | 202 / 44.4 / 1.076 | 205 / 29.8 / 1.050 | 202 | 3 / −14.6 | 0 | 493 / 189.2 |
| c03 | 1079 / 916.9 / 1.301 | 1091 / 880.7 / 1.283 | 1077 | 14 / −52.1 | 2 / −16.0 | 2161 / 2225.9 |
| s14 | 1774 / 934.4 / 1.329 | 1861 / 1140.3 / 1.394 | 1754 | 107 / +214.7 | 20 / +8.8 | 3970 / 1993.6 |
| s95 | 536 / 517.8 / 1.210 | 536 / 517.8 / 1.210 | 536 | 0 | 0 | 999 / 1346.3 |

Every filtered trade reappears in the re-run except 22 whose slot timing shifted (2 on c03, 20
on s14); the re-run adds 3 / 14 / 107 / 0 re-admitted trades — the concurrency effect the
protocol predicted, and it is small except on s14 (its 5-minute cooldown and 1-slot cap bind
most often). Sign, magnitude and verdict are unchanged: PF 1.05–1.39 for the gated arm vs the
incumbent's 1.13 / 1.36 / 1.31 / 1.31, and the re-run gated arm still books fewer points than
the incumbent on all four (bar 7 fails in the re-run too). The c03 and s14 re-runs took 1,341 s
and 1,134 s on a box shared with other sweeps; s95's first attempt was killed by the OS and was
re-run alone (913 s). Filtering a saved list with an entry-time gate is a faithful proxy for
this class of gate; the S100-style state effect does not arise.

## 8. Next hypotheses (pre-registration candidates, not results)

1. **Strategy-type-conditional HTF rules**: "fade the daily candle" for mean-reversion modules
   (s14: against-B3 TEST PF 1.465 vs aligned 1.187 at quote 0.70) is the mirror of the corpus
   rule and the only sizeable, same-signed TRAIN/TEST effect in the study — but it is a subset
   selection (fewer points) and must be tested as a re-run with its own control.
2. The corpus's premium/discount inversion is a *mechanism* statement (continuation vs
   mean-reversion), testable as B2-classic on mean-reversion modules vs B2-inverted on
   breakout modules, pre-declared per module type.
3. A two-sided-market test of the daily bias engine needs the 2016+ deep-history M1
   (`XAUUSD Data Inventory`), day-level only (no strategy needed): the 47.8 % / −2.1-pt
   figures on a decade would settle whether §2.2 is a regime artefact.

## 9. Files

`PROTOCOL.md` · `htf_bias.py` (gates, NY/weekly candles, engines, assertions) · `s5quote.py`
(vectorised quote-S5 resolver + `--validate`) · `run_overlay.py` (labels, statistics, nulls,
bars; `main_d1()` for the demeaned pooled test) · `harness_gate.py` + `rerun_check.py`
(appendix) · `results/labels_<module>.parquet` (per-trade labels, raw mid and quote points),
`cells.csv` (2,340 cells), `regime2x2.csv`, `pooled.csv`, `pooled_demeaned.csv`,
`survivor_bars.csv`, `rerun_check.csv`, `info.json`, `run_overlay.log`.
