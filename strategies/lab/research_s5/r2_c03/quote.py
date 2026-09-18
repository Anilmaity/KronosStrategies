"""quote.py -- re-resolve every arm's exits on the S5 quote (the live trigger convention).

Vectorised replica of lab/s5exit.resolve(mode="quote_s5", start_offset_s=60), copied from
research_s5/execution/03_geometry.py (validated there against lab/results/s5exit on 100.000 %
of c03 trades). Long: stop and target on the BID; short: on the ASK; stop checked before target
inside a bar; the walk starts 60 s after the signal bar's open; horizon 45 days (c03 has no
time exit). Writes <arm>.quote.parquet next to each <arm>.trades.parquet with cost-free points
`quote_pts0` (score.py subtracts 0.45 / 0.70) and the fill-time diagnostics used by H7.

    cd strategies && ../.venv/bin/python lab/research_s5/r2_c03/quote.py [--check]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
STRAT = HERE.parents[2]
sys.path.insert(0, str(STRAT))
sys.path.insert(0, str(STRAT / "lab" / "research_s5" / "execution"))
import common as xcommon   # noqa: E402  -- execution study's S5 loader (npz mirror in the scratchpad)

RES = HERE / "results"
HORIZON_S = 45 * 24 * 3600
CHUNKS = (17_280, 120_960, 10**9)

_S5 = None


def s5():
    global _S5
    if _S5 is None:
        _S5 = xcommon.load()
    return _S5


def _resolve_one(d, i, j, long_, sl, tp):
    if j <= i:
        return None, -1
    a = i
    for ch in CHUNKS:
        b = min(j, a + ch)
        lo = d.bid[a:b] if long_ else d.ask[a:b]
        if long_:
            sh, th = lo <= sl, lo >= tp
        else:
            sh, th = lo >= sl, lo <= tp
        ks = int(np.argmax(sh)) if sh.any() else None
        kt = int(np.argmax(th)) if th.any() else None
        if ks is not None and (kt is None or ks <= kt):
            return "SL", a + ks
        if kt is not None:
            return "TP", a + kt
        a = b
        if a >= j:
            break
    return "TIME", j - 1


def resolve_frame(df: pd.DataFrame) -> pd.DataFrame:
    d = s5()
    T = d.t.astype("datetime64[s]")
    ent = pd.to_datetime(df.entry_time, utc=True).dt.tz_convert(None).to_numpy("datetime64[s]")
    i = np.searchsorted(T, ent + np.timedelta64(60, "s"), "right")
    j = np.searchsorted(T, ent + np.timedelta64(HORIZON_S, "s"), "right")
    long_ = (df.side == "BUY").to_numpy()
    sl, tp, epx = df.sl.to_numpy(float), df.tp.to_numpy(float), df.entry_px.to_numpy(float)
    oc, k, px = [], np.empty(len(df), int), np.empty(len(df))
    for n in range(len(df)):
        o, kk = _resolve_one(d, int(i[n]), int(j[n]), bool(long_[n]), sl[n], tp[n])
        oc.append(o); k[n] = kk
        px[n] = sl[n] if o == "SL" else tp[n] if o == "TP" else (d.c[kk] if kk >= 0 else np.nan)
    oc = np.array(oc, dtype=object)
    raw = np.where(long_, px - epx, epx - px)
    r = pd.DataFrame({"entry_time": df.entry_time.to_numpy(), "side": df.side.to_numpy(),
                      "risk": np.abs(epx - sl), "quote_outcome": oc, "quote_pts0": raw})
    ok = i < len(T)
    ii = np.minimum(i, len(T) - 1)
    r["spread_entry"] = np.where(ok, d.spread[ii], np.nan)
    # market mid at the signal bar's close (last S5 close inside the entry minute) and later LTPs
    km = np.clip(np.searchsorted(T, ent + np.timedelta64(60, "s"), "left") - 1, 0, len(T) - 1)
    r["m1c"] = d.c[km]
    r["nominal_gap"] = np.where(long_, d.c[km] - epx, epx - d.c[km])
    for delta in (5, 15, 30):
        kd = np.clip(np.searchsorted(T, ent + np.timedelta64(60 + delta, "s"), "right") - 1, 0, len(T) - 1)
        r[f"ltp_{delta}s"] = d.c[kd]
        r[f"drift_{delta}s"] = np.where(long_, d.c[kd] - epx, epx - d.c[kd])
    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="validate against lab/results/s5exit/c03_fvg_fill_c0.80.parquet")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.check:
        ref = pd.read_parquet(STRAT / "lab" / "results" / "s5exit" / "c03_fvg_fill_c0.80.parquet")
        ref["entry_time"] = pd.to_datetime(ref.entry_time, utc=True)
        tr = pd.read_parquet(STRAT / "lab" / "results" / "xau2y_stage1" / "c03_fvg_fill_c0.80.trades.parquet")
        tr["entry_time"] = pd.to_datetime(tr.entry_time, utc=True)
        m = tr.merge(ref[["entry_time", "quote_s5_outcome", "quote_s5_pts"]], on="entry_time")
        q = resolve_frame(m)
        agree = (q.quote_outcome.to_numpy() == m.quote_s5_outcome.to_numpy()).mean()
        pts = np.isclose(q.quote_pts0 - 0.80, m.quote_s5_pts, atol=1e-6).mean()
        print(f"check vs s5exit: {len(m)} trades, outcome agree {agree*100:.3f} %, pts agree {pts*100:.3f} %")
    for t_path in sorted(RES.glob("*.trades.parquet")):
        q_path = t_path.with_name(t_path.name.replace(".trades.parquet", ".quote.parquet"))
        if q_path.exists() and not a.force:
            continue
        tr = pd.read_parquet(t_path)
        if not len(tr):
            continue
        q = resolve_frame(tr)
        q.to_parquet(q_path, index=False)
        print(f"{t_path.name}: {len(q)} resolved; quote pts0 {q.quote_pts0.sum():.1f} vs mid pts0 {(tr.pts + 0.75).sum():.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
