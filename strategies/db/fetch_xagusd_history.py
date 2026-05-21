"""
fetch_xagusd_history.py
-----------------------
One-shot: pull XAG_USD S5 candles from OANDA into `.history_data/XAG_USD/`
day-by-day for the same window as XAU_USD (2025-01-01 → today).

After this finishes, run:
  python strategies/db/backfill_ltp_from_history.py --symbol XAG_USD --commit
to load the cached JSON into the `ltp` hypertable.
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

# Force UTF-8 stdout so the OANDA lib's log messages (which contain a
# UTF-8 arrow) don't blow up on Windows' cp1252 default console encoding.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

REPO_ROOT = Path(__file__).resolve().parents[2]
COLLECTOR_DIR = REPO_ROOT / "tick_data_collector"
sys.path.insert(0, str(COLLECTOR_DIR))

from oanda_tick_lib import OandaTickFetcher

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

load_dotenv(COLLECTOR_DIR / ".env")
load_dotenv(REPO_ROOT / ".env")

API_KEY = os.getenv("OANDA_API_KEY")
if not API_KEY:
    print("FATAL: OANDA_API_KEY not set", file=sys.stderr)
    sys.exit(1)

START = "2025-01-01"
END = date.today().isoformat()
CACHE_DIR = REPO_ROOT / ".history_data"

print(f"[FETCH] XAG_USD  {START} -> {END}  cache={CACHE_DIR}")

fetcher = OandaTickFetcher(
    api_key=API_KEY,
    instrument="XAG_USD",
    cache_dir=str(CACHE_DIR),
    practice=True,
    request_timeout=30,
)

summary = fetcher.fetch_range(START, END)
saved = sum(1 for v in summary.values() if v == "saved")
empty = sum(1 for v in summary.values() if v == "empty")
errors = sum(1 for v in summary.values() if v.startswith("error"))
print(f"[DONE] saved={saved} empty={empty} errors={errors}")
