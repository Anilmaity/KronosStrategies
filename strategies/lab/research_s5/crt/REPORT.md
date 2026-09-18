# REPORT — Candle Range Theory (CRT / AMD) at H1 and H4 with honest execution

Study folder `lab/research_s5/crt/` · pre-registration `PROTOCOL.md` · raw output
`results/run_output.txt` and `results/extras_output.txt` (every number below is copied from
those two files) · data: QA'd M1 cache for candle formation, S5 bid/ask cache for execution.

## Verdict in one line

**CRT fails every bar on every intraday grid.** With the entry at the C3-open quote and
quote-level exits, the pattern loses at both costs in TRAIN and in TEST on H1-UTC, H4-UTC and
H4-NY, with or without the HTF bias filter (12 arms, 0 passes). A random C3 with the same
geometry loses the same amount: the concept explains nothing the control does not.

## 1. Question

Does "C1 sets the range, C2 sweeps an extreme and closes back inside, C3 delivers to the
opposite extreme" carry an edge on XAU_USD at H1 / H4 (UTC grid) and on the ICT 4-hour grid
anchored at 17:00 ET — once the entry is the next S5 quote after the C2 close and every
exit is resolved on 5-second bid/ask? And: does the daily result in `bt_crt_daily.py`
(WR 60 / PF 1.63 / +0.25 R on 25 trades) generalise?

## 2. Pre-registered rule (unchanged; see PROTOCOL.md)

Sweep threshold 0.05·ATR(14) beyond C1's extreme with C2 close back inside; ATR at C1.
Entry at the first S5 bar of C3 — long at `ask_c`, short at `bid_c` — skipped if the first
bar is > 15 min after the scheduled open. Stop beyond C2's extreme + 0.1·ATR; target C1's
opposite extreme; fill must lie strictly between them. Exits on the quote (long on bid,
short on ask), stop before target within a bar, forced exit at the C3 close on the quote.
Costs 0.45 / 0.80 pts on top. Window 2024-09-01 → 2026-09-17, TRAIN < 2025-12-01 ≤ TEST.
Pre-declared variant: EMA20 vs EMA50 bias on the parent TF (H4 for H1; D1 for H4). No free
parameters, no TRAIN-based selection.

Data integrity checks passed before anything was scored: candles built from M1 agree with
the cached H1 / H4 / D1 files on high and low at **100.000 %** (12,263 / 3,316 / 647
overlapping bars). All fills were at the scheduled open (0 fills later than 60 s on any
grid; median fill delay 0 s).

## 3. Event counts and the geometry of the pattern

| grid | C2 sweeps in window | both sides swept (skipped) | C2 already closed at/beyond the target (skipped) | late open (skipped) | **trades** | TRAIN / TEST | median risk (pts) | median reward/risk |
|---|---:|---:|---:|---:|---:|---|---:|---:|
| H1-UTC | 4075 | 332 | 593 | 0 | **3150** | 1911 / 1239 | 7.72 | 1.01 |
| H4-UTC | 1045 | 90 | 194 | 3 | **758** | 481 / 277 | 15.07 | 1.01 |
| H4-NY | 1054 | 126 | 160 | 74 | **694** | 415 / 279 | 15.73 | 0.98 |

The 74 late opens on H4-NY are the 17:00-ET slot (feed reopens 18:04 ET). Outcome mix under
the honest rule (TP / SL / TIME): H1 25.9 / 28.9 / 45.2 %; H4-UTC 27.0 / 30.1 / 42.9 %;
H4-NY 30.1 / 30.1 / 39.8 %. With a ~1 R target and a one-candle hold, 40–45 % of trades
simply expire.

## 4. Results — TRAIN / TEST × cost, quote_s5 (points; PF in points; R = net pts / risk)

### H1-UTC
| arm | cost | split | n | WR % | PF | pts | avg pts | avg R |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| nofilt | 0.45 | TRAIN | 1911 | 43.0 | 0.612 | −1837.2 | −0.961 | −0.195 |
| nofilt | 0.45 | TEST | 1239 | 48.1 | 0.813 | −1118.8 | −0.903 | −0.058 |
| nofilt | 0.80 | TRAIN | 1911 | 39.8 | 0.511 | −2506.1 | −1.311 | −0.269 |
| nofilt | 0.80 | TEST | 1239 | 47.1 | 0.749 | −1552.4 | −1.253 | −0.090 |
| bias | 0.45 | TRAIN | 968 | 43.9 | 0.673 | −746.7 | −0.771 | −0.165 |
| bias | 0.45 | TEST | 623 | 48.2 | 0.776 | −699.2 | −1.122 | −0.064 |
| bias | 0.80 | TRAIN | 968 | 41.4 | 0.562 | −1085.5 | −1.121 | −0.236 |
| bias | 0.80 | TEST | 623 | 46.7 | 0.716 | −917.3 | −1.472 | −0.095 |

### H4-UTC
| arm | cost | split | n | WR % | PF | pts | avg pts | avg R |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| nofilt | 0.45 | TRAIN | 481 | 46.6 | 0.828 | −368.2 | −0.766 | −0.095 |
| nofilt | 0.45 | TEST | 277 | 45.5 | 0.784 | −614.8 | −2.220 | −0.121 |
| nofilt | 0.80 | TRAIN | 481 | 45.1 | 0.760 | −536.6 | −1.116 | −0.132 |
| nofilt | 0.80 | TEST | 277 | 44.0 | 0.754 | −711.8 | −2.570 | −0.137 |
| bias | 0.45 | TRAIN | 232 | 49.6 | 0.888 | −111.6 | −0.481 | −0.090 |
| bias | 0.45 | TEST | 139 | 49.6 | 0.966 | −47.5 | −0.342 | −0.103 |
| bias | 0.80 | TRAIN | 232 | 47.8 | 0.815 | −192.8 | −0.831 | −0.125 |
| bias | 0.80 | TEST | 139 | 47.5 | 0.931 | −96.2 | −0.692 | −0.118 |

### H4-NY (17:00-ET grid)
| arm | cost | split | n | WR % | PF | pts | avg pts | avg R |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| nofilt | 0.45 | TRAIN | 415 | 46.7 | 0.771 | −463.2 | −1.116 | −0.080 |
| nofilt | 0.45 | TEST | 279 | 47.7 | 0.901 | −263.2 | −0.943 | −0.053 |
| nofilt | 0.80 | TRAIN | 415 | 44.8 | 0.711 | −608.5 | −1.466 | −0.116 |
| nofilt | 0.80 | TEST | 279 | 47.3 | 0.866 | −360.8 | −1.293 | −0.068 |
| bias | 0.45 | TRAIN | 215 | 48.4 | 0.857 | −144.0 | −0.670 | −0.064 |
| bias | 0.45 | TEST | 129 | 48.8 | 0.942 | −73.0 | −0.566 | −0.013 |
| bias | 0.80 | TRAIN | 215 | 47.0 | 0.791 | −219.2 | −1.020 | −0.099 |
| bias | 0.80 | TEST | 129 | 48.8 | 0.908 | −118.1 | −0.916 | −0.028 |

### Bars (`lab.tools.campaign_score.score_campaign`, split 2025-12-01)
| arm (base 0.45) | verdict | bars | TEST n | TEST PF 0.45 / 0.80 | TEST pts | TRAIN PF | TEST months > 0 | gold corr | up-months pts / down-months pts |
|---|---|---:|---:|---|---:|---:|---:|---:|---|
| H1-UTC nofilt | **FAIL** | 2/6 | 1239 | 0.813 / 0.749 | −1118.8 | 0.612 | 10 % | 0.32 | −1720.3 / −1235.7 |
| H1-UTC bias | **FAIL** | 2/6 | 623 | 0.776 / 0.716 | −699.2 | 0.673 | 10 % | 0.07 | −827.8 / −618.2 |
| H4-UTC nofilt | **FAIL** | 2/6 | 277 | 0.784 / 0.754 | −614.8 | 0.828 | 20 % | 0.36 | −321.2 / −661.8 |
| H4-UTC bias | **FAIL** | 2/6 | 139 | 0.966 / 0.931 | −47.5 | 0.888 | 30 % | 0.27 | +229.9 / −389.0 |
| H4-NY nofilt | **FAIL** | 1/6 | 279 | 0.901 / 0.866 | −263.2 | 0.771 | 30 % | 0.51 | −257.4 / −469.0 |
| H4-NY bias | **FAIL** | 2/6 | 129 | 0.942 / 0.908 | −73.0 | 0.857 | 30 % | 0.39 | −32.2 / −184.8 |

The only bars passed are n (bar 1) and, for five arms, the regime bar — which passes
trivially via |corr| < 0.4 for an arm that loses in both regimes. No arm has TEST PF > 1 at
either cost; no arm has TRAIN PF > 0.9; TEST months positive are 10–30 %. Six judged arms,
zero passes; the expected 5 % single-bar false positives are irrelevant to a table with no
survivor.

### Where the loss comes from (`extras_output.txt`)
Gross expectancy at mid, before spread and cost: **H1 +0.151 pts/trade (+0.025 R),
H4-UTC −0.135 pts (−0.020 R), H4-NY −0.019 pts (+0.004 R)**. The spread actually paid on the
quote fills averages 0.639 / 0.712 / 0.578 pts; add the 0.45 cost and friction is
≈ 1.1–1.2 pts per trade against a gross edge of at most +0.15. The pattern's gross edge is
indistinguishable from zero and an order of magnitude below the friction it must clear.

### By year (0.45, quote_s5, nofilt)
| grid | 2024 | 2025 | 2026 |
|---|---|---|---|
| H1-UTC | n 500, PF 0.480, −0.273 R | n 1534, PF 0.651, −0.160 R | n 1116, PF 0.819, −0.054 R |
| H4-UTC | n 133, PF 0.594, −0.172 R | n 378, PF 0.865, −0.070 R | n 247, PF 0.787, −0.121 R |
| H4-NY | n 93, PF 0.909, +0.059 R | n 352, PF 0.773, −0.104 R | n 249, PF 0.892, −0.067 R |

The year-on-year "improvement" on H1 is cost drag shrinking as gold's ATR grew, not edge:
median risk went 3.95 → 6.62 → 12.58 pts and friction per trade fell from 0.244 R to
0.098 R (H1). Nothing turns positive.

### By side (0.45, quote_s5, all window)
| grid | BUY | SELL |
|---|---|---|
| H1-UTC nofilt | n 1588, PF 0.752, −0.134 R | n 1562, PF 0.695, −0.147 R |
| H4-UTC nofilt | n 369, PF 1.022, −0.063 R | n 389, PF 0.621, −0.144 R |
| H4-NY nofilt | n 356, PF 0.893, −0.054 R | n 338, PF 0.794, −0.085 R |
| H4-UTC bias | n 286, PF 1.154, −0.060 R | n 85, PF 0.501, −0.213 R |

Longs beat shorts everywhere, and H4-UTC longs show PF > 1 in points while their average R
is still negative (a few large-risk winners in the 2025 rally carry the points). This is the
gold-up regime, not a sweep-direction effect: it is not a pre-registered arm and the R
figure is negative.

## 5. Control (a) — random C3, concept removed (K = 20 draws per trade, same slot, ±30 d, no sweep, transplanted ATR-scaled geometry)

| grid | cost | split | real PF | real pts | control PF mean (p5–p95) | control pts mean | share of draws with PF ≥ real |
|---|---:|---|---:|---:|---|---:|---:|
| H1-UTC | 0.45 | TRAIN | 0.612 | −1837.2 | 0.617 (0.554–0.665) | −1856.9 | 0.55 |
| H1-UTC | 0.45 | TEST | 0.813 | −1118.8 | 0.754 (0.635–0.814) | −1554.8 | 0.10 |
| H1-UTC | 0.80 | TRAIN | 0.511 | −2506.1 | 0.518 (0.465–0.558) | −2525.8 | 0.65 |
| H1-UTC | 0.80 | TEST | 0.749 | −1552.4 | 0.696 (0.584–0.752) | −1988.4 | 0.10 |
| H4-UTC | 0.45 | TRAIN | 0.828 | −368.2 | 0.753 (0.663–0.911) | −543.4 | 0.10 |
| H4-UTC | 0.45 | TEST | 0.784 | −614.8 | 0.889 (0.745–1.082) | −324.1 | 0.85 |
| H4-UTC | 0.80 | TRAIN | 0.760 | −536.6 | 0.688 (0.605–0.833) | −711.7 | 0.10 |
| H4-UTC | 0.80 | TEST | 0.754 | −711.8 | 0.855 (0.718–1.041) | −421.0 | 0.85 |
| H4-NY | 0.45 | TRAIN | 0.771 | −463.2 | 0.800 (0.689–0.932) | −397.3 | 0.55 |
| H4-NY | 0.45 | TEST | 0.901 | −263.2 | 0.856 (0.671–1.005) | −422.9 | 0.35 |
| H4-NY | 0.80 | TRAIN | 0.711 | −608.5 | 0.736 (0.634–0.856) | −542.6 | 0.55 |
| H4-NY | 0.80 | TEST | 0.866 | −360.8 | 0.824 (0.644–0.967) | −520.5 | 0.35 |

Every real trade had a control pool (3150/3150, 758/758, 694/694). The random control loses
the same order of magnitude as the real trades on every grid, and the sign of
real-minus-control flips between TRAIN and TEST on H1 (≈ 0 → real better) and on H4-UTC
(real better → real worse, 85 % of draws beat it on TEST). There is no consistent residual
for the sweep to be credited with; the loss is the geometry (a ~1 R target, a stop beyond a
just-swept extreme, one candle of time) plus friction, whether or not a sweep happened.

## 6. Control (b) — the C2-wick geometric artefact (vault note), same events, wick quintiles

Columns: `vault close` = C3 closes beyond the C2 open (the note's wording); `touch, C2-close
entry` = enter at the C2 close (mid), target the C2 open, stop as the rule, resolved on C3's
M1 bars, counted delivered if the target is touched or the entry already sits beyond it;
`touch, C3-open quote entry` = the same with the entry moved to the pre-registered fill;
`trivial` = share whose entry already sits beyond the C2-open target; `honest TP` and
`honest avg R` = the pre-registered rule (C1-opposite-extreme target).

| grid | quintile (wick/range median) | cushion R (med) | trivial % | vault close % | touch, C2-close entry % | touch, C3-open quote entry % | honest target dist (R, med) | honest TP % | honest avg R @0.45 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| H1 | Q1 small (0.236) | +0.206 | 54.1 | 51.3 | 71.0 | 69.0 | 1.771 | 23.5 | −0.132 |
| H1 | Q5 large (0.741) | −0.012 | 46.8 | 49.4 | 90.5 | 88.3 | 0.654 | 31.9 | −0.122 |
| H4-UTC | Q1 small (0.246) | +0.251 | 55.3 | 52.6 | 70.4 | 69.7 | 1.838 | 21.7 | −0.154 |
| H4-UTC | Q5 large (0.769) | +0.002 | 50.0 | 46.7 | 93.4 | 90.8 | 0.620 | 34.9 | −0.086 |
| H4-NY | Q1 small (0.233) | +0.229 | 56.8 | 59.7 | 80.6 | 79.1 | 1.586 | 25.2 | +0.058 |
| H4-NY | Q5 large (0.748) | −0.006 | 48.9 | 50.4 | 93.5 | 91.4 | 0.690 | 34.5 | −0.096 |

Q1−Q5 gaps (from `run_output.txt`): vault-close +1.9 / +5.9 / +9.3 pp; touch with C2-close
entry −19.5 / −23.0 / −12.9 pp; touch with C3-open quote entry −19.3 / −21.1 / −12.3 pp;
honest TP −8.4 / −13.2 / −9.3 pp; honest avg R −0.010 / −0.068 / +0.154 R.

What this establishes, and what it corrects in the task's framing:
- The "delivery beyond the C2 open" metric is geometry on this feed too: 47–57 % of its
  deliveries are trivial (the entry already sits beyond the target), and the remainder track
  the distance to the target (a large-wick C2 has a small body, so its open is ~0.01 R away
  and is "delivered" 90–93 % of the time). The note's exact wick/delivery ordering could not
  be reproduced from its text (here the large-wick bucket delivers most), but the mechanism
  it identified — delivery tracking distance — is what the numbers show.
- **Moving the entry from the C2 close to the C3-open quote does NOT remove the artefact:
  it moves delivery by only 1–3 pp** (71.0 → 69.0, 70.4 → 69.7, 80.6 → 79.1 in Q1; 90.5 →
  88.3, 93.4 → 90.8, 93.5 → 91.4 in Q5), because the C3 open is the C2 close plus half a
  spread. What removes it is the target definition plus the fill-inside-geometry rule: with
  C1's opposite extreme as the target, trivial deliveries are 0 % by construction, the TP
  rate falls to 22–35 %, and the residual Q1−Q5 TP gradient (−8 to −13 pp) still tracks the
  target distance (1.6–1.8 R vs 0.6–0.7 R) while expectancy in R is flat and of no consistent
  sign across grids. Wick size predicts nothing once the target is a level the entry has not
  already reached.

## 7. Resolution flip — mid-M1 bar view vs quote-S5 (the number the daily test could not produce)

| grid | split | n | outcome-label flip fraction | mid-M1 PF / pts @0.45 | quote-S5 PF / pts @0.45 | mid-M1 WR → quote WR | TP count M1 → quote | SL count M1 → quote |
|---|---|---:|---:|---|---|---|---|---|
| H1-UTC | ALL | 3150 | **0.039** | 0.895 / −1026.2 | 0.724 / −2956.0 | 49.0 → 45.0 | 900 → 816 | 867 → 911 |
| H1-UTC | TRAIN | 1911 | 0.045 | 0.815 / −777.0 | 0.612 / −1837.2 | 48.0 → 43.0 | 555 → 499 | 536 → 573 |
| H1-UTC | TEST | 1239 | 0.031 | 0.955 / −249.2 | 0.813 / −1118.8 | 50.7 → 48.1 | 345 → 317 | 331 → 338 |
| H4-UTC | ALL | 758 | **0.022** | 0.906 / −445.8 | 0.803 / −983.0 | 49.1 → 46.2 | 215 → 205 | 226 → 228 |
| H4-UTC | TEST | 277 | 0.014 | 0.878 / −332.3 | 0.784 / −614.8 | 47.3 → 45.5 | 69 → 65 | 85 → 86 |
| H4-NY | ALL | 694 | **0.023** | 0.939 / −269.6 | 0.845 / −726.4 | 49.0 → 47.1 | 218 → 209 | 208 → 209 |
| H4-NY | TEST | 279 | 0.007 | 0.975 / −63.7 | 0.901 / −263.2 | 50.2 → 47.7 | 86 → 85 | 83 → 82 |

H1 crosstab (mid-M1 rows × quote-S5 columns): SL 861/4/2, TIME 32/1351/0, TP 18/68/814.
The flips are one-directional against the trader: M1 "TP"s that the bid never reached
become TIME (68) or SL (18), and M1 "TIME"s become SL (32); the reverse (quote better than
M1) happens 6 times in 3150. The label-flip fraction is small at these timeframes (2–4 %)
because a 7–16 pt stop dwarfs a 0.6 pt spread, but the points shift is a full spread per
trade: the bar view over-credits by 0.61 pts/trade on H1 (PF 0.895 vs 0.724) and by
0.70 / 0.66 pts/trade on H4. Even the bar view is negative on every grid and split, so the
flip does not change the verdict; it changes how badly the pattern loses. The mid-S5 model
sits within 0.02 PF of mid-M1 — intrabar ordering is not the issue at H1/H4, the quote is.

## 8. Does the daily result generalise?

No. Per-trade expectancy under the honest rule is −0.05 to −0.20 R at 0.45 cost on 694–3150
trades across three grids, opposite in sign to the daily test's +0.25 R; and the daily
figure itself, WR 60 % on n = 25, has a 95 % interval of 40.8–79.2 pp, which contains the
46–50 % intraday win rate — the daily sample cannot distinguish its result from the
intraday one, and the intraday sample cannot find its edge.

## 9. What this does and does not establish

Establishes: (i) mechanical CRT with a one-candle hold and a C1-extreme target has no
edge at H1 or H4 on either anchor over 24 months of XAU_USD, at any cost, with or without
the EMA bias gate; (ii) a random candle with the same geometry loses the same; (iii) the
bar-level view over-states the result by a spread per trade, and the loss is friction
against a gross edge of ≈ 0; (iv) the C2-wick delivery metric is geometry here too, and it
is the target/fill rule, not the entry timing, that removes it.

Does not establish: anything about multi-candle holds (the daily test's T = 3–5 days), about
targets other than C1's extreme, about D1 (n too small on 24 months), or about a gold bear
market (none in the window). Post-hoc cells not to be credited: 4 of 33 slot cells show
PF > 1 (H1 C3 at 07:00 UTC PF 1.40 n 154; H4-NY 21:00-ET slot PF 1.33 n 104; H4-UTC 00:00
PF 1.04 n 74 and 20:00 PF 1.07 n 128) against ~1.7 expected by chance at 5 %.

Power: the TEST MDE on win rate versus the control is ±2.8 pp (H1), ±5.9 pp (H4 grids),
±8.3–8.6 pp (bias arms). The measured real-minus-control WR differences (−3.0 to +1.6 pp)
are inside these floors everywhere, i.e. "no residual" is a powered null at H1 and an
unresolved one below ~6 pp at H4.

## 10. Deviations from protocol

None to the rule, arms, split, costs or controls. Two diagnostic columns were added to the
wick control after the first run, to answer the entry-vs-target question directly: the
"touch, C2-close entry" column (the vault entry as literally stated) and the spread-paid /
late-fill lines. Neither is a judged arm.

## 11. Next hypotheses (each needs its own pre-registration)

1. **Multi-candle hold at H4**: 40–45 % of trades expire at the C3 close with the target
   unreached; the daily test held 3–5 bars. Pre-register max hold = 3 candles with the same
   quote resolution and random control; the question is whether the extra time buys more
   than it pays in stop-outs.
2. **One slot, one test**: H1 C3 at 07:00 UTC (London open) and the H4-NY 21:00-ET slot
   (Asia open) are the only cells with PF > 1.3; a single pre-declared slot each, judged on
   TEST with the same random control, would settle whether that is session structure or two
   of the ~1.7 chance passes.
3. **Friction floor as a design constraint**: gross expectancy at mid is +0.15 pts at best
   against ≥ 1.1 pts of friction; any CRT variant worth testing must be shown to have a
   gross mid edge ≥ 2 pts/trade on TRAIN before its quote-level run is worth computing —
   larger candles (D1 on a longer history) or targets beyond C1's extreme, not tighter
   intraday grids.
