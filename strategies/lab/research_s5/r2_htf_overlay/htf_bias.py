"""HTF-bias gate builders for the r2_htf_overlay study (PROTOCOL.md §3).

Every gate is a pure function of (entry_time, side, entry_px) and of higher-timeframe
candles that CLOSED before the fill. Availability is asserted per trade, never assumed.

Conventions
-----------
* M1 bars are left-labelled (bar start). The harness labels a trade's `entry_time` with the
  signal bar's OPEN and fills at that bar's CLOSE, so the fill happens at entry_time + 1 min
  and everything that closed at or before `entry_time + 1 min` is known at the fill.
* Trading day = 18:00 NY -> 17:00 NY on the DST-aware wall clock (`America/New_York`),
  labelled by the date of its 18:00 open. Weeks group trading days Sun..Thu labels
  (= Mon..Fri sessions).
* A reference day with fewer than MIN_BARS_VALID M1 bars is invalid (holiday / feed hole).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

_STRAT = Path(__file__).resolve().parents[3]
M1_PATH = _STRAT / "backtest" / "results" / "bars_cache_2y" / "is_XAU_USD_1m.parquet"
D1_UTC_PATH = _STRAT / "backtest" / "results" / "bars_cache_2y" / "is_XAU_USD_1d.parquet"
TZ = "America/New_York"
OPEN_HOUR = 18
MIN_BARS_VALID = 600
ONE_MIN = np.timedelta64(60, "s")


# ── candles ──────────────────────────────────────────────────────────────────
def load_m1() -> pd.DataFrame:
    m = pd.read_parquet(M1_PATH)
    m["time"] = pd.to_datetime(m["time"], utc=True)
    for c in ("open", "high", "low", "close"):
        m[c] = m[c].astype(float)
    return m.sort_values("time").reset_index(drop=True)


def trading_day(t_utc: pd.Series, open_hour: int = OPEN_HOUR) -> pd.Series:
    local = t_utc.dt.tz_convert(TZ).dt.tz_localize(None)
    return (local - pd.Timedelta(hours=open_hour)).dt.normalize()


def ny_daily(m1: pd.DataFrame, open_hour: int = OPEN_HOUR) -> pd.DataFrame:
    """NY-anchored daily candles with availability columns."""
    td = trading_day(m1["time"], open_hour)
    g = m1.groupby(td, sort=True)
    d = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
              close=("close", "last"), n_bars=("time", "size"),
              utc_start=("time", "min"), utc_end=("time", "max"))
    d["utc_end_close"] = d["utc_end"] + pd.Timedelta(minutes=1)
    d["valid"] = d["n_bars"] >= MIN_BARS_VALID
    d.index.name = "trading_day"
    return d.reset_index()


def utc_daily_from_cache() -> pd.DataFrame:
    """The harness's own daily frame (UTC-midnight aligned), for the SMA20 control and the
    B1 anchor sensitivity. Closed once time + 1 day has passed (lab.harness.RegimeGate)."""
    d = pd.read_parquet(D1_UTC_PATH)
    d["time"] = pd.to_datetime(d["time"], utc=True)
    d = d.sort_values("time").reset_index(drop=True)
    out = pd.DataFrame({
        "trading_day": d["time"].dt.tz_localize(None).dt.normalize(),
        "open": d["open"].astype(float), "high": d["high"].astype(float),
        "low": d["low"].astype(float), "close": d["close"].astype(float),
        "n_bars": 1440, "utc_start": d["time"],
        "utc_end_close": d["time"] + pd.Timedelta(days=1),
    })
    out["valid"] = True
    return out


def ny_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """Weekly candles from VALID trading days. A day labelled Sunday (opens Sun 18:00 NY)
    belongs to the ISO week of the following Monday, so key = label + 1 day."""
    v = daily[daily["valid"]].copy()
    key = (v["trading_day"] + pd.Timedelta(days=1)).dt.isocalendar()
    v["week"] = key["year"].astype(int) * 100 + key["week"].astype(int)
    g = v.groupby("week", sort=True)
    w = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
              close=("close", "last"), n_days=("trading_day", "size"),
              utc_start=("utc_start", "min"), utc_end_close=("utc_end_close", "max"),
              first_day=("trading_day", "min"))
    w["valid"] = w["n_days"] >= 3
    return w.reset_index()


# ── engines ──────────────────────────────────────────────────────────────────
def previous_candle_engine(c: pd.DataFrame, range_lookback: int = 3) -> pd.DataFrame:
    """Spec §2.3 on a sequence of closed candles. `implied_bias` at row i is the bias for
    the NEXT candle. Re-implements ClaudeTradingRD detectors/bias.previous_candle_state."""
    h, l, cl = c["high"].to_numpy(), c["low"].to_numpy(), c["close"].to_numpy()
    n = len(c)
    ph = np.r_[np.nan, h[:-1]]
    pl = np.r_[np.nan, l[:-1]]
    took_high = h > ph
    took_low = l < pl
    closed_above = cl > ph
    closed_below = cl < pl
    state = np.full(n, "inside", dtype=object)
    bias = np.full(n, "none", dtype=object)
    both = took_high & took_low
    oh = took_high & ~took_low
    ol = took_low & ~took_high
    state[both] = "both"
    state[oh & closed_above] = "continuation"; bias[oh & closed_above] = "bullish"
    state[oh & ~closed_above] = "reversal";    bias[oh & ~closed_above] = "bearish"
    state[ol & closed_below] = "continuation"; bias[ol & closed_below] = "bearish"
    state[ol & ~closed_below] = "reversal";    bias[ol & ~closed_below] = "bullish"
    inside = ~took_high & ~took_low
    inside[0] = False  # first candle has no predecessor: undefined, not inside
    state[0] = "undefined"
    resolved = np.where(oh, "bullish", np.where(ol, "bearish", None))
    trend = pd.Series(resolved, dtype=object).ffill().fillna("none").to_numpy()
    run = pd.Series(inside.astype(int)).rolling(range_lookback, min_periods=range_lookback).sum().to_numpy()
    range_bound = inside & (run >= range_lookback)
    bias[inside] = trend[inside]
    state[inside] = "inside"
    state[range_bound] = "range_bound"; bias[range_bound] = "none"
    bias[0] = "none"
    return pd.DataFrame({"state": state, "implied_bias": bias, "trend": trend,
                         "prev_high": ph, "prev_low": pl}, index=c.index)


def structure_engine(c: pd.DataFrame, width: int = 1) -> pd.Series:
    """Daily BOS/CHoCH state on closed candles (pillar-01). A swing high at i needs
    H[i] > H[i-k] and H[i] > H[i+k] for k=1..width; it is confirmed at the close of i+width.
    State at row j (after j's close): bullish from a close above the most recent confirmed
    swing high until a close below the most recent confirmed swing low; mirror; 'none' before
    the first break."""
    h, l, cl = c["high"].to_numpy(), c["low"].to_numpy(), c["close"].to_numpy()
    n = len(c)
    sh_level, sl_level = np.nan, np.nan
    # confirmed swings are inserted at the row where they become known (i + width)
    conf_high = {}
    conf_low = {}
    for i in range(width, n - width):
        if all(h[i] > h[i - k] and h[i] > h[i + k] for k in range(1, width + 1)):
            conf_high.setdefault(i + width, []).append(h[i])
        if all(l[i] < l[i - k] and l[i] < l[i + k] for k in range(1, width + 1)):
            conf_low.setdefault(i + width, []).append(l[i])
    state = np.full(n, "none", dtype=object)
    cur = "none"
    for j in range(n):
        # breaks are tested against swings confirmed strictly before j's close (rows < j)
        if not np.isnan(sh_level) and cl[j] > sh_level:
            cur = "bullish"
        elif not np.isnan(sl_level) and cl[j] < sl_level:
            cur = "bearish"
        state[j] = cur
        if j in conf_high:
            sh_level = conf_high[j][-1]
        if j in conf_low:
            sl_level = conf_low[j][-1]
    return pd.Series(state, index=c.index)


# ── trade -> reference candle joins ──────────────────────────────────────────
def _fill_time(entry_time: pd.Series) -> np.ndarray:
    return entry_time.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy("datetime64[ns]") + ONE_MIN


def newest_closed(candles: pd.DataFrame, fill: np.ndarray) -> np.ndarray:
    """Index (into candles) of the newest candle whose utc_end_close <= fill, else -1."""
    closes = candles["utc_end_close"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy("datetime64[ns]")
    assert np.all(np.diff(closes) > np.timedelta64(0, "s")), "candles must be strictly ordered"
    return np.searchsorted(closes, fill, side="right") - 1


def _side_vs_bias(side: np.ndarray, bias: np.ndarray) -> np.ndarray:
    want = np.where(side == "BUY", "bullish", "bearish")
    out = np.full(len(side), "none", dtype=object)
    out[(bias == want)] = "aligned"
    out[(bias != want) & np.isin(bias, ["bullish", "bearish"])] = "against"
    return out


def label_pce(trades: pd.DataFrame, daily_valid: pd.DataFrame, eng: pd.DataFrame,
              own_day: pd.Series | None = None) -> tuple[np.ndarray, dict]:
    """B1 / B4: bias of the newest closed VALID reference candle applied to the trade.
    `daily_valid` and `eng` share an index (valid candles only, in order)."""
    fill = _fill_time(trades["entry_time"])
    idx = newest_closed(daily_valid, fill)
    ok = idx >= 0
    bias = np.full(len(trades), "missing", dtype=object)
    bias[ok] = eng["implied_bias"].to_numpy()[idx[ok]]
    # assertions: reference closed before the fill, and is not the trade's own day
    closes = daily_valid["utc_end_close"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy("datetime64[ns]")
    assert np.all(closes[idx[ok]] <= fill[ok])
    if own_day is not None:
        ref_day = daily_valid["trading_day"].to_numpy()[idx[ok]]
        assert np.all(ref_day < own_day.to_numpy()[ok]), "reference candle is the trade's own day"
    stale = np.zeros(len(trades), dtype=bool)
    if own_day is not None:
        # reference older than the immediately preceding calendar session (holiday / hole)
        gap = (own_day.to_numpy()[ok] - daily_valid["trading_day"].to_numpy()[idx[ok]]) / np.timedelta64(1, "D")
        stale[ok] = gap > 3.5   # > a weekend
    lab = _side_vs_bias(trades["side"].to_numpy(), bias)
    lab[~ok] = "missing"
    return lab, {"missing": int((~ok).sum()), "stale_ref": int(stale.sum())}


def label_pd_range(trades: pd.DataFrame, daily_valid: pd.DataFrame, lookback: int = 3,
                   classic: bool = True) -> tuple[np.ndarray, dict]:
    """B2: premium/discount of the range of the last `lookback` closed valid days."""
    fill = _fill_time(trades["entry_time"])
    idx = newest_closed(daily_valid, fill)
    ok = idx >= lookback - 1
    hi = daily_valid["high"].rolling(lookback).max().to_numpy()
    lo = daily_valid["low"].rolling(lookback).min().to_numpy()
    eq = np.full(len(trades), np.nan)
    eq[ok] = (hi[idx[ok]] + lo[idx[ok]]) / 2.0
    px = trades["entry_px"].to_numpy(float)
    side = trades["side"].to_numpy()
    lab = np.full(len(trades), "missing", dtype=object)
    below = px < eq
    above = px > eq
    # classic: long in discount (below EQ), short in premium (above EQ)
    long_ok = below if classic else above
    short_ok = above if classic else below
    is_buy = side == "BUY"
    lab[ok & is_buy & long_ok] = "aligned"
    lab[ok & is_buy & ~long_ok & (px != eq)] = "against"
    lab[ok & ~is_buy & short_ok] = "aligned"
    lab[ok & ~is_buy & ~short_ok & (px != eq)] = "against"
    lab[ok & (px == eq)] = "none"
    return lab, {"missing": int((~ok).sum()), "eq": eq}


def label_day_open(trades: pd.DataFrame, daily_all: pd.DataFrame, deadband_adr: float = 0.0,
                   daily_valid: pd.DataFrame | None = None) -> tuple[np.ndarray, dict]:
    """B3: the developing daily candle — entry_px vs today's NY open (momentum reading).
    deadband_adr > 0: |entry_px - open| < deadband_adr * ADR20 -> none."""
    td = trading_day(trades["entry_time"])
    day = daily_all.set_index("trading_day")
    ok = td.isin(day.index).to_numpy()
    opn = np.full(len(trades), np.nan)
    first = np.full(len(trades), np.datetime64("NaT"), dtype="datetime64[ns]")
    opn[ok] = day.loc[td[ok], "open"].to_numpy()
    first[ok] = day.loc[td[ok], "utc_start"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy("datetime64[ns]")
    et = trades["entry_time"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy("datetime64[ns]")
    assert np.all(et[ok] >= first[ok]), "entry before the day's first bar"
    px = trades["entry_px"].to_numpy(float)
    diff = px - opn
    band = np.zeros(len(trades))
    if deadband_adr > 0:
        assert daily_valid is not None
        adr = daily_valid.assign(rng=daily_valid["high"] - daily_valid["low"])["rng"].rolling(20).mean().to_numpy()
        fill = _fill_time(trades["entry_time"])
        idx = newest_closed(daily_valid, fill)
        band[idx >= 0] = adr[idx[idx >= 0]] * deadband_adr
        ok = ok & ~np.isnan(band)
    side = trades["side"].to_numpy()
    bias = np.full(len(trades), "none", dtype=object)
    bias[ok & (diff > band)] = "bullish"
    bias[ok & (diff < -band)] = "bearish"
    lab = _side_vs_bias(side, bias)
    lab[~ok] = "missing"
    return lab, {"missing": int((~ok).sum()), "open": opn}


def label_struct(trades: pd.DataFrame, daily_valid: pd.DataFrame, width: int = 1) -> tuple[np.ndarray, dict]:
    """B5: daily structure state of the newest closed valid candle."""
    st = structure_engine(daily_valid, width=width).to_numpy()
    fill = _fill_time(trades["entry_time"])
    idx = newest_closed(daily_valid, fill)
    ok = idx >= 0
    bias = np.full(len(trades), "missing", dtype=object)
    bias[ok] = st[idx[ok]]
    lab = _side_vs_bias(trades["side"].to_numpy(), bias)
    lab[~ok] = "missing"
    return lab, {"missing": int((~ok).sum())}


def label_stack(b1: np.ndarray, b3: np.ndarray) -> np.ndarray:
    """B6: aligned iff both aligned; against iff either against; else none."""
    out = np.full(len(b1), "none", dtype=object)
    out[(b1 == "aligned") & (b3 == "aligned")] = "aligned"
    out[(b1 == "against") | (b3 == "against")] = "against"
    out[(b1 == "missing") | (b3 == "missing")] = "missing"
    return out


def label_sma_regime(trades: pd.DataFrame, utc_daily: pd.DataFrame, sma: int = 20) -> tuple[np.ndarray, dict]:
    """C2: lab.harness.RegimeGate semantics. `now` = entry_time (the harness's `now`),
    bar closed once now >= time + 1 day; regime = newest closed close > SMA(sma)."""
    d = utc_daily.sort_values("utc_start")
    t = d["utc_start"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy("datetime64[ns]")
    closes_at = t + np.timedelta64(1, "D")
    close = d["close"].to_numpy(float)
    ma = pd.Series(close).rolling(sma).mean().to_numpy()
    now = trades["entry_time"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy("datetime64[ns]")
    i = np.searchsorted(closes_at, now, side="right") - 1
    ok = (i >= 0)
    defined = np.zeros(len(trades), dtype=bool)
    defined[ok] = ~np.isnan(ma[i[ok]])
    bias = np.full(len(trades), "missing", dtype=object)
    up = np.zeros(len(trades), dtype=bool)
    up[ok & defined] = close[i[ok & defined]] > ma[i[ok & defined]]
    bias[ok & defined & up] = "bullish"
    bias[ok & defined & ~up] = "bearish"
    lab = _side_vs_bias(trades["side"].to_numpy(), bias)
    lab[~(ok & defined)] = "missing"
    return lab, {"missing": int((~(ok & defined)).sum())}


# ── everything at once ───────────────────────────────────────────────────────
def build_all_labels(trades: pd.DataFrame, m1: pd.DataFrame | None = None,
                     daily: pd.DataFrame | None = None) -> tuple[pd.DataFrame, dict]:
    """Return a frame of labels (one column per gate / variant) aligned with `trades`."""
    if daily is None:
        m1 = m1 if m1 is not None else load_m1()
        daily = ny_daily(m1)
    dv = daily[daily["valid"]].reset_index(drop=True)
    eng_d = previous_candle_engine(dv)
    weekly = ny_weekly(daily)
    wv = weekly[weekly["valid"]].reset_index(drop=True)
    eng_w = previous_candle_engine(wv)
    utc_d = utc_daily_from_cache()
    eng_utc = previous_candle_engine(utc_d)
    own = trading_day(trades["entry_time"])

    L = pd.DataFrame(index=trades.index)
    info = {}
    L["B1"], info["B1"] = label_pce(trades, dv, eng_d, own_day=own)
    L["B1_utc"], info["B1_utc"] = label_pce(trades, utc_d, eng_utc)
    for lb in (3, 2, 5):
        L[f"B2_lb{lb}"], info[f"B2_lb{lb}"] = label_pd_range(trades, dv, lookback=lb, classic=True)
    L["B2"] = L["B2_lb3"]
    for db in (0.0, 0.05, 0.10):
        key = "B3" if db == 0 else f"B3_db{db:.2f}"
        L[key], info[key] = label_day_open(trades, daily, deadband_adr=db, daily_valid=dv)
    L["B4"], info["B4"] = label_pce(trades, wv, eng_w)
    for w in (1, 2):
        key = "B5" if w == 1 else "B5_w2"
        L[key], info[key] = label_struct(trades, dv, width=w)
    L["B6"] = label_stack(L["B1"].to_numpy(), L["B3"].to_numpy())
    L["C2"], info["C2"] = label_sma_regime(trades, utc_d, sma=20)
    L["trading_day"] = own.to_numpy()
    return L, info
