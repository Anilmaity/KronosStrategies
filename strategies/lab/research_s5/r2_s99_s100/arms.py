"""Arm definitions for PROTOCOL.md §5 (S99) and §6 (S100). Fixed before any arm ran.

Every arm is ONE replay at mid-M1 cost 0.75 (1.00 derived exactly, see PROTOCOL §1); the two
baselines are additionally replayed at 1.00 as the proof of that derivation.
"""
from __future__ import annotations

from lab.harness import Cfg
from lab.sweep import Arm

from lab.research_s5.r2_s99_s100 import s99v

W = dict(start="2024-09-01", end="2026-09-17", split="2025-12-01")
V = "lab.research_s5.r2_s99_s100.s99v"            # dotted delegate for the variant knobs
S99, S100 = "s99_mss_fvg", "s100_m3_combo"


def _v(**knobs) -> dict:
    """Patch dict: swap the original get_signal for the variant and set its knobs."""
    p = {"get_signal": s99v.get_signal_v}
    p.update({f"{V}:{k}": v for k, v in knobs.items()})
    return p


S99_ARMS = [
    # baselines (incumbent), both costs replayed
    Arm("s99_base_c0.75", S99, Cfg(cost_pts=0.75), **W),
    Arm("s99_base_c1.00", S99, Cfg(cost_pts=1.00), **W),
    # identity controls: variant with neutral knobs; original with the wider M15 window
    Arm("s99_ident_c0.75", S99, Cfg(cost_pts=0.75, patch=_v()), **W),
    Arm("s99_base_w400_c0.75", S99, Cfg(cost_pts=0.75, win_15m=400), **W),
    # A. minimum FVG size (ATR units)
    *[Arm(f"s99_mingap{g}_c0.75", S99, Cfg(cost_pts=0.75, patch=_v(MIN_GAP_ATR=g)), **W)
      for g in (0.25, 0.5, 1.0)],
    # B. stop buffer (ATR units)
    *[Arm(f"s99_buf{b}_c0.75", S99, Cfg(cost_pts=0.75, patch=_v(BUF_ATR=b)), **W)
      for b in (0.1, 0.5, 1.0)],
    # C. HTF bias gate, two definitions, with / against
    Arm("s99_bias_with_ema21_c0.75", S99, Cfg(cost_pts=0.75, patch=_v(BIAS="with", BIAS_DEF="ema21_15m")), **W),
    Arm("s99_bias_against_ema21_c0.75", S99, Cfg(cost_pts=0.75, patch=_v(BIAS="against", BIAS_DEF="ema21_15m")), **W),
    Arm("s99_bias_with_h1_c0.75", S99, Cfg(cost_pts=0.75, win_15m=400, patch=_v(BIAS="with", BIAS_DEF="ema50_h1")), **W),
    Arm("s99_bias_against_h1_c0.75", S99, Cfg(cost_pts=0.75, win_15m=400, patch=_v(BIAS="against", BIAS_DEF="ema50_h1")), **W),
    # D. liquidity memory / E. retrace window (original module, constant patch)
    *[Arm(f"s99_sweepn{n}_c0.75", S99, Cfg(cost_pts=0.75, patch={"_SWEEP_N": n}), **W) for n in (24, 96)],
    *[Arm(f"s99_retrace{n}_c0.75", S99, Cfg(cost_pts=0.75, patch={"_RETRACE_W": n}), **W) for n in (12, 48)],
    # F. Stage-2 harness gates
    Arm("s99_buy_c0.75", S99, Cfg(cost_pts=0.75, sides=("BUY",)), **W),
    Arm("s99_sell_c0.75", S99, Cfg(cost_pts=0.75, sides=("SELL",)), **W),
    Arm("s99_above_c0.75", S99, Cfg(cost_pts=0.75, regime="above_sma20"), **W),
    Arm("s99_below_c0.75", S99, Cfg(cost_pts=0.75, regime="below_sma20"), **W),
    Arm("s99_nolondon_c0.75", S99, Cfg(cost_pts=0.75, block_hours=tuple(range(7, 12))), **W),
    Arm("s99_nony_c0.75", S99, Cfg(cost_pts=0.75, block_hours=tuple(range(12, 21))), **W),
]

_H = (1, 2, 3, 4, 5, 6, 7, 8, 13, 14, 15)
S100_ARMS = [
    Arm("s100_base_c0.75", S100, Cfg(cost_pts=0.75), **W),
    Arm("s100_base_c1.00", S100, Cfg(cost_pts=1.00), **W),
    # A. stop floor, live semantics (reject)
    *[Arm(f"s100_minsl{f}_c0.75", S100, Cfg(cost_pts=0.75, min_sl_dist_pts=f), **W) for f in (2.0, 2.5, 3.0)],
    # B. hours
    Arm("s100_hours_drop1_c0.75", S100, Cfg(cost_pts=0.75, patch={"_HOURS": tuple(h for h in _H if h not in (1,))}), **W),
    Arm("s100_hours_drop12_c0.75", S100, Cfg(cost_pts=0.75, patch={"_HOURS": tuple(h for h in _H if h not in (1, 2))}), **W),
    Arm("s100_hours_drop123_c0.75", S100, Cfg(cost_pts=0.75, patch={"_HOURS": tuple(h for h in _H if h not in (1, 2, 3))}), **W),
    Arm("s100_hours_nyonly_c0.75", S100, Cfg(cost_pts=0.75, patch={"_HOURS": (13, 14, 15)}), **W),
    Arm("s100_hours_asialondon_c0.75", S100, Cfg(cost_pts=0.75, patch={"_HOURS": (1, 2, 3, 4, 5, 6, 7, 8)}), **W),
    # C. ER gate (env), with its own control at the wider window
    Arm("s100_er_off_w1600_c0.75", S100, Cfg(cost_pts=0.75, win_1m=1600, env={"S100_ER_GATE": "off"}), **W),
    Arm("s100_er_ranging_w1600_c0.75", S100, Cfg(cost_pts=0.75, win_1m=1600, env={"S100_ER_GATE": "ranging"}), **W),
    Arm("s100_er_strict_w1600_c0.75", S100, Cfg(cost_pts=0.75, win_1m=1600, env={"S100_ER_GATE": "strict"}), **W),
    # D. TP multiple
    *[Arm(f"s100_tpr{r}_c0.75", S100, Cfg(cost_pts=0.75, patch={"_TP_R": r}), **W) for r in (2.0, 3.0)],
    # E. Stage-2 harness gates
    Arm("s100_buy_c0.75", S100, Cfg(cost_pts=0.75, sides=("BUY",)), **W),
    Arm("s100_sell_c0.75", S100, Cfg(cost_pts=0.75, sides=("SELL",)), **W),
    Arm("s100_above_c0.75", S100, Cfg(cost_pts=0.75, regime="above_sma20"), **W),
    Arm("s100_below_c0.75", S100, Cfg(cost_pts=0.75, regime="below_sma20"), **W),
]
