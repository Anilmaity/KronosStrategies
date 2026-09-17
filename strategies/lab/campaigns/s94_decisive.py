"""The six S94 arms of _s94_decisive.py (2026-09-03), as a sweep campaign.

Same window, same split, same Cfg per arm -- so lab/results/s94_decisive.log is the
sequential reference these results must reproduce exactly."""
from lab.harness import Cfg
from lab.sweep import Arm

NAME = "s94_decisive"
W = dict(start="2025-11-01", end="2026-08-12", split="2026-02-01")
S = "s94_sweep_reversal"
ARMS = [
    Arm("sd2.0_c0.45", S, Cfg(cost_pts=0.45, patch={"_SD_MULT": 2.0}), **W),
    Arm("sd2.5_c0.45", S, Cfg(cost_pts=0.45, patch={"_SD_MULT": 2.5}), **W),
    Arm("sd2.5_c0.80", S, Cfg(cost_pts=0.80, patch={"_SD_MULT": 2.5}), **W),
    Arm("sd3.0_c0.45", S, Cfg(cost_pts=0.45, patch={"_SD_MULT": 3.0}), **W),
    Arm("be1R_c0.45",  S, Cfg(cost_pts=0.45, be_at_r=1.0), **W),
    Arm("be1R_c0.80",  S, Cfg(cost_pts=0.80, be_at_r=1.0), **W),
]
