"""
plot_xauusd_1m.py
─────────────────
Fetch 30 days of XAU/USD S5 candles from OANDA, synthesise tick data,
resample to 1-minute candles, and display an interactive chart.

Steps
  1. OandaTickFetcher fetches + caches each day (skips days already cached).
  2. All cached tick JSON files are loaded and flattened into one DataFrame.
  3. build_candles() resamples ticks → 1-minute OHLCV.
  4. lightweight_charts renders the chart.

Run from the project root:
    python oanda_tick_lib/examples/plot_xauusd_1m.py
"""

import os
import sys

# ── Make project root importable when running as a plain script ──────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pandas as pd
from lightweight_charts import Chart

from App.HFT_Data_Collector.oanda_tick_lib import OandaTickFetcher
from Extras_Script.Visulize_Tick_Data.utils.db_utils import build_candles, format_chart

# ── Config ────────────────────────────────────────────────────────────────────
API_KEY = "c17c606a2f01975df287675814550ddf-df0201ce97ccf3deeac8805e58b96def"  # ← replace with your OANDA API key
INSTRUMENT = "XAU_USD"
CACHE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "Tick_Data_Generator", "cache_data"
)
START = "2026-04-01"
END = "2026-05-03"
TF = "1m"


def load_ticks(fetcher: OandaTickFetcher, start: str, end: str) -> pd.DataFrame:
    """Load (and fetch if missing) cached ticks for a date range."""
    all_ticks: list[dict] = []

    for d in pd.date_range(start=start, end=end):
        day_data = fetcher.load_day(d)
        if day_data is None:
            print(f"[INFO] {d.date()} not in cache — skipping (run fetch_range first)")
            continue
        for group in day_data:
            all_ticks.extend(group)

    if not all_ticks:
        print("[ERROR] No tick data loaded. Check your date range and cache.")
        sys.exit(1)

    df = pd.DataFrame(all_ticks)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df = df.dropna(subset=["price"]).sort_values("time").reset_index(drop=True)

    print(
        f"[TICKS] {len(df):,} ticks loaded  "
        f"({df['time'].iloc[0].strftime('%Y-%m-%d')}  →  "
        f"{df['time'].iloc[-1].strftime('%Y-%m-%d')})"
    )
    return df


def main() -> None:
    print(f"\n{'═' * 55}")
    print(f"  {INSTRUMENT}  |  {TF} Chart  |  {START} → {END}")
    print(f"{'═' * 55}\n")

    fetcher = OandaTickFetcher(
        api_key=API_KEY,
        instrument=INSTRUMENT,
        cache_dir=CACHE_DIR,
        practice=True,  # set False for a live account
    )

    # 1. Fetch any missing days (cached days are skipped automatically)
    fetcher.fetch_range(START, END)

    # 2. Load ticks from cache → flat DataFrame
    ticks = load_ticks(fetcher, START, END)

    # 3. Resample → 1-minute OHLCV
    candles = build_candles(ticks, TF)

    # 4. Build and show chart
    chart = Chart(toolbox=True, title=f"{INSTRUMENT} – {TF}")
    format_chart(chart)
    chart.topbar.textbox(
        "info",
        f"{INSTRUMENT}  ·  {TF}  ·  {START} → {END}  ·  {len(candles):,} candles",
    )
    chart.set(candles.drop(columns=["volume"]))

    print(f"\nChart open — close the window to exit.\n")
    chart.show(block=True)


if __name__ == "__main__":
    main()
