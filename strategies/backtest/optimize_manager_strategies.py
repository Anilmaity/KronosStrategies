"""
optimize_manager_strategies.py
------------------------------
Fast offline parameter search for the four Strategy Manager slots, targeting
HIGH WIN RATE (70-80%) while staying profitable after taker costs.

Runs entirely from the local bars cache (results/bars_cache/is_XAU_USD_*.parquet,
2025-01 -> 2026-07); no network. Train/test discipline:

    TRAIN = 2025-01-01 .. 2025-12-31     (param selection)
    TEST  = 2026-01-01 .. 2026-07-02     (held-out confirmation only)

Cost model: flat COST_PTS round-trip per trade (research baseline stress
0.45pt), P&L in XAU points; USD at 0.02 lot = pts * 2.

Fill conventions (match the live engine post-2026-07-06 fix):
  ORB          : stop-entry AT the boundary on the touch bar; exits scanned
                 from the NEXT bar (spec 3.4 parity).
  EMA cross /
  Donchian /
  MR scalper   : market at the signal bar close; exits from the next bar.
  Same-bar SL+TP ambiguity resolves SL-FIRST (conservative, matches
  manager_sim step_position ordering).

Usage (from strategies/):
    python -m backtest.optimize_manager_strategies --strategy all
    python -m backtest.optimize_manager_strategies --strategy orb --top 15
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # strategies/

CACHE = Path(__file__).resolve().parent / "results" / "bars_cache"
COST_PTS = 0.45
USD_PER_PT = 2.0            # 0.02 lot
TRAIN_END = pd.Timestamp("2026-01-01", tz="UTC")


# ── data ──────────────────────────────────────────────────────────────────────

def load(tf: str) -> pd.DataFrame:
    df = pd.read_parquet(CACHE / f"is_XAU_USD_{tf}.parquet")
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.sort_values("time").reset_index(drop=True)


def ema_np(x: np.ndarray, n: int) -> np.ndarray:
    k = 2.0 / (n + 1)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = x[i] * k + out[i - 1] * (1 - k)
    return out


def atr_np(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int) -> np.ndarray:
    prev_c = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
    return pd.Series(tr).rolling(n).mean().to_numpy()


# ── generic exit walk ─────────────────────────────────────────────────────────

@dataclass
class Trade:
    t_entry: pd.Timestamp
    side: int              # +1 long / -1 short
    entry: float
    sl: float
    tp: float
    t_exit: pd.Timestamp
    exit: float
    outcome: str
    pnl: float             # pts, after COST_PTS


def walk_exit(h, l, c, dates, k0, side, entry, sl, tp,
              hold_bars, eod_flat, times) -> tuple[int, float, str]:
    """Scan from bar k0+1. SL checked before TP inside a bar (conservative).
    Returns (exit_idx, exit_px, outcome)."""
    n = len(c)
    last = min(n - 1, k0 + hold_bars) if hold_bars else n - 1
    d0 = dates[k0]
    for k in range(k0 + 1, n):
        if eod_flat and dates[k] != d0:
            return k - 1, c[k - 1], "EOD"
        if side > 0:
            if l[k] <= sl:
                return k, sl, "SL"
            if h[k] >= tp:
                return k, tp, "TP"
        else:
            if h[k] >= sl:
                return k, sl, "SL"
            if l[k] <= tp:
                return k, tp, "TP"
        if k >= last:
            return k, c[k], "TIME"
    return n - 1, c[n - 1], "OPEN"


def walk_trail(h, l, c, dates, k0, side, entry, trail_dist,
               hold_bars, times) -> tuple[int, float, str]:
    """Chandelier trail from bar k0+1: stop ratchets off the running HWM/LWM,
    pre-update stop checked first (mirrors manager_sim step_position)."""
    n = len(c)
    last = min(n - 1, k0 + hold_bars) if hold_bars else n - 1
    stop = entry - trail_dist if side > 0 else entry + trail_dist
    hwm = entry
    for k in range(k0 + 1, n):
        if side > 0:
            if l[k] <= stop:
                return k, stop, "TRAIL"
            hwm = max(hwm, h[k])
            stop = max(stop, hwm - trail_dist)
        else:
            if h[k] >= stop:
                return k, stop, "TRAIL"
            hwm = min(hwm, l[k])
            stop = min(stop, hwm + trail_dist)
        if k >= last:
            return k, c[k], "TIME"
    return n - 1, c[n - 1], "OPEN"


# ── metrics ───────────────────────────────────────────────────────────────────

def summarize(trades: list[Trade], label: str) -> dict:
    if not trades:
        return {"label": label, "n": 0}
    pnl = np.array([t.pnl for t in trades])
    wins = pnl > 0
    gp, gl = pnl[wins].sum(), -pnl[~wins].sum()
    days = max(1, len({t.t_entry.date() for t in trades}))
    return {
        "label": label,
        "n": len(trades),
        "wr": 100.0 * wins.mean(),
        "pf": gp / gl if gl > 0 else float("inf"),
        "net_pts": pnl.sum(),
        "net_usd": pnl.sum() * USD_PER_PT,
        "avg_pts": pnl.mean(),
        "tr_per_day": len(trades) / days,
    }


def split_summary(trades: list[Trade], label: str) -> tuple[dict, dict]:
    tr = [t for t in trades if t.t_entry < TRAIN_END]
    te = [t for t in trades if t.t_entry >= TRAIN_END]
    return summarize(tr, label + "|train"), summarize(te, label + "|test")


def fmt(s: dict) -> str:
    if s.get("n", 0) == 0:
        return f"{s['label']:<46s} n=0"
    return (f"{s['label']:<46s} n={s['n']:<4d} WR={s['wr']:5.1f}% PF={s['pf']:5.2f} "
            f"net={s['net_pts']:+8.1f}pts (${s['net_usd']:+8.2f}) "
            f"avg={s['avg_pts']:+.2f} {s['tr_per_day']:.2f}/day")


# ── 1. ORB session breakout ───────────────────────────────────────────────────

def run_orb(m5: pd.DataFrame, *, sessions=(1, 7, 12, 13, 14), or_min=30,
            tp_frac=1.5, hold_bars=36, n_long=240, slope_lk=48,
            max_risk=None, sl_frac=1.0) -> list[Trade]:
    """Bias-filtered opening-range breakout, touch fill at the boundary.
    tp_frac : TP distance as fraction of OR width.
    sl_frac : SL distance as fraction of OR width (1.0 = opposite extreme).
    max_risk: skip the trade when SL distance (pts) exceeds this."""
    t = m5["time"].to_numpy()
    times = m5["time"]
    h = m5["high"].to_numpy(float); l = m5["low"].to_numpy(float)
    c = m5["close"].to_numpy(float)
    hours = times.dt.hour.to_numpy(); mins = times.dt.minute.to_numpy()
    dates = times.dt.date.to_numpy()

    e = ema_np(c, n_long)
    bias = np.zeros(len(c), dtype=int)
    idx0 = n_long + slope_lk
    up = (c[idx0:] > e[idx0:]) & (e[idx0:] > e[idx0 - slope_lk:-slope_lk])
    dn = (c[idx0:] < e[idx0:]) & (e[idx0:] < e[idx0 - slope_lk:-slope_lk])
    bias[idx0:] = np.where(up, 1, np.where(dn, -1, 0))

    trades: list[Trade] = []
    # group bar indices by (date, hour) once
    df_idx = pd.DataFrame({"d": dates, "hr": hours, "mn": mins})
    for (d, sh), grp in df_idx.groupby(["d", "hr"], sort=True):
        if sh not in sessions:
            continue
        or_ix = grp.index[(grp["mn"] < or_min)].to_numpy()
        if len(or_ix) < 2:
            continue
        rng_hi = h[or_ix].max(); rng_lo = l[or_ix].min()
        rng = rng_hi - rng_lo
        if rng <= 0:
            continue
        scan_ix = grp.index[(grp["mn"] >= or_min)].to_numpy()
        for k in scan_ix:
            b = bias[k]
            side = 0
            if h[k] >= rng_hi and b == 1:
                side, entry = 1, rng_hi
                sl = rng_hi - sl_frac * rng
                tp = rng_hi + tp_frac * rng
            elif l[k] <= rng_lo and b == -1:
                side, entry = -1, rng_lo
                sl = rng_lo + sl_frac * rng
                tp = rng_lo - tp_frac * rng
            if side == 0:
                continue
            risk = abs(entry - sl)
            if max_risk is not None and risk > max_risk:
                break                       # one attempt per session, spec parity
            ke, px, out = walk_exit(h, l, c, dates, k, side, entry, sl, tp,
                                    hold_bars, True, times)
            pnl = side * (px - entry) - COST_PTS
            trades.append(Trade(times.iloc[k], side, entry, sl, tp,
                                times.iloc[ke], px, out, pnl))
            break                            # one entry per session window
    return trades


# ── 2. s96 EMA cross momentum ─────────────────────────────────────────────────

def h4_bias_on_m5(m5: pd.DataFrame, h4: pd.DataFrame,
                  ema_fast=20, ema_slow=50) -> np.ndarray:
    """+1/-1/0 H4 EMA-trend bias mapped onto each M5 bar (uses only H4 bars
    CLOSED before the M5 bar's open — no look-ahead). Mimics the manager's
    h4_bias gate for ungated replays."""
    hc = h4["close"].to_numpy(float)
    ef = ema_np(hc, ema_fast); es = ema_np(hc, ema_slow)
    b4 = np.where(ef > es, 1, -1)
    b4[:ema_slow] = 0
    h4_close_ts = (h4["time"] + pd.Timedelta(hours=4)).to_numpy()
    pos = np.searchsorted(h4_close_ts, m5["time"].to_numpy(), side="right") - 1
    out = np.zeros(len(m5), dtype=int)
    ok = pos >= 0
    out[ok] = b4[pos[ok]]
    return out


def run_ema_cross(m5: pd.DataFrame, *, fast=9, slow=21, atr_n=14, k_atr=1.5,
                  tp_r=None, hold_bars=96, hours=None,
                  bias_m5: np.ndarray | None = None,
                  min_er: float | None = None, er_n: int = 48) -> list[Trade]:
    """EMA fast/slow cross events. tp_r=None -> chandelier trail (current live);
    tp_r=x -> static TP at x * (k_atr*ATR) with static SL at k_atr*ATR.
    bias_m5: optional +1/-1 H4 bias — only take aligned crosses.
    min_er : optional Kaufman efficiency-ratio floor (trend-strength gate,
             the offline stand-in for the manager's TRENDING regime)."""
    times = m5["time"]
    h = m5["high"].to_numpy(float); l = m5["low"].to_numpy(float)
    c = m5["close"].to_numpy(float)
    dates = times.dt.date.to_numpy()
    hr = times.dt.hour.to_numpy()

    ef = ema_np(c, fast); es = ema_np(c, slow)
    a = atr_np(h, l, c, atr_n)
    if min_er is not None:
        s = pd.Series(c)
        change = (s - s.shift(er_n)).abs()
        vol = s.diff().abs().rolling(er_n).sum()
        er = (change / vol).to_numpy()
    cross_up = (ef[:-1] <= es[:-1]) & (ef[1:] > es[1:])
    cross_dn = (ef[:-1] >= es[:-1]) & (ef[1:] < es[1:])

    trades: list[Trade] = []
    busy_until = -1
    for k in np.nonzero(cross_up | cross_dn)[0] + 1:
        if k <= busy_until or k < 120 or not (a[k] > 0):
            continue
        if hours is not None and hr[k] not in hours:
            continue
        side = 1 if cross_up[k - 1] else -1
        if bias_m5 is not None and bias_m5[k] != side:
            continue
        if min_er is not None and not (np.isfinite(er[k]) and er[k] >= min_er):
            continue
        entry = c[k]
        risk = k_atr * a[k]
        if tp_r is None:
            ke, px, out = walk_trail(h, l, c, dates, k, side, entry, risk,
                                     hold_bars, times)
        else:
            sl = entry - side * risk
            tp = entry + side * tp_r * risk
            ke, px, out = walk_exit(h, l, c, dates, k, side, entry, sl, tp,
                                    hold_bars, False, times)
        pnl = side * (px - entry) - COST_PTS
        trades.append(Trade(times.iloc[k], side, entry,
                            entry - side * risk, entry + side * (tp_r or 30) * risk,
                            times.iloc[ke], px, out, pnl))
        busy_until = ke
    return trades


# ── 3. H4 Donchian trend-follow ───────────────────────────────────────────────

def run_donchian_h4(h4: pd.DataFrame, *, n=20, ema_fast=20, ema_slow=50,
                    atr_n=14, k_atr=3.0, tp_r=None, hold_bars=0) -> list[Trade]:
    times = h4["time"]
    h = h4["high"].to_numpy(float); l = h4["low"].to_numpy(float)
    c = h4["close"].to_numpy(float)
    dates = times.dt.date.to_numpy()
    ef = ema_np(c, ema_fast); es = ema_np(c, ema_slow)
    a = atr_np(h, l, c, atr_n)

    trades: list[Trade] = []
    busy_until = -1
    for k in range(max(ema_slow + n + 2, atr_n + 1), len(c)):
        if k <= busy_until or not (a[k] > 0):
            continue
        donch_hi = h[k - n:k].max(); donch_lo = l[k - n:k].min()
        up = ef[k] > es[k]
        side = 0
        if c[k] > donch_hi and up:
            side = 1
        elif c[k] < donch_lo and not up:
            side = -1
        if side == 0:
            continue
        entry = c[k]
        risk = k_atr * a[k]
        if tp_r is None:
            ke, px, out = walk_trail(h, l, c, dates, k, side, entry, risk,
                                     hold_bars, times)
        else:
            sl = entry - side * risk
            tp = entry + side * tp_r * risk
            ke, px, out = walk_exit(h, l, c, dates, k, side, entry, sl, tp,
                                    hold_bars, False, times)
        pnl = side * (px - entry) - COST_PTS
        trades.append(Trade(times.iloc[k], side, entry,
                            entry - side * risk, entry + side * (tp_r or 50) * risk,
                            times.iloc[ke], px, out, pnl))
        busy_until = ke
    return trades


# ── 4. MR scalper (z-score fade, quiet hours) ────────────────────────────────

def run_zscore_mr(m5: pd.DataFrame, *, n=48, zt=2.5, tp_atr=0.5, sl_atr=1.5,
                  atr_n=14, hold_bars=24, hours=tuple(range(3, 9)),
                  bias_m5: np.ndarray | None = None) -> list[Trade]:
    times = m5["time"]
    h = m5["high"].to_numpy(float); l = m5["low"].to_numpy(float)
    c = m5["close"].to_numpy(float)
    dates = times.dt.date.to_numpy()
    hr = times.dt.hour.to_numpy()

    s = pd.Series(c)
    mu = s.rolling(n).mean().to_numpy()
    sd = s.rolling(n).std(ddof=0).to_numpy()
    a = atr_np(h, l, c, atr_n)
    with np.errstate(invalid="ignore", divide="ignore"):
        z = (c - mu) / sd

    trades: list[Trade] = []
    busy_until = -1
    for k in range(n + 1, len(c)):
        if k <= busy_until or hr[k] not in hours:
            continue
        if not (a[k] > 0) or not np.isfinite(z[k]):
            continue
        side = 0
        if z[k] <= -zt:
            side = 1              # fade the stretch down
        elif z[k] >= zt:
            side = -1             # fade the stretch up
        if side == 0:
            continue
        if bias_m5 is not None and bias_m5[k] != side:
            continue              # only fade counter-moves WITHIN the H4 trend
        entry = c[k]
        sl = entry - side * sl_atr * a[k]
        tp = entry + side * tp_atr * a[k]
        ke, px, out = walk_exit(h, l, c, dates, k, side, entry, sl, tp,
                                hold_bars, False, times)
        pnl = side * (px - entry) - COST_PTS
        trades.append(Trade(times.iloc[k], side, entry, sl, tp,
                            times.iloc[ke], px, out, pnl))
        busy_until = ke
    return trades


# ── sweeps ────────────────────────────────────────────────────────────────────

def sweep_orb(m5, top):
    print("\n===== ORB session breakout (SESSION_BREAKOUT / s95 family) =====")
    base_tr, base_te = split_summary(
        run_orb(m5, tp_frac=1.5), "BASELINE tp=1.5xOR sl=1.0xOR")
    print(fmt(base_tr)); print(fmt(base_te))
    rows = []
    for or_min in (15, 30):
        for tp_frac in (0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 1.25, 1.5):
            for sl_frac in (1.0, 1.25, 1.5):
                for max_risk in (None, 8.0, 12.0):
                    label = (f"or={or_min} tp={tp_frac}xOR sl={sl_frac}xOR "
                             f"cap={max_risk}")
                    tr, te = split_summary(
                        run_orb(m5, or_min=or_min, tp_frac=tp_frac,
                                sl_frac=sl_frac, max_risk=max_risk), label)
                    rows.append((tr, te))
    _report(rows, top)


def sweep_s96(m5, top, h4=None):
    print("\n===== s96 EMA9/21 momentum =====")
    base_tr, base_te = split_summary(
        run_ema_cross(m5, tp_r=None), "BASELINE trail 1.5ATR")
    print(fmt(base_tr)); print(fmt(base_te))
    rows = []
    london_ny = tuple(range(7, 16))
    bias = h4_bias_on_m5(m5, h4) if h4 is not None else None
    for k_atr in (1.0, 1.5, 2.0, 2.5):
        for tp_r in (0.3, 0.4, 0.5, 0.6, 0.8, 1.0):
            for hours in (None, london_ny):
                tag = "LDN-NY" if hours else "24h"
                for b, btag in ((None, ""), (bias, "+H4bias")):
                    for mer, etag in ((None, ""), (0.30, "+ER.30"), (0.40, "+ER.40")):
                        label = f"sl={k_atr}ATR tp={tp_r}R {tag}{btag}{etag}"
                        tr, te = split_summary(
                            run_ema_cross(m5, k_atr=k_atr, tp_r=tp_r,
                                          hours=hours, bias_m5=b,
                                          min_er=mer), label)
                        rows.append((tr, te))
    _report(rows, top)


def sweep_momo_h1(h1, top):
    """Momentum slot alternative: H1 Donchian continuation (the pre-rewrite
    s96 family, which validated at test PF 1.445) with high-WR exits."""
    print("\n===== momentum alt: H1 Donchian(24) continuation =====")
    base_tr, base_te = split_summary(
        run_donchian_h4(h1, n=24, k_atr=3.0, tp_r=None, hold_bars=0),
        "BASELINE H1 Donch24 trail 3ATR")
    print(fmt(base_tr)); print(fmt(base_te))
    rows = []
    for n in (20, 24, 48):
        for k_atr in (1.5, 2.0, 3.0, 4.0):
            for tp_r in (0.3, 0.4, 0.5, 0.75, 1.0):
                label = f"H1 don{n} sl={k_atr}ATR tp={tp_r}R"
                tr, te = split_summary(
                    run_donchian_h4(h1, n=n, k_atr=k_atr, tp_r=tp_r,
                                    hold_bars=0), label)
                rows.append((tr, te))
    _report(rows, top)


def sweep_trend(h4, top):
    print("\n===== CHALLENGE_XAU H4 Donchian trend =====")
    base_tr, base_te = split_summary(
        run_donchian_h4(h4, tp_r=None), "BASELINE trail 3ATR")
    print(fmt(base_tr)); print(fmt(base_te))
    rows = []
    for k_atr in (2.0, 3.0, 4.0):
        for tp_r in (0.3, 0.4, 0.5, 0.75, 1.0):
            label = f"sl={k_atr}ATR tp={tp_r}R"
            tr, te = split_summary(run_donchian_h4(h4, k_atr=k_atr, tp_r=tp_r),
                                   label)
            rows.append((tr, te))
    _report(rows, top)


def sweep_scalper(m5, top, h4=None):
    print("\n===== MR scalper (z-score fade) =====")
    rows = []
    bias = h4_bias_on_m5(m5, h4) if h4 is not None else None
    for n in (24, 48, 96):
        for zt in (2.0, 2.5, 3.0):
            for tp_atr in (0.3, 0.5, 0.8):
                for sl_atr in (1.0, 1.5, 2.0):
                    for hours in (tuple(range(3, 9)), tuple(range(0, 9)),
                                  tuple(range(7, 16))):
                        for b, btag in ((None, ""), (bias, "+H4bias")):
                            label = (f"n={n} z={zt} tp={tp_atr}A sl={sl_atr}A "
                                     f"h={hours[0]}-{hours[-1]}{btag}")
                            tr, te = split_summary(
                                run_zscore_mr(m5, n=n, zt=zt, tp_atr=tp_atr,
                                              sl_atr=sl_atr, hours=hours,
                                              bias_m5=b), label)
                            rows.append((tr, te))
    _report(rows, top)


def _report(rows, top):
    """Rank by train (WR>=68 & profitable first, then net); show test beside."""
    def key(pair):
        tr = pair[0]
        if tr.get("n", 0) < 30:
            return (-2, 0)
        ok = tr["wr"] >= 68.0 and tr["net_pts"] > 0
        return (1 if ok else 0, tr["net_pts"])
    rows.sort(key=key, reverse=True)
    print(f"-- top {top} by train (WR>=68% & net>0 first, min 30 trades); "
          f"test shown for honesty --")
    for tr, te in rows[:top]:
        print(fmt(tr))
        print(fmt(te))
        print()


def verify_winners(m5, h4, h1):
    """The three configs chosen 2026-07-06 (train-selected, test-confirmed),
    printed at baseline and stressed cost. Run before any deploy."""
    global COST_PTS
    for cost in (0.45, 0.80):
        COST_PTS = cost
        print(f"\n===== chosen configs @ cost={cost}pt =====")
        for tr, te in (
            split_summary(run_orb(m5, or_min=30, tp_frac=0.6, sl_frac=1.5),
                          "SESSION or30 tp0.6xOR sl1.5xOR"),
            split_summary(run_donchian_h4(h1, n=24, k_atr=3.0, tp_r=0.4,
                                          hold_bars=0),
                          "MOMENTUM H1 don24 sl3ATR tp0.4R"),
            split_summary(run_donchian_h4(h4, n=20, k_atr=4.0, tp_r=0.4,
                                          hold_bars=0),
                          "TREND H4 don20 sl4ATR tp0.4R"),
        ):
            print(fmt(tr)); print(fmt(te))
    COST_PTS = 0.45


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="all",
                    choices=["all", "orb", "s96", "trend", "scalper", "momo",
                             "verify"])
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()

    m5 = load("5m")
    h4 = load("4h")
    print(f"M5 bars: {len(m5):,}  {m5['time'].iloc[0]} -> {m5['time'].iloc[-1]}")
    print(f"cost={COST_PTS}pt/trade, $ at 0.02 lot, train<{TRAIN_END.date()}<=test")

    if args.strategy in ("all", "orb"):
        sweep_orb(m5, args.top)
    if args.strategy in ("all", "s96"):
        sweep_s96(m5, args.top, h4=h4)
    if args.strategy in ("all", "trend"):
        sweep_trend(h4, args.top)
    if args.strategy in ("all", "momo"):
        sweep_momo_h1(load("1h"), args.top)
    if args.strategy == "verify":
        verify_winners(m5, h4, load("1h"))
    if args.strategy in ("all", "scalper"):
        sweep_scalper(m5, args.top, h4=h4)


if __name__ == "__main__":
    main()
