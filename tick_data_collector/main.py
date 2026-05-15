import logging
import os
import sys
import time
from datetime import datetime, timezone, time as dtime

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv


# Allow running as a plain script from the project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from oanda_tick_lib import OandaTickFetcher, split_candles
from utils.db_utils import connect_db
from utils.check_market_timing import is_market_closed_utc

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
load_dotenv()

API_KEY    = os.getenv("OANDA_API_KEY")
INSTRUMENT = os.getenv("OANDA_INSTRUMENT", "XAU_USD")
PRACTICE   = os.getenv("OANDA_PRACTICE", "true").lower() != "false"
print(API_KEY , INSTRUMENT, PRACTICE)
POLL_SECONDS = 5



# ── Database insert ───────────────────────────────────────────────────────────
def insert_ticks(conn, ticks: list[dict], symbol: str) -> None:
    rows = [
        (
            datetime.fromisoformat(t["time"].replace("Z", "+00:00")),
            float(t["price"]),
            symbol,
        )
        for t in ticks
    ]

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO ltp (time, ltp, symbol) VALUES %s ON CONFLICT DO NOTHING",
            rows,
        )
    conn.commit()


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    if not API_KEY:
        log.error("Set OANDA_API_KEY in environment or .env")
        sys.exit(1)

    fetcher = OandaTickFetcher(
        api_key=API_KEY,
        instrument=INSTRUMENT,
        cache_dir="./Tick_Data_Generator/cache_data",
        practice=PRACTICE,
        request_timeout=10,
    )

    conn = connect_db()

    log.info(
        "Live writer started | instrument=%s | practice=%s | poll=%ds",
        INSTRUMENT, PRACTICE, POLL_SECONDS,
    )

    last_candle_time = None

    try:
        while True:
            loop_start = time.monotonic()

            try:
                # 🔒 Market closed check (weekends + 21:00–22:00 UTC daily maintenance)
                if not is_market_closed_utc():
                    log.info("Market Live -")

                    try:
                        candle = fetcher.fetch_latest_candle()
                    except Exception as exc:
                        log.error("Fetch candle failed: %s", exc)
                        candle = None

                    if candle is None:
                        log.warning("No complete candle available")

                    elif candle["time"] == last_candle_time:
                        log.debug("Duplicate candle — waiting")

                    else:
                        try:
                            ticks = split_candles(candle)
                        except Exception as exc:
                            log.error("split_candles failed: %s", exc)
                            ticks = None

                        if ticks:
                            try:
                                insert_ticks(conn, ticks, INSTRUMENT)

                                log.info(
                                    "Wrote candle=%s O=%s H=%s L=%s C=%s",
                                    candle["time"],
                                    candle["open"], candle["high"],
                                    candle["low"],  candle["close"],
                                )

                                last_candle_time = candle["time"]

                            except psycopg2.Error as exc:
                                log.error("DB insert failed: %s", exc)
                                try:
                                    conn.rollback()
                                except Exception:
                                    pass

                                if conn.closed:
                                    log.warning("Reconnecting DB...")
                                    try:
                                        conn = connect_db()
                                    except Exception as recon_exc:
                                        log.error("DB reconnect failed: %s", recon_exc)
                            except Exception:
                                log.exception("Unexpected DB insert error")

                else:
                    log.info("Market Closed — skipping API call")

            except Exception:
                log.exception("Unexpected error in main loop — continuing")

            # ⏱ Sleep control
            elapsed = time.monotonic() - loop_start
            time.sleep(max(0.0, POLL_SECONDS - elapsed))

    except KeyboardInterrupt:
        log.info("Stopped by user")

    finally:
        try:
            conn.close()
        except Exception:
            pass
        log.info("DB connection closed")


if __name__ == "__main__":
    main()