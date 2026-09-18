"""Vectorised re-implementation of lab.s5exit.resolve(mode="quote_s5") for whole trade lists.

Semantics copied exactly (PROTOCOL.md §5): walk S5 bars strictly after entry_time + 60 s up to
entry_time + max_hold; a LONG's stop and target are tested on the BID close, a SHORT's on
the ASK close; stop before target within a bar; no hit -> TIME at the last bar's mid close.
`validate()` asserts exact agreement with the shared resolver on a trade list before use.

    python -m lab.research_s5.r2_htf_overlay.s5quote --validate   # c03 + s14, every trade
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_STRAT = Path(__file__).resolve().parents[3]
if str(_STRAT) not in sys.path:
    sys.path.insert(0, str(_STRAT))

from lab.s5exit import load_s5, resolve as shared_resolve   # noqa: E402

# horizons in minutes (PROTOCOL.md §5)
HORIZON_MIN = {
    "s93_fvg_scalp": 120, "s95_session_breakout": 180, "s100_m3_combo": 72,
    "s97_snap_scalper_m5": 30, "s98_zscore_mr_m15": 240, "s94_sweep_reversal": 1200,
    "s99_mss_fvg": 480,
    "c03_fvg_fill": 7200, "s14_ob_mit_bias": 1000, "s03_ob_mitigation": 3700,
    "s04_breaker_block": 1100, "s10_90min_fade": 5700, "s11_m90_fade_ny": 1500,
    "s12_m90_fade_bias": 300, "s96_h1_momentum": 23500,
}


class S5Quotes:
    def __init__(self, s5: pd.DataFrame | None = None):
        self.s5 = s5 if s5 is not None else load_s5()
        self.t5 = self.s5["time"].dt.tz_convert(None).to_numpy("datetime64[ns]")
        self.bid = self.s5["bid_c"].to_numpy(float)
        self.ask = self.s5["ask_c"].to_numpy(float)
        self.mid_c = self.s5["c"].to_numpy(float)
        self.lo, self.hi = self.s5["time"].min(), self.s5["time"].max()

    def coverage_mask(self, trades: pd.DataFrame, max_hold_min: float) -> np.ndarray:
        h = pd.Timedelta(minutes=max_hold_min)
        return ((trades["entry_time"] >= self.lo) & (trades["entry_time"] + h <= self.hi)).to_numpy()

    def resolve_frame(self, trades: pd.DataFrame, max_hold_min: float,
                      start_offset_s: int = 60) -> pd.DataFrame:
        """Returns outcome / exit_px / raw_pts (no cost) per trade; NaN where unresolvable."""
        ent = trades["entry_time"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy("datetime64[ns]")
        end = ent + np.timedelta64(int(max_hold_min * 60), "s")
        i0 = np.searchsorted(self.t5, ent + np.timedelta64(start_offset_s, "s"), "right")
        j0 = np.searchsorted(self.t5, end, "right")
        side = trades["side"].to_numpy()
        sl = trades["sl"].to_numpy(float)
        tp = trades["tp"].to_numpy(float)
        epx = trades["entry_px"].to_numpy(float)
        n = len(trades)
        out_oc = np.full(n, None, dtype=object)
        out_px = np.full(n, np.nan)
        for k in range(n):
            i, j = int(i0[k]), int(j0[k])
            if j <= i:
                continue
            long_ = side[k] == "BUY"
            px = self.bid[i:j] if long_ else self.ask[i:j]
            if long_:
                hit_sl = px <= sl[k]
                hit_tp = px >= tp[k]
            else:
                hit_sl = px >= sl[k]
                hit_tp = px <= tp[k]
            ks = int(np.argmax(hit_sl)) if hit_sl.any() else -1
            kt = int(np.argmax(hit_tp)) if hit_tp.any() else -1
            if ks < 0 and kt < 0:
                out_oc[k], out_px[k] = "TIME", self.mid_c[j - 1]
            elif ks >= 0 and (kt < 0 or ks <= kt):
                out_oc[k], out_px[k] = "SL", sl[k]
            else:
                out_oc[k], out_px[k] = "TP", tp[k]
        raw = np.where(side == "BUY", out_px - epx, epx - out_px)
        return pd.DataFrame({"q_outcome": out_oc, "q_exit_px": out_px, "q_raw": raw}, index=trades.index)


def validate(q: S5Quotes, trades: pd.DataFrame, max_hold_min: float, label: str) -> None:
    mask = q.coverage_mask(trades, max_hold_min)
    d = trades[mask].reset_index(drop=True)
    mine = q.resolve_frame(d, max_hold_min)
    bad = 0
    for k, t in enumerate(d.itertuples()):
        r = shared_resolve(t, q.s5, q.t5, "quote_s5", 0.0, max_hold_min, start_offset_s=60)
        if r is None:
            if mine.q_outcome[k] is not None:
                bad += 1
            continue
        oc, px = r
        if oc != mine.q_outcome[k] or abs(px - mine.q_exit_px[k]) > 1e-9:
            bad += 1
            if bad <= 5:
                print("  mismatch", label, k, t.entry_time, oc, px, mine.q_outcome[k], mine.q_exit_px[k])
    print(f"validate {label}: n={len(d)} mismatches={bad}")
    assert bad == 0, f"{label}: vectorised resolver disagrees with lab.s5exit.resolve"


if __name__ == "__main__":
    if "--validate" in sys.argv:
        q = S5Quotes()
        for mod in ("c03_fvg_fill", "s14_ob_mit_bias", "s93_fvg_scalp"):
            tr = pd.read_parquet(_STRAT / "lab" / "results" / "xau2y_stage1" / f"{mod}_c0.80.trades.parquet")
            tr["entry_time"] = pd.to_datetime(tr["entry_time"], utc=True)
            validate(q, tr, HORIZON_MIN[mod], mod)
        # reproduction check against the published s5exit files (cost 0.80)
        for mod in ("c03_fvg_fill", "s14_ob_mit_bias"):
            pub = pd.read_parquet(_STRAT / "lab" / "results" / "s5exit" / f"{mod}_c0.80.parquet")
            tr = pd.read_parquet(_STRAT / "lab" / "results" / "xau2y_stage1" / f"{mod}_c0.80.trades.parquet")
            tr["entry_time"] = pd.to_datetime(tr["entry_time"], utc=True)
            m = q.coverage_mask(tr, HORIZON_MIN[mod])
            d = tr[m].reset_index(drop=True)
            mine = q.resolve_frame(d, HORIZON_MIN[mod])
            mine_pts = mine.q_raw - 0.80
            print(f"reproduction {mod}: published n={len(pub)} pts={pub.quote_s5_pts.sum():.1f} | "
                  f"mine n={len(d)} pts={mine_pts.sum():.1f}")
