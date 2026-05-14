from datetime import date

from App.HFT_Data_Collector.oanda_tick_lib import OandaTickFetcher

if __name__ == "__main__":
    API_KEY = "c17c606a2f01975df287675814550ddf-df0201ce97ccf3deeac8805e58b96def"
    INSTRUMENT = "XAU_USD"
    PRACTICE = True

    fetcher = OandaTickFetcher(
        api_key=API_KEY,
        instrument=INSTRUMENT,
        cache_dir="./Tick_Data_Generator/cache_data",
        request_timeout=10,
    )

    candle = fetcher.fetch_latest_candle(count=500)
    print(len(candle))
    print(candle)