# QA report -- bars_cache_2y (XAU_USD, OANDA practice)

Built by `strategies/lab/tools/build_bars_cache_2y.py` on 2026-09-17 21:00 UTC. Every number below was computed by that script in this run.

## Sources and construction

* Production cache (OANDA-fetched, all TFs): `/Users/anil/Projects/Kronos/KronosStrategies/strategies/backtest/results/bars_cache` -- covers 2025-01-01 -> 2026-08-12. Untouched.
* Deep M1 history: `/Users/anil/Projects/Kronos/ClaudeTradingRD/m3_scalper/xau_m1_full.parquet` -- 2010-01-03 -> 2026-07-23, OHLC only (no volume/spread).
* Window built: **2024-08-15 -> 2026-08-12** (first M1 bar 2024-08-15 00:00:00+00:00, last 2026-08-12 23:32:00+00:00).
* Seam at **2025-01-01 00:00 UTC**: M1 before it comes from the deep file, from it on from production; higher TFs before it are DERIVED from that M1, from it on they are production's own OANDA-fetched bars.
* Prefix rows (< seam) carry `volume = NaN` and `spread = NaN` (the deep file has neither; the harness reads neither). Production's `spread` is a constant 0.3 until 2026-02-13 and NaN after -- it is not a real spread series.

## Missing tail: 2026-08-12 -> 2026-09-18 is ABSENT

The window actually wanted ends 2026-09-18. No local source has any bar after 2026-08-12 23:32:00+00:00 and this machine has no OANDA credentials, so nothing was fetched (no network access was attempted). Closing that ~5-week hole needs an OANDA fetch from the production box (its strategies container has OANDA credentials in its environment; `strategies/shared/tsdb_reader.py` holds the candle-fetch code). Any '24-month to 2026-09-18' backtest run on this cache is really 2024-08-15 -> 2026-08-12.

## 1. Splice-seam verification (deep M1 vs production M1, full overlap)

Overlap compared: 2025-01-01 00:00:00+00:00 -> 2026-07-23 04:07:00+00:00 (the deep file's last bar).

| metric | value |
|---|---|
| deep rows in overlap | 543,136 |
| production rows in overlap | 543,135 |
| common timestamps | 543,135 |
| only in deep file | 1 [Timestamp('2025-01-01 23:04:00+0000', tz='UTC')] |
| only in production | 0  |
| rows with any OHLC disagreement | **0** (per column {'open': 0, 'high': 0, 'low': 0, 'close': 0}) |
| rows exactly equal on OHLC | 543,135 (100.0000%) |

Rule applied: from the seam on, production only (byte-identical to the production cache). Timestamps only the deep file has inside the production window: 1 (['2025-01-01 23:04:00+00:00']) -- NOT added. That one bar is 2025-01-01 23:04 UTC (O 2625.07 H 2625.20 L 2625.07 C 2625.20), the first minute after the New Year reopen. Production's M1 starts at 23:05, and production's OANDA-fetched 15m/1h/4h/1d bars all OPEN at 2625.115 (the 23:05 open) with a 15m volume of 396 = exactly the 23:05 + 23:10 5m volumes, i.e. OANDA's own higher-TF candles do not contain that minute. Whether the 23:04 M1 candle is real cannot be settled offline; leaving it out keeps every TF consistent with M1 and the 2025+ part identical to what the published 19.5-month results used.

## 2. Derivation rule validation (production M1 -> TF vs production's OANDA TF bars)

Rule tested: left-labelled, left-closed buckets anchored at 00:00 UTC (5m/15m/1h/4h at 00,04,08,12,16,20 UTC / 1d at midnight UTC); a bar exists iff at least one M1 bar falls in the bucket; open=first, high=max, low=min, close=last, volume=sum. No special-casing of the daily break or the weekend is needed: the bucket that contains the reopen simply starts at its anchor (e.g. the Sunday 20:00 UTC 4h bar holds 22:00-24:00 UTC only, the Friday 20:00 UTC 4h bar holds 20:00-21:00 UTC in EDT / 20:00-22:00 UTC in EST) -- exactly what OANDA serves.

| TF | derived bars | production bars | common | OHLC exact | match rate | volume exact | only derived | only production |
|---|---|---|---|---|---|---|---|---|
| 5m | 112,983 | 112,982 | 112,982 | 112,982 | **100.0000%** | 112,982 | 1 ['2026-08-12 23:30:00+00:00'] | 0 [] |
| 15m | 37,685 | 37,684 | 37,684 | 37,684 | **100.0000%** | 37,684 | 1 ['2026-08-12 23:30:00+00:00'] | 0 [] |
| 1h | 9,427 | 9,426 | 9,426 | 9,426 | **100.0000%** | 9,426 | 1 ['2026-08-12 23:00:00+00:00'] | 0 [] |
| 4h | 2,549 | 2,548 | 2,548 | 2,548 | **100.0000%** | 2,548 | 1 ['2026-08-12 20:00:00+00:00'] | 0 [] |
| 1d | 498 | 497 | 497 | 497 | **100.0000%** | 497 | 1 ['2026-08-12 00:00:00+00:00'] | 0 [] |

The single 'only derived' bar per TF is the trailing incomplete bucket (production keeps only complete candles; M1 ends 2026-08-12 23:32 UTC). The prefix bars are therefore built with a rule that reproduces OANDA's own bars exactly over 19.5 months, but they remain DERIVED, not fetched.

| TF | derived prefix rows (2024-08-15 -> 2024-12-31) | production rows copied | total | arrow schema == production |
|---|---|---|---|---|
| 5m | 26,898 | 112,982 | 139,880 | True |
| 15m | 8,966 | 37,684 | 46,650 | True |
| 1h | 2,243 | 9,426 | 11,669 | True |
| 4h | 607 | 2,548 | 3,155 | True |
| 1d | 119 | 497 | 616 | True |
| 1m | 134,087 (deep file) | 563,500 | 697,587 | True |

### Cross-TF consistency of the FINAL files (each TF vs derivation from the final M1, whole window)

| TF | common | OHLC exact | only derived | only file |
|---|---|---|---|---|
| 5m | 139,880 | 139,880 (100.0000%) | 1 | 0 |
| 15m | 46,650 | 46,650 (100.0000%) | 1 | 0 |
| 1h | 11,669 | 11,669 (100.0000%) | 1 | 0 |
| 4h | 3,155 | 3,155 (100.0000%) | 1 | 0 |
| 1d | 616 | 616 (100.0000%) | 1 | 0 |

## 3. Integrity per file

| index | rows | first | last | tz | dtype_time | duplicates | non_monotonic | high<low | open_outside | close_outside | non_positive | nan_ohlc | nan_volume | nan_spread |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1m | 697587 | 2024-08-15 00:00:00+00:00 | 2026-08-12 23:32:00+00:00 | UTC | datetime64[ns, UTC] | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 134087 | 308129 |
| 5m | 139880 | 2024-08-15 00:00:00+00:00 | 2026-08-12 23:25:00+00:00 | UTC | datetime64[ns, UTC] | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 26898 | 61809 |
| 15m | 46650 | 2024-08-15 00:00:00+00:00 | 2026-08-12 23:15:00+00:00 | UTC | datetime64[ns, UTC] | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 8966 | 20604 |
| 1h | 11669 | 2024-08-15 00:00:00+00:00 | 2026-08-12 22:00:00+00:00 | UTC | datetime64[ns, UTC] | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2243 | 5153 |
| 4h | 3155 | 2024-08-15 00:00:00+00:00 | 2026-08-12 16:00:00+00:00 | UTC | datetime64[ns, UTC] | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 607 | 1392 |
| 1d | 616 | 2024-08-15 00:00:00+00:00 | 2026-08-11 00:00:00+00:00 | UTC | datetime64[ns, UTC] | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 119 | 271 |

Expected: duplicates 0, non_monotonic 0, high<low 0, open/close outside 0, non_positive 0, nan_ohlc 0. nan_volume/nan_spread are the prefix rows (and, for spread, production's own NaNs after 2026-02-13).

## 4. Trading calendar as observed in the data

Sessions are New York based: the daily close is 17:00 NY and the reopen 18:00 NY, so in UTC the daily break is 21:00-22:00 in EDT and 22:00-23:00 in EST (it is NOT fixed at 21:00 UTC). The first bar after every reopen (daily and Sunday) is at 18:04 or 18:05 NY -- there are never bars in the first 4 minutes after a reopen. The week runs Sunday 18:04 NY -> Friday 16:59 NY.

Observed session open times (NY, first bar of each 18:00->17:00 session, top 8):

| open_ny | sessions |
|---|---|
| 18:04 | 444 |
| 18:05 | 65 |
| 19:00 | 2 |
| 20:00 | 1 |
| 18:35 | 1 |

Observed session close times (NY, last bar of each session, top 8):

| close_ny | sessions |
|---|---|
| 16:59 | 489 |
| 14:29 | 10 |
| 16:58 | 4 |
| 13:29 | 2 |
| 18:59 | 2 |
| 12:59 | 2 |
| 14:44 | 1 |
| 13:44 | 1 |

The 4 empty minutes after each reopen are genuine OANDA behaviour: the production S5 sub-cache (`bars_cache/s5/XAU_USD/2026-08.parquet`) also starts its week at 2026-08-02 22:04:55 UTC.

Weekday sessions (Mon-Fri NY, 18:00 previous day -> 17:00) in the window: 521; observed: 513.

Weekday sessions with ZERO bars (8):

| session (NY date) | holiday |
|---|---|
| 2024-12-25 | Christmas Day |
| 2025-01-01 | New Year's Day |
| 2025-04-18 | Good Friday |
| 2025-11-28 | Black Friday |
| 2025-12-25 | Christmas Day |
| 2026-01-01 | New Year's Day |
| 2026-01-21 |  |
| 2026-04-03 | Good Friday |

Sessions with < 1000 M1 bars (a full session has ~1380; the last row is the end of the data):

| session (NY date) | count | open_ny | close_ny | holiday |
|---|---|---|---|---|
| 2025-07-04 | 116 | Thu 18:04 | Thu 19:59 | Independence Day |
| 2025-12-09 | 56 | Mon 18:04 | Mon 18:59 |  |
| 2025-12-24 | 56 | Tue 18:04 | Tue 18:59 | Christmas Eve |
| 2026-08-13 | 89 | Wed 18:04 | Wed 19:32 |  |

## 5. Gaps > 5 minutes in the combined M1

`missing_from_utc` is the first missing minute, `next` the first bar after the gap, `len_min` = next - prev bar, `deep_bars_in_span` = bars the deep M1 file holds inside the gap (blank when the gap lies outside its range). Categories (New York local time): `daily break` = same NY date, close 16:5x -> reopen 18:0x; `weekend` = Fri 16:5x -> Sun 18:0x; `weekend + holiday early close` = Fri holiday early close -> Sun 18:0x; `holiday` = span touches a US holiday, both edges at normal early-close/reopen times and no whole non-holiday weekday session swallowed; `ANOMALOUS (holiday-adjacent)` = touches a holiday but an edge is not a session boundary and/or a whole weekday session that OANDA normally trades is absent; `ANOMALOUS (weekend edge)` = Fri -> Sun but with a non-standard edge; `UNEXPLAINED` = everything else. Everything not `daily break`/`weekend`/`holiday` is enumerated individually.

| category | gaps |
|---|---|
| daily break | 392 |
| weekend | 96 |
| holiday | 15 |
| UNEXPLAINED | 4 |
| weekend + holiday early close | 3 |
| ANOMALOUS (holiday-adjacent) | 3 |
| ANOMALOUS (weekend edge) | 1 |

Total gaps > 5 min: 514. Daily-break lengths (min): {65.0: 388, 66.0: 4}; weekend lengths (min): {2885.0: 1, 2886.0: 1, 2945.0: 40, 2946.0: 52, 3005.0: 1, 3006.0: 1} (2885/2886 and 3005/3006 are the DST-transition weekends: the Sunday reopen is 60 min earlier/later in UTC).

### 5a. UNEXPLAINED gaps (enumerated individually)

| missing_from_utc | next | len_min | len_h | prev_ny | next_ny | deep_bars_in_span | note |
|---|---|---|---|---|---|---|---|
| 2025-04-24 22:05:00+00:00 | 2025-04-24 22:13:00+00:00 | 9 | 0.15 | 2025-04-24 18:04:00-04:00 | 2025-04-24 18:13:00-04:00 | 0 |  |
| 2025-12-09 00:00:00+00:00 | 2025-12-10 00:00:00+00:00 | 1441 | 24.02 | 2025-12-08 18:59:00-05:00 | 2025-12-09 19:00:00-05:00 | 0 | UTC-day-aligned hole |
| 2026-01-20 18:30:00+00:00 | 2026-01-22 00:00:00+00:00 | 1771 | 29.52 | 2026-01-20 13:29:00-05:00 | 2026-01-21 19:00:00-05:00 | 0 | UTC-day-aligned hole |
| 2026-03-27 16:46:00+00:00 | 2026-03-27 17:05:00+00:00 | 20 | 0.33 | 2026-03-27 12:45:00-04:00 | 2026-03-27 13:05:00-04:00 | 0 |  |

### 5b. ANOMALOUS gaps (holiday-adjacent or weekend-edge; enumerated individually)

| category | missing_from_utc | next | len_min | len_h | prev_ny | next_ny | deep_bars_in_span | note |
|---|---|---|---|---|---|---|---|---|
| ANOMALOUS (holiday-adjacent) | 2025-07-04 00:00:00+00:00 | 2025-07-06 22:05:00+00:00 | 4206 | 70.1 | 2025-07-03 19:59:00-04:00 | 2025-07-06 18:05:00-04:00 | 0 | 2025-07-03 Independence Day eve; 2025-07-04 Independence Day \| UTC-day-aligned hole \| whole weekday session(s) absent: 2025-07-04 \| edge(s) not at a normal session boundary |
| ANOMALOUS (holiday-adjacent) | 2025-11-27 18:30:00+00:00 | 2025-11-30 23:05:00+00:00 | 4596 | 76.6 | 2025-11-27 13:29:00-05:00 | 2025-11-30 18:05:00-05:00 | 0 | 2025-11-27 Thanksgiving; 2025-11-28 Black Friday \| whole weekday session(s) absent: 2025-11-28 |
| ANOMALOUS (weekend edge) | 2025-12-05 22:00:00+00:00 | 2025-12-07 23:35:00+00:00 | 2976 | 49.6 | 2025-12-05 16:59:00-05:00 | 2025-12-07 18:35:00-05:00 | 0 | Friday close 16:59 / Sunday reopen 18:35 NY |
| ANOMALOUS (holiday-adjacent) | 2025-12-24 00:00:00+00:00 | 2025-12-25 23:05:00+00:00 | 2826 | 47.1 | 2025-12-23 18:59:00-05:00 | 2025-12-25 18:05:00-05:00 | 0 | 2025-12-24 Christmas Eve; 2025-12-25 Christmas Day \| UTC-day-aligned hole \| whole weekday session(s) absent: 2025-12-24 \| edge(s) not at a normal session boundary |

### 5c. Holiday gaps and holiday early closes

| category | missing_from_utc | next | len_min | len_h | prev_ny | next_ny | note |
|---|---|---|---|---|---|---|---|
| holiday | 2024-09-02 18:30:00+00:00 | 2024-09-02 22:04:00+00:00 | 215 | 3.58 | 2024-09-02 14:29:00-04:00 | 2024-09-02 18:04:00-04:00 | 2024-09-02 Labor Day |
| holiday | 2024-11-28 19:30:00+00:00 | 2024-11-28 23:04:00+00:00 | 215 | 3.58 | 2024-11-28 14:29:00-05:00 | 2024-11-28 18:04:00-05:00 | 2024-11-28 Thanksgiving |
| weekend + holiday early close | 2024-11-29 19:45:00+00:00 | 2024-12-01 23:04:00+00:00 | 3080 | 51.33 | 2024-11-29 14:44:00-05:00 | 2024-12-01 18:04:00-05:00 | 2024-11-29 Black Friday |
| holiday | 2024-12-24 18:45:00+00:00 | 2024-12-25 23:04:00+00:00 | 1700 | 28.33 | 2024-12-24 13:44:00-05:00 | 2024-12-25 18:04:00-05:00 | 2024-12-24 Christmas Eve; 2024-12-25 Christmas Day |
| holiday | 2024-12-31 22:00:00+00:00 | 2025-01-01 23:05:00+00:00 | 1506 | 25.1 | 2024-12-31 16:59:00-05:00 | 2025-01-01 18:05:00-05:00 | 2024-12-31 New Year's Eve; 2025-01-01 New Year's Day |
| holiday | 2025-01-20 19:30:00+00:00 | 2025-01-20 23:05:00+00:00 | 216 | 3.6 | 2025-01-20 14:29:00-05:00 | 2025-01-20 18:05:00-05:00 | 2025-01-20 MLK Day |
| holiday | 2025-02-17 19:30:00+00:00 | 2025-02-17 23:05:00+00:00 | 216 | 3.6 | 2025-02-17 14:29:00-05:00 | 2025-02-17 18:05:00-05:00 | 2025-02-17 Presidents' Day |
| holiday | 2025-04-17 21:00:00+00:00 | 2025-04-20 22:05:00+00:00 | 4386 | 73.1 | 2025-04-17 16:59:00-04:00 | 2025-04-20 18:05:00-04:00 | 2025-04-18 Good Friday |
| holiday | 2025-05-26 18:30:00+00:00 | 2025-05-26 22:05:00+00:00 | 216 | 3.6 | 2025-05-26 14:29:00-04:00 | 2025-05-26 18:05:00-04:00 | 2025-05-26 Memorial Day |
| holiday | 2025-06-19 18:30:00+00:00 | 2025-06-19 22:05:00+00:00 | 216 | 3.6 | 2025-06-19 14:29:00-04:00 | 2025-06-19 18:05:00-04:00 | 2025-06-19 Juneteenth |
| holiday | 2025-09-01 18:30:00+00:00 | 2025-09-01 22:05:00+00:00 | 216 | 3.6 | 2025-09-01 14:29:00-04:00 | 2025-09-01 18:05:00-04:00 | 2025-09-01 Labor Day |
| holiday | 2025-12-31 22:00:00+00:00 | 2026-01-01 23:05:00+00:00 | 1506 | 25.1 | 2025-12-31 16:59:00-05:00 | 2026-01-01 18:05:00-05:00 | 2025-12-31 New Year's Eve; 2026-01-01 New Year's Day |
| holiday | 2026-01-19 19:30:00+00:00 | 2026-01-19 23:04:00+00:00 | 215 | 3.58 | 2026-01-19 14:29:00-05:00 | 2026-01-19 18:04:00-05:00 | 2026-01-19 MLK Day |
| holiday | 2026-02-16 19:30:00+00:00 | 2026-02-16 23:04:00+00:00 | 215 | 3.58 | 2026-02-16 14:29:00-05:00 | 2026-02-16 18:04:00-05:00 | 2026-02-16 Presidents' Day |
| holiday | 2026-04-02 21:00:00+00:00 | 2026-04-05 22:04:00+00:00 | 4385 | 73.08 | 2026-04-02 16:59:00-04:00 | 2026-04-05 18:04:00-04:00 | 2026-04-03 Good Friday |
| holiday | 2026-05-25 18:30:00+00:00 | 2026-05-25 22:04:00+00:00 | 215 | 3.58 | 2026-05-25 14:29:00-04:00 | 2026-05-25 18:04:00-04:00 | 2026-05-25 Memorial Day |
| weekend + holiday early close | 2026-06-19 17:00:00+00:00 | 2026-06-21 22:04:00+00:00 | 3185 | 53.08 | 2026-06-19 12:59:00-04:00 | 2026-06-21 18:04:00-04:00 | 2026-06-19 Juneteenth |
| weekend + holiday early close | 2026-07-03 17:00:00+00:00 | 2026-07-05 22:04:00+00:00 | 3185 | 53.08 | 2026-07-03 12:59:00-04:00 | 2026-07-05 18:04:00-04:00 | 2026-07-03 Independence Day eve |

### 5d. Daily breaks / weekends with a non-modal length

| category | missing_from_utc | next | len_min | len_h | prev_ny | next_ny | note |
|---|---|---|---|---|---|---|---|
| weekend | 2024-11-01 21:00:00+00:00 | 2024-11-03 23:04:00+00:00 | 3005 | 50.08 | 2024-11-01 16:59:00-04:00 | 2024-11-03 18:04:00-05:00 | DST transition weekend |
| weekend | 2025-03-07 22:00:00+00:00 | 2025-03-09 22:05:00+00:00 | 2886 | 48.1 | 2025-03-07 16:59:00-05:00 | 2025-03-09 18:05:00-04:00 | DST transition weekend |
| weekend | 2025-10-31 21:00:00+00:00 | 2025-11-02 23:05:00+00:00 | 3006 | 50.1 | 2025-10-31 16:59:00-04:00 | 2025-11-02 18:05:00-05:00 | DST transition weekend |
| weekend | 2026-03-06 22:00:00+00:00 | 2026-03-08 22:04:00+00:00 | 2885 | 48.08 | 2026-03-06 16:59:00-05:00 | 2026-03-08 18:04:00-04:00 | DST transition weekend |

## 6. Bars per weekday (UTC) per TF

| time | 1m | 5m | 15m | 1h | 4h | 1d |
|---|---|---|---|---|---|---|
| Sunday | 9810 | 2000 | 686 | 172 | 104 | 104 |
| Monday | 141895 | 28460 | 9488 | 2376 | 624 | 104 |
| Tuesday | 141148 | 28311 | 9437 | 2360 | 616 | 103 |
| Wednesday | 137624 | 27605 | 9202 | 2300 | 601 | 101 |
| Thursday | 139644 | 28010 | 9338 | 2336 | 613 | 104 |
| Friday | 127466 | 25494 | 8499 | 2125 | 597 | 100 |
| Saturday | 0 | 0 | 0 | 0 | 0 | 0 |

Sunday bars are the 22:00/23:00 UTC -> midnight UTC reopen slice; Saturday must be 0. The daily file has a Sunday bar for every week (the production cache already has this property: 1d bars are midnight-UTC buckets).

## 7. Bars per month (UTC) per TF

| month (UTC) | 1m | 5m | 15m | 1h | 4h | 1d | 1m per weekday-day |
|---|---|---|---|---|---|---|---|
| 2024-08 | 16393 | 3288 | 1096 | 274 | 74 | 14 | 745 |
| 2024-09 | 28855 | 5790 | 1930 | 483 | 131 | 26 | 1374 |
| 2024-10 | 31646 | 6348 | 2116 | 529 | 142 | 27 | 1376 |
| 2024-11 | 28493 | 5715 | 1905 | 477 | 129 | 25 | 1357 |
| 2024-12 | 28700 | 5757 | 1919 | 480 | 131 | 27 | 1305 |
| 2025-01 | 30115 | 6036 | 2014 | 504 | 137 | 27 | 1309 |
| 2025-02 | 27365 | 5485 | 1830 | 458 | 124 | 24 | 1368 |
| 2025-03 | 29006 | 5815 | 1940 | 485 | 131 | 26 | 1381 |
| 2025-04 | 28880 | 5791 | 1932 | 483 | 130 | 25 | 1313 |
| 2025-05 | 30000 | 6013 | 2006 | 502 | 136 | 26 | 1364 |
| 2025-06 | 28856 | 5784 | 1930 | 483 | 131 | 26 | 1374 |
| 2025-07 | 30381 | 6092 | 2032 | 508 | 136 | 26 | 1321 |
| 2025-08 | 28889 | 5791 | 1932 | 483 | 131 | 26 | 1376 |
| 2025-09 | 30117 | 6037 | 2014 | 504 | 136 | 26 | 1369 |
| 2025-10 | 31528 | 6320 | 2108 | 527 | 142 | 27 | 1371 |
| 2025-11 | 25984 | 5209 | 1738 | 435 | 118 | 24 | 1299 |
| 2025-12 | 27481 | 5509 | 1838 | 460 | 125 | 25 | 1195 |
| 2026-01 | 27098 | 5431 | 1812 | 454 | 124 | 25 | 1232 |
| 2026-02 | 27368 | 5488 | 1830 | 458 | 124 | 24 | 1368 |
| 2026-03 | 30369 | 6093 | 2032 | 508 | 137 | 27 | 1380 |
| 2026-04 | 28896 | 5796 | 1932 | 483 | 130 | 25 | 1313 |
| 2026-05 | 28746 | 5766 | 1922 | 481 | 131 | 26 | 1369 |
| 2026-06 | 30032 | 6024 | 2008 | 502 | 135 | 26 | 1365 |
| 2026-07 | 31292 | 6276 | 2092 | 523 | 141 | 27 | 1361 |
| 2026-08 | 11097 | 2226 | 742 | 185 | 49 | 9 | 528 |

`1m per weekday-day` = M1 bars / Mon-Fri calendar days in the month (full month ~1300-1400; 2024-08 and 2026-08 are partial months).

## SUMMARY

* Window: 2024-08-15 00:00:00+00:00 -> 2026-08-12 23:32:00+00:00 (UTC, tz-aware); wanted end 2026-09-18 is NOT covered (2026-08-12 -> 2026-09-18 needs an OANDA fetch from the production box).
* Rows: 1m 697,587, 5m 139,880, 15m 46,650, 1h 11,669, 4h 3,155, 1d 616
* Seam (deep M1 vs production M1, 2025-01-01 -> 2026-07-23): 543,135 common timestamps, 0 OHLC disagreements, 1 only-in-deep, 0 only-in-production.
* Derivation validation (M1 -> TF vs OANDA TF, 2025-01-01 -> 2026-08-12): 5m 112,982/112,982 = 100.00%, 15m 37,684/37,684 = 100.00%, 1h 9,426/9,426 = 100.00%, 4h 2,548/2,548 = 100.00%, 1d 497/497 = 100.00%. Prefix 2024-08-15 -> 2024-12-31 for 5m/15m/1h/4h/1d is DERIVED with this rule, not fetched.
* Integrity: all files clean (0 duplicates / non-monotonic / high<low / OHLC out of range / non-positive / NaN OHLC).
* Gaps > 5 min: 514 total = daily break 392, weekend 96, holiday 15, UNEXPLAINED 4, weekend + holiday early close 3, ANOMALOUS (holiday-adjacent) 3, ANOMALOUS (weekend edge) 1.
* Weekday sessions with zero bars: 8 -> 2024-12-25 (Christmas Day), 2025-01-01 (New Year's Day), 2025-04-18 (Good Friday), 2025-11-28 (Black Friday), 2025-12-25 (Christmas Day), 2026-01-01 (New Year's Day), 2026-01-21 (no holiday), 2026-04-03 (Good Friday).
* UNEXPLAINED gaps: 4 (54.0 h); ANOMALOUS gaps: 4 (243.4 h incl. the holiday parts). Each, with the deep file's bar count inside the span (0 = no local source has it):
    - 2025-04-24 22:05 -> 2025-04-24 22:13 UTC, 0.15 h, UNEXPLAINED; deep bars in span: 0
    - 2025-07-04 00:00 -> 2025-07-06 22:05 UTC, 70.1 h, ANOMALOUS (holiday-adjacent) [2025-07-03 Independence Day eve; 2025-07-04 Independence Day | UTC-day-aligned hole | whole weekday session(s) absent: 2025-07-04 | edge(s) not at a normal session boundary]; deep bars in span: 0
    - 2025-11-27 18:30 -> 2025-11-30 23:05 UTC, 76.6 h, ANOMALOUS (holiday-adjacent) [2025-11-27 Thanksgiving; 2025-11-28 Black Friday | whole weekday session(s) absent: 2025-11-28]; deep bars in span: 0
    - 2025-12-05 22:00 -> 2025-12-07 23:35 UTC, 49.6 h, ANOMALOUS (weekend edge) [Friday close 16:59 / Sunday reopen 18:35 NY]; deep bars in span: 0
    - 2025-12-09 00:00 -> 2025-12-10 00:00 UTC, 24.02 h, UNEXPLAINED [UTC-day-aligned hole]; deep bars in span: 0
    - 2025-12-24 00:00 -> 2025-12-25 23:05 UTC, 47.1 h, ANOMALOUS (holiday-adjacent) [2025-12-24 Christmas Eve; 2025-12-25 Christmas Day | UTC-day-aligned hole | whole weekday session(s) absent: 2025-12-24 | edge(s) not at a normal session boundary]; deep bars in span: 0
    - 2026-01-20 18:30 -> 2026-01-22 00:00 UTC, 29.52 h, UNEXPLAINED [UTC-day-aligned hole]; deep bars in span: 0
    - 2026-03-27 16:46 -> 2026-03-27 17:05 UTC, 0.33 h, UNEXPLAINED; deep bars in span: 0

### Trust verdict

Fit for a 24-month backtest of 2024-08-15 -> 2026-08-12 (~34 days of M1/M5 warm-up in front of a 2024-09-18 start). The M1 is one continuous OANDA-practice series with an exact seam and clean structure; the higher-TF prefix is built with a rule that reproduces OANDA's own bars 100% over 19.5 months. Caveats: (1) the last five weeks the user wants (to 2026-09-18) do not exist locally; (2) the whole-session holes listed above sit in the PRODUCTION part and are absent from every local source (the vault's XAUUSD Data Inventory note states OANDA serves 2025-12-09 and 2026-01-21 in full, so at least those are fetch holes, not closures) -- a strategy is simply not traded on those days and a position open into one of them sees a jump at the next bar; the holes are already inside the published 19.5-month results, so nothing changes for comparability, but they should be patched by a fetch when credentials are available; (3) prefix bars carry no volume; (4) the daily break is NY-17:00 anchored, not a fixed 21:00 UTC, so session logic keyed to fixed UTC hours drifts by an hour across DST; (5) the daily file has a Sunday bar every week (a 2-hour midnight-UTC bucket), inherited from production.


## Addendum 2026-09-18 — tail merged (2026-08-12 → 2026-09-17 20:55 UTC)

Fetched from the OANDA practice API on 2026-09-18 (`lab/tools/fetch_oanda_tail.py`, price=MBA,
dailyAlignment=0 / alignmentTimezone=UTC, complete candles only) and merged into these files.
Overlap day 2026-08-12 agreed exactly on every timeframe before the merge; all integrity checks
re-run on the merged files (0 duplicates, monotonic, 0 high<low, 0 OHLC out of range, 0 NaN).
Tail 5m derived from tail M1 vs fetched 5m: 7397 bars, 0 mismatches.

| TF | rows before | tail rows added | rows after | overlap rows / mismatches | last bar |
|---|---|---|---|---|---|
| 1m | 697,587 | 35,533 | 733,120 | 1349 / 0 | 2026-09-17 20:55:00+00:00 |
| 5m | 139,880 | 7,127 | 147,007 | 270 / 0 | 2026-09-17 20:50:00+00:00 |
| 15m | 46,650 | 2,375 | 49,025 | 90 / 0 | 2026-09-17 20:30:00+00:00 |
| 1h | 11,669 | 594 | 12,263 | 22 / 0 | 2026-09-17 19:00:00+00:00 |
| 4h | 3,155 | 161 | 3,316 | 5 / 0 | 2026-09-17 16:00:00+00:00 |
| 1d | 616 | 31 | 647 | 0 / 0 | 2026-09-16 00:00:00+00:00 |

The "missing tail" caveat above is therefore closed: the cache now ends 2026-09-17. `spread`
in the tail is ask−bid close (median 0.55 pts over the period).
