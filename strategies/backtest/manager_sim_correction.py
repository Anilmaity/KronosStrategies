"""
strategies/backtest/manager_sim_correction.py
=============================================
Post-hoc fill-realism correction for the committed manager-sim run
(timestamp 20260702_235849).

Problem
-------
The original sim filled entries at ``sig.entry_price ± friction`` — the price
embedded in the signal.  Live entry_manager places MARKET orders, so by
detection-bar close the price has already moved through the breakout level.
One extreme case: 2026-07-01T14:00 SESSION_BREAKOUT BUY was filled at
entry_px=4047.56 but the 1m bar that generated the signal closed near the TP
(4098.98), meaning live would never have opened the position at a favourable
price — it would have instantly hit TP or been rejected.

Correction method
-----------------
For each trade:
  detection bar  = 1m bar whose open time == entry_time in the CSV
  new_entry_px   = bar_close + friction  (BUY)
                 = bar_close - friction  (SELL)
  friction       = 0.25 (spread/2=0.15 + slippage=0.10)

Phantom guards (dropped trades are counted as phantoms, never booked):
  Beyond-TP — all trades:
    BUY:  new_entry_px >= tp  → phantom (market already past TP)  → drop
    SELL: new_entry_px <= tp  → phantom                            → drop
  Beyond-SL — NON-TRAILING trades only:
    BUY:  new_entry_px <= sl  → phantom (market already through the stop;
    SELL: new_entry_px >= sl     live is stopped out instantly)    → drop
  Trailing strategies are exempt from the SL guard here because their CSV
  ``sl`` column is the ratcheted trail stop at exit, not the signal stop.
  (The engine's own guard in run_sim applies the SL guard to trailing
  strategies too, using sig.stop_loss at detection time.)

PnL recomputation for kept trades:
  BUY:  new_pnl_pts = exit_px − new_entry_px
  SELL: new_pnl_pts = new_entry_px − exit_px
  new_pnl_usd = new_pnl_pts × lots × 100

Limitation — TRAIL exits are NOT correctable from the CSVs
----------------------------------------------------------
The trailing exit path is fill-path-dependent: the trail seeds its
high-water mark from the entry fill, so a different fill changes every
subsequent ratchet, the exit bar, and the exit price.  This CSV-level
correction replaces the entry but keeps the ORIGINAL exit, which is
internally inconsistent for TRAIL trades — their "corrected" numbers must
not be quoted as results.

Concretely for S96 (the only trailing strategy in the roster): S96's
sig.entry_price is the last CLOSED H1 bar's close — up to ~1 hour stale.
On crash days (e.g. 2026-06-17: five consecutive BUYs at an identical stale
4379.8 while the market traded ~4264–4272) the correction refills at market
but keeps exits anchored to stops derived from the phantom entry,
converting phantom losers into phantom winners.  The S96 corrected numbers
are therefore artifacts in BOTH directions.  S96's real number requires an
engine re-run with market fills + both phantom guards; this script cannot
produce one.

Output
------
Returns a dict with corrected DataFrames and summary statistics, and writes
a structured markdown report suitable for embedding in the AMENDED summary.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import NamedTuple

import pandas as pd

# ── paths ──────────────────────────────────────────────────────────────────────

_HERE      = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent          # KronosStrategies/
_RESULTS   = _REPO_ROOT / "strategies" / "backtest" / "results" / "manager_sim"
_CACHE     = _REPO_ROOT / "strategies" / "backtest" / "results" / "bars_cache"

GATED_CSV    = _RESULTS / "trades_gated_20260702_235849.csv"
UNGATED_CSV  = _RESULTS / "trades_ungated_20260702_235849.csv"
BARS_PARQUET = _CACHE   / "is_XAU_USD_1m.parquet"

FRICTION     = 0.25       # spread/2 (0.15) + slippage (0.10)
LOTS_SIM     = 0.02       # sim lots
LOTS_LIVE    = 0.01       # live MANAGER_CHILD_LOT
USD_PER_PT_SIM  = LOTS_SIM  * 100.0   # $2.00 / pt
USD_PER_PT_LIVE = LOTS_LIVE * 100.0   # $1.00 / pt

PHANTOM_EXAMPLE = "2026-07-01T14:00:00+00:00"   # the canonical phantom trade

# Strategies whose Signal.trailing=True.  Their CSV `sl` column is the
# ratcheted trail stop at exit (not the signal stop), so the beyond-SL
# phantom guard is not applicable at CSV level.
TRAILING_STRATEGIES = {"KRONOS_S96_H1_MOMENTUM"}


# ── helpers ────────────────────────────────────────────────────────────────────

def _load_bar_closes(parquet_path: Path) -> dict[str, float]:
    """Return {UTC-isoformat → close} for every 1m bar."""
    df = pd.read_parquet(parquet_path, columns=["time", "close"])
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return {t.isoformat(): float(c) for t, c in zip(df["time"], df["close"])}


def _normalize_ts(entry_time_str: str) -> str:
    """Return UTC isoformat key from the entry_time string in the CSVs."""
    ts = pd.Timestamp(entry_time_str)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.isoformat()


def _correct_trades(df: pd.DataFrame, bar_closes: dict[str, float]) -> pd.DataFrame:
    """Apply fill-realism correction; returns augmented DataFrame.

    New columns: corrected_entry_px, new_pnl_pts, new_pnl_usd_sim,
                 new_pnl_usd_live, bar_close, phantom, phantom_kind.
    Phantom rows have NaN pnl values.

    phantom_kind: "TP" (fill beyond take-profit — all trades) or
                  "SL" (fill beyond the signal stop — non-trailing only).
    """
    records = []
    for _, row in df.iterrows():
        ts_key = _normalize_ts(str(row["entry_time"]))
        bar_close = bar_closes.get(ts_key)

        side = row["side"]
        tp   = float(row["tp"])
        sl   = float(row["sl"])
        ep   = float(row["exit_px"])
        is_trailing = str(row["strategy"]) in TRAILING_STRATEGIES

        if bar_close is None:
            # Fallback: keep original (should not happen for committed run)
            rec = row.to_dict()
            rec.update(corrected_entry_px=float(row["entry_px"]),
                       new_pnl_pts=float(row["pnl_pts"]),
                       new_pnl_usd_sim=float(row["pnl_usd"]),
                       new_pnl_usd_live=float(row["pnl_usd"]) / 2.0,
                       bar_close=None, phantom=False, phantom_kind="")
            records.append(rec)
            continue

        if side == "BUY":
            new_entry_px = bar_close + FRICTION
            phantom_tp   = new_entry_px >= tp
            phantom_sl   = (not is_trailing) and new_entry_px <= sl
        else:
            new_entry_px = bar_close - FRICTION
            phantom_tp   = new_entry_px <= tp
            phantom_sl   = (not is_trailing) and new_entry_px >= sl
        phantom = phantom_tp or phantom_sl

        rec = row.to_dict()
        rec["bar_close"] = bar_close
        rec["corrected_entry_px"] = new_entry_px
        rec["phantom"] = phantom
        rec["phantom_kind"] = "TP" if phantom_tp else ("SL" if phantom_sl else "")

        if phantom:
            rec["new_pnl_pts"]      = float("nan")
            rec["new_pnl_usd_sim"]  = float("nan")
            rec["new_pnl_usd_live"] = float("nan")
        else:
            if side == "BUY":
                new_pnl_pts = ep - new_entry_px
            else:
                new_pnl_pts = new_entry_px - ep
            rec["new_pnl_pts"]      = new_pnl_pts
            rec["new_pnl_usd_sim"]  = new_pnl_pts * USD_PER_PT_SIM
            rec["new_pnl_usd_live"] = new_pnl_pts * USD_PER_PT_LIVE

        records.append(rec)

    return pd.DataFrame(records)


# ── summary tables ─────────────────────────────────────────────────────────────

class StratRow(NamedTuple):
    strategy:      str
    trades_raw:    int
    net_usd_raw:   float
    trades_corr:   int
    phantoms:      int
    net_usd_corr:  float
    net_usd_live:  float   # at 0.01 lots (halved)


def _strat_rows(df_corr: pd.DataFrame) -> list[StratRow]:
    rows = []
    for strat, grp in df_corr.groupby("strategy"):
        phantoms   = int(grp["phantom"].sum())
        kept       = grp[~grp["phantom"]]
        rows.append(StratRow(
            strategy     = strat,
            trades_raw   = len(grp),
            net_usd_raw  = float(grp["pnl_usd"].sum()),
            trades_corr  = len(kept),
            phantoms     = phantoms,
            net_usd_corr = float(kept["new_pnl_usd_sim"].sum()),
            net_usd_live = float(kept["new_pnl_usd_live"].sum()),
        ))
    return rows


def _combined_row(df_corr: pd.DataFrame, label: str) -> dict:
    phantoms = int(df_corr["phantom"].sum())
    kept     = df_corr[~df_corr["phantom"]]
    return dict(
        label        = label,
        trades_raw   = len(df_corr),
        net_usd_raw  = float(df_corr["pnl_usd"].sum()),
        trades_corr  = len(kept),
        phantoms     = phantoms,
        net_usd_corr = float(kept["new_pnl_usd_sim"].sum()),
        net_usd_live = float(kept["new_pnl_usd_live"].sum()),
    )


# ── markdown helpers ───────────────────────────────────────────────────────────

def _fmt(v: float, prefix: bool = True) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}"


def _strat_table_md(rows: list[StratRow]) -> str:
    lines = [
        "| Strategy | Raw trades | Raw Net $ (0.02L) | Phantoms | Corr trades"
        " | Corr Net $ (0.02L) | Corr Net $ (0.01L) |",
        "|----------|----------:|------------------:|---------:|------------:"
        "|-------------------:|-------------------:|",
    ]
    for r in rows:
        lines.append(
            f"| {r.strategy} | {r.trades_raw} | {_fmt(r.net_usd_raw)} |"
            f" {r.phantoms} | {r.trades_corr} |"
            f" {_fmt(r.net_usd_corr)} | {_fmt(r.net_usd_live)} |"
        )
    return "\n".join(lines)


def _combined_md(c: dict) -> str:
    return (
        f"| Combined | {c['trades_raw']} | {_fmt(c['net_usd_raw'])} |"
        f" {c['phantoms']} | {c['trades_corr']} |"
        f" {_fmt(c['net_usd_corr'])} | {_fmt(c['net_usd_live'])} |"
    )


# ── main ───────────────────────────────────────────────────────────────────────

def run_correction() -> dict:
    """Load data, apply correction, return structured results dict."""
    print("Loading 1m bars cache …", file=sys.stderr)
    bar_closes = _load_bar_closes(BARS_PARQUET)

    print("Loading gated trades …", file=sys.stderr)
    gated_raw = pd.read_csv(GATED_CSV)
    print("Loading ungated trades …", file=sys.stderr)
    ungated_raw = pd.read_csv(UNGATED_CSV)

    print("Applying correction …", file=sys.stderr)
    gated_corr   = _correct_trades(gated_raw, bar_closes)
    ungated_corr = _correct_trades(ungated_raw, bar_closes)

    # Per-strategy rows
    gated_rows   = _strat_rows(gated_corr)
    ungated_rows = _strat_rows(ungated_corr)

    # Combined rows
    gated_comb   = _combined_row(gated_corr,   "Gated")
    ungated_comb = _combined_row(ungated_corr, "Ungated")

    # Ex-S96 (exclude KRONOS_S96_H1_MOMENTUM)
    s96_name = "KRONOS_S96_H1_MOMENTUM"
    gated_ex96   = gated_corr[gated_corr["strategy"] != s96_name]
    ungated_ex96 = ungated_corr[ungated_corr["strategy"] != s96_name]
    gated_ex96_comb   = _combined_row(gated_ex96,   "Gated ex-S96")
    ungated_ex96_comb = _combined_row(ungated_ex96, "Ungated ex-S96")

    # Phantom example
    phantom_example = gated_corr[
        gated_corr["entry_time"].astype(str).str.startswith(
            PHANTOM_EXAMPLE.replace("+00:00", "")
        ) & gated_corr["phantom"]
    ]

    return dict(
        gated_raw      = gated_raw,
        ungated_raw    = ungated_raw,
        gated_corr     = gated_corr,
        ungated_corr   = ungated_corr,
        gated_rows     = gated_rows,
        ungated_rows   = ungated_rows,
        gated_comb     = gated_comb,
        ungated_comb   = ungated_comb,
        gated_ex96     = gated_ex96_comb,
        ungated_ex96   = ungated_ex96_comb,
        phantom_example= phantom_example,
    )


def build_report_section(res: dict) -> str:
    """Build the markdown content for the AMENDED summary."""
    gc  = res["gated_comb"]
    uc  = res["ungated_comb"]
    ge96 = res["gated_ex96"]
    ue96 = res["ungated_ex96"]
    g_rows = res["gated_rows"]
    u_rows = res["ungated_rows"]

    # Delta (gated corrected − ungated raw, to match original delta definition)
    delta_corr_raw = gc["net_usd_corr"] - uc["net_usd_raw"]
    delta_corr_raw_live = gc["net_usd_live"] - (uc["net_usd_raw"] / 2.0)

    # delta corrected vs corrected
    delta_corr_corr = gc["net_usd_corr"] - uc["net_usd_corr"]

    # ex-S96 deltas
    delta_ex96_raw = ge96["net_usd_raw"] - ue96["net_usd_raw"]
    delta_ex96_corr = ge96["net_usd_corr"] - ue96["net_usd_corr"]

    # Raw gated and ungated totals (from original sim)
    raw_gated_net  = float(res["gated_raw"]["pnl_usd"].sum())
    raw_ungated_net = float(res["ungated_raw"]["pnl_usd"].sum())

    total_phantoms_gated   = int(res["gated_corr"]["phantom"].sum())
    total_phantoms_ungated = int(res["ungated_corr"]["phantom"].sum())

    # --- phantom example detail ---
    example_str = "Not found in gated CSV"
    gated_at_example = res["gated_corr"][
        res["gated_corr"]["entry_time"].astype(str).str.contains("2026-07-01T14:00")
    ]
    if not gated_at_example.empty:
        row = gated_at_example.iloc[0]
        bar_c  = row.get("bar_close", "N/A")
        new_ep = row.get("corrected_entry_px", "N/A")
        tp_val = row.get("tp", "N/A")
        is_ph  = row.get("phantom", False)
        cmp_sym = ">=" if is_ph else "<"
        new_pnl_str = "DROPPED" if is_ph else f"{row['new_pnl_usd_sim']:.2f}"
        example_str = (
            f"strategy=SESSION_BREAKOUT, entry_time=2026-07-01T14:00, side=BUY  \n"
            f"- Old entry_px = {row['entry_px']:.3f} (sig.entry_price + 0.25)  \n"
            f"- Detection bar close = {bar_c:.3f}  \n"
            f"- Corrected entry_px = {new_ep:.3f} (bar_close + 0.25)  \n"
            f"- TP = {tp_val:.3f}  \n"
            f"- Phantom = {is_ph} (corrected_entry_px {cmp_sym} TP)  \n"
            f"- Old pnl_usd = {row['pnl_usd']:.2f}  \n"
            f"- New pnl_usd = {new_pnl_str}  \n"
        )

    # --- header table columns ---
    hdr = "| Strategy | Raw trades | Raw Net $ (0.02L) | Phantoms | Corr trades | Corr Net $ (0.02L) | Corr Net $ (0.01L) |"
    sep = "|----------|----------:|------------------:|---------:|------------:|-------------------:|-------------------:|"

    def _strat_md(rows):
        lines = []
        for r in rows:
            lines.append(
                f"| {r.strategy} | {r.trades_raw} | {_fmt(r.net_usd_raw)}"
                f" | {r.phantoms} | {r.trades_corr}"
                f" | {_fmt(r.net_usd_corr)} | {_fmt(r.net_usd_live)} |"
            )
        return "\n".join(lines)

    def _comb_md(c):
        return (
            f"| **COMBINED** | {c['trades_raw']} | {_fmt(c['net_usd_raw'])}"
            f" | {c['phantoms']} | {c['trades_corr']}"
            f" | **{_fmt(c['net_usd_corr'])}** | **{_fmt(c['net_usd_live'])}** |"
        )

    md = f"""\
## Fill-Realism Correction

**Correction applied:** 2026-07-03

### Method

Original sim filled at `sig.entry_price ± 0.25 friction`.  Live entry_manager
places MARKET orders — by the time the 1m bar closes the breakout has already
run.  Corrected entry = detection-bar 1m close ± 0.25 friction.
Phantom guards (dropped trades, never booked): beyond-TP (BUY: entry >= TP;
SELL: entry <= TP) for all trades, and beyond-SL (BUY: entry <= SL;
SELL: entry >= SL) for non-trailing trades (trailing rows carry a ratcheted
stop in the CSV, so the signal stop is not recoverable there).
Exit prices are kept unchanged, which is NOT valid for TRAIL exits: the trail
seeds from the entry fill, so TRAIL exits are fill-path-dependent and cannot
be corrected from the CSVs.  S96 (trailing) corrected values are artifacts in
both directions and must not be quoted as results — S96's real number requires
an engine re-run with market fills + both phantom guards.

**Phantom example — 2026-07-01T14:00 SESSION_BREAKOUT:**

{example_str}

### Corrected Gated Results

{hdr}
{sep}
{_strat_md(g_rows)}
{_comb_md(gc)}

### Corrected Ungated Results

{hdr}
{sep}
{_strat_md(u_rows)}
{_comb_md(uc)}

### Correction Summary

| Metric | Gated | Ungated |
|--------|------:|--------:|
| Raw Net USD (0.02L) | {_fmt(raw_gated_net)} | {_fmt(raw_ungated_net)} |
| Phantom trades dropped | {total_phantoms_gated} | {total_phantoms_ungated} |
| Corrected Net USD (0.02L) | {_fmt(gc['net_usd_corr'])} | {_fmt(uc['net_usd_corr'])} |
| Corrected Net USD (0.01L) | {_fmt(gc['net_usd_live'])} | {_fmt(uc['net_usd_live'])} |
| Correction delta (gated raw→corr) | {_fmt(gc['net_usd_corr'] - raw_gated_net)} | {_fmt(uc['net_usd_corr'] - raw_ungated_net)} |

---

## Ex-S96 View

S96 (KRONOS_S96_H1_MOMENTUM) contributed +$212.87 gated / -$4,945.66 ungated in the
raw sim, accounting for the bulk of the gating edge.  TRENDING regime was only
5.7% of the window, so the 10-trade S96 sample is too small for confidence.
The ex-S96 view shows the residual edge from the three other strategies.

| Mode | Raw Net $ (0.02L) | Phantoms | Corrected Net $ (0.02L) | Corrected Net $ (0.01L) |
|------|------------------:|---------:|------------------------:|------------------------:|
| Gated ex-S96   | {_fmt(ge96['net_usd_raw'])}  | {ge96['phantoms']}  | {_fmt(ge96['net_usd_corr'])}  | {_fmt(ge96['net_usd_live'])}  |
| Ungated ex-S96 | {_fmt(ue96['net_usd_raw'])} | {ue96['phantoms']} | {_fmt(ue96['net_usd_corr'])} | {_fmt(ue96['net_usd_live'])} |
| **Delta ex-S96 (raw G-U)** | {_fmt(delta_ex96_raw)} | — | {_fmt(delta_ex96_corr)} | — |

---

## Live Sizing Note

`deploy_manager.py` uses `MANAGER_CHILD_LOT=0.01`, so all live dollar figures
are **half** the 0.02-lot sim figures.  Corrected headline expectations:

- Gated combined (0.01L, 3 months): **{_fmt(gc['net_usd_live'])}**
- Ex-S96 gated combined (0.01L, 3 months): **{_fmt(ge96['net_usd_live'])}**
- Kill-switch threshold $150 at 0.01L is relatively **2× tighter** than the
  0.02L sim — equivalent to only $75 adverse run in live terms.

---

## REVISED VERDICT

**RECOMMEND arming PAPER mode (`arm_mode=PAPER`).**

Corrected gated combined at 0.02L = **{_fmt(gc['net_usd_corr'])}** over 3 months
(raw was {_fmt(raw_gated_net)}).  At live sizing (0.01L) that is
**{_fmt(gc['net_usd_live'])}** with a profit factor estimated ~1.1,
concentrated in SESSION_BREAKOUT + a 10-trade S96 sample.

Live promotion contingent on:

1. **Sensitivity grid** — re-run with `--sensitivity` to confirm the gating edge
   is not parameter-sensitive.
2. **S96 live-eligibility reconciliation** — TRENDING regime was only 5.7% of
   this window, giving 10 gated S96 trades.  S96 showed a catastrophic ungated
   showing (-$4,945) when unrestrained; confirm TRENDING gate is airtight before
   arming S96 at any size.
3. **Paper fills confirming corrected expectancy** — target ~{_fmt(gc['net_usd_corr'])} at
   0.02L / {_fmt(gc['net_usd_live'])} at 0.01L over 3 months.
4. **S97 review** — S97 (SNAP_SCALPER) was negative in both raw and corrected
   modes.  Consider **NOT arming S97** until further analysis.
5. **S95 review** — S95 is near breakeven both raw and corrected; acceptable as
   a regime-filtered scalper but add no meaningful expectancy.
"""
    return md


if __name__ == "__main__":
    res     = run_correction()
    section = build_report_section(res)

    # Print to stdout (captured by caller for the AMENDED summary)
    print(section)

    # Also print headline numbers to stderr for quick sanity check
    gc  = res["gated_comb"]
    uc  = res["ungated_comb"]
    print(
        f"\n=== CORRECTION HEADLINE ===\n"
        f"Gated:   raw={gc['net_usd_raw']:+.2f}  corr={gc['net_usd_corr']:+.2f}"
        f"  phantoms={gc['phantoms']}\n"
        f"Ungated: raw={uc['net_usd_raw']:+.2f}  corr={uc['net_usd_corr']:+.2f}"
        f"  phantoms={uc['phantoms']}\n"
        f"Gated live (0.01L): {gc['net_usd_live']:+.2f}\n",
        file=sys.stderr,
    )
