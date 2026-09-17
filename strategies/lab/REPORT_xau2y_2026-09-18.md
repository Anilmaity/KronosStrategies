# XAU_USD 24-month ICT strategy screen — report (2026-09-18)

Protocol: `PROTOCOL_xau2y_2026-09-18.md` (pre-registered). Data: `backtest/results/bars_cache_2y/`
(2024-08-15 → 2026-09-17, OANDA practice feed, QA in `QA_REPORT.md`: 0 seam disagreements over
543,135 M1 rows, 100.00 % exact higher-TF derivation, every gap classified, 8 unfillable holes
enumerated). Replay window 2024-09-01 → 2026-09-17, TRAIN < 2025-12-01 ≤ TEST, costs 0.45 / 0.80
(measured median spread over the last five weeks: 0.55). All numbers below are from
`lab/tools/campaign_score.py` and the scripts quoted; nothing is hand-typed.

## Headline

**Two ICT strategies the book does not currently trade — `c03_fvg_fill` (5m FVG fill inside an
impulse leg, killzones, H1 EMA-slope bias) and `s14_ob_mit_bias` (5m order-block mitigation gated
by 15m EMA21 bias) — pass every pre-registered bar on 24 months, survive 5-second bid/ask exit
resolution, are uncorrelated with each other (daily R corr 0.02) and with gold's direction, and
together with `s95_session_breakout` and the live `s93_fvg_scalp` form an equal-risk book that was
positive in 23 of 25 months at 0.80 cost.** The live roster's S94, S99 and S100 fail the screen at
their shipped configuration; S93 passes as shipped and its Stage-2 arms confirm the shipped
config is its own optimum. The best-looking strategy in Stage 1, `s97_snap_scalper_m5`
(TEST PF 2.58), is a **bar-granularity artefact** and is disqualified.

## Stage 1 — screen (15 modules × 2 costs, 983 s)

| module | concept | TEST n | TEST PF @0.45 / @0.80 | TRAIN PF | +months | gold-corr | verdict |
|---|---|---|---|---|---|---|---|
| s97_snap_scalper_m5 | M5 snap-fade + HTF bias | 232 | 2.577 / 2.239 | 2.394 | 90 % | −0.24 | PASS → **disqualified in Stage 2** |
| s14_ob_mit_bias | OB mitigation + 15m EMA21 bias | 1421 | 1.672 / 1.400 | 1.540 | 100 % | 0.48 | **PASS** |
| c03_fvg_fill | 5m FVG fill, killzones, H1 bias | 838 | 1.571 / 1.459 | 1.429 | 100 % | −0.38 | **PASS** |
| s95_session_breakout | session ORB + EMA240 bias | 384 | 1.388 / 1.327 | 1.409 | 70 % | 0.21 | **PASS** |
| s93_fvg_scalp (live) | FVG continuation, NY 13–14 | 200 | 1.340 / 1.238 | 1.153 | 70 % | −0.23 | **PASS** |
| s96_h1_momentum (non-ICT) | H1 Donchian | 275 | 1.365 / 1.340 | 1.257 | 50 % | −0.02 | NEAR (monthly bar; excluded per protocol) |
| s100_m3_combo (live) | M3 FVG+OB+RSI | 2432 | 1.065 / 0.959 | 0.898 | 60 % | −0.25 | FAIL (stress) |
| s12_m90_fade_bias | 90-min fade + bias | 2091 | 1.059 / 0.831 | 0.938 | 50 % | −0.27 | FAIL |
| s03_ob_mitigation | OB mitigation (no bias) | 2620 | 0.988 / 0.824 | 1.017 | 50 % | 0.53 | FAIL |
| s99_mss_fvg (live) | sweep→MSS→FVG | 961 | 0.925 / 0.835 | 0.937 | 40 % | 0.32 | FAIL |
| s04_breaker_block | breaker | 4052 | 0.889 / 0.756 | 0.854 | 20 % | −0.10 | FAIL |
| s94_sweep_reversal (live, shipped) | liquidity sweep | 697 | 0.866 / 0.804 | 0.827 | 50 % | 0.62 | FAIL |
| s11_m90_fade_ny | 90-min fade, NY | 910 | 0.855 / 0.718 | 0.911 | 30 % | −0.07 | FAIL |
| s10_90min_fade | 90-min fade | 4714 | 0.786 / 0.647 | 0.689 | 10 % | 0.32 | FAIL |
| s98_zscore_mr_m15 (non-ICT) | z-score MR | 16 | 0.197 / 0.190 | 0.745 | — | — | FAIL (n) |

Fidelity note applied for the first time here: the harness now enforces each module's
`CONFIG.session_start_hour/end_hour` exactly as `research_runner` does (six modules would
otherwise have been judged trading 24 h). Roster results were verified unchanged by the change.

## Stage 2 — pre-declared grid (86 arms, 3,692 s)

Gates: sides BUY/SELL; block Asia/London/NY; regime above/below daily SMA20; one module constant
(s97 `_TP_FRAC`, s93 `_TP_R`, s95 `_TP_MULT` via the delegate module); declared deviation: s97
window robustness (w15m 300/600). Every no-op gate (hours a module never trades) reproduced its
Stage-1 numbers exactly — the control the grid needed.

Arms passing bars 1–5 **and** bar 7 (beat own Stage-1 arm at 0.80 on TEST PF *and* points):

| arm | TEST n | PF @0.80 | pts @0.80 | vs Stage 1 | plateau (bar 6) |
|---|---|---|---|---|---|
| s95 `_TP_MULT` 1.0 | 380 | 1.395 | 1027.5 | +0.068 / +213.6 | 0.6→1.212, 0.8→1.327, 1.0→1.395: monotone, **edge of grid** (1.2 not run — not pre-declared) |
| s97 `_TP_FRAC` 0.35 | 238 | 6.055 | 440.5 | +3.816 / +67.0 | see disqualification |
| s97 w15m 600 | 381 | 2.298 | 649.5 | +0.059 / +276.0 | see disqualification |

Quality-improving but profit-reducing (higher PF, fewer points → fail bar 7, **not adopted**):
s14 SELL-only (PF 1.61 @0.80), s14 no-London (1.59), c03 SELL-only (1.63), c03 above-SMA (1.58),
s95 SELL-only (1.58), s95 below-SMA (1.52). These are subsets of a profitable population; the
protocol's bar 7 exists precisely so a subset with less total profit is not mistaken for an
improvement (the S94 long-only lesson of 2026-09-18).

s93: shipped `_TP_R` 1.5 beats 1.0 (1.037) and 2.0 (1.142) at 0.80; side/regime splits all
NEAR (monthly bar) — the shipped configuration is confirmed as its own optimum. `nony` → 0 trades
(it only trades 13–14 UTC), as expected.

### s97 disqualification (fidelity, not statistics)
Trade anatomy: **median hold 1 minute**, 83–93 % of exits are TP on the very next M1 bar, median
TP distance 1.4–2.0 pts against a 0.55-pt spread. Re-resolving the same entries on 5-second
bars (`lab/s5exit.py`, walk starting after the entry bar closes — the tool's start was corrected
for this, `start_offset_s=60`) over the S5-covered window 2026-07-06 → 08-13:

| arm @0.80 | n | mid M1 (harness) | mid S5 chronological | **bid/ask S5 (live trigger)** |
|---|---|---|---|---|
| s97 shipped | 25 | PF 0.86 | 0.76 | **0.55** |
| s97 `_TP_FRAC` 0.35 | 27 | 8.55 | 3.83 | **1.14** |
| s97 w15m 600 | 60 | 1.37 | 1.25 | **0.87** |
| c03 | 108 | 1.54 | 1.54 | **1.41** |
| s14 | 227 | 1.49 | 1.49 | **1.36** |
| s95 | 52 | 0.61 | 0.61 | 0.61 (a losing 5 weeks; 70 % of months positive on 24 mo) |
| s93 | 26 | 1.29 | 1.29 | 1.29 |

A mid-price M1 high touching a 2-pt target is not a fill. s97's edge is the M1 bar's own
range. The four survivors lose ~0–9 % of PF under the live trigger convention, which is the
haircut to apply to every number in this report.

## Stage 3 — book (c03, s14, s95, s93; shipped configs; 0.80 cost; 1 R per trade)

| | c03 | s14 | s95 | s93 |
|---|---|---|---|---|
| trades (24 mo) | 2161 | 3970 | 999 | 493 |
| PF (pts) | 1.36 | 1.31 | 1.31 | 1.13 |
| R per trade | +0.21 | +0.18 | +0.05 | −0.03 |
| median risk (pts) | 3.5 | 2.2 | 19.9 | 4.2 |

Daily-R correlation: c03/s14 **0.02**, c03/s95 0.13, c03/s93 0.20, s14/s95 0.08, s14/s93 0.03,
s95/s93 0.02. Monthly: max 0.49 (s14/s95).

Equal-risk book: **+1,195.6 R over 531 trading days, daily PF 2.51, max drawdown −67.9 R, worst
day −14.4 R, positive months 23/25 (worst 2025-08 at −51.2 R), TEST half 10/10 months positive,
monthly-R correlation with gold 0.04, +823 R in gold-up months and +372 R in gold-down months.**

## What this does and does not establish

Established, on 24 months of clean OANDA data, at realistic cost, held-out TEST, with quote-level
exit resolution checked:
1. `c03_fvg_fill` and `s14_ob_mit_bias` are the two best ICT strategies in the repo and are not
   in the live book. Both are high-frequency (c03 ≈ 4/day, s14 ≈ 8/day) with small per-trade edge
   (≈ 0.2 R) — a volume edge, so execution quality (one-bar-late live entries, MetaAPI 504s,
   slippage beyond the 0.80) decides whether it survives contact with the broker.
2. `s14` is `s03` + a 15m EMA21 trend filter, and `s03` alone fails; the filter is the strategy.
   Its gold-correlation (0.48) is the highest of the four but it made +871 pts in gold-down months.
3. `s95_session_breakout` (retired 2026-07-23 by operator decision) passes the screen and gains
   from a larger TP multiple; it was retired on a live record, not a backtest — reconcile before
   re-arming.
4. The live roster: S93 confirmed as shipped; **S94, S99, S100 fail the 24-month screen at their
   shipped configuration** (S100 only on the 0.80 stress; S94 outright). S94's long-only variant
   from yesterday was judged on the shorter window and would fail this protocol's monthly bar.

Not established:
- Anything about a gold bear market — the window has none.
- Live fidelity: the harness enters at the signal bar's close with no slippage beyond `cost_pts`.
  The repo's own live-vs-sim work (`backtest/parity_harness.py`, 2026-08) measured that gap for
  the roster; it must be measured for c03/s14 before any capital is allocated.
- Robustness of the s95 `_TP_MULT` slope beyond 1.0 (edge of the pre-declared grid).

## Recommended next steps (operator decisions, in order)
1. **Paper-deploy c03 and s14** under the manager (own slots, `DRY_RUN=true`) and run the parity
   harness for ≥ 4 weeks: per-trade live-vs-sim diff, fill latency, slippage. Ship nothing on the
   backtest alone.
2. Decide S99/S100: both live, both failing the 24-month screen at stress cost. At minimum re-run
   their own Stage-2 grids (not done here — out of the pre-registered survivor set).
3. Extend the S5 cache backwards (it covers 5.5 weeks) so quote-level exit resolution can be
   applied to the full window instead of a sample.
4. If s95 is reconsidered, run `_TP_MULT` 1.2/1.5 as a new pre-registered arm set before reading
   anything into the 1.0 result.

## Files
- protocol `PROTOCOL_xau2y_2026-09-18.md`; inventory `STRATEGY_INVENTORY_2026-09-18.md`
- campaigns `campaigns/xau2y_stage1.py`, `campaigns/xau2y_stage2.py`; results `results/xau2y_stage{1,2}/`
  (+ `SCORE.csv` each); scorer `tools/campaign_score.py`; cache builder `tools/build_bars_cache_2y.py`;
  tail fetch `tools/fetch_oanda_tail.py`; data QA `backtest/results/bars_cache_2y/QA_REPORT.md`
- harness additions this campaign: `CONFIG` session gate, dotted delegate `patch`, `LAB_BARS_CACHE`
