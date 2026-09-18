"""sweeps_lib.py -- S5 liquidity-sweep anatomy: levels, events, quote-level resolver, controls.

Everything here follows PROTOCOL.md in this folder. Timestamps are OANDA bar OPEN times;
a bar's close is known at time + 5 s. All arrays are numpy over the full S5 cache.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
_STRAT = _HERE.parents[2]          # .../strategies
if str(_STRAT) not in sys.path:
    sys.path.insert(0, str(_STRAT))

from lab.tools.qa_s5_cache import load_s5   # noqa: E402  (read-only reuse)

CROSS_PTS = 0.10      # mid must exceed the level by this to count as a cross
REARM_PTS = 0.50      # close this far back inside re-arms the level
STOP_BUF = 0.20       # stop beyond the sweep extreme
HOLD_S = 4 * 3600     # max hold / label horizon
T_GRID = (30, 120, 300)
LEVEL_TYPES = ("PD", "ASIA", "H1", "LON")
WINDOW = (np.datetime64("2024-09-01T00:00:00"), np.datetime64("2026-09-18T00:00:00"))
SPLIT = np.datetime64("2025-12-01T00:00:00")
BAD_LO = np.datetime64("2025-12-25T23:00:00")
BAD_HI = np.datetime64("2025-12-25T23:15:00")


@dataclass
class S5:
    t: np.ndarray        # int64 epoch seconds
    dt: np.ndarray       # datetime64[s]
    o: np.ndarray; h: np.ndarray; l: np.ndarray; c: np.ndarray
    bid: np.ndarray; ask: np.ndarray

    @property
    def n(self) -> int:
        return len(self.t)


def load() -> S5:
    df = load_s5()
    dt = df["time"].dt.tz_convert(None).to_numpy("datetime64[s]")
    t = dt.astype("int64")
    return S5(t=t, dt=dt, o=df.o.to_numpy(float), h=df.h.to_numpy(float), l=df.l.to_numpy(float),
              c=df.c.to_numpy(float), bid=df.bid_c.to_numpy(float), ask=df.ask_c.to_numpy(float))


# ---------------------------------------------------------------- levels
def _prev_completed(key: np.ndarray, hi: np.ndarray, lo: np.ndarray, min_bars: int):
    """For each bar's group key (day or hour index), the high/low of the most recent
    COMPLETED group with >= min_bars bars that is strictly before this group."""
    g = pd.DataFrame({"k": key, "h": hi, "l": lo}).groupby("k").agg(h=("h", "max"), l=("l", "min"), n=("h", "size"))
    q = g[g.n >= min_bars]
    qk = q.index.to_numpy()
    pos = np.searchsorted(qk, key, side="left") - 1     # last qualifying key < this key
    ok = pos >= 0
    H = np.full(len(key), np.nan); L = np.full(len(key), np.nan)
    H[ok] = q.h.to_numpy()[pos[ok]]; L[ok] = q.l.to_numpy()[pos[ok]]
    return H, L


def _session_range(day: np.ndarray, hod: np.ndarray, hi, lo, h0: int, h1: int, v0: int, v1: int, min_bars: int):
    """High/low of hours [h0, h1) of the same UTC day, valid during hours [v0, v1)."""
    m = (hod >= h0) & (hod < h1)
    g = pd.DataFrame({"k": day[m], "h": hi[m], "l": lo[m]}).groupby("k").agg(h=("h", "max"), l=("l", "min"), n=("h", "size"))
    q = g[g.n >= min_bars]
    idx = q.index.to_numpy()
    pos = np.searchsorted(idx, day)
    have = (pos < len(idx))
    have[have] = idx[pos[have]] == day[have]
    valid = have & (hod >= v0) & (hod < v1)
    H = np.full(len(day), np.nan); L = np.full(len(day), np.nan)
    H[valid] = q.h.to_numpy()[pos[valid]]; L[valid] = q.l.to_numpy()[pos[valid]]
    return H, L


def build_levels(s: S5) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    day = s.t // 86400
    hour = s.t // 3600
    hod = (s.t % 86400) // 3600
    out = {}
    out["PD"] = _prev_completed(day, s.h, s.l, 5760)
    out["H1"] = _prev_completed(hour, s.h, s.l, 360)
    out["ASIA"] = _session_range(day, hod, s.h, s.l, 0, 7, 7, 24, 1000)
    out["LON"] = _session_range(day, hod, s.h, s.l, 7, 12, 12, 21, 1000)
    return out


# ---------------------------------------------------------------- events
def _first_true(mask: np.ndarray) -> int:
    """index of first True or -1"""
    if mask.size == 0:
        return -1
    k = int(mask.argmax())
    return k if mask[k] else -1


def find_events(s: S5, H: np.ndarray, L: np.ndarray, side: str, level_type: str) -> pd.DataFrame:
    """State machine for one level array. side='high' sweeps the high (SHORT trades);
    side='low' sweeps the low (LONG trades). Returns one row per accepted excursion,
    sweep or break, with time_beyond (s; NaN if no reclaim within HOLD_S)."""
    Lv = H if side == "high" else L
    Lopp = L if side == "high" else H
    n = s.n
    # candidate cross bars: level defined & unchanged from previous bar, prev close inside, this bar crosses
    same = np.zeros(n, bool)
    same[1:] = np.isfinite(Lv[1:]) & (Lv[1:] == Lv[:-1])
    if side == "high":
        cand = same.copy(); cand[1:] &= (s.c[:-1] < Lv[1:]) & (s.h[1:] >= Lv[1:] + CROSS_PTS)
    else:
        cand = same.copy(); cand[1:] &= (s.c[:-1] > Lv[1:]) & (s.l[1:] <= Lv[1:] - CROSS_PTS)
    cidx = np.flatnonzero(cand)
    # end of the constant-level run containing each bar (machine resets when the level changes)
    chg = np.flatnonzero(~same)                     # bars where the level value differs from the previous bar
    seg_end = np.searchsorted(chg, np.arange(n), side="right")
    seg_end = np.where(seg_end < len(chg), chg[np.minimum(seg_end, len(chg) - 1)], n)

    rows = []
    blocked_until = -1
    sgn = 1.0 if side == "high" else -1.0
    for i in cidx:
        if i < blocked_until:
            continue
        lv = Lv[i]
        k_end = int(np.searchsorted(s.t, s.t[i] + HOLD_S, side="right"))
        # reclaim: first close back inside, searched in growing chunks
        j = -1
        for span in (64, 512, k_end - i):
            seg = s.c[i:min(i + span, k_end)]
            m = (seg < lv) if side == "high" else (seg > lv)
            j = _first_true(m)
            if j >= 0:
                j += i
                break
        if j >= 0:
            time_beyond = float(s.t[j] - s.t[i])
            ext = s.h[i:j + 1].max() if side == "high" else s.l[i:j + 1].min()
            # re-arm: first close REARM_PTS back inside, within this level's run
            end_run = int(seg_end[i])
            seg = s.c[j:end_run]
            m = (seg <= lv - REARM_PTS) if side == "high" else (seg >= lv + REARM_PTS)
            r = _first_true(m)
            blocked_until = (j + r) if r >= 0 else end_run
        else:
            time_beyond = np.nan
            ext = s.h[i:k_end].max() if side == "high" else s.l[i:k_end].min()
            blocked_until = int(seg_end[i])       # no reclaim within 4 h: dead until the level changes
        depth = sgn * (ext - lv)
        rows.append((i, j, lv, Lopp[i], depth, ext, time_beyond, s.ask[i] - s.bid[i]))
    ev = pd.DataFrame(rows, columns=["i", "j", "level", "level_opp", "depth", "extreme", "time_beyond", "spread"])
    ev["level_type"] = level_type
    ev["side"] = side
    ev["dir"] = "SHORT" if side == "high" else "LONG"
    ev["t_cross"] = s.dt[ev.i.to_numpy()] if len(ev) else np.array([], "datetime64[s]")
    ev["depth_spr"] = ev.depth / ev.spread.clip(lower=0.01)
    ev["hod"] = (s.t[ev.i.to_numpy()] % 86400) // 3600 if len(ev) else []
    return ev


def apply_window(ev: pd.DataFrame) -> pd.DataFrame:
    tc = ev.t_cross.to_numpy("datetime64[s]")
    keep = (tc >= WINDOW[0]) & (tc < WINDOW[1]) & ~((tc >= BAD_LO - np.timedelta64(HOLD_S, "s")) & (tc < BAD_HI))
    ev = ev[keep].reset_index(drop=True)
    ev["split"] = np.where(ev.t_cross.to_numpy("datetime64[s]") < SPLIT, "TRAIN", "TEST")
    return ev


# ---------------------------------------------------------------- Question A labels
def label_reversal(s: S5, ev: pd.DataFrame) -> pd.DataFrame:
    """After the reclaim bar j (from j+1, mid prices, HOLD_S horizon): index of first
    revisit of the extreme (CONT) and of first touch of L-1D, L-2D, L-1.0 in the reclaim
    direction. Same-bar ties resolve as CONT."""
    out = {k: np.full(len(ev), -1, np.int64) for k in ("k_cont", "k_rev1", "k_rev2", "k_revf")}
    for r, e in enumerate(ev.itertuples()):
        j = int(e.j)
        if j < 0:
            continue
        a, b = j + 1, int(np.searchsorted(s.t, s.t[j] + HOLD_S, side="right"))
        if b <= a:
            continue
        if e.side == "high":
            hh, ll = s.h[a:b], s.l[a:b]
            kc = _first_true(hh >= e.extreme)
            k1 = _first_true(ll <= e.level - e.depth)
            k2 = _first_true(ll <= e.level - 2 * e.depth)
            kf = _first_true(ll <= e.level - 1.0)
        else:
            hh, ll = s.h[a:b], s.l[a:b]
            kc = _first_true(ll <= e.extreme)
            k1 = _first_true(hh >= e.level + e.depth)
            k2 = _first_true(hh >= e.level + 2 * e.depth)
            kf = _first_true(hh >= e.level + 1.0)
        out["k_cont"][r], out["k_rev1"][r], out["k_rev2"][r], out["k_revf"][r] = kc, k1, k2, kf
    ev = ev.copy()
    for k, v in out.items():
        ev[k] = v

    def lab(kr, kc):
        res = np.full(len(ev), "UNRESOLVED", object)
        has_r, has_c = kr >= 0, kc >= 0
        res[has_c & (~has_r | (kc <= kr))] = "CONT"          # tie -> CONT
        res[has_r & (~has_c | (kr < kc))] = "REV"
        return res
    ev["lab1"] = lab(ev.k_rev1.to_numpy(), ev.k_cont.to_numpy())
    ev["lab2"] = lab(ev.k_rev2.to_numpy(), ev.k_cont.to_numpy())
    ev["labf"] = lab(ev.k_revf.to_numpy(), ev.k_cont.to_numpy())
    return ev


# ---------------------------------------------------------------- resolver
def resolve_trade(s: S5, e_idx: int, long_: bool, entry: float, sl: float, tp: float):
    """Quote-level walk from bar e_idx+1 to the last bar <= t[e_idx] + HOLD_S.
    Stop before target within a bar. Returns (outcome, exit_px, exit_idx) or None."""
    a = e_idx + 1
    b = int(np.searchsorted(s.t, s.t[e_idx] + HOLD_S, side="right"))
    if b <= a:
        return None
    px = s.bid[a:b] if long_ else s.ask[a:b]
    if long_:
        ks = _first_true(px <= sl); kt = _first_true(px >= tp)
    else:
        ks = _first_true(px >= sl); kt = _first_true(px <= tp)
    if ks >= 0 and (kt < 0 or ks <= kt):
        return "SL", sl, a + ks
    if kt >= 0:
        return "TP", tp, a + kt
    return "TIME", float(px[-1]), b - 1


def crosses_bad_window(s: S5, e_idx: int, x_idx: int) -> bool:
    t0, t1 = s.dt[e_idx], s.dt[x_idx]
    return (t0 < BAD_HI) and (t1 >= BAD_LO)


def run_arm(s: S5, ev: pd.DataFrame, signal_col: str, stop_fn, target: str, concurrency: bool = True):
    """Generic arm runner. ev rows in time order. signal_col: 'j' (reclaim) or 'i' (touch).
    stop_fn(row) -> stop price. target: '2R' or 'opp'. Returns trades DataFrame + skip counts."""
    trades, skipped_open, bad_geom, bad_win, no_bars = [], 0, 0, 0, 0
    open_until = -1
    for e in ev.itertuples():
        sig = int(getattr(e, signal_col))
        e_idx = sig + 1
        if e_idx >= s.n:
            no_bars += 1; continue
        if concurrency and e_idx < open_until:
            skipped_open += 1; continue
        long_ = e.side == "low"
        entry = s.ask[e_idx] if long_ else s.bid[e_idx]
        sl = stop_fn(e)
        R = (entry - sl) if long_ else (sl - entry)
        if R <= 0:
            bad_geom += 1; continue
        if target == "2R":
            tp = entry + 2 * R if long_ else entry - 2 * R
        else:
            tp = float(e.level_opp)
            if (long_ and tp <= entry) or ((not long_) and tp >= entry):
                bad_geom += 1; continue
        res = resolve_trade(s, e_idx, long_, entry, sl, tp)
        if res is None:
            no_bars += 1; continue
        oc, xpx, x_idx = res
        if crosses_bad_window(s, e_idx, x_idx):
            bad_win += 1; continue
        raw = (xpx - entry) if long_ else (entry - xpx)
        open_until = x_idx + 1
        trades.append(dict(entry_time=s.dt[e_idx], side="BUY" if long_ else "SELL", entry_px=entry, sl=sl, tp=tp,
                           risk=R, outcome=oc, exit_px=xpx, exit_time=s.dt[x_idx], raw=raw,
                           event_i=int(e.i), time_beyond=e.time_beyond, depth=e.depth, depth_spr=e.depth_spr,
                           level_type=e.level_type, hod=int(e.hod)))
    tr = pd.DataFrame(trades)
    return tr, dict(skipped_open=skipped_open, bad_geom=bad_geom, bad_window=bad_win, no_bars=no_bars)


def _hour_pools(s: S5) -> dict[int, np.ndarray]:
    hod = (s.t % 86400) // 3600
    return {h: np.flatnonzero(hod == h) for h in range(24)}


def random_control(s: S5, trades: pd.DataFrame, pools: dict, seed: int) -> pd.DataFrame:
    """Matched random-time control: same UTC hour-of-day, +/-30 days, same side, same R,
    target 2R, entry at the next bar's quote, same walk."""
    rng = np.random.default_rng(seed)
    rows = []
    for tr in trades.itertuples():
        pool = pools[int(tr.hod)]
        t0 = int(np.datetime64(tr.entry_time, "s").astype("int64"))
        lo = np.searchsorted(s.t[pool], t0 - 30 * 86400); hi = np.searchsorted(s.t[pool], t0 + 30 * 86400)
        if hi <= lo:
            continue
        sig = int(pool[rng.integers(lo, hi)])
        e_idx = sig + 1
        if e_idx >= s.n:
            continue
        long_ = tr.side == "BUY"
        entry = s.ask[e_idx] if long_ else s.bid[e_idx]
        R = float(tr.risk)
        sl = entry - R if long_ else entry + R
        tp = entry + 2 * R if long_ else entry - 2 * R
        res = resolve_trade(s, e_idx, long_, entry, sl, tp)
        if res is None:
            continue
        oc, xpx, x_idx = res
        if crosses_bad_window(s, e_idx, x_idx):
            continue
        raw = (xpx - entry) if long_ else (entry - xpx)
        rows.append(dict(entry_time=s.dt[e_idx], side=tr.side, entry_px=entry, sl=sl, tp=tp, risk=R,
                         outcome=oc, exit_px=xpx, exit_time=s.dt[x_idx], raw=raw, matched_to=tr.entry_time,
                         level_type=tr.level_type, hod=int(tr.hod)))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- stats helpers
def pf(v: np.ndarray) -> float:
    gw = v[v > 0].sum(); gl = -v[v <= 0].sum()
    return float(gw / gl) if gl > 0 else float("inf")


def summarise(v: np.ndarray) -> dict:
    v = np.asarray(v, float)
    return dict(n=int(len(v)), pf=round(pf(v), 3) if len(v) else None, pts=round(float(v.sum()), 1),
                mean=round(float(v.mean()), 3) if len(v) else None, wr=round(100 * float((v > 0).mean()), 1) if len(v) else None)


def perm_test_median(x: np.ndarray, y: np.ndarray, n_perm: int = 10000, seed: int = 0) -> tuple[float, float]:
    """Two-sided permutation test on the difference of medians. Returns (diff, p)."""
    x = np.asarray(x, float); y = np.asarray(y, float)
    if len(x) < 5 or len(y) < 5:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    obs = np.median(x) - np.median(y)
    z = np.concatenate([x, y]); nx = len(x)
    cnt = 0
    for _ in range(n_perm):
        rng.shuffle(z)
        d = np.median(z[:nx]) - np.median(z[nx:])
        if abs(d) >= abs(obs) - 1e-12:
            cnt += 1
    return float(obs), (cnt + 1) / (n_perm + 1)


def boot_diff_ci(x: np.ndarray, y: np.ndarray, n_boot: int = 2000, seed: int = 0):
    rng = np.random.default_rng(seed)
    x = np.asarray(x, float); y = np.asarray(y, float)
    if not len(x) or not len(y):
        return float("nan"), float("nan"), float("nan")
    d = x.mean() - y.mean()
    bs = np.empty(n_boot)
    for k in range(n_boot):
        bs[k] = rng.choice(x, len(x)).mean() - rng.choice(y, len(y)).mean()
    return float(d), float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))
