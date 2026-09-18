# REPORT — r2_new_edge: five leads from the S5 programme under quote-level execution (2026-09-18)

Folder `lab/research_s5/r2_new_edge/`. Pre-registration `PROTOCOL.md` (written 11:53 UTC,
before any hypothesis number; deviations logged in its §9 — none changed a rule). Scripts
`common.py`, `run_h1.py`, `run_h3.py`, `r2_s96_fixed.py` + `run_h2.py`, `run_h4.py`,
`r2_htf_gate.py` + `run_h5.py`. Raw outputs every number below is copied from:
`results/h1_output.txt`, `results/h1_diag_output.txt` (post-hoc, labelled),
`results/h3_output.txt`, `results/h2_output.txt`, `results/h2_diag_output.txt` (post-hoc),
`results/h4_output.txt`, `results/h5_output.txt`, `results/h5_run.log`; per-trade books
`results/*.parquet`, controls `results/*ctrl*.parquet`, campaign arms `results/h5_campaign/`.

Execution convention for every S5 number: fill at the next S5 bar's quote (long ask / short
bid), exits on the quote, stop before target within a bar, costs **0.45 / 0.70** (quote-S5) on
top; 0.80 printed for comparability with the six S5 studies. Harness numbers (H2, H5) are
mid-M1 at 0.75 / 1.00 and, for H2, re-resolved on quote-S5 (validated against
`lab.s5exit.resolve`: 150/150 outcomes and exit prices agree). Bars = xau2y bars 1–5
(n ≥ 40 TEST; TEST PF > 1 at both costs; TRAIN PF > 0.9; ≥ 55 % TEST months positive and no
month > 50 % of TEST net; regime). TRAIN < 2025-12-01 ≤ TEST. 527 UTC / 528 NY trading days.

## The one-table answer

| lead | hypothesis | judged arm: TEST PF @0.45 / 0.70 (n), pts @0.70 | TRAIN PF @0.70 | control | bars | verdict |
|---|---|---|---|---|---|---|
| **H1** | trade WITH the first London breach of the Asian range; stop = range mid, target 1× range, exit 20:55 UTC | **2.125 / 2.095 (72), +973** | 0.972 (146, −28) | C-PRE (no London): TEST 1.40, TRAIN 0.93; C-RAND: real at the 100th pct on TEST, 82nd on TRAIN | **PASS 6/6** | passes the bars; **not robust** — TRAIN −0.17 R/trade, TEST +0.31 R/trade, all of it 2026 shorts; the concept-removed control also passes on TEST |
| H2-A | s96 H1 momentum with the nominal-entry defect removed | 1.146 / 1.130 (269), +564 | 1.060 (492) | C-RAND (geometry base WR 73 %, PF 0.88): real at the 96–97th pct both halves | NEAR 5/6 | thin honest edge (+0.03 R/trade), **monthly bar fails**: 2026-01 = 10.4 R of an 8.2 R TEST book |
| H2-B | s96 stop-entry (M1 close through the Donchian level in the forming hour) | 1.181 / 1.165 (328), +904 | 1.137 (551) | — | NEAR 5/6 | same shape, 6/10 months, 2026-01 = 10.2 of 13.6 R |
| H3 | 10:00 ET release-range straddle (09:45–10:00 range, OCO at the edges, stop = opposite edge, 2× range, exit 12:00 ET) | 0.718 / 0.697 (205), −625 | 0.874 (319) | C-NOON 0.65 (loses more); C-RAND: real at the 14th pct on TEST, 86th on TRAIN | FAIL 3/6 | **FAIL** — the window has range and no direction |
| H4 | H4 CRT with a three-candle hold, 4 arms | best H4-NY bias 1.002 / 0.983 (121); others 0.73–0.93 | 0.84–0.99 | random C3′ with the same hold: inside the null band on every arm | FAIL 2–4/6 | **FAIL** — gross mid edge +0.08…+0.75 pts/trade < the 2.0-pt gate |
| H5 | TTrades daily-candle gate (G1 current / G2 previous day) on c03 and s14 | mid-M1 @1.00: c03 G1 1.415 (729) vs incumbent 1.411 (842); best PF gain +0.08 (s14 G2) | | incumbent at the same window (bar 7) | bars 1–5 pass, **bar 7 fails ×4** | **FAIL** — every gate gives up 11–39 % of TEST points for ≤ +0.08 PF |

Comparisons: 15 judged TEST reads declared (PROTOCOL §7), ~0.75 single-bar chance passes
expected. One event passed all six bars (H1 primary; its M = 0.5 secondary passed on the same
72 trades and is not a second event).

---

## 1. H1 — trade WITH the first London breach of the Asian range

**Rule (PROTOCOL §1).** Asian range = S5 mid H/L 00:00–06:59:55 UTC; first S5 bar in
07:00–09:59:55 whose mid high (low) exceeds the range; hold gate T ∈ {0, 30, 60} s of S5 closes
beyond the level; fill at the next bar's ask/bid; stop at the range midpoint; target
`AR_hi + M·range` (M ∈ {1.0, 0.5}); time exit 20:55 UTC; one trade per day, first side only.
Selection: T with the best TRAIN PF at 0.70 for M = 1.0 among T with TRAIN n ≥ 80.

**Events.** 527 days; 189 never breach the range in 07–10; of 338 breach days the T = 0
close condition rejects 120 (the breach bar closes back inside — the sub-spread poke the
sweeps study measured), leaving **218 trades** (TRAIN 146 / TEST 72). T = 30 leaves 97
(TRAIN 63) and T = 60 leaves 68 (TRAIN 46) — both below the pre-registered floor, so
**T = 0 was the only eligible cell** (PROTOCOL §9). Breach median 34.9 min after 07:00 (IQR
10–80); depth at the breach bar median 0.39 pt against a 0.60 spread (15.1 % ≥ 2 spreads);
Asian range median 26.6 pt → median risk 13.8, reward 25.5, RR 1.87; median hold 332 min;
outcomes SL 113 / TP 57 / TIME 48.

### Grid (TRAIN-selected) and judged arms
| T | M | n | TRAIN n | TRAIN PF @0.70 | TRAIN pts | TRAIN WR | TEST n | TEST PF @0.70 | TEST pts | status |
|---|---|---|---|---|---|---|---|---|---|---|
| **0** | **1.0** | 218 | 146 | **0.972** | −27.6 | 34.9 % | 72 | **2.095** | +973.0 | primary (judged) |
| 0 | 0.5 | 218 | 146 | 1.205 | +146.1 | 52.1 % | 72 | 1.417 | +344.6 | secondary (judged) |
| 30 | 1.0 | 97 | 63 | 1.521 | +192.7 | 46.0 % | 34 | 2.818 | +772.1 | not eligible (n) |
| 30 | 0.5 | 97 | 63 | 1.917 | +214.4 | 60.3 % | 34 | 1.698 | +268.9 | record |
| 60 | 1.0 | 68 | 46 | 1.666 | +182.6 | 47.8 % | 22 | 2.835 | +600.9 | not eligible (n) |
| 60 | 0.5 | 68 | 46 | 1.758 | +129.4 | 60.9 % | 22 | 1.504 | +149.5 | record |

| judged arm | split | n | PF @0.45 | PF @0.70 | PF @0.80 | pts @0.70 | WR | outcomes | bars |
|---|---|---|---|---|---|---|---|---|---|
| **primary T=0 M=1.0** | TRAIN | 146 | 1.009 | 0.972 | 0.958 | −27.6 | 34.9 % | SL 86 / TP 33 / TIME 27 | |
| | **TEST** | **72** | **2.125** | **2.095** | 2.084 | **+973.0** | 56.9 % | SL 27 / TP 24 / TIME 21 | **PASS 6/6** |
| secondary T=0 M=0.5 | TRAIN | 146 | 1.263 | 1.205 | 1.183 | +146.1 | 52.1 % | | |
| | TEST | 72 | 1.442 | 1.417 | 1.407 | +344.6 | 62.5 % | TP 38 / SL 24 / TIME 10 | PASS 6/6 |
| depth arm (T=0, M=1, depth ≥ 2 spreads) | TRAIN | 11 | 1.197 | 1.169 | 1.157 | +18.1 | 36.4 % | | |
| | TEST | 22 | 6.178 | 6.095 | 6.062 | +815.7 | 72.7 % | | NEAR 5/6 (n) |

Primary bars: n ✓ 72; TEST PF ✓✓; TRAIN PF ✓ (1.009 at the bar's base cost — it is 0.972 at
0.70); monthly ✓ (8/10 TEST months positive, largest month 42 % of net); regime ✓ (corr 0.09;
+554.7 in gold-up months, +445.2 in gold-down). TRAIN sd 16.3 pts/trade → TEST MDE 3.85
pts/trade against a TEST mean of +13.5.

### Controls
- **C-PRE** (concept removed: 00–04 range, first breach 04–07 UTC, same geometry, same exit):
  254 trades (111 days shared with the primary); TRAIN PF 0.961 / **0.925**, −78 pts; TEST
  **1.429 / 1.404**, +506 pts on 101; bars NEAR 5/6 (monthly fails: 5/10, max month 54 %).
  M = 0.5: TRAIN 0.72, TEST 1.25. **The rule without London also turns positive in TEST.**
- **C-RAND** (200 geometry-matched random books; same clock minute, ±30 d, same stop/target
  distances and exit offset): TRAIN null net mean −180 (p5 −490, p95 +150), PF mean 0.844,
  p95 1.152 → real TRAIN (−27.6, PF 0.972) at the **82nd** percentile. TEST null mean −123
  (p5 −619, p95 +348), PF mean 0.918, p95 1.351 → real TEST (+973, PF 2.095) at the
  **100th** percentile. Random entries with this geometry lose in both halves (WR 36–39 %
  against a 35 % break-even): the breakout direction carries information in TEST and not
  measurably in TRAIN.

### Pre-declared splits of the primary (PF / pts @0.70)
| split | TRAIN | TEST |
|---|---|---|
| BUY | n 80, 1.287, +133.3 | n 36, 1.251, +124.0 |
| SELL | n 66, 0.694, −160.9 | n 36, **3.158, +848.9** |
| D1 bias with / against | 68: 0.992, −3.7 / 74: 0.974, −13.5 | 40: 1.607, +347.2 / 32: 2.978, +625.8 |
| 2024 / 2025 / 2026 | 45: 0.645, −92.8 / 101: 1.089, +65.2 / — | — / 8: 0.724, −26.9 / **64: 2.264, +999.9** |

### Post-hoc anatomy (`results/h1_diag_output.txt`; labelled, no arm added)
- In R: TRAIN −24.8 R on 146 (**−0.17 R/trade**, median −1.05 R); TEST +22.3 R on 72
  (**+0.31 R/trade**). TP-rate among resolved trades 27.7 % (TRAIN) vs 47.1 % (TEST) against a
  ~35 % break-even at RR 1.87. In R the TEST mean is one MDE (0.29 R) above zero; in points
  it is 3.5 MDEs, because points weight 2026 (median Asian range 52.5 pt) ~4× 2024 (13.7).
- Year × side: 2024 BUY −7.6 / SELL −85.2; 2025 BUY +92.3 / SELL −54.0; **2026 BUY +172.6 /
  SELL +827.3 = +23.8 R of the +22.3 R TEST total**.
- Largest trade 2026-01-30 SELL (Asian range 387 pt, risk 193.6) +314 pts = 32 % of TEST net
  (1.6 R — an ordinary winner in R). TEST without the top 3: PF 1.49; top 5: 1.31. The
  2026-01-26 → 02-06 crash fortnight: 3 trades, +384 pts; ex-crash TEST PF 1.70 on 69.
- TEST months in R 8/10 positive; 2026-04 holds 9.7 of 22.3 R. Equal-risk monthly R over the
  full window: **11/25 months positive (TRAIN 3/15, TEST 8/10)** — the "one regime" reading in
  one number.

### What H1 establishes and does not
Established: the pre-registered rule passes every xau2y bar on its declared arm, beats
geometry-matched random entries on TEST (100th pct) and is uncorrelated with the book
(daily-R corr c03 +0.13, s14 +0.10, s93 +0.01; s95 +0.13). Not established: that this is an
edge rather than a 2026 regime — TRAIN is flat in points and negative in R, every TRAIN cell
at T = 0 loses, the profit is one year and one side, and the concept-removed control also
turns positive on TEST (5/6). The T = 30 / 60 cells (TRAIN PF 1.5–1.7, TEST 2.8) are where
the information appears to sit — the reclaimed poke vs the held breach — but they were
ineligible by the n floor and are reported, not credited. **Verdict: bars PASS; evidence
insufficient for a paper slot.** Next step in §Next.

---

## 2. H2 — s96 H1 momentum with the nominal-entry defect removed

**Defect (diagnostic recorded in PROTOCOL).** 237 of the 775 Stage-1 s96 entries fire off
the top of the hour with a nominal entry a mean **+3.69 pt** better than the market at
signal time (top-of-hour entries: +0.005 median). Mechanism: after an in-hour exit the
stateless module re-fires on the same closed H1 bar and the harness books the stale H1 close.

**Arms (PROTOCOL §2)**, harness-driven through `Cfg.patch={"get_signal": …}` on the real
module, xau2y bars, `win_15m=320`, cooldown 3600 s and one position as shipped:
- **A (corrected)**: same H1 Donchian(24)/EMA20/50/ATR(14) logic; entry = the newest M1
  close; SL/TP anchored to the H1 close as the module places them; one signal per closed H1
  bar. 762 entries (553 at :00; the remainder are the live-faithful re-fires after an in-hour
  exit, now priced at the market); median risk 37.2 pt; TP/risk median 0.377.
- **B (stop-entry)**: first M1 close through the Donchian extreme of the last 24 closed H1
  bars in the bias direction, during the forming hour; SL 3×ATR, TP 0.4R from the entry; one
  per hour. 879 entries.

| arm | resolution | split | n | PF @0.45 / 0.70 (quote) or @0.75 / 1.00 (mid) | pts | WR | outcomes | bars |
|---|---|---|---|---|---|---|---|---|
| Stage-1 s96 (defective) | mid-M1 | TRAIN / TEST | 500 / 275 | 1.212 / 1.176 · **1.343 / 1.326** | +755 · +1444 | 76 / 78 % | | |
| Stage-1 s96 (defective) | quote-S5 | TRAIN / TEST | 500 / 274 | 1.220 / 1.184 · **1.271 / 1.254** | +667 · +1118 | 76 / 77 % | TP 595 / SL 179 | NEAR 5/6 (5/10 months) |
| **A corrected** | mid-M1 | TRAIN / TEST | 492 / 270 | 1.087 / 1.051 · 1.220 / 1.202 | +305 · +902 | 76 / 78 % | | |
| **A corrected** | **quote-S5** | TRAIN | 492 | **1.095 / 1.060** | +216.5 | 75.4 % | TP 375 / SL 117 | |
| | | **TEST** | **269** | **1.146 / 1.130** | +563.6 | 77.0 % | TP 209 / SL 60 | **NEAR 5/6** — monthly ✗ (4/10, max month 145 % of net) |
| B stop-entry | mid-M1 | TRAIN / TEST | 551 / 328 | 1.183 / 1.146 · 1.229 / 1.212 | +704 · +1199 | 76 / 77 % | | |
| B stop-entry | quote-S5 | TRAIN | 551 | 1.173 / 1.137 | +545.4 | 75.1 % | | |
| | | TEST | 328 | 1.181 / 1.165 | +903.8 | 75.3 % | TP 251 / SL 77 | NEAR 5/6 — monthly ✗ (5/10, max month 105 %) |

Quote-vs-mid haircut 0.57 pts/trade on all three books (outcome flips 0.8–1.3 %). The defect
was worth **0.12 PF on TEST at quote-S5 (1.254 → 1.130)** and 0.12 on TRAIN — the execution
study's model (1.34 → 1.19 on mid-M1) is confirmed in direction and size.

**Control (C-RAND, arm A, 200 books, 14-day horizon):** the 3×ATR-stop / 0.4R-target geometry
alone wins 72.5–73.0 % of random entries with PF 0.877 (TRAIN) / 0.876 (TEST) at 0.70; the
real arm (75.4 % / 77.0 %, PF 1.060 / 1.130) sits at the **97th (TRAIN) and 96th (TEST)**
percentile — a real, small breakout-direction effect beyond the geometry's high win rate.
TEST MDE 2.38 pts/trade vs a TEST mean of +2.1 pts/trade: at the detection floor.

**Splits (@0.70):** BUY TRAIN 1.278 / TEST 1.175; SELL TRAIN 0.767 / TEST 1.090; hours 00–06
TRAIN 1.437 / TEST 0.969, 07–15 1.105 / 1.379, 16–23 0.729 / 1.105 — nothing consistent
across halves except the long side. **Post-hoc (`h2_diag_output.txt`):** TEST +8.2 R on 269
(+0.03 R/trade; TRAIN +0.002 R/trade); 2026-01 = +10.4 R, i.e. the other nine months net
−2.2 R; the three largest TEST trades are 2026-01-29/30 (risk 125–294 pt). Arm B: 6/10 months,
2026-01 = 10.2 of 13.6 R. Daily-R corr with the book: c03 +0.17, s14 +0.06, s93 +0.01.

**Verdict.** The honest s96 is a positive-expectancy H1 momentum rule at quote-level
execution (TEST PF 1.13–1.17 at 0.70, above its geometry control), but its expectancy is
+0.03 R/trade, its TEST book is one month (the January 2026 crash), and it fails the monthly
bar on both arms — the same shape that made the operator retire it in July 2026 ("+$30 in
three months at 74 % WR"). The stop-entry makes the fill real without changing the picture.
**NEAR, not a candidate.** The Stage-1 s96 row in `REPORT_xau2y` should be read as PF 1.13
(quote-S5, corrected), not 1.34.

---

## 3. H3 — 10:00 ET release-range straddle

**Rule (PROTOCOL §3).** Range = S5 mid H/L 09:45–09:59:55 ET (DST per timestamp); from
10:00 ET, long at the first ask above the range high / short at the first bid below the low
(OCO, first to trigger, one trade per day) until 11:09:55; stop = opposite edge; target
M × range (M ∈ {1, 2}); time exit 12:00 ET. 528 NY days → 523–524 trades; median range 9.8
pt, risk 10.3, fill slip beyond the edge 0.33 pt (p90 1.69), spread at fill 0.66; 46 % of
triggers occur inside the 10:00:00–10:00:55 minute; median hold 60 min.

| arm | split | n | PF @0.45 | PF @0.70 | pts @0.70 | WR | outcomes | bars |
|---|---|---|---|---|---|---|---|---|
| M=1 (grid) | TRAIN | 318 | — | 0.695 | −391.5 | 46.9 % | | |
| **M=2 (selected)** | TRAIN | 319 | 0.928 | 0.874 | −177.6 | 40.8 % | SL 138 / TIME 122 / TP 59 | |
| | **TEST** | **205** | **0.718** | **0.697** | −624.7 | 36.1 % | SL 107 / TIME 71 / TP 27 | **FAIL 3/6** (2/10 months +) |
| range 09:30–10:00, M=2 | TRAIN | 304 | 0.896 | 0.846 | −221.2 | 45.4 % | | |
| | TEST | 199 | 0.718 | 0.698 | −635.8 | 41.7 % | TIME 128 / SL 61 / TP 10 | FAIL 1/6 |
| M=1 (record) | TEST | 205 | — | 0.663 | −637.5 | | | |

Controls: **C-NOON** (identical rule at 12:00 ET; range median 5.7 pt) TRAIN 0.617, TEST
0.653 — worse than the release window, so 10:00 is *less bad* than a dead hour, not good.
**C-RAND** (200 books): TRAIN real at the 86th percentile (null PF mean 0.744, p95 0.929 vs
real 0.874); TEST real at the **14th** (null mean 0.823, p95 1.050 vs real 0.697). The sign
of the residual flips between halves: no directional information either way. Splits: both
sides lose on TEST (BUY 0.555, SELL 0.827); 10:00-minute triggers lose in both halves
(0.752 / 0.728), later triggers flip (1.022 / 0.675); 2026 PF 0.68 on 183. Daily-R corr
with the book ≤ 0.11.

**Verdict: FAIL.** The 09:50–11:10 window concentrates range, as the killzones study
measured, and the first breakout of the pre-release range does not carry its direction:
47 % stop at the opposite edge, 37 % expire, 16 % reach 2× range. The NFP-style straddle from
the event-driven skill does not transfer to the daily 10:00 ET releases on gold at this
geometry.

---

## 4. H4 — H4 CRT with a three-candle hold

**Rule (PROTOCOL §4).** `crt_lib` events, fills, stops and targets unchanged; exit horizon =
end of candle C5 (i2 + 3) instead of C3. Gate: TRAIN gross mid-M1 expectancy ≥ +2.0
pts/trade. H4-UTC 713 trades (74 events lost to a non-consecutive C5), H4-NY 633.

| arm | TRAIN gross mid (pts/trade) | gate | TRAIN n / PF @0.70 | TEST n / PF @0.45 / @0.70 / pts | outcomes | bars | random C3′, share of 20 books with PF ≥ real (TRAIN / TEST @0.70) |
|---|---|---|---|---|---|---|---|
| H4-UTC nofilt | +0.178 | FAIL | 449 / 0.850 | 264 / 0.740 / 0.726 / −1085.7 | SL 324 / TP 286 / TIME 103 | 2/6 | 0.25 / 0.90 |
| H4-UTC bias | +0.083 | FAIL | 220 / 0.837 | 133 / 0.825 / 0.810 / −367.2 | | 2/6 | 0.60 / 0.75 |
| H4-NY nofilt | +0.254 | FAIL | 377 / 0.865 | 256 / 0.926 / 0.907 / −312.1 | SL 285 / TP 278 / TIME 70 | 3/6 | 0.20 / 0.30 |
| H4-NY bias | +0.754 | FAIL | 198 / 0.986 | 121 / 1.002 / 0.983 / −27.8 | | 4/6 | 0.30 / 0.45 |

The longer hold does what it was meant to — TIME exits fall from 40–45 % to 11–14 % and the
TP rate rises from 27–30 % to 40–44 % — but the SL rate rises as much (45 %), so gross
expectancy stays at +0.1 to +0.8 pts/trade against ≈ 1.1–1.2 pts of friction. Every arm fails
the gate and the bars; the random-C3′ control with the same hold sits in the same band.
**Verdict: FAIL; the multi-candle-hold lead is closed for H4.**

---

## 5. H5 — the TTrades daily-candle gate on c03 / s14

**Rule (PROTOCOL §5).** Wrapper around the real `get_signal` (`r2_htf_gate.py`, via
`Cfg.patch`): **G1** admits a signal only when its side agrees with the current UTC daily
candle so far (newest M1 close vs the day's open); **G2** only when it agrees with the
previous UTC day's candle. Both strategies and the incumbent re-run at `win_15m=200`
(the incumbent at the same window is the bar-7 reference); mid-M1 at 0.75 / 1.00 on the
xau2y cache, 2024-09-01 → 2026-09-17. Smoke test (one month of c03): incumbent 90 trades,
G1 keeps 83, G2 70. Full run: `results/h5_campaign/r2_h5_htf_gate/`, scored by
`run_h5.py --score` → `results/h5_output.txt`, `results/h5_table.csv`.

| arm | TRAIN n | TRAIN PF @0.75 / 1.00 | TRAIN pts @1.00 | TEST n | TEST PF @0.75 / 1.00 | TEST pts @1.00 | TEST WR | bars 1–5 | bar 7 vs incumbent (TEST PF and pts @1.00) | TRAIN-justified |
|---|---|---|---|---|---|---|---|---|---|---|
| c03 incumbent | 1320 | 1.248 / 1.123 | +367.7 | 842 | 1.487 / 1.411 | +1441.5 | 48.0 % | PASS 6/6 | — | — |
| c03 G1 (current day) | 1195 | 1.315 / 1.183 | +480.4 | 729 | 1.489 / 1.415 | +1280.2 | 48.4 % | PASS 6/6 | PF +0.004, **pts −161 → FAIL** | yes (TRAIN PF and pts both up) |
| c03 G2 (previous day) | 723 | 1.181 / 1.057 | +91.2 | 532 | 1.539 / 1.463 | +1040.1 | 48.9 % | PASS 6/6 | PF +0.052, **pts −401 → FAIL** | no (TRAIN worse on both) |
| s14 incumbent | 2549 | 1.294 / 1.122 | +518.9 | 1421 | 1.435 / 1.267 | +680.7 | 52.2 % | PASS 6/6 | — | — |
| s14 G1 | 1845 | 1.306 / 1.130 | +393.7 | 953 | 1.346 / 1.187 | +328.7 | 50.8 % | PASS 6/6 | **PF −0.080, pts −352 → FAIL** | no |
| s14 G2 | 1328 | 1.211 / 1.049 | +112.3 | 763 | 1.520 / 1.343 | +456.9 | 54.4 % | PASS 6/6 | PF +0.076, **pts −224 → FAIL** | no |

(The incumbent numbers at `win_15m=200` reproduce Stage 1 within a few trades — c03 TEST
842 vs 838, s14 1421 vs 1421 — so the window change is not doing anything.)

**Verdict: FAIL on bar 7, all four arms.** The daily-candle gate removes 13–45 % of the
trades and, at best, leaves the profit factor where it was (c03 G1: +0.004 on TEST) while
giving up 11–39 % of the TEST points; on s14 the current-day gate even lowers PF (1.267 →
1.187). Only c03 G1 is TRAIN-justified on PF, and it is exactly the "quality-improving,
profit-reducing subset" the xau2y protocol's bar 7 exists to reject. **The corpus's
governing rule — no entry against the daily candle — does not add information to c03 or s14
beyond what their own bias filters (H1 EMA slope, M15 EMA21) already carry.** The July
2026 drawdown's counter-HTF story does not generalise to these two strategies' 24-month
books. Bar 6 (plateau) is n/a (no numeric parameter); quote-S5 was not run (no arm passed
bar 7).

---

## What this study establishes, and what it does not

1. **Of the six leads the S5 programme left, none is a deployable edge at quote-level
   execution on 24 months.** Three are closed with working controls (H3 straddle, H4
   multi-candle CRT, H5 daily-candle gate: bar 7 fails on all four arms). Two are "positive but not an edge": the
   honest s96 (H2) is a +0.03 R/trade momentum rule whose whole TEST book is January 2026,
   and the London continuation (H1) passes every bar on a 2026-short-side profit the
   concept-removed control shares.
2. **H1 is the one result that deserves a second, independent look**, precisely because it
   passed: the hold-gated cells (T = 30 / 60 s) are positive in *both* halves (TRAIN PF
   1.5–1.7 on 46–63 trades) and are the only place in this study where a TRAIN signal exists;
   they were ineligible by the pre-registered n floor. The claim to test is "a London breach
   that holds ≥ 30 s continues; one that is reclaimed within 30 s does not" — the sweeps
   study's reclaim-speed anatomy in trade form.
3. **The s96 fidelity defect is measured, not modelled**: 0.12 PF on TEST at quote-S5; the
   corrected module still beats its geometry control (96–97th pct), so the retired strategy
   has a real but tiny edge, and its retirement stands.
4. **Two methodological points**, both already in the vault's traps note and both bitten again
   here: (a) points-denominated bars over a window in which the instrument's range quadrupled
   make one year decide the verdict (H1: 2026 = 100 % of the TEST R); the R-unit view should
   accompany every points verdict; (b) the concept-removed control is the one that matters —
   H1's C-PRE (no London) passed 5/6 on TEST, which is what says "regime", while the random
   control alone would have said "edge".
5. Not established: anything about a gold bear market (none in the window); tick-level
   slippage or latency (S5 closes bound both); anything about the hold-gated H1 cells beyond
   their being reported.

## Next hypotheses (each needs its own pre-registration)

1. **H1 hold-gated continuation on the 3-year M1 cache** (`50 Research/XAUUSD Data
   Inventory.md`): T = 30 s (or the M1 equivalent: the breach minute closes beyond the level),
   M = 1.0, range-mid stop, exit 20:55; TRAIN n ≥ 80 required; C-PRE and C-RAND as here; judge
   in R and in points; a pass requires TRAIN PF > 1 in R as well as the bars. If it holds on
   2023–2024 data (before the 2026 regime), it is a paper-slot candidate.
2. (Done post-hoc, above: equal-risk monthly R is positive in 11/25 months, 3/15 on TRAIN —
   the one-regime objection is confirmed, not weakened.)
3. **s96 with a monthly-consistency stop**: the honest arm's expectancy is one month's; test
   whether a volatility-scaled TP (0.4R is 1.2×ATR — try 0.8R with the same stop, TRAIN-
   selected) turns the 76 % win rate into a book that is positive in most months, judged on
   quote-S5. Low prior; cheap (one harness arm).
