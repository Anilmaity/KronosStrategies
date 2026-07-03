"""
manager_sim_variant.py  (Task 6 — parallel sensitivity)
--------------------------------------------------------
Per-process sensitivity-variant driver for the Strategy Manager simulator.

run_sensitivity() in manager_sim_report.py runs the 6 variants serially
in-process (~44 min each at production depths → ~4.4 h total).  This driver
runs ONE variant per invocation so all 6 can run concurrently as separate
OS processes (setattr'ing regime_engine constants is safe per-process; no
restore races).

Variant definitions are imported from
backtest.manager_sim_report.SENSITIVITY_VARIANTS — the single source of
truth shared with the serial runner.  Threshold numbers are never duplicated
here.

Run one variant (from KronosStrategies/strategies/):
    python -m backtest.manager_sim_variant --variant vol_loose \
        --start 2026-04-01 --end 2026-07-02 \
        --out backtest/results/manager_sim/variant_vol_loose.json

Variant names: vol_loose, vol_tight, er_loose, er_tight,
               win_minus30, win_plus30.

Collect the 6 JSONs into a standalone sensitivity-grid markdown (appended to
--out-md), reconstructing the base gated/ungated nets from the base run's
trade CSVs:
    python -m backtest.manager_sim_variant \
        --collect "backtest/results/manager_sim/variant_*.json" \
        --base-gated   backtest/results/manager_sim/trades_gated_<ts>.csv \
        --base-ungated backtest/results/manager_sim/trades_ungated_<ts>.csv \
        --out-md backtest/results/manager_sim/sensitivity_parallel.md
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))        # strategies/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))  # repo root

from backtest.manager_sim_engine import SimConfig, load_frames, run_sim
from backtest.manager_sim_report import (
    SENSITIVITY_VARIANTS,
    _compute_dd,
    _profit_factor,
    _shifted_specs,
    _win_rate,
)

CACHE_DIR = Path(__file__).resolve().parent / "results" / "bars_cache"


# ─────────────────────────────────────────────────────────────────────────────
# Single-variant run
# ─────────────────────────────────────────────────────────────────────────────

def run_variant(name: str, frames: dict, cfg: SimConfig) -> dict:
    """Run ONE gated sensitivity variant and return its summary dict.

    Threshold variants setattr constants on
    strategy_manager.regime.regime_engine (originals restored afterwards —
    harmless per-process, kept for in-process callers like tests).
    Window variants pass shifted session_vol specs to run_sim.
    """
    vdef = SENSITIVITY_VARIANTS[name]

    if vdef["kind"] == "threshold":
        import strategy_manager.regime.regime_engine as rem
        attrs = vdef["attrs"]
        originals = {k: getattr(rem, k) for k in attrs}
        try:
            for k, v in attrs.items():
                setattr(rem, k, v)
            res = run_sim(frames, cfg)
        finally:
            for k, v in originals.items():
                setattr(rem, k, v)
    else:  # kind == "windows"
        res = run_sim(frames, cfg, specs=_shifted_specs(vdef["windows"]))

    trades = res.trades
    dd_usd, _ = _compute_dd(trades)
    pf = _profit_factor(trades)

    per_strategy: dict[str, float] = {}
    for t in trades:
        per_strategy[t.strategy] = per_strategy.get(t.strategy, 0.0) + t.pnl_usd

    return {
        "variant":    name,
        "label":      vdef["label"],
        "trades":     len(trades),
        "net_usd":    round(sum(t.pnl_usd for t in trades), 4),
        "max_dd_usd": round(dd_usd, 4),
        "wr":         round(_win_rate(trades), 4),
        # JSON has no Infinity: PF with zero gross loss serialises as null.
        "pf":         round(pf, 6) if math.isfinite(pf) else None,
        "per_strategy_net": {
            k: round(v, 4) for k, v in sorted(per_strategy.items())
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Collect mode
# ─────────────────────────────────────────────────────────────────────────────

def _csv_net_usd(path) -> float:
    """Sum the pnl_usd column of a base-run trades CSV."""
    total = 0.0
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            total += float(row["pnl_usd"])
    return total


def collect(glob_pat: str, base_gated_csv, base_ungated_csv, out_md) -> str:
    """Render the sensitivity grid + rubric-condition-4 verdict as markdown.

    Loads every variant JSON matching *glob_pat*, reconstructs the base
    gated/ungated combined nets from the base run's trade CSVs, and APPENDS
    a standalone markdown section to *out_md*.  Returns the markdown.

    Condition 4 (same semantics as manager_sim_report._evaluate_rubric):
    no variant delta (variant_net − ungated_net) may have the opposite sign
    to the base delta (gated_net − ungated_net).
    """
    files = sorted(glob.glob(glob_pat))
    if not files:
        raise SystemExit(f"--collect matched no files: {glob_pat}")

    variants = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            variants.append(json.load(fh))

    g_net = _csv_net_usd(base_gated_csv)
    u_net = _csv_net_usd(base_ungated_csv)
    delta = g_net - u_net

    if abs(delta) < 1e-9:
        cond4 = True  # zero delta cannot flip
    else:
        cond4 = all((v["net_usd"] - u_net) * delta >= 0 for v in variants)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "## Sensitivity Grid (parallel per-process variant runs)",
        "",
        f"Generated {ts} UTC from {len(variants)} variant file(s)"
        + ("" if len(variants) == 6 else "  **(expected 6 — grid incomplete)**")
        + ".",
        "",
        f"Base combined net USD — gated: **{g_net:+.2f}**, "
        f"ungated: **{u_net:+.2f}**, delta (G−U): **{delta:+.2f}**.",
        "",
        "| Variant | Label | Trades | Net USD | Delta vs Base Gated |"
        " Variant Delta (V−U) | Max DD $ | WR% | PF |",
        "|---------|-------|-------:|--------:|--------------------:|"
        "--------------------:|---------:|----:|---:|",
    ]
    for v in variants:
        pf = v.get("pf")
        pf_s = "inf" if pf is None else f"{pf:.2f}"
        lines.append(
            f"| {v['variant']} | {v.get('label', '')} | {v['trades']} |"
            f" {v['net_usd']:+.2f} | {v['net_usd'] - g_net:+.2f} |"
            f" {v['net_usd'] - u_net:+.2f} | {v['max_dd_usd']:.2f} |"
            f" {v['wr']:.1f} | {pf_s} |"
        )
    lines += [
        "",
        "Rubric condition 4 — no sensitivity variant flips the sign of the "
        f"combined gated-vs-ungated delta: **{'PASS' if cond4 else 'FAIL'}**",
        "",
    ]
    md = "\n".join(lines) + "\n"

    out_md = Path(out_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    with open(out_md, "a", encoding="utf-8") as fh:
        fh.write(md)
    return md


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=(
            "Run ONE Strategy-Manager sensitivity variant as its own process "
            "(--variant), or collect finished variant JSONs into a markdown "
            "grid (--collect)."
        )
    )
    ap.add_argument("--variant", choices=sorted(SENSITIVITY_VARIANTS),
                    help="Sensitivity variant to run (one gated full run)")
    ap.add_argument("--start", default="2026-04-01",
                    help="Simulation start (YYYY-MM-DD, UTC)")
    ap.add_argument("--end", default="2026-07-02",
                    help="Simulation end exclusive (YYYY-MM-DD, UTC)")
    ap.add_argument("--cache-dir", default=str(CACHE_DIR),
                    help="Directory containing is_XAU_USD_*.parquet files")
    ap.add_argument("--out",
                    help="Output JSON path for the variant summary")
    ap.add_argument("--slice-rows", default=None,
                    help="JSON dict overriding SimConfig.slice_rows "
                         "(test hook; production uses live-faithful defaults)")
    ap.add_argument("--collect",
                    help="Glob of variant JSONs to collect into a markdown grid")
    ap.add_argument("--base-gated",
                    help="Base run gated trades CSV (collect mode)")
    ap.add_argument("--base-ungated",
                    help="Base run ungated trades CSV (collect mode)")
    ap.add_argument("--out-md",
                    help="Markdown file the sensitivity section is APPENDED to")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)

    # ── Collect mode ──────────────────────────────────────────────────────────
    if args.collect:
        missing = [f"--{n}" for n, v in (
            ("base-gated", args.base_gated),
            ("base-ungated", args.base_ungated),
            ("out-md", args.out_md),
        ) if not v]
        if missing:
            ap.error(f"--collect requires {', '.join(missing)}")
        md = collect(args.collect, args.base_gated, args.base_ungated,
                     args.out_md)
        print(md)
        print(f"Appended sensitivity section -> {args.out_md}")
        return 0

    # ── Single-variant run mode ───────────────────────────────────────────────
    if not args.variant or not args.out:
        ap.error("--variant and --out are required (or use --collect)")

    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end   = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)

    cfg_kwargs: dict = {}
    if args.slice_rows:
        cfg_kwargs["slice_rows"] = json.loads(args.slice_rows)
    # Default SimConfig => faithful slice_rows, market fills, gated.
    cfg = SimConfig(start=start, end=end, gated=True, **cfg_kwargs)

    print(f"[{args.variant}] loading frames from {args.cache_dir} ...")
    frames = load_frames(Path(args.cache_dir), start, end)
    print(f"[{args.variant}] 1m bars in window: {len(frames['1m']):,}")

    result = run_variant(args.variant, frames, cfg)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        f"[{args.variant}] n={result['trades']} net=${result['net_usd']:+.2f} "
        f"dd=${result['max_dd_usd']:.2f} wr={result['wr']:.1f}% -> {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
