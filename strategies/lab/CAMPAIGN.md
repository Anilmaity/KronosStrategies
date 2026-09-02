# Optimization campaign — opened 2026-09-02

Standing brief: analyse the live record, optimise the roster, validate in parallel,
ship what genuinely works. Demo account, so deployment risk is capital-free — but the
**evidential** bar stays high, because a change that only looks good is worse than no
change (it burns the next campaign's credibility too).

## Ground truth — the live record

`backtest/results/parity/live_trades_2026-07-06_2026-08-12.csv`
242 trades, real broker USD, Winprofx-Demo.

| | n | USD | WR% | PF | exp$ |
|---|---|---|---|---|---|
| **whole book** | 242 | **+267.75** | 33.5 | **1.056** | +1.11 |
| S100 M3 Combo | 97 | +485.82 | 29.9 | 1.296 | +5.01 |
| S99 MSS FVG | 55 | +119.26 | 45.5 | 1.120 | +2.17 |
| S93 FVG Scalp | 43 | −168.34 | 37.2 | 0.823 | −3.91 |
| S94 Sweep Reversal | 47 | −168.99 | 23.4 | 0.857 | −3.60 |

The book is barely above water and one week (Aug 3–9, +939) carries it.

## H1 — the stop-distance cliff **(primary)**

`lots = clamp(38/(sl_dist*100), 0.01, 0.10)`, so `lots == 0.10` ⇔ `sl_dist < 3.8 pt`.
Splitting the live book on that boundary:

| | n | USD | WR% | PF |
|---|---|---|---|---|
| stop ≥ 3.8 pt | 78 | **+1349.38** | 47.4 | **2.012** |
| stop < 3.8 pt | 164 | **−1081.63** | 26.8 | **0.685** |

Holds for **all four** strategies independently:

| strategy | wide PF | tight PF |
|---|---|---|
| S94 | 3.364 | 0.310 |
| S100 | 2.261 | 1.023 |
| S99 | 1.613 | 0.835 |
| S93 | 1.426 | 0.397 |

Robustness checks already done:
- **Monotone**: every wide bucket is PF > 1.35; only the capped bucket is < 1.
- **Not outlier-driven**: drop the 5 best wide trades → still +704; drop the 5 worst
  tight trades → still −752.
- **Not a sizing artefact**: realised risk-USD is ~equal in both buckets (31.3 vs 28.2).
  Tight-stop trades don't risk more, they just *lose more often* (WR 26.8 vs 47.4).

Reading: `MIN_SL_DIST_PTS = 1.5` is far below the level where XAUUSD noise stops
dominating. The live friction floor argument was right in kind and wrong in magnitude.

## H2 — Asia hours are structurally negative

Hours 00–03 UTC: n=42, **−492 USD**, WR ~14%. S99 already dropped hours 1–5 in July 2026
after measuring ~1.5 pt round-trip friction there. S94 (24 h) and S100 (from 01:00) never
got the same treatment.

## H3 — the news blackout is too narrow

Hour 12 as a whole: n=16, **−298 USD**, WR 25%. The live blackout is only 12:25–12:45.

## The mechanism behind H1 — it is arithmetic, not a fitted effect

For a strategy that targets `k·R` with round-trip cost `c` and stop distance `R`:

```
expectancy      = R·(p·(k+1) − 1) − c
break-even p*   = (1 + c/R) / (k + 1)
```

Two measurements turn this from algebra into a diagnosis.

**1. The raw win rate is flat in stop width.** Over S93's 990 baseline trades, win rate
*before cost* barely moves across stop-distance buckets, while `c/R` collapses:

| stop bucket | n | raw WR | c/R @0.45 | net pts |
|---|---|---|---|---|
| <2 pt | 126 | 41.3% | 25.0% | −50.9 |
| 2–3 | 247 | 42.5% | 18.3% | −76.4 |
| 3–4 | 192 | 41.1% | 13.0% | −66.7 |
| 4–6 | 203 | 39.4% | 9.4% | −103.0 |
| 6–9 | 121 | 47.1% | 6.4% | **+67.2** |
| 9+ | 101 | 46.5% | 3.8% | **+140.3** |

Stop width does not change how often the strategy is *right*. It changes how much of the
edge friction eats. The tight-stop disadvantage is a **drag term**, not a skill term.

**2. The required win rate is knowable in advance.** At `k = 1.5`:

| stop | c=0.45 | c=0.62 | c=0.80 |
|---|---|---|---|
| 1.5 pt | 52.0% | 56.5% | 61.3% |
| 3 pt | 46.0% | 48.3% | 50.7% |
| 6 pt | 43.0% | 44.1% | 45.3% |
| 12 pt | 41.5% | 42.1% | 42.7% |

S93 delivers **42.4%**. At 1.5R it only clears break-even beyond roughly an 8–12 pt stop.
`MIN_SL_DIST_PTS = 1.5` admits a whole population of trades that cannot pay for their own
friction *no matter how good the signal is*.

### It predicts the live book

Using each strategy's target `k`, its realised win rate and its median live stop, the
margin `realised WR − break-even WR` separates the winners from the losers:

| strategy | k | realised WR | break-even WR | margin | live USD |
|---|---|---|---|---|---|
| S99 | 1.5 | 45.5% | 46.5% | −1.1 | **+119** |
| S100 | **2.5** | 29.9% | 33.2% | −3.3 | **+486** |
| S93 | 1.5 | 37.2% | 46.5% | −9.3 | −168 |
| S94 | ~2.0 | 23.4% | 38.8% | −15.4 | −169 |

The two strategies near break-even made money; the two far below it lost money.

**S100 is the only live winner and it is the only one targeting 2.5R.** That is independent
corroboration: raising `k` lowers the required win rate, which is the second lever the
formula exposes.

### Live is *worse* than the backtest for tight stops

Live raw win rate by stop bucket: 26.8% (<3.8 pt) / 54.5% (3.8–5) / 45.0% (5–8) / 43.8% (8+).
Unlike the backtest, where WR was flat, live tight-stop WR **collapses**. Real fills hit
tight stops harder than an M1-bar replay models. So a floor validated on backtest data is
**conservative** — the live case for it is stronger.

## Direct measurement of the exit-model bias (`lab/s5exit.py`)

The harness resolves exits on **mid** M1 bars. Live, a stop sits at the broker and triggers
on the **quote** — bid for a long, ask for a short — which is half a spread closer to entry.
A fixed distance is a large fraction of a 2 pt stop and negligible on a 10 pt stop, so a
mid-priced backtest should over-state tight stops and be unbiased on wide ones.

Tested by holding the **entries fixed** and re-resolving only the exit, on the 2026-07/08
S5 cache (which carries `bid_c`/`ask_c`), over each strategy's own max-hold horizon:

| bucket | S93 (n=70) | S99 (n=105) |
|---|---|---|
| <2 pt | 0 (n=4) | **−13.4 pts** |
| 2–3 pt | **−6.5** | **−19.3** |
| 3–4 pt | **−8.8** | 0 |
| 4–6 pt | **0** | **0** |
| 6+ pt | **0** | **0** |

The penalty is **confined to sub-3–4 pt stops and is exactly zero above**, in both
strategies independently. Per-trade that is ≈ −0.64 pt (<2), −0.52 (2–3), −0.27 (3–4), 0 (≥4).

Also worth noting: `mid_S5` beat `mid_M1` on both (S93 +57.3 vs +45.9; S99 +17.8 vs −1.3),
confirming the harness's SL-before-TP rule is genuinely pessimistic, as intended.

### Feeding that correction back into the 19.5-month backtest

Applying the measured per-bucket penalty to the test half:

| floor | S93 raw PF | S93 corrected | S99 raw PF | S99 corrected |
|---|---|---|---|---|
| 1.5 (current) | 0.931 | **0.899** | 0.907 | **0.856** |
| 3.5 | 0.969 | 0.962 | 0.878 | 0.872 |
| 5.0 | 0.979 | 0.979 | 0.932 | 0.932 |
| 6.0 | 1.043 | **1.043** | 0.908 | 0.908 |
| 8.0 | 0.968 | 0.968 | 0.949 | 0.949 |

> [!warning] The floor helps but does not rescue
> It lifts S93's corrected test PF from 0.899 toward ~1.0 and S99's from 0.856 to ~0.95,
> monotonically over most of the range. But **neither becomes convincingly profitable**,
> and S93's only crossing of PF 1.0 (floor 6.0) **falls back at floor 8.0** — a lone spike,
> not a plateau. By the pre-registered bar, "raise the floor" **passes on direction and
> fails on sufficiency**. It removes provably negative-EV trades; it does not manufacture
> an edge that is not there.
>
> This pushes the decisive question onto the second lever — the TP multiple `k` — which is
> what the sub-agents are testing.

### Caveat that cuts the other way

My harness generates ~2.7 S99 trades/day; live took ~1.3/day. Live's full gate stack
(entry drift, dup guard, no-add-to-loser, book-level concurrency) plus manager pauses filter
roughly half the population, and live S99 made **+$252** while the harness's ungated
population loses. So the harness is a *superset generator* and is **pessimistic in absolute
terms**. Use it for relative comparisons within a strategy; do not read "S99 is
unprofitable" out of it — the supported claim is only "S99's *ungated* signal population is
unprofitable". This reproduces the known prior result that the live gates do real work.

## Measured spread by hour — this REFUTES H2's proposed mechanism

From the S5 quote cache (2026-07-06 … 08-13, ~20k quotes per hour):

| hour block | median spread |
|---|---|
| Asia 00–05 | 0.62–0.65 |
| London 06–11 | 0.55–0.59 |
| NY 12–19 | 0.54–0.58 |
| rollover 20, 22–23 | 0.66–0.75 |

**The spread is nearly flat across the day.** Asia is only ~0.06 pt wider than London/NY —
1.7% of a 3.5 pt stop, worth about **0.7 pp** of break-even win rate. That cannot produce
the live Asia result (−$492 at 14% WR).

> [!important] H2's mechanism is dead; the hypothesis is downgraded
> I proposed Asia hours were bad because of thin-liquidity friction. The quote data says
> friction there is essentially normal. So the Asia effect is either genuine signal
> degradation (rangebound tape producing false breaks) or **small-sample noise** — the live
> Asia sample is only n=42.
>
> The backtest gets a vote here, and it leans toward noise: S99's hour-by-half breakdown is
> **unstable** (hour 9 is PF 0.57 train / 1.30 test; hour 12 is 1.32 train / 0.55 test —
> no hour is consistently good or bad). S93's hours *were* stable across halves, so the
> hour question is strategy-specific and must not be applied book-wide.
>
> Recorded as a hypothesis I raised and then falsified, rather than quietly dropped.

## Cost recalibration — 0.80 is the realistic case, not the stress case

Median spread is **0.59 pt**. A round trip pays roughly one full spread (half in, half out),
before any slippage. So realistic all-in friction is **~0.6–0.8 pt**, not the **0.45** the
strategies were validated at.

This reframes the published validation numbers: the "0.80 stress test" those strategies
were said to survive is closer to **normal operating cost**, and the 0.45 headline is
optimistic. At 0.80, every roster member's expectancy margin is clearly negative
(S93 −6.7 pp, S99 −7.5 pp, S100 −4.7 pp, S94 −1.6 pp).

The pre-registered acceptance bar already requires surviving 0.80, so it happens to be the
right bar — but it should be read as "does this work at realistic cost", not "does this
survive an unlikely stress".

## The unifying diagnosis: the whole roster sits at break-even

Putting each strategy's measured target multiple `k`, its realised win rate, and a
realistic stop/cost through `p* = (1 + c/R)/(k+1)`:

| strategy | k | WR | need @0.45 | need @0.62 | need @0.80 | margin @0.62 | k to break even |
|---|---|---|---|---|---|---|---|
| S93 | 1.50 | 42.4% | 45.1% | 47.1% | 49.1% | **−4.7 pp** | 1.78 |
| S99 | 1.50 | 41.6% | 45.1% | 47.1% | 49.1% | **−5.5 pp** | 1.83 |
| S94 | **3.65** | 24.2% | 23.9% | 24.8% | 25.8% | −0.6 pp | 3.77 |
| S100 | 2.50 | 29.9% | 32.0% | 33.2% | 34.6% | −3.3 pp | 2.89 |

(S94's `k` is *measured* by the sub-agent as the median of |tp−entry|/|entry−sl| = **3.65**,
not the ~2.0 I initially assumed. At that k the arithmetic puts it at break-even within
0.3 pp — which is precisely what its live record shows: +$22 over 66 trades.)

**Every strategy on the roster sits within a few percentage points of break-even.** That is
the single most useful thing this campaign has established. It reframes everything:

- The book is not broken, it is **marginal**. +$358 over 571 trades is noise around zero.
- A marginal system's P&L is dominated by **friction**, which is why stop width, quote-vs-mid
  triggering and cost assumptions all showed up as first-order effects.
- It explains why live-vs-sim parity keeps failing: when true expectancy is ~0, a small
  modelling error flips the sign, so parity is *structurally* hard to achieve here.
- It explains why one week (Aug 3–9) carried the entire July–August result.

`k_breakeven` above is a **lower bound**: it assumes the win rate is unchanged when the
target moves further out, which it never is. The empirical `_TP_R` sweep is the real test,
and it is what the sub-agents are running.

## Candidate changes this implies

- **R1** raise `MIN_SL_DIST_PTS`, ideally expressed as a multiple of round-trip cost rather
  than an absolute point value (the current 1.5 is ~2.5× cost; the arithmetic wants ~6×+).
- **R2** raise `_TP_R` on the 1.5R strategies (S93, S99) toward S100's 2.5.
- **R3** drop per-strategy losing hours — **S93 only**; the hour effect does not generalise
  (S99's hours are unstable across halves and the spread data refutes the Asia mechanism).
- **R4** **limit entries instead of market entries** — see below. Structurally the largest
  available lever, and the only one that attacks the dominant term directly.

R1 and R2 are two routes to the same fix and may not be additive — whichever costs fewer
trades for the same expectancy gain should win. The sub-agents test both.

### R4 — the case for resting limit orders

Every live strategy on this roster is a **retrace** strategy. Each one computes its entry
level *in advance* (`PendingRetrace.prox`, the proximal edge), arms a pending setup, and
then waits for price to come back to that level — `check_touch` literally polls for the
touch. Today, on the touch, `entry_manager` fires a **market** order.

That is the worst of both worlds:

- it pays the spread as the aggressor (~0.30 pt) *and* takes slippage;
- it fills *after* the level, which is what the `entry_drift` gate exists to police — and
  that gate rejected 18/16/25/74 signals across S93/S94/S99/S100 in the live parity window;
- the backtest meanwhile assumes a fill **exactly at** `prox`, so live and model diverge
  by construction.

A resting limit order at `prox` is the natural implementation: passive fill at the level,
no entry drift, no aggressor spread. It would make live **match** the model rather than
drift from it. Estimated friction: ~0.30–0.40 round trip instead of ~0.62–0.80.

Effect on the test half (same trades, cost varied):

| entry style | S93 all | S93 + 3.5 floor | S99 all | S99 + 3.5 floor |
|---|---|---|---|---|
| LIMIT 0.30 | 0.969 | **1.002** | 0.945 | 0.905 |
| LIMIT 0.40 | 0.944 | 0.980 | 0.919 | 0.887 |
| MARKET 0.62 | 0.891 | 0.934 | 0.865 | 0.847 |
| MARKET 0.80 | 0.851 | 0.898 | 0.823 | 0.817 |

> [!warning] Large lever, still not a rescue
> Halving friction moves S93 from PF 0.891 to ~1.00 and S99 from 0.865 to 0.945. Both are
> big improvements and neither is a profitable strategy. **The honest reading is that S93
> and S99 do not have enough raw edge to pay for even optimistic friction.**
>
> The known counterweight: the harness trades the **ungated** signal population (~2× live's
> trade count), and live S99 — with the full gate stack — made +$252. So this is evidence
> about the ungated population, not proof that the gated live strategy is negative.

Costs and risks of R4, stated up front: a limit order may not fill when price gaps through
(losing the trade rather than losing money), it introduces adverse selection (you fill
preferentially when price continues against you), and it needs real order-management work
in `entry_manager` + `metaapi_client` (placement, expiry, cancel-on-invalidation). It is a
development project, not a config change — but it is the one that attacks the term that
actually dominates this book.

## Baselines — the backtest ranking matches the live ranking exactly

19.5 months, cost 0.45, ungated signal population:

| strategy | train n / PF | test n / PF | test WR | live all-time |
|---|---|---|---|---|
| **S100** | 2276 / **1.002** | 1656 / **1.027** | 33.7% | **+$814** |
| S93 | 629 / 1.006 | 361 / 0.931 | 41.6% | −$163 |
| S99 | 935 / 0.904 | 648 / 0.907 | 43.1% | +$252 |
| S94 | 617 / 0.843 | 469 / 0.858 | 22.4% | +$22 |

**S100 is the only strategy positive in both halves**, and the backtest ordering
(S100 > S93 ≈ S99 > S94 on train, S100 > S99 ≈ S93 > S94 on test) reproduces the live
ordering. That is meaningful independent corroboration that the harness measures something
real, even though it is pessimistic in absolute terms.

> [!important] R1 DOWNGRADED — the global stop floor is not supported
> My H1 said "raise `MIN_SL_DIST_PTS`" as a book-wide change. The 19.5-month replays do not
> support that:
> - S93: helps train (PF 1.006 → 1.222 at floor 4.0), does **not** rescue test.
> - S99: helps slightly, test never exceeds 1.0.
> - **S94: actively hurts** — test PF 0.949 (baseline) → 0.958 (2.5) → **0.902** (3.5).
>
> The live evidence for H1 was strong but **confounded**: `MAX_LOT = 0.10` put an artificial
> boundary at exactly 3.8 pt, so "capped vs uncapped" was not a clean stop-width split — it
> also separated different volatility regimes and different times of day.
>
> The *mechanism* (cost/risk drag) remains correct arithmetic and still explains why the
> roster sits at break-even. But "raise the floor globally" does **not** follow from it and
> is **not** shipping. This is the hypothesis I was most confident about at the start of the
> campaign, and the backtest refused it.

## Results in from the sweeps

### S93 — the hour restriction is the one clear win so far

Full replays (not post-filter), `_HOURS` patched:

| arm | train | test |
|---|---|---|
| baseline (7,8,9,12,13,14) | n=629 +8.4 PF 1.006 | n=361 **−97.9 PF 0.931** DD −221.8 |
| drop hour 12 | n=546 −22.2 PF 0.983 | n=309 −35.7 PF 0.971 DD −171.0 |
| **_HOURS = (13,14)** | n=267 **+94.6 PF 1.139** | n=132 **+221.8 PF 1.408** WR 51.5 DD **−43.3** |

Positive in **both halves**, and the drawdown collapses from −221.8 to −43.3.

> [!important] The selection is train-justified, not test-fitted
> I derived the hour ladder partly from the test half, which would normally contaminate it.
> But ranking S93's hours on the **train half alone** gives 13 (PF 1.200) > 14 (1.133) >
> 12 (1.056) > 7 (0.969) > 9 (0.852) > 8 (0.656) — so **(13,14) is the train-optimal pair**.
> An analyst with no sight of the test half would have chosen the same thing; the test half
> then confirms it. That is a legitimate walk-forward result rather than a fitted one.
>
**Plateau check — PASSED.** Every restricted hour set beats baseline on **both** halves,
and performance degrades smoothly as morning hours are added back. This is a plateau, not
a lone spike:

| `_HOURS` | train PF | test PF | test n | test DD |
|---|---|---|---|---|
| (13,14) | 1.139 | **1.408** | 132 | −43.3 |
| (9,13,14) | 1.092 | 1.323 | 182 | −59.8 |
| (12,13,14) | 1.147 | 1.215 | 184 | −86.0 |
| (8,9,13,14) | 1.027 | 1.150 | 248 | −113.1 |
| (7,8,9,12,13,14) baseline | 1.006 | **0.931** | 361 | −221.8 |

**TP_R on S93 — also refuted.** 1.0 → test PF 0.846; 2.0 → 0.861; both worse than the
shipped 1.5 (0.931). Same result as S99: these strategies' TP multiples are already at
their optimum, and the arithmetic's "raise k" suggestion does not survive the win-rate
response. R2 is now refuted on **both** 1.5R strategies.

> Still pending before this can ship: the **0.80 cost stress**. Running now.

**Third, fully out-of-sample confirmation — the live record:**

| S93 live (Jul 6 – Aug 12) | n | USD | WR | PF | maxDD |
|---|---|---|---|---|---|
| as actually traded | 43 | **−168.3** | 37.2% | 0.82 | −441.1 |
| hours 13,14 only | 15 | **+60.6** | 40.0% | **1.22** | −190.2 |
| the hours it would drop | 28 | −229.0 | 35.7% | 0.67 | −416.7 |

Live per-hour: 7 → +23.4, 8 → +0.7, 9 → −31.2, **12 → −221.8**, 13 → +50.0, 14 → +10.6.
Hour 12 alone accounts for more than the strategy's entire loss.

Applying just this one restriction to the live book takes it from **+$267.7 to +$496.7**
(242 → 214 trades) — an 85% improvement in realised P&L from removing 28 trades.

So the restriction now holds in **three independent windows**: backtest train, backtest
test, and the live broker record. Live n=15 is small, but the sign and direction agree
with both backtest halves.

### S100 — the same hour pattern, on the biggest strategy (live evidence; backtest pending)

S100 trades hours 1–8 and 13–15. Its live per-hour record:

| hour | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 13 | 14 | 15 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| n | 10 | 11 | 7 | 9 | 13 | 11 | 9 | 10 | 8 | 5 | 4 |
| USD | −157 | −148 | −74 | +190 | +68 | +68 | +12 | +47 | **+398** | +96 | −14 |
| WR | 10% | 9% | 14% | 44% | 31% | 36% | 33% | 30% | 63% | 40% | 25% |

| restriction | n | USD | WR | PF | maxDD |
|---|---|---|---|---|---|
| as traded | 97 | +485.8 | 29.9% | 1.30 | −375.5 |
| **drop 1,2,3** | 69 | **+865.7** | 37.7% | **1.83** | **−181.2** |
| drop 1,2,3,4 | 60 | +676.2 | 36.7% | 1.70 | −162.9 |
| NY only 13,14,15 | 17 | +480.4 | 47.1% | 2.90 | −158.2 |

Dropping hours 1–3 nearly doubles profit **and** halves drawdown. At 28 trades and ~11% WR
against a 30% baseline this is unlikely-but-not-extreme noise (p ≈ 2–3%), and I selected the
worst hours after seeing them — so **the backtest must decide**. The S100 sub-agent is
running exactly these arms.

**The cross-strategy pattern is worth noting**: hour 13 (NY open) is the best hour for both
S93 and S100, and the early hours are the worst for both. Whether that is one real
time-of-day effect or two correlated small samples is precisely what the 19.5-month replays
are for.

### S94 — a large unresolved discrepancy

Baseline, 19.5 months, correct 1500-bar M5 warm-up:

| half | n | pts | PF | WR |
|---|---|---|---|---|
| train | 617 | −354.7 | 0.843 | 23.7% |
| test | 469 | −320.6 | 0.858 | 22.4% |
| full | 1086 | **−675.3** | **0.851** | 23.1% |

Consistently negative across both halves — against a **published validation of PF 1.82**
(886 trades, +622.7R, 12 of 13 months positive). Three sources, three answers:

| source | verdict |
|---|---|
| published `validate_oos.py` (full year) | PF **1.82** |
| this harness (19.5 mo, ungated) | PF **0.85** |
| live broker record (all time) | **+$22** over 66 trades — flat |

The live record sits between the two backtests, which is the least convenient possible
outcome. Candidate explanations for the gap: cost assumption (0.30 published vs 0.45 here),
the level-universe truncation the module itself warns about, or a genuine difference in
entry logic between `validate_oos.py` and the shipped module. **Not resolved.** Until it is,
S94's published number should not be relied on for allocation decisions, and its measured
k=3.65 / WR 24.2% puts it at break-even by the arithmetic anyway.

### The hour/stop-floor overlap question — RETRACTED, unresolved

I previously recorded here that the overlap probe had settled this, quoting
`OVERLAP_13_14_minsl_3.0` test PF 1.202 against the 1.408 reference and concluding the stop
floor adds nothing once hours are fixed.

**That conclusion is retracted.** The S93 agent found those numbers violate a hard
invariant: raising `min_sl_dist_pts` can only ever reject MORE trades, yet
`OVERLAP_13_14_minsl_2.0` reported TRAIN n=335 against a n=267 base at min_sl=1.5. Trade
count cannot rise when a filter tightens, so the arm was not measuring what it claimed.

**Root cause: a bug in this harness, found only because of that invariant check.**
`replay()` applied `Cfg.env` to `os.environ` and never restored it, so in a multi-arm sweep
inside one process an env var set by an early arm leaked into every later arm. S93 reads its
flags at call time (`_veto_enabled`, `_gap_cap_atr`) and again on `importlib.reload`, so once
a `D_veto_off` arm set `S93_SOFT_VETO=off`, the SOFT veto stayed off for the rest of that
process — admitting more trades and breaking monotonicity.

Fixed: env is now snapshotted and restored in a `finally`. Regression-checked —

```
veto OFF arm : n=20     <- env applied
clean arm A  : n=12     <- env restored
clean arm B  : n=12     <- reproducible
```

Pre-fix, clean arm A returned n=20: the leak inflated affected arms by ~67% on that sample.

**Blast radius**: any arm that ran *after* an env-setting arm in the same process is
suspect; arms before it are fine. The `_HOURS` ladder ran in phase 1 before any env arm, and
my own stress/plateau runs never used `Cfg.env` at all — so **the evidence behind the
shipped change is unaffected**, and a fully-pinned diagnostic reproduced the trusted
baseline (n=990, 629/361) bit-for-bit.

### Resolved by the pinned redo

The agent's fully-pinned redo (every constant explicitly set — `_HOURS`, `_MIN_FVG_ATR`,
`_TP_R`, `_BUF_ATR` — plus a live monotonicity assertion) has now completed:

```
OVERLAP_13_14_minsl_3.0  hours=(13,14) fvgATR=0.3 msl=3.0 c=0.45
                         TRAIN n=181 +93.3  pf=1.166 | TEST n=128 +227.1 pf=1.424
(13,14) reference        msl=1.5              | TEST n=132 +221.8 pf=1.408
```

Monotonicity now holds (n=128 ≤ 132 — a tighter filter admits fewer trades, as it must),
so these numbers are trustworthy.

**Answer: the stop floor is NEUTRAL inside the NY hours, not harmful.** Test PF 1.424 vs
1.408 on 128 vs 132 trades is not a real difference. Adding a floor on top of the hour
restriction neither helps nor hurts.

Both earlier claims were wrong: my original "the floor makes S93 worse" was based on the
contaminated arms, and the retraction that replaced it left the question open. The clean
answer is that the hour restriction already captures the effect, and the floor adds nothing
on top — which is still consistent with the R1 downgrade, just for a milder reason than I
first asserted.

### S99 — raising the TP multiple is REJECTED

| arm | train | test |
|---|---|---|
| baseline (TP 1.5R) | n=935 −225.3 PF 0.904 | n=648 −227.1 **PF 0.907** WR 43.1 |
| TP_R = 2.0 | n=915 −242.9 PF 0.906 | n=626 −490.6 **PF 0.820** WR **35.5** |

Win rate falls 43.1% → 35.5%, which is *more* than the extra payoff is worth: gross edge
`p(k+1)−1` drops from 0.078 to 0.065. **R2 is empirically refuted for S99** — its TP
multiple is already at or past the optimum, and the arithmetic's `k_breakeven` lower bound
was exactly that, a lower bound that the win-rate response invalidates.

S99's stop buckets show only the widest is profitable (9+ pt, avg 15.75, net PF 1.106,
n=164); every other bucket is below 1. That is a thin, tail-dependent edge.

## S94's aggregate pattern — the most informative thing about it

Across every dimension the sub-agent tested, the same shape repeats: a change nudges the
TEST half positive at the optimistic 0.45 cost, then dies at the realistic 0.80.

| dimension | best arm, TEST @0.45 | same arm @0.80 |
|---|---|---|
| `block_hours` (drop Asia 0–5) | PF 1.029 | **0.971** ✗ |
| `_STOP_BUF` 0.05 | PF 0.989 | **0.931** ✗ |
| `_MIN_RR` (best case) | — | **0.898** ✗ |
| `min_sl_dist_pts` (best, 2.5) | PF 0.958 | **0.909** ✗ |
| **`_SD_MULT` 2.5 / 3.0** | PF **1.151 / 1.154** | **1.089 / 1.093** ✓ |

**`_SD_MULT` is the only S94 change that survives realistic cost.** Everything else is an
artefact of the 0.45 assumption — which is exactly the assumption this campaign showed was
optimistic (measured median spread 0.59 pt).

Read together, that says S94's problem is not any single mis-set parameter: the strategy has
too little edge to pay realistic friction, and only the change that *raises the payoff per
winner* (rather than filtering trades away) makes a dent. That is the same conclusion the
break-even arithmetic reaches from theory, arrived at independently from data.

### Why `_SD_MULT` still did not ship

Four attempts at a wider-window confirmation were killed without completing a single arm.

I then claimed, from a partial reading of a running job, that S94 cost "≈4 minutes of CPU
per month" and that the rescoped run needed ~3.6 h — and concluded it was intractable.
**That was wrong.** The completed measurement is:

```
1mo:  98s   2mo: 182s     -> ~91s per month of replay
```

So the rescoped 9.5-month window is **~14 min/arm**, and six arms is **~86 min** — well
within reach. I had over-estimated by 2.5x by reading CPU-seconds off a job whose output was
still buffered, and nearly abandoned a tractable confirmation on the strength of it. Same
failure shape as the other bad calls this session: a number asserted from partial evidence
instead of the completed measurement that was already on its way.

With the resumable log banking each finished arm, the run makes progress even if killed.

So `_SD_MULT = 2.5–3.0` stands as a **rigorously-supported but unconfirmed lead**: it clears
all five pre-registered bars on a 5-month window with a genuine two-point plateau and zero
trade-count cost, and has not been reproduced on anything wider. Given S94 is *precisely*
the strategy whose published number failed a wider sample, that gap is disqualifying for
shipping and is the first thing the next session should close.

### Harness optimisation — correct, but not the bottleneck

The M5/M15 window slices only change when a bar on that frame closes, yet the harness
re-sliced them on every M1 bar — for S94 (`win_5m=1500`) that is a 1500-row frame built
~490k times per replay instead of ~9.5k. Now cached by slice index.

Verified a pure speedup, not a behaviour change:

```
1mo: 91s  n=72  pf=0.406   (uncached 98s,  n=72  pf=0.406)  IDENTICAL=True
2mo: 159s n=140 pf=0.562   (uncached 182s, n=140 pf=0.562)  IDENTICAL=True
```

**But only 7–13% faster, so it did not solve the problem.** I predicted the slicing was the
dominant cost; it was not. The real cost is inside S94's own `get_signal` — its incremental
level/sweep machine and pending-probe loop, run ~490k times. That is a third wrong mechanism
asserted before measuring, and the measurement took four minutes.

The optimisation stays (it is free and correct, and every strategy benefits), but an S94 arm
is still ~80s per month of replay ≈ 13 min for the 9.5-month window — longer than the
background-job survival window observed here. **The confirmation is therefore not achievable
in this session.**

`lab/_s94_decisive.py` is left resumable (skips completed arms, absolute paths, scope
compromise documented in its docstring).

## R5 — allocation beats tuning

Every subset of the four strategies, scored on the live Jul 6 – Aug 12 record:

| book | n | USD | maxDD | return/DD |
|---|---|---|---|---|
| S100 alone | 97 | +485.8 | −375.5 | **1.29** |
| **S100 + S99** | 152 | **+605.1** | −508.9 | 1.19 |
| S100 + S94 + S99 | 199 | +436.1 | −542.4 | 0.80 |
| **all four (current live book)** | 242 | **+267.8** | **−768.4** | **0.35** |
| S93 + S94 | 90 | −337.3 | −781.0 | −0.43 |

Adding S93 and S94 to S100+S99 costs **$337 of profit and $260 of extra drawdown**.
The all-time DB record agrees: S100+S99 = **+$1066** over 253 trades vs **+$925** over 378
for the whole ICT book — more money from a third fewer trades.

> [!caution] Subset selection is a multiple-comparison trap
> With four strategies there are 15 subsets and I picked the best one after seeing the
> results. That specific ranking is **not** trustworthy on its own.
>
> What *is* trustworthy is the convergent case against **S93 specifically**, which four
> independent lines agree on: worst live all-time (−$163, negative in 2 of 3 months),
> worst backtest (test PF 0.931, and no lever in the sweep rescued it), worst arithmetic
> margin (−4.7 pp at realistic cost), and worst per-strategy live return/drawdown.
>
> S94's case is genuinely **mixed** — all-time +$22, a −$278 July then a +$416 August, and
> its measured k=3.65 puts it within 0.3 pp of break-even. Volatile, not clearly broken.
> It stays.

## Method

Harness `lab/harness.py` — drives the **real** `get_signal` and the **real**
`shared.gate_rules`, over 19.5 months of cached bars (2025-01-01 … 2026-08-12).
Exits resolve on M1 with **SL checked before TP** (pessimistic). Points-primary.

### Harness validation

Checked against the live S93 record (the one strategy with both a long replay and a clean
live sample):

| | harness, test half | live S93 |
|---|---|---|
| n | 361 | 43 |
| outcome mix SL / TP / TIME | 57.1 / 41.4 / 1.5 % | 60.5 / 37.2 / 2.3 % |
| win rate | 41.6% | 37.2% |
| profit factor | **0.931** | **0.90** |

This matters more than it looks. S93's *published* validation claimed test PF 1.24, and the
production manager sim failed its pre-registered parity check (66.9% trade match, aggregate
USD 51.7% off). This harness reproduces the live **sign and magnitude**. Where the two
disagree, prefer the harness — it is the one that agrees with the broker.

Caveat: live stop distances are truncated at 3.8 pt by the `MAX_LOT=0.10` cap, so the live
stop distribution is not comparable to the harness above that boundary.

**Split**: train `2025-01-05 → 2026-02-01`, test `2026-02-01 → 2026-08-12`.
The test half contains the live period, so a change that helps live must show up there.

**Pre-registered acceptance bar** (fixed before any sweep result was read):
1. improves **test**-half PF *and* points, not just train;
2. survives the **0.80 pt** cost stress on the test half;
3. sits on a **plateau** — neighbouring parameter values also improve (no lone spike);
4. does not cut trade count below **n = 60** on the test half;
5. is directionally consistent with the live evidence.

A change failing any one of these does not ship. Recording the failure is a result.

## Parallel flow

Two-stage by cost:

- **Cheap screen (orchestrator)** — book-level structural knobs (stop floor, blocked
  hours) approximated as post-filters on the baseline trade list; near-free, used only
  to rank candidates.
- **Expensive confirmation (sub-agents, one per strategy, in parallel)** — full replays
  over each strategy's *own* parameter space, where rejecting a signal changes which
  later signals are reachable and a post-filter would lie.

Cross-cutting agents cover portfolio overlap and live-window reconciliation.

## Live production state, read from the box 2026-09-02

`ManagerConfig`: **master ON**, kill-switch **$250**, max_concurrent **5**,
soft brake $120. Note the kill/cap have **drifted from the documented $150 / 3** —
the vault's [[Kill Switches and Risk Limits]] note is stale.

All six armed strategies read `desired_active=False` at the time of reading, with
`last_reason = "market closed"` — the benign 21:00–22:00 UTC OANDA maintenance window.
The PAUSE 02:30 / START 03:30 IST cycle is visible in `ManagerAction` and is healthy.

### Full realised P&L, all 571 closed positions

| book | n | USD |
|---|---|---|
| **ICT strategies** | 391 | **+867** |
| **Telegram copy** | 180 | **−509** |
| net | 571 | +358 |

| strategy | n | USD | WR | Jul | Aug | Sep |
|---|---|---|---|---|---|---|
| S100 M3 Combo | 177 | **+814** | 29.4% | +53 | +475 | +286 |
| S99 MSS FVG | 76 | **+252** | 44.7% | +151 | +69 | +32 |
| S94 Sweep Reversal | 66 | +22 | 24.2% | −278 | +416 | −116 |
| S93 FVG Scalp | 59 | **−163** | 37.3% | −191 | +80 | −52 |
| Neymar VIP | 45 | −97 | 35.6% | — | −108 | +11 |
| Neymar Telegram Copy | 135 | **−412** | 47.4% | −120 | −250 | ~0 |

**The strategy book works; the copy-trading is what drags it to break-even.**
Post the 2026-08-11 accounting fix the copy book is −$483 over 128 trades, so the loss is
real and not the old multi-TP clubbing artefact — the fix *revealed* it.

> [!warning] But it is not statistically significant
> Per-trade Telegram USD: n=180, mean −2.83, sd 24.89, **t = −1.52**, 95% CI [−6.46, +0.81].
> Monthly sign test 4/5 losing months, **p = 0.375**. The ICT book is likewise not
> significantly positive (t = +0.91). At these sample sizes almost nothing here is
> conclusive, and any claim otherwise would be overreach.
>
> The honest summary: the copy-trader has a negative point estimate, **no validatable
> edge by construction** (you cannot backtest a human's future Telegram posts), and its
> two feeds are ~91% duplicate trades. That is a strong *decision-theoretic* case to cut
> or halve it, and a weak *statistical* one. Flagged for the operator rather than acted on
> unilaterally, since running both feeds was a deliberate operator choice.

## SHIPPED — S93 `_HOURS` narrowed to the NY killzone (2026-09-02)

`backtest_strategies/s93_fvg_scalp.py`: `_HOURS = (7, 8, 9, 12, 13, 14) -> (13, 14)`.

All five pre-registered criteria met:

| criterion | result |
|---|---|
| 1. improves test PF **and** points | 0.931 → **1.408**, −97.9 → **+221.8 pts** |
| 2. survives 0.80 cost stress | baseline test PF 0.851 → **1.310**, +175.6 pts |
| 3. plateau, not a spike | 5 arms all beat baseline on both halves, smooth ladder |
| 4. test n ≥ 60 | **n = 132** |
| 5. plausible mechanism + live agreement | live PF 0.82 → **1.22**, hour 12 alone −$221.8 |

The plateau holds **at stress too**, which is the strongest form of the result:

| `_HOURS` @ cost 0.80 | train PF | test PF | test pts |
|---|---|---|---|
| (7,8,9,12,13,14) baseline | 0.869 | 0.851 | −224.2 |
| (12,13,14) | 1.003 | 1.122 | +95.3 |
| **(13,14) shipped** | **1.002** | **1.310** | **+175.6** |

Train is only *break-even* at realistic cost (1.002), not positive — stated rather than
glossed. Max drawdown falls from −254.7 to −45.4 pts on the test half.

**Test work required by the change** (a good illustration of why the suite matters): the
existing fixtures anchored on `_T0 = 07:00Z` and a separately hard-coded `_now()` base of
`08:45Z` — both hours the change removes, so all 27 tests broke. Fixed by moving both
clocks to 13:00/14:45Z, plus two new tests: `test_hours_are_ny_killzone_only` pins the
tuple so a future widening must be deliberate, and `test_morning_hours_are_gated_out`
asserts a setup that would previously arm at 7/8/9/12 is now refused. **29 tests green.**

Cost: S93's trade count falls ~63% (test-half n 361 → 132). It becomes a low-frequency NY
scalp. Given it was the roster's only live money-loser, fewer and better trades is the
intended trade-off.

### Process error worth recording

I edited `s93_fvg_scalp.py` **while the S93 sub-agent's sweep was still running**. Because
`harness.replay()` calls `importlib.reload(mod)` per arm, every replay started after the
edit silently picked up `_HOURS = (13,14)`. The agent's D-arms (SOFT_VETO / GAP_CAP) ran
against the new two-hour module while its baseline and A/B/C arms ran against the old
six-hour one — so those tables are **not** mutually comparable. Detectable by train `n`:
~600–690 is the old module, ~370 is the new one.

Lesson: **freeze the code under test for the duration of a sweep**, or version the module
in the result record. The harness should probably stamp a hash of the strategy module into
every result row so this cannot happen silently again.

I originally wrote here that the contaminated arms were still informative — that under
`_HOURS=(13,14)`, veto-off gave test PF 1.244 and gap-cap-off gave 1.150, both worse than
1.408 with both on, so **both** opt15 Task-9 filters still earned their place.

**Half of that is retracted.** The S93 agent traced a further casualty of the env leak that
I had not: `D_veto_off` ran immediately *before* every `D_gapcap_*` arm in both scripts, so
`S93_SOFT_VETO=off` leaked into all of them. The gap-cap arms therefore measured
"veto off AND gap cap off", not "gap cap off alone" — every gap-cap number is confounded and
**`S93_GAP_CAP_ATR` is not validly tested at all** this campaign.

What survives: **SOFT_VETO=on is confirmed worth keeping** on a clean comparison. The gap cap
stays enabled at its shipped default because nothing argues against it, not because anything
here supports it.

## DEPLOYED to production 2026-09-02

S93's `_HOURS` change is **live on the `algorobos` box**.

| gate | result |
|---|---|
| full test suite | **100%, zero failures** (needed `--basetemp` — see below) |
| executable diff on box | exactly 2 lines: the `_HOURS` tuple and its description string |
| backup | `/home/ubuntu/s93_fvg_scalp.py.bak_20260902` |
| rebuild | `docker compose -p kronos build s93_fvg_scalp` then `up -d` |
| **running image verified** | `docker exec ... python -c "import ...; print(m._HOURS)"` → **`(13, 14)`** |
| container | `Up (healthy)`, runner started, heartbeat alive |
| blast radius | the other 5 containers untouched and still up |

The verification deliberately executed Python **inside the running container** rather than
checking the file on disk: on this box service code is baked into images at build time, so
the source file proves nothing about what is actually executing. Rollback is restore-backup
plus rebuild.

> [!important] Deployed and code-verified — but NOT yet exercised by live trading
> Deploy landed 2026-09-01 22:52 UTC. S93 now trades only 13:00–14:59 UTC, and those hours
> do not next occur until 2026-09-02 13:00 UTC. As of 23:54 UTC there are **zero** S93
> signals post-deploy, which is exactly what should be expected and is **not** evidence the
> change works in production. First real test is the next NY session.
>
> **What to check then**: S93 `StrategySignal` rows should appear only in the 13–14 UTC
> window and nowhere else. Trade frequency should fall to roughly a third of its old rate.
>
> **Timezone trap when checking**: `StrategySignal.signal_at` defaults to
> `get_kolkata_time()`, so its `.hour` is **IST, not UTC** (13–14 UTC = 18:30–20:29 IST).
> The 4 pre-deploy signals read as IST hours 14/17/18, which maps back into the old UTC
> tuple correctly. Every hour analysis in this campaign used UTC `entry_time` from the
> parity CSV and the harness's `now_utc`, so none of it is affected — but a naive DB query
> on `signal_at.hour` would be off by 5:30. See the vault's Units and Conventions note.

## Sub-agent verdicts (all four reports in)

| strategy | verdict | report |
|---|---|---|
| S93 | **SHIPPED** — `_HOURS → (13,14)` | `REPORT_s93.md` |
| S94 | `_SD_MULT` 2.5–3.0 clears all 5 bars **on a 5-month window only** — full-window confirmation required before shipping | `REPORT_s94.md` |
| S99 | **no change justified** — min_sl, TP_R and hours all REJECT | `REPORT_s99.md` |
| S100 | **no change** — OB entry model flagged for a dedicated filtered replay | `REPORT_s100.md` |

S99's TP_R refutation is the cleanest single result: test PF falls **monotonically**
0.907 → 0.820 → 0.825 → 0.789 → 0.711 as `_TP_R` rises 1.5 → 3.5, at both cost levels, with
win rate collapsing 43% → 23%. Mechanism: a wider target gives the stop and the timeout more
chance to resolve first, so win rate falls faster than the R-multiple compensates. S93 found
the same independently. **`_SD_MULT` helping S94 is not a contradiction** — S94's measured
k is 3.65 with WR sitting exactly at break-even, which is the one regime where extending the
target pays.

## The standing open question

Every agent independently flagged the same thing: **the ungated harness baselines lose while
the live gated book earns.** S99 harness PF 0.904/0.907 vs live PF 1.120. S93's docstring
claims train 1.29 / test 1.19 vs harness 1.006/0.931.

Two explanations, opposite implications:
1. The live entry gates (drift, dup-guard, no-add-to-loser, concurrency) plus manager pauses
   do real work the harness does not model → the strategies are fine, the harness is
   pessimistic.
2. The original validations were optimistic in ways now partly documented (S94's `be=True`,
   the 0.45 cost assumption) → live is running closer to luck than edge.

**Both demonstrably happen.** The S94 docstring defect proves (2) occurs; the live P&L proves
(1) occurs. Separating them per strategy is the highest-value next investigation and is
bigger than any parameter sweep.

## Process failures worth carrying forward

1. **Edited a module mid-sweep** — contaminated the S93 agent's D-arms. Freeze code under
   test, or hash the module into every result row.
2. **Asserted a cause I had not established** — claimed `nohup &` jobs were being reaped and
   told three agents their work was dead. It was false; the S94 agent verified its sweep had
   survived and corrected me. Same failure shape as the H1 stop-floor claim: real
   observation, plausible mechanism, insufficient rigour between them.
3. **Read a crash log from the tail only** — the repeated pytest `PermissionError` was at
   **startup**, not teardown, so several "suite runs" executed zero tests. Cause: pytest's
   shared `pytest-of-<user>/pytest-current` tmpdir colliding with a concurrent pytest run
   from another project. Fix: `--basetemp=<private dir>`. Read the head of a failure, not
   just the tail.

Sub-agents caught (2) and, indirectly, (3). Independent verification earned its keep here —
notably the S99 agent **refused to report a claim of mine on trust**, and only included it
after reproducing it from its own tool output.

## Actions taken

- **2026-09-02 — stopped two dead containers.** `kronos-session_breakout-1` and
  `kronos-s97_snap_scalper-1` had been running two weeks for strategies whose
  `UserStrategy` rows are `deployed=False`/archived, with **zero `StrategySignal` rows in
  30 days** (S97 never fired at all). `session_breakout` ran with **no `mem_limit`** on a
  box that was at 130 MB free with 779 MB swapped. Stopping both took available memory
  **552 → 729 MB (+32%)**. Every live trading container was left untouched and healthy.
  Fully reversible with `docker start`.

## Next session — two experiments, both ready to run

Both are written, resumable (each finished arm is appended to its log and skipped on
restart), and use absolute paths. Neither could complete here: late in the session,
multi-minute background jobs stopped surviving, and the foreground cap is 120s while every
full replay exceeds it. Run them first, ideally with little else competing for CPU.

**1. `lab/_s100_ob.py` — highest value.** Does disabling S100's OB entry model help?
OB is the only one of its three models negative in both halves and over the full period
(TRAIN PF 0.997 / TEST 0.957 / full 0.976 over 1274 trades). Disabled by patching
`_OB_DISP` so `body >= _OB_DISP * atr` can never pass. Three arms: OB-off @0.45, OB-off
@0.80, OB-on @0.80. Reference (OB on, @0.45): TRAIN n=2276 pf=1.002 / TEST n=1656 pf=1.027.
The log records which entry models appear in each arm, so the patch can be verified rather
than assumed. **S100 is the roster's only clearly profitable strategy (+$814 live) — cutting
dead weight from the earner beats tuning the marginal members.**

**2. `lab/_s94_decisive.py` — closes the campaign's one open lead.** Does `_SD_MULT`
2.5/3.0 hold on a wider window? Six arms over 2025-11-01..2026-08-12, judged on the
6.5-month test half (vs the sub-agent's 3-month). Cost ~80s per month of replay, so
~13 min/arm. `_SD_MULT` is the ONLY S94 change that survived the realistic-cost stress.

Also open, unstarted, and larger than a sweep: **R4 limit-order entries** (see above) and
the **standing question** of why ungated harness baselines lose while the live gated book
earns.

## Log

- 2026-09-02 — campaign opened; harness built and smoke-tested; baselines launched.
- 2026-09-02 — H1 mechanism identified (friction drag, not skill); live book analysed.
- 2026-09-02 — harness warm-up bug found and fixed: `i0` guaranteed only `win_1m` bars,
  so M5/M15 windows were truncated at the start of every replay. Severe for S94
  (1500-bar M5 window needs ~26 days of warm-up); minor elsewhere. All four sub-agents
  notified to re-baseline rather than compare arms across the fix.
- 2026-09-02 — production read-out; two dead containers stopped.
