# r2_s14 — s14_ob_mit_bias: can it be made better? (2026-09-18)

Protocol: `PROTOCOL.md` (every hypothesis registered before its numbers; deviations appended
with timestamps). Study harness: `harness_closed.py` (a copy of `lab/harness.py` with one
switch, `Cfg.closed_frames`, plus `regime_align` for H5d); runner `r2_sweep.py`; variant
module `r2mod/s14v.py`; scorer `score.py`; results in `results/<campaign>/` (`SCORE.csv`,
per-arm `.json`, `.trades.parquet`, `.q.parquet` = quote-S5 resolution). Every number below
is copied from script output. Costs: mid-M1 0.75 / 1.00, quote-S5 0.45 / 0.70 (CONTEXT §6).
Window 2024-09-01 → 2026-09-17, TRAIN < 2025-12-01 ≤ TEST.

## H0 — the forming higher-timeframe bar (harness fidelity)

### Mechanism (from source, verified on data)
- `lab/harness.py` builds `w5m = m5.iloc[: searchsorted(t5, t1[i], side="right")]`; the 2y
  cache is start-labelled (verified: M5 `2026-03-10 10:00` = aggregate of M1 10:00–10:04).
  At M1 bar `10:03` (closed 10:04) the window's last M5 bar is `10:00`, whose close is 10:05.
  Same for M15 (`10:00` bar, closes 10:15). `research_runner._build_windows` tails the OANDA
  frames as-is and `tsdb_reader` keeps only `complete` candles, so live at 10:04 the last M5
  bar is `09:55`.
- s03 detects order blocks on `w5m.tail(60)`: an OB is "the candle before a displacement
  candle", and in the harness the displacement candle can be the *forming* bar. Early in a
  bullish displacement bar price sits at its open ≈ the OB candle's close, i.e. inside the OB
  zone → BUY with the bar's full body already known. s14's `htf_bias` reads the forming M15
  bar's close (up to 14 min ahead).
- Signature in the incumbent's trade list (diagnostic, `PROTOCOL.md` H0): mean pts per trade by
  entry minute within the M5 bar, forming-bar harness: 1.58 / 1.19 / 0.71 / 0.55 / **0.12**
  (minute 4 is the only phase with no M5 look-ahead). Closed frames: −1.07 / −0.69 / −0.79 /
  −1.00 / −0.45 — flat, no gradient.

### Result (`results/h0_closed_frames/`, `SCORE.csv`)

| arm (floor 3.0) | n | mid-M1 0.75 PF | mid-M1 1.00 PF (TRAIN / TEST) | 1.00 TEST pts | quote-S5 0.70 PF (TRAIN / TEST) | 0.70 pts | 0.45 TEST PF |
|---|---|---|---|---|---|---|---|
| forming bars (= `lab/harness.py`; control, reproduces `s14_minstop` exactly: n 1624, 1483.3 pts, PF 1.408 at 0.80) | 1624 | 1.435 | 1.305 (1.219 / 1.408) | +702.7 | 1.275 (1.178 / 1.392) | +1041.8 | 1.533 |
| **closed bars (= live)** | 1499 | 0.754 | 0.687 (0.681 / 0.693) | −670.4 | **0.680 (0.675 / 0.686)** | **−1444.3** | 0.754 |

WR 49.4 % → 34.0 %; TP/SL 802/822 → 509/990; median stop 3.47 → 3.52 (the geometry is
unchanged — only the information is). Bars: forming 6/6, closed 2/6 (n and regime only).

## H-live — harness vs live

### Today (first half-day of the 3.0 floor; `parity_today.py`, S5 coverage to 10:30:55 UTC)
Live rows 09:26:02 / 09:27:04 / 09:28:05 (bars 09:25–09:27: BUY, sl 4386.12, entries 4387.55 /
4387.04 / 4387.81, tps 4390.41 / 4388.88 / 4391.19) are reproduced **to the cent by both
conventions**. The forming-bar convention additionally emits BUY signals at 08:50 and 08:51
(stops 2.23 / 1.68 pt, i.e. the 08:50 one would have been a *trade* at floor 1.5) that the
live runner never logged; the closed convention emits none. The eight 11:06–11:37 rows are
after S5 coverage (no data). Live placed the 09:28 signal: filled 4387.06 (0.75 below the
signal price — favourable drift), stopped 09:33:48 at 4386.04 (stop 4386.12, 0.08 through),
realised −0.1 price×lots = −$10. Pre-11:11 rows ran at floor 1.5; the 11:11:04 row was gated
at 1.5, its 11:11:15 twin at 3.0 — the restart boundary is visible in the record.

### May–June 2026 (767 signal rows, 153 placed; `parity_mayjune.py`)
Match on (signal minute, side) of every live row against the harness's raw signals:
**closed 86.3 %** (stop to the cent in 95 % of matches, median entry difference 0.00, p90 0.84),
forming **60.8 %** (94 % / 0.00 / 1.00). Matched by status: closed 83 % of PLACED, 87 % of
REJECTED. The 153 placed signals resolved on the S5 quote at their live levels: **PF 0.652 /
−100.8 pts at 0.45; 0.561 / −139.0 at 0.70; WR 31.4 / 29.4 %; 105 SL / 48 TP** (the
positions/orders of that period were purged from the DB and `broker_deals` starts 07-06, so
this is the S5 resolution of what live signalled, not the broker's P&L). Consistent with the
closed-frame harness (WR 34 %), not with the deployed numbers (WR 49 %).

### H0-ext — mechanism split and blast radius (`results/h0_ext/`)

Same incumbent config, one frame closed at a time (cost 0 replays; scored at the study costs):

| arm | n | mid-M1 1.00 PF (TRAIN / TEST) | quote-S5 0.70 PF (TRAIN / TEST) | 0.70 pts |
|---|---|---|---|---|
| both frames forming (`lab/harness.py`) | 1624 | 1.305 (1.219 / 1.408) | 1.275 (1.178 / 1.392) | +1041.8 |
| M5 closed, M15 forming (only the EMA21 bias sees the future) | 1474 | 1.075 (1.013 / 1.149) | 1.065 (1.003 / 1.139) | +242.2 |
| M15 closed, M5 forming (only the order block sees the future) | 1604 | 0.850 (0.840 / 0.860) | 0.835 (0.821 / 0.851) | −741.0 |
| **both closed (live)** | 1499 | 0.687 (0.681 / 0.693) | **0.680 (0.675 / 0.686)** | **−1444.3** |

Gross (cost-0) points: +2782 / +1759 / +918 / +45 — the two leaks are roughly additive, and the
**bias leak (a 15m close up to 14 min ahead) is the larger one**. "The filter is the strategy"
(xau2y §Established 2) was right for the wrong reason: the filter was the look-ahead.

`c03_fvg_fill` at its live config (floor 1.5), closed frames: n 1997 (Stage-1: 2147), mid-M1
1.00 PF **0.861** (TRAIN 0.800 / TEST 0.910), quote-S5 0.70 PF **0.869 / −955.5 pts** (TRAIN
0.827 / TEST 0.902); 0.45 TEST 0.949. c03 reads `w5m.iloc[-1]` as "the 5m candle that closed
back outside the gap". It is live since 05:00 UTC today; out of this study's mandate to change,
reported because the same harness produced its deployment numbers (the r2_c03 study covers it).

Floor 1.5 (the pre-11:11 live config), closed frames: n 3644, quote-S5 0.70 PF 0.591 /
−3423 pts (TRAIN 0.565 / TEST 0.633). The floor grid's *direction* survives the fix (3.0 beats
1.5 at every cost), its *conclusion* does not (nothing is positive).

## H1–H5 — can the closed-frame strategy be made positive? (`results/h1_h5/SCORE.csv`)

All arms: closed frames, floor 3.0 unless stated, one replay each at cost 0, costs applied per
trade by `score.py`; quote-S5 via `lab.s5exit.resolve` (offset 60 s, horizon 1000 min).
Incumbent row = H0 closed arm. Bars 1–7 are judged at 0.70 quote-S5 (stress) unless noted.

| arm (hypothesis) | n | median stop | mid-M1 1.00 TRAIN / TEST PF | **quote 0.70 TRAIN PF / pts** | **quote 0.70 TEST PF / pts** | 0.45 TEST PF | bars 1–5 |
|---|---|---|---|---|---|---|---|
| **incumbent** floor 3.0 closed | 1499 | 3.52 | 0.681 / 0.693 | 0.675 / −776.4 | 0.686 / −668.0 | 0.754 | 2/6 |
| H1 floor 3.5 | 1005 | 3.93 | 0.737 / 0.635 | 0.719 / −475.9 | 0.617 / −625.5 | 0.674 | 2/6 |
| H1 floor 4.0 | 629 | 4.32 | 0.697 / 0.629 | 0.674 / −365.9 | 0.615 / −442.2 | 0.666 | 2/6 |
| H1 floor 5.0 | 63 | 5.09 | 0.658 / 0.573 | 0.450 / −57.5 | 0.549 / −78.4 | 0.588 | 2/6 |
| H2a FIRST_TOUCH (unmitigated block only) | 381 | 3.56 | 0.706 / 0.647 | 0.704 / −237.6 | 0.648 / −123.7 | 0.711 | 2/6 |
| H2a control: random gate p = 0.254 | 799 | 3.58 | 0.692 / 0.658 | 0.661 / −466.1 | 0.639 / −391.2 | 0.703 | 2/6 |
| H2b block age ≤ 12 M5 bars | 859 | 3.50 | 0.697 / 0.665 | 0.681 / −498.0 | 0.631 / −388.0 | 0.694 | 2/6 |
| H2c displacement 2.5× (vs 1.8) | 675 | 3.53 | 0.720 / 0.565 | 0.713 / −335.2 | 0.594 / −359.4 | 0.654 | 2/6 |
| H2d body zone (open–close) | 695 | 3.39 | 0.675 / 0.578 | 0.664 / −380.5 | 0.605 / −364.7 | 0.668 | 1/6 |
| H3 protected-swing stop, 5 bars | 1696 | 4.55 | 0.797 / 0.662 | **0.766** / −944.7 | 0.694 / −1005.1 | 0.738 | 2/6 |
| H3 protected-swing stop, 3 bars | 1772 | 4.04 | 0.746 / 0.632 | 0.720 / −1017.9 | 0.643 / −1096.4 | 0.693 | 2/6 |
| H4 TP 1.0 R | 1830 | 3.55 | 0.560 / 0.630 | 0.562 / −1001.4 | 0.603 / −809.6 | 0.693 | 2/6 |
| H4 TP 1.5 R | 1623 | 3.53 | 0.632 / 0.701 | 0.651 / −805.1 | 0.691 / −647.1 | 0.771 | 2/6 |
| H4 TP 3.0 R | 1382 | 3.50 | 0.750 / 0.670 | 0.748 / −605.9 | 0.711 / −628.9 | 0.773 | 2/6 |
| H5a 15m EMA 50 | 1478 | 3.50 | 0.686 / 0.708 | 0.689 / −727.2 | 0.704 / −612.8 | 0.775 | 2/6 |
| H5b 5m EMA 21 | 1552 | 3.53 | 0.651 / 0.688 | 0.633 / −946.4 | 0.689 / −668.4 | 0.757 | 1/6 |
| H5d daily SMA20 alignment (BUY above / SELL below) | 781 | 3.49 | 0.717 / 0.716 | 0.726 / −346.5 | 0.765 / −236.5 | 0.842 | 2/6 |
| H5a 15m EMA 9 | 1518 | 3.54 | 0.683 / 0.711 | 0.661 / −846.4 | 0.675 / −690.0 | 0.742 | 2/6 |
| H5c H1 EMA 21 (resampled from the M15 window) | — | — | still in flight when the coordinator closed the grid (13:26 UTC); killed unfinished, not reported | | | | |

Reading, per the pre-registered rules:
- **No arm passes bar 2 (TEST PF > 1) at either mid-M1 cost, nor bar 3 (TRAIN PF > 0.9).** The
  best TRAIN quote-S5 PF is H3 swing-5 at 0.766 (TEST 0.694, −1005 pts); the least negative
  TRAIN points is H2a at −237.6 (110 TEST trades). Bar 7 (beat the incumbent on TEST PF *and*
  points) is met by H5d on PF (0.765 vs 0.686) and points (−236.5 vs −668.0) and by H2a on
  points — but a less-negative loss is not an improvement the protocol recognises when bars
  2–3 fail; both are subsets that lose less by trading less.
- **H2a vs its control:** FIRST_TOUCH −237.6 / −123.7 (TRAIN / TEST) vs random thinning
  −466.1 / −391.2 on ~2× the trades; per trade −0.88 / −1.12 vs −1.03 / −1.12. The concept
  gate loses the same per trade as a coin. Note the random gate at the *trade-level* acceptance
  0.254 kept 799 trades, not 381: a rejected signal frees the slot for the next one, so the
  concept's raw-signal acceptance is well below 0.254 (deviation declared in PROTOCOL.md).
- **H1 plateau:** quote 0.70 TRAIN PF 0.675 → 0.719 → 0.674 → 0.450 for 3.0 / 3.5 / 4.0 / 5.0;
  TEST 0.686 → 0.617 → 0.615 → 0.549. There is no rising trend to extend; 3.5 is a lone TRAIN
  bump that reverses on TEST. The "edge of grid" caveat of `REPORT_s14_minstop` is resolved:
  nothing beyond 3.0, and 3.0 itself is a floor on a losing strategy.
- **H4:** a 1 R target raises WR to 51 % (cost 0) and is the worst arm after cost; 3 R is the
  best of the family and still 0.75 / 0.71. No target multiple makes the entries pay.
- **H5:** every bias definition that cannot see the future is ≈ 0.63–0.77. Daily alignment (H5d)
  is the least bad, consistent with the xau2y Stage-2 finding that regime subsets of s14 had
  higher PF — that finding was also produced on the leaky harness.
- Uniformity of the loss (incumbent, quote 0.70): 23 of 25 months negative; every hour 07–15
  UTC negative (PF 0.5–0.8); BUY 0.70 / SELL 0.70; every stop bucket 0.6–0.7; every
  minute-phase within the M5 and M15 bar negative. This is a well-powered negative, not a
  thin one: 1,499 trades, −0.96 pts per trade.

## Verdict

- s14_ob_mit_bias **cannot be made better by any of the 16 pre-registered changes**, because
  under live-faithful information it has no edge to improve: quote-S5 PF 0.68 (TRAIN 0.675 /
  TEST 0.686), −1,444 pts over 24 months at 0.70 — about −2.7 pts per trading day, ≈ −$30/day
  at the current ~0.11-lot sizing (`RISK_PER_TRADE_USD 38` over a 3.5-pt stop). The 3.0 floor
  shipped at 11:11 UTC is directionally right (it beats 1.5 at every cost, 0.68 vs 0.59) and
  irrelevant (both lose).
- Every deployment number for s14 in `REPORT_xau2y`, `REPORT_s5exit`, `REPORT_s14_minstop`
  and CONTEXT §2 was produced on the leaky harness; c03's likewise, and under closed frames
  c03 is quote-S5 0.87 at 0.70. The May–June 2026 live record (153 placed signals, PF 0.56–0.65
  on the S5 quote) was the honest measurement all along and was not consulted before today's
  deployment.

## Recommendations (operator decisions)

1. **Stop `research_ob_mit_bias` now** (`docker compose -p kronos stop research_ob_mit_bias`
   on the box). Do not tune it; there is nothing to tune. Keep the `Strategy` rows.
2. Treat every xau2y survivor as unscreened until re-run under the fixed harness (the
   coordinator's `1c9ae30`); c03 (live) is the urgent one — its closed-frame numbers above fail
   bar 2 at both costs.
3. Add the closed-bar test the vault's trap #9 describes to CI, and make **minute-level signal
   parity against `apis_strategysignal`** (the `parity_mayjune.py` pattern: ≥ 85 % of live rows
   reproduced on minute+side, stop to the cent) a gate before any screen result is believed.
4. If an order-block strategy is wanted at all: the concept as coded (s03: price inside the
   wick range of the candle before a 1.8× body, stop 0.3 beyond, 2 R) is a coin with a spread,
   and no TTrades gate tried here (unmitigated block, freshness, displacement, body zone,
   protected-swing stop) moves it. The one thing this study did not test is a *higher-timeframe
   order block* (H1/H4) with M5 confirmation — the corpus's own "no entry without a
   higher-timeframe reason" — which needs frames the runner does not supply today.

## What this does and does not establish

Established: the harness defect, its size on s14 (all of the edge) and c03 (all of the edge),
the live convention (closed frames, 86 % signal parity on 767 rows, 3/3 to the cent today),
and that 16 pre-registered variants of s14 under honest information are all negative at both
quote-S5 costs on both halves. Not established: anything about order blocks as a discretionary
tool, or about OB strategies with genuinely higher-timeframe context; nothing about a gold bear
market. Multiple comparisons: 16 arms + 1 control + 4 H0/H0-ext arms; with all of them negative
the correction is moot.

## Files
`PROTOCOL.md` (H0, H-live, H1–H5, H2a control, deviations, coordinator instruction);
`harness_closed.py` (study harness: `closed_frames` True/False/"m5"/"m15", `regime_align`);
`r2_sweep.py` (runner, `R2_SLICE`/`R2_REVERSE`); `r2mod/s14v.py` (parameterised s14);
`score.py`; `parity_today.py`, `parity_mayjune.py`; `campaigns/*.py`; `results/h0_closed_frames/`,
`results/h0_ext/`, `results/h1_h5/` (each with `SCORE.csv`, per-arm `.json`, `.trades.parquet`,
`.q.parquet`); `results/live_signals.csv`, `results/parity_*_sim.csv`, `results/*.log`.
