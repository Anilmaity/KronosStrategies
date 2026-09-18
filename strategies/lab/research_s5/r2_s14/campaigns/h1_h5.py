"""H1-H5 arms, pre-registered in PROTOCOL.md before this file ran. All closed frames, floor
3.0 unless the arm is the floor itself, cost 0 (costs applied by score.py). Incumbent for
every comparison = h0_closed_frames/s14_f3.0_c0.80_closed (same trades at cost 0.80)."""
from r2_sweep import Arm, Cfg

NAME = "h1_h5"
W = dict(start="2024-09-01", end="2026-09-17", split="2025-12-01")
V = "r2mod.s14v"


def C(floor=3.0, **kw):
    return Cfg(cost_pts=0.0, min_sl_dist_pts=floor, closed_frames=True, **kw)


ARMS = [
    # H1 floor extension
    Arm("h1_floor3.5", "s14_ob_mit_bias", C(3.5), **W),
    Arm("h1_floor4.0", "s14_ob_mit_bias", C(4.0), **W),
    Arm("h1_floor5.0", "s14_ob_mit_bias", C(5.0), **W),
    # H2 OB quality
    Arm("h2a_first_touch", V, C(patch={"FIRST_TOUCH": True}), **W),
    Arm("h2b_age12", V, C(patch={"MAX_AGE_BARS": 12}), **W),
    Arm("h2c_disp2.5", V, C(patch={"DISP_MULT": 2.5}), **W),
    Arm("h2d_body_zone", V, C(patch={"ZONE": "body"}), **W),
    # H3 protected-swing stop
    Arm("h3_swing5", V, C(patch={"SL_MODE": "swing", "SWING_N": 5}), **W),
    Arm("h3_swing3", V, C(patch={"SL_MODE": "swing", "SWING_N": 3}), **W),
    # H4 target
    Arm("h4_tp1.0", V, C(patch={"TP_R": 1.0}), **W),
    Arm("h4_tp1.5", V, C(patch={"TP_R": 1.5}), **W),
    Arm("h4_tp3.0", V, C(patch={"TP_R": 3.0}), **W),
    # H5 bias
    Arm("h5a_ema9", V, C(patch={"BIAS_LEN": 9}), **W),
    Arm("h5a_ema50", V, C(patch={"BIAS_LEN": 50}), **W),
    Arm("h5b_5m_ema21", V, C(patch={"BIAS_TF": "5m"}), **W),
    Arm("h5c_1h_ema21", V, C(patch={"BIAS_TF": "1h"}), **W),
    Arm("h5d_daily_align", "s14_ob_mit_bias", C(regime_align=True), **W),
]
