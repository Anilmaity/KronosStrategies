"""S5 ambiguity resolver for the Manager Backtest worker.

The M1 replay cannot order TP and SL touches that land inside the same exit
bar; ``step_position`` checks SL first, which is conservative but can be
wrong. For exactly those trades this module re-walks the exit minute on S5
candles and settles which level was touched first, rewriting exit_px /
outcome / pnl with the SAME friction formula step_position uses.

Trades with outcome OPEN, TRAIL, or TIME are never touched (trailing exits
depend on a ratcheting stop level that an S5 walk of one minute cannot
reproduce; TIME exits by engine construction touched neither level in their
exit bar — see _is_ambiguous).
"""
from __future__ import annotations

from dataclasses import replace

import pandas as pd

from backtest.manager_sim_engine import SimConfig, TradeRecord


def _is_ambiguous(trade: TradeRecord, bar: pd.Series) -> bool:
    # Only TP/SL verdicts can be ambiguous. TIME exits are excluded by
    # construction: step_position checks SL then TP on the exit bar BEFORE the
    # time check, so a non-trailing TIME trade touched neither level in that
    # bar; the only trade that could reach TIME with a level touched is a
    # trailing one, which this resolver must never rewrite (its stop ratchets
    # intra-bar in ways one minute of S5 cannot reproduce).
    if trade.outcome not in ("TP", "SL"):
        return False
    is_buy = trade.side == "BUY"
    # Mirror step_position's touch predicates: SL strict (< / >), TP inclusive.
    if is_buy:
        sl_touch = bar["low"] < trade.sl
        tp_touch = bar["high"] >= trade.tp
    else:
        sl_touch = bar["high"] > trade.sl
        tp_touch = bar["low"] <= trade.tp
    return sl_touch and tp_touch


def _first_touch(trade: TradeRecord, s5: pd.DataFrame) -> str | None:
    """Walk S5 rows chronologically; return 'TP' or 'SL' for the first touch,
    None when neither level is touched on S5."""
    is_buy = trade.side == "BUY"
    for _, row in s5.iterrows():
        if is_buy:
            sl_touch = row["low"] < trade.sl
            tp_touch = row["high"] >= trade.tp
        else:
            sl_touch = row["high"] > trade.sl
            tp_touch = row["low"] <= trade.tp
        if sl_touch and tp_touch:
            return None          # still ambiguous even at S5: keep M1 verdict
        if sl_touch:
            return "SL"
        if tp_touch:
            return "TP"
    return None


def _rewrite(trade: TradeRecord, verdict: str, cfg: SimConfig) -> TradeRecord:
    friction = cfg.entry_friction_pts
    is_buy = trade.side == "BUY"
    level = trade.tp if verdict == "TP" else trade.sl
    exit_px = (level - friction) if is_buy else (level + friction)
    pnl_pts = (exit_px - trade.entry_px) if is_buy else (trade.entry_px - exit_px)
    return replace(trade, exit_px=exit_px, outcome=verdict,
                   pnl_pts=pnl_pts, pnl_usd=cfg.pts_to_usd(pnl_pts))


def resolve_ambiguous(trades: list[TradeRecord], m1: pd.DataFrame,
                      s5_provider, cfg: SimConfig,
                      ) -> tuple[list[TradeRecord], dict]:
    """Settle same-bar TP-vs-SL ambiguity on S5.

    Parameters
    ----------
    trades      : TradeRecords from the M1 replay.
    m1          : the replay's 1m frame (tz-aware UTC ``time`` column).
    s5_provider : Callable[[datetime, datetime], pd.DataFrame] returning S5
                  candles for a span (Task 5's ensure_s5, cache_dir applied).
    cfg         : the run's SimConfig (friction / usd conversion).
    """
    m1_by_time = m1.set_index("time")
    out: list[TradeRecord] = []
    n_ambiguous = n_flipped = n_unresolved = 0
    pnl_delta = 0.0

    for trade in trades:
        bar_ts = pd.Timestamp(trade.exit_time).floor("min")
        try:
            bar = m1_by_time.loc[bar_ts]
        except KeyError:
            out.append(trade)
            continue
        if not _is_ambiguous(trade, bar):
            out.append(trade)
            continue

        n_ambiguous += 1
        s5 = s5_provider(bar_ts.to_pydatetime(),
                         (bar_ts + pd.Timedelta(seconds=60)).to_pydatetime())
        verdict = None if (s5 is None or len(s5) == 0) else _first_touch(trade, s5)
        if verdict is None:
            n_unresolved += 1
            out.append(trade)
            continue
        if verdict == trade.outcome:
            out.append(trade)
            continue

        fixed = _rewrite(trade, verdict, cfg)
        n_flipped += 1
        pnl_delta += fixed.pnl_pts - trade.pnl_pts
        out.append(fixed)

    return out, {
        "n_ambiguous": n_ambiguous,
        "n_flipped": n_flipped,
        "n_unresolved": n_unresolved,
        "pnl_delta_pts": round(pnl_delta, 4),
    }
