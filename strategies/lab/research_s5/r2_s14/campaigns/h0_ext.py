"""H0-ext: mechanism split (M5-only / M15-only closed) and c03 blast radius. Pre-registered."""
from r2_sweep import Arm, Cfg
NAME = "h0_ext"
W = dict(start="2024-09-01", end="2026-09-17", split="2025-12-01")
ARMS = [
    Arm("s14_f3.0_m5closed_only", "s14_ob_mit_bias", Cfg(cost_pts=0.0, min_sl_dist_pts=3.0, closed_frames="m5"), **W),
    Arm("s14_f3.0_m15closed_only", "s14_ob_mit_bias", Cfg(cost_pts=0.0, min_sl_dist_pts=3.0, closed_frames="m15"), **W),
    Arm("c03_f1.5_closed", "c03_fvg_fill", Cfg(cost_pts=0.0, min_sl_dist_pts=1.5, closed_frames=True), **W),
]
