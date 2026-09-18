"""common.py -- shared data layer for the killzones study.

Loads the S5 cache ONCE, places it on a global 5-second slot grid (missing slots forward-
filled with the previous close and flagged has_bar=False), converts every slot to the
America/New_York wall clock (DST resolved per timestamp by pandas, not by a constant),
and assigns each slot to a trading day 18:00 NY -> 17:00 NY.

Everything downstream loops over DAYS / WINDOWS / EVENTS, never over bars.
Run from KronosStrategies/strategies with ../.venv/bin/python.
"""
from __future__ import annotations

import os, sys, time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
RESULTS.mkdir(exist_ok=True)
STRAT = HERE.parent.parent.parent  # KronosStrategies/strategies
sys.path.insert(0, str(STRAT))
CACHE_2Y = STRAT / "backtest" / "results" / "bars_cache_2y"
SCRATCH = Path(os.environ.get("KZ_SCRATCH", "/private/tmp/claude-501/-Users-anil-Projects-Kronos/979109dc-9856-4311-91b0-14d2dda5b135/scratchpad"))
SCRATCH.mkdir(parents=True, exist_ok=True)

NY = "America/New_York"
DAY_MIN = 1380                      # 18:00 -> 17:00 NY = 23 h
SESSION_OPEN_MIN = 18 * 60          # NY wall-clock minute of the trading-day start
START = pd.Timestamp("2024-09-01", tz="UTC")
END = pd.Timestamp("2026-09-18", tz="UTC")     # exclusive; M5 cache ends 2026-09-17
SPLIT = pd.Timestamp("2025-12-01", tz="UTC")
BURST_PTS = 1.5
BURST_REL = 0.0004
SPIKE = (pd.Timestamp("2025-12-25 23:00", tz="UTC"), pd.Timestamp("2025-12-25 23:15", tz="UTC"))


def ny_tdm(start_hm: str, end_hm: str):
    """NY wall-clock 'HH:MM' -> trading-day minutes [a, b) (b may exceed a by wrap)."""
    def m(s):
        h, mi = s.split(":")
        return (int(h) * 60 + int(mi) - SESSION_OPEN_MIN) % 1440
    a, b = m(start_hm), m(end_hm)
    if b <= a:
        b += 1440
    return a, b


WINDOW_SETS = {
    "killzones": [("KZ_Asia", "20:00", "00:00"), ("KZ_London", "02:00", "05:00"),
                  ("KZ_NYAM", "07:00", "10:00"), ("KZ_LondonClose", "10:00", "12:00")],
    "macros": [("M_0950", "09:50", "10:10"), ("M_1050", "10:50", "11:10"),
               ("M_1310", "13:10", "13:40"), ("M_1515", "15:15", "15:45")],
    "silver_bullet": [("SB_03", "03:00", "04:00"), ("SB_10", "10:00", "11:00"), ("SB_14", "14:00", "15:00")],
    "calib_0800_1159": [("CAL_08_12", "08:00", "12:00")],
    "reopen_15": [("REOPEN15", "18:00", "18:15")],
    "reopen_30": [("REOPEN30", "18:00", "18:30")],
}


def windows_tdm(set_name):
    return [(n, *ny_tdm(a, b)) for n, a, b in WINDOW_SETS[set_name]]


def in_windows(tdm, wins):
    """tdm: array of trading-day minutes; wins: list of (name, a, b) with b possibly > 1380 (wrap)."""
    hit = np.zeros(len(tdm), bool)
    for _, a, b in wins:
        if b <= DAY_MIN:
            hit |= (tdm >= a) & (tdm < b)
        else:
            hit |= (tdm >= a) | (tdm < b - 1440)
    return hit


def union_cov(wins):
    cov = np.zeros(DAY_MIN, bool)
    for _, a, b in wins:
        ks = np.arange(a, b) % 1440
        ks = ks[ks < DAY_MIN]
        cov[ks] = True
    return cov


# ----------------------------------------------------------------------------- grid
def build_grid(force=False):
    """Return dict of grid arrays (cached as .npy in scratch)."""
    f = SCRATCH / "kz_grid.npz"
    if f.exists() and not force:
        z = np.load(f)
        g = {k: z[k] for k in z.files}
        print(f"[grid] loaded cache {f} ({len(g['c']):,} slots)")
        return g
    from lab.tools.qa_s5_cache import load_s5
    t0 = time.time()
    s5 = load_s5()
    print(f"[grid] S5 loaded {len(s5):,} bars in {time.time()-t0:.0f}s")
    sec = s5["time"].astype("int64").to_numpy() // 10**9
    slot = sec // 5
    s0 = int(slot[0]); n = int(slot[-1] - s0 + 1)
    idx = (slot - s0).astype(np.int64)
    has = np.zeros(n, bool); has[idx] = True
    g = {"slot0": np.int64(s0), "has_bar": has}
    # forward-fill: for missing slots use previous CLOSE for h/l/c/bid/ask
    fill_src = np.maximum.accumulate(np.where(has, np.arange(n), 0))   # index of last real slot
    c = np.full(n, np.nan); c[idx] = s5["c"].to_numpy(float)
    c = c[fill_src]
    for col in ("h", "l", "bid_c", "ask_c"):
        a = np.full(n, np.nan); a[idx] = s5[col].to_numpy(float)
        a = np.where(has, a, c)          # missing slot: value = ffilled close
        g[col] = a
    g["c"] = c
    g["vol"] = np.zeros(n); g["vol"][idx] = s5["volume"].to_numpy(float)
    del s5
    # NY wall clock per slot -> trading-day index and minute
    utc = pd.to_datetime((np.arange(n, dtype=np.int64) + s0) * 5, unit="s", utc=True)
    wall = utc.tz_convert(NY).tz_localize(None).asi8 // 10**9          # naive local seconds
    off = (wall - (np.arange(n, dtype=np.int64) + s0) * 5) // 3600       # UTC offset hours (-4/-5)
    shifted = wall - SESSION_OPEN_MIN * 60
    g["day_idx"] = (shifted // 86400).astype(np.int64)
    g["tdm"] = ((shifted % 86400) / 60.0).astype(np.float64)
    g["utc_off"] = off.astype(np.int8)
    # DST transitions check
    ch = np.flatnonzero(np.diff(off)) + 1
    print("[grid] UTC offset changes at:", [str(utc[i]) for i in ch])
    np.savez(f, **g)
    print(f"[grid] built {n:,} slots in {time.time()-t0:.0f}s -> {f}")
    return g


def day_table(g):
    """One row per trading day: slot range, validity, split."""
    d = g["day_idx"]; has = g["has_bar"]
    bounds = np.flatnonzero(np.diff(d)) + 1
    starts = np.r_[0, bounds]; ends = np.r_[bounds, len(d)]
    rows = []
    s0 = int(g["slot0"])
    for a, b in zip(starts, ends):
        hb = np.flatnonzero(has[a:b])
        if len(hb) == 0:
            continue
        first, last = a + hb[0], a + hb[-1]
        span_h = (last - first) * 5 / 3600
        maxgap_min = (np.diff(hb).max() * 5 / 60) if len(hb) > 1 else 0.0
        # day label = NY date of the 17:00 close = start date + 1 day
        close_date = (pd.Timestamp("1970-01-01") + pd.Timedelta(days=int(d[a]) + 1)).date()
        t_first = pd.Timestamp((first + s0) * 5, unit="s", tz="UTC")
        rows.append(dict(day=close_date, slot_a=a, slot_b=b, first=first, last=last, span_h=span_h,
                         maxgap_min=maxgap_min, n_bars=len(hb), t_first=t_first,
                         utc_off=int(g["utc_off"][first])))
    D = pd.DataFrame(rows)
    D["valid"] = (D.span_h >= 20) & (D.maxgap_min <= 60)
    D["in_window"] = (D.t_first >= START - pd.Timedelta(hours=6)) & (D.t_first < END)
    D["split"] = np.where(D.t_first < SPLIT - pd.Timedelta(hours=6), "TRAIN", "TEST")
    # the Christmas spike: mark the day invalid explicitly (it already fails span)
    D.loc[(D.day == pd.Timestamp("2025-12-26").date()), "valid"] = False
    return D


def rolling_range(g, w):
    h = pd.Series(g["h"]); l = pd.Series(g["l"])
    r = (h.rolling(w).max() - l.rolling(w).min()).to_numpy()
    return r


def episodes(mask):
    """maximal runs of True -> (start, end_inclusive) arrays."""
    m = np.r_[False, mask, False].astype(np.int8)
    d = np.diff(m)
    s = np.flatnonzero(d == 1); e = np.flatnonzero(d == -1) - 1
    return s, e


def build_events(g, D, force=False):
    f = RESULTS / "events.parquet"
    fd = RESULTS / "days.parquet"
    if f.exists() and fd.exists() and not force:
        return pd.read_parquet(f), pd.read_parquet(fd)
    t0 = time.time()
    r6 = rolling_range(g, 6); r12 = rolling_range(g, 12); r60 = rolling_range(g, 60)
    c = g["c"]; h = g["h"]; l = g["l"]; tdm = g["tdm"]; s0 = int(g["slot0"])
    ev = []
    dstats = []
    for r in D[D.valid & D.in_window].itertuples():
        a, b, first, last = r.slot_a, r.slot_b, r.first, r.last
        lo = first + 60                       # no window straddles the reopen (5 min)
        hi = last + 1
        def add(kind, slot, **kw):
            ev.append(dict(day=r.day, split=r.split, kind=kind, slot=int(slot), tdm=float(tdm[slot]), **kw))
        iH = first + int(np.argmax(h[first:hi])); iL = first + int(np.argmax(-l[first:hi]))
        add("H", iH, value=float(h[iH])); add("L", iL, value=float(l[iL]))
        e12 = lo + 11 + int(np.nanargmax(r12[lo + 11:hi])); add("X60", e12 - 11, value=float(r12[e12]))
        e60 = lo + 59 + int(np.nanargmax(r60[lo + 59:hi])); add("X300", e60 - 59, value=float(r60[e60]))
        dr = float(h[first:hi].max() - l[first:hi].min())
        for label, thr in (("BURST", BURST_PTS), ("BURSTREL", BURST_REL * c[lo:hi])):
            m = np.zeros(hi - lo, bool)
            seg = r6[lo + 5:hi]
            m[5:] = seg >= (thr if np.isscalar(thr) else thr[5:])
            s, e = episodes(m)
            s += lo; e += lo
            mv = c[e] - c[s - 6]
            for si, ei, mvi in zip(s, e, mv):
                add(label, si - 5, value=float(mvi), end_slot=int(ei), n_slots=int(ei - si + 1))
            if label == "BURST":
                dstats.append(dict(day=r.day, split=r.split, day_range=dr, n_bursts=len(s),
                                   burst_clock_share=float(m.sum() / (hi - first)),
                                   burst_abs_over_range=float(np.abs(mv).sum() / dr) if dr > 0 else np.nan,
                                   burst_net_over_range=float(np.abs(mv.sum()) / dr) if dr > 0 else np.nan))
    E = pd.DataFrame(ev)
    E["time_utc"] = pd.to_datetime((E.slot + s0) * 5, unit="s", utc=True)
    DS = D.merge(pd.DataFrame(dstats), on=["day", "split"], how="left")
    E.to_parquet(f, index=False); DS.to_parquet(fd, index=False)
    print(f"[events] {len(E):,} events over {E.day.nunique()} days in {time.time()-t0:.0f}s -> {f}")
    return E, DS


def pf(v):
    v = np.asarray(v, float)
    gw, gl = v[v > 0].sum(), -v[v <= 0].sum()
    return float(gw / gl) if gl > 0 else float("inf")


if __name__ == "__main__":
    g = build_grid()
    D = day_table(g)
    print(D[["valid", "in_window", "split"]].value_counts().sort_index())
    E, DS = build_events(g, D)
    print(E.kind.value_counts())
