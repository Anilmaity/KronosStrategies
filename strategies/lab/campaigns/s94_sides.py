"""S94 directional arms -- the question the live record raised (2026-07-18..09-17 on prod:
BUY +$484 / PF 1.65 vs SELL -$133 / PF 0.75 over 63 trades).

Same full window and split as s94_sdmult_full. `control` (both sides, shipped _SD_MULT)
must reproduce lab/results/s94_sdmult_full/sd2.0_c0.45 exactly -- it proves Cfg.sides is a
no-op by default. Long-only is run at the shipped 2.0 and at the 2.5/3.0 plateau; short-only
at 2.0 only, as the counter-example."""
from lab.harness import Cfg
from lab.sweep import Arm

NAME = "s94_sides"
S = "s94_sweep_reversal"
W = dict(start="2025-01-05", end="2026-08-12", split="2026-02-01")
ARMS = [Arm("control_both_sd2.0_c0.45", S, Cfg(cost_pts=0.45, patch={"_SD_MULT": 2.0}), **W)]
ARMS += [Arm(f"long_sd{sd}_c{c:.2f}", S, Cfg(cost_pts=c, sides=("BUY",), patch={"_SD_MULT": sd}), **W)
         for sd in (2.0, 2.5, 3.0) for c in (0.45, 0.80)]
ARMS += [Arm("short_sd2.0_c0.45", S, Cfg(cost_pts=0.45, sides=("SELL",), patch={"_SD_MULT": 2.0}), **W)]
