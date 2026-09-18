"""Honest re-run of the two live legs on the closed-bar harness (fix 1c9ae30) -- pre-registered
2026-09-19 before any arm ran. Costs: mid-M1 0.75 base / 1.00 stress; every arm is then
re-resolved on S5 bid/ask (lab.s5exit, 0.45/0.70), which is where the decision is taken.

S94 (live: S94_SIDES=BUY, S94_SD_MULT=2.5 since 09-18; every prior S94 campaign ran on the leak):
  grid  _SD_MULT {2.0, 2.5, 3.0} x sides {both, BUY}  (6 arms x 2 costs). Baseline = both/2.0
  (the honest Stage-1 arm, 0.812/0.771). Question: does the live config clear bars 1-5 honestly,
  and bar 7 vs the shipped baseline at stress cost on quote TEST PF AND points, TRAIN-justified.

S93 (live: _HOURS (13,14), _TP_R 1.5 -- the 09-01 hours change was also live-confirmed):
  grid  _HOURS {(13,14), (12,13,14), (7,8,9,12,13,14)=pre-09-01} x _TP_R {1.0, 1.5, 2.0}
  (9 arms x 2 costs). Incumbent = (13,14)/1.5 (honest Stage-1: NEAR 1.158/1.095, TRAIN 1.079,
  max month 79 % of TEST net). Question: is the shipped config its own honest optimum, and does
  anything clear the monthly bar. Selection on TRAIN quote PF; TEST read once.

Bars: PROTOCOL_xau2y_2026-09-18.md 1-7. Comparisons: 15 arms x 2 costs; expect ~1 spurious
single-bar pass. Ship nothing from a TEST-only shape.

    LAB_BARS_CACHE=backtest/results/bars_cache_2y python -m lab.sweep lab/campaigns/honest_s94_s93.py --workers 10
"""
from lab.harness import Cfg
from lab.sweep import Arm

NAME = "honest_s94_s93"
W = dict(start="2024-09-01", end="2026-09-17", split="2025-12-01")
COSTS = (0.75, 1.00)
ARMS = []
for sd in (2.0, 2.5, 3.0):
    for sides, tag in ((("BUY", "SELL"), "both"), (("BUY",), "long")):
        for c in COSTS:
            ARMS.append(Arm(f"s94_{tag}_sd{sd}_c{c:.2f}", "s94_sweep_reversal",
                            Cfg(cost_pts=c, sides=sides, patch={"_SD_MULT": sd}), **W))
for hours, htag in (((13, 14), "h13-14"), ((12, 13, 14), "h12-14"), ((7, 8, 9, 12, 13, 14), "hpre0901")):
    for tpr in (1.0, 1.5, 2.0):
        for c in COSTS:
            ARMS.append(Arm(f"s93_{htag}_tpr{tpr}_c{c:.2f}", "s93_fvg_scalp",
                            Cfg(cost_pts=c, patch={"_HOURS": hours, "_TP_R": tpr}), **W))
