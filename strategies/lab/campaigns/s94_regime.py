"""S94 regime-conditioned arms -- skill or beta? (follow-up to REPORT_s94_sides_2026-09-18)

Cfg.regime gates NEW entries on the newest CLOSED daily close vs its 20-day SMA (look-ahead
safe, see lab.harness.RegimeGate). If long-only S94's edge is sweep-reversal skill, the
below-SMA long arm should still hold up; if it is gold beta, it will lose. Shorts are run
in both regimes as the mirror. `control` (long, no regime) must reproduce
lab/results/s94_sides/long_sd3.0_c0.45 byte-for-byte."""
from lab.harness import Cfg
from lab.sweep import Arm

NAME = "s94_regime"
S = "s94_sweep_reversal"
W = dict(start="2025-01-05", end="2026-08-12", split="2026-02-01")


def arm(label, sides, sd, cost, regime=None):
    return Arm(label, S, Cfg(cost_pts=cost, sides=sides, regime=regime, patch={"_SD_MULT": sd}), **W)


ARMS = [
    arm("control_long_sd3.0_c0.45",  ("BUY",),  3.0, 0.45),
    arm("long_sd3.0_above_c0.45",    ("BUY",),  3.0, 0.45, "above_sma20"),
    arm("long_sd3.0_below_c0.45",    ("BUY",),  3.0, 0.45, "below_sma20"),
    arm("long_sd3.0_above_c0.80",    ("BUY",),  3.0, 0.80, "above_sma20"),
    arm("long_sd2.5_above_c0.45",    ("BUY",),  2.5, 0.45, "above_sma20"),
    arm("long_sd2.5_below_c0.45",    ("BUY",),  2.5, 0.45, "below_sma20"),
    arm("long_sd2.0_above_c0.45",    ("BUY",),  2.0, 0.45, "above_sma20"),
    arm("short_sd2.0_below_c0.45",   ("SELL",), 2.0, 0.45, "below_sma20"),
    arm("short_sd2.0_above_c0.45",   ("SELL",), 2.0, 0.45, "above_sma20"),
]
