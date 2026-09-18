"""Identity check: r2mod.s14v at defaults must equal s14_ob_mit_bias on a 2-month window."""
from r2_sweep import Arm, Cfg
NAME = "v_check"
W = dict(start="2026-03-01", end="2026-05-01", split="2026-04-01")
ARMS = [Arm("orig_closed", "s14_ob_mit_bias", Cfg(cost_pts=0.0, min_sl_dist_pts=3.0, closed_frames=True), **W),
        Arm("v_closed", "r2mod.s14v", Cfg(cost_pts=0.0, min_sl_dist_pts=3.0, closed_frames=True), **W)]
