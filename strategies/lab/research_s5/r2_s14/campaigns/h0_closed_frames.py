"""H0 -- harness fidelity: forming higher-TF bar. Pre-registered in PROTOCOL.md (H0) before
this file ran. Control: closed_frames=False must reproduce lab/results/s14_minstop
minsl3.0 exactly at 0.80 (n 1624, pts 1483.3, PF 1.408) -- run at 0.80 for that check, plus
the study's 0.75 / 1.00 convention for the closed-frames arm and its control."""
from r2_sweep import Arm, Cfg

NAME = "h0_closed_frames"
W = dict(start="2024-09-01", end="2026-09-17", split="2025-12-01")
ARMS = [
    Arm("s14_f3.0_c0.80_open",   "s14_ob_mit_bias", Cfg(cost_pts=0.80, min_sl_dist_pts=3.0, closed_frames=False), **W),
    Arm("s14_f3.0_c0.80_closed", "s14_ob_mit_bias", Cfg(cost_pts=0.80, min_sl_dist_pts=3.0, closed_frames=True), **W),
    Arm("s14_f3.0_c1.00_closed", "s14_ob_mit_bias", Cfg(cost_pts=1.00, min_sl_dist_pts=3.0, closed_frames=True), **W),
    Arm("s14_f1.5_c0.80_closed", "s14_ob_mit_bias", Cfg(cost_pts=0.80, min_sl_dist_pts=1.5, closed_frames=True), **W),
]
