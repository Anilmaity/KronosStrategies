# S94 regime-conditioned arms -- skill or beta? (2026-09-18)

**Verdict: the long-side edge is not only gold beta -- in the TEST half it earned +1.12 pts
per trade *below* the SMA20 while gold's forward returns were negative in both regimes.
The regime gate itself is not the lever (neither state beats unconditioned long-only, and
it halves the sample). The robust finding across every arm, half and regime is that S94
SHORTS lose; the shippable-looking change is long-only at `_SD_MULT` 2.5-3.0, with the
explicit caveat that the sample contains no bear market.**

## What ran

`lab/campaigns/s94_regime.py` via `lab.sweep` with the new harness gate `Cfg.regime`
(`lab.harness.RegimeGate`: newest CLOSED daily close vs 20-day SMA, midnight-UTC daily
bars, no look-ahead; `tests/test_harness_regime.py`). Full window 2025-01-05..2026-08-12,
TRAIN < 2026-02-01 <= TEST. Nine arms, 53 s. The control (long, no regime) reproduces
`s94_sides/long_sd3.0_c0.45` byte-for-byte.

## Results (points)

| arm | n | TRAIN n / PF / pts-per-trade | TEST n / PF / pts-per-trade | full pts / PF |
|---|---|---|---|---|
| control long sd3.0 | 598 | 355 / 1.046 | 243 / 1.132 | +258.8 / 1.087 |
| long sd3.0 **above** | 393 | 284 / 1.095 / +0.43 | 109 / 1.053 / +0.35 | +159.6 / 1.080 |
| long sd3.0 **below** | 200 | 66 / 0.685 / -1.25 | 134 / **1.214** / **+1.12** | +67.2 / 1.070 |
| long sd3.0 above @0.80 | 393 | 284 / 1.016 | 109 / 1.000 | +22.1 / 1.010 |
| long sd2.5 above | 393 | 284 / 0.987 | 109 / 0.972 | -35.9 / 0.982 |
| long sd2.5 below | 200 | 66 / 0.675 | 134 / 1.267 | +95.4 / 1.104 |
| long sd2.0 above | 393 | 284 / 0.871 | 109 / 0.792 | -297.9 / 0.842 |
| short sd2.0 **below** | 224 | 70 / 0.502 / -1.36 | 154 / 0.638 / -1.56 | -334.9 / 0.608 |
| short sd2.0 above | 257 | 185 / 1.003 / +0.01 | 72 / 0.909 / -0.35 | -23.6 / 0.972 |

## What gold itself did in each state (forward returns from the day's close)

| half | state | days | fwd 5d | fwd 10d | fwd-5d positive |
|---|---|---|---|---|---|
| TRAIN | below SMA20 | 73 | +1.57% | +2.85% | 75% |
| TRAIN | above SMA20 | 241 | +0.75% | +1.63% | 63% |
| TEST | below SMA20 | 100 | -0.23% | -0.57% | 46% |
| TEST | above SMA20 | 64 | -0.36% | -1.49% | 33% |

Whole window: gold 2,755 -> 4,372 (+59%); 305 of 478 days above the SMA20. "Below SMA20"
in TRAIN meant a dip inside a straight-line rally that recovered 75% of the time; in TEST it
meant a topping range with slightly negative drift either way.

## Reading

- **TRAIN half: consistent with beta.** Longs won above the SMA (+0.43/trade) where gold
  drifted up, and lost below it (-1.25/trade, n=66) even though gold's own dips recovered.
  Nothing here separates skill from drift.
- **TEST half: not explained by beta.** Gold's forward returns were negative in both
  states, yet longs earned +0.35/trade above and **+1.12/trade below** the SMA (n=134,
  PF 1.21). Buying swept lows inside a topping range paid without any help from drift.
  This is the same regime and the same behaviour as the live 2026-07-18..09-17 record
  (BUY PF 1.65 in a corrective range) -- and those live weeks are mostly out of the
  harness sample.
- **Regime gating is not the lever.** Above-only (+159.6) and below-only (+67.2) each
  underperform unconditioned long-only (+258.8) on points, TRAIN/TEST disagree on which
  state is better, and the below arm has 66 TRAIN trades. Do not add a regime gate.
- **Shorts are the robust finding.** Negative in both halves and both regimes
  (-1.36 to -1.56 pts/trade below the SMA; ~0 to -0.35 above), TEST PF 0.72 unconditioned,
  and negative live (-$133, PF 0.75). Nothing in nine arms or the previous eight makes S94
  shorts positive.

## Recommendation (for the operator; lab result, not a live change)

1. **Turn S94 shorts off.** Strongest, most robust result of the whole S94 campaign.
   Live path: a default-OFF env flag on the strategy (e.g. `S94_SIDES=BUY`) or a manager
   policy -- not a silent constant edit.
2. **With shorts off, `_SD_MULT` 2.5-3.0** clears all five pre-registered bars on the full
   window (TEST PF 1.116 / 1.132, stress 1.051 / 1.069, plateau, n unchanged). No basis to
   prefer one; 3.0 has the better numbers, 2.5 is the smaller step from the shipped 2.0.
3. **No regime gate.**
4. **Caveat that stays on the record:** 2025-26 contains no sustained bear market. The
   TEST-half evidence is for a topping *range*, not a downtrend. Long-only S94 in a real
   bear is untested, and the kill-switch / soft brake remain the only protection there.

## Files

- harness: `lab/harness.py` (`RegimeGate`, `Cfg.regime`, `Cfg.regime_sma`),
  `tests/test_harness_regime.py`; `lab.sweep` workers now load the `1d` frame
- campaign: `lab/campaigns/s94_regime.py`; results `lab/results/s94_regime/`
