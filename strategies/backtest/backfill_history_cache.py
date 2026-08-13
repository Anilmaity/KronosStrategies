"""
backfill_history_cache.py — complete the local bars_cache parquets from OANDA.

Usage (from strategies/):
    python -m backtest.backfill_history_cache [--symbol XAU_USD] [--dry-run]

Reads each results/bars_cache/is_<SYMBOL>_<tf>.parquet, finds its last
timestamp, fetches the missing span from OANDA (M1/M5/M15/H1/H4/D1 all
fetched natively — no resampling, avoids alignment drift), merges, and
writes back. Idempotent. Sanity gates abort loudly.

fetch_candles() in shared/tsdb_reader.py already paginates internally (up to
60 pages × 4500 bars = 270 000 cap), so no manual chunking loop is needed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

# OANDA key lives only in tick_data_collector/.env locally.
# Must be loaded BEFORE importing tsdb_reader because tsdb_reader reads
# os.getenv("OANDA_API_KEY") at import time to build its requests.Session.
_REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_REPO_ROOT / "tick_data_collector" / ".env")

from shared.tsdb_reader import fetch_candles  # noqa: E402  (needs env first)

CACHE_DIR = Path(__file__).resolve().parent / "results" / "bars_cache"
TFS = ["1m", "5m", "15m", "1h", "4h", "1d"]
TARGET_END = datetime(2026, 7, 2, tzinfo=timezone.utc)


def merge_frames(existing: pd.DataFrame, fresh: pd.DataFrame) -> pd.DataFrame:
    """Concat existing + fresh, drop duplicate `time` keeping the FRESH row, sort."""
    if fresh.empty:
        return existing.reset_index(drop=True)
    out = pd.concat([existing, fresh], ignore_index=True)
    out = out.drop_duplicates(subset="time", keep="last")
    return out.sort_values("time").reset_index(drop=True)


def _max_market_gap_minutes(df: pd.DataFrame) -> float:
    """Largest gap between consecutive 1m bars EXCLUDING the weekend close
    (Fri 21:00 UTC -> Sun 22:00 UTC) and the daily 21:00-22:00 UTC break."""
    t = df["time"]
    gaps = t.diff().dt.total_seconds().div(60).fillna(0)
    mask = pd.Series(True, index=df.index)
    prev_hour = t.shift(1).dt.hour
    prev_dow = t.shift(1).dt.dayofweek
    # bars that follow the daily/weekly close legitimately gap
    mask &= ~(prev_hour == 20)          # 20:xx -> next bar after 21:00 break (US-DST EDT = 21:00 UTC; winter data would use 21)
    mask &= ~(prev_dow == 4)            # Friday close -> Sunday reopen
    return float(gaps[mask].max())


def backfill(symbol: str, dry_run: bool, target_end: datetime = TARGET_END) -> None:
    for tf in TFS:
        path = CACHE_DIR / f"is_{symbol}_{tf}.parquet"
        if not path.exists():
            raise SystemExit(f"FATAL: {path} missing — cannot extend a cache that doesn't exist")
        df = pd.read_parquet(path)
        df["time"] = pd.to_datetime(df["time"], utc=True)
        last = df["time"].max()
        print(f"[{tf}] cache ends {last}  rows={len(df)}")
        if last >= target_end:
            print(f"[{tf}] already covers target — skip")
            continue
        days_needed = (datetime.now(timezone.utc) - last).days + 2
        print(f"[{tf}] fetching {days_needed} days from OANDA …")
        fresh = fetch_candles(tf, days=days_needed, symbol=symbol)
        if fresh.empty:
            raise SystemExit(f"FATAL: OANDA returned no {tf} candles — check OANDA_API_KEY")
        fresh["time"] = pd.to_datetime(fresh["time"], utc=True)
        merged = merge_frames(df, fresh)
        new_end = merged["time"].max()
        print(f"[{tf}] merged -> {merged['time'].min()} .. {new_end}  rows={len(merged)}")
        if tf == "1m":
            gap = _max_market_gap_minutes(
                merged[merged["time"] >= pd.Timestamp("2026-04-01", tz="UTC")]
            )
            # 250-min threshold: legitimate holiday closures (e.g. Memorial Day 2026: 215 min observed)
            # are caught with buffer; genuine outages are > 4 h and fail loudly.
            if gap > 250:
                raise SystemExit(f"FATAL: {gap:.0f}-minute market-hours gap in 1m data")
            print(f"[{tf}] max market-hours gap (post 2026-04-01): {gap:.1f} min  OK")
        if not dry_run:
            merged.to_parquet(path, index=False)
            print(f"[{tf}] written")
        else:
            print(f"[{tf}] DRY-RUN — not written")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="XAU_USD")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--target", default=None,
                    help="extend the cache up to this UTC date (YYYY-MM-DD); "
                         "defaults to the historical TARGET_END")
    args = ap.parse_args()
    target = (datetime.strptime(args.target, "%Y-%m-%d").replace(tzinfo=timezone.utc)
              if args.target else TARGET_END)
    backfill(args.symbol, args.dry_run, target)
