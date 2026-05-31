# BTC_USD Edge Research — Findings

**Date:** 2026-05-31 · **Data:** `ltp` hypertable on TigerData (TimescaleDB Cloud)
**Author:** Anil (with Claude)
**Methodology:** ICT/SMC + statistical lenses, prove-edge-before-building, then
stress / walk-forward / Monte-Carlo per the `backtest-expert` discipline.

---

## TL;DR

1. **The data is excellent.** `BTC_USD` has **24.39M ticks** (~1.3s resolution),
   **2024-12-30 → 2026-05-29** (~17 months) — 3× deeper than the existing
   XAU/XAG sets. It trades **24h on weekdays, closed Sat, partial Sun** (24/5),
   so it has richer intraday session structure than the gold book assumes.
2. **Intraday BTC is near-efficient.** Autocorrelation ≈ 0, variance-ratio ≈
   0.98–1.00 at 5m/15m/1h. *Every* simple bracket strategy (opening-range
   breakout, volatility breakout, drop-momentum, z-fade) is **PF < 1.0 even at a
   generous $30 round-trip cost.** There is no robust simple intraday edge.
3. **The gold book would not transfer.** The live Kronos suite is almost entirely
   mean-reversion/fade built for XAU. On BTC, **intraday fades lose** (z<−2 fade
   has t = −2.19 → BTC *continues* after sharp drops). Porting it blindly = a
   likely loser.
4. **A real edge exists at the swing horizon — but it is fragile.** At 4h/1d BTC
   **mean-reverts** (1d autocorr1 = −0.14, VR(5)=0.77) and **breakouts fade**
   (4h Donchian-10 forward edge t = **−3.08** = ICT liquidity-sweep/turtle-soup).
   A z-score reversion (fade |z|>1.5 back to the 30-bar mean, **wide** stop, 24h
   hold) gives **PF 1.31, WR 57.6%**, survives cost stress to $90, 80% of folds
   positive, Monte-Carlo P(loss) ≈ 11%.
5. **But it does NOT pass out-of-sample.** WFE = 0.35 (need ≥0.5); IS PF 1.41 →
   OOS PF 1.16. The edge is **regime-dependent** — it earned its money in 2025's
   wide oscillating range ($62.8k↔$124.7k) and **decays in 2026's downtrend**.
   Refinement attempts (combine with sweep-reversal; Efficiency-Ratio regime
   gate) **either didn't help or overfit** (ER gate: IS PF 2.1 → OOS PF 0.5).
6. **A disciplined weekly-trend gate, calibrated only on 2025 and tested once on
   held-out 2026, did NOT rescue it** (§7). It looks great in-sample (PF 1.45→1.73)
   but the held-out forward window is breakeven-to-worse (ungated expR −0.011 →
   gated −0.068; walk-forward WFE 0.01). Every gate that improves 2025 fails 2026
   — the signature of true regime-dependence.

**Verdict: REFINE / do not deploy.** The edge is real *only* in the range regime;
no in-sample filter generalises. The only honest path to deployable is a genuine
**forward paper-test as new ticks accumulate** (harness: `forward_test.py --refresh`),
and/or a regime classifier proven on data it never saw.

---

## 1. Data characterization

| metric | value |
|---|---|
| ticks | 24,385,900 |
| span | 2024-12-30 → 2026-05-29 (~17 mo) |
| tick spacing | ~1–2s (avg ~1.3s) |
| schedule | 24h Mon–Fri, **closed Sat, partial Sun** |
| 1m candles | 525,602 (443 trading days) |
| daily range | mean 3.46%, median 3.02%, p90 5.87% |

**Volatility is NY-centric.** 5m mean \|return\| peaks **13:00–17:00 UTC**
(14–17 bps) and is *lowest* in the London AM (7–8 bps) — opposite of FX. Sessions:
NY 12.8 bps > LATE 10.3 > ASIA 8.9 > LONDON 8.0.

## 2. Intraday is near-efficient (random walk)

| TF | autocorr(1) | VR(4) |
|---|---|---|
| 5m | −0.004 | 0.982 |
| 15m | −0.007 | 0.981 |
| 1h | +0.001 | 1.000 |

Raw edge scans (15m, forward fixed-horizon return, \|t\|>2 ≈ significant): no
fade edge; the only significant signals are **negative for fades** — i.e.
`zscore<-2_fade_long` t = −2.19 (downside *continues*). Bracket backtests (all
PF<1 @ $30 cost): ORB_NY 0.84, VBO 0.81–0.91, DROPMOM 0.81, ZMR_control 0.90
(55% win but small wins / big stops — textbook bad fade).

## 3. Swing horizon flips to mean-reversion

Regime: start $92.6k → end $73.6k (**−20.6%**), min $62.8k / max $124.7k (99%
range), 50.7% up-days → a **wide range / topping** sample, not a trend.

| TF | autocorr(1) | VR(5) | VR(10) | read |
|---|---|---|---|---|
| 4h | +0.033 | 1.006 | 0.848 | mixed → reverts longer |
| 1d | **−0.141** | 0.768 | 0.735 | **mean-reverting** |

Forward-edge scans (significant, ICT-aligned):
- **Donchian breakout fades** — 4h Don-10 t=−3.08, Don-20 t=−2.74, Don-30 t=−2.64
  (= liquidity sweep / turtle soup).
- **Z-extreme reverts** — 4h \|z\|>2 fade +37.9 bps t=+2.20; 1d \|z\|>2 +173 bps t=+2.20.

## 4. Building & fixing the swing strategy

First brackets lost (PF 0.80–0.93): tight ATR stops **tag the overshoot before
reversion** — the classic MR pitfall. Fix = **wide stop + 24h (6×4h) time-exit**.
Sweep (z_thr × sl_atr × hold × guard): a real **plateau** appears — z∈{1.5,2.0},
hold=6, sl_atr≥3.5 all PF 1.2–1.33; **16/48 configs profitable in both years**.
`sl_atr=5.0` ≈ no-stop PF *with* a real catastrophe stop → chosen.

## 5. Honest validation (full / OOS / Monte-Carlo / cost)

| candidate | n | WR% | PF | expR | DD/PnL | OOS WFE | k-fold+ | MC P(loss) | verdict |
|---|---|---|---|---|---|---|---|---|---|
| **ZREV z=1.5** | 125 | 57.6 | 1.31 | 0.13 | 0.46 | **0.35** | 80% | 0.11 | REFINE* |
| ZREV z=2.0 | 82 | 54.9 | 1.31 | 0.142 | 0.60 | **0.08** | 60% | 0.17 | REFINE* |
| SWEEPREV | 113 | 54.9 | 0.78 | −0.10 | inf | 0.0 | 20% | 0.84 | ABANDON |
| COMBINED | 134 | 55.2 | 0.92 | −0.035 | inf | 0.0 | 40% | 0.66 | ABANDON |
| ZREV z1.5 +ER0.30 | 36 | 50.0 | 1.13 | 0.067 | 3.25 | **−0.67** | 60% | 0.38 | ABANDON (overfit) |

\* By the strict 7-check protocol all are technically ABANDON; ZREV is flagged
REFINE because it is genuinely +EV in-sample and robust to cost/Monte-Carlo — it
fails specifically on **out-of-sample / drawdown**, the regime-dependence signature.

Cost stress (ZREV z1.5): $30 PF 1.31 · $60 PF 1.27 · $90 PF 1.23 (cost-robust at
this horizon, unlike intraday).

## 7. Held-out forward test of a weekly-trend gate (`forward_test.py`)

Anti-snooping protocol: base config fixed (z=1.5, N=30, sl=5×ATR, 24h hold); the
**only** thing calibrated is the weekly-trend gate (`weekly_trend_state`, "align"
= don't fade against a strong weekly trend / "range_only"), calibrated **only on
the design window (< 2026-01-01)**, locked, then evaluated **once** on held-out
2026, plus a rolling anchored walk-forward that re-fits the gate each fold.

| window | config | n | WR% | PF | expR | MC P(loss) |
|---|---|---|---|---|---|---|
| design 2025 | ungated | 89 | — | 1.45 | 0.191 | — |
| design 2025 | align_w8_s0.5 (best) | 61 | — | **1.73** | **0.287** | — |
| **forward 2026 (held-out)** | ungated | 36 | 58.3 | 0.97 | −0.011 | 0.52 |
| **forward 2026 (held-out)** | **gated (locked)** | 23 | 60.9 | **0.83** | **−0.068** | 0.62 |

Rolling walk-forward: mean IS expR 0.270 → **mean OOS expR 0.003, WFE 0.01**
(consistency 75% but on trivially small pnls). **Conclusion: the gate boosts
in-sample and fails out-of-sample — it does NOT make the edge deployable.** 2026's
trend regime simply lacked the reversion edge 2025's range had.

## 8. What would make it deployable (next steps)

- **External regime filter, defined out-of-sample** — e.g. trade reversion only
  when a higher-TF (weekly) trend is absent; do the opposite (or stand aside) in
  trends. The naive ER gate overfit; a slower, pre-registered trend filter tested
  on held-out data is the disciplined path.
- **More data / forward test.** 17 months ≈ one BTC macro swing. A true forward
  window (paper) is worth more than further in-sample tuning.
- **Risk-sized exposure** (vol-target) to pull DD/PnL under 0.25.

## Files

```
btc_research/
  data.py           # ltp -> 1m parquet cache (server-side time_bucket); resamplers
  characterize.py   # vol/session, autocorr/VR, intraday edge scans -> characterization.json
  trend_scan.py     # regime + 4h/1d structure + donchian/reversion edges -> trend_scan.json
  engine.py         # vectorised signal gen + conservative exit sim (realistic cost)
  strategies.py     # ORB/VBO/DROPMOM/ZMR + swing sweep_reversal/zscore_revert/combined
  run_candidates.py # intraday battery (all PF<1)
  run_swing.py      # 4h swing battery + per-year
  run_sweep.py      # parameter plateau sweep
  validate.py       # OOS split + k-fold + Monte-Carlo + cost stress -> validation.json
  dashboard.py      # builds self-contained dashboard.html
  cache/  results/  # parquet + JSON + trade CSVs
```
