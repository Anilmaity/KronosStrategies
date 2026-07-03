"""
backtest_manager_sim.py
-----------------------
CLI wrapper for the offline Strategy Manager simulator.

Usage (from KronosStrategies/strategies/):
    python -m backtest.backtest_manager_sim --start 2026-04-01 --end 2026-07-02
    python -m backtest.backtest_manager_sim --mode both --spread-pts 0.40

--mode both  runs gated + ungated back-to-back with a shared timestamp, writing
             four output files (trades CSV, regime JSONL, summary JSON × 2 modes).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))        # strategies/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))  # repo root

import pandas as pd

from backtest.manager_sim_engine import (
    SimConfig,
    load_frames,
    run_sim,
)

CACHE_DIR        = Path(__file__).resolve().parent / "results" / "bars_cache"
OUTPUT_DIR       = Path(__file__).resolve().parent / "results"
MANAGER_SIM_DIR  = OUTPUT_DIR / "manager_sim"


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Offline Strategy Manager simulator (gated / ungated / both)."
    )
    ap.add_argument("--start",           default="2026-04-01",
                    help="Simulation start date (YYYY-MM-DD, UTC)")
    ap.add_argument("--end",             default="2026-07-02",
                    help="Simulation end date exclusive (YYYY-MM-DD, UTC)")
    ap.add_argument("--mode",            choices=["gated", "ungated", "both"],
                    default="gated")
    ap.add_argument("--spread-pts",      type=float, default=0.30,
                    help="Half-spread in points (default 0.30)")
    ap.add_argument("--slippage-pts",    type=float, default=0.10,
                    help="Slippage in points (default 0.10)")
    ap.add_argument("--lots",            type=float, default=0.02,
                    help="Lot size per trade (default 0.02)")
    ap.add_argument("--kill-switch-usd", type=float, default=150.0,
                    help="Daily loss threshold that trips the kill-switch (USD)")
    ap.add_argument("--max-concurrent",  type=int,   default=3,
                    help="Max open positions at one time")
    ap.add_argument("--regime-cadence",  type=int,   default=5,
                    help="Regime evaluation cadence in minutes (default 5)")
    ap.add_argument("--cache-dir",       default=str(CACHE_DIR),
                    help="Directory containing is_XAU_USD_*.parquet files")
    ap.add_argument("--sensitivity",     action="store_true", default=False,
                    help=(
                        "Run 6 sensitivity variants (vol thresholds ×2, ER thresholds ×2, "
                        "session windows ±30 min) after the base run. "
                        "OFF by default — each variant is a full re-run (~44 min each), "
                        "so the full grid takes ~4.4 h. Recommended as an overnight job."
                    ))
    return ap.parse_args()


def _make_cfg(args: argparse.Namespace, gated: bool) -> SimConfig:
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end   = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
    return SimConfig(
        start=start,
        end=end,
        spread_pts=args.spread_pts,
        slippage_pts=args.slippage_pts,
        lots=args.lots,
        kill_switch_usd=args.kill_switch_usd,
        max_concurrent=args.max_concurrent,
        regime_cadence_min=args.regime_cadence,
        gated=gated,
    )


def _write_outputs(result, prefix: str, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # Trades CSV
    trades_path = output_dir / f"{prefix}_trades.csv"
    if result.trades:
        rows = [
            {
                "strategy":   t.strategy,
                "entry_time": t.entry_time.isoformat(),
                "side":       t.side,
                "entry_px":   t.entry_px,
                "sl":         t.sl,
                "tp":         t.tp,
                "exit_px":    t.exit_px,
                "exit_time":  t.exit_time.isoformat(),
                "outcome":    t.outcome,
                "pnl_pts":    round(t.pnl_pts, 4),
                "pnl_usd":    round(t.pnl_usd, 4),
                "gate_reason": t.gate_reason,
            }
            for t in result.trades
        ]
        pd.DataFrame(rows).to_csv(trades_path, index=False)
        print(f"    trades ({len(result.trades):,}) -> {trades_path}")
    else:
        print("    no trades fired")

    # Regime snapshots — JSONL (one object per line)
    regime_path = output_dir / f"{prefix}_regime.jsonl"
    with open(regime_path, "w", encoding="utf-8") as fh:
        for row in result.regime_rows:
            fh.write(json.dumps(row) + "\n")
    print(f"    regime rows ({len(result.regime_rows):,}) -> {regime_path}")

    # Summary JSON
    summary = {
        "n_trades":    len(result.trades),
        "kill_trips":  result.kill_trips,
        "paused_pct":  result.paused_pct,
    }
    if result.trades:
        outcomes = {}
        for t in result.trades:
            outcomes[t.outcome] = outcomes.get(t.outcome, 0) + 1
        net_usd = sum(t.pnl_usd for t in result.trades)
        wins    = sum(1 for t in result.trades if t.pnl_usd > 0)
        summary.update({
            "net_usd":  round(net_usd, 2),
            "win_rate": round(100.0 * wins / len(result.trades), 1),
            "outcomes": outcomes,
        })
    summary_path = output_dir / f"{prefix}_summary.json"
    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    print(f"    summary -> {summary_path}")


def main() -> None:
    args = parse_args()
    cache_dir = Path(args.cache_dir)

    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end   = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)

    print(f"Loading frames from {cache_dir} ...")
    frames = load_frames(cache_dir, start, end)
    print(f"  1m bars in window: {len(frames['1m']):,}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    modes = ["gated", "ungated"] if args.mode == "both" else [args.mode]

    results: dict[str, object] = {}
    for mode_str in modes:
        gated = mode_str == "gated"
        cfg   = _make_cfg(args, gated)
        print(f"\nRunning {mode_str} ...")
        result = run_sim(frames, cfg)
        results[mode_str] = result

        prefix = f"manager_sim_{stamp}_{mode_str}"
        _write_outputs(result, prefix, OUTPUT_DIR)

        # Quick console summary
        if result.trades:
            n   = len(result.trades)
            net = sum(t.pnl_usd for t in result.trades)
            wr  = 100.0 * sum(1 for t in result.trades if t.pnl_usd > 0) / n
            print(f"  {n} trades | WR={wr:.0f}% | net=${net:.2f}")
        if result.kill_trips:
            print(f"  kill-switch trips: {result.kill_trips}")
        for name, pct in result.paused_pct.items():
            print(f"  paused {name}: {pct:.1f}%")

    # ── Decision report (--mode both only) ───────────────────────────────────
    if args.mode == "both" and "gated" in results and "ungated" in results:
        from backtest.manager_sim_report import write_report, run_sensitivity  # lazy import

        sensitivity: list[tuple[str, float]] | None = None
        if args.sensitivity:
            gated_cfg = _make_cfg(args, gated=True)
            print(
                "\nRunning sensitivity analysis (6 variants) — "
                "this may take several hours at production depths …"
            )
            sens_data = run_sensitivity(frames, gated_cfg)
            sensitivity = [(d["variant"], d["combined_net_usd"]) for d in sens_data]
            for d in sens_data:
                print(
                    f"  {d['variant']:20s}: net=${d['combined_net_usd']:+.2f}"
                    f"  n={d['n_trades']}  WR={d['win_rate']:.1f}%"
                )

        report_path = write_report(
            gated=results["gated"],
            ungated=results["ungated"],
            cfg=_make_cfg(args, gated=True),
            out_dir=MANAGER_SIM_DIR,
            sensitivity=sensitivity,
        )
        print(f"\nDecision report  -> {report_path}")

    print("\nDone.")


if __name__ == "__main__":
    main()
