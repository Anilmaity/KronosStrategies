# c03 / s14 under the live trigger convention — full 24-month S5 resolution (2026-09-18)

Follow-up to `REPORT_xau2y_2026-09-18.md` § s97 disqualification, item 3 of its next steps.
That report's bid/ask haircut (−8 % c03, −9 % s14) was measured on 5.5 weeks of S5. With the
full S5 cache (`QA_S5_2026-09-18.md`: 8.68 M bars, 2024-08-15 → 2026-09-18, integrity PASS,
99.96 % minute-level agreement with the M1 cache) the same question is now answered on
**every trade of the Stage-1 arms**: 2,147 c03 and 3,970 s14 entries, unchanged; only the
exit is re-resolved. Tool `lab/s5exit.py` (`--start-offset 60`, `--split 2025-12-01`);
per-trade results in `lab/results/s5exit/`. Nothing below is hand-typed.

Three exit models, same trades: **mid_M1** (the harness: mid M1 bars, SL checked before TP),
**mid_S5** (mid S5 bars in true chronological order), **quote_S5** (a long's stop fires on
the BID, its target on the BID; a short's on the ASK — what the broker actually does).

## Result at 0.80 cost (ordinary operating cost; median spread 0.58–0.66 by hour)

| | c03 mid_M1 | c03 **quote_S5** | s14 mid_M1 | s14 **quote_S5** |
|---|---|---|---|---|
| ALL PF / pts (n) | 1.352 / 2190.9 (2147) | **1.248 / 1611.1** | 1.311 / 1993.6 (3970) | **1.114 / 792.4** |
| TRAIN PF / pts | 1.232 / 662.6 | **1.131 / 391.1** | 1.257 / 1028.7 | **1.057 / 248.5** |
| TEST PF / pts | 1.453 / 1528.3 | **1.348 / 1220.0** | 1.400 / 964.9 | **1.210 / 543.9** |
| WR | 48.0 % | 45.1 % | 51.2 % | 47.0 % |
| mid-model TPs that become stops | | 68 of 1,030 (6.6 %) | | 176 of 2,031 (8.7 %) |

At 0.45: c03 1.504 → 1.389 (TEST 1.564 → 1.451); s14 1.590 → 1.352 (TEST 1.672 → 1.445).

`mid_S5` ≈ `mid_M1` everywhere (c03 1.357, s14 1.314): the harness's SL-before-TP ordering
was already the right call at minute resolution. **The whole haircut is the trigger
geometry** — a stop that sits spread/2 closer to the entry than the mid-price model believes,
and a target spread/2 farther.

## Where it lands: the stop distance

Haircut in points at 0.80, by |entry − sl| bucket:

| stop | c03 n | c03 mid → quote | s14 n | s14 mid → quote |
|---|---|---|---|---|
| < 2 pt | 417 | 177.0 → 82.4 | **1,520** | **225.3 → −102.0** |
| 2–3 | 474 | 274.6 → 126.7 | 1,468 | 723.4 → 299.1 |
| 3–4 | 345 | 225.7 → 127.4 | 700 | 764.3 → 427.9 |
| 4–6 | 414 | 315.2 → 167.7 | 282 | 280.5 → 167.5 |
| 6+ | 497 | 1198.4 → 1106.9 | — | — |

**s14's sub-2-point stops — 38 % of its trades — are net losers under live triggers** (−102
pts) while showing +225 in the harness; every other bucket keeps roughly 40–60 % of its
mid-model profit. c03, whose median stop is 3.5 pt and whose 6+ bucket carries most of the
points, loses only 8 % on that bucket. The −8 % / −9 % from the 5-week sample understated
s14: on 24 months it is **−15 % of PF and −60 % of points at 0.80**.

## Verdict against the pre-registered bars

Both still clear bars 1–3 with live triggers at 0.80 on both halves (c03 TRAIN 1.131 /
TEST 1.348; s14 TRAIN 1.057 / TEST 1.210). c03's edge is robust to the execution model;
s14's is real but thin on the TRAIN half and entirely carried by its ≥ 2-point stops.

Not adopted, recorded as a hypothesis for a new pre-registered arm set: **a minimum stop
distance gate for s14 (≥ 2 pt)**. It is a subset selection made after seeing the result,
exactly what the protocol's bar 7 exists to guard against; it needs its own TRAIN-justified
grid (1.5 / 2.0 / 2.5) before anyone reads the −102 as a reason to ship it.

No trade of either strategy was open across the 2025-12-25 23:06 feed spike.

## Implication for the demo deployment (both live on Winprofx-Demo since 09-18)

The live parity measurement the xau2y report asked for is now bounded from below: expect
c03 ≈ 1.25 and s14 ≈ 1.1 PF at 0.80, not the harness's 1.35 / 1.31 — anything materially
worse than that in the live record is slippage, latency or the one-bar-late entry, not the
spread. `RISK_PER_TRADE_USD 38` sizing is unaffected; the kill-switch is not.
