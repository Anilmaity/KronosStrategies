"""crt_lib.py -- Candle Range Theory (CRT) at intraday grids with quote-level execution.

Implements PROTOCOL.md in this folder. Everything here is pure computation over numpy
arrays; run_crt.py drives it and writes results/.

Grids: H1_UTC, H4_UTC, H4_NY (17:00-ET anchored 4h), D1_UTC and D1_NY (bias parents only).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

NY = "America/New_York"
FILL_TOL = np.timedelta64(15 * 60, "s")      # C3 must open within 15 min of schedule
BAD_LO = np.datetime64("2025-12-25T23:00:00")  # untradeable feed spike (QA note)
BAD_HI = np.datetime64("2025-12-25T23:15:00")

GRIDS = {
    # name: (anchor, step_hours, nominal_minutes)
    "H1_UTC": ("UTC", 1, 60),
    "H4_UTC": ("UTC", 4, 240),
    "H4_NY": ("NY", 4, 240),
    "D1_UTC": ("UTC", 24, 1440),
    "D1_NY": ("NY", 24, 1440),
}
PARENT = {"H1_UTC": "H4_UTC", "H4_UTC": "D1_UTC", "H4_NY": "D1_NY"}


# ----------------------------------------------------------------------------- candles
def build_candles(m1: pd.DataFrame, grid: str) -> pd.DataFrame:
    """Aggregate QA'd M1 (tz-aware UTC `time`) onto a grid. Returns one row per slot with
    key (grid-local slot start), start_utc/end_utc (datetime64[ns], naive UTC), o h l c,
    n (minutes), slot (wall-clock hour of the slot start), first_utc (first M1 time)."""
    anchor, step, nominal = GRIDS[grid]
    t = m1["time"]
    tu = t.dt.tz_convert("UTC").dt.tz_localize(None)
    if anchor == "UTC":
        key = tu.dt.floor(f"{step}h")
        offset = pd.Series(np.zeros(len(t), dtype="timedelta64[ns]"), index=t.index)
    else:
        wall = t.dt.tz_convert(NY).dt.tz_localize(None)
        shift = pd.Timedelta(hours=17)
        key = (wall - shift).dt.floor(f"{step}h") + shift
        offset = wall - tu                       # NY wall minus UTC = utc offset
    g = pd.DataFrame({"key": key, "tu": tu, "off": offset, "o": m1["open"].values,
                      "h": m1["high"].values, "l": m1["low"].values, "c": m1["close"].values})
    agg = g.groupby("key", sort=True).agg(o=("o", "first"), h=("h", "max"), l=("l", "min"),
                                          c=("c", "last"), n=("c", "size"),
                                          first_utc=("tu", "first"), off=("off", "first"))
    agg = agg.reset_index()
    agg["start_utc"] = (agg["key"] - agg["off"]).astype("datetime64[ns]")
    stepd = pd.Timedelta(hours=step)
    nxt_start = agg["start_utc"].shift(-1)
    consecutive_next = (agg["key"].shift(-1) - agg["key"]) == stepd
    agg["end_utc"] = np.where(consecutive_next, nxt_start, agg["start_utc"] + stepd).astype("datetime64[ns]")
    agg["prev_consecutive"] = ((agg["key"] - agg["key"].shift(1)) == stepd).values
    agg["complete"] = agg["n"] >= nominal * 0.5
    agg["slot"] = agg["key"].dt.hour if step < 24 else 0
    agg["nominal"] = nominal
    agg["step_h"] = step
    return agg.drop(columns=["off"])


def wilder_atr(c: pd.DataFrame, n: int = 14) -> np.ndarray:
    h, l, cl = c["h"].values, c["l"].values, c["c"].values
    pc = np.r_[cl[0], cl[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return pd.Series(tr).ewm(alpha=1.0 / n, adjust=False).mean().values


def ema(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).ewm(span=n, adjust=False).mean().values


# ----------------------------------------------------------------------------- S5 arrays
@dataclass
class S5:
    t: np.ndarray      # datetime64[ns] naive UTC
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    bid: np.ndarray
    ask: np.ndarray


def s5_arrays(df: pd.DataFrame) -> S5:
    t = df["time"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy("datetime64[ns]")
    f = lambda k: df[k].to_numpy(np.float64)
    return S5(t, f("o"), f("h"), f("l"), f("c"), f("bid_c"), f("ask_c"))


@dataclass
class M1:
    t: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray


def m1_arrays(df: pd.DataFrame) -> M1:
    t = df["time"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy("datetime64[ns]")
    f = lambda k: df[k].to_numpy(np.float64)
    return M1(t, f("open"), f("high"), f("low"), f("close"))


# ----------------------------------------------------------------------------- walks
def walk_quote(s5: S5, i_entry: int, j_end: int, long_: bool, sl: float, tp: float):
    """Quote-level exit walk over S5 bars (i_entry, j_end). Long tests the BID, short the
    ASK. Stop before target within a bar. Returns (outcome, exit_px, exit_idx)."""
    px = s5.bid[i_entry + 1:j_end] if long_ else s5.ask[i_entry + 1:j_end]
    if len(px) == 0:
        return None
    if long_:
        hs, ht = px <= sl, px >= tp
    else:
        hs, ht = px >= sl, px <= tp
    ks = int(np.argmax(hs)) if hs.any() else -1
    kt = int(np.argmax(ht)) if ht.any() else -1
    if ks >= 0 and (kt < 0 or ks <= kt):
        return "SL", sl, i_entry + 1 + ks
    if kt >= 0:
        return "TP", tp, i_entry + 1 + kt
    return "TIME", float(px[-1]), j_end - 1


def walk_mid(h: np.ndarray, l: np.ndarray, c: np.ndarray, i0: int, j_end: int, long_: bool,
             sl: float, tp: float):
    """Mid high/low exit walk over bars [i0, j_end) -- for M1 (inclusive of the entry bar,
    which opened at the entry) and for mid S5 (call with i0 = entry bar + 1)."""
    hh, ll = h[i0:j_end], l[i0:j_end]
    if len(hh) == 0:
        return None
    if long_:
        hs, ht = ll <= sl, hh >= tp
    else:
        hs, ht = hh >= sl, ll <= tp
    ks = int(np.argmax(hs)) if hs.any() else -1
    kt = int(np.argmax(ht)) if ht.any() else -1
    if ks >= 0 and (kt < 0 or ks <= kt):
        return "SL", sl
    if kt >= 0:
        return "TP", tp
    return "TIME", float(c[j_end - 1])


def fill_index(s5: S5, start: np.datetime64, end: np.datetime64):
    """First S5 bar at/after the scheduled open, within FILL_TOL; and the end index
    (first bar at/after end). Returns (i_entry, j_end) or (None, reason)."""
    i = int(np.searchsorted(s5.t, start, "left"))
    j = int(np.searchsorted(s5.t, end, "left"))
    if i >= len(s5.t) or i >= j:
        return None, "no_data"
    if s5.t[i] - start > FILL_TOL:
        return None, "late_open"
    if j - 1 <= i:
        return None, "no_bars_after_entry"
    return (i, j), None


# ----------------------------------------------------------------------------- events
def detect_events(cd: pd.DataFrame, atr: np.ndarray) -> pd.DataFrame:
    """CRT C2 detection: row i is C2, i-1 is C1, i+1 is C3. Returns a frame of candidate
    events with the candle fields needed downstream (before any fill check)."""
    n = len(cd)
    h, l, c, o = cd["h"].values, cd["l"].values, cd["c"].values, cd["o"].values
    comp, prevc = cd["complete"].values, cd["prev_consecutive"].values
    idx = np.arange(1, n - 1)
    a = atr[idx - 1]                                  # ATR at C1
    ok = (comp[idx] & comp[idx - 1] & prevc[idx] & prevc[idx + 1] & (a > 0) & (idx >= 15))
    up = (h[idx] > h[idx - 1] + 0.05 * a) & (c[idx] < h[idx - 1])
    dn = (l[idx] < l[idx - 1] - 0.05 * a) & (c[idx] > l[idx - 1])
    both = up & dn
    ev = pd.DataFrame({
        "i2": idx, "atr": a, "ok": ok, "sweep_up": up, "sweep_dn": dn, "both": both,
        "c1_h": h[idx - 1], "c1_l": l[idx - 1], "c2_o": o[idx], "c2_h": h[idx], "c2_l": l[idx],
        "c2_c": c[idx], "c3_o": o[idx + 1], "c3_h": h[idx + 1], "c3_l": l[idx + 1], "c3_c": c[idx + 1],
        "c3_start": cd["start_utc"].values[idx + 1], "c3_end": cd["end_utc"].values[idx + 1],
        "c3_slot": cd["slot"].values[idx + 1], "c3_n": cd["n"].values[idx + 1],
    })
    ev = ev[ev.ok & (ev.sweep_up | ev.sweep_dn)].copy()
    ev["side"] = np.where(ev.sweep_up, "SELL", "BUY")
    return ev.reset_index(drop=True)


def bias_series(parent: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """(end_utc sorted, bias_up bool) for the parent grid: EMA20 > EMA50 of closes."""
    e20, e50 = ema(parent["c"].values, 20), ema(parent["c"].values, 50)
    return parent["end_utc"].values.astype("datetime64[ns]"), e20 > e50


def bias_at(ends: np.ndarray, up: np.ndarray, when: np.ndarray) -> np.ndarray:
    """Bias of the last parent candle CLOSED at or before `when` (NaN-safe: -1 if none)."""
    k = np.searchsorted(ends, when, "right") - 1
    out = np.full(len(when), -1, dtype=int)
    m = k >= 0
    out[m] = up[k[m]].astype(int)
    return out


def resolve_events(ev: pd.DataFrame, s5: S5, m1: M1, costs=(0.45, 0.80)) -> pd.DataFrame:
    """Fill + three-way resolution for each event. Adds columns; unfillable events are
    kept with `skip` set so the counts can be reported."""
    rows = []
    for r in ev.itertuples(index=False):
        long_ = r.side == "BUY"
        start, end = np.datetime64(r.c3_start, "ns"), np.datetime64(r.c3_end, "ns")
        d = {"skip": None}
        if r.both:
            d["skip"] = "both_sides_swept"; rows.append(d); continue
        if (start < BAD_HI) and (end > BAD_LO):
            d["skip"] = "spike_window"; rows.append(d); continue
        ij, why = fill_index(s5, start, end)
        if ij is None:
            d["skip"] = why; rows.append(d); continue
        i, j = ij
        fill = s5.ask[i] if long_ else s5.bid[i]
        sl = r.c2_l - 0.1 * r.atr if long_ else r.c2_h + 0.1 * r.atr
        tp = r.c1_h if long_ else r.c1_l
        if not ((sl < fill < tp) if long_ else (tp < fill < sl)):
            d["skip"] = "fill_outside_geometry"; rows.append(d); continue
        d.update(entry_time=s5.t[i], entry_px=fill, sl=sl, tp=tp, risk=abs(fill - sl),
                 reward=abs(tp - fill), i_entry=i, j_end=j, fill_delay_s=float((s5.t[i] - start) / np.timedelta64(1, "s")))
        # quote_s5
        oc, px, k = walk_quote(s5, i, j, long_, sl, tp)
        d.update(q_outcome=oc, q_exit=px, q_raw=(px - fill) if long_ else (fill - px),
                 q_exit_time=s5.t[k])
        # mid_s5: entry at the mid close of the fill bar, exits on mid h/l after it
        oc2, px2 = walk_mid(s5.h, s5.l, s5.c, i + 1, j, long_, sl, tp)
        mfill = s5.c[i]
        d.update(ms_outcome=oc2, ms_raw=(px2 - mfill) if long_ else (mfill - px2))
        # mid_m1: entry at the M1 open of C3, exits on M1 mid h/l inclusive of that bar
        a = int(np.searchsorted(m1.t, start, "left")); b = int(np.searchsorted(m1.t, end, "left"))
        if b > a:
            oc3, px3 = walk_mid(m1.h, m1.l, m1.c, a, b, long_, sl, tp)
            m1fill = m1.o[a]
            d.update(m1_outcome=oc3, m1_raw=(px3 - m1fill) if long_ else (m1fill - px3), m1_fill=m1fill)
        else:
            d.update(m1_outcome=None, m1_raw=np.nan, m1_fill=np.nan)
        rows.append(d)
    out = pd.concat([ev.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    for cst in costs:
        tag = f"{cst:.2f}"
        out[f"pts_c{tag}"] = out["q_raw"] - cst
        out[f"m1_pts_c{tag}"] = out["m1_raw"] - cst
        out[f"ms_pts_c{tag}"] = out["ms_raw"] - cst
    return out


# ----------------------------------------------------------------------------- control (a)
def candle_fill_table(cd: pd.DataFrame, s5: S5) -> pd.DataFrame:
    """Per candle: fillable at open, entry index, end index, and is-signal flag of the
    candle as a C2 (either side) -- the pool for the random-C3 control."""
    starts = cd["start_utc"].values.astype("datetime64[ns]")
    ends = cd["end_utc"].values.astype("datetime64[ns]")
    i = np.searchsorted(s5.t, starts, "left")
    j = np.searchsorted(s5.t, ends, "left")
    i_c = np.minimum(i, len(s5.t) - 1)
    fillable = (i < len(s5.t)) & (i < j) & ((s5.t[i_c] - starts) <= FILL_TOL) & (j - 1 > i)
    return pd.DataFrame({"i_entry": i, "j_end": j, "fillable": fillable})


def random_control(tr: pd.DataFrame, cd: pd.DataFrame, atr: np.ndarray, is_signal: np.ndarray,
                   ft: pd.DataFrame, s5: S5, K: int = 20, seed: int = 20260918,
                   costs=(0.45, 0.80)) -> pd.DataFrame:
    """For each real trade draw K random C3' (same slot, +-30 d, fillable, previous candle
    not a signal) and transplant the trade's ATR-scaled stop/target distances. Returns a
    long frame: one row per (trade, rep)."""
    rng = np.random.default_rng(seed)
    starts = cd["start_utc"].values.astype("datetime64[ns]")
    slots = cd["slot"].values
    n = len(cd)
    cand_ok = ft["fillable"].values.copy()
    # previous candle must exist, be consecutive and NOT a signal; C1' ATR must exist
    prev_sig = np.r_[True, is_signal[:-1]]
    prevc = cd["prev_consecutive"].values
    atr_c1p = np.r_[np.nan, np.nan, atr[:-2]]          # ATR at C1' = candle j-2
    cand_ok &= (~prev_sig) & prevc & (np.arange(n) >= 16) & np.isfinite(atr_c1p) & (atr_c1p > 0)
    win = np.timedelta64(30, "D")
    out = []
    for t in tr.itertuples():
        i3 = t.i2 + 1
        lo = np.searchsorted(starts, starts[i3] - win, "left")
        hi = np.searchsorted(starts, starts[i3] + win, "right")
        pool = np.arange(lo, hi)
        pool = pool[cand_ok[lo:hi] & (slots[lo:hi] == t.c3_slot) & (pool != i3)]
        if len(pool) == 0:
            continue
        picks = rng.choice(pool, size=K, replace=len(pool) < K)
        long_ = t.side == "BUY"
        sd, td = t.risk / t.atr, t.reward / t.atr
        for rep, jc in enumerate(picks):
            i, j = int(ft["i_entry"].values[jc]), int(ft["j_end"].values[jc])
            a2 = atr_c1p[jc]
            fill = s5.ask[i] if long_ else s5.bid[i]
            sl = fill - sd * a2 if long_ else fill + sd * a2
            tp = fill + td * a2 if long_ else fill - td * a2
            r = walk_quote(s5, i, j, long_, sl, tp)
            if r is None:
                continue
            oc, px, _ = r
            raw = (px - fill) if long_ else (fill - px)
            row = {"trade_idx": t.Index, "rep": rep, "side": t.side,
                   "entry_time": t.entry_time, "ctl_time": s5.t[i], "outcome": oc, "raw": raw,
                   "risk": abs(fill - sl)}
            for cst in costs:
                row[f"pts_c{cst:.2f}"] = raw - cst
            out.append(row)
    return pd.DataFrame(out)


# ----------------------------------------------------------------------------- control (b)
def wick_control(tr: pd.DataFrame, s5: S5, m1: M1 | None = None) -> pd.DataFrame:
    """The vault-note metric and its honest re-scoring on the same events."""
    d = tr.copy()
    long_ = d.side.values == "BUY"
    rng_ = (d.c2_h - d.c2_l).values
    body_hi = np.maximum(d.c2_o, d.c2_c).values
    body_lo = np.minimum(d.c2_o, d.c2_c).values
    wick = np.where(long_, body_lo - d.c2_l.values, d.c2_h.values - body_hi)
    d["wick_frac"] = np.where(rng_ > 0, wick / np.where(rng_ > 0, rng_, 1), np.nan)
    # vault metric: entry = C2 close (mid), target = C2 open, delivered = C3 close beyond C2 open
    d["vault_delivered"] = np.where(long_, d.c3_c > d.c2_o, d.c3_c < d.c2_o)
    risk_c2 = np.where(long_, d.c2_c - d.sl, d.sl - d.c2_c)
    cushion = np.where(long_, d.c2_c - d.c2_o, d.c2_o - d.c2_c)
    d["cushion_R"] = cushion / risk_c2
    d["entry_beyond_target"] = cushion > 0
    d["dist_to_c1_target_R"] = d.reward / d.risk
    # entry moved to the C3-open quote, target still the C2 open, quote_s5 exits
    oc = []
    for r in d.itertuples(index=False):
        lg = r.side == "BUY"
        tp2 = r.c2_o
        fill = r.entry_px
        if (lg and fill >= tp2) or ((not lg) and fill <= tp2):
            oc.append("TP_at_entry"); continue
        w = walk_quote(s5, int(r.i_entry), int(r.j_end), lg, r.sl, tp2)
        oc.append(w[0] if w else None)
    d["moved_entry_c2open_outcome"] = oc
    # the vault entry itself: C2 close (mid) as the entry, target C2 open, stop as the rule,
    # resolved on C3's M1 mid bars (the bar-level view the vault work used)
    if m1 is not None:
        oc2 = []
        for r in d.itertuples(index=False):
            lg = r.side == "BUY"
            tp2, fill = r.c2_o, r.c2_c
            if (lg and fill >= tp2) or ((not lg) and fill <= tp2):
                oc2.append("TP_at_entry"); continue
            a = int(np.searchsorted(m1.t, np.datetime64(r.c3_start, "ns"), "left"))
            b = int(np.searchsorted(m1.t, np.datetime64(r.c3_end, "ns"), "left"))
            w = walk_mid(m1.h, m1.l, m1.c, a, b, lg, r.sl, tp2) if b > a else None
            oc2.append(w[0] if w else None)
        d["c2close_entry_c2open_outcome"] = oc2
    return d


# ----------------------------------------------------------------------------- stats
def pf(v: np.ndarray) -> float:
    v = np.asarray(v, float)
    gw, gl = v[v > 0].sum(), -v[v <= 0].sum()
    return float(gw / gl) if gl > 0 else float("inf")


def summ(df: pd.DataFrame, col: str) -> dict:
    v = df[col].dropna().values
    if len(v) == 0:
        return dict(n=0, wr=np.nan, pf=np.nan, pts=0.0, avg_pts=np.nan, avgR=np.nan)
    R = v / df.loc[df[col].notna(), "risk"].values
    return dict(n=int(len(v)), wr=round(100 * float((v > 0).mean()), 1), pf=round(pf(v), 3),
                pts=round(float(v.sum()), 1), avg_pts=round(float(v.mean()), 3), avgR=round(float(R.mean()), 3))
