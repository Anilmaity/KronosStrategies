"""lab/tools/fetch_oanda_s5.py -- full-history XAU_USD S5 cache from OANDA (practice).

Writes one parquet per calendar month into backtest/results/bars_cache/s5/XAU_USD/
with the schema lab.s5exit already reads: time (UTC), o/h/l/c (mid), bid_c, ask_c,
volume. Resumable: a month whose file is marked complete in MANIFEST.json is skipped;
the month in progress is always re-fetched whole. Complete candles only.

    OANDA_API_KEY=... python -m lab.tools.fetch_oanda_s5 --from 2024-08-15 [--to now]

Rate: ~5,000 candles per request, 0.15 s pause, exponential backoff on 429/5xx.
Never prints the key."""
from __future__ import annotations

import argparse, json, os, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import requests

_HERE = Path(__file__).resolve().parent
OUT = _HERE.parent.parent / "backtest" / "results" / "bars_cache" / "s5" / "XAU_USD"
COUNT = 5000
COLS = ["time", "o", "h", "l", "c", "bid_c", "ask_c", "volume"]


def _session(key: str, practice: bool) -> tuple[requests.Session, str]:
    base = "https://api-fxpractice.oanda.com/v3" if practice else "https://api-fxtrade.oanda.com/v3"
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {key}", "Accept-Datetime-Format": "RFC3339"})
    return s, base


def _get(s: requests.Session, url: str, params: dict) -> list[dict]:
    delay = 1.0
    for attempt in range(8):
        r = s.get(url, params=params, timeout=60)
        if r.status_code == 200:
            return r.json().get("candles", [])
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(delay); delay = min(delay * 2, 30); continue
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    raise RuntimeError("gave up after retries")


def fetch_month(s, base, m_start: datetime, m_end: datetime, log) -> pd.DataFrame:
    rows, frm, pages = [], m_start, 0
    while frm < m_end:
        c = _get(s, f"{base}/instruments/XAU_USD/candles",
                 {"granularity": "S5", "price": "MBA", "from": frm.isoformat().replace("+00:00", "Z"),
                  "count": COUNT, "dailyAlignment": 0, "alignmentTimezone": "UTC"})
        pages += 1
        for k in c:
            # OANDA rejects count together with from+to, so clip the month here
            if k.get("complete", False) and datetime.fromisoformat(k["time"].replace("Z", "+00:00")) < m_end:
                rows.append((k["time"], float(k["mid"]["o"]), float(k["mid"]["h"]), float(k["mid"]["l"]),
                             float(k["mid"]["c"]), float(k["bid"]["c"]), float(k["ask"]["c"]), float(k["volume"])))
        if not c:
            break
        last = datetime.fromisoformat(c[-1]["time"].replace("Z", "+00:00"))
        if last <= frm:
            break
        frm = last + timedelta(seconds=5)
        if len(c) < COUNT or last >= m_end:
            break
        time.sleep(0.15)
    if not rows:
        return pd.DataFrame(columns=COLS)
    df = pd.DataFrame(rows, columns=COLS)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.drop_duplicates("time", keep="last").sort_values("time").reset_index(drop=True)
    log(f"  {m_start:%Y-%m}: {len(df):,} bars in {pages} pages, {df.time.min():%m-%d %H:%M} -> {df.time.max():%m-%d %H:%M}")
    return df


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", required=True)
    ap.add_argument("--to", default="now")
    ap.add_argument("--force", action="store_true", help="re-fetch months marked complete")
    a = ap.parse_args(argv)
    key = (os.environ.get("OANDA_API_KEY") or "").strip()  # .env may be CRLF
    if not key:
        print("OANDA_API_KEY not set", file=sys.stderr); return 2
    practice = os.getenv("OANDA_PRACTICE", "true").strip().lower() not in ("false", "0", "no")
    s, base = _session(key, practice)
    OUT.mkdir(parents=True, exist_ok=True)
    manifest_p = OUT / "MANIFEST.json"
    manifest = json.loads(manifest_p.read_text()) if manifest_p.exists() else {}
    start = datetime.fromisoformat(a.frm).replace(tzinfo=timezone.utc)
    end = datetime.now(timezone.utc) if a.to == "now" else datetime.fromisoformat(a.to).replace(tzinfo=timezone.utc)
    log = lambda m: print(m, flush=True)
    log(f"S5 XAU_USD {start:%Y-%m-%d} -> {end:%Y-%m-%d %H:%M} ({'practice' if practice else 'live'}) -> {OUT}")
    t0 = time.time(); total = 0
    cur = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while cur < end:
        nxt = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
        m_start, m_end = max(cur, start), min(nxt, end)
        tag = f"{cur:%Y-%m}"
        is_last = nxt >= end
        if manifest.get(tag, {}).get("complete") and not a.force and not is_last:
            log(f"  {tag}: skip (complete, {manifest[tag]['rows']:,} bars)")
            cur = nxt; continue
        df = fetch_month(s, base, m_start, m_end, log)
        df.to_parquet(OUT / f"{tag}.parquet", index=False)
        manifest[tag] = {"rows": int(len(df)), "complete": not is_last,
                         "first": str(df.time.min()) if len(df) else None, "last": str(df.time.max()) if len(df) else None,
                         "fetched_at": datetime.now(timezone.utc).isoformat()}
        manifest_p.write_text(json.dumps(manifest, indent=1, sort_keys=True))
        total += len(df); cur = nxt
    log(f"done: {total:,} bars fetched in {time.time() - t0:,.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
