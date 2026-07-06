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


# ── 5. M1 liquidity-sweep reversal (killzones) ───────────────────────────────

def run_liq_sweep_m1(m1: pd.DataFrame, *, n=60, poke_atr=0.2, buf_atr=0.3,
                     tp_r=0.8, atr_n=14, hold_bars=240,
                     hours=(7, 8, 9, 12, 13, 14),
                     bias_m5: np.ndarray | None = None) -> list[Trade]:
    """ICT-style stop-hunt fade on M1: a poke beyond the prior n-bar extreme
    (by >= poke_atr x ATR) that CLOSES back inside = liquidity sweep +
    rejection -> fade it. SL beyond the sweep wick + buf_atr x ATR,
    TP = tp_r x risk. bias_m5: fade only sweeps against the H4 trend
    (sell swept highs in a downtrend, buy swept lows in an uptrend)."""
    times = m1["time"]
    h = m1["high"].to_numpy(float); l = m1["low"].to_numpy(float)
    c = m1["close"].to_numpy(float)
    dates = times.dt.date.to_numpy()
    hr = times.dt.hour.to_numpy()

    a = atr_np(h, l, c, atr_n)
    prev_hi = pd.Series(h).rolling(n).max().shift(1).to_numpy()
    prev_lo = pd.Series(l).rolling(n).min().shift(1).to_numpy()

    trades: list[Trade] = []
    busy_until = -1
    for k in range(n + 1, len(c)):
        if k <= busy_until or hr[k] not in hours or not (a[k] > 0):
            continue
        side = 0
        if (h[k] > prev_hi[k] + poke_atr * a[k]) and c[k] < prev_hi[k]:
            side = -1                       # swept the highs, rejected -> SELL
            sl = h[k] + buf_atr * a[k]
        elif (l[k] < prev_lo[k] - poke_atr * a[k]) and c[k] > prev_lo[k]:
            side = 1                        # swept the lows, rejected -> BUY
            sl = l[k] - buf_atr * a[k]
        if side == 0:
            continue
        if bias_m5 is not None and bias_m5[k] != side:
            continue
        entry = c[k]
        risk = abs(entry - sl)
        if risk <= 0:
            continue
        tp = entry + side * tp_r * risk
        ke, px, out = walk_exit(h, l, c, dates, k, side, entry, sl, tp,
                                hold_bars, False, times)
        pnl = side * (px - entry) - COST_PTS
        trades.append(Trade(times.iloc[k], side, entry, sl, tp,
                            times.iloc[ke], px, out, pnl))
        busy_until = ke
    return trades


def sweep_lowtf(m1, top, h4=None):
    print("\n===== M1 liquidity-sweep reversal (killzones) =====")
    rows = []
    bias = h4_bias_on_m5(m1, h4) if h4 is not None else None
    kz_ldn_ny = (7, 8, 9, 12, 13, 14)
    kz_all = tuple(range(1, 16))
    for n in (30, 60, 120):
        for poke in (0.0, 0.2, 0.5):
            for tp_r in (0.5, 0.8, 1.2):
                for buf in (0.2, 0.5):
                    for hours, htag in ((kz_ldn_ny, "KZ"), (kz_all, "1-15")):
                        for b, btag in ((None, ""), (bias, "+H4bias")):
                            label = (f"n={n} poke={poke}A tp={tp_r}R "
                                     f"buf={buf}A {htag}{btag}")
                            tr, te = split_summary(
                                run_liq_sweep_m1(m1, n=n, poke_atr=poke,
                                                 tp_r=tp_r, buf_atr=buf,
                                                 hours=hours, bias_m5=b),
                                label)
                            rows.append((tr, te))
    _report(rows, top)


# ── 6. ICT MSS + FVG reversal (M5) ───────────────────────────────────────────

def _fractal_swings(h: np.ndarray, l: np.ndarray, w: int = 2):
    """Last CONFIRMED fractal swing high/low value as of each bar (a swing at
    i confirms at i+w, so no look-ahead). Returns (swing_hi, swing_lo) arrays
    (nan until the first confirmation)."""
    n = len(h)
    swing_hi = np.full(n, np.nan)
    swing_lo = np.full(n, np.nan)
    last_hi = last_lo = np.nan
    for i in range(w, n - w):
        if h[i] == h[i - w:i + w + 1].max() and (h[i] > h[i - w:i]).all():
            last_hi_new = h[i]
        else:
            last_hi_new = None
        if l[i] == l[i - w:i + w + 1].min() and (l[i] < l[i - w:i]).all():
            last_lo_new = l[i]
        else:
            last_lo_new = None
        k = i + w                     # confirmation bar
        if last_hi_new is not None:
            last_hi = last_hi_new
        if last_lo_new is not None:
            last_lo = last_lo_new
        swing_hi[k] = last_hi
        swing_lo[k] = last_lo
    # forward-fill so every bar sees the latest confirmed swing
    for arr in (swing_hi, swing_lo):
        v = np.nan
        for i in range(n):
            if not np.isnan(arr[i]):
                v = arr[i]
            arr[i] = v
    return swing_hi, swing_lo


def run_mss_fvg(m5: pd.DataFrame, *, sweep_n=48, retrace_w=24, tp_r=1.5,
                buf_atr=0.2, atr_n=14, hold_bars=96,
                hours=(7, 8, 9, 12, 13, 14),
                bias_m5: np.ndarray | None = None) -> list[Trade]:
    """ICT Market-Structure-Shift + FVG reversal on M5 (zero discretion):

    bearish setup at bar k:
      sweep : within the prior sweep_n bars price traded ABOVE the last
              confirmed swing high (buy-side liquidity taken)
      MSS   : bar k CLOSES below the last confirmed swing low (displacement)
      FVG   : the displacement leaves a bearish FVG (high[k] < low[k-2])
      entry : first retrace into the FVG within retrace_w bars, filled at the
              proximal edge (high[k]); SL above the distal edge (low[k-2])
              + buf_atr x ATR; TP = tp_r x risk. Mirror for bullish.
    bias_m5: optional H4 alignment (reversal trades WITH the HTF trend)."""
    times = m5["time"]
    h = m5["high"].to_numpy(float); l = m5["low"].to_numpy(float)
    c = m5["close"].to_numpy(float)
    dates = times.dt.date.to_numpy()
    hr = times.dt.hour.to_numpy()
    a = atr_np(h, l, c, atr_n)
    swing_hi, swing_lo = _fractal_swings(h, l)
    roll_hi = pd.Series(h).rolling(sweep_n).max().to_numpy()
    roll_lo = pd.Series(l).rolling(sweep_n).min().to_numpy()

    trades: list[Trade] = []
    busy_until = -1
    for k in range(sweep_n + 2, len(c)):
        if k <= busy_until or hr[k] not in hours or not (a[k] > 0):
            continue
        if np.isnan(swing_hi[k]) or np.isnan(swing_lo[k]):
            continue
        side = 0
        # bearish MSS: highs swept recently, close breaks the swing low,
        # displacement leaves a bearish FVG
        if (roll_hi[k] > swing_hi[k] and c[k] < swing_lo[k]
                and h[k] < l[k - 2]):
            side = -1
            fvg_prox, fvg_dist = h[k], l[k - 2]
        elif (roll_lo[k] < swing_lo[k] and c[k] > swing_hi[k]
                and l[k] > h[k - 2]):
            side = 1
            fvg_prox, fvg_dist = l[k], h[k - 2]
        if side == 0:
            continue
        if bias_m5 is not None and bias_m5[k] != side:
            continue
        # wait for the retrace into the FVG (limit-style fill at the edge)
        entry_j = None
        for j in range(k + 1, min(k + 1 + retrace_w, len(c))):
            if side < 0 and h[j] >= fvg_prox:
                entry_j = j
                break
            if side > 0 and l[j] <= fvg_prox:
                entry_j = j
                break
        if entry_j is None:
            continue
        entry = fvg_prox
        sl = fvg_dist + (buf_atr * a[k] * (1 if side < 0 else -1))
        risk = abs(sl - entry)
        if risk <= 0:
            continue
        tp = entry + side * tp_r * risk
        # phantom guard: the retrace bar itself may already be through SL
        if (side < 0 and h[entry_j] >= sl) or (side > 0 and l[entry_j] <= sl):
            continue
        ke, px, out = walk_exit(h, l, c, dates, entry_j, side, entry, sl, tp,
                                hold_bars, False, times)
        pnl = side * (px - entry) - COST_PTS
        trades.append(Trade(times.iloc[entry_j], side, entry, sl, tp,
                            times.iloc[ke], px, out, pnl))
        busy_until = ke
    return trades


def sweep_mss(m5, top, h4=None):
    print("\n===== ICT MSS+FVG reversal (M5) =====")
    rows = []
    bias = h4_bias_on_m5(m5, h4) if h4 is not None else None
    kz = (7, 8, 9, 12, 13, 14)
    wide = tuple(range(1, 16))
    for sweep_n in (24, 48, 96):
        for tp_r in (1.0, 1.5, 2.0):
            for retrace_w in (12, 24):
                for hours, htag in ((kz, "KZ"), (wide, "1-15")):
                    for b, btag in ((None, ""), (bias, "+H4bias")):
                        label = (f"sw={sweep_n} tp={tp_r}R w={retrace_w} "
                                 f"{htag}{btag}")
                        tr, te = split_summary(
                            run_mss_fvg(m5, sweep_n=sweep_n, tp_r=tp_r,
                                        retrace_w=retrace_w, hours=hours,
                                        bias_m5=b), label)
                        rows.append((tr, te))
    _report(rows, top)


# ── 7. Scalping candidates (fifth campaign, 2026-07-06) ─────────────────────

def run_burst_scalp(m5: pd.DataFrame, *, range_atr=2.0, close_q=0.25,
                    tp_atr=0.8, sl_atr=1.0, atr_n=14, hold_bars=12,
                    hours=(7, 8, 9, 12, 13, 14), vol_x=None,
                    bias_m5: np.ndarray | None = None) -> list[Trade]:
    """Displacement-burst CONTINUATION scalp: an abnormal-range M5 bar
    (range >= range_atr x ATR) closing within close_q of its extreme ignites
    a short burst — follow it. Optional vol_x: bar volume must also exceed
    vol_x times its 48-bar average (tick-volume ignition filter)."""
    times = m5["time"]
    h = m5["high"].to_numpy(float); l = m5["low"].to_numpy(float)
    c = m5["close"].to_numpy(float)
    v = m5["volume"].to_numpy(float)
    dates = times.dt.date.to_numpy()
    hr = times.dt.hour.to_numpy()
    a = atr_np(h, l, c, atr_n)
    vavg = pd.Series(v).rolling(48).mean().to_numpy()

    trades: list[Trade] = []
    busy_until = -1
    for k in range(50, len(c)):
        if k <= busy_until or hr[k] not in hours or not (a[k] > 0):
            continue
        rng = h[k] - l[k]
        if rng < range_atr * a[k]:
            continue
        if vol_x is not None and not (v[k] > vol_x * vavg[k]):
            continue
        side = 0
        if (h[k] - c[k]) <= close_q * rng:
            side = 1                        # closed near the high -> follow up
        elif (c[k] - l[k]) <= close_q * rng:
            side = -1                       # closed near the low -> follow down
        if side == 0:
            continue
        if bias_m5 is not None and bias_m5[k] != side:
            continue
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


def run_squeeze_scalp(m5: pd.DataFrame, *, sq_n=6, sq_pct=0.6, tp_atr=0.8,
                      sl_atr=1.0, atr_n=14, hold_bars=12,
                      hours=(7, 8, 9, 12, 13, 14),
                      bias_m5: np.ndarray | None = None) -> list[Trade]:
    """Volatility-contraction squeeze break: the last sq_n bars' combined
    range compresses below sq_pct x (sq_n x ATR); the first close beyond the
    squeeze box breaks it — follow with a tight TP/SL."""
    times = m5["time"]
    h = m5["high"].to_numpy(float); l = m5["low"].to_numpy(float)
    c = m5["close"].to_numpy(float)
    dates = times.dt.date.to_numpy()
    hr = times.dt.hour.to_numpy()
    a = atr_np(h, l, c, atr_n)
    box_hi = pd.Series(h).rolling(sq_n).max().to_numpy()
    box_lo = pd.Series(l).rolling(sq_n).min().to_numpy()

    trades: list[Trade] = []
    busy_until = -1
    for k in range(sq_n + atr_n + 2, len(c)):
        if k <= busy_until or hr[k] not in hours or not (a[k] > 0):
            continue
        bh, bl = box_hi[k - 1], box_lo[k - 1]     # squeeze box BEFORE this bar
        if (bh - bl) > sq_pct * sq_n * a[k]:
            continue                              # not compressed
        side = 0
        if c[k] > bh:
            side = 1
        elif c[k] < bl:
            side = -1
        if side == 0:
            continue
        if bias_m5 is not None and bias_m5[k] != side:
            continue
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


def run_fvg_scalp(m5: pd.DataFrame, *, min_fvg_atr=0.3, retrace_w=12,
                  tp_r=1.0, buf_atr=0.2, atr_n=14, hold_bars=24,
                  hours=(7, 8, 9, 12, 13, 14),
                  bias_m5: np.ndarray | None = None) -> list[Trade]:
    """Trend-aligned FVG continuation scalp: a displacement bar leaves an FVG
    (>= min_fvg_atr x ATR wide) in the H4 trend direction; enter the first
    retrace to the proximal edge within retrace_w bars, SL beyond the distal
    edge + buffer, TP = tp_r x risk, short hold."""
    times = m5["time"]
    h = m5["high"].to_numpy(float); l = m5["low"].to_numpy(float)
    c = m5["close"].to_numpy(float)
    dates = times.dt.date.to_numpy()
    hr = times.dt.hour.to_numpy()
    a = atr_np(h, l, c, atr_n)

    trades: list[Trade] = []
    busy_until = -1
    for k in range(atr_n + 3, len(c)):
        if k <= busy_until or hr[k] not in hours or not (a[k] > 0):
            continue
        side = 0
        if l[k] > h[k - 2] and (l[k] - h[k - 2]) >= min_fvg_atr * a[k]:
            side = 1                       # bullish FVG
            prox, dist = l[k], h[k - 2]
        elif h[k] < l[k - 2] and (l[k - 2] - h[k]) >= min_fvg_atr * a[k]:
            side = -1                      # bearish FVG
            prox, dist = h[k], l[k - 2]
        if side == 0:
            continue
        if bias_m5 is not None and bias_m5[k] != side:
            continue                       # continuation: WITH the H4 trend
        entry_j = None
        for j in range(k + 1, min(k + 1 + retrace_w, len(c))):
            if side > 0 and l[j] <= prox:
                entry_j = j
                break
            if side < 0 and h[j] >= prox:
                entry_j = j
                break
        if entry_j is None:
            continue
        entry = prox
        sl = dist - (buf_atr * a[k] * (1 if side > 0 else -1))
        risk = abs(entry - sl)
        if risk <= 0:
            continue
        tp = entry + side * tp_r * risk
        if (side > 0 and l[entry_j] <= sl) or (side < 0 and h[entry_j] >= sl):
            continue                       # retrace ran straight through
        ke, px, out = walk_exit(h, l, c, dates, entry_j, side, entry, sl, tp,
                                hold_bars, False, times)
        pnl = side * (px - entry) - COST_PTS
        trades.append(Trade(times.iloc[entry_j], side, entry, sl, tp,
                            times.iloc[ke], px, out, pnl))
        busy_until = ke
    return trades


def sweep_scalp2(m5, top, h4=None):
    print("\n===== scalping campaign 5: burst / squeeze / volume ignition =====")
    rows = []
    bias = h4_bias_on_m5(m5, h4) if h4 is not None else None
    kz = (7, 8, 9, 12, 13, 14)
    wide = tuple(range(1, 16))
    for range_atr in (1.5, 2.0, 2.5):
        for tp_atr in (0.6, 0.8, 1.2):
            for sl_atr in (0.8, 1.0, 1.5):
                for hours, htag in ((kz, "KZ"), (wide, "1-15")):
                    for vol_x, vtag in ((None, ""), (1.5, "+vol1.5"), (2.0, "+vol2")):
                        for b, btag in ((None, ""), (bias, "+H4bias")):
                            label = (f"burst r{range_atr} tp{tp_atr} sl{sl_atr} "
                                     f"{htag}{vtag}{btag}")
                            tr, te = split_summary(
                                run_burst_scalp(m5, range_atr=range_atr,
                                                tp_atr=tp_atr, sl_atr=sl_atr,
                                                hours=hours, vol_x=vol_x,
                                                bias_m5=b), label)
                            rows.append((tr, te))
    for min_fvg in (0.3, 0.5, 0.8):
        for tp_r in (0.8, 1.0, 1.5):
            for retrace_w in (6, 12, 24):
                for hours, htag in ((kz, "KZ"), (wide, "1-15")):
                    for b, btag in ((None, ""), (bias, "+H4bias")):
                        label = (f"fvgscalp f{min_fvg} tp{tp_r}R w{retrace_w} "
                                 f"{htag}{btag}")
                        tr, te = split_summary(
                            run_fvg_scalp(m5, min_fvg_atr=min_fvg, tp_r=tp_r,
                                          retrace_w=retrace_w, hours=hours,
                                          bias_m5=b), label)
                        rows.append((tr, te))
    for sq_n in (6, 12):
        for sq_pct in (0.5, 0.7):
            for tp_atr in (0.6, 0.8, 1.2):
                for sl_atr in (0.8, 1.2):
                    for hours, htag in ((kz, "KZ"), (wide, "1-15")):
                        for b, btag in ((None, ""), (bias, "+H4bias")):
                            label = (f"squeeze n{sq_n} p{sq_pct} tp{tp_atr} "
                                     f"sl{sl_atr} {htag}{btag}")
                            tr, te = split_summary(
                                run_squeeze_scalp(m5, sq_n=sq_n, sq_pct=sq_pct,
                                                  tp_atr=tp_atr, sl_atr=sl_atr,
                                                  hours=hours, bias_m5=b), label)
                            rows.append((tr, te))
    _report(rows, top)


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
                             "verify", "lowtf", "mss", "scalp2"])
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
    if args.strategy == "lowtf":
        sweep_lowtf(load("1m"), args.top, h4=h4)
    if args.strategy == "mss":
        sweep_mss(m5, args.top, h4=h4)
    if args.strategy == "scalp2":
        sweep_scalp2(m5, args.top, h4=h4)
    if args.strategy in ("all", "scalper"):
        sweep_scalper(m5, args.top, h4=h4)


if __name__ == "__main__":
    main()
