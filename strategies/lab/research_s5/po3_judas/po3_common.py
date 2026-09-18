"""po3_common.py -- shared loader / day windows / sweep detection / quote walk for the
Power-of-Three (Judas swing) S5 study.  See PROTOCOL.md for every definition.

Run from `strategies/`:  ../.venv/bin/python -m lab.research_s5.po3_judas.run_descriptive
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
STRAT = HERE.parents[2]           # .../strategies
if str(STRAT) not in sys.path:
    sys.path.insert(0, str(STRAT))

from lab.tools.qa_s5_cache import load_s5          # noqa: E402

RESULTS = HERE / "results"
WIN0, WIN1 = "2024-09-01", "2026-09-18"             # [WIN0, WIN1) -> last day 2026-09-17
SPLIT = "2025-12-01"
H = 3600
MIN_BARS = dict(asia=2500, london=1080, ny=3900)    # ~50 % of the weekday medians (PROTOCOL §2)
STOP_PAD = 0.2
D1_PATH = STRAT / "backtest" / "results" / "bars_cache_2y" / "is_XAU_USD_1d.parquet"


class S5:
    """The whole cache as flat numpy arrays; `t` is epoch seconds (UTC)."""

    def __init__(self) -> None:
        df = load_s5()
        self.t = df["time"].values.astype("datetime64[s]").astype(np.int64)
        for k in ("o", "h", "l", "c", "bid_c", "ask_c"):
            setattr(self, k, df[k].to_numpy(np.float64))
        self.spread = self.ask_c - self.bid_c
        self.n = len(self.t)

    def idx(self, epoch: int) -> int:
        """index of the first bar with t >= epoch"""
        return int(np.searchsorted(self.t, epoch, "left"))


def ts(epoch) -> pd.Timestamp:
    return pd.Timestamp(int(epoch), unit="s", tz="UTC")


def utc_days() -> list[pd.Timestamp]:
    days = pd.date_range(WIN0, WIN1, freq="D", tz="UTC")[:-1]
    return [d for d in days if d.weekday() < 5]


def london_open(date: pd.Timestamp, anchor: str) -> int:
    d0 = int(date.timestamp())
    if anchor == "A3":
        return int(pd.Timestamp(date.strftime("%Y-%m-%d") + " 08:00", tz="Europe/London").timestamp())
    return d0 + 7 * H


def day_bounds(date: pd.Timestamp, anchor: str) -> tuple[int, int]:
    d0 = int(date.timestamp())
    if anchor == "A2":
        prev = date - pd.Timedelta(days=1)
        a = pd.Timestamp(prev.strftime("%Y-%m-%d") + " 17:00", tz="America/New_York")
        b = pd.Timestamp(date.strftime("%Y-%m-%d") + " 17:00", tz="America/New_York")
        return int(a.timestamp()), int(b.timestamp())
    return d0, d0 + 24 * H


def quality_ok(s5: S5, date: pd.Timestamp) -> bool:
    d0 = int(date.timestamp())
    n_asia = s5.idx(d0 + 7 * H) - s5.idx(d0)
    n_lon = s5.idx(d0 + 10 * H) - s5.idx(d0 + 7 * H)
    n_ny = s5.idx(d0 + 21 * H) - s5.idx(d0 + 10 * H)
    return n_asia >= MIN_BARS["asia"] and n_lon >= MIN_BARS["london"] and n_ny >= MIN_BARS["ny"]


def trading_days(s5: S5) -> list[pd.Timestamp]:
    return [d for d in utc_days() if quality_ok(s5, d)]


def bucket(epoch: int, d0: int, anchor: str) -> str:
    """extreme-location bucket (PROTOCOL §3)"""
    s = epoch - d0
    if s < 0:
        return "prev-evening"
    h = s / H
    if h < 7:
        return "Asia"
    if h < 10:
        return "London"
    if h < 16:
        return "post-London"
    if h < 21:
        return "NY late"
    return "post-break"


# ---------------------------------------------------------------- sweep detection

def first_breaches(s5: S5, r_lo: float, r_hi: float, w0: int, w1: int, end: int) -> list[dict]:
    """First breach of each side of [r_lo, r_hi] inside bar window [w0, w1) (indices),
    with the reclaim searched up to bar index `end` (exclusive). One dict per side that
    breached; `reclaim_idx` is None when the close never returns inside before `end`."""
    out = []
    h, l, c = s5.h[w0:w1], s5.l[w0:w1], s5.c
    for side, mask in (("UP", h > r_hi), ("DOWN", l < r_lo)):
        if not mask.any():
            continue
        b = w0 + int(np.argmax(mask))
        if side == "UP":
            back = c[b + 1:end] < r_hi
        else:
            back = c[b + 1:end] > r_lo
        rec = b + 1 + int(np.argmax(back)) if back.any() else None
        seg_end = (rec + 1) if rec is not None else end
        if side == "UP":
            ext = float(s5.h[b:seg_end].max()); depth = ext - r_hi
        else:
            ext = float(s5.l[b:seg_end].min()); depth = r_lo - ext
        out.append(dict(side=side, breach_idx=b, reclaim_idx=rec, extreme=ext, depth=depth,
                        duration=(s5.t[rec] - s5.t[b]) if rec is not None else np.nan))
    out.sort(key=lambda d: d["breach_idx"])
    return out


# ---------------------------------------------------------------- quote walk

def walk(s5: S5, e: int, end: int, long_: bool, sl: float, tp: float):
    """Fill at bar e's quote; check bars e+1 .. end (inclusive; `end` is the time-exit
    bar). Long: stop when bid_c <= sl, target when bid_c >= tp; short on the ask. Stop
    before target within a bar. Returns (outcome, exit_idx, exit_px)."""
    if e + 1 > end:
        return None
    px = s5.bid_c[e + 1:end + 1] if long_ else s5.ask_c[e + 1:end + 1]
    if long_:
        m_sl, m_tp = px <= sl, px >= tp
    else:
        m_sl, m_tp = px >= sl, px <= tp
    i_sl = int(np.argmax(m_sl)) if m_sl.any() else None
    i_tp = int(np.argmax(m_tp)) if m_tp.any() else None
    if i_sl is not None and (i_tp is None or i_sl <= i_tp):
        return "SL", e + 1 + i_sl, sl
    if i_tp is not None:
        return "TP", e + 1 + i_tp, tp
    return "TIME", end, float(px[-1])


def entry_quote(s5: S5, e: int, long_: bool) -> float:
    return float(s5.ask_c[e]) if long_ else float(s5.bid_c[e])


# ---------------------------------------------------------------- D1 bias / gold months

def d1_bias() -> pd.Series:
    """prior-day close vs 20-day SMA, keyed by the UTC date it applies TO (i.e. shifted)."""
    d = pd.read_parquet(D1_PATH)
    d["time"] = pd.to_datetime(d["time"], utc=True).dt.normalize()
    d = d.sort_values("time").set_index("time")
    sma = d["close"].rolling(20).mean()
    bias = np.sign(d["close"] - sma)            # +1 above, -1 below, NaN first 19 days
    bias.index = bias.index + pd.Timedelta(days=1)   # applies to the NEXT day
    full = pd.date_range(bias.index.min(), bias.index.max() + pd.Timedelta(days=3), freq="D", tz="UTC")
    return bias.reindex(full).ffill()           # weekend/holiday -> last known prior close


def gold_monthly() -> pd.Series:
    d = pd.read_parquet(D1_PATH)
    t = pd.to_datetime(d["time"], utc=True).dt.tz_convert(None)
    d = d.assign(time=t).sort_values("time").set_index("time")
    m = d["close"].resample("ME").agg(["first", "last"])
    g = (m["last"] / m["first"] - 1.0) * 100.0
    g.index = g.index.strftime("%Y-%m")
    return g.rename("gold_pct")


# ---------------------------------------------------------------- stats

def pf(x: np.ndarray) -> float:
    gw = x[x > 0].sum(); gl = -x[x <= 0].sum()
    return float(gw / gl) if gl > 0 else float("inf")


def book(df: pd.DataFrame, cost: float) -> dict:
    if not len(df):
        return dict(n=0, pf=np.nan, pts=0.0, wr=np.nan, mean=np.nan, sd=np.nan)
    p = df["raw_pts"].to_numpy() - cost
    return dict(n=int(len(p)), pf=round(pf(p), 3), pts=round(float(p.sum()), 1),
                wr=round(100 * float((p > 0).mean()), 1), mean=round(float(p.mean()), 3),
                sd=round(float(p.std(ddof=1)) if len(p) > 1 else np.nan, 2))


def bars(df: pd.DataFrame, cost_base: float = 0.45, cost_stress: float = 0.80, split: str = SPLIT) -> dict:
    """xau2y bars 1-5 for one arm (PROTOCOL §6)."""
    et = pd.to_datetime(df["entry_time"], utc=True)
    cut = pd.Timestamp(split, tz="UTC")
    tr, te = df[et < cut], df[et >= cut]
    out = dict(train_n=len(tr), test_n=len(te))
    for lab, sub in (("train", tr), ("test", te)):
        for cst in (cost_base, cost_stress):
            b = book(sub, cst)
            out[f"{lab}_pf_{cst}"] = b["pf"]; out[f"{lab}_pts_{cst}"] = b["pts"]
    # bar 4 at base cost on TEST months
    if len(te):
        m = (te["raw_pts"] - cost_base).groupby(et[et >= cut].dt.strftime("%Y-%m")).sum()
        pos = float((m > 0).mean()); net = float(m.sum())
        mx = float(m.max() / net) if net > 0 else float("inf")
    else:
        pos, mx = 0.0, float("inf")
    out.update(test_months=int(len(te) and m.size), test_pos_share=round(pos, 2),
               test_max_share=(round(mx, 2) if np.isfinite(mx) else None))
    # bar 5 full window at base cost
    fm = (df["raw_pts"] - cost_base).groupby(et.dt.strftime("%Y-%m")).sum()
    j = pd.concat([fm.rename("pts"), gold_monthly()], axis=1).dropna()
    if len(j) >= 6 and j.pts.std() > 0:
        up, dn = j[j.gold_pct > 0], j[j.gold_pct <= 0]
        corr = float(np.corrcoef(j.pts, j.gold_pct)[0, 1])
        out.update(gold_corr=round(corr, 2), up_pts=round(float(up.pts.sum()), 1), dn_pts=round(float(dn.pts.sum()), 1))
        b5 = (up.pts.sum() > 0 and dn.pts.sum() > 0) or abs(corr) < 0.4
    else:
        out.update(gold_corr=None, up_pts=None, dn_pts=None); b5 = False
    out["bar1_n"] = out["test_n"] >= 40
    out["bar2_base"] = bool(out[f"test_pf_{cost_base}"] > 1.0)
    out["bar2_stress"] = bool(out[f"test_pf_{cost_stress}"] > 1.0)
    out["bar3_train"] = bool(out[f"train_pf_{cost_base}"] > 0.9)
    out["bar4_monthly"] = bool(pos >= 0.55 and mx <= 0.5)
    out["bar5_regime"] = bool(b5)
    keys = ["bar1_n", "bar2_base", "bar2_stress", "bar3_train", "bar4_monthly", "bar5_regime"]
    out["bars_passed"] = int(sum(out[k] for k in keys))
    out["verdict"] = "PASS" if out["bars_passed"] == 6 else ("NEAR" if out["bars_passed"] == 5 else "FAIL")
    return out


class Tee:
    def __init__(self, path: Path):
        self.f = open(path, "w", encoding="utf-8")

    def __call__(self, *a):
        s = " ".join(str(x) for x in a)
        print(s); self.f.write(s + "\n"); self.f.flush()
