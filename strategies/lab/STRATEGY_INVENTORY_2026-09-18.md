# Strategy inventory for the offline backtest campaign -- 2026-09-18

Scope: every module implementing `get_signal(w1m, w5m, w15m, now_utc)` under
`backtest_strategies/` and `concept_strategies/` (15 modules). Each was smoke-run through
`lab/harness.py::replay` on ONE month (`start="2026-05-01"`, `end="2026-06-01"`) with
`Cfg(cost_pts=0.45)` and the windows listed below. No module was modified; the environment
was left untouched (S94 / S100 ran at their env defaults). Timings are wall-clock seconds
for the one-month replay, measured with three replay processes running side by side on a
14-core machine (per-process cost is what is reported; the harness is single-threaded).

The "est. 24-mo minutes" column is `1-mo seconds x 24 / 60`. Note the cache itself holds
19.5 months (2025-01-01 .. 2026-08-12), so a full-cache replay costs ~19.5x the month figure;
24 months is the requested planning number.

## Table

| module | NAME | concept(s) | decision TF | windows used (1m/5m/15m) | time-exit | status quote | 1-mo s | est. 24-mo min | n / pts / pf (1 mo) | run status |
|---|---|---|---|---|---|---|---|---|---|---|
| s03_ob_mitigation | OB_MIT | Order block (5m OB = last opposing candle before displacement) mitigation / touch entry | 5m OB, 1m close entry | 700/160/100 (defaults; inferred need: w5m>=20 (uses tail 60), w1m>=5) | none | none stated ("Concept: 5m OB ... when price returns to mitigate (touch) the OB, it tends to reverse"); S14 cites its 30d baseline "36.4% WR, +160.9 pts" | 34.8 | 13.9 | 637 / +224.0 / 1.199 | OK |
| s04_breaker_block | BB | Breaker block (failed OB polarity flip, retest) | 5m OB/break, 1m close entry | 700/160/100 (defaults; inferred need: w5m>=30 (uses tail 80), w1m>=5) | none | none stated ("Concept: when a bullish OB is broken DOWN through with momentum, the zone flips polarity to a bearish Breaker") | 50.7 | 20.3 | 882 / -399.6 / 0.778 | OK |
| s10_90min_fade | M90_FADE | 90-minute algorithmic time cycle: fade the opening manipulation sweep back to cycle open | 1m only | 700/160/100 (defaults; inferred need: w1m>=5, practically >=31 to hold the 30-min cycle slice) | none | none stated; research tone ("Trade within first 30 mins of a 90-min cycle (was 15 -- too narrow)", "Lowered run threshold from 2.0 -> 1.2pt") | 0.8 | 0.3 | 1173 / -395.6 / 0.793 | OK |
| s11_m90_fade_ny | M90_NY_BUY | S10 90-min cycle fade, BUY side only (NY-open session lives only in CONFIG hours 14-17, which the harness does NOT apply) | 1m (delegates to s10) | 700/160/100 (defaults; same as s10) | none | "Experiment E1 ... Target: cross the 70% live-promotion bar" | 1.3 | 0.5 | 624 / -188.5 / 0.813 | OK |
| s12_m90_fade_bias | M90_BIAS | S10 90-min cycle fade gated by 15m EMA21 HTF bias | 1m (delegates to s10) + 15m bias | 700/160/100 (defaults; inferred need: w15m>=22 for EMA21) | none | "Experiment E1c ... Hypothesis: the losing M90 trades are the ones fading *with* the dominant HTF trend" | 1.7 | 0.7 | 485 / -25.2 / 0.959 | OK |
| s14_ob_mit_bias | OB_MIT_BIAS | Order block mitigation (S03) gated by 15m EMA21 HTF bias | 5m OB, 1m entry (delegates to s03) + 15m bias | 700/160/100 (defaults; inferred need: w5m>=20, w15m>=22) | none | "Experiment E2 ... Filtering to bias-aligned mitigations should lift WR materially" | 37.5 | 15.0 | 347 / +538.5 / 2.168 | OK |
| s93_fvg_scalp | KRONOS_S93_FVG_SCALP | FVG continuation scalp (displacement FVG, NY killzone hours {13,14}), SOFT M15 swing-structure veto, gap cap | 5m FVG, 1m retrace touch; 15m structure | 60/160/100 (harness WINDOWS; declared MIN_BARS_5M=18, MIN_BARS_15M=63) | 120 min | live ("SCALPING-category child strategy ... the first scalp to pass held-out validation"; hours (13,14) "SHIPPED" 2026-09-02) | 1.9 | 0.8 | 24 / +13.1 / 1.124 | OK |
| s94_sweep_reversal | KRONOS_S94_SWEEP_REVERSAL | Liquidity sweep reversal (PDH/PDL, session H/L, fractal swing levels; close-back-through confirm; HTF15 wick validation) | 5m sweep/levels, 1m retest touch; 15m wick check | 60/1500/100 (harness WINDOWS; declared MIN_BARS_5M=298; _LEVEL_TTL=1440 M5 bars is the full level universe) | 1200 min | live but flagged ("TREND-category child strategy"; "Do NOT cite PF 1.82 for the shipped configuration"; offline 19.5mo replay "PF 0.851") | 4.2 | 1.7 | 68 / -67.1 / 0.740 | OK |
| s95_session_breakout | KRONOS_S95_SESSION_BREAKOUT | Session opening-range breakout (30-min OR, sessions [1,7,12,13,14] UTC, EMA240+slope bias) -- session concept, not ICT | 5m OR + bias, 1m boundary touch | 700/300/100 (explicit win_5m=300; inferred need: w5m>=290 = EMA240 + 48 slope + 2; default 160 -> silent no-trade) | 180 min | paper slot mirroring live ("thin delegate of kronos_session_breakout ... so the manager's PAPER session slot trades the SAME design as the live SESSION_BREAKOUT bot") | 16.5 | 6.6 | 43 / +78.3 / 1.270 | OK |
| s96_h1_momentum | KRONOS_S96_H1_MOMENTUM | H1 Donchian(24) continuation momentum, EMA20/50 bias -- momentum, not ICT | H1 (resampled from 15m) | 700/160/320 (explicit win_15m=320; inferred need: >=76 closed H1 = >=304 15m bars +1 forming bucket) | none | retired ("RETIRED from the manager roster 2026-07-06 (operator decision): the weakest 3-month contributor") | 18.8 | 7.5 | 30 / -75.9 / 0.855 | OK |
| s97_snap_scalper_m5 | KRONOS_S97_SNAP_SCALPER | M5 snap-fade of a 2-bar overshoot, gated by ict_engine HTF structure bias (H4/H1 resampled from 15m) | 5m, with H1/H4 structure | first 700/160/200 (module floor _MIN_HTF_BARS*16=192) -> n=0; retried 700/160/260 -> n=0; retried 700/160/400 -> n=17. Effective need: w15m>=~400 (see notes) | 30 min | paper / superseded ("PAPER-ONLY in v1 (doctrine: edge unproven at honest costs)"; S98 docstring: "Replaces S97 snap-fade"; S93 docstring lists "S97 snap-fade" among "five failed campaigns") | 5.6 (w15m=200) / 7.7 (w15m=400) | 3.1 | 0 / 0 / 0 (w15m=200); 17 / +34.8 / 4.539 (w15m=400) | OK after window retry |
| s98_zscore_mr_m15 | KRONOS_S98_ZSCORE_MR | M15 z-score (SMA50/std50) mean reversion with ADF gate -- statistical MR, not ICT | 15m | 700/160/100 (defaults; inferred need: w15m>=52) | 240 min | paper / failed ("PAPER slot"; S93 docstring lists "S98 z-score MR" among "five failed campaigns") | 1.5 | 0.6 | 2 / -18.6 / 0.000 | OK |
| s99_mss_fvg | KRONOS_S99_MSS_FVG | Liquidity sweep -> market structure shift (MSS) -> FVG retrace reversal, hours 06-15 UTC | 5m MSS/FVG, 1m retrace touch | 60/160/100 (harness WINDOWS; declared MIN_BARS_5M=58) | 480 min | live ("REVERSAL-category child strategy ... the first new family to pass held-out validation since the manager redesign") | 3.4 | 1.4 | 98 / -57.9 / 0.817 | OK |
| s100_m3_combo | KRONOS_S100_M3_COMBO | M3 combo: FVG retrace + order block edge retest + RSI(3) momentum, EMA20/200 direction gate, hours 1-8/13-15 UTC | M3 (resampled from 1m), 1m retrace touch | 700/160/100 (harness WINDOWS; declared MIN_BARS_1M=642 with S100_ER_GATE=off) | 72 min | paper-first ("regime-dependent, deploy paper-first"; "Validated 2026-07-23 on 3y OANDA M1") | 3.7 | 1.5 | 247 / +57.6 / 1.088 | OK |
| c03_fvg_fill | C03_FVG_FILL | 5m FVG fill with reaction-close trigger, London/NY killzones, H1-EMA-slope bias | 5m FVG + 5m close trigger, 1m entry price; 15m-derived H1 slope | 700/160/100 (defaults; inferred need: w5m>=40 (tail 40 / ATR 20), w15m>=84 (20*4+4), w1m>=5) | none | none stated (concept strategy: "An unfilled 5m FVG inside an impulse leg that broke prior swing structure") | 15.2 | 6.1 | 88 / +147.6 / 1.418 | OK |

Total est. 24-month cost, one arm per module at the windows above: ~80 min sequential
(~20 min for s04 alone; the s03/s14/s04 family is 60% of it because `ict_engine.detect_order_blocks`
is a per-row `iloc` loop re-run on every M1 bar).

## Modules that cannot run

None. All 15 modules imported and completed the one-month replay without raising. No
`assert_windows` failure occurred (the four roster modules take their compose windows from
`harness.WINDOWS`; the others declare no MIN_BARS_* or are satisfied by the defaults).

Silent-no-trade cases found and resolved by window (class a), no exception raised:

- `s97_snap_scalper_m5` -- 0 trades at w15m=200 and at 260. Diagnosed by sampling
  `_htf_bias()` over May 2026: at w15m=200 the H4 resample yields 12-15 closed bars and
  `ict_engine.get_htf_bias` returned "neutral" on 241/241 samples; at 260 (16-19 closed H4)
  6/241 non-neutral; at 400 (25-28 closed H4) 54/241 non-neutral. The module's own floor
  (`_MIN_HTF_BARS * 16 = 192`) is therefore too low for the bias it depends on; use
  `win_15m=400` (or larger) in the campaign. Note the bias itself is a function of window
  length, so the campaign must fix one value and record it.
- `s95_session_breakout` -- would be silent at the default w5m=160 (needs >=290 for EMA240 +
  48-bar slope); run with w5m=300.
- `s96_h1_momentum` -- would be silent at the default w15m=100 (needs >=304); run with w15m=320.

## Recommended campaign set (all 15 run cleanly; grouped by ICT concept)

Families (treat each as ONE family for arm budgeting; members share code paths):

1. **Order block** -- `s03_ob_mitigation`, `s14_ob_mit_bias` (s14 calls s03.get_signal and adds a
   15m EMA21 bias gate -> near-duplicate, same trades filtered), `s04_breaker_block` (same
   `detect_order_blocks` detector, opposite polarity). s14 is the strongest raw one-month
   result in the whole inventory (PF 2.17, n=347) and the natural lead arm.
2. **FVG retrace / continuation** -- `s93_fvg_scalp` (live), `c03_fvg_fill`, `s100_m3_combo`
   (FVG leg + OB + RSI on M3). s93/s99/s100/c03 all enter on FVG retraces (s93's docstring
   already flags the s93/s99 correlation); expect overlapping entries.
3. **Liquidity sweep / MSS reversal** -- `s94_sweep_reversal` (live, flagged), `s99_mss_fvg`
   (live; sweep -> MSS -> FVG, so it also belongs to family 2).
4. **90-minute cycle fade (time-based manipulation)** -- `s10_90min_fade`, `s11_m90_fade_ny`,
   `s12_m90_fade_bias`. s11 and s12 both call s10.get_signal and only filter its output
   (s11: BUY side only; s12: 15m EMA21 bias) -> one family, and s11's NY-session restriction
   and s12/s10's 07-16 session are CONFIG hours that `replay()` does not apply (see notes).
   Cheapest family by far (<1 min per 24-mo arm).
5. **Session / opening range** -- `s95_session_breakout` (delegate of `kronos_session_breakout`;
   not strictly ICT but session-anchored).
6. **Non-ICT controls** (include only if the campaign wants baselines): `s96_h1_momentum`
   (retired Donchian momentum), `s97_snap_scalper_m5` (superseded snap-fade; use w15m=400),
   `s98_zscore_mr_m15` (failed z-score MR; 2 trades/month).

## Notes

- `replay()` applies news blackout, min-SL, `block_hours`, `sides` and `regime`, but NOT a
  module's `CONFIG.session_start_hour/end_hour`. Modules that rely on CONFIG for their session
  (s03/s04/s10/s11/s12/s14: 07-16 UTC; s11: 14-17 UTC) traded around the clock in this smoke
  run. Emulate via `Cfg(block_hours=...)` if the campaign wants live-equivalent hours; s93,
  s94, s95, s97, s98, s99, s100 and c03 gate hours inside `get_signal` and are unaffected.
- One-month figures are for smoke/timing only (n as low as 2-30 for s96/s97/s98/s93); they are
  not evidence of edge.
- s94 and s100 read env at import; they ran at defaults (S94_SIDES=BUY,SELL, S94_SD_MULT=2.0,
  S100_ER_GATE=off). `Cfg.env` / `Cfg.patch` in the harness are the sanctioned way to vary them.
- s95 and the s03/s04/s14 family are the slow ones (pure-Python per-bar loops), but all are
  well under 25 minutes per 24-month arm, so the campaign is bounded by arm count, not module cost.
- Raw per-module JSON from the smoke runs is in the session scratchpad (not in the repo).
