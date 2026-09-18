"""s14 minimum-stop grid -- pre-registered 2026-09-18, written BEFORE any arm ran.

Hypothesis (from REPORT_s5exit_2026-09-18.md): s14's sub-2-point stops (38 % of its
trades, [1.5, 2) under the live 1.5 floor) are net negative once the stop is triggered on
the bid/ask; a higher MIN_SL_DIST_PTS floor should raise live-trigger PF without giving
up the points that the >= 2-pt stops earn.

Grid (fixed now): min_sl_dist_pts in {2.0, 2.5, 3.0}, costs 0.45 / 0.80. The Stage-1 arm
(floor 1.5, results/xau2y_stage1/s14_ob_mit_bias_c*.parquet) is the fourth grid point and
the baseline. The gate is applied the way live applies it -- the signal is REJECTED, so a
later signal can take the slot -- not as a subset filter on the saved trade list.

Judged under BOTH exit models, but the decision is taken under quote_S5 (live triggers),
by the Stage-2 bars of PROTOCOL_xau2y_2026-09-18.md:
  1-5  as Stage 1 (n >= 40, TEST PF > 1 at both costs, TRAIN PF > 0.9, monthly consistency,
       regime independence) -- computed by campaign_score on the harness (mid_M1) trades;
  6    plateau: the neighbouring floors move the same direction (no lone spike);
  7    beat the Stage-1 arm at 0.80 on quote_S5 TEST PF AND quote_S5 TEST points.
  +    TRAIN-justified: the floor chosen must be the best floor on TRAIN quote_S5 PF; TEST
       is read once, for that floor only.
Ship only if all hold. A floor that raises PF but loses points fails bar 7 -- the point
of this grid is to find out whether the [1.5, 2) trades are subtracting or just diluting.

    LAB_BARS_CACHE=backtest/results/bars_cache_2y python -m lab.sweep lab/campaigns/s14_minstop.py --workers 6
    for each arm: python -m lab.s5exit --trades <arm>.trades.parquet --cost <c> --max-hold 1000 --split 2025-12-01
"""
from lab.harness import Cfg
from lab.sweep import Arm

NAME = "s14_minstop"
W = dict(start="2024-09-01", end="2026-09-17", split="2025-12-01")
COSTS = (0.45, 0.80)
FLOORS = (2.0, 2.5, 3.0)

ARMS = [Arm(f"s14_ob_mit_bias_minsl{f}_c{c:.2f}", "s14_ob_mit_bias", Cfg(cost_pts=c, min_sl_dist_pts=f), **W)
        for f in FLOORS for c in COSTS]
