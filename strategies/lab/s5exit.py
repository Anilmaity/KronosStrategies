"""lab/s5exit.py -- re-resolve existing trade exits at 5-second, sided (bid/ask) resolution.

Why this exists
---------------
The harness resolves exits on MID M1 bars with SL checked before TP. Live, a stop sits at
the broker and triggers on the QUOTE, not the mid:

    LONG  stop fires when BID  <= sl        (bid = mid - spread/2)
    SHORT stop fires when ASK  >= sl        (ask = mid + spread/2)

So the true stop sits roughly spread/2 CLOSER to entry than a mid-based model believes.
That is a fixed number of points, which is a large fraction of a 2pt stop and a trivial
fraction of a 10pt stop -- exactly the asymmetry that would make a mid-based backtest
over-state tight-stop performance, and it is the leading candidate explanation for why the
LIVE tight-stop win rate (26.8%) collapsed relative to the harness (~42%).

This module keeps the ENTRIES fixed (same signals, same levels) and only re-resolves the
EXIT, so the comparison isolates the exit model with nothing else moving.

Three models, same trades:
    mid_m1   the harness default -- mid M1 bars, SL checked before TP (pessimistic ordering)
    mid_s5   mid S5 bars in true chronological order (resolves the intrabar ambiguity)
    quote_s5 S5 bid/ask -- the live trigger convention

Usage:
    python -m lab.s5exit --trades lab/results/s93_fvg_scalp_base_c0.45_s1.5_hnone.csv
"""
from __future__ import annotations

import argparse
import glob
from pathlib import Path

import numpy as np
import pandas as pd

S5DIR = Path(__file__).resolve().parent.parent / "backtest" / "results" / "bars_cache" / "s5" / "XAU_USD"


def load_s5() -> pd.DataFrame:
    frames = [pd.read_parquet(f) for f in sorted(glob.glob(str(S5DIR / "*.parquet")))]
    d = pd.concat(frames, ignore_index=True)
    d["time"] = pd.to_datetime(d["time"], utc=True)
    return d.sort_values("time").reset_index(drop=True)


def resolve(trade, s5: pd.DataFrame, t5, mode: str, cost: float, max_hold_min: float):
    """Walk S5 bars from entry to the strategy's OWN max-hold horizon and return
    (outcome, exit_px).

    The horizon must be the strategy's max_hold, NOT the M1 model's exit time: a
    different exit model legitimately exits later, and truncating at the M1 exit would
    mis-label every such trade as a time exit.

    `mode` selects which price series the stop/target are tested against:
      mid_s5   -> h/l (mid)
      quote_s5 -> bid_c/ask_c, the live trigger convention
    """
    ent = np.datetime64(trade.entry_time.tz_convert("UTC").tz_localize(None))
    end = ent + np.timedelta64(int(max_hold_min * 60), "s")
    i = int(np.searchsorted(t5, ent, "right"))
    j = int(np.searchsorted(t5, end, "right"))
    if j <= i:
        return None
    w = s5.iloc[i:j]
    if len(w) == 0:
        return None
    long_ = trade.side == "BUY"
    if mode == "quote_s5":
        # LONG exits on the BID (you sell to close); SHORT exits on the ASK.
        px_lo = w["bid_c"].to_numpy(float) if long_ else w["ask_c"].to_numpy(float)
        px_hi = px_lo
    else:
        px_lo = w["l"].to_numpy(float)
        px_hi = w["h"].to_numpy(float)

    for k in range(len(w)):
        if long_:
            if px_lo[k] <= trade.sl:
                return "SL", trade.sl
            if px_hi[k] >= trade.tp:
                return "TP", trade.tp
        else:
            if px_hi[k] >= trade.sl:
                return "SL", trade.sl
            if px_lo[k] <= trade.tp:
                return "TP", trade.tp
    return "TIME", float(w.iloc[-1]["c"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", required=True)
    ap.add_argument("--cost", type=float, default=0.45)
    ap.add_argument("--max-hold", type=float, required=True,
                    help="the strategy's _MAX_HOLD_MIN (S93 120, S99 480, S94 1200, S100 72)")
    a = ap.parse_args()

    s5 = load_s5()
    lo, hi = s5.time.min(), s5.time.max()
    t5 = s5["time"].dt.tz_convert(None).to_numpy("datetime64[ns]")

    d = pd.read_csv(a.trades, parse_dates=["entry_time", "exit_time"])
    horizon = pd.Timedelta(minutes=a.max_hold)
    d = d[(d.entry_time >= lo) & (d.entry_time + horizon <= hi)].reset_index(drop=True)
    print(f"trades inside S5 coverage ({lo.date()} .. {hi.date()}): {len(d)}")
    if not len(d):
        return

    out = []
    for t in d.itertuples():
        row = {"entry_time": t.entry_time, "side": t.side, "risk": t.risk,
               "mid_m1_outcome": t.outcome, "mid_m1_pts": t.pts}
        for mode in ("mid_s5", "quote_s5"):
            r = resolve(t, s5, t5, mode, a.cost, a.max_hold)
            if r is None:
                row[f"{mode}_pts"] = np.nan
                row[f"{mode}_outcome"] = None
                continue
            oc, px = r
            raw = (px - t.entry_px) if t.side == "BUY" else (t.entry_px - px)
            row[f"{mode}_outcome"] = oc
            row[f"{mode}_pts"] = raw - a.cost
        out.append(row)
    r = pd.DataFrame(out).dropna(subset=["quote_s5_pts"])
    print(f"resolved at S5: {len(r)}")

    r["bucket"] = pd.cut(r.risk, [0, 2, 3, 4, 6, 100],
                         labels=["<2", "2-3", "3-4", "4-6", "6+"])

    def blk(col_pts, col_oc, label):
        g = r.groupby("bucket", observed=True).apply(
            lambda x: pd.Series({
                "n": len(x),
                "wr": round(100 * (x[col_pts] > 0).mean(), 1),
                "pts": round(x[col_pts].sum(), 1)}), include_groups=False)
        g.columns = pd.MultiIndex.from_product([[label], g.columns])
        return g

    print("\n=== same entries, three exit models, by stop-distance bucket ===")
    tbl = pd.concat([blk("mid_m1_pts", "mid_m1_outcome", "mid_M1"),
                     blk("mid_s5_pts", "mid_s5_outcome", "mid_S5"),
                     blk("quote_s5_pts", "quote_s5_outcome", "quote_S5")], axis=1)
    print(tbl.to_string())

    print("\n=== totals ===")
    for c, lab in (("mid_m1_pts", "mid_M1  "), ("mid_s5_pts", "mid_S5  "),
                   ("quote_s5_pts", "quote_S5")):
        print(f"  {lab}  n={len(r)}  pts={r[c].sum():+8.1f}  wr={100*(r[c]>0).mean():.1f}%")

    print("\n=== outcome flips vs the mid_M1 model ===")
    print(pd.crosstab(r.mid_m1_outcome, r.quote_s5_outcome, margins=True).to_string())


if __name__ == "__main__":
    main()
