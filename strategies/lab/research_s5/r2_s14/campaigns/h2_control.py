"""H2a control: concept-free random gate with FIRST_TOUCH's realised acceptance (0.254)."""
from r2_sweep import Arm, Cfg
NAME = "h1_h5"     # same results folder so score.py tabulates it with the H2 arms
W = dict(start="2024-09-01", end="2026-09-17", split="2025-12-01")
ARMS = [Arm("h2a_control_random0.254", "r2mod.s14v", Cfg(cost_pts=0.0, min_sl_dist_pts=3.0, closed_frames=True, patch={"RANDOM_GATE": 0.254}), **W)]
