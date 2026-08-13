"""run_live_parity.py — compare the 5s sim against REAL live trades.

Phase 3 of docs/superpowers/specs/2026-08-12-5s-backtest-fidelity-design.md.

This is the measurement the whole project exists for: does the backtest
reproduce what the account actually did? It replays the live roster over a
window with 5s exit resolution (OANDA S5 via s5_cache) and diffs the result
against a ground-truth CSV of real trades, using the pre-registered tolerance
in spec section 4.5.

Ground-truth CSV columns:
    strategy,side,entry_time,entry_px,exit_time,exit_px,outcome,usd,lots,ticket
`usd` must be BROKER truth (deal profit + commission + swap), not the DB's
mid-price estimate, and times must be REAL UTC — note Position.created_at in the
prod DB is IST wall-clock labelled UTC, so it is 5:30 ahead of reality.

Usage (from strategies/):
    python -m backtest.run_live_parity \
        --start 2026-08-12 --end 2026-08-13 \
        --live results/parity/live_trades_2026-08-12.csv --spread 0.62
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_REPO_ROOT / "tick_data_collector" / ".env")

from backtest import parity_harness as ph          # noqa: E402
from backtest import s5_cache                      # noqa: E402
from backtest.manager_sim_engine import (          # noqa: E402
    SimConfig, load_frames, run_sim,
)
from backtest.run_5s_ab import ROSTER              # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("live_parity")

_HERE = Path(__file__).resolve().parent
CACHE_DIR = _HERE / "results" / "bars_cache"
S5_DIR = CACHE_DIR / "s5"
OUT_DIR = _HERE / "results" / "parity"


def load_live(path: str | Path) -> list[ph.LiveTrade]:
    df = pd.read_csv(path)
    for col in ("entry_time", "exit_time"):
        df[col] = pd.to_datetime(df[col], utc=True)
    return [ph.LiveTrade(
        strategy=r.strategy, side=r.side,
        entry_time=r.entry_time.to_pydatetime(),
        entry_px=float(r.entry_px),
        exit_time=r.exit_time.to_pydatetime(),
        exit_px=float(r.exit_px),
        outcome=r.outcome, usd=float(r.usd),
        ticket=str(r.ticket)) for r in df.itertuples(index=False)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--live", required=True, help="ground-truth CSV")
    ap.add_argument("--spread", type=float, default=0.62)
    ap.add_argument("--slippage", type=float, default=0.10)
    ap.add_argument("--lots", type=float, default=0.10)
    ap.add_argument("--window-s", type=float, default=90.0)
    ap.add_argument("--model-entry-drift", action="store_true",
                    help="apply live's entry_drift gate using the S5 close at "
                         "--latency past the M1 close")
    ap.add_argument("--latency", type=float, default=5.0)
    ap.add_argument("--external-pnl", default=None,
                    help="CSV of unsimulated realized P&L: utc_time,usd "
                         "(e.g. the Telegram copy trades that feed live's "
                         "kill-switch)")
    ap.add_argument("--exit-slippage-only", action="store_true",
                    help="charge only slippage on exits (a stop level is "
                         "already executable; the half-spread is double-charged)")
    ap.add_argument("--entry-time-at-close", action="store_true",
                    help="measure max_hold from the fill moment (bar close) "
                         "rather than the bar open")
    ap.add_argument("--sided-fills", action="store_true",
                    help="fill BUY at the S5 ask / SELL at the bid instead of "
                         "mid +/- scalar friction")
    args = ap.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    live = load_live(args.live)
    log.info("ground truth: %d live trade(s) %s .. %s", len(live),
             min(t.entry_time for t in live), max(t.entry_time for t in live))

    frames = load_frames(CACHE_DIR, start, end)
    s5 = s5_cache.load_s5("XAU_USD", start, end, base=S5_DIR)
    if len(s5) == 0:
        log.error("no S5 cached for %s..%s — run backtest.s5_backfill first",
                  start.date(), end.date())
        return 2
    log.info("5s bars: %d (%s .. %s)", len(s5), s5["time"].iloc[0],
             s5["time"].iloc[-1])

    ext = []
    if args.external_pnl:
        edf = pd.read_csv(args.external_pnl)
        edf["utc_time"] = pd.to_datetime(edf["utc_time"], utc=True)
        ext = [(r.utc_time.to_pydatetime(), float(r.usd))
               for r in edf.itertuples(index=False)]
        log.info("external P&L events: %d totalling %+.2f USD",
                 len(ext), sum(u for _, u in ext))

    cfg = SimConfig(start=start, end=end, lots=args.lots,
                    spread_pts=args.spread, slippage_pts=args.slippage,
                    exec_resolution="5s",
                    model_entry_drift=args.model_entry_drift,
                    sided_fills=args.sided_fills,
                    exit_slippage_only=args.exit_slippage_only,
                    entry_time_at_bar_close=args.entry_time_at_close,
                    external_pnl=ext,
                    entry_latency_s=args.latency)
    log.info("friction %.3f pt/side | windows %s", cfg.entry_friction_pts,
             {s.name: (s.win_1m, s.win_5m) for s in ROSTER})

    res = run_sim({**frames, "5s": s5}, cfg, specs=ROSTER)
    sim = [ph.SimTrade(strategy=t.strategy, side=t.side,
                       entry_time=t.entry_time, entry_px=t.entry_px,
                       exit_time=t.exit_time, exit_px=t.exit_px,
                       outcome=t.outcome, usd=t.pnl_usd)
           for t in res.trades if t.outcome != "OPEN"]

    match = ph.match_trades(live, sim, window_s=args.window_s)
    verdict = ph.evaluate_tolerance(match)
    s = verdict.stats

    lines: list[str] = []
    def emit(x: str = "") -> None:
        lines.append(x)
        print(x)

    emit()
    emit(f"=== LIVE PARITY {start.date()} .. {end.date()} "
         f"| spread {args.spread} | 5s exec ===")
    emit(f"live trades       : {len(live)}")
    emit(f"sim trades        : {len(sim)}")
    emit(f"matched           : {s['matched']}   "
         f"(match rate {s['match_rate']:.1%})")
    emit(f"live-only (missed): {s['live_only']}")
    emit(f"sim-only (invented): {s['sim_only']}")
    emit(f"outcome agreement : {s['outcome_agreement']:.1%}")
    emit(f"entry delta       : median {s['entry_median']:.3f}pt  "
         f"p90 {s['entry_p90']:.3f}pt")
    emit(f"exit delta        : median {s['exit_median']:.3f}pt  "
         f"p90 {s['exit_p90']:.3f}pt")
    emit(f"USD               : live {s['usd_live']:+.2f}  "
         f"sim {s['usd_sim']:+.2f}  ({s['usd_rel']:.1%} off)")
    emit(f"mechanisms        : {s['mechanisms'] or '{}'}")
    emit(f"sim gate rejects  : "
         f"{ {k: v for k, v in res.entry_gate_rejects.items() if any(v.values())} }")
    emit()
    emit(f"VERDICT: {'PASS' if verdict.passed else 'FAIL'} "
         f"against the pre-registered tolerance")
    for f in verdict.failures:
        emit(f"  - {f}")

    if match.matched:
        emit()
        emit("--- matched trades ---")
        emit(f"{'entry (UTC)':<20}{'strategy':<24}{'side':<5}"
             f"{'dEntry':>8}{'dExit':>8}  {'exit live':<10}{'exit sim':<10} outcome")
        for p in sorted(match.matched, key=lambda x: x.live.entry_time):
            emit(f"{p.live.entry_time:%Y-%m-%d %H:%M:%S}  "
                 f"{p.live.strategy:<24}{p.live.side:<5}"
                 f"{p.entry_delta:>+8.2f}{p.exit_delta:>+8.2f}  "
                 f"{p.live.exit_time:%H:%M:%S}  {p.sim.exit_time:%H:%M:%S}  "
                 f"{p.live.outcome}->{p.sim.outcome}"
                 f"{'' if p.outcome_agrees else '  *FLIP*'}")

    if match.live_only:
        emit()
        emit("--- live trades the sim MISSED ---")
        for t in match.live_only:
            emit(f"  {t.entry_time:%Y-%m-%d %H:%M:%S}  {t.strategy:<26}"
                 f"{t.side:<5} @{t.entry_px:.2f}  {t.outcome}  "
                 f"usd {t.usd:+.2f}  ticket {t.ticket}")

    if match.sim_only:
        emit()
        emit("--- trades the sim INVENTED ---")
        for t in match.sim_only:
            emit(f"  {t.entry_time:%Y-%m-%d %H:%M:%S}  {t.strategy:<26}"
                 f"{t.side:<5} @{t.entry_px:.2f}  {t.outcome}  "
                 f"usd {t.usd:+.2f}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"live_parity_{start.date()}_{end.date()}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    emit()
    emit(f"[OUT] {out}")
    return 0 if verdict.passed else 1


if __name__ == "__main__":
    sys.exit(main())
