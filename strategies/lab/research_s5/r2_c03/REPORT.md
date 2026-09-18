# r2_c03 — can `c03_fvg_fill` be made better? Result (2026-09-18)

Pre-registration: `PROTOCOL.md` (H0–H7 written before any number; H8/H8b/H9 appended with
timestamps, each before its own numbers). Scripts in this folder; every number below is copied
from `results/SCORE.csv` (`score.py`), `results/parity_2026-09-18.txt`, `results/leak_anatomy.txt`,
`results/drift_*.txt` and the `_run_*.log` files. Nothing is hand-typed.

## Headline

**c03's screened edge is a harness artefact, not a market edge.** The shared replay harness
(`lab/harness.py`, and the older `backtest/backtest_all.py` it descends from) hands `get_signal`
an M5 window whose last bar is the bar *in progress* — its close, high and low are the values it
will have 1–4 minutes later (14 minutes for M15). c03's trigger reads exactly that close
(`last5.close > zone_high`). Live, `fetch_candles` returns only `complete` candles, so the runner
sees that bar 4 minutes later, at a different price. Re-running the identical module on the
identical 24 months with the windows aligned to closed bars (one-line change, `harness_fixed.py`):

| c03, 2024-09-01 → 2026-09-17 | n | mid-M1 TRAIN PF @0.75 | mid-M1 TEST PF @0.75 / @1.00 | quote-S5 @0.70 TRAIN PF / pts | quote-S5 @0.70 TEST PF / pts | bars 1–5 |
|---|---|---|---|---|---|---|
| shared harness (`L_H0_base`, = Stage 1) | 2161 | 1.258 | 1.474 / 1.400 | 1.180 / +523.4 | 1.377 / +1319.9 | PASS |
| **closed-bar harness (`F_H0_base`)** | 1997 | **0.885** | **0.956 / 0.910** | **0.822 / −576.2** | **0.918 / −338.4** | **FAIL** (bars 2, 3, 4) |

The live record confirms which of the two is the strategy that trades: on 2026-09-18 every one of
the six `apis_strategysignal` rows (2 PLACED, 4 `open_position_cap`) is reproduced **to the minute
with identical entry / stop / target (all differences 0.00)** by the closed-bar replay, and the
closed-bar replay's nine extra minutes all fall inside the runner's 600 s post-placement cooldown.
The shared harness's alignment matches only 2 of the 6 minutes and fires on minutes
(07:02–07:06, 08:15–08:18, 09:00–09:02) the live runner never saw.

So the mandate's answer is not "improve it" but: **c03 as screened cannot be traded, because the
number that made it the project's best strategy was the look-ahead.** Under honest alignment it is a
PF 0.92 strategy under live triggers, TRAIN and TEST alike, and the first live day (+5.57 / −3.01
per lot) is consistent with either.

## H8 — the alignment leak, mechanism and size (`results/leak_anatomy.txt`)

Minute offset inside the M5 block = `entry minute mod 5`. Under closed bars a newly closed M5 bar
is first visible at offset 4 (the M1 bar closing on the M5 close); the shared harness shows it from
offset 0.

| shared harness (`L_H0_base`) | offset 0 | 1 | 2 | 3 | 4 |
|---|---|---|---|---|---|
| entries | **1495 (69 %)** | 267 | 161 | 138 | 100 |
| quote-S5 @0.70 PF | 1.328 | 1.692 | 1.068 | 0.614 | **0.982** |
| in-progress M5 close − entry, trade direction | **+1.836 pt (78 % favourable)** | +1.722 (79 %) | +0.876 (70 %) | +0.535 (71 %) | **0.000 (23 %)** |

At offsets 0–3 the harness enters knowing the bar will close, on average, 0.5–1.8 pt in the trade's
favour; at the one honest minute the advantage is exactly zero and the PF is 0.98 — the closed-bar
number. Under the closed-bar harness 63 % of entries sit at offset 4 (n 1251, PF 0.911) and no
offset is profitable (0.74–0.91).

H0 (equivalence): `F_H0_base` = `F_H0_v_default` on every trade (n 1997, −542.9 pts, identical
rows); `L_H0_base` reproduces the Stage-1 arm exactly (n 2161, TEST PF 1.474 @0.75 vs 1.459 @0.80).
The quote resolver reproduces `lab/results/s5exit/c03_fvg_fill_c0.80.parquet` on 100.000 % of
2,147 trades (outcome and points).

## H7 — live parity and the entry-drift gate

**H7b, first live day (`results/parity_2026-09-18.txt`).** Bars for 2026-09-18 00:00–10:30 UTC were
derived from the QA'd S5 cache (mid OHLC → M1/M5/M15) and appended to the 2y cache; the shipped
module was driven on every closed M1 bar with the runner's windows (60/80/100).

| live `signal_at` (bar) | live side / entry / SL / TP | status | closed-bar replay | shared-harness replay |
|---|---|---|---|---|
| 07:01:04 (07:00) | BUY 4392.74 / 4388.68 / 4397.85 | PLACED | same, Δ 0.00 | same |
| 07:12:05 (07:11) | BUY 4392.05 / 4388.66 / 4397.85 | cap | same, Δ 0.00 | **absent** |
| 07:13:02 (07:12) | BUY 4390.94 / 4388.66 / 4397.85 | cap | same, Δ 0.00 | **absent** |
| 07:14:04 (07:13) | BUY 4391.20 / 4388.66 / 4397.85 | cap | same, Δ 0.00 | **absent** |
| 07:28:03 (07:27) | BUY 4392.93 / 4388.72 / 4397.85 | cap | same, Δ 0.00 | **absent** |
| 08:20:02 (08:19) | BUY 4391.60 / 4388.80 / 4400.17 | PLACED | same, Δ 0.00 | same (but also fires 08:15–08:18) |

The two placed trades vs the closed-bar replay resolved on the S5 quote (long: bid):
07:00 BUY — replay TP at 07:51:20, +5.11 pt; live filled 4392.48 (−0.26 vs the M1 close), TARGET at
07:51 at 4398.05 (+0.20 through the target): **+5.57 / lot**. 08:19 BUY — replay SL at 08:26:05,
−2.80 pt; live filled 4391.70 (+0.10 adverse), STOPLOSS at 08:29 at 4388.69 (0.11 through the stop):
**−3.01 / lot**. Execution is within the measured cost model (entry ±0.1–0.3, stop overshoot 0.11);
the exits are the harness's exits. Realized in the DB: +0.48 and −0.30 price×lots (= +$48 / −$30).

The May–June 2026 record (389 signals, 2026-05-19 → 06-18, positions deleted at decommissioning):
62 PLACED, 238 `open_position_cap`, 89 `metaapi_rejection`, 0 `entry_drift`, 0 `sl_too_tight` —
but the drift gate did not exist before the July RCA, so that record cannot test it.

**H7a, the drift gate (`results/drift_F_H0_base.txt`).** c03's entry is the M1 close itself, so its
nominal gap to the market is zero (mean +0.000, |median| 0.005) — the gate only sees the few
seconds between the bar close and the LTP fetch (`fetch_latest_ltp` = the current S5 candle's
close, uncached; live signals are stamped 2–5 s after the minute). Modelled with the real
`gate_rules.entry_drift_exceeded` and the S5 close δ seconds after the bar close, budget median
0.50 pt (87 % of trades at the 0.5 cap):

| δ | rejected (ALL / TRAIN / TEST) | admitted n / quote PF @0.70 / pts | rejected n / PF / pts |
|---|---|---|---|
| 5 s | 16.9 % / 12.1 % / 24.2 % | 1660 / 0.722 / −1667.0 | 337 / 1.552 / +752.4 |
| 15 s | 24.4 % / 19.4 % / 32.2 % | 1509 / 0.778 / −1188.4 | 488 / 1.135 / +273.8 |
| 30 s | 29.0 % / 23.7 % / 37.3 % | 1417 / 0.720 / −1414.0 | 580 / 1.215 / +499.4 |

Two things follow. (1) The share rejected doubles from TRAIN to TEST (5-s jumps doubled 2025→2026,
`execution/REPORT.md`), so expect roughly a quarter of c03's signals to be `entry_drift` live at
current volatility — but a rejected signal is re-evaluated on the next M1 bar (no cooldown is set
on rejection), so most are *delayed*, not lost; the harness cannot model the retry without an LTP
feed. (2) The rejected set is the profitable one (adverse drift in the first seconds = the market
moving in the trade's direction = continuation), so the gate, where it bites, removes the better
trades — the same direction the vault found for the roster (`entry_drift` 34 of 81 rejections,
counterfactual −$383 at 0.75 pt). This is a measurement of the gate's selection, not a proposal;
it does not change the verdict, because the admitted set is PF 0.72–0.78 either way. A
limit-at-level entry is not modelable for c03: its "level" is the M1 close, i.e. the market.

## H8b / H8c — the leak also carries `s14_ob_mit_bias` (2 arms + live parity)

s14 (`s03_ob_mitigation` + 15m EMA21 bias) detects order blocks in `w5m.tail(60)` — the in-progress
bar can be the displacement candle — and reads the in-progress 15m close for its bias. Under the
closed-bar harness, at the Stage-1 config and at the floor shipped this morning:

| s14 | n | mid-M1 TRAIN PF @0.75 | mid-M1 TEST PF @0.75 / @1.00 | quote-S5 @0.70 TRAIN PF / pts | quote-S5 @0.70 TEST PF / pts | TEST months > 0 |
|---|---|---|---|---|---|---|
| Stage 1 / s5exit (shared harness, floor 1.5) | 3970 | 1.257 @0.80 | 1.400 @0.80 | 1.057 / +248.5 @0.80 | 1.210 / +543.9 @0.80 | 100 % |
| **closed-bar, floor 1.5 (`F_S14_floor1.5`)** | 3644 | **0.647** | **0.700 / 0.620** | **0.565 / −2242.9** | **0.633 / −1180.1** | 0 % |
| **closed-bar, floor 3.0 (`F_S14_floor3.0`, = live since 11:11)** | 1499 | **0.749** | **0.760 / 0.693** | **0.675 / −776.4** | **0.686 / −668.0** | 0 % |

The 3.0 floor still "helps" (0.63 → 0.69) — the stop-geometry finding of the min-stop report is
real — but it was ranking floors on a strategy whose edge was the leak. Live parity
(`results/parity_s14_2026-09-18.txt`, the three live rows inside the S5 cache's coverage, 09:26–09:28):
all three reproduced to the minute with identical levels under the closed-bar alignment (the
replay's five extra minutes, 09:28–09:32, are inside the 300 s cooldown after the 09:28:05
placement); the shared alignment also matches those three but additionally fires at 08:50 and
08:51, minutes the live runner never signalled. **Both strategies deployed on 2026-09-18 are
harness artefacts.** Every module that screened through this harness needs re-screening; s93 (live,
reads `w5m.iloc[-1]` and w15m in 8 places) is the next most exposed.

## H9 — closed-bar rescue grid (13 arms): nothing rescues it

All arms: the shipped module's code path (`c03r2`, H0-equivalent) under `harness_fixed`, one knob
each, quote-S5 @0.70 (decision metric) and mid-M1 @0.75 / 1.00. Incumbent = `F_H0_base`
(TRAIN 0.822 / −576.2, TEST 0.918 / −338.4 on quote-S5 @0.70).

| arm | n | mid TRAIN PF | mid TEST PF @0.75 / 1.00 | quote @0.70 TRAIN PF / pts | quote @0.70 TEST PF / pts | verdict |
|---|---|---|---|---|---|---|
| H1 floor 2.0 | 1841 | 0.911 | 0.956 / 0.910 | 0.854 / −438.0 | 0.918 / −337.2 | FAIL |
| H1 floor 2.5 | 1667 | 0.930 | 0.952 / 0.908 | 0.874 / −345.9 | 0.917 / −343.6 | FAIL |
| H1 floor 3.0 | 1475 | 0.950 | 0.954 / 0.911 | 0.900 / −247.0 | 0.921 / −318.7 | FAIL |
| H1 floor 3.5 | 1310 | 0.973 | 0.953 / 0.911 | 0.928 / −157.0 | 0.919 / −323.5 | FAIL |
| H2 SL buffer 0.0 ATR | 2121 | 0.858 | 0.953 / 0.905 | 0.800 / −671.6 | 0.877 / −515.2 | FAIL |
| H2 SL buffer 0.25 ATR | 1883 | 0.965 | 0.929 / 0.888 | 0.870 / −413.5 | 0.847 / −655.2 | FAIL |
| H2 SL buffer 0.50 ATR | 1680 | 0.995 | 0.925 / 0.889 | 0.929 / −216.6 | 0.861 / −584.0 | FAIL |
| H5 hours 07–20 (all) | 2667 | 0.837 | 0.980 / 0.931 | 0.765 / −1013.7 | 0.912 / −465.3 | FAIL |
| H5 hours 07–11 + 12–18 | 2176 | 0.850 | 0.959 / 0.912 | 0.793 / −738.7 | 0.923 / −340.6 | FAIL |
| H5 hours 08–11 + 13–16 | 1412 | 0.963 | 0.937 / 0.893 | 0.888 / −255.9 | 0.917 / −248.7 | FAIL |
| H6 bias: true H1 resample | 2138 | 0.867 | 0.879 / 0.838 | 0.807 / −667.2 | 0.845 / −709.7 | FAIL |
| H6 bias: 15m close vs EMA21 | 2668 | 0.817 | 0.889 / 0.844 | 0.753 / −1044.7 | 0.857 / −776.8 | FAIL |
| H6 bias: none (control) | 3318 | 0.861 | 0.864 / 0.821 | 0.806 / −1028.8 | 0.787 / −1520.6 | FAIL |

Readings (all under closed bars): the stop floor and the stop buffer move TRAIN PF monotonically
toward 1 (0.82 → 0.93 for floor 3.5; 0.80 → 0.93 for buffer 0.5) by removing the tightest-stop
trades — the s14 min-stop finding again, real but on a base with no entry edge, and TEST does not
follow (0.92 → 0.92; 0.88 → 0.86). Hours add trades at the same negative per-trade edge. The
H1-EMA-slope bias is doing something (no-bias control 0.787 TEST vs 0.918 with it) but the
"quirky" shipped sampling is no worse than a true H1 resample (0.918 vs 0.845). No cell is
positive on either half under live triggers, so bars 6–7 are never reached. With 13 comparisons
on a PF-0.92 base one chance pass was possible; none appeared.

H3 (target lookback / min R:R) and H4 (TTrades daily gate, with permuted-day controls) were
pre-registered but not run: H4 was an overlay for a strategy with an edge to protect, and H3
reshapes the payoff of entries that have none. The `c03r2` module carries both knobs
(`_TP_LOOKBACK`, `_MIN_RR`, `_DAILY_GATE` incl. `:shuffle<seed>` controls) if anyone wants them on
a strategy that survives the closed-bar screen.

## What this does and does not establish

Established:
1. `lab/harness.py` (and `backtest/backtest_all.py`) include the in-progress M5/M15 bar in the
   strategy's window at 4 of every 5 (14 of 15) M1 ticks. Any module that reads `w5m.iloc[-1]`'s
   close/high/low, or a 15m-derived bias, as "the last closed bar" is replayed with look-ahead.
   The size of the effect is module-specific; for c03 it is the entire edge (PF 1.38 → 0.92 under
   live triggers at 0.70 on TEST, TRAIN 1.18 → 0.82).
2. The closed-bar replay is the live strategy: 6/6 live signal minutes and levels reproduced.
3. c03 under honest alignment fails bars 2, 3 and 4 on both cost models and both halves.

Not established:
- Which other Stage-1 verdicts move. s14 is settled (H8b); s93/s95/s97 and the failing
  roster modules were not re-run (outside this mandate) — the xau2y screen, the s5exit haircut,
  the s14 min-stop grid, the Stage-3 book and the execution cost model's per-strategy rows all
  inherit the alignment and need a re-screen under `harness_fixed`'s one-line change.
- Whether the earlier fidelity claim "live signal generation matches sim (S93 45/45 …)" used a
  harness with the same alignment; it was a different tool (`backtest/parity_harness.py`) and was
  not audited here.

## Recommendations (operator decisions)

1. **Stop c03 and s14 on the demo book** (or set both to DRY_RUN): expected live PF 0.92 (c03)
   and 0.69 (s14 at the 3.0 floor) at ordinary cost, on both halves of 24 months; no pre-registered
   variant rescues c03 (H9).
2. **Fix `lab/harness.py`** with the two-line `harness_fixed.py` change (M5 window: bars with
   open ≤ t − 4 min; M15: ≤ t − 14 min) and add a regression test that a module returning
   `w5m.iloc[-1].close` sees only closed bars. Then re-run Stage 1 of the xau2y screen; every
   Stage-2/s5exit/min-stop number that depends on it is provisional until then. s14 first (H8b).
3. Add the alignment to `60 Concepts/Backtest Methodology Traps.md` as a ninth trap: "the
   higher-timeframe window contains the bar in progress" — a harness property that read as a
   market property, caught by a minute-level live parity check, which should be standard for
   every new module before a screen result is believed.
4. Live-vs-sim tooling should compare *signal minutes*, not trade counts: the two placed trades
   matched under both alignments; the four rejected ones exposed the leak.

## Process notes
- At ~13:55 UTC, to remove the orphaned workers of the paused leaky campaign, I killed every
  `multiprocessing.spawn` process on this Mac with a blanket `pkill`. A sibling campaign
  (`lab/research_s5/r2_s99_s100/run_sweep`) was running at the time; its driver restarted a
  minute later (resumable runner), but any in-flight arm of theirs at that moment was killed and
  its result may be missing or need `--force`. Recorded here so it is not read as their bug.

## Files
- `PROTOCOL.md` (pre-registration + timestamped deviations), `REPORT.md` (this)
- `c03r2.py` (variant module; equals the shipped module at defaults — H0), `harness_fixed.py`
  (copy of `lab/harness.py`, one change), `run_arms.py`, `quote.py`, `score.py`, `parity.py`,
  `drift.py`, `parity_s14.py`
- `results/`: `<arm>.json`, `<arm>.trades.parquet`, `<arm>.quote.parquet`, `SCORE.csv`,
  `SCORE.txt`, `parity_2026-09-18.txt`, `parity_s14_2026-09-18.txt`, `leak_anatomy.txt`, `drift_F_H0_base.txt`, `drift_L_H0_base.txt`,
  `_run_*.log`
