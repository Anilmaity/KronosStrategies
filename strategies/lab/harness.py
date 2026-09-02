"""
lab/harness.py -- authoritative offline replay harness (2026-09-02 optimization campaign).

Design rules (do not relax without a note in the report):
  * Drives the REAL strategy modules' get_signal() -- never a reimplementation.
  * Applies the REAL shared.gate_rules predicates for the modelable live gates.
  * Bars come from the local parquet cache (2025-01-01 .. 2026-08-12); no network, no DB.
  * Exits resolve on M1 bars, SL checked BEFORE TP within a bar (pessimistic --
    never flatters the strategy on an ambiguous bar).
  * Reports POINTS as primary (matching the 2026-08-02 points-primary fidelity fix)
    plus R-multiples. USD is deliberately NOT reported: lot sizing is a separate
    decision and mixing it in is how the July book hid its real shape.

Usage:
    from lab.harness import load_bars, replay, Cfg
    bars = load_bars()
    res  = replay("s93_fvg_scalp", bars, start="2025-01-01", end="2026-08-12")
"""
from __future__ import annotations

import importlib
import os
import sys
from dataclasses import dataclass, field
from datetime import timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
_STRAT = _HERE.parent
if str(_STRAT) not in sys.path:
    sys.path.insert(0, str(_STRAT))

CACHE = _STRAT / "backtest" / "results" / "bars_cache"

from shared.gate_rules import (            # noqa: E402  -- the real live predicates
    parse_utc_windows, in_news_blackout, sl_too_tight,
)

TF_FILES = {"1m": "is_XAU_USD_1m.parquet", "5m": "is_XAU_USD_5m.parquet",
            "15m": "is_XAU_USD_15m.parquet", "1h": "is_XAU_USD_1h.parquet",
            "4h": "is_XAU_USD_4h.parquet", "1d": "is_XAU_USD_1d.parquet"}


def load_bars(tfs=("1m", "5m", "15m")) -> dict:
    """Load cached OHLC frames with `time` normalised to tz-naive UTC -- exactly
    what tsdb_reader.fetch_candles hands the live runners."""
    out = {}
    for tf in tfs:
        df = pd.read_parquet(CACHE / TF_FILES[tf])
        t = pd.to_datetime(df["time"], utc=True).dt.tz_convert(None)
        df = df.assign(time=t.astype("datetime64[ns]"))
        for c in ("open", "high", "low", "close"):
            df[c] = df[c].astype(float)
        out[tf] = df.sort_values("time").reset_index(drop=True)
    return out


@dataclass
class Cfg:
    """Replay knobs. Defaults reproduce PRODUCTION behaviour as configured today."""
    cost_pts: float = 0.45          # round-trip friction, charged once on entry
    min_sl_dist_pts: float = 1.5    # live MIN_SL_DIST_PTS
    news_blackout: str = "12:25-12:45"
    max_concurrent: int = 0         # 0 -> use the module's CONFIG value
    cooldown_s: int = 0             # 0 -> use the module's CONFIG value
    win_1m: int = 700
    win_5m: int = 160
    win_15m: int = 100
    block_hours: tuple = ()         # extra UTC hours that refuse NEW entries
    # Break-even stop move. 0 = off (the live behaviour: entry_manager writes a STATIC
    # stop and target). When > 0, once price has travelled that many R in favour, the
    # stop moves to the entry price. This is `be=True` in ClaudeTradingRD's
    # backtest_compare.simulate, the setting S94's published PF 1.82 was actually
    # produced with -- see the CORRECTION block in s94_sweep_reversal.py. Default OFF so
    # every result recorded before 2026-09-02 remains directly comparable.
    be_at_r: float = 0.0
    env: dict = field(default_factory=dict)   # per-strategy env overrides
    # Module-constant overrides applied AFTER importlib.reload, e.g.
    # {"_TP_R": 2.0, "_HOURS": (7, 8, 9, 13, 14)}. This is how a sweep reaches
    # a strategy's internal knobs; the module is reloaded each replay so a patch
    # never leaks into the next run.
    patch: dict = field(default_factory=dict)


# Per-strategy runner windows, mirroring compose.yml. A window below a module's
# declared MIN_BARS_* makes get_signal() silently return None on every tick (the
# CHALLENGE_XAU defect class) -- assert_windows() below turns that into a loud error
# instead of an empty result set that reads as "the strategy just didn't trade".
WINDOWS = {
    "s93_fvg_scalp":      dict(win_1m=60,  win_5m=160,  win_15m=100),
    "s99_mss_fvg":        dict(win_1m=60,  win_5m=160,  win_15m=100),
    "s94_sweep_reversal": dict(win_1m=60,  win_5m=1500, win_15m=100),
    "s100_m3_combo":      dict(win_1m=700, win_5m=160,  win_15m=100),
}


def _load_module(name: str):
    pkg = "concept_strategies" if name.lower().startswith("c") else "backtest_strategies"
    return importlib.import_module(f"{pkg}.{name}")


def assert_windows(mod, cfg: "Cfg") -> None:
    """Refuse to run an undersized window -- the same guard research_runner applies."""
    bad = []
    for frame, need, have in (("1M", getattr(mod, "MIN_BARS_1M", None), cfg.win_1m),
                              ("5M", getattr(mod, "MIN_BARS_5M", None), cfg.win_5m),
                              ("15M", getattr(mod, "MIN_BARS_15M", None), cfg.win_15m)):
        if isinstance(need, int) and not isinstance(need, bool) and have < need:
            bad.append(f"win_{frame}={have} < MIN_BARS_{frame}={need}")
    if bad:
        raise ValueError(f"{mod.NAME}: undersized window -> silent no-trade: {'; '.join(bad)}")


def replay(module_name: str, bars: dict, start=None, end=None, cfg: Cfg | None = None,
           progress: bool = False) -> dict:
    cfg = cfg or Cfg()
    # Env overrides MUST be restored afterwards. Without this they leak into every
    # later replay in the same process: the strategy modules read their env flags at
    # call time (S93's _veto_enabled / _gap_cap_atr) and again on importlib.reload, so
    # an arm that set S93_SOFT_VETO=off silently disabled the veto for the rest of the
    # sweep. That produced trade counts violating a hard invariant -- raising
    # min_sl_dist_pts appeared to ADMIT more trades -- which is what exposed the bug.
    _env_saved = {k: os.environ.get(k) for k in (cfg.env or {})}
    for k, v in (cfg.env or {}).items():
        os.environ[k] = str(v)
    try:
        return _replay_inner(module_name, bars, start, end, cfg)
    finally:
        for k, old in _env_saved.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old


def _replay_inner(module_name: str, bars: dict, start, end, cfg: "Cfg") -> dict:
    mod = _load_module(module_name)
    importlib.reload(mod)                       # pick up env-dependent module constants
    for attr, val in (cfg.patch or {}).items():
        if not hasattr(mod, attr):
            raise AttributeError(f"{module_name} has no constant {attr!r} to patch")
        setattr(mod, attr, val)
    if hasattr(mod, "reset_state"):
        mod.reset_state()
    # apply the strategy's compose windows unless the caller overrode them
    for k, v in WINDOWS.get(module_name, {}).items():
        if getattr(cfg, k) == getattr(Cfg(), k):
            setattr(cfg, k, v)
    assert_windows(mod, cfg)
    scfg = mod.CONFIG
    cooldown = cfg.cooldown_s or scfg.cooldown_s
    maxc = cfg.max_concurrent or getattr(scfg, "max_concurrent_positions", 1)

    m1, m5, m15 = bars["1m"], bars["5m"], bars["15m"]
    t1 = m1["time"].to_numpy("datetime64[ns]")
    lo = np.datetime64(pd.Timestamp(start)) if start else t1[0]
    hi = np.datetime64(pd.Timestamp(end)) if end else t1[-1]

    t5 = m5["time"].to_numpy("datetime64[ns]")
    t15 = m15["time"].to_numpy("datetime64[ns]")

    # Warm-up must satisfy EVERY frame, not just M1. A win_5m of 1500 needs ~7500 M1
    # bars behind the first traded bar; guaranteeing only win_1m silently handed the
    # strategy a truncated M5 window for weeks (S94's level universe is built from it).
    i0 = max(int(np.searchsorted(t1, lo)),
             cfg.win_1m + 5, cfg.win_5m * 5 + 5, cfg.win_15m * 15 + 5)
    i1 = int(np.searchsorted(t1, hi))
    if i0 >= i1:
        raise ValueError(f"{module_name}: window needs more warm-up than the data holds "
                         f"(i0={i0} >= i1={i1}); start the replay later or shrink win_*")
    h1, l1, c1 = (m1[c].to_numpy(float) for c in ("high", "low", "close"))

    wins = parse_utc_windows(cfg.news_blackout)
    open_trades: list = []
    rows: list = []
    last_entry_ts = None
    _last_j5 = _last_j15 = -1
    _w5m_cache = _w15m_cache = None

    for i in range(i0, i1):
        now = pd.Timestamp(t1[i]).to_pydatetime().replace(tzinfo=timezone.utc)

        # ---- manage open positions on THIS bar (SL before TP: pessimistic) ----
        still = []
        for tr in open_trades:
            hit = None
            if tr["side"] == "BUY":
                if l1[i] <= tr["sl"]:
                    hit = ("SL", tr["sl"])
                elif h1[i] >= tr["tp"]:
                    hit = ("TP", tr["tp"])
            else:
                if h1[i] >= tr["sl"]:
                    hit = ("SL", tr["sl"])
                elif l1[i] <= tr["tp"]:
                    hit = ("TP", tr["tp"])
            if hit is None and tr["expiry"] is not None and now >= tr["expiry"]:
                hit = ("TIME", c1[i])
            if hit is None:
                # Break-even arming, evaluated only AFTER the stop and target checks so
                # an ambiguous bar resolves against the trade (same pessimism as
                # SL-before-TP). Once armed the stop sits at the entry price, so a later
                # touch books ~0 minus cost rather than -1R.
                if cfg.be_at_r > 0 and not tr["be_armed"]:
                    if tr["side"] == "BUY":
                        if h1[i] >= tr["entry"] + cfg.be_at_r * tr["risk"]:
                            tr["sl"] = tr["entry"]
                            tr["be_armed"] = True
                    elif l1[i] <= tr["entry"] - cfg.be_at_r * tr["risk"]:
                        tr["sl"] = tr["entry"]
                        tr["be_armed"] = True
                still.append(tr)
                continue
            outcome, xpx = hit
            raw = (xpx - tr["entry"]) if tr["side"] == "BUY" else (tr["entry"] - xpx)
            pts = raw - cfg.cost_pts
            rows.append(dict(strategy=module_name, side=tr["side"], entry_time=tr["etime"],
                             entry_px=tr["entry"], sl=tr["sl"], tp=tr["tp"],
                             exit_time=now, exit_px=xpx, outcome=outcome,
                             pts=round(pts, 4), risk=round(tr["risk"], 4),
                             r=round(pts / tr["risk"], 4) if tr["risk"] > 0 else 0.0,
                             hour=tr["etime"].hour, be_armed=tr["be_armed"],
                             reason=tr["reason"]))
        open_trades = still

        if len(open_trades) >= maxc:
            continue
        if last_entry_ts is not None and (now - last_entry_ts).total_seconds() < cooldown:
            continue

        # ---- build the runner's windows (closed bars only, tails as-is) ----
        # The M5/M15 slices only change when a new bar on that frame closes -- roughly
        # once every 5 (resp. 15) M1 bars -- but re-slicing them on EVERY M1 bar was the
        # dominant cost: S94 uses win_5m=1500, so it was building a 1500-row frame ~490k
        # times per replay instead of ~9.5k. Cache by slice index. The cached object is
        # handed to get_signal unchanged, so results are identical.
        j5 = int(np.searchsorted(t5, t1[i], side="right"))
        j15 = int(np.searchsorted(t15, t1[i], side="right"))
        if j5 < 30:
            continue
        if j5 != _last_j5:
            _w5m_cache = m5.iloc[max(0, j5 - cfg.win_5m): j5]
            _last_j5 = j5
        if j15 != _last_j15:
            _w15m_cache = m15.iloc[max(0, j15 - cfg.win_15m): j15]
            _last_j15 = j15
        w1m = m1.iloc[max(0, i - cfg.win_1m + 1): i + 1]
        w5m, w15m = _w5m_cache, _w15m_cache

        sig = mod.get_signal(w1m, w5m, w15m, now)
        if sig is None:
            continue

        # ---- the modelable live entry gates ----
        if now.hour in cfg.block_hours:
            continue
        if in_news_blackout(now, wins):
            continue
        if sl_too_tight(sig.entry_price, sig.stop_loss, cfg.min_sl_dist_pts):
            continue

        risk = abs(float(sig.entry_price) - float(sig.stop_loss))
        if risk <= 0:
            continue
        mh = getattr(sig, "max_hold_min", None)
        open_trades.append(dict(side=sig.side, entry=float(sig.entry_price),
                                sl=float(sig.stop_loss), tp=float(sig.take_profit),
                                risk=risk, etime=now, reason=sig.reason, be_armed=False,
                                expiry=(now + timedelta(minutes=float(mh))) if mh else None))
        last_entry_ts = now

    return summarize(module_name, rows, cfg)


def summarize(name: str, rows: list, cfg: Cfg) -> dict:
    base = dict(strategy=name, cost=cfg.cost_pts, min_sl=cfg.min_sl_dist_pts,
                block_hours=list(cfg.block_hours))
    if not rows:
        return {**base, "n": 0, "pts": 0.0, "pf": 0.0, "wr": 0.0, "r": 0.0,
                "exp_r": 0.0, "exp_pts": 0.0, "maxdd_pts": 0.0, "trades": pd.DataFrame()}
    df = pd.DataFrame(rows)
    w = df[df.pts > 0]
    l = df[df.pts <= 0]
    gp, gl = w.pts.sum(), -l.pts.sum()
    eq = df.pts.cumsum()
    return {**base,
            "n": len(df), "pts": round(df.pts.sum(), 1),
            "pf": round(gp / gl, 3) if gl > 0 else float("inf"),
            "wr": round(100 * len(w) / len(df), 1),
            "r": round(df.r.sum(), 2), "exp_r": round(df.r.mean(), 4),
            "exp_pts": round(df.pts.mean(), 3),
            "maxdd_pts": round(float((eq - eq.cummax()).min()), 1),
            "avg_win": round(w.pts.mean(), 2) if len(w) else 0.0,
            "avg_loss": round(l.pts.mean(), 2) if len(l) else 0.0,
            "trades": df}


def fmt(res: dict) -> str:
    return (f"{res['strategy']:<26} n={res['n']:<5} pts={res['pts']:>9.1f} "
            f"PF={res['pf']:<6} WR={res['wr']:<5}% R={res['r']:>8.1f} "
            f"expR={res['exp_r']:<8} DD={res['maxdd_pts']:>8.1f}")
