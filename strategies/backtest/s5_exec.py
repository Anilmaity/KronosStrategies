"""s5_exec.py — 5-second exit resolution for the fidelity backtest.

Phase 2 of docs/superpowers/specs/2026-08-12-5s-backtest-fidelity-design.md.

`manager_sim_engine.step_position` resolves a whole minute against one bar's
high/low, checking SL before TP. Any minute touching both levels is therefore
booked as a loss regardless of what actually happened — the dominant sim/live
divergence for strategies on 1.5-3 pt stops.

`walk_exit` replays the same position over the minute's S5 bars IN SEQUENCE, so
the outcome comes from the observed order. Ambiguity does not disappear, it
shrinks from 60 s to 5 s: when a single S5 bar touches both levels the
conservative SL-first choice is kept AND counted, so the residual is reported
rather than hidden.

Cycle 1 deliberately keeps step_position's mid-price trigger predicates and
symmetric friction, so a parity run can attribute any improvement to ordering
alone. Sided bid/ask fills are a separate, separately-measurable change.

Reuses SimPosition / TradeRecord from manager_sim_engine — the two engines must
never carry divergent copies of the position model.
"""
from __future__ import annotations

import logging
from dataclasses import replace

import pandas as pd

from backtest.manager_sim_engine import SimConfig, SimPosition, TradeRecord

log = logging.getLogger(__name__)


_ONE_MINUTE = pd.Timedelta("1min")


def slice_for_minute(s5: pd.DataFrame, s5_times: pd.Series,
                     now_ts: pd.Timestamp) -> pd.DataFrame | None:
    """The S5 bars covering [now_ts, now_ts + 60s), or None if there are none.

    `s5_times` is passed in so run_sim can hoist the Series out of its hot loop.
    Half-open on purpose: a bar stamped exactly at now+60s opens the NEXT minute
    and must not be replayed twice.
    """
    if s5 is None or len(s5) == 0:
        return None
    lo = int(s5_times.searchsorted(now_ts, side="left"))
    hi = int(s5_times.searchsorted(now_ts + _ONE_MINUTE, side="left"))
    if hi <= lo:
        return None
    return s5.iloc[lo:hi]


def _record(pos: SimPosition, sl: float, exit_px: float, exit_time,
            outcome: str, cfg: SimConfig) -> TradeRecord:
    is_buy = pos.side == "BUY"
    pnl_pts = (exit_px - pos.entry_px) if is_buy else (pos.entry_px - exit_px)
    return TradeRecord(
        strategy=pos.strategy,
        entry_time=pos.entry_time,
        side=pos.side,
        entry_px=pos.entry_px,
        sl=sl,
        tp=pos.tp,
        exit_px=exit_px,
        exit_time=exit_time,
        outcome=outcome,
        pnl_pts=pnl_pts,
        pnl_usd=cfg.pts_to_usd(pnl_pts),
        gate_reason="",
    )


def _bump(counter: dict | None, key: str) -> None:
    if counter is not None:
        counter[key] = counter.get(key, 0) + 1


def walk_exit(
    pos: SimPosition,
    s5_bars: pd.DataFrame,
    cfg: SimConfig,
    ambiguity: dict | None = None,
) -> tuple[SimPosition | None, TradeRecord | None]:
    """Advance an open position over a sequence of S5 bars.

    Per bar, in this order (mirrors step_position so only the resolution
    granularity changes):

      1. SL touch, against the PRE-ratchet stop
      2. TP touch (non-trailing positions only)
      3. TIME exit once elapsed >= max_hold_min, at that bar's close
      4. Trailing ratchet from this bar's high/low — affects LATER bars only

    Returns (updated_pos, None) while open, (None, TradeRecord) once closed.
    `ambiguity` accumulates diagnostic counts when supplied.
    """
    if s5_bars is None or len(s5_bars) == 0:
        return pos, None

    friction = cfg.exit_friction_pts
    is_buy = pos.side == "BUY"

    for bar in s5_bars.itertuples(index=False):
        pre_sl = pos.sl
        bar_time = pd.Timestamp(bar.time).to_pydatetime()

        sl_hit = (bar.l < pre_sl) if is_buy else (bar.h > pre_sl)
        tp_hit = False
        if not pos.trailing:
            tp_hit = (bar.h >= pos.tp) if is_buy else (bar.l <= pos.tp)

        # ── residual ambiguity: both levels inside ONE 5s bar ────────────────
        if sl_hit and tp_hit:
            _bump(ambiguity, "ambiguous_bars")

        # ── 1. SL ────────────────────────────────────────────────────────────
        if sl_hit:
            exit_px = (pre_sl - friction) if is_buy else (pre_sl + friction)
            outcome = "TRAIL" if pos.trailing else "SL"
            return None, _record(pos, pre_sl, exit_px, bar_time, outcome, cfg)

        # ── 2. TP ────────────────────────────────────────────────────────────
        if tp_hit:
            exit_px = (pos.tp - friction) if is_buy else (pos.tp + friction)
            return None, _record(pos, pos.sl, exit_px, bar_time, "TP", cfg)

        # ── 3. TIME ──────────────────────────────────────────────────────────
        if pos.max_hold_min is not None:
            elapsed_min = (bar_time - pos.entry_time).total_seconds() / 60.0
            if elapsed_min >= pos.max_hold_min:
                close = float(bar.c)
                exit_px = (close - friction) if is_buy else (close + friction)
                return None, _record(pos, pos.sl, exit_px, bar_time, "TIME", cfg)

        # ── 4. Trailing ratchet (next bar onwards) ───────────────────────────
        if pos.trailing:
            if is_buy:
                new_hwm = max(pos.hwm, float(bar.h))
                new_sl = max(pos.sl, new_hwm - pos.trail_dist)
            else:
                new_hwm = min(pos.hwm, float(bar.l))
                new_sl = min(pos.sl, new_hwm + pos.trail_dist)
            pos = replace(pos, hwm=new_hwm, sl=new_sl)

    return pos, None
