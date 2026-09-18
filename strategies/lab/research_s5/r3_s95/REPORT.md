# r3_s95 — s95 reconciliation (2026-09-19)

Pre-registered in `PROTOCOL.md`; every number from `results/run_output.txt`; per-trade data in
`results/trades_02c.parquet`. Honest closed-bar harness list (1,014 trades), S5 quote, the
r2_live_parity `02c` fill convention (market fill at bar-open + 66 s on the sided quote, live
`entry_drift` budget, quote exits, +0.26 pt slip on SL).

## The answer in one line

**s95 has a real, honest edge — and live cannot fill it with market orders.** The edge lives in
the breakouts that run fast, which the live `entry_drift` gate correctly refuses to chase.

## H1 — expectancy under the live fill model

| series | n | TRAIN PF / pts | TEST PF / pts | +months (TEST) |
|---|---|---|---|---|
| honest harness, nominal entry, mid-M1 @0.75 | 1,014 | 1.279 / +541 | **1.315 / +814** | 60 % |
| nominal entry, quote exits @0.70 | 1,014 | 1.182 / +371 | **1.259 / +690** | — |
| **02c: market fill @+66 s, drift gate, quote exits** | **538 accepted** | 1.042 / +52 | 1.060 / +87 | 40 %, max-month share 1.68 |

The drift gate rejects **476 of 1,014 entries (47 %)**: the market had already run a mean
**+2.89 pt** past the level by +66 s. Those rejected trades are the strategy: nominal-entry PF
**1.97 / +1,634 pts**; the accepted 538 are PF **0.81 / −572 pts**. Live saw the same thing in
July (2 of 8 signals rejected at +11.76 and +1.14 pt). Bars on the 02c series: n ✓, TEST PF > 1
under 02c ✓ but **not under quote-S5 0.70 on the accepted set** (0.824), TRAIN ✓, **monthly ✗**
(4/10 months, one month 1.7× the net), **regime ✗** (down-months −124, corr +0.45).
**Decision rule → FAIL: no PAPER re-arm as-is.**

## H2 — the "nominal-entry" term, resolved

The execution study called s95's harness entries *conservative* (−1.07 pt vs the market at
signal time). Under the 02c timing the market fill is **+1.01 pt worse** than nominal (median
+0.43; only 34 % of fills better; SELL +1.34, BUY +0.76). Both are true: at the signal bar's
close the level is favourable; 66 s later a breakout has left. The harness's number is a fill
the runner's market order does not get.

## H3 — the live window (n = 13, consistency only)

02c sim 07-01 → 07-17: 18 trades, −112 pts, PF 0.42 (13 TIME exits). Live: 8 trades −$86 during
the stale-fill bug + 5 trades +$28 after. Consistent with "break-even to negative at market
fills"; no conclusion from n = 13.

## H4 — book fit (equal risk, honest lists)

Daily-R correlation with S93 **−0.05**. S93 alone −3.3 R / 25 months; S93 + s95 (02c) +0.3 R;
s95 (02c) alone +3.6 R, worst month −4.0 R, max DD −10 R. On 02c fills s95 is break-even
diversification, nothing more.

## The lead (not pre-registered; reported as such): stop-entry execution

A resting **stop order at the breakout level** fills at the level (+ slip) instead of chasing at
+66 s, so the nominal-entry series is what live would get. Model: nominal quote-exit series at
0.70 minus 0.26 slip on every entry —

| | n | PF / pts | pts/trade | +months / max-month share |
|---|---|---|---|---|
| TRAIN | 623 | 1.100 / +210 | +0.34 | 0.47 / 1.08 |
| TEST | 391 | **1.218 / +588** | +1.51 | 0.60 / 0.56 |

That is the "R4 limit/stop-order entries" item that has been the standing open work since
2026-09-02 (`lab/CAMPAIGN.md`) — s95 is the strategy that makes the engineering worth doing:
`entry_manager` places market orders only; a stop-entry path (MetaAPI `createStopBuyOrder` /
`createStopSellOrder` at the level, cancel at the session's end or on the opposite signal) would
turn the 47 % drift-rejected set into fills. The TRAIN monthly bar still fails on this estimate
(one month 1.08× net), so even then it is a paper candidate, not a live one.

## Recommendation

1. Do **not** re-arm s95 on the market-order path — under live fills it is PF ≈ 1.05 with a
   monthly-bar fail, and the manager's session_vol gating would cut it further.
2. **Build stop-entry execution** (R4) as the next engineering item, then re-run s95 through the
   real order path on paper for ≥ 4 weeks. It is the one strategy on the honest screen with an
   edge that execution, not the concept, is losing.
3. Until then the engine book is **S93 alone** (honest, marginal); the copy leg is VIP.
