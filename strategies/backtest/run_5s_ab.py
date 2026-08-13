"""run_5s_ab.py — A/B the sim's exit resolution: 1-minute vs 5-second.

Spec: docs/superpowers/specs/2026-08-12-5s-backtest-fidelity-design.md

Runs the SAME strategies over the SAME period twice, changing only
`cfg.exec_resolution`, and diffs the resulting trades. Everything else — signal
generation, gates, friction, sizing — is held constant, so any difference is
attributable to intrabar ordering alone: the M1 engine checks SL before TP
against one bar's high/low, while the 5s engine replays the minute in sequence.

5s bars come from the local 1-second tick cache (.history_data), which is finer
than OANDA S5 and matches live's 1-second position_manager loop.

Usage (from strategies/):
    python -m backtest.run_5s_ab --start 2026-04-01 --end 2026-04-30
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from backtest import tick_s5
from backtest.manager_sim_engine import (
    LIVE_WINDOWS, SimConfig, StratSpec, load_frames, run_sim,
)
from backtest import parity_harness as ph
from backtest_strategies import (
    s93_fvg_scalp, s94_sweep_reversal, s99_mss_fvg, s100_m3_combo,
)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("run_5s_ab")

_HERE = Path(__file__).resolve().parent
CACHE_DIR = _HERE / "results" / "bars_cache"
TICK_DIR = _HERE.parents[1] / ".history_data" / "XAU_USD"
OUT_DIR = _HERE / "results" / "parity"

# The live roster (Winprofx-Demo, all always_on per their ManagedStrategy rows),
# each carrying the lookback depth its live service is configured with. Without
# these the sim gave S94 300 M5 bars against live's 1500 and generated a
# different signal set entirely (2026-08-13 fidelity fix).
ROSTER = [
    StratSpec(s93_fvg_scalp.NAME, s93_fvg_scalp, "always_on", {},
              **LIVE_WINDOWS.get(s93_fvg_scalp.NAME, {})),
    StratSpec(s94_sweep_reversal.NAME, s94_sweep_reversal, "always_on", {},
              **LIVE_WINDOWS.get(s94_sweep_reversal.NAME, {})),
    StratSpec(s99_mss_fvg.NAME, s99_mss_fvg, "always_on", {},
              **LIVE_WINDOWS.get(s99_mss_fvg.NAME, {})),
    StratSpec(s100_m3_combo.NAME, s100_m3_combo, "always_on", {},
              **LIVE_WINDOWS.get(s100_m3_combo.NAME, {})),
]


def _to_sim_trades(trades) -> list[ph.SimTrade]:
    return [ph.SimTrade(strategy=t.strategy, side=t.side,
                        entry_time=t.entry_time, entry_px=t.entry_px,
                        exit_time=t.exit_time, exit_px=t.exit_px,
                        outcome=t.outcome, usd=t.pnl_usd) for t in trades]


def _to_live_shaped(trades) -> list[ph.LiveTrade]:
    """Reuse the parity matcher for a sim-vs-sim diff by casting one side."""
    return [ph.LiveTrade(strategy=t.strategy, side=t.side,
                         entry_time=t.entry_time, entry_px=t.entry_px,
                         exit_time=t.exit_time, exit_px=t.exit_px,
                         outcome=t.outcome, usd=t.pnl_usd, ticket="")
            for t in trades]


def _stats(trades) -> dict:
    closed = [t for t in trades if t.outcome != "OPEN"]
    wins = [t for t in closed if t.pnl_pts > 0]
    losses = [t for t in closed if t.pnl_pts <= 0]
    gross_w = sum(t.pnl_pts for t in wins)
    gross_l = abs(sum(t.pnl_pts for t in losses))
    return {
        "trades": len(closed),
        "wins": len(wins),
        "win_rate": (len(wins) / len(closed)) if closed else 0.0,
        "points": sum(t.pnl_pts for t in closed),
        "usd": sum(t.pnl_usd for t in closed),
        "pf": (gross_w / gross_l) if gross_l else float("inf"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--lots", type=float, default=0.10,
                    help="live roster sizes at 0.10 (multiplyer=1)")
    ap.add_argument("--spread", type=float, default=0.30,
                    help="modelled spread in points; the sim's historical "
                         "default is 0.30 but 0.76 was measured live on "
                         "2026-08-12")
    ap.add_argument("--slippage", type=float, default=0.10)
    ap.add_argument("--arms", choices=["both", "5s"], default="both",
                    help="'5s' skips the 1m control arm (halves runtime) once "
                         "the A/B question is already answered")
    ap.add_argument("--tick-dir", default=str(TICK_DIR))
    ap.add_argument("--cache-dir", default=str(CACHE_DIR))
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    log.info("loading M1..D1 frames %s -> %s", start.date(), end.date())
    frames = load_frames(Path(args.cache_dir), start, end)
    log.info("  1m rows=%d (%s -> %s)", len(frames["1m"]),
             frames["1m"]["time"].iloc[0], frames["1m"]["time"].iloc[-1])

    log.info("building 5s bars from the 1s tick cache …")
    s5 = tick_s5.build_s5(args.tick_dir, start.date(), end.date())
    if len(s5) == 0:
        log.error("no tick data in %s for %s..%s — cannot run the 5s arm",
                  args.tick_dir, start.date(), end.date())
        return 2
    log.info("  5s rows=%d (%s -> %s)", len(s5), s5["time"].iloc[0],
             s5["time"].iloc[-1])

    base = dict(start=start, end=end, lots=args.lots,
                spread_pts=args.spread, slippage_pts=args.slippage)
    log.info("friction: spread %.2f + slippage %.2f -> %.3f pt per side",
             args.spread, args.slippage, args.spread / 2 + args.slippage)
    res_1m = None
    if args.arms == "both":
        log.info("--- arm A: exec_resolution=1m ---")
        res_1m = run_sim({**frames}, SimConfig(**base, exec_resolution="1m"),
                         specs=ROSTER)
    log.info("--- arm B: exec_resolution=5s ---")
    res_5s = run_sim({**frames, "5s": s5},
                     SimConfig(**base, exec_resolution="5s"), specs=ROSTER)

    st5 = _stats(res_5s.trades)
    st1 = _stats(res_1m.trades) if res_1m is not None else None

    lines: list[str] = []
    def emit(s: str = "") -> None:
        lines.append(s)
        print(s)

    match = None
    flips: list = []
    if res_1m is not None:
        # Diff the two trade sets with the parity matcher (1m cast as "live").
        match = ph.match_trades(_to_live_shaped(res_1m.trades),
                                _to_sim_trades(res_5s.trades), window_s=90)
        flips = [p for p in match.matched if not p.outcome_agrees]

    emit()
    emit(f"=== 5s exit resolution — {start.date()} .. {end.date()} "
         f"| spread {args.spread:.2f} slip {args.slippage:.2f} "
         f"| lots {args.lots} ===")

    if st1 is not None:
        emit(f"{'metric':<14}{'1m arm':>14}{'5s arm':>14}{'delta':>14}")
        for k, fmt in (("trades", "{:.0f}"), ("wins", "{:.0f}"),
                       ("win_rate", "{:.1%}"), ("points", "{:+.1f}"),
                       ("usd", "{:+.2f}"), ("pf", "{:.2f}")):
            emit(f"{k:<14}{fmt.format(st1[k]):>14}{fmt.format(st5[k]):>14}"
                 f"{fmt.format(st5[k] - st1[k]):>14}")
    else:
        emit(f"{'metric':<14}{'5s arm':>14}")
        for k, fmt in (("trades", "{:.0f}"), ("wins", "{:.0f}"),
                       ("win_rate", "{:.1%}"), ("points", "{:+.1f}"),
                       ("usd", "{:+.2f}"), ("pf", "{:.2f}")):
            emit(f"{k:<14}{fmt.format(st5[k]):>14}")

    emit()
    if match is not None:
        emit(f"matched trades      : {len(match.matched)}")
        emit(f"only in 1m arm      : {len(match.live_only)}")
        emit(f"only in 5s arm      : {len(match.sim_only)}")
        emit(f"OUTCOME FLIPS       : {len(flips)}"
             + (f"  ({len(flips) / len(match.matched):.1%} of matched)"
                if match.matched else ""))
    emit(f"ambiguous 5s bars   : "
         f"{res_5s.exec_ambiguity.get('ambiguous_bars', 0)}"
         "   (single 5s bar touched BOTH levels — irreducible)")
    emit(f"minutes w/o 5s data : "
         f"{res_5s.exec_ambiguity.get('minutes_fell_back_to_1m', 0)}"
         "   (fell back to the M1 bar)")

    emit()
    emit("--- per-strategy (5s arm) ---")
    by_strat: dict[str, list] = {}
    for t in res_5s.trades:
        if t.outcome != "OPEN":
            by_strat.setdefault(t.strategy, []).append(t)
    emit(f"{'strategy':<28}{'trades':>8}{'win%':>8}{'points':>10}{'PF':>7}")
    for name, ts in sorted(by_strat.items()):
        w = [t for t in ts if t.pnl_pts > 0]
        gl = abs(sum(t.pnl_pts for t in ts if t.pnl_pts <= 0))
        gw = sum(t.pnl_pts for t in w)
        pf = (gw / gl) if gl else float("inf")
        emit(f"{name:<28}{len(ts):>8}{len(w) / len(ts):>8.1%}"
             f"{sum(t.pnl_pts for t in ts):>+10.1f}{pf:>7.2f}")

    if flips:
        emit()
        emit("--- flipped trades (1m outcome -> 5s outcome) ---")
        for p in flips[:40]:
            emit(f"  {p.live.entry_time:%Y-%m-%d %H:%M}  "
                 f"{p.live.strategy:<26} {p.live.side:<4} "
                 f"{p.live.outcome:>5} -> {p.sim.outcome:<5} "
                 f"usd {p.live.usd:+8.2f} -> {p.sim.usd:+8.2f}")
        if len(flips) > 40:
            emit(f"  … {len(flips) - 40} more")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{start.date()}_{end.date()}_sp{args.spread:.2f}"
    (out_dir / f"ab_5s_vs_1m_{stamp}.md").write_text("\n".join(lines))

    if match is not None and match.matched:
        pd.DataFrame([{
            "entry_time": p.live.entry_time, "strategy": p.live.strategy,
            "side": p.live.side,
            "outcome_1m": p.live.outcome, "outcome_5s": p.sim.outcome,
            "usd_1m": p.live.usd, "usd_5s": p.sim.usd,
            "exit_1m": p.live.exit_px, "exit_5s": p.sim.exit_px,
            "flipped": not p.outcome_agrees,
        } for p in match.matched]).to_csv(
            out_dir / f"ab_trades_{stamp}.csv", index=False)

    pd.DataFrame([{
        "entry_time": t.entry_time, "exit_time": t.exit_time,
        "strategy": t.strategy, "side": t.side, "outcome": t.outcome,
        "entry_px": t.entry_px, "exit_px": t.exit_px,
        "pnl_pts": t.pnl_pts, "pnl_usd": t.pnl_usd,
    } for t in res_5s.trades]).to_csv(
        out_dir / f"trades_5s_{stamp}.csv", index=False)
    emit()
    emit(f"[OUT] {out_dir / f'ab_5s_vs_1m_{stamp}.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
