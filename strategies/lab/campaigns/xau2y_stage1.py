"""Stage 1 of PROTOCOL_xau2y_2026-09-18.md -- screen every runnable ICT module at its
shipped configuration over the 24-month cache, at base and stress cost.

Run with the 2-year cache selected:
    LAB_BARS_CACHE=backtest/results/bars_cache_2y python -m lab.sweep lab/campaigns/xau2y_stage1.py
Score:
    python -m lab.tools.campaign_score lab/results/xau2y_stage1 --split 2025-12-01 \
        --cache backtest/results/bars_cache_2y

Windows per module come from STRATEGY_INVENTORY_2026-09-18.md (the roster four from
harness.WINDOWS; s95/s96/s97 need larger frames than the Cfg defaults or they silently
never trade). CONFIG session hours are applied by the harness exactly as research_runner
applies them."""
from lab.harness import Cfg
from lab.sweep import Arm

NAME = "xau2y_stage1"
W = dict(start="2024-09-01", end="2026-09-17", split="2025-12-01")
COSTS = (0.45, 0.80)

# module -> extra Cfg kwargs (window overrides only; everything else shipped defaults)
MODULES = {
    "s03_ob_mitigation":    {},
    "s04_breaker_block":    {},
    "s10_90min_fade":       {},
    "s11_m90_fade_ny":      {},
    "s12_m90_fade_bias":    {},
    "s14_ob_mit_bias":      {},
    "s93_fvg_scalp":        {},
    "s94_sweep_reversal":   {},
    "s95_session_breakout": {"win_5m": 300},
    "s96_h1_momentum":      {"win_15m": 320},
    "s97_snap_scalper_m5":  {"win_15m": 400},
    "s98_zscore_mr_m15":    {},
    "s99_mss_fvg":          {},
    "s100_m3_combo":        {},
    "c03_fvg_fill":         {},
}

ARMS = [Arm(f"{m}_c{c:.2f}", m, Cfg(cost_pts=c, **kw), **W)
        for m, kw in MODULES.items() for c in COSTS]
