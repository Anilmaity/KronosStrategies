"""Stage 2 of PROTOCOL_xau2y_2026-09-18.md -- the pre-declared grid, for the five Stage-1
survivors only: s97_snap_scalper_m5, s14_ob_mit_bias, c03_fvg_fill, s95_session_breakout,
s93_fvg_scalp. (s96 was NEAR on the monthly bar, which Stage 2 cannot address -> excluded.)

Grid per survivor, each at cost 0.45 and 0.80:
  gates : sides BUY | SELL; block_hours Asia(0-6) | London(7-11) | NY(12-20);
          regime above_sma20 | below_sma20
  const : ONE module constant at two values bracketing the shipped one --
          s97 _TP_FRAC {0.35, 0.7} (shipped 0.5); s93 _TP_R {1.0, 2.0} (1.5);
          s95 kronos_session_breakout._TP_MULT {0.6, 1.0} (0.8, via dotted patch).
          s14 and c03 have no module-level constant (multiples are inline) -> gate arms only.
Declared deviation (recorded before running): s97's HTF bias flips with the 15m window
length (n=0 at w15m 200/260, n=232 at 400 in the inventory smoke). Two robustness arms,
w15m 300 and 600, are added so a window-dependent result cannot pass unnoticed.

    LAB_BARS_CACHE=backtest/results/bars_cache_2y python -m lab.sweep lab/campaigns/xau2y_stage2.py --workers 12
    python -m lab.tools.campaign_score lab/results/xau2y_stage2 --split 2025-12-01 --cache backtest/results/bars_cache_2y
"""
from lab.harness import Cfg
from lab.sweep import Arm

NAME = "xau2y_stage2"
W = dict(start="2024-09-01", end="2026-09-17", split="2025-12-01")
COSTS = (0.45, 0.80)
ASIA, LONDON, NY = tuple(range(0, 7)), tuple(range(7, 12)), tuple(range(12, 21))

SURVIVORS = {  # module -> (window kwargs, constant arms as {suffix: patch})
    "s97_snap_scalper_m5":  ({"win_15m": 400}, {"tpfrac0.35": {"_TP_FRAC": 0.35}, "tpfrac0.7": {"_TP_FRAC": 0.7}}),
    "s14_ob_mit_bias":      ({}, {}),
    "c03_fvg_fill":         ({}, {}),
    "s95_session_breakout": ({"win_5m": 300}, {"tpmult0.6": {"backtest_strategies.kronos_session_breakout:_TP_MULT": 0.6},
                                               "tpmult1.0": {"backtest_strategies.kronos_session_breakout:_TP_MULT": 1.0}}),
    "s93_fvg_scalp":        ({}, {"tpr1.0": {"_TP_R": 1.0}, "tpr2.0": {"_TP_R": 2.0}}),
}

GATES = {
    "buy":        dict(sides=("BUY",)),
    "sell":       dict(sides=("SELL",)),
    "noasia":     dict(block_hours=ASIA),
    "nolondon":   dict(block_hours=LONDON),
    "nony":       dict(block_hours=NY),
    "above":      dict(regime="above_sma20"),
    "below":      dict(regime="below_sma20"),
}

ARMS = []
for mod, (win, consts) in SURVIVORS.items():
    for c in COSTS:
        for g, kw in GATES.items():
            ARMS.append(Arm(f"{mod}_{g}_c{c:.2f}", mod, Cfg(cost_pts=c, **win, **kw), **W))
        for sfx, patch in consts.items():
            ARMS.append(Arm(f"{mod}_{sfx}_c{c:.2f}", mod, Cfg(cost_pts=c, **win, patch=patch), **W))
# declared deviation: s97 window robustness
for c in COSTS:
    for w in (300, 600):
        ARMS.append(Arm(f"s97_snap_scalper_m5_w15m{w}_c{c:.2f}", "s97_snap_scalper_m5", Cfg(cost_pts=c, win_15m=w), **W))
