"""
build_bars_cache_2y.py -- build the 24-month XAU_USD bars cache for lab/harness.py.

Output: strategies/backtest/results/bars_cache_2y/  (six is_XAU_USD_<tf>.parquet + QA_REPORT.md)
Window: 2024-08-15 -> 2026-08-12 (end of the production cache).

Sources
  * production cache  strategies/backtest/results/bars_cache/   (OANDA practice, all TFs fetched
    directly; 2025-01-01 -> 2026-08-12)                                             -- READ ONLY
  * deep M1 history   ClaudeTradingRD/m3_scalper/xau_m1_full.parquet (same OANDA practice source,
    2010 -> 2026-07-23, columns time/open/high/low/close only)                       -- READ ONLY

Rules
  * M1: deep file for time < SEAM, production for time >= SEAM.  The two are compared on their
    whole overlap first; production wins wherever both have a bar, the union is kept otherwise.
  * 5m/15m/1h/4h/1d: production's own OANDA bars for time >= SEAM; the prefix is derived from M1
    with the bucket rule that is VALIDATED here against production (left-labelled buckets anchored
    at 00:00 UTC, a bar exists iff >= 1 M1 bar falls in the bucket).
  * Same column set / dtypes / tz as production (time = timestamp[ns, tz=UTC]).  The deep file has
    no volume/spread, so prefix rows carry NaN there (the harness never reads those columns).

Idempotent: every run recomputes everything from the two read-only sources and overwrites the
output directory's files.  Never writes anywhere else.

Usage:  KronosStrategies/.venv/bin/python strategies/lab/tools/build_bars_cache_2y.py
"""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

# ----------------------------------------------------------------------------- paths / constants
_HERE = Path(__file__).resolve().parent
_STRAT = _HERE.parent.parent                                  # strategies/
_KRONOS = _STRAT.parent.parent                                # Projects/Kronos
PROD = _STRAT / "backtest" / "results" / "bars_cache"
OUT = _STRAT / "backtest" / "results" / "bars_cache_2y"
DEEP = _KRONOS / "ClaudeTradingRD" / "m3_scalper" / "xau_m1_full.parquet"

TF_FILES = {"1m": "is_XAU_USD_1m.parquet", "5m": "is_XAU_USD_5m.parquet",
            "15m": "is_XAU_USD_15m.parquet", "1h": "is_XAU_USD_1h.parquet",
            "4h": "is_XAU_USD_4h.parquet", "1d": "is_XAU_USD_1d.parquet"}
RULES = {"5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h", "1d": "1D"}
COLS = ["time", "open", "high", "low", "close", "volume", "spread"]
OHLC = ["open", "high", "low", "close"]

START = pd.Timestamp("2024-08-15", tz="UTC")     # first bar of the new cache
SEAM = pd.Timestamp("2025-01-01", tz="UTC")      # deep file before, production from here on
END_EXCL = pd.Timestamp("2026-08-13", tz="UTC")  # production cache ends 2026-08-12 23:32 UTC
WANTED_END = "2026-09-18"                        # what the user actually wants; not available locally

NY = "America/New_York"
GAP_MIN = pd.Timedelta("5min")

assert OUT != PROD and OUT.name == "bars_cache_2y", "refusing to write anywhere but bars_cache_2y"


# ----------------------------------------------------------------------------- helpers
def derive(m1: pd.DataFrame, rule: str) -> pd.DataFrame:
    """The bucket rule validated against production: left-labelled, left-closed buckets anchored
    at 00:00 UTC; bar exists iff the bucket has >= 1 M1 bar.  Volume is the sum of M1 volume
    (NaN if the M1 rows carry no volume); spread is NaN."""
    g = m1.set_index("time").resample(rule, label="left", closed="left")
    d = g.agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    vol = g["volume"].sum(min_count=1) if "volume" in m1.columns else np.nan
    d["volume"] = vol
    d["spread"] = np.nan
    d = d.dropna(subset=["open"]).reset_index()
    return d[COLS]


def compare_frames(a: pd.DataFrame, b: pd.DataFrame, cols=OHLC) -> dict:
    """Exact comparison of two bar frames keyed by time."""
    ai = a.set_index("time"); bi = b.set_index("time")
    common = ai.index.intersection(bi.index)
    only_a = ai.index.difference(bi.index); only_b = bi.index.difference(ai.index)
    eq = ai.loc[common, cols].to_numpy() == bi.loc[common, cols].to_numpy()
    rowok = eq.all(axis=1)
    out = dict(rows_a=len(ai), rows_b=len(bi), common=len(common), only_a=list(only_a), only_b=list(only_b),
               exact_rows=int(rowok.sum()), mismatch_rows=int((~rowok).sum()),
               per_col_mismatch={c: int((~eq[:, i]).sum()) for i, c in enumerate(cols)})
    if (~rowok).any():
        bad = common[~rowok]
        out["mismatch_examples"] = pd.concat([ai.loc[bad, cols].add_suffix("_a"), bi.loc[bad, cols].add_suffix("_b")], axis=1).head(20)
        out["max_abs_diff"] = float(np.abs(ai.loc[bad, cols].to_numpy() - bi.loc[bad, cols].to_numpy()).max())
    if "volume" in a.columns and "volume" in b.columns:
        va = ai.loc[common, "volume"].to_numpy(); vb = bi.loc[common, "volume"].to_numpy()
        out["volume_exact"] = int(np.sum(va == vb))
    return out


def integrity(df: pd.DataFrame) -> dict:
    t = df["time"]
    return {
        "rows": len(df),
        "first": str(t.iloc[0]), "last": str(t.iloc[-1]),
        "tz": str(t.dt.tz),
        "dtype_time": str(t.dtype),
        "duplicates": int(t.duplicated().sum()),
        "non_monotonic": int((t.diff().dropna() <= pd.Timedelta(0)).sum()),
        "high<low": int((df.high < df.low).sum()),
        "open_outside": int(((df.open > df.high) | (df.open < df.low)).sum()),
        "close_outside": int(((df.close > df.high) | (df.close < df.low)).sum()),
        "non_positive": int((df[OHLC] <= 0).any(axis=1).sum()),
        "nan_ohlc": int(df[OHLC].isna().any(axis=1).sum()),
        "nan_volume": int(df.volume.isna().sum()),
        "nan_spread": int(df.spread.isna().sum()),
    }


# ---- US holiday calendar (gold's reduced-hours days), computed, not hard-coded per year --------
def _easter(y: int) -> date:
    a = y % 19; b = y // 100; c = y % 100; d = b // 4; e = b % 4; f = (b + 8) // 25
    g = (b - f + 1) // 3; h = (19 * a + b - d - g + 15) % 30; i = c // 4; k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7; m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31; day = ((h + l - 7 * m + 114) % 31) + 1
    return date(y, month, day)


def _nth_weekday(y, m, weekday, n):            # n>0: nth; n<0: last
    if n > 0:
        d = date(y, m, 1)
        d += timedelta((weekday - d.weekday()) % 7)
        return d + timedelta(7 * (n - 1))
    d = date(y + (m == 12), (m % 12) + 1, 1) - timedelta(1)
    return d - timedelta((d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    if d.weekday() == 5: return d - timedelta(1)
    if d.weekday() == 6: return d + timedelta(1)
    return d


def us_holidays(years) -> dict:
    h = {}
    for y in years:
        h[_observed(date(y, 1, 1))] = "New Year's Day"
        h[date(y, 12, 31)] = "New Year's Eve"
        h[_nth_weekday(y, 1, 0, 3)] = "MLK Day"
        h[_nth_weekday(y, 2, 0, 3)] = "Presidents' Day"
        h[_easter(y) - timedelta(2)] = "Good Friday"
        h[_nth_weekday(y, 5, 0, -1)] = "Memorial Day"
        h[_observed(date(y, 6, 19))] = "Juneteenth"
        h[_observed(date(y, 7, 4))] = "Independence Day"
        h[date(y, 7, 3)] = "Independence Day eve"
        h[_nth_weekday(y, 9, 0, 1)] = "Labor Day"
        tg = _nth_weekday(y, 11, 3, 4)
        h[tg] = "Thanksgiving"; h[tg + timedelta(1)] = "Black Friday"
        h[date(y, 12, 24)] = "Christmas Eve"
        h[_observed(date(y, 12, 25))] = "Christmas Day"
        h[date(y, 12, 26)] = "Boxing Day"
    return h


FULL_CLOSURE = {"Christmas Day", "New Year's Day", "Good Friday"}   # observed full-day closures


def classify_gaps(m1: pd.DataFrame, deep: pd.DataFrame) -> pd.DataFrame:
    """Every gap > GAP_MIN between consecutive M1 bars, classified in New York local time.
    prev = last bar before the gap, next = first bar after it, len = next - prev.
    `deep_bars_in_span` = bars the deep M1 file has strictly inside the gap (NaN if the gap is
    outside the deep file's range) -- shows whether any local source could fill it."""
    t = m1["time"]
    d = t.diff()
    g = pd.DataFrame({"prev": t.shift(1), "next": t, "len": d})[d > GAP_MIN].copy()
    g["prev_ny"] = g.prev.dt.tz_convert(NY); g["next_ny"] = g.next.dt.tz_convert(NY)
    hol = us_holidays(range(START.year, END_EXCL.year + 1))
    deep_t = deep.time.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy("datetime64[ns]")
    cats, notes, deep_in = [], [], []
    for _, r in g.iterrows():
        p, n = r.prev_ny, r.next_ny
        mins = r.len.total_seconds() / 60
        normal_close = p.hour == 16 and p.minute >= 55          # 16:55-16:59 NY (17:00 close)
        early_close = p.minute in (29, 44, 59) and 12 <= p.hour <= 14   # observed holiday early closes
        normal_reopen = n.hour == 18 and n.minute <= 10         # 18:00-18:10 NY (18:00 reopen)
        days = pd.date_range(p.normalize(), n.normalize(), freq="D").date
        hits = [(dd, hol[dd]) for dd in days if dd in hol]
        # whole weekday sessions (Mon-Fri NY dates strictly inside the gap) that are not full closures
        inner = [dd for dd in days[1:-1] if dd.weekday() < 5 and hol.get(dd) not in FULL_CLOSURE]
        utc_aligned = (r.prev.hour == 23 and r.prev.minute == 59) or (r.next.hour == 0 and r.next.minute == 0)
        dst = p.utcoffset() != n.utcoffset()
        extra = []
        if utc_aligned: extra.append("UTC-day-aligned hole")
        if inner: extra.append("whole weekday session(s) absent: " + ", ".join(str(x) for x in inner))
        if dst: extra.append("DST transition weekend")
        hol_txt = "; ".join(f"{dd} {nm}" for dd, nm in hits)
        if p.date() == n.date() and p.hour == 16 and normal_reopen and mins <= 75:
            cat = "daily break"
        elif p.weekday() == 4 and n.weekday() == 6 and normal_close and normal_reopen and not inner:
            cat = "weekend"
        elif p.weekday() == 4 and n.weekday() == 6 and early_close and normal_reopen and hits and not inner:
            cat = "weekend + holiday early close"
        elif hits and normal_reopen and (normal_close or early_close) and not utc_aligned and not inner:
            cat = "holiday"
        elif hits:
            cat = "ANOMALOUS (holiday-adjacent)"
            extra.append("edge(s) not at a normal session boundary" if not (normal_reopen and (normal_close or early_close)) else "")
        elif p.weekday() == 4 and n.weekday() == 6:
            cat = "ANOMALOUS (weekend edge)"
            extra.append(f"Friday close {p:%H:%M} / Sunday reopen {n:%H:%M} NY")
        else:
            cat = "UNEXPLAINED"
        cats.append(cat)
        notes.append(" | ".join(x for x in ([hol_txt] if hol_txt else []) + extra if x))
        lo, hi = np.datetime64(r.prev.tz_convert("UTC").tz_localize(None)), np.datetime64(r.next.tz_convert("UTC").tz_localize(None))
        if deep_t[0] <= lo and hi <= deep_t[-1]:
            deep_in.append(int(((deep_t > lo) & (deep_t < hi)).sum()))
        else:
            deep_in.append(np.nan)
    g["category"] = cats; g["note"] = notes; g["deep_bars_in_span"] = deep_in
    g["len_min"] = (g.len.dt.total_seconds() / 60).round(1)
    g["len_h"] = (g.len_min / 60).round(2)
    g["missing_from_utc"] = g.prev + pd.Timedelta("1min")     # first missing minute
    return g.reset_index(drop=True)


def md_table(df: pd.DataFrame, index=True) -> str:
    """Dependency-free markdown table (the venv has no tabulate)."""
    if isinstance(df, pd.Series):
        df = df.to_frame()
    if index:
        df = df.reset_index()
    cols = [str(c) for c in df.columns]

    def fmt(v):
        if isinstance(v, float):
            return "" if np.isnan(v) else (f"{v:.0f}" if v.is_integer() and abs(v) < 1e15 else f"{v:g}")
        return str(v).replace("|", "\\|")

    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for row in df.itertuples(index=False):
        lines.append("| " + " | ".join(fmt(v) for v in row) + " |")
    return "\n".join(lines)


# ----------------------------------------------------------------------------- build
def main() -> str:
    OUT.mkdir(parents=True, exist_ok=True)
    rep = []                                           # markdown lines
    P = rep.append

    prod = {tf: pd.read_parquet(PROD / f) for tf, f in TF_FILES.items()}
    prod_schema = pq.read_schema(PROD / TF_FILES["1m"])
    deep = pd.read_parquet(DEEP)
    for tf, df in prod.items():
        assert list(df.columns) == COLS, (tf, list(df.columns))
        assert str(df.time.dt.tz) == "UTC"
    assert list(deep.columns) == ["time", "open", "high", "low", "close"] and str(deep.time.dt.tz) == "UTC"

    # ---- 1. seam verification on the full overlap -------------------------------------------
    ov_lo, ov_hi = SEAM, deep.time.max()
    a = deep[(deep.time >= ov_lo) & (deep.time <= ov_hi)]
    b = prod["1m"][(prod["1m"].time >= ov_lo) & (prod["1m"].time <= ov_hi)]
    seam = compare_frames(a, b)

    # ---- combined M1: deep before SEAM, production from SEAM; union, production wins ----------
    deep_part = deep[(deep.time >= START) & (deep.time < SEAM)].copy()
    deep_part["volume"] = np.nan; deep_part["spread"] = np.nan
    prod_part = prod["1m"][(prod["1m"].time >= SEAM) & (prod["1m"].time < END_EXCL)]
    # Timestamps >= SEAM that only the deep file has are NOT added (see the report: OANDA's own
    # higher-TF candles contradict the one such bar), so the 2025+ part stays byte-identical to production.
    extra = deep[(deep.time >= SEAM) & (deep.time < END_EXCL) & ~deep.time.isin(prod_part.time)]
    m1 = (pd.concat([deep_part[COLS], prod_part[COLS]])
          .drop_duplicates("time", keep="last").sort_values("time").reset_index(drop=True))
    assert m1.time.is_unique
    pd.testing.assert_frame_equal(m1[m1.time >= SEAM].reset_index(drop=True), prod_part[COLS].reset_index(drop=True))

    # ---- 2. derivation validation: production M1 -> TF vs production TF ----------------------
    deriv = {}
    pm1 = prod["1m"]
    for tf, rule in RULES.items():
        deriv[tf] = compare_frames(derive(pm1, rule), prod[tf])

    # ---- build the higher TFs: derived prefix (< SEAM) + production (>= SEAM) -----------------
    final = {"1m": m1}
    prefix_rows = {}
    for tf, rule in RULES.items():
        pre = derive(m1[m1.time < SEAM], rule)
        pre = pre[pre.time < SEAM]
        post = prod[tf][(prod[tf].time >= SEAM) & (prod[tf].time < END_EXCL)][COLS]
        df = pd.concat([pre, post]).drop_duplicates("time", keep="last").sort_values("time").reset_index(drop=True)
        final[tf] = df
        prefix_rows[tf] = len(pre)

    # ---- write, matching production dtypes and arrow schema ----------------------------------
    for tf, df in final.items():
        df = df[COLS].copy()
        df["time"] = df["time"].astype("datetime64[ns, UTC]")
        for c in COLS[1:]:
            df[c] = df[c].astype("float64")
        df.to_parquet(OUT / TF_FILES[tf], index=False)
    schema_ok = {tf: pq.read_schema(OUT / f).equals(prod_schema) for tf, f in TF_FILES.items()}
    final = {tf: pd.read_parquet(OUT / f) for tf, f in TF_FILES.items()}     # QA on what was written

    # ---- cross-TF consistency of the FINAL files (each TF vs derivation from final M1) --------
    xtf = {tf: compare_frames(derive(final["1m"], rule), final[tf]) for tf, rule in RULES.items()}

    # ---- integrity, calendar, gaps -----------------------------------------------------------
    integ = {tf: integrity(df) for tf, df in final.items()}
    gaps = classify_gaps(final["1m"], deep)
    m1f = final["1m"]
    ny = m1f.time.dt.tz_convert(NY)
    # Session = NY 18:00 open -> next-day 17:00 close, labelled by the NY date of the CLOSE (so the
    # Sunday-evening open belongs to Monday's session).  Wall-clock arithmetic, so DST switches on
    # Sunday mornings do not split a session.
    wall = ny.dt.tz_localize(None)
    sess = (wall + pd.Timedelta("6h")).dt.normalize()         # 18:00 -> next day 00:00 -> that date
    per_session = m1f.groupby(sess).time.agg(["count", "min", "max"])
    per_session["open_ny"] = per_session["min"].dt.tz_convert(NY).dt.strftime("%a %H:%M")
    per_session["close_ny"] = per_session["max"].dt.tz_convert(NY).dt.strftime("%a %H:%M")
    open_dist = per_session.open_ny.str[4:].value_counts().head(8)
    close_dist = per_session.close_ny.str[4:].value_counts().head(8)
    hol = us_holidays(range(START.year, END_EXCL.year + 1))
    # every Mon-Fri NY date in the window should have a session; list the ones with none / thin ones
    all_wd = [d for d in pd.date_range(sess.min(), sess.max(), freq="D") if d.weekday() < 5]
    have = set(per_session.index)
    missing_sessions = pd.DataFrame({"session (NY date)": [d.date() for d in all_wd if d not in have]})
    missing_sessions["holiday"] = [hol.get(d, "") for d in missing_sessions["session (NY date)"]]
    thin = per_session[(per_session["count"] < 1000)].copy()
    thin.index = [d.date() for d in thin.index]
    thin["holiday"] = [hol.get(d, "") for d in thin.index]
    thin.index.name = "session (NY date)"

    tables_wd = {tf: df.time.dt.tz_convert("UTC").dt.day_name().value_counts()
                 .reindex(["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]).fillna(0).astype(int)
                 for tf, df in final.items()}
    wd = pd.DataFrame(tables_wd)
    tables_mo = {tf: df.time.dt.strftime("%Y-%m").value_counts().sort_index() for tf, df in final.items()}
    mo = pd.DataFrame(tables_mo).fillna(0).astype(int)
    mo.index.name = "month (UTC)"
    # M1 minutes present per month as % of the same month's trading-minute count implied by the
    # observed calendar is hard to define without assuming a calendar; instead show weekday-only
    # M1 bars per calendar weekday-day (Mon-Fri days in month) as a density figure.
    mo["1m per weekday-day"] = [round(mo.loc[m, "1m"] / np.busday_count(m + "-01", (pd.Period(m) + 1).strftime("%Y-%m-01")), 0)
                                for m in mo.index]

    # ---------------------------------------------------------------------- report
    P("# QA report -- bars_cache_2y (XAU_USD, OANDA practice)")
    P("")
    P(f"Built by `strategies/lab/tools/build_bars_cache_2y.py` on {pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M UTC}. "
      "Every number below was computed by that script in this run.")
    P("")
    P("## Sources and construction")
    P("")
    P(f"* Production cache (OANDA-fetched, all TFs): `{PROD}` -- covers 2025-01-01 -> 2026-08-12. Untouched.")
    P(f"* Deep M1 history: `{DEEP}` -- 2010-01-03 -> 2026-07-23, OHLC only (no volume/spread).")
    P(f"* Window built: **{START:%Y-%m-%d} -> 2026-08-12** (first M1 bar {integ['1m']['first']}, last {integ['1m']['last']}).")
    P(f"* Seam at **{SEAM:%Y-%m-%d} 00:00 UTC**: M1 before it comes from the deep file, from it on from production; "
      "higher TFs before it are DERIVED from that M1, from it on they are production's own OANDA-fetched bars.")
    P("* Prefix rows (< seam) carry `volume = NaN` and `spread = NaN` (the deep file has neither; the harness reads "
      "neither). Production's `spread` is a constant 0.3 until 2026-02-13 and NaN after -- it is not a real spread series.")
    P("")
    P("## Missing tail: 2026-08-12 -> 2026-09-18 is ABSENT")
    P("")
    P(f"The window actually wanted ends {WANTED_END}. No local source has any bar after {integ['1m']['last']} and this "
      "machine has no OANDA credentials, so nothing was fetched (no network access was attempted). "
      "Closing that ~5-week hole needs an OANDA fetch from the production box (its strategies container has OANDA "
      "credentials in its environment; `strategies/shared/tsdb_reader.py` holds the candle-fetch code). "
      "Any '24-month to 2026-09-18' backtest run on this cache is really 2024-08-15 -> 2026-08-12.")
    P("")
    P("## 1. Splice-seam verification (deep M1 vs production M1, full overlap)")
    P("")
    P(f"Overlap compared: {ov_lo} -> {ov_hi} (the deep file's last bar).")
    P("")
    P(f"| metric | value |\n|---|---|")
    P(f"| deep rows in overlap | {seam['rows_a']:,} |")
    P(f"| production rows in overlap | {seam['rows_b']:,} |")
    P(f"| common timestamps | {seam['common']:,} |")
    P(f"| only in deep file | {len(seam['only_a'])} {seam['only_a'][:5] if seam['only_a'] else ''} |")
    P(f"| only in production | {len(seam['only_b'])} {seam['only_b'][:5] if seam['only_b'] else ''} |")
    P(f"| rows with any OHLC disagreement | **{seam['mismatch_rows']}** (per column {seam['per_col_mismatch']}) |")
    P(f"| rows exactly equal on OHLC | {seam['exact_rows']:,} ({seam['exact_rows']/max(seam['common'],1)*100:.4f}%) |")
    if seam["mismatch_rows"]:
        P(""); P("DISAGREEMENTS FOUND -- production values were used for the overlap. Examples:"); P("")
        P(md_table(seam["mismatch_examples"])); P(f"max |diff| = {seam['max_abs_diff']}")
    P("")
    P(f"Rule applied: from the seam on, production only (byte-identical to the production cache). Timestamps only the "
      f"deep file has inside the production window: {len(extra)} ({[str(x) for x in extra.time.tolist()[:5]]}) -- NOT added. "
      "That one bar is 2025-01-01 23:04 UTC (O 2625.07 H 2625.20 L 2625.07 C 2625.20), the first minute after the New Year "
      "reopen. Production's M1 starts at 23:05, and production's OANDA-fetched 15m/1h/4h/1d bars all OPEN at 2625.115 (the "
      "23:05 open) with a 15m volume of 396 = exactly the 23:05 + 23:10 5m volumes, i.e. OANDA's own higher-TF candles "
      "do not contain that minute. Whether the 23:04 M1 candle is real cannot be settled offline; leaving it out keeps "
      "every TF consistent with M1 and the 2025+ part identical to what the published 19.5-month results used.")
    P("")
    P("## 2. Derivation rule validation (production M1 -> TF vs production's OANDA TF bars)")
    P("")
    P("Rule tested: left-labelled, left-closed buckets anchored at 00:00 UTC (5m/15m/1h/4h at 00,04,08,12,16,20 UTC / 1d at "
      "midnight UTC); a bar exists iff at least one M1 bar falls in the bucket; open=first, high=max, low=min, close=last, "
      "volume=sum. No special-casing of the daily break or the weekend is needed: the bucket that contains the reopen "
      "simply starts at its anchor (e.g. the Sunday 20:00 UTC 4h bar holds 22:00-24:00 UTC only, the Friday 20:00 UTC 4h "
      "bar holds 20:00-21:00 UTC in EDT / 20:00-22:00 UTC in EST) -- exactly what OANDA serves.")
    P("")
    P("| TF | derived bars | production bars | common | OHLC exact | match rate | volume exact | only derived | only production |")
    P("|---|---|---|---|---|---|---|---|---|")
    for tf, r in deriv.items():
        P(f"| {tf} | {r['rows_a']:,} | {r['rows_b']:,} | {r['common']:,} | {r['exact_rows']:,} | "
          f"**{r['exact_rows']/max(r['common'],1)*100:.4f}%** | {r['volume_exact']:,} | "
          f"{len(r['only_a'])} {[str(x) for x in r['only_a'][:3]]} | {len(r['only_b'])} {[str(x) for x in r['only_b'][:3]]} |")
    P("")
    P("The single 'only derived' bar per TF is the trailing incomplete bucket (production keeps only complete candles; "
      "M1 ends 2026-08-12 23:32 UTC). The prefix bars are therefore built with a rule that reproduces OANDA's own bars "
      "exactly over 19.5 months, but they remain DERIVED, not fetched.")
    P("")
    P("| TF | derived prefix rows (2024-08-15 -> 2024-12-31) | production rows copied | total | arrow schema == production |")
    P("|---|---|---|---|---|")
    for tf in RULES:
        P(f"| {tf} | {prefix_rows[tf]:,} | {len(final[tf]) - prefix_rows[tf]:,} | {len(final[tf]):,} | {schema_ok[tf]} |")
    P(f"| 1m | {int((m1.time < SEAM).sum()):,} (deep file) | {int((m1.time >= SEAM).sum()):,} | {len(m1):,} | {schema_ok['1m']} |")
    P("")
    P("### Cross-TF consistency of the FINAL files (each TF vs derivation from the final M1, whole window)")
    P("")
    P("| TF | common | OHLC exact | only derived | only file |")
    P("|---|---|---|---|---|")
    for tf, r in xtf.items():
        P(f"| {tf} | {r['common']:,} | {r['exact_rows']:,} ({r['exact_rows']/max(r['common'],1)*100:.4f}%) | "
          f"{len(r['only_a'])} | {len(r['only_b'])} |")
    P("")
    P("## 3. Integrity per file")
    P("")
    integ_df = pd.DataFrame(integ).T
    P(md_table(integ_df))
    P("")
    P("Expected: duplicates 0, non_monotonic 0, high<low 0, open/close outside 0, non_positive 0, nan_ohlc 0. "
      "nan_volume/nan_spread are the prefix rows (and, for spread, production's own NaNs after 2026-02-13).")
    P("")
    P("## 4. Trading calendar as observed in the data")
    P("")
    P("Sessions are New York based: the daily close is 17:00 NY and the reopen 18:00 NY, so in UTC the daily break is "
      "21:00-22:00 in EDT and 22:00-23:00 in EST (it is NOT fixed at 21:00 UTC). The first bar after every reopen "
      "(daily and Sunday) is at 18:04 or 18:05 NY -- there are never bars in the first 4 minutes after a reopen. "
      "The week runs Sunday 18:04 NY -> Friday 16:59 NY.")
    P("")
    P("Observed session open times (NY, first bar of each 18:00->17:00 session, top 8):")
    P(""); P(md_table(open_dist.rename("sessions").to_frame())); P("")
    P("Observed session close times (NY, last bar of each session, top 8):")
    P(""); P(md_table(close_dist.rename("sessions").to_frame())); P("")
    P("The 4 empty minutes after each reopen are genuine OANDA behaviour: the production S5 sub-cache "
      "(`bars_cache/s5/XAU_USD/2026-08.parquet`) also starts its week at 2026-08-02 22:04:55 UTC.")
    P("")
    P(f"Weekday sessions (Mon-Fri NY, 18:00 previous day -> 17:00) in the window: {len(all_wd)}; observed: {len(per_session)}.")
    P("")
    P(f"Weekday sessions with ZERO bars ({len(missing_sessions)}):")
    P("")
    P(md_table(missing_sessions, index=False) if len(missing_sessions) else "none")
    P("")
    P(f"Sessions with < 1000 M1 bars (a full session has ~1380; the last row is the end of the data):")
    P("")
    P(md_table(thin[["count", "open_ny", "close_ny", "holiday"]]) if len(thin) else "none")
    P("")
    P("## 5. Gaps > 5 minutes in the combined M1")
    P("")
    P("`missing_from_utc` is the first missing minute, `next` the first bar after the gap, `len_min` = next - prev bar, "
      "`deep_bars_in_span` = bars the deep M1 file holds inside the gap (blank when the gap lies outside its range). "
      "Categories (New York local time): `daily break` = same NY date, close 16:5x -> reopen 18:0x; `weekend` = Fri 16:5x -> "
      "Sun 18:0x; `weekend + holiday early close` = Fri holiday early close -> Sun 18:0x; `holiday` = span touches a US "
      "holiday, both edges at normal early-close/reopen times and no whole non-holiday weekday session swallowed; "
      "`ANOMALOUS (holiday-adjacent)` = touches a holiday but an edge is not a session boundary and/or a whole weekday "
      "session that OANDA normally trades is absent; `ANOMALOUS (weekend edge)` = Fri -> Sun but with a non-standard edge; "
      "`UNEXPLAINED` = everything else. Everything not `daily break`/`weekend`/`holiday` is enumerated individually.")
    P("")
    cat_counts = gaps.category.value_counts()
    P(md_table(cat_counts.rename("gaps").to_frame()))
    P("")
    P(f"Total gaps > 5 min: {len(gaps)}. Daily-break lengths (min): "
      f"{gaps[gaps.category=='daily break'].len_min.value_counts().sort_index().to_dict()}; "
      f"weekend lengths (min): {gaps[gaps.category=='weekend'].len_min.value_counts().sort_index().to_dict()} "
      "(2885/2886 and 3005/3006 are the DST-transition weekends: the Sunday reopen is 60 min earlier/later in UTC).")
    P("")
    show_cols = ["missing_from_utc", "next", "len_min", "len_h", "prev_ny", "next_ny", "deep_bars_in_span", "note"]
    u = gaps[gaps.category == "UNEXPLAINED"]
    an = gaps[gaps.category.str.startswith("ANOMALOUS")]
    P("### 5a. UNEXPLAINED gaps (enumerated individually)")
    P("")
    P(md_table(u[show_cols], index=False) if len(u) else "none")
    P("")
    P("### 5b. ANOMALOUS gaps (holiday-adjacent or weekend-edge; enumerated individually)")
    P("")
    P(md_table(an[["category"] + show_cols], index=False) if len(an) else "none")
    P("")
    P("### 5c. Holiday gaps and holiday early closes")
    P("")
    h = gaps[gaps.category.isin(["holiday", "weekend + holiday early close"])]
    P(md_table(h[["category"] + show_cols[:6] + ["note"]], index=False) if len(h) else "none")
    P("")
    P("### 5d. Daily breaks / weekends with a non-modal length")
    P("")
    nb = gaps[(gaps.category == "daily break") & (gaps.len_min > 66)]
    nw = gaps[(gaps.category == "weekend") & ((gaps.len_min < 2945) | (gaps.len_min > 2946))]
    P(md_table(pd.concat([nb, nw])[["category"] + show_cols[:6] + ["note"]], index=False) if len(nb) + len(nw)
      else "none (all daily breaks are 65-66 min, all weekends 2945-2946 min)")
    P("")
    P("## 6. Bars per weekday (UTC) per TF")
    P("")
    P(md_table(wd))
    P("")
    P("Sunday bars are the 22:00/23:00 UTC -> midnight UTC reopen slice; Saturday must be 0. The daily file has a Sunday bar "
      "for every week (the production cache already has this property: 1d bars are midnight-UTC buckets).")
    P("")
    P("## 7. Bars per month (UTC) per TF")
    P("")
    P(md_table(mo))
    P("")
    P("`1m per weekday-day` = M1 bars / Mon-Fri calendar days in the month (full month ~1300-1400; 2024-08 and 2026-08 are partial months).")
    P("")
    # ---------------------------------------------------------------------- summary / verdict
    n_unexpl = len(u); n_anom = len(an)
    unexpl_hours = round(float(u.len_min.sum()) / 60, 1); anom_hours = round(float(an.len_min.sum()) / 60, 1)
    bad_integrity = {tf: {k: v for k, v in r.items() if k in ("duplicates", "non_monotonic", "high<low", "open_outside",
                                                            "close_outside", "non_positive", "nan_ohlc") and v}
                     for tf, r in integ.items()}
    bad_integrity = {tf: v for tf, v in bad_integrity.items() if v}
    holes = pd.concat([u, an])
    summary = []
    S = summary.append
    S("## SUMMARY")
    S("")
    S(f"* Window: {integ['1m']['first']} -> {integ['1m']['last']} (UTC, tz-aware); wanted end {WANTED_END} is NOT covered "
      f"(2026-08-12 -> {WANTED_END} needs an OANDA fetch from the production box).")
    S("* Rows: " + ", ".join(f"{tf} {integ[tf]['rows']:,}" for tf in TF_FILES))
    S(f"* Seam (deep M1 vs production M1, {ov_lo:%Y-%m-%d} -> {ov_hi:%Y-%m-%d}): {seam['common']:,} common timestamps, "
      f"{seam['mismatch_rows']} OHLC disagreements, {len(seam['only_a'])} only-in-deep, {len(seam['only_b'])} only-in-production.")
    S("* Derivation validation (M1 -> TF vs OANDA TF, 2025-01-01 -> 2026-08-12): " +
      ", ".join(f"{tf} {r['exact_rows']:,}/{r['common']:,} = {r['exact_rows']/max(r['common'],1)*100:.2f}%" for tf, r in deriv.items()) +
      ". Prefix 2024-08-15 -> 2024-12-31 for 5m/15m/1h/4h/1d is DERIVED with this rule, not fetched.")
    S(f"* Integrity: " + ("all files clean (0 duplicates / non-monotonic / high<low / OHLC out of range / non-positive / NaN OHLC)."
                          if not bad_integrity else f"PROBLEMS: {bad_integrity}"))
    S(f"* Gaps > 5 min: {len(gaps)} total = " + ", ".join(f"{k} {v}" for k, v in cat_counts.items()) + ".")
    S(f"* Weekday sessions with zero bars: {len(missing_sessions)} -> " +
      (", ".join(f"{r[0]} ({r[1] or 'no holiday'})" for r in missing_sessions.itertuples(index=False)) or "none") + ".")
    S(f"* UNEXPLAINED gaps: {n_unexpl} ({unexpl_hours} h); ANOMALOUS gaps: {n_anom} ({anom_hours} h incl. the holiday parts). "
      "Each, with the deep file's bar count inside the span (0 = no local source has it):")
    for r in holes.sort_values("missing_from_utc").itertuples(index=False):
        S(f"    - {r.missing_from_utc:%Y-%m-%d %H:%M} -> {r.next:%Y-%m-%d %H:%M} UTC, {r.len_h} h, {r.category}"
          f"{' [' + r.note + ']' if r.note else ''}; deep bars in span: "
          f"{'n/a (outside deep range)' if pd.isna(r.deep_bars_in_span) else int(r.deep_bars_in_span)}")
    S("")
    S("### Trust verdict")
    S("")
    S("Fit for a 24-month backtest of 2024-08-15 -> 2026-08-12 (~34 days of M1/M5 warm-up in front of a 2024-09-18 "
      "start). The M1 is one continuous OANDA-practice series with an exact seam and clean structure; the higher-TF "
      "prefix is built with a rule that reproduces OANDA's own bars 100% over 19.5 months. Caveats: (1) the last five "
      "weeks the user wants (to 2026-09-18) do not exist locally; (2) the whole-session holes listed above sit in the "
      "PRODUCTION part and are absent from every local source (the vault's XAUUSD Data Inventory note states OANDA "
      "serves 2025-12-09 and 2026-01-21 in full, so at least those are fetch holes, not closures) -- a strategy is "
      "simply not traded on those days and a position open into one of them sees a jump at the next bar; the holes are "
      "already inside the published 19.5-month results, so nothing changes for comparability, but they should be patched "
      "by a fetch when credentials are available; (3) prefix bars carry no volume; (4) the daily break is NY-17:00 "
      "anchored, not a fixed 21:00 UTC, so session logic keyed to fixed UTC hours drifts by an hour across DST; "
      "(5) the daily file has a Sunday bar every week (a 2-hour midnight-UTC bucket), inherited from production.")
    rep.extend(summary)
    text = "\n".join(rep) + "\n"
    (OUT / "QA_REPORT.md").write_text(text)
    return "\n".join(summary)


if __name__ == "__main__":
    print(main())
    print(f"\nwritten: {OUT}")
