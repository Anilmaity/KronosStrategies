# S94 long-only -- directional arms (2026-09-18)

**Verdict: passes the pre-registered bars on paper; NOT shippable as a strategy change,
because the effect is mostly gold beta.** Long-only S94 at `_SD_MULT` 2.5-3.0 is the first
S94 configuration to clear TEST PF > 1 and the 0.80 stress on the full window -- but it
makes its money in the months gold rises and loses in the months gold falls. That is
directional exposure, not sweep-reversal skill, and the harness cannot tell the two apart
without a regime dimension.

## What ran

`lab/campaigns/s94_sides.py` via `lab.sweep` with the new harness gate `Cfg.sides` (a
modelable entry-side filter applied after `get_signal()`, like `block_hours`; default
`("BUY", "SELL")`, proven a no-op by the control arm reproducing
`s94_sdmult_full/sd2.0_c0.45` byte-for-byte). Full window 2025-01-05..2026-08-12,
TRAIN < 2026-02-01 <= TEST. Eight arms, 51 s.

Origin of the hypothesis: the live prod record 2026-07-18..09-17 (63 S94 trades, +$351,
PF 1.28) was entirely the BUY side (+$484, PF 1.65); SELL lost (-$133, PF 0.75). The
harness window ends 2026-08-12, so ~5 of the 8 live weeks are out of sample here.

## Results (points)

| arm | n | TRAIN n / PF | TEST n / PF | TEST pts | TEST DD | full pts / PF |
|---|---|---|---|---|---|---|
| control both, sd2.0 @0.45 | 1086 | 617 / 0.843 | 469 / 0.858 | -320.6 | -467.8 | -675.3 / 0.851 |
| long sd2.0 @0.45 | 598 | 355 / 0.839 | 243 / 0.958 | | | -291.1 / 0.895 |
| long sd2.0 @0.80 | 598 | 355 / 0.768 | 243 / 0.899 | | | -500.4 / 0.830 |
| long sd2.5 @0.45 | 598 | 355 / 0.958 | 243 / **1.116** | | -358.7 | +94.4 / 1.033 |
| long sd2.5 @0.80 | 598 | 355 / 0.883 | 243 / **1.051** | | | -114.9 / 0.962 |
| long sd3.0 @0.45 | 598 | 355 / **1.046** | 243 / **1.132** | | -393.8 | **+258.8 / 1.087** |
| long sd3.0 @0.80 | 598 | 355 / 0.968 | 243 / **1.069** | | | +49.5 / 1.016 |
| short sd2.0 @0.45 | 488 | 262 / 0.850 | 226 / 0.719 | | | -384.2 / 0.779 |

Shorts are the bleed: TEST PF 0.719, negative in 15 of 20 months. Removing them lifts every
`_SD_MULT` by ~0.10-0.15 TEST PF.

## Against the five bars (long, sd3.0)

1. TEST PF improves and > 1.0 -- 0.858 -> 1.132. PASS.
2. 0.80 stress > 1 -- 1.069. PASS (TRAIN half at 0.80 is 0.968: a yellow flag).
3. Plateau -- 2.5 / 3.0 land at 1.116 / 1.132. PASS.
4. n unaffected by `_SD_MULT` -- 598 across all long arms. PASS.
5. Mechanism -- WR falls (25.4 -> 22.2 -> 19.4) as PF rises: consistent with bigger wins.
   **But see below: the mechanism that explains the P&L is gold's direction.**

## Why it is not shippable as-is: monthly attribution vs gold

| | long sd3.0 @0.45 |
|---|---|
| positive months | 10 / 20 |
| gold-UP months positive | 9 / 15 (+525.1 pts total) |
| gold-DOWN months positive | 1 / 5 (-266.2 pts total) |
| corr(monthly pts, gold monthly %) | **0.51** (control: 0.63) |

The TEST half's PF 1.132 is three months: 2026-02 (+283, gold +11.3%), 2026-04 (+201) and
2026-07 (+123). 2026-03 (gold -12.8%) cost -194 and 2026-06 (gold -10.7%) cost -158.
Long-only S94 is, to first order, "buy the dip while gold is in a bull market". Over 2025-26
that was a fine trade; it is not evidence that the sweep-reversal logic has a long-side
edge, and the harness cannot separate the two without conditioning on regime.

## What would settle it (each ~25 s per arm now)

1. **Regime-conditioned long arm** -- admit BUY only when the daily close is above its
   20-day SMA (the state the 2026-09-17 market read used), and separately BUY only when
   *below* it. If the edge is skill, the below-SMA arm should still be >= 1; if it is beta,
   it will lose. Needs a `Cfg.regime` gate on the daily frame (harness has `1d` cached).
2. **Naive-long control** -- a random-entry long with S94's stop/TP geometry and n, to
   price the beta directly.
3. If (1) passes, the live path is not a code change to `s94_sweep_reversal.py` but a
   **manager policy**: S94 sits in the `trend` slot as `always_on`; moving it to a
   regime-gated policy (long-eligible only in an up-regime) is configuration the
   [[Strategy Manager]] already supports. Any strategy-side side filter would ship
   default-OFF per the opt15 discipline.

## Files

- harness: `lab/harness.py` (`Cfg.sides`), `tests/test_harness_sides.py`
- campaign: `lab/campaigns/s94_sides.py`; results `lab/results/s94_sides/`
