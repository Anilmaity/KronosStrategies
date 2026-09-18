"""Stage 1 of PROTOCOL_xau2y_2026-09-18.md RE-RUN on the closed-bar harness (fix 2026-09-18,
tests/test_harness_closed_bars.py) at the new cost convention (mid-M1 0.75 base / 1.00 stress,
lab/research_s5/execution). Same modules, windows, window and split as xau2y_stage1.py.
Every prior Stage-1/2/3 number is provisional until this replaces it.

    LAB_BARS_CACHE=backtest/results/bars_cache_2y python -m lab.sweep lab/campaigns/xau2y_stage1_fixed.py --workers 8
    python -m lab.tools.campaign_score lab/results/xau2y_stage1_fixed --split 2025-12-01 --cache backtest/results/bars_cache_2y
"""
from lab.harness import Cfg
from lab.sweep import Arm
from lab.campaigns.xau2y_stage1 import MODULES, W

NAME = "xau2y_stage1_fixed"
COSTS = (0.75, 1.00)
ARMS = [Arm(f"{m}_c{c:.2f}", m, Cfg(cost_pts=c, **kw), **W) for m, kw in MODULES.items() for c in COSTS]
