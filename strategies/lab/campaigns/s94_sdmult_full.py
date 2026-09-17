"""S94 _SD_MULT full-window confirmation -- the step REPORT_s94.md's Caveat demanded
before any live change, and which the 09-03 run could not afford (three attempts killed
mid-run; it settled for 2025-11-01..). Now a ~25 s arm.

Window: the campaign-standard 19.5 months, 2025-01-05..2026-08-12 (earliest start that
gives S94 its full 1500-bar M5 warm-up), TRAIN < 2026-02-01 <= TEST.
Arms: _SD_MULT in {2.0 (shipped), 2.5, 3.0, 3.5} x cost {0.45, 0.80 stress}.
Baseline reference (CAMPAIGN.md, same window): TRAIN 617 / PF 0.843, TEST 469 / PF 0.858."""
from lab.harness import Cfg
from lab.sweep import Arm

NAME = "s94_sdmult_full"
S = "s94_sweep_reversal"
W = dict(start="2025-01-05", end="2026-08-12", split="2026-02-01")
ARMS = [Arm(f"sd{sd}_c{c:.2f}", S, Cfg(cost_pts=c, patch={"_SD_MULT": sd}), **W)
        for sd in (2.0, 2.5, 3.0, 3.5) for c in (0.45, 0.80)]
