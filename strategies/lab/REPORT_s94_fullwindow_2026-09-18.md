# S94 `_SD_MULT` -- full-window confirmation (2026-09-18)

**Verdict: DO NOT SHIP.** Every `_SD_MULT` arm fails the pre-registered acceptance bars on
the full 19.5-month window. The 5-month result in `REPORT_s94.md` (TEST PF 1.151 / 1.154 at
2.5 / 3.0) did not generalise -- exactly the risk its own Caveat named. This closes the
question the 09-03 follow-up could only answer on a shortened window.

## What ran

`lab/campaigns/s94_sdmult_full.py` via `lab.sweep`: `_SD_MULT` in {2.0 (shipped), 2.5, 3.0,
3.5} x cost {0.45, 0.80 stress}, window 2025-01-05..2026-08-12 (full 1500-bar M5 warm-up),
TRAIN < 2026-02-01 <= TEST. Eight arms, 52 s wall-clock with 8 workers (each arm ~51 s; the
same arm took ~4.3 min before the 2026-09-18 kernel work and could not be completed at all
on 09-03). No code under test changed; the harness is the one in `lab/harness.py`.

Sanity: the shipped arm reproduces the CAMPAIGN.md baseline exactly -- TRAIN 617 / PF 0.843,
TEST 469 / PF 0.858, full -675.3 pts / PF 0.851.

## Results (points; TEST = 2026-02-01..2026-08-12, n floor 40)

| `_SD_MULT` | cost | TRAIN n | TRAIN pts | TRAIN PF | TEST n | TEST pts | TEST PF | TEST WR | TEST DD |
|---|---|---|---|---|---|---|---|---|---|
| 2.0 (shipped) | 0.45 | 617 | -354.7 | 0.843 | 469 | -320.6 | 0.858 | 22.4 | -467.8 |
| 2.0 | 0.80 | 617 | -570.6 | 0.764 | 469 | -484.7 | 0.797 | 22.2 | -602.5 |
| 2.5 | 0.45 | 617 | -185.4 | 0.921 | 469 | -33.0 | 0.986 | 18.8 | -353.0 |
| 2.5 | 0.80 | 617 | -401.4 | 0.841 | 469 | -197.1 | 0.920 | 18.6 | -460.4 |
| 3.0 | 0.45 | 617 | -45.5 | 0.981 | 469 | -27.6 | 0.989 | 15.8 | -358.3 |
| 3.0 | 0.80 | 617 | -261.5 | 0.899 | 469 | -191.7 | 0.925 | 15.6 | -447.4 |
| 3.5 | 0.45 | 618 | +138.6 | 1.057 | 471 | -600.5 | 0.771 | 11.9 | -624.5 |
| 3.5 | 0.80 | 618 | -77.7 | 0.970 | 471 | -765.4 | 0.724 | 11.7 | -765.6 |

## Against the five pre-registered bars (REPORT_s94.md, Dimension C)

1. **Improves TEST PF and points, and TEST PF > 1.0** -- 2.5 and 3.0 improve on baseline
   (0.858 -> 0.986 / 0.989; -320.6 -> -33.0 / -27.6 pts) but **neither clears 1.0**. FAIL.
2. **Survives 0.80 stress with PF > 1** -- 0.920 / 0.925. FAIL.
3. **Plateau, not a spike** -- 2.5 and 3.0 still land together (0.986 / 0.989), so the
   *direction* is real; 3.5 collapses (TEST 0.771 with TRAIN 1.057 -- the overfit signature).
   Passes as a shape, but of a below-breakeven effect.
4. **n unaffected** -- 1086 at 2.0 / 2.5 / 3.0; 3.5 admits 3 extra via `_MIN_RR`. PASS.
5. **Mechanism** -- WR falls monotonically (23.1 -> 19.7 -> 16.9 -> 14.5) while PF rises to
   3.0 then breaks: "fewer, bigger wins" saturates at 3.0. PASS, but it saturates short of
   breakeven.

Two of five fail, and they are the two that decide shipping.

## What it means

- Raising `_SD_MULT` to 2.5-3.0 is a genuine improvement over the shipped 2.0 (~+0.13 TEST PF,
  ~290 pts less loss over 6.5 months, smaller drawdown) -- it is just an improvement from
  clearly-losing to roughly-breakeven, and it goes back to losing at realistic cost. It is
  not a reason to change the live constant; it is a reason not to expect the live constant
  to make money either.
- The 09-03 partial-window numbers (TEST 0.986 / 0.989 on 2025-11..2026-08) are reproduced
  here to three decimals on the identical TEST half, so the earlier "DO NOT SHIP" stands
  and is now full-window.
- S94's live record over the last two months (analysis of 2026-07-18..09-17, prod DB) was
  +$351 on 63 trades, PF 1.28 -- carried entirely by the BUY side (+$484, PF 1.65) while
  SELL lost (-$133, PF 0.75). The harness has no side or regime dimension for S94 yet; a
  `long-only` / `above-SMA20` arm is the next question worth the 25 s, and it is a far more
  promising lever than any `_SD_MULT` value.

## Files

- campaign: `lab/campaigns/s94_sdmult_full.py`
- results: `lab/results/s94_sdmult_full/<arm>.json` + `.trades.parquet`
