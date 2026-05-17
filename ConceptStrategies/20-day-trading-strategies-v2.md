# 20 Day-Trading Strategies — v2 (opus-reviewed, with fine-tune parameters)

> **Status:** v2. Each strategy has been critically reviewed by an Opus subagent for edge survivability, falsifiability, look-ahead bias, decay risk, hidden costs, and infrastructure realism. Every strategy now ends with a **Fine-tune parameters** table specifying defaults, search ranges, and what each parameter test should measure. The v1 catalog is preserved in git history (commit prior to this one) for comparison.

A practitioner's reference catalog of 20 unique intraday strategies, each grounded in one or more of the 11 trading-knowledge pillars (`skills/INDEX.md`). All strategies are day-trades: positions open and close within the same session. No swings, no overnights.

## What v2 changed (high-level)

| Part | Key v2 fixes |
|------|--------------|
| A (Structure/ICT) | Removed retroactive HTF-bias filters; ATR-relative thresholds replace fixed pip values; tightened BOS look-ahead; added strategy-disabled-when kill switches |
| B (Volume Profile) | Real-time day-type proxies replace post-hoc Dalton labels; 80%-VA rule demoted from given to testable hypothesis; profile/VWAP object versioning specified |
| C (Event/Macro) | Pre-FOMC drift reframed with decay narrative + regime filter; CPI fade split by surprise magnitude; NFP gets revision-disable; calendar blackout standing rule |
| D (Quant/Order-flow) | Family-wise multiple-testing disclosure + standing transaction-cost block; squeeze + ORB sample-period dependency surfaced; pairs MR cointegration kill-switch; footprint infra requirements made explicit |

## How to use this document

Each strategy specifies:

- **Concept** — one-line description
- **Edge sources** — which pillar(s) supply the edge and why
- **Why it has edge** — the market mechanic being exploited (what other participants are doing)
- **Entry model** — instrument, timeframe, setup conditions, trigger, invalidation
- **Position management** — initial stop, risk per trade, scaling rules
- **Exit model** — targets, trailing, mandatory time stop
- **Strategy disabled when** — explicit kill switches (added in v2)
- **Confluence pairings** — which other strategies in this catalog stack with this one and why
- **Fine-tune parameters** — testable parameters with defaults, search ranges, and optimization targets

No single strategy is a permanent edge. Stacking 2–3 aligned signals from different pillars compounds probability. See `skills/trading-knowledge-map/SKILL.md` for the confluence rule.

## Catalog organization

| Part | # | Edge Family |
|------|---|-------------|
| A | 1–5 | Structure & ICT/SMC intraday (pillars 01, 04) |
| B | 6–10 | Auction / volume profile / VWAP intraday (pillars 02, 04) |
| C | 11–15 | Event-driven / news / macro intraday (pillars 05, 06, 11) |
| D | 16–20 | Quant / order-flow / cross-asset intraday (pillars 04, 09) |

## Strategy index

| # | Name | Primary instruments |
|---|------|---------------------|
| 1 | Asian Range Sweep + London Open Reversal | XAUUSD, FX majors |
| 2 | Opening 15-Min Liquidity Sweep + Retest | XAUUSD, ES, FX |
| 3 | 5-Minute Fair Value Gap Fill | XAUUSD, ES, FX |
| 4 | Order Block Retest After London BOS | XAUUSD, FX |
| 5 | Power-of-Three (AMD) Distribution Leg | XAUUSD, FX |
| 6 | Prior-Day VAH/VAL Fade | ES, NQ, NIFTY |
| 7 | Naked POC Magnet | ES, NQ |
| 8 | VWAP Mean Reversion in Balance Days | ES, NIFTY |
| 9 | Open-Drive Trend Day Continuation | ES, NQ |
| 10 | Initial Balance (IB) Breakout | ES, NQ |
| 11 | Pre-FOMC Drift | ES, SPY |
| 12 | Post-CPI Fade | ES, XAUUSD |
| 13 | NFP Volatility Expansion Breakout | ES, XAUUSD |
| 14 | London-NY Session Overlap Vol Trade | ES, EURUSD |
| 15 | RBI Policy Reaction Trade | BANKNIFTY |
| 16 | ATR Squeeze Breakout (5m) | any liquid intraday |
| 17 | Opening Range Breakout (ORB) Momentum | ES, NQ, NIFTY |
| 18 | Footprint Absorption Reversal | ES, NQ (DOM required) |
| 19 | Intraday z-score Mean Reversion (Pairs) | EURUSD/GBPUSD, NIFTY/BANKNIFTY |
| 20 | Gold-DXY Intraday Divergence | XAUUSD + DXY |

## Confluence quick-reference

- **Intraday reversal stack:** 1 + 18 + 12 — Asian sweep, footprint absorption confirmation, post-event positioning unwind
- **Breakout continuation stack:** 10 + 17 + 16 — IB breakout, ORB momentum, ATR squeeze release in alignment
- **Event-day caution stack:** 11/12/13 disable 14, 16, 19 — event days break intraday vol assumptions of mechanical setups
- **Cross-asset confirmation stack:** 20 + 5 + 12 — gold-DXY divergence on CPI distribution day
- **Mean-reversion stack:** 6 + 8 + 19 — VAH/VAL fade on balance day with VWAP reversion and statistical extreme

## Risk discipline (applies to every strategy)

- Day-trade risk per name: 0.2–0.5% of account equity (v2 tightened several strategies to 0.2%)
- Hard daily loss cap: 1.5% (stop trading for the day if hit)
- Max simultaneous correlated positions: 2
- Every strategy has a **mandatory time stop** — no holding overnight under any circumstance, including unrealized winners
- Calendar-event blackout: do not trade mechanical setups (14, 16, 17, 19) within ±24h of Tier-1 events (FOMC, CPI, NFP) — those are handled by strategies 11–13 explicitly
- Spread guard: skip setup if current bid-ask spread > 3× rolling median for the instrument
- See `skills/pillar-08-risk-position-sizing/SKILL.md` for sizing detail
- See `skills/pillar-07-trading-psychology/SKILL.md` for tilt control after losses

## How to use the Fine-tune tables

Each strategy ends with a parameter table. Workflow:

1. Implement the strategy with defaults in a paper or sim environment.
2. Pick 1–2 parameters at a time and walk-forward test across the search range.
3. Optimize against the **What to measure** column — not against P&L alone (P&L overfits; expectancy, stop-out-before-BE rate, T1 conversion are more honest).
4. Re-validate on held-out data. If the optimum collapses out-of-sample, the parameter is overfitting noise — keep the default.
5. Track family-wise error: optimizing 10 parameters on the same dataset inflates the apparent edge. Use the multiple-testing correction notes in Part D's preamble.

See `skills/pillar-09-statistical-quant-thinking/SKILL.md` and `backtest-expert` skill for the full methodology.

---

# Part A — Structure & ICT Intraday Strategies (1-5)

*Reviewed and refined.*

## 1. Asian Range Sweep + London Open Reversal

**Concept (1 line):** At London open, price sweeps the Asian session high or low, fails to hold the breakout, and reverses back into the Asian range — entry is taken on the reclaim, not the sweep itself.

**Edge sources:** pillar-01-market-structure (failed-breakout-then-reclaim is a documented mean-reversion mechanic that is detectable in real time, not retroactively); pillar-04-volume-orderflow-liquidity (stops resting at obvious overnight extremes get triggered into thin London-open liquidity; once they are absorbed, the directional fuel for the reversal is mechanical, not narrative).

**Why it has edge (2-3 sentences):** Overnight ranges accumulate stop orders at visually obvious extremes because most participants — discretionary and systematic — anchor stops to recent highs/lows. The London open is the first session where book depth is sufficient to fill those stops in size, which mechanically produces a wick excursion beyond the range. If that excursion fails to attract continuation flow (no follow-through volume, candle reclaims into the range), the trapped breakout participants become the counter-flow that funds the reversal leg back to the opposite Asian extreme.

### Entry model
- **Instrument & timeframe:** XAUUSD, EURUSD, or GBPUSD — 15m bias / 5m execution.
- **Setup conditions:**
  1. Asian session defined as 19:00–02:00 London time (adjust to DST). Range must be at least 0.5x the prior 20-day Asian-range ATR to ensure meaningful liquidity is stacked at the extremes; if the Asian range is below this floor, skip the day.
  2. Between London open (07:00 London) and 09:30 London, price prints a 5m candle whose wick pierces the Asian high or low by at least 0.15x the Asian range, but whose body closes back inside the Asian range.
  3. The very next 5m candle does not make a new extreme beyond the sweep wick (no continuation).
  4. An Order Block (last opposing-color candle before the reversal impulse) is identifiable on the 5m chart within 0.20x Asian-range distance of the swept extreme.
  5. HTF context: H4 must not be in an active impulse leg in the sweep direction (no H4 BOS in sweep direction within the prior 8 hours). This is a real-time filter, not "HTF bias agrees in retrospect."
- **Trigger:** Limit order at the 5m OB midpoint placed immediately after condition 3 is met. Cancel the limit if not filled within 30 minutes.
- **Invalidation:** Either (a) a 5m close beyond the sweep wick extreme in the sweep direction, or (b) the limit goes unfilled within 30 minutes — both kill the setup.

### Position management
- **Initial stop:** 0.10x Asian range beyond the sweep wick extreme. Account for typical XAUUSD London-open spread widening (test 1.5–3x normal spread in slippage model).
- **Risk per trade:** 0.25–0.5% of account.
- **Scaling:** No add. Single entry at the OB.

### Exit model
- **Targets:** T1 = 50% midpoint of the Asian range. T2 = opposing Asian session extreme. Use the structural levels; do not override with R-multiples.
- **Trailing / stop adjustment:** At T1, close 50% and move stop to breakeven plus spread. Trail remainder under the most recent 5m swing low (longs) or above 5m swing high (shorts).
- **Time stop:** Flat by 12:00 London. If T1 has not been touched within 2 hours of entry, exit at market — the mean-time-to-T1 in the working hypothesis is 45–90 minutes; beyond that the reversal thesis is decaying.

### Strategy disabled when
- High-impact red-folder news (CPI, NFP, ECB, BoE policy) is scheduled within 30 minutes of London open.
- Day is a holiday in London, Tokyo, or New York (thin Asian liquidity invalidates the stacked-stops premise).
- Asian range exceeds 1.5x its 20-day ATR (a wide Asian range often means London open is the continuation of an overnight trend, not a reversal opportunity).

### Confluence pairings
- Stacks with strategy #2 when the sweep occurs within the first 15 minutes of London open — same entry zone, two independent triggers.
- Stacks with strategy #4 once the post-sweep BOS forms — the OB that created the BOS often coincides with the sweep-zone OB.
- Note: overlaps with volume-profile fade strategies (likely in Part B); when sweep extreme coincides with prior-day VAH/VAL, only take the trade once; do not double-count as two independent edges.

### Fine-tune parameters

| Parameter | Default | Search range | What to measure |
|-----------|---------|--------------|-----------------|
| Asian session window (London time) | 19:00–02:00 | 18:00–01:00, 20:00–03:00, 22:00–02:00 | Expectancy and trade count per regime |
| Min Asian range (vs 20d ATR) | 0.5x | 0.3x–1.0x in 0.1 steps | Win-rate lift vs trade-count loss |
| Sweep wick penetration (vs range) | 0.15x | 0.05x–0.30x | Fraction of "false sweeps" filtered |
| Reclaim confirmation candles | 1 (next candle no new high) | 1, 2, 3, or 5m close-back-inside | Stop-out rate before T1 |
| OB distance from sweep | 0.20x range | 0.10x–0.40x | Fill rate vs adverse excursion |
| Stop buffer beyond wick | 0.10x range | 0.05x–0.25x | Stop-out frequency at fixed 1R |
| Limit-order expiry | 30 min | 15, 30, 45, 60 min | Selection bias on filled trades |
| T1 location | 50% Asian range | 25%, 50%, 75% | Expectancy and BE-rate |
| Time stop after entry | 120 min | 60, 90, 120, 180 min | Median time-to-T1; tail-trade contribution |
| HTF block window (H4 BOS lookback) | 8 hours | 4, 8, 12, 24 hours | False-positive rate of reversal entries |

---

## 2. Opening 15-Min Liquidity Sweep + Retest

**Concept (1 line):** The first 15-minute candle of London or NY open sweeps overnight stops and reverses; entry is on the retest of the sweep candle's body after a 5m structure break confirms reversal.

**Edge sources:** pillar-01-market-structure (a structure break following an external-liquidity raid is a higher-quality signal than one inside a range, because the swept stops are an identifiable causal driver of the break); pillar-04-volume-orderflow-liquidity (the opening 15m bar typically carries the day's first significant volume cluster; a retest into its body is a re-entry into the highest-volume opening zone).

**Why it has edge (2-3 sentences):** Session opens force participants who held positions overnight to either defend or unwind, and many use the prior session extreme as their decision level. A first-15m candle that sweeps that extreme and reverses indicates the unwind direction was the dominant flow, while late breakout participants from the wick are now trapped. The retest of the sweep candle body is the lowest-risk re-entry because the structural invalidation (the wick high/low) is well-defined and proximate.

### Entry model
- **Instrument & timeframe:** EURUSD, GBPUSD, XAUUSD, NAS100 futures — 15m for setup / 5m for execution.
- **Setup conditions:**
  1. Prior session high and low are marked before the open. For London open, "prior session" = previous NY session (15:00–22:00 London). For NY open, "prior session" = overnight including London (00:00–14:30 NY).
  2. The first 15m candle of London open (08:00–08:15 London) or NY open (09:30–09:45 NY) prints a wick that penetrates the prior session high or low by at least 0.10x the prior session's range.
  3. That same 15m candle closes back inside the prior session range.
  4. On the 5m chart, within the following 60 minutes, price prints a close beyond the most recent 5m swing in the reversal direction (BOS confirmation). The "most recent 5m swing" is defined as a fractal pivot of at least 3 bars left and right, fixed at the time of the sweep — not redrawn later.
  5. No conflicting H4 impulse in the sweep direction in the prior 8 hours.
- **Trigger:** After the BOS, limit order at 50% of the 15m sweep candle's body, valid for 90 minutes from BOS time.
- **Invalidation:** Either (a) a 5m close beyond the sweep candle's wick extreme in the sweep direction, or (b) limit expires unfilled.

### Position management
- **Initial stop:** Beyond the 15m sweep candle wick extreme, plus a buffer of 0.05x prior session range. Slippage assumption: 1.5x normal spread at retest fills, 2–3x if filled within 5 minutes of the open.
- **Risk per trade:** 0.25–0.5%.
- **Scaling:** No add.

### Exit model
- **Targets:** T1 = opposing prior-session extreme. T2 = next HTF liquidity pool in the same direction, identified at entry time and held fixed.
- **Trailing:** Close 50% at T1, stop to breakeven. Trail remainder under each new 5m higher-low (longs) / above each new 5m lower-high (shorts).
- **Time stop:** London setups closed by 12:00 London; NY setups by 14:00 NY. If T1 untouched within 2 hours of entry, exit at market.

### Strategy disabled when
- Scheduled high-impact news within 15 minutes of the chosen open.
- Half-day (early-close) sessions or day-before-holiday tape.
- Prior session range was less than 0.5x its 20-day ATR (thin liquidity; sweep premise unreliable).
- Wider-than-normal opening spread on the instrument (test threshold: >2x median spread for that minute over prior 30 days).

### Confluence pairings
- Strategy #1 when the prior-session sweep is also an Asian-range sweep.
- Strategy #5 — the 15m sweep is the manipulation leg of the AMD cycle.
- Caution: overlap with opening-range / ORB strategies (likely Part D); the directional thesis is opposite, so do not treat as confirmation — treat as regime-dependent.

### Fine-tune parameters

| Parameter | Default | Search range | What to measure |
|-----------|---------|--------------|-----------------|
| Open-bar window | 15 min | 5, 10, 15, 30 min | Sweep frequency vs reversal quality |
| Prior-session definition | NY session for London / overnight for NY | Two alternatives each | Trade count and edge consistency |
| Min wick penetration | 0.10x prior range | 0.05x–0.25x | Reversal hit-rate vs sample size |
| BOS swing-pivot length | 3-bar fractal | 2-bar, 3-bar, 5-bar | Fraction of BOS signals that hold |
| BOS confirmation window | 60 min | 30, 60, 90, 120 min | Selection bias on late confirmations |
| Limit at sweep body | 50% body | 25%, 50%, 75%, near-wick | Fill rate vs adverse excursion |
| Stop buffer beyond wick | 0.05x prior range | 0.02x–0.20x | Fraction of stops hit before BE move |
| T1 holdout vs partial | 100% at T1 | 50/50 split, 33/67, 100% | Expectancy and variance |
| Time stop after entry | 120 min | 60, 90, 120, 180 min | Median time-to-T1 |
| Spread filter (vs median) | 2.0x | 1.5x, 2.0x, 3.0x | Trade exclusion impact on net edge |

---

## 3. 5-Minute Fair Value Gap (FVG) Fill

**Concept (1 line):** An unfilled 5m FVG inside an impulse leg is a price-inefficiency zone; entry is on price returning to and reacting from that zone, in the direction of the impulse that created it.

**Edge sources:** pillar-01-market-structure (an FVG inside a confirmed impulse is structurally significant only if the impulse itself broke a prior swing — otherwise the gap is range noise); pillar-04-volume-orderflow-liquidity (a three-bar gap implies a one-sided print with little two-way auction at those prices; mean-reversion to the inefficiency is a documented short-horizon tendency that can be backtested directly).

**Why it has edge (2-3 sentences):** When price impulses with little overlap between candles, intermediate prices are "skipped" with minimal volume traded — a measurable inefficiency. Empirically (worth verifying per instrument), a meaningful fraction of these gaps gets at least partially revisited within a short horizon, especially in trending sessions where the same flow that produced the impulse provides a reload at better prices. The trade is the reaction off the gap, not the gap formation itself, which keeps the rule falsifiable.

### Entry model
- **Instrument & timeframe:** EURUSD, GBPUSD, XAUUSD — 5m for identification and execution.
- **Setup conditions:**
  1. Impulse precondition: a 3-candle 5m sequence where the total range is at least 1.2x the 20-bar 5m ATR, and the sequence broke a 5m swing high (bullish) or low (bearish) fixed at sequence start.
  2. FVG present: candle1.high < candle3.low (bullish FVG) or candle1.low > candle3.high (bearish FVG).
  3. Gap size at least 0.3x current 5m ATR (filters microscopic gaps that are noise).
  4. Gap forms during a kill zone: London 08:00–11:00 London or NY 09:30–12:00 NY.
  5. H1 trend filter: 20-EMA slope on H1 agrees with FVG direction over the prior 4 hours (this is a real-time, non-look-ahead filter; do not redefine "HTF bias" after the fact).
  6. Gap is unfilled — no prior 5m candle has closed beyond the far edge of the gap.
- **Trigger:** Price enters the gap and prints a 5m candle whose close is back outside the gap in the impulse direction. Enter at that close, market order.
- **Invalidation:** A 5m candle closes through the far edge of the gap (full gap fill with close-through) before the trigger candle prints.

### Position management
- **Initial stop:** Beyond the far edge of the FVG, plus 0.10x 5m ATR buffer.
- **Risk per trade:** 0.25–0.5%.
- **Scaling:** No add on standalone 5m setups.

### Exit model
- **Targets:** T1 = the impulse leg's swing extreme (the high/low the impulse created). T2 = next 5m or H1 swing in the trade direction, fixed at entry time.
- **Trailing:** 50% off at T1, stop to breakeven. Trail remainder under most recent 5m swing.
- **Time stop:** Close within 90 minutes of entry; close all positions by 12:00 London (London setup) or 14:00 NY (NY setup). The 5m timeframe's edge decays quickly outside kill zones.

### Strategy disabled when
- Realized 5m ATR over prior 60 minutes is below 0.5x its 20-day average for that time-of-day bucket (dead-tape regime; gaps don't get filled with conviction).
- High-impact news within 15 minutes of the trigger.
- Instrument is in the first 5 minutes of a session open (use strategies #1/#2 instead; opening-bar gaps have different mechanics).

### Confluence pairings
- Strategy #4 — the BOS impulse that triggers strategy #4 often leaves the same FVG.
- Strategy #1 — reversal impulses off Asian sweeps frequently leave a 5m FVG that is the precise re-entry zone.
- Caution: 5m FVGs are common; do not treat every gap as a setup. Without the impulse + HTF filter, this degrades into noise trading.

### Fine-tune parameters

| Parameter | Default | Search range | What to measure |
|-----------|---------|--------------|-----------------|
| Impulse size (vs 20-bar 5m ATR) | 1.2x | 0.8x–2.0x | Win-rate vs trade-count tradeoff |
| Min gap size (vs 5m ATR) | 0.3x | 0.1x–0.8x | Fill rate and post-fill follow-through |
| Kill-zone windows | London 08–11, NY 09:30–12 | +/- 30 min on each edge | Expectancy per session band |
| HTF trend filter | H1 20-EMA slope, 4h | H1 50-EMA, H4 20-EMA, no filter | Net edge with vs without filter |
| Trigger rule | 5m close back outside gap | First touch, 50% reaction, close-outside | Stop-out rate at fixed 1R |
| Stop buffer beyond gap | 0.10x 5m ATR | 0.05x–0.30x | Stop-out before BE move |
| T1 holdout vs partial | 50% at T1 | 33%, 50%, 67%, 100% | Expectancy and variance |
| Time stop | 90 min | 30, 60, 90, 120 min | Median time-to-T1 |
| Realized-vol floor (vs 20d) | 0.5x | 0.3x–0.8x | Trade-exclusion impact on net edge |
| Max gaps per session | unlimited | 1, 2, 3, unlimited | Overtrading penalty on net P&L |

---

## 4. Order Block Retest After London BOS

**Concept (1 line):** When London prints its first 15m BOS, identify the Order Block that produced it; enter on the first retest of that OB in the BOS direction.

**Edge sources:** pillar-01-market-structure (a 15m BOS that follows an external sweep is a higher-quality signal than one inside balance — both pieces are identifiable in real time); pillar-04-volume-orderflow-liquidity (the OB candle is the location where the last opposing flow was absorbed before the impulse; a retest into that zone is a re-entry into the absorption level).

**Why it has edge (2-3 sentences):** A real BOS provides a clear directional regime and a structural reference (the OB) for re-entry at better prices. Participants who missed the impulse either chase or look for the retest; the retest is mechanically defined by the OB candle, which keeps the setup falsifiable and the stop tight. The edge is in waiting for the first BOS rather than predicting it — this is the most exploited and most forgiving ICT pattern, so realistic expectations on R-multiple and hit-rate matter more than the setup mechanics.

### Entry model
- **Instrument & timeframe:** EURUSD, GBPUSD, XAUUSD — 15m for OB / 5m for trigger.
- **Setup conditions:**
  1. Mark the prior overnight (Asia + late NY) high and low as external liquidity references before London opens.
  2. During 07:00–10:00 London, price first sweeps one of those external references (wick beyond, body back inside on the 5m).
  3. After the sweep, a 15m candle closes beyond the most recent 15m fractal swing in the opposite direction — this is the BOS. The fractal swing must have been established before the sweep, fixed in time.
  4. Identify the OB: the last opposing-color 15m candle before the impulse leg that produced the BOS. The OB body is the entry zone; the OB wick extends the invalidation zone.
  5. An FVG is present inside or contiguous to the OB on the 5m chart (impulse-driven imbalance).
  6. The OB has not been mitigated since formation.
- **Trigger:** Limit order at the 50% level of the 15m OB body. Order valid until the OB is mitigated or until 12:00 London, whichever first.
- **Invalidation:** A 15m candle closes beyond the distal boundary of the OB (full OB break-through); the setup becomes a Breaker, not a continuation.

### Position management
- **Initial stop:** Distal boundary of the 15m OB plus 0.10x 15m ATR buffer.
- **Risk per trade:** 0.25–0.5%.
- **Scaling:** A second unit at a nested 5m OB inside the 15m OB is permitted, both sharing the same stop. No further adds.

### Exit model
- **Targets:** T1 = the BOS impulse swing extreme (the high/low immediately created by the impulse). T2 = next HTF liquidity pool in the BOS direction, identified at entry and held fixed.
- **Trailing:** 50% off at T1, stop to breakeven. Trail remainder under each new 15m higher-low (longs) / above 15m lower-high (shorts).
- **Time stop:** Flat by 15:00 NY. If T1 untouched 3 hours after entry, exit at market.

### Strategy disabled when
- No clean 15m BOS by 10:00 London (no setup that day; do not relax the BOS definition).
- High-impact European news (ECB, BoE, German CPI) within 30 minutes of the BOS or retest.
- Realized 15m ATR over the prior 4 hours is more than 2x its 20-day average — OBs in extreme-volatility regimes tend to be fully blown through rather than respected.
- Daily structure has just printed a CHoCH against the BOS direction within the prior 24 hours (regime conflict; lower expectancy).

### Confluence pairings
- Strategy #3 — the FVG inside the OB is the precise overlap entry.
- Strategy #1 — when the sweep that precedes the BOS is the Asian-range sweep, three frameworks converge on the same zone; do not size up — count as one trade.
- Strategy #5 — the BOS is the distribution-leg start; the OB retest is the distribution-leg entry.

### Fine-tune parameters

| Parameter | Default | Search range | What to measure |
|-----------|---------|--------------|-----------------|
| Sweep-before-BOS requirement | required | required, optional, sweep within Nm of BOS | Win-rate lift from filter |
| BOS confirmation window | 07:00–10:00 London | 07:00–11:00, 08:00–10:00 | Trade count vs edge per band |
| OB entry level | 50% body | proximal, 50%, distal | Fill rate vs slippage to T1 |
| Stop buffer beyond OB | 0.10x 15m ATR | 0.05x–0.30x | Stop-out rate at fixed 1R |
| Nested 5m OB scale-in | enabled | enabled, disabled | Expectancy of second unit alone |
| T1 holdout vs partial | 50% at T1 | 33%, 50%, 67%, 100% | Expectancy and variance |
| Time stop after entry | 180 min | 90, 120, 180, 240 min | Median time-to-T1; tail-trade contribution |
| Realized-vol ceiling (vs 20d) | 2.0x | 1.5x, 2.0x, 3.0x, off | Net P&L impact of vol filter |
| Daily-CHoCH conflict lookback | 24 hours | 8, 24, 48 hours | False-positive rate of OB retests |
| OB validity (time since formation) | until 12:00 London | 2h, 4h, until session end | Mitigation success rate vs delay |

---

## 5. Power-of-Three (AMD) Distribution Leg

**Concept (1 line):** The Accumulation–Manipulation–Distribution cycle structures each session; trade only the distribution leg, entered after manipulation is confirmed by a sweep-and-reversal followed by a 15m BOS.

**Edge sources:** pillar-01-market-structure (the AMD framework is operational only if each phase has a falsifiable definition — otherwise it is post-hoc storytelling; this rule set forces real-time phase tagging); pillar-04-volume-orderflow-liquidity (the manipulation extreme is where opposing-direction stops are concentrated; absorbing them is what funds the distribution leg toward the opposite session's liquidity pool).

**Why it has edge (2-3 sentences):** When the Asian session ranges, retail and discretionary stops cluster on both sides; one side gets swept at London open (manipulation), and the remaining stop pool on the opposite side becomes the natural draw for the distribution leg. The edge is conditional: it requires a clean sweep, a clean BOS, and an HTF context that does not contradict the distribution direction. Without all three, the AMD label is descriptive, not predictive.

### Entry model
- **Instrument & timeframe:** EURUSD, GBPUSD, XAUUSD — H1 / 15m for AMD phase tagging, 5m for execution.
- **Setup conditions:**
  1. **Accumulation:** Asian session 19:00–02:00 London has both an identifiable high and low, with range at least 0.5x its 20-day ATR. Both extremes must be visually clean (no extended wicks that already swept obvious external liquidity overnight).
  2. **Manipulation:** During 07:00–10:00 London, exactly one side of the Asian range is swept (wick beyond, body close back inside on the 5m). If both sides get swept before any BOS, the AMD interpretation breaks — skip the day.
  3. **Distribution initiation:** Within 90 minutes of the manipulation sweep, a 15m close prints beyond a 15m fractal swing in the direction opposite the sweep. This is the BOS that confirms distribution.
  4. **DOL identification:** The distribution target is the opposite Asian extreme. Beyond that, the next external pool (prior day high/low, prior week extreme) can be a stretch target if the daily bias agrees. DOL is logged at entry time and not re-rationalized later.
  5. **HTF context:** H4 must not be in an impulse leg against the distribution direction within the prior 8 hours.
- **Trigger:** First retracement after BOS into the 15m OB or 5m FVG left by the BOS impulse. Limit order at the OB 50% or at the FVG mid; valid for 90 minutes after BOS. If no retracement and price runs directly to T1 (gap-and-go), do not chase — skip and log as missed-but-correct.
- **Invalidation:** Either (a) a 15m close beyond the manipulation extreme in the sweep direction, or (b) limit expires unfilled.

### Position management
- **Initial stop:** Beyond the manipulation wick extreme, plus 0.10x 15m ATR buffer.
- **Risk per trade:** 0.25–0.5%.
- **Scaling:** One add permitted on the first retest of the BOS level, sharing the same stop. No further adds.

### Exit model
- **Targets:** T1 = opposite Asian extreme (primary DOL). T2 = next external liquidity pool aligned with daily bias, fixed at entry. Use structural levels, not R-multiples.
- **Trailing:** 50% off at T1, stop to breakeven. Trail remainder under 15m swing structure. If new distribution-direction extreme prints and pulls back, lock at least 1R on remainder.
- **Time stop:** Flat by 15:00 NY. If T1 untouched 3 hours after entry, exit at market — the session-bound thesis decays into NY close.

### Strategy disabled when
- Asian session was a trending move rather than a range (no two-sided extremes; AMD framework does not apply). Heuristic: if the Asian close is within 20% of the Asian high or low, treat as trend, not range.
- Both sides of the Asian range get swept in the same morning before any BOS — the day's character is a double-sided sweep or expansion, not AMD.
- High-impact news at London or NY open.
- Daily timeframe has printed a CHoCH against the distribution direction within the prior 48 hours.
- Holiday-thinned sessions (the algorithmic / participation premise behind AMD weakens).

### Confluence pairings
- Strategy #1 and strategy #2 — same manipulation event, different entry mechanics; count as one trade if all three trigger together.
- Strategy #4 — the BOS and OB retest of strategy #4 is the same execution event as the distribution-leg entry here.
- Caution: do not stack with mean-reversion strategies on the same instrument the same day — they imply opposite regimes and one of them will be wrong by construction.

### Fine-tune parameters

| Parameter | Default | Search range | What to measure |
|-----------|---------|--------------|-----------------|
| Asian session window | 19:00–02:00 London | 18:00–01:00, 20:00–03:00 | Win-rate per regime |
| Min Asian range (vs 20d ATR) | 0.5x | 0.3x–1.0x | Filter strength vs trade count |
| Range-vs-trend classifier (close inside extreme) | 20% of range | 10%, 20%, 30% | False-classification rate |
| Manipulation window | 07:00–10:00 London | 07:00–09:00, 07:00–11:00 | Sweep capture rate |
| BOS confirmation window after sweep | 90 min | 30, 60, 90, 120 min | False-positive distribution entries |
| Entry level (OB or FVG) | 50% OB body / FVG mid | proximal, 50%, distal | Fill rate vs adverse excursion |
| Stop buffer beyond manipulation wick | 0.10x 15m ATR | 0.05x–0.30x | Stop-out rate at fixed 1R |
| Scale-in at BOS retest | enabled | enabled, disabled | Standalone expectancy of add |
| T1 partial size | 50% | 33%, 50%, 67%, 100% | Expectancy and variance |
| Time stop after entry | 180 min | 90, 120, 180, 240 min | Median time-to-T1; T2 conversion rate |


---

# Part B — Volume Profile / Auction / VWAP Intraday Strategies (6-10)

*Reviewed and refined.*

Cross-cutting notes that apply to every strategy in this section:

- **Day-type look-ahead risk.** Dalton's day-type taxonomy (Normal, Normal Variation, Neutral, Trend, Double-Distribution) is only definitive at the close. Any rule that uses "today is a Trend Day" mid-session is using a real-time *proxy* (IB extension multiple, open-type, range expansion), not the canonical label. Backtests that label day types after the close and then condition entries on the label are biased; always implement the proxy and validate it against the close-of-session label out-of-sample.
- **Profile object versioning.** Specify whether VAH/VAL/POC/IB references are: (a) prior RTH session only, (b) prior 24h including overnight, (c) developing intraday. State whether VPOC is *start-of-session frozen* or *developing*; the two are very different objects and will produce different trades.
- **Value-area % convention.** Defaults below use 70% (CBOT standard). 60% and 80% variants are legitimate and should be tested.
- **Cost realism.** Strategies 6 and 8 are mean-reversion; per Chan, mean-reversion edges are highly sensitive to round-trip cost. Every backtest must include commission, exchange fees, and a slippage model (at minimum 1 tick adverse on entry and exit; for thin-book moments such as the IB extreme, model 2 ticks).
- **Time stops.** Stated time stops are starting defaults, not derived optima. Survival curves (P&L vs. time-in-trade) should be computed per strategy and the time stop placed where marginal expectancy turns negative.
- **Regime disable.** Each strategy lists a kill switch — a condition under which the strategy should not trade that day, even if the local setup appears.

---

## 6. Prior-Day VAH/VAL Fade

**Concept (1 line):** In a confirmed balance regime, an excursion outside prior-day value that fails to accept new value tends to revert toward prior-day POC and, conditionally, the opposite value-area boundary.

**Edge sources:** pillar-02-auction-volume-profile (responsive activity at VA boundaries; the "80% rule" framing from Dalton); pillar-04-volume-orderflow-liquidity (narrow-spread / high-volume rejection and CVD divergence as confirmation of responsive participation); pillar-09-statistical-quant-thinking (the rule is a conditional probability dependent on regime; cost sensitivity is high).

**Why it may have edge (honest framing):** When two or more consecutive sessions print overlapping value areas, other-timeframe participants are not pricing in new information and short-timeframe rotation dominates. The folklore "80% rule" — once price re-enters value from outside, it has an ~80% historical tendency to traverse to the opposite boundary — is widely cited but not consistently replicated in modern electronic markets and varies by instrument, value-area %, and regime. Treat the 80% number as a hypothesis to be measured per instrument, not as a given.

**Falsifiability test:** Across at least 200 qualifying setups per instrument, the unconditional win rate of "re-enter VA after probe → tag opposite VA boundary before close" must exceed the strategy's break-even win rate given the stop/target geometry (typically ~40-45% net of costs for a 1:1.5 R). If it does not, the edge is not present on that instrument.

### Entry model
- **Instrument & timeframe:** ES, NQ, MES, MNQ, or CL futures; XAUUSD futures (GC/MGC) where session structure is clean. 5-minute execution, 30-minute profile context.
- **Setup conditions:**
  1. **Regime filter (real-time proxy for balance):** prior session IB extension ≤ 1.0× IB range AND prior session range ≤ 1.0× ATR(20). Avoid the explicit "Normal day" label since it is post-hoc.
  2. **Value overlap:** developing session VA overlaps prior-day VA by ≥ 50% of prior-day VA width measured at the time of trigger.
  3. **Excursion:** price prints above prior-day VAH or below prior-day VAL on the developing session.
  4. **News exclusion:** no Tier-1 macro release (CPI, NFP, FOMC, ECB, BoE) within ±30 minutes of trigger.
- **Trigger:** A 5-minute bar that closes back inside prior-day VA after the probe, with bar volume ≥ 1.3× the 20-bar rolling median AND footprint delta opposite the probe direction (negative delta at VAH probe, positive at VAL probe).
- **Invalidation:** Two consecutive 5-minute closes outside prior-day VAH (short) or VAL (long) after entry, OR a single close beyond 1.0× ATR(5) past the probe extreme.

### Position management
- **Initial stop:** Probe extreme + 1× ATR(5, 5-minute) + 1 tick.
- **Risk per trade:** 0.25-0.5%.
- **Scaling:** Full size at trigger; 50% off at T1; remainder runs to T2 or time-stop.

### Exit model
- **Targets:** T1 = prior-day POC. T2 = opposite prior-day VA boundary. T2 is conditional: only hold for T2 if, at the T1 touch, CVD continues in the trade direction and price does not stall at the POC for more than 3 bars.
- **Trailing:** Stop to break-even at T1. Trail remainder using a close beyond the 3-period EMA on 5-minute.
- **Time stop:** Default 90 minutes from entry, or 30 minutes before RTH close, whichever is first. Validate by plotting cumulative expectancy vs. minutes-in-trade and choosing the point where marginal expectancy crosses zero.
- **Kill switch:** If by 11:00 ET session range already exceeds 1.25× ATR(20) and price has accepted outside prior-day VA, do not take any further VAH/VAL fade in that session — the regime has flipped to initiative.

### Confluence pairings
Strategies 1-5 (structure/ICT): a VAH/VAL coinciding with an ICT order block or PDH/PDL liquidity adds structural weight to the level. Strategies 16-20 (quant/order-flow): a CVD divergence or normalized z-score extreme at the probe materially raises conditional win rate; lack of either is a soft veto.

### Fine-tune parameters

| Parameter | Default | Search range | What to measure |
|-----------|---------|--------------|-----------------|
| Value-area % | 70% | 60 / 68 / 70 / 80 | Win rate, expectancy per setup, sensitivity of T2 hit rate |
| Profile source | Prior RTH only | RTH-only / 24h / RTH+ETH composite | Setup count, signal stability across sessions |
| Probe-bar volume threshold | 1.3× 20-bar median | 1.0 / 1.2 / 1.3 / 1.5 / 2.0× | Precision (fewer false fades) vs. recall (sample size) |
| Re-entry confirmation bars | 1 close | 1 / 2 closes back inside | Whipsaw rate, slippage from later entry |
| Regime filter — prior-day IB extension cap | 1.0× IB range | 0.5 / 0.75 / 1.0 / 1.25× | Trend-day false positives in setup pool |
| Stop multiple (ATR) | 1.0× | 0.5 / 0.75 / 1.0 / 1.5× ATR(5) | R-multiple distribution, max adverse excursion |
| Time stop | 90 min | 30 / 60 / 90 / 120 / EOD | Expectancy decay curve |
| News-exclusion window | ±30 min | ±0 / ±15 / ±30 / ±60 min | Tail-loss frequency around releases |
| Slippage model | 1 tick / side | 0 / 1 / 2 ticks; volatility-scaled | Robustness of net edge to cost assumptions |

---

## 7. Naked POC Magnet

**Concept (1 line):** A POC from a recent session that subsequent price has not revisited can act as a magnet; after a liquidity sweep on the opposite side clears directional stops, price often gravitates toward the untouched POC.

**Edge sources:** pillar-02-auction-volume-profile (naked / virgin POC concept; auctions returning to prior accepted value); pillar-04-volume-orderflow-liquidity (sweep mechanics, absorption signature).

**Why it may have edge (honest framing):** A naked POC marks a price where prior auction activity was densest. The hypothesis is that markets revisit those nodes to "test" prior fair value. This is a folklore claim with mixed empirical support; the conditional version — *after a liquidity sweep on the opposite side, within N sessions of the POC's creation* — has a stronger plausibility because the sweep removes the directional overhang that was previously pinning price away.

**Falsifiability test:** "Age cutoff" matters. Define `nPOC_age` = sessions since the POC was created without being touched. Measure hit rate of "price reaches POC before close, given current setup conditions" stratified by `nPOC_age` ∈ {1, 2, 3, 5, 10}. If hit rate is flat or declining and not above the strategy's break-even, no edge.

### Entry model
- **Instrument & timeframe:** ES, NQ, GC, CL futures. 15-minute structural context, 5-minute execution.
- **Setup conditions:**
  1. **Naked POC inventory:** maintain a list of unvisited POCs from the prior `nPOC_age` ≤ 5 sessions (default). Each entry is `{price, age, source-session-date}`.
  2. **Side:** current price must be on the opposite side of the nPOC.
  3. **Distance bound:** `|price − nPOC|` ≤ 1.5× ATR(14, daily). Beyond this, the magnet is too far for a single-session play.
  4. **Liquidity sweep on current session:** a 5-minute bar pierces a defined stop cluster (PDH, PDL, IB high/low, equal-highs/lows formed today) by ≥ 0.25× ATR(5) AND the same or next bar closes back inside the prior range.
  5. **Absorption signature:** sweep bar has volume ≥ 1.5× 20-bar median AND delta opposite to the sweep direction (sell delta on a high sweep, buy delta on a low sweep).
- **Trigger:** Close of the first 5-minute bar in the direction of the nPOC after the sweep confirms.
- **Invalidation:** A 5-minute close beyond the sweep extreme on volume ≥ 1.5× median in the breakout direction. Sweep has resolved as initiative, not absorption.

### Position management
- **Initial stop:** Sweep extreme ± 1 tick.
- **Risk per trade:** 0.25-0.5%.
- **Scaling:** Full at entry; 50% off at nPOC; remainder per acceptance logic.

### Exit model
- **Targets:** T1 = nPOC. T2 (conditional): if price prints ≥ 2 bars at the nPOC and CVD continues, target the opposite VA boundary of the source session.
- **Trailing:** Break-even at T1; 0.5× ATR(5) trailing stop on remainder.
- **Time stop:** 90 minutes default. If the magnet thesis is correct, the move usually completes within one TPO bracket (~30-60 minutes); 90 minutes is a hard ceiling.
- **Kill switch:** Skip the setup if a Tier-1 macro release falls between entry and projected T1.

### Confluence pairings
Strategies 1-5: nPOC coincident with an FVG midpoint or prior-session equilibrium adds dual-framework target weight. Strategies 11-15: news-window exclusion is a hard veto, not a soft preference.

### Fine-tune parameters

| Parameter | Default | Search range | What to measure |
|-----------|---------|--------------|-----------------|
| nPOC age cutoff | 5 sessions | 1 / 2 / 3 / 5 / 10 / 20 | Hit rate stratified by age; decay curve |
| Profile source for POC | RTH-only | RTH-only / 24h | POC stability, setup count |
| Sweep pierce magnitude | 0.25× ATR(5) | 0.1 / 0.25 / 0.5 / 0.75× ATR | False-sweep rate vs. signal count |
| Sweep-bar volume threshold | 1.5× 20-bar median | 1.0 / 1.25 / 1.5 / 2.0× | Absorption-vs-initiative classification accuracy |
| Distance bound to nPOC | 1.5× daily ATR(14) | 0.5 / 1.0 / 1.5 / 2.0× | Hit rate vs. distance bucket |
| Value-area % (for source POC definition) | 70% | 60 / 70 / 80% | POC location stability |
| Time stop | 90 min | 30 / 60 / 90 / 120 min | Expectancy decay curve |
| Stop offset from sweep extreme | 1 tick | 1 tick / 0.25× ATR / 0.5× ATR | Stop-out vs. winner geometry |

---

## 8. VWAP Mean Reversion in Balance Days

**Concept (1 line):** On rotational sessions, excursions to ±1 / ±2 SD VWAP bands tend to revert to session VWAP.

**Edge sources:** pillar-02-auction-volume-profile (balance regime where responsive participants dominate); pillar-09-statistical-quant-thinking (mean reversion as a conditional, cost-sensitive edge).

**Why it may have edge (honest framing):** VWAP approximates the volume-weighted intraday cost basis; in a balance regime, price extensions from this mean are statistically rare and tend to be faded because no directional flow is sustaining them. The 68% / 95% framing from the normal distribution is a useful intuition but intraday returns are not Gaussian — fat tails will trigger ±2 SD touches more often than 5% of the time, especially around news. Treat SD bands as empirical, not theoretical.

**Falsifiability test:** Stratify ±1 SD and ±2 SD touches by real-time regime proxy (IB extension at touch time). On confirmed-balance subset, mean reversion to VWAP within 30 minutes of touch should exceed 55% with positive expectancy net of costs. On confirmed-trend subset, the same setup should be unprofitable — this asymmetry is the edge.

### Entry model
- **Instrument & timeframe:** ES, NQ, liquid ETFs. 3-min or 5-min execution. Session VWAP and ±1 / ±2 SD bands plotted from RTH open. Use developing VWAP only (no anchored variants here).
- **Setup conditions:**
  1. **Real-time balance proxy:** by minute 90 of the session, IB extension ≤ 0.5× IB range AND session range ≤ 0.75× ATR(20). If breached at any time, disable strategy for the day.
  2. **Band touch:** price tags ±1 SD (aggressive) or ±2 SD (conservative).
  3. **Rejection structure:** touch bar prints volume ≥ 1.2× 20-bar median AND closes with a tail back toward VWAP comprising ≥ 50% of bar range.
  4. **Delta divergence:** at the band, CVD opposite the price extreme over the prior 5 bars.
- **Trigger:** First 3- or 5-minute close back inside ±1 SD after the band tag. Alternative: two-bar reversal (rejection bar + confirming bar).
- **Invalidation:** Close beyond ±2 SD with no upper/lower tail AND volume ≥ 1.5× median in the extension direction. Mean is migrating.

### Position management
- **Initial stop:** 1.5× ATR(5) beyond band tag extreme; for ±2 SD entries this is roughly the ±2.5 SD line, but use ATR not SD because SD widens intraday.
- **Risk per trade:** 0.25-0.5%.
- **Scaling:** Full at trigger; 50% off at VWAP (T1); remainder managed to opposite ±1 SD (T2).

### Exit model
- **Targets:** T1 = session VWAP. T2 = opposite ±1 SD, taken only when CVD and delta confirm at T1.
- **Trailing:** Break-even at T1; 0.75× ATR(5) trailing on remainder.
- **Time stop:** 60 minutes from entry, or 60 minutes before RTH close. Validate against expectancy decay.
- **Kill switch:** Real-time balance proxy breached (IB extension > 1.0× IB range, or developing range > ATR(20)) → no further entries that session.

### Confluence pairings
Strategies 1-5: a band tag coincident with an order block or PDH/PDL extends invalidation logic. Strategies 16-20: simultaneous z-score signal raises conditional reliability.

### Fine-tune parameters

| Parameter | Default | Search range | What to measure |
|-----------|---------|--------------|-----------------|
| VWAP band SD multiple — entry | ±2 SD | ±1.0 / 1.5 / 2.0 / 2.5 SD | Win rate, expectancy, signal count |
| Stop multiple (ATR) | 1.5× ATR(5) | 1.0 / 1.25 / 1.5 / 2.0× | Stop-out distribution |
| Real-time balance proxy — IB extension cap | 0.5× IB range | 0.25 / 0.5 / 0.75 / 1.0× | Trend-day leakage into setup pool |
| Touch-bar volume threshold | 1.2× 20-bar median | 1.0 / 1.2 / 1.5× | Rejection-vs-continuation classification |
| Execution timeframe | 5-min | 1 / 3 / 5 / 10 min | Slippage, signal noise |
| VWAP anchor | RTH open | RTH open / prior-day close / overnight high-vol pivot | Robustness of mean across anchors |
| Time stop | 60 min | 30 / 45 / 60 / 90 min | Expectancy decay |
| Cost model | 1 tick / side | 0 / 1 / 2 ticks; vol-scaled | Survival of edge under realistic cost |

---

## 9. Open-Drive Trend Day Continuation

**Concept (1 line):** When the session opens with confirmed initiative away from prior value and holds, pullbacks to the developing VWAP are continuation entries in the drive direction.

**Edge sources:** pillar-02-auction-volume-profile (Trend Day and Double Distribution Trend Day; initiative activity); pillar-04-volume-orderflow-liquidity (Weis Wave volume expansion, CVD alignment).

**Why it may have edge (honest framing):** Trend Days are a minority of sessions (Dalton-cited base rates are roughly 10-20% depending on instrument and definition; verify per instrument before quoting). Their conditional payoff is large because range is concentrated in one direction. The risk is misclassifying a wide-range Normal Variation Day as a Trend Day and getting chopped. The edge depends entirely on the real-time classifier's precision.

**Falsifiability test:** Define real-time "trend-day-in-progress" classifier from the rules below. Measure: (a) precision — fraction of classified trend days that actually print as Trend or Double-Distribution Trend at the close; (b) recall is less important than precision here, because the strategy only needs high-quality positives. Target precision ≥ 65% before deploying.

### Entry model
- **Instrument & timeframe:** ES, NQ, GC futures. 5-minute execution; developing session VWAP.
- **Setup conditions:**
  1. **Open-drive proxy:** within first 15 minutes, price moves ≥ 0.75× prior 20-day average IB range away from prior-day VA AND does not retrace into prior-day VA.
  2. **Volume confirmation:** first 3-5 bars print expanding volume in the drive direction (each bar ≥ prior bar's volume on at least 3 of 5) AND CVD monotone in the drive direction.
  3. **Hold through opening TPO:** at the 30-minute mark, price remains outside prior-day VA.
  4. **Pullback to developing VWAP:** without crossing prior-day VAH (bull) / VAL (bear).
- **Trigger:** 5-minute close in drive direction after VWAP touch, with pullback bar volume below 20-bar median (no-supply / no-demand signature).
- **Invalidation:** 5-minute close back inside prior-day VA after pullback — drive thesis voided; exit immediately.

### Position management
- **Initial stop:** Beyond pullback extreme; typically 1.0-1.25× ATR(5).
- **Risk per trade:** 0.25-0.5%.
- **Scaling:** Full at first VWAP pullback. One add permitted on a second VWAP pullback if the first is at break-even or better; add uses its own bar-relative stop. Cap at two entries.

### Exit model
- **Targets:** T1 = PDH (bull) / PDL (bear). T2 = IB-range projection (IB high + IB range bull; IB low − IB range bear). T3 (Double-Distribution): second distribution's POC.
- **Trailing:** After T1, stop on remainder = most recent 30-minute swing extreme. Developing VWAP acts as a trailing reference — a 5-min close back through VWAP exits.
- **Time stop:** Default exit any remainder 45 minutes before close. On classifier-confirmed Double-Distribution days, allow holding to 15 minutes before close.
- **Kill switch:** If after the open-drive trigger price re-enters prior-day VA before the 30-minute confirmation, no trend-day trade that session.

### Confluence pairings
Strategies 1-5: drive toward a HTF liquidity pool from slots 1-5 supplies a draw-on-liquidity target. Strategies 11-15: macro catalyst on the same day raises classifier precision; position size may move toward 0.5%.

### Fine-tune parameters

| Parameter | Default | Search range | What to measure |
|-----------|---------|--------------|-----------------|
| Open-drive magnitude (× prior-20d avg IB) | 0.75× | 0.5 / 0.75 / 1.0 / 1.25× | Trend-day classifier precision and recall |
| Open-drive window | 15 min | 10 / 15 / 30 min | Setup count vs. classifier precision |
| IB minutes (for context / projections) | 60 | 30 / 45 / 60 / 90 min | Projection accuracy of T2 |
| Volume expansion rule (consecutive bars) | 3 of 5 | 2 of 3 / 3 of 5 / 4 of 5 | False open-drive rate |
| Pullback-bar volume cap (× 20-bar median) | 1.0× | 0.5 / 0.75 / 1.0 / 1.25× | No-supply / no-demand precision |
| Stop multiple (ATR) | 1.0× ATR(5) | 0.75 / 1.0 / 1.25× | Add-on viability, R distribution |
| Re-entry-into-VA invalidation window | 30 min | 15 / 30 / 45 min | False kill rate |
| Time-stop offset before close | 45 min | 15 / 30 / 45 / 60 min | End-of-day mean-reversion drag |

---

## 10. Initial Balance (IB) Breakout

**Concept (1 line):** A clean break of IB High or IB Low on confirming volume, before the late-session window, signals other-timeframe initiative and targets measured IB-range extensions.

**Edge sources:** pillar-02-auction-volume-profile (IB and range-extension mechanics; day-type implications of IB behavior); pillar-04-volume-orderflow-liquidity (wide-spread / high-volume breakout bar; CVD alignment vs. sweep).

**Why it may have edge (honest framing):** The IB encodes the session's initial fair-value discovery; a confirmed break is structurally interpretable as initiative. The 1× / 1.5× / 2× IB-range extension targets are useful heuristics but the empirical extension distribution is instrument- and regime-specific — quote per-instrument extension histograms before relying on a fixed target ladder. The volume filter is doing most of the work in distinguishing extension from sweep; subjective "above-average" must be replaced with a measurable rule.

**Falsifiability test:** Net expectancy of "enter on IB-break close with volume ≥ k × median, exit at IB ± 1× IB range, stop = 1× ATR(5) inside IB" must be positive across the chosen `k` over a minimum of 200 setups per instrument, including realistic slippage. Sweep failure rate (entry → close back inside IB within 2 bars) should be below 35% on the kept signals.

### Entry model
- **Instrument & timeframe:** ES, NQ, GC, CL. 5-minute execution. IB = high/low of first 60 minutes of RTH (parameterized below).
- **Setup conditions:**
  1. **Wait for IB completion** — no anticipation.
  2. **Break:** 5-minute close beyond IB High (bull) or IB Low (bear).
  3. **Volume filter:** breakout bar volume ≥ 1.5× the 20-bar rolling median of the IB period (parameter).
  4. **CVD alignment:** CVD slope in the breakout direction over the breakout bar and the prior bar.
  5. **No 2-bar snapback:** within 2 bars of the break, price does not close back inside IB.
  6. **Session-window cutoff:** break occurs before 13:00 local session time (parameter).
- **Trigger:** Close of first qualifying breakout bar, or pullback entry to the broken IB boundary if direct R:R is unacceptable.
- **Invalidation:** 5-minute close back inside IB after entry. If invalidated, the session more likely resolves as Normal/Neutral and the IB extreme becomes a strategy-6 or strategy-8 candidate on the next test.

### Position management
- **Initial stop:** Direct entry: 1.0× ATR(5) inside IB from the broken boundary. Pullback entry: beyond the pullback extreme by 1 tick.
- **Risk per trade:** 0.25-0.5%. Pullback entries may use the upper bound given the tighter stop.
- **Scaling:** Single entry; no pyramiding.

### Exit model
- **Targets:** T1 = 1× IB-range projection from the broken boundary. T2 = 1.5× projection. T3 = 2× projection, taken only when open-drive (strategy 9 classifier) is also true.
- **Trailing:** Break-even at T1. After T2, trail on a 30-minute bar — exit on first 30-min close reversing toward IB.
- **Time stop:** Exit all positions 45 minutes before RTH close.
- **Kill switch:** If breakout occurs after the session-window cutoff, no trade. If two prior IB breaks the same week resulted in sweep-failures on the same instrument, halve size until the next clean confirmation.

### Confluence pairings
Strategies 1-5: IB break that also clears PDH/PDL liquidity is a dual-confirmation setup. Strategies 16-20: replace the "above-average volume" heuristic with a quantified breakout-volume model (e.g., bar volume z-score on the IB-period distribution).

### Fine-tune parameters

| Parameter | Default | Search range | What to measure |
|-----------|---------|--------------|-----------------|
| IB length (minutes) | 60 | 30 / 45 / 60 / 90 | Win rate, sweep-failure rate, range-extension accuracy |
| Breakout-bar volume threshold (× IB-period 20-bar median) | 1.5× | 1.0 / 1.25 / 1.5 / 2.0 / 2.5× | Sweep-vs-extension classification accuracy |
| Snapback window (bars) | 2 | 1 / 2 / 3 / 4 bars | False breakout filter strength |
| Session-window cutoff (local time) | 13:00 | 11:00 / 12:00 / 13:00 / 14:00 / none | Late-break follow-through |
| Stop placement (× ATR(5) inside IB) | 1.0× | 0.5 / 0.75 / 1.0 / 1.5× | R distribution, premature stop-outs |
| T1 / T2 / T3 (× IB range) | 1.0 / 1.5 / 2.0 | 0.75 / 1.0 / 1.25 / 1.5 / 2.0 / 2.5 | Empirical extension histogram per instrument |
| Value-area % for context profile | 70% | 60 / 70 / 80% | Sensitivity of confluence reads |
| Required CVD alignment | yes | yes / no | Incremental value of CVD filter |
| Slippage model | 1 tick / side | 0 / 1 / 2 ticks; vol-scaled at IB extreme | Net-of-cost edge survival |
| Time stop before close | 45 min | 15 / 30 / 45 / 60 min | EOD mean-reversion drag |

---


---

# Part C — Event-Driven / News / Macro Intraday Strategies (11-15)

*Reviewed and refined.*

These five strategies share three structural hazards that the original drafts under-weighted: (a) academic edges decay once published, (b) bid-ask spreads at announcement widen 5-10x and quoted entries are frequently unfillable, (c) overlapping event calendars contaminate any "single-event" claim. Each strategy below is now framed as a falsifiable hypothesis with a calendar blackout, surprise-magnitude conditioning, and an explicit fine-tune table. Where the original edge cannot survive scrutiny, the claim is weakened rather than dressed up.

**Global rules for all five (apply before any strategy-specific filter):**
- **Calendar blackout.** Do not run strategy N if any other Tier-1 event (FOMC, ECB, BoE, BoJ, RBI, US CPI, US NFP, US PCE, US Retail Sales, US PPI, JOLTS, ISM, GDP advance) is scheduled within ±24h of the trigger window. Event-on-event interactions destroy the conditioning that gave each strategy its claimed edge.
- **Spread guard.** Reject entry if quoted spread at trigger time exceeds 3x the trailing 20-day median spread for that symbol at that time of day. Backtests using mid-quote fills are misleading; assume entry at far-touch + 1 tick and exit at far-touch.
- **Data latency.** Each strategy below is designed for retail-grade data (consolidated tape, 1-second granularity at best). If your feed is slower than that, push entries one bar later — do not trust the first post-release print.
- **Sample size honesty.** FOMC = 8/yr, NFP = 12/yr, CPI = 12/yr, RBI MPC = 6/yr. Five years of data is 40, 60, 60, and 30 trials respectively. Statistical power for these is poor; expect wide confidence intervals on every parameter.

---

## 11. Pre-FOMC Drift

**Concept (1 line):** Equity indices have historically exhibited an upward bias in the 24h before FOMC announcements (Lucca and Moench, 2015); test whether the effect persists in the current decade and trade only in regimes where it does.

**Honest edge status:** The Lucca-Moench effect was documented on 1994-2011 data. Subsequent work (including Lucca's own 2021 follow-up and several practitioner studies) shows the effect has *decayed substantially* post-publication, became negligible to negative in 2015-2019, partially re-emerged 2020-2022 during the QE/QT regime, and is again inconclusive 2023-2025. Treat this as a regime-conditional pattern, not a standing edge. The strategy below is structured so that a flat or negative live track record after 20-30 events forces a stand-down, not a "keep going, it's just variance" rationalization.

**Edge sources:** pillar-05-macro-intermarket-news (scheduled-event positioning; pre-event liquidity withdrawal), pillar-06-sentiment-positioning (pre-announcement under-hedging shows up as declining put-call ratio in the 24h window — when this pattern is *absent*, the drift historically fails)

**Why it might still have edge (and why it might not):** When dealers and short-sellers withdraw inventory before a binary announcement, the marginal buyer faces lower price resistance — a microstructure rationale that does not depend on the Fed's decision direction. However, two forces have eroded it: (a) the pattern is widely known and front-run by the same hedge funds Lucca observed, and (b) zero-DTE option flows around FOMC have changed dealer hedging behavior post-2022 in ways that frequently invert the pre-announcement drift. Trade this only with a regime filter that has held up out-of-sample.

### Entry model
- **Instrument & timeframe:** ES (S&P 500 E-mini futures) or SPY. 15-minute chart for execution. 8 FOMC decisions per year.
- **Setup conditions:**
  1. FOMC statement scheduled today, 2:00 PM ET.
  2. Regime filter A (vol): VIX < 20 AND VIX term structure in contango (VIX < VIX3M by at least 0.5 vol points). The drift historically fails in backwardation.
  3. Regime filter B (pre-positioning): SPX 1-day put-call ratio < trailing 60-day median. If hedging is *elevated* coming in, the under-hedging mechanism is not present.
  4. Pre-drift not already captured: ES gap up < 0.4% from prior close at the time of entry.
  5. Calendar blackout passes (no other Tier-1 event within ±24h, including ECB the prior day).
- **Trigger:** Enter at the regular-hours close on T-1 (day before FOMC) using a limit at the closing print, or on the FOMC-day open within the first 30 minutes if T-1 close was missed.
- **Invalidation:** ES makes a sustained new session low below prior day's low after entry, or VIX spikes >25 intraday.

### Position management
- **Initial stop:** Below prior day's regular-session low, or 0.6% below entry, whichever is tighter.
- **Risk per trade:** 0.2% of account. The strategy's expected edge (if present) is small; size accordingly.
- **Scaling:** Single-unit entry. No add-ons.

### Exit model
- **Targets:** None. This is a time-exit strategy.
- **Trailing:** Move stop to break-even after +0.3%. Do not trail tighter than break-even — the historical effect is a drift, not a trend.
- **Time stop:** Hard exit by 1:30 PM ET, 30 minutes before the release. Non-negotiable. Positions through the announcement convert this from a microstructure trade into a directional Fed bet, which it was never designed to be.

### Live-track stand-down rule
- Track expectancy on a rolling basis. After 20 live FOMC trials, if expectancy is negative or Sharpe < 0, suspend the strategy until two consecutive years show positive expectancy in a re-test.

### Confluence pairings
- VWAP structure: above VWAP at entry confirms intraday bid.
- Prior-day volume POC as support strengthens the structural case.
- ICT NY kill zone overlap: entry within 9:30-11:00 ET aligns with institutional participation.

### Fine-tune parameters

| Parameter | Default | Search range | What to measure |
|-----------|---------|--------------|-----------------|
| Entry timing (hours before release) | 24h (T-1 close) | 4h, 8h, 16h, 24h, 48h | Expectancy decay vs entry lead time |
| Hard exit time before release | 30 min | 5, 15, 30, 60, 120 min | Sensitivity of expectancy to exit lead |
| VIX regime cap | 20 | 15, 18, 20, 25, no cap | Win rate stratified by VIX bucket |
| VIX term-structure filter (VIX - VIX3M) | < -0.5 | -2, -1, -0.5, 0, +0.5 | Effect under contango vs backwardation |
| Put-call ratio percentile filter | < 50th | 25, 50, 75, no filter | Whether pre-hedging level conditions edge |
| Max pre-entry gap | 0.4% | 0.2, 0.4, 0.6, 1.0, no cap | Diminishing returns when drift partially priced |
| Sample-period regime split | 2015+ | 1994-2010 / 2011-2019 / 2020-2025 | Decay profile across regimes |
| Stop distance | 0.6% | 0.3, 0.6, 1.0, 1.5%, structural only | Stop-out vs full-drift capture trade-off |
| Risk per trade | 0.2% | 0.1, 0.2, 0.35, 0.5% | Bankroll sensitivity given small edge |
| Live stand-down threshold (trials) | 20 | 10, 20, 30, 50 | Type-I vs Type-II error in regime-shift detection |

---

## 12. Post-CPI Fade

**Concept (1 line):** The initial 5-minute reaction to US CPI is often a positioning flush that retraces over 15-45 minutes — but *only* when the surprise is modest. Fade in-line prints; never fade outsized surprises.

**Honest edge status:** Fading the first move after CPI is a long-standing intraday claim. The historical evidence supports it for in-line and small-surprise prints (the spike is dealer-driven and unwinds). For outsized prints (|surprise| > 2σ of trailing 24-month consensus dispersion), the initial move is the *correct* repricing and fading it is a structural loss. The original strategy did not separate these regimes. This version fades only the conditional sub-sample where the edge actually exists.

**Edge sources:** pillar-05-macro-intermarket-news (post-release first-3-minute noise vs. 5-15-minute signal), pillar-06-sentiment-positioning (crowded pre-CPI positioning flush)

**Why it has edge (conditional):** Pre-release liquidity is withdrawn; the first algorithmic 1-3 minute response overshoots when the data is close to consensus because the move is dominated by stop-running rather than information. When the surprise is large (>2σ), the same first-3-minute move is dominated by genuine repricing and rarely retraces meaningfully on the day.

### Entry model
- **Instrument & timeframe:** ES or XAUUSD. 1-minute execution, 5-minute structure. CPI released 8:30 ET monthly (BLS schedule).
- **Setup conditions:**
  1. At least 5 minutes elapsed since 8:30 ET. Never trade the first 5-minute candle.
  2. **Surprise filter (critical):** absolute headline-vs-consensus surprise ≤ 1.5σ of trailing 24-month surprise distribution. If the surprise exceeds this, *stand aside*. Optional second test: also require core CPI surprise ≤ 1.5σ.
  3. The 5-minute post-CPI candle (8:30-8:35 ET) closed with body > 50% of range — defines a clean directional spike to fade.
  4. 1-minute reversal signal against the spike: pin bar, inside bar, or failure swing at 8:35-8:40 ET.
  5. Spread guard: post-release quoted spread has compressed back to within 1.5x median — if still blown out, wait.
  6. Calendar blackout passes.
- **Trigger:** 1-minute close in the opposite direction of the initial spike, after the 8:30-8:35 candle has closed.
- **Invalidation:** Initial 5-minute extreme is breached on a subsequent 1-minute close. If price continues without pausing, the setup does not exist.

### Position management
- **Initial stop:** Beyond the extreme of the 8:30-8:35 spike candle. This is structural invalidation.
- **Risk per trade:** 0.2% of account. Realized vol at this hour is 3-5x normal; tighten unit count, do not widen stop.
- **Scaling:** Full size at trigger. Window is short.

### Exit model
- **Targets:** T1 = 50% retracement of the 8:29 → spike extreme move. T2 = full retracement to the pre-CPI price.
- **Trailing:** Move stop to break-even after T1. Trail by 1-minute swings once T2 is in sight.
- **Time stop:** Hard exit by 9:30 ET. NY cash open begins a new narrative; the fade thesis expires.

### Confluence pairings
- VWAP retest as resistance (longs in fade) or support (shorts) strengthens the retracement thesis.
- Spike into low-volume node above/below prior value area: mean-reversion back into value is a high-probability target.
- HTF order block at the pre-CPI price acts as a structural magnet.

### Fine-tune parameters

| Parameter | Default | Search range | What to measure |
|-----------|---------|--------------|-----------------|
| Surprise-σ cap (fade allowed) | 1.5σ | 0.5, 1.0, 1.5, 2.0, 2.5σ | Edge inversion threshold |
| Post-release wait window | 5 min | 2, 3, 5, 8, 15 min | Whipsaw filter strength |
| Spike body / range threshold | 0.5 | 0.3, 0.5, 0.7, 0.9 | Spike "cleanness" filter |
| Spread-normalization multiplier | 1.5x | 1.0, 1.5, 2.0, 3.0x | Fillability vs participation rate |
| T1 retracement % | 50% | 25, 38.2, 50, 61.8, 100% | Where retracement most reliably stalls |
| Hard exit time | 9:30 ET | 9:00, 9:15, 9:30, 10:00, 11:00 ET | Sensitivity to NY-open narrative shift |
| Instrument selection | ES + XAU | ES only / XAU only / FX (DXY) only | Instrument-conditional edge |
| Core vs headline surprise weighting | headline | headline, core, both required, max(|h|,|c|) | Which surprise drives retracement |
| Regime filter (VIX) | none | <15, 15-22, 22-30, >30 | Vol-regime conditioning |
| Trade only revisions vs initial print | initial only | initial / revision / both | Revision-day behavior is different |

---

## 13. NFP Volatility Expansion Breakout

**Concept (1 line):** Trade the directional break of the first 5-minute post-NFP reference candle — but disable on revision-heavy months and on prints where pre-release range failed to compress.

**Honest edge status:** NFP breakout is structurally sound *in principle* — pre-release liquidity compression followed by genuine vol expansion is repeatable. Two material concerns the original draft missed: (a) post-pandemic NFP data quality has been visibly degraded with frequent multi-month revisions (e.g., 2024 annual benchmark revision of -818k), and revision-heavy prints generate whipsaw rather than clean directional moves; (b) the "5-minute reference candle" entry implicitly assumes fillable liquidity at the candle's edge, which is often not present in the first minute after the print. Use the strategy with explicit disabling rules.

**Edge sources:** pillar-05-macro-intermarket-news (scheduled vol expansion), event-driven-strategy-design (range-break framework around the print)

**Why it has edge:** Pre-release liquidity withdrawal creates a tight reference range; the post-release directional close above/below that range reflects which side of pre-positioning has been absorbed. The trade does not predict the NFP number, only acts after direction is revealed.

### Entry model
- **Instrument & timeframe:** ES or XAUUSD. 5-minute setup, 1-minute trigger validation. NFP at 8:30 ET, first Friday monthly (BLS — verify holiday shifts).
- **Setup conditions:**
  1. Confirmed NFP day; verify against BLS calendar.
  2. **Revision-disable rule:** if the prior month's NFP was revised by more than 50k in absolute terms, *do not trade this month*. Revision noise contaminates the response function.
  3. **Pre-release compression check:** 30-min range immediately before 8:00 ET ≤ 0.3% of ES (or ≤ 60 ticks XAU). If not compressed, the breakout reference is unreliable — stand aside.
  4. Define the NFP reference range as the open-to-close of the 8:30-8:35 ET candle.
  5. Do not enter during the 8:30-8:35 candle itself.
  6. Spread guard: by trigger time, quoted spread within 2x median.
- **Trigger:** 8:35-8:40 ET 5-minute bar closes cleanly above the 8:30-8:35 high (long) or below its low (short). "Cleanly" = closing price, not wick.
- **Invalidation:** Subsequent 5-minute candle closes back inside the reference range. Failed breakout — exit immediately, do not wait for stop.

### Position management
- **Initial stop:** Opposite extreme of the 8:30-8:35 reference candle.
- **Risk per trade:** 0.25% of account; if the reference range is wide enough to imply >0.5% risk at standard unit, reduce size proportionally rather than widening tolerance.
- **Scaling:** Full size at trigger. No scale-in.

### Exit model
- **Targets:** T1 = 1x reference-candle range from breakout. T2 = 2x range.
- **Trailing:** Stop to break-even after T1. Trail T2 portion by 5-minute swings.
- **Time stop:** Hard exit by 10:00 ET (90 minutes post-release).

### Confluence pairings
- Breakout aligned with HTF daily bias (prior-day OHLC structure).
- VWAP remains on the correct side of price by 9:00 ET.
- Volume spike on breakout candle without iceberg absorption on the opposite side.

### Fine-tune parameters

| Parameter | Default | Search range | What to measure |
|-----------|---------|--------------|-----------------|
| Reference-candle duration | 5 min | 3, 5, 10, 15 min | Signal vs noise on initial bar |
| Confirmation-close timeframe | 5 min | 1, 3, 5, 10 min | False-break vs lag trade-off |
| Pre-release compression cap | 0.3% ES | 0.2, 0.3, 0.4, 0.5%, no cap | Compression as setup precondition |
| Prior-month revision disable threshold | 50k | 25k, 50k, 100k, 200k, off | Revision-contamination effect |
| Hard exit time | 10:00 ET | 9:00, 9:30, 10:00, 11:00, EOD ET | Move-duration sensitivity |
| T1 multiplier of reference range | 1.0x | 0.5, 1.0, 1.5, 2.0x | Optimal first-target placement |
| Failed-break exit rule | next-bar close back inside | same bar wick / next bar / 2 bars | Re-entry-after-fakeout frequency |
| Spread guard multiplier | 2.0x | 1.5, 2.0, 3.0, 5.0x | Implementability filter |
| Instrument selection | ES + XAU | ES, XAU, DXY, EURUSD separately | Cross-instrument edge consistency |
| Surprise-direction filter | none | trade only when surprise sign = breakout sign | Whether trend follows surprise |

---

## 14. London-NY Session Overlap Vol Trade

**Concept (1 line):** Range-break setup at the NY pre-open, conditioned on calendar quiet and tight pre-overlap compression.

**Honest edge status:** "Trade the London-NY overlap because volume is highest" is not by itself an edge — it is liquid-hours selection, available to every participant. The original framing implied edge from the session window itself, which does not survive scrutiny. The real testable claim here is narrower: *that a tight 6:00-8:00 ET consolidation followed by a confirmed break before 9:45 ET captures NY-open directional flow with better risk-reward than ad-hoc intraday breakouts*. Treat this as an opening-range-breakout (ORB) variant, not a uniquely edged session trade. If the strategy does not beat a plain ORB benchmark in backtest, drop it.

**Edge sources:** pillar-05-macro-intermarket-news (session structure as calendar-driven volume mechanic), pillar-06-sentiment-positioning (intraday vol regime conditioning via VIX term structure)

**Why it might have edge over plain ORB:** Pre-overlap compression filters out chop days; the calendar quiet filter avoids event contamination; the NY kill-zone window biases entries toward institutional participation. None of these are exclusive — benchmark rigorously against a vanilla ORB before deploying.

### Entry model
- **Instrument & timeframe:** ES, EURUSD, or GBPUSD. 15-min range, 5-min trigger.
- **Setup conditions:**
  1. Calendar blackout passes — no Tier-1 release between 6:00-11:00 ET. This strategy is *not* the one to use on CPI/NFP/FOMC days; strategies 11-13 supersede.
  2. Pre-overlap consolidation range: 6:00-8:00 ET, range width ≤ 0.4% ES (≤ 30 pips EURUSD). If wider, "consolidation" is too loose for a clean break.
  3. VIX in 13-22 range. Below 12 produces range-bound overlap; above 25 produces whipsaw.
  4. Spread guard at trigger.
- **Trigger:** 5-min bar closes above range high (long) or below range low (short), 8:00-9:45 ET window.
- **Invalidation:** Recapture of opposite range boundary within 2 bars (failed break). Exit. Also exit on any ad hoc news headline.

### Position management
- **Initial stop:** Range midpoint if range ≤ 0.2%; opposite range boundary if range 0.2-0.4%.
- **Risk per trade:** 0.4% of account. Non-event setup, tighter range — slightly larger size acceptable than the event strategies.
- **Scaling:** 50% at trigger, +50% on retest of broken boundary without close-back-inside.

### Exit model
- **Targets:** T1 = 1x range width from breakout. T2 = prior session high (longs) or low (shorts).
- **Trailing:** Stop to break-even after T1. Trail T2 via 15-min swings.
- **Time stop:** Hard exit by 11:30 ET.

### Required benchmark
Before deployment, backtest against a vanilla ORB (first 30-min range of regular session). If this strategy's risk-adjusted return does not exceed the plain ORB by a margin larger than implementation cost, *do not deploy* — the marginal complexity is not justified.

### Confluence pairings
- ICT London / NY kill zone overlap structure.
- Developing VWAP as dynamic support/resistance through 10:00 ET.
- Prior-day VAH/VAL as natural T1 magnet.

### Fine-tune parameters

| Parameter | Default | Search range | What to measure |
|-----------|---------|--------------|-----------------|
| Pre-overlap range window | 6:00-8:00 ET | 4:00-8:00 / 5:00-8:00 / 6:00-8:00 / 7:00-8:00 / 7:00-9:30 ET | Range-quality vs sample-size trade-off |
| Max range width filter | 0.4% ES | 0.2, 0.3, 0.4, 0.6, no cap | Compression as setup precondition |
| Trigger window end | 9:45 ET | 8:30, 9:00, 9:30, 9:45, 10:30 ET | Stale-break tolerance |
| VIX regime band | 13-22 | 10-15 / 13-22 / 18-28 / no filter | Vol-regime edge stratification |
| Failed-break recapture window | 2 bars | 1, 2, 3, 5 bars | False-fail vs whipsaw cost |
| T1 (range multiplier) | 1.0x | 0.5, 1.0, 1.5, 2.0x | First-target placement |
| Hard exit time | 11:30 ET | 10:30, 11:00, 11:30, 12:30, 15:00 ET | Lunch-hour decay sensitivity |
| Calendar-blackout buffer | day-of only | ±4h, ±12h, day-of, ±24h | Adjacent-event contamination |
| ORB benchmark comparison | required | n/a | Whether this strategy beats vanilla ORB |
| Instrument selection | ES + EURUSD | each separately | Cross-instrument consistency |

---

## 15. RBI Policy Reaction Trade (Indian Markets)

**Concept (1 line):** On RBI MPC days, BANKNIFTY's initial post-decision spike frequently fades; trade the *second* directional move that emerges after the Governor's press conference.

**Honest edge status:** The two-move pattern (spike → fade → second move) is consistent with the general central-bank-reaction template and is plausible for BANKNIFTY given its rate sensitivity. Two caveats: (a) sample size is tiny (6 MPC meetings/year × 5 years = 30 trials; statistical confidence is weak); (b) **SEBI F&O regulation is changing rapidly** — contract specs, lot sizes, expiry days, peak-margin rules, and weekly-vs-monthly availability have been revised repeatedly in 2023-2025. Verify *every* regulatory specification against the current NSE circular before each trade; do not trust the numbers below to be valid at deployment.

**Edge sources:** pillar-11-indian-market-specific (RBI MPC cadence, BANKNIFTY rate sensitivity, SEBI peak-margin regime), pillar-05-macro-intermarket-news (central bank reaction function, first-3-minute noise vs 5-15-minute signal)

**Why it might have edge:** BANKNIFTY's sector composition (heavy weight in private and PSU banks) makes it the most rate-sensitive Indian equity index. Initial algorithmic response on the rate print often overshoots because forward guidance — delivered later in the Governor's press conference — carries more durable information than the rate level itself. The second move reflects participants pricing the *path* after they hear the commentary.

### Entry model
- **Instrument & timeframe:** BANKNIFTY monthly futures. 15-min structure, 5-min trigger.
  - *Regulatory caveat (as of 2026-05):* BANKNIFTY weekly contracts were discontinued November 2024 under SEBI F&O reforms; monthly remains. Lot size has been revised; **verify current lot size and margin from the latest NSE circular before sizing any trade**. Peak-margin SPAN+ELM rules continue to apply with intraday snapshots.
- **Setup conditions:**
  1. RBI MPC decision day confirmed against current RBI calendar (6 meetings/year, roughly bimonthly Feb/Apr/Jun/Aug/Oct/Dec — verify each year).
  2. Rate decision released (typically 10:00-10:15 AM IST).
  3. Initial spike completed: 15-min candle prints a new session extreme vs pre-announcement 9:15-10:00 IST range.
  4. Spike retraces ≥ 50% of its body within the next two 15-min bars (fade confirmed).
  5. Governor's press conference has begun or concluded (typically 11:00-11:45 AM IST). Do not enter before the press conference — forward-guidance language routinely triggers the *actual* directional move.
  6. India VIX ≤ 22 (raised filter from original 25 — fear-regime trades are too noisy for the second-move thesis).
  7. Calendar blackout: no major global event (FOMC the prior evening US-time, ECB same day, etc.) within ±24h.
- **Trigger:** A 15-min bar closes opposite the initial spike (fade confirmed), AND a subsequent 15-min bar closes in the prevailing second-move direction. Enter on the break of the high (long) or low (short) of the last fade candle.
- **Invalidation:** Spike fails to retrace 50% within two 15-min bars (the spike may be the genuine move). Also invalidated if India VIX spikes above 25 intraday.

### Position management
- **Initial stop:** Beyond the initial spike extreme.
- **Risk per trade:** 0.2% of account (tightened from original 0.25% — RBI days carry binary press-conference risk and the sample is too small to lean heavily). Size in whole lots only; maintain ≥15% buffer above SPAN+ELM to avoid peak-margin penalty.
- **Scaling:** Single lot unless account allows two within 0.2% risk.

### Exit model
- **Targets:** T1 = opposite boundary of the 9:15-10:00 IST pre-announcement range. T2 = prior-day high (long) or low (short). Minimum 1:1.5 R if neither is reachable.
- **Trailing:** Stop to break-even after T1. Trail by 15-min swings on the runner.
- **Time stop:** Hard exit by 2:00 PM IST. Holding into the final 90 minutes exposes the position to expiry-day flows and mean reversion unrelated to the policy thesis.

### Regulatory monitoring rule
Before each MPC, run a 10-minute pre-trade check: (a) verify current BANKNIFTY lot size on NSE circular, (b) confirm monthly contract is the front month, (c) confirm SPAN+ELM margin and peak-margin schedule for that day, (d) confirm RBI press-conference time. If any spec has changed from the previous MPC, re-run sizing arithmetic from scratch.

### Confluence pairings
- Daily bias alignment (BANKNIFTY above/below prior-week VWAP).
- ICT liquidity framework: initial spike as buy-side or sell-side liquidity sweep; second move as post-sweep delivery.
- Developing volume POC near the second-move entry zone.

### Fine-tune parameters

| Parameter | Default | Search range | What to measure |
|-----------|---------|--------------|-----------------|
| Spike-fade retracement threshold | 50% body | 38.2, 50, 61.8, 78.6% | Spike-strength qualifier |
| Fade-confirmation window | 2 bars | 1, 2, 3, 4 × 15-min bars | False-fade rate |
| Wait-for-press-conference gate | required | required / optional / skip | Whether commentary is load-bearing |
| India VIX regime cap | 22 | 18, 22, 25, 30, no cap | Vol-regime conditioning |
| Pre-announcement range window | 9:15-10:00 IST | 9:15-9:45 / 9:15-10:00 / 9:30-10:15 IST | Range baseline placement |
| Hard exit time | 14:00 IST | 13:00, 14:00, 14:30, 15:15 IST | Expiry-flow contamination |
| T1 placement | opposite range edge | 50% / opposite edge / 1.5x range | First-target reliability |
| Risk per trade | 0.2% | 0.1, 0.2, 0.35, 0.5% | Bankroll vs small-sample variance |
| MPC-meeting subset | all 6/yr | all / rate-change-only / hold-only | Whether edge concentrates in decision type |
| Margin buffer above SPAN+ELM | 15% | 5, 10, 15, 25% | Peak-margin penalty avoidance |

---

*End Part C.*


---

# Part D — Quant / Order-Flow / Cross-Asset Intraday Strategies (16-20)

*Reviewed and refined.*

## Family-wise honesty disclosure (applies to all 5 strategies)

These five strategies are tested on overlapping intraday datasets covering the same instruments and sessions. If you backtest all five and select the ones that look best, you are guaranteed to overfit by multiple-testing. With a per-strategy alpha of 0.05, the family-wise error rate across five strategies is approximately 1 - 0.95^5 = 0.226 — there is a 22.6% chance at least one passes by luck even if none has true edge. Apply a Bonferroni correction (alpha/5 = 0.01 per strategy) or, more practically, use the False Discovery Rate (Benjamini-Hochberg) procedure when judging the live deployment cut-off. Do not deploy any strategy whose backtested edge is within one standard error of the multiple-testing-adjusted threshold.

**Standing transaction cost assumption used throughout Part D:** Round-turn cost = 2 × spread + 1 tick slippage per leg + commission. For liquid US index futures (ES, NQ): assume 1.5 ticks round-turn all-in. For NIFTY/BANKNIFTY futures: assume 1 tick + brokerage. For XAUUSD CFD: assume 0.35-0.50 spread + slippage of 0.20 on stop hits during news. For FX majors: assume 0.5 pip spread + 0.3 pip slippage on stops. Stops cross the spread; targets earn it. If a strategy's backtested edge does not survive this cost assumption with a margin of at least 30%, it is not deployable.

**Stationarity warning:** Every parameter below assumes a stable distribution of the input variable (ATR, correlation, spread, OR width). Distributions shift with: (a) volatility regime changes — VIX moving from <15 to >25; (b) liquidity regime — pre/post a major exchange microstructure change; (c) macro regime — rate-hiking vs rate-cutting cycle. Re-fit parameters quarterly at minimum, walk-forward never in-sample.

---

## 16. ATR Squeeze Breakout (5m)

**Concept (1 line):** When intraday realized volatility compresses to a historically low percentile, the subsequent directional break of the compression range delivers a vol-expansion move that can be sized using percent-volatility targeting.

**Edge sources:** pillar-09-statistical-quant-thinking (volatility clustering — low vol precedes high vol; squeeze percentile is a statistically testable filter); pillar-04-volume-orderflow-liquidity (volume contraction during the squeeze confirms no active distribution; expansion volume on the break validates directional commitment)

**Why it has edge (2-3 sentences):** Volatility is mean-reverting in both directions: prolonged compression is followed by expansion. The squeeze percentile filter removes the majority of false breakouts that occur when ATR is already elevated, selecting only the highest-contrast compression-to-expansion transitions. Sizing by percent-volatility (risk = fixed fraction of account / current ATR) automatically scales position down in noisy regimes and up in clean ones, keeping dollar risk constant.

**Honest decay note:** ATR-squeeze / Bollinger-squeeze / TTM-squeeze variants have been published since the late 1990s (Hodge, Carter) and were widely automated by 2010. Multiple academic and practitioner studies (notably Carver in *Systematic Trading*) document that on liquid US large-cap equities and front-month index futures, the standalone squeeze-breakout edge has decayed materially since 2010-2012 as the pattern became a known target for sweep algos. The strategy is most likely to retain edge in (a) less-arbitraged instruments (NIFTY futures, second-tier FX crosses, commodity futures outside the top 5), (b) when combined with directional filters (do not buy a squeeze break against a clear higher-timeframe downtrend), and (c) in elevated-vol regimes where the squeeze itself is statistically rarer. Assume no naked edge on ES/SPY/QQQ unless your walk-forward shows otherwise on out-of-sample windows of at least 12 months.

**Direction-of-break filter (added):** Do not take squeeze breaks against the daily trend. Define daily trend by 50-period EMA slope on the daily chart, OR by daily RSI(14) — long-only breaks when daily close is above the 50 EMA AND daily RSI > 50; short-only breaks when daily close is below the 50 EMA AND daily RSI < 50. Squeeze breaks against trend have a documented false-break rate above 60% in retail-instrument samples post-2015.

### Entry model
- **Instrument & timeframe:** Any liquid intraday instrument (NIFTY futures, XAUUSD, ES futures, EURUSD). Primary chart: 5-minute. Percentile lookback computed on 5m ATR values across the trailing 20 trading days (~2,400 5m bars).
- **Setup conditions:** (1) ATR(14) on the 5m chart is below the 15th percentile of the trailing 20-day ATR distribution. (2) Price has been range-bound for at least 12 consecutive 5m bars (1 hour) within the compression. (3) The compression range high and low are clean — no single candle body has closed outside the range during the compression period. (4) Setup is valid only during active session hours (London open through NY midday); avoid Asia-only compression breaks that lack volume participation. (5) Daily trend filter (NEW): break direction must align with the daily 50 EMA slope and RSI(14) > 50 (long) or < 50 (short).
- **Trigger:** 5m candle closes above the compression range high (long trigger) or below the compression range low (short trigger). No anticipatory entry inside the range. Volume on the trigger bar must be ≥ 1.4× the 20-period 5m volume average.
- **Invalidation:** Price closes back inside the compression range within 3 candles after the break. ATR reading rises above the 40th percentile before the trigger fires (compression has already released).

### Position management
- **Initial stop:** Opposite edge of the compression range plus one ATR(14) buffer. This keeps the stop outside the statistical noise of the compression zone.
- **Risk per trade:** 0.25–0.5% of account. Percent-volatility sizing: position size = (account × risk%) / (stop distance in points × point value). Apply standing Part D cost assumption when computing expected R.
- **Scaling:** No scaling into the trade. Single entry on the break close.
- **Cooldown after loss:** After two consecutive losses on this setup in the same week, halt for 48 hours and re-validate the squeeze-percentile distribution — successive losses often signal a regime shift to elevated background vol where the 15th-percentile threshold no longer selects rare events.

### Exit model
- **Targets:** Target 1 = 1.5× the compression range height measured from the breakout edge. Target 2 = 2.5× the compression range height, or the next significant intraday structure level, whichever is closer.
- **Trailing / stop adjustment:** After Target 1 hit and 50% closed, trail using a 2-bar low/high on the 5m chart.
- **Time stop:** If Target 1 is not reached within 30 minutes of entry, exit the full position at market.

### Confluence pairings
- Slot 1–5: Compression at a higher-timeframe order block or FVG increases signal quality.
- Slot 6–10: Breakout that simultaneously clears developing VWAP or prior POC is a stronger signal.

### Fine-tune parameters

| Parameter | Default | Search range | What to measure |
|-----------|---------|--------------|-----------------|
| ATR period | 14 | 8, 14, 20, 30 | Walk-forward Sharpe by ATR-period choice |
| Squeeze percentile threshold | 15th | 5th, 10th, 15th, 20th, 25th | Trade frequency vs. win rate frontier |
| Percentile lookback window (days) | 20 | 10, 20, 40, 60 | Stationarity of squeeze threshold |
| Min bars in compression | 12 (1hr) | 6, 12, 18, 24 | False-break rate by compression duration |
| Trigger volume multiplier | 1.4× | 1.0×, 1.2×, 1.4×, 1.7×, 2.0× | Avg R per trade by volume filter |
| Daily trend filter (EMA period) | 50 | 20, 50, 100, 200 | With-trend vs against-trend hit rate gap |
| Stop ATR buffer | 1.0× | 0.5×, 1.0×, 1.5×, 2.0× | Stop-out rate vs MAE distribution |
| Target 1 multiple of range | 1.5× | 1.0×, 1.5×, 2.0×, 2.5× | Expectancy curve by T1 |
| Time stop (min) | 30 | 15, 30, 45, 60 | Avg R conditional on T1 not hit |
| Cooldown after 2 losses (hrs) | 48 | 0, 24, 48, 72 | Performance with vs without cooldown |

---

## 17. Opening Range Breakout (ORB) Momentum

**Concept (1 line):** The first 30-minute candle range after a session open defines a supply/demand equilibrium; a clean break of that range accompanied by above-average volume and confirmed by a trending regime filter has a statistically documented tendency to continue in the break direction.

**Edge sources:** pillar-04-volume-orderflow-liquidity (volume expansion on the break is the central confirmation); pillar-09-statistical-quant-thinking (regime filter — ORB outcomes differ materially in trending vs. mean-reverting regimes)

**Why it has edge (2-3 sentences):** The opening 30 minutes consolidates overnight orders, news reactions, and gap fills; once this equilibrium is broken with committed volume, the dominant side of institutional order flow is revealed. The regime filter (ADX > 20, or prior-day range expansion, or both) removes days where the market is choppy and ORB breaks repeatedly fail. Without the regime filter, ORB is marginal at best; with it, the setup selects days where trend-continuation is the base case.

**Honest sample-period note:** ORB research (Carver, Davey, and numerous published equity studies including Crabel's original 1990 work on opening-range breakouts) is unambiguous on one point — out-of-sample edge is highly sample-period dependent. ORB performed exceptionally in 1990-2007 US equity index futures, weakly in 2010-2019, and well again in 2020-2022 (vol regime). Davey explicitly warns that an ORB strategy looking good on a 5-year backtest can produce a 2-year flat-to-negative live run as the regime shifts. Anyone deploying ORB should (a) walk-forward at minimum 3 distinct vol regimes, (b) accept that drawdowns of 6-12 months are normal and not necessarily a sign of broken edge, (c) cap allocation accordingly. The 30-minute IB choice itself is a hyperparameter — test 15m, 30m, 60m honestly; the literature shows no canonical winner across instruments.

### Entry model
- **Instrument & timeframe:** Index futures (NIFTY, ES, NQ), liquid equities, or major FX pairs. Entry on 5m chart; ORB high/low on 30m chart (test 15m and 60m alternatives — see parameter table).
- **Setup conditions:** (1) Define OR: high/low of first 30 minutes of primary session. (2) Regime filter: prior day's ADX(14) > 20 on daily, OR prior day's range > 20-day average. (3) Trigger 5m candle volume ≥ 1.5× 20-period average. (4) No major scheduled news within first 30 minutes.
- **Trigger:** First 5m candle to close entirely above OR high (long) or below OR low (short). Full candle body must clear the boundary.
- **Invalidation:** Price re-enters OR after trigger close. OR width > 1.5× 20-day average OR width (extended OR has documented poor continuation).

### Position management
- **Initial stop:** Mid-point of OR (aggressive) or opposite OR boundary (conservative). Mid-point only when OR is narrow (< 0.5 ATR of prior day).
- **Risk per trade:** 0.25–0.5%. Cap position so risk never exceeds 0.5% regardless of OR tightness.
- **Scaling:** Single entry.
- **Cooldown after loss:** After two consecutive ORB losses in a week, skip the next 3 trading days — regime shifts are the most common cause of clustered ORB failures.

### Exit model
- **Targets:** T1 = 1× OR range projected from breakout edge. T2 = 2× OR range, or prior day high/low.
- **Trailing / stop adjustment:** After T1 hit and 50% closed, move stop to OR boundary in direction of trade. Trail using 15m structural lows/highs for remainder.
- **Time stop:** Exit any remaining position by 13:00 local session time.

### Confluence pairings
- Slot 6–10: Break that clears VWAP simultaneously with OR is higher-conviction.
- Slot 11–15: ORB aligned with overnight macro bias has materially higher continuation rate.

### Fine-tune parameters

| Parameter | Default | Search range | What to measure |
|-----------|---------|--------------|-----------------|
| OR duration (minutes) | 30 | 5, 15, 30, 60 | Sharpe by OR window |
| Regime filter: ADX threshold | 20 | 15, 20, 25, 30 | Win rate gated by ADX |
| OR-width veto (× 20d avg) | 1.5× | 1.2×, 1.5×, 2.0×, no cap | Reward by OR-width bucket |
| Trigger volume multiplier | 1.5× | 1.2×, 1.5×, 2.0× | False-break rate by volume |
| Stop placement | OR mid | OR mid, OR opposite, mid+0.5 ATR | Stop-out rate and avg loss |
| Target 1 (× OR range) | 1.0× | 0.5×, 1.0×, 1.5×, 2.0× | Expectancy by T1 |
| Session time-stop (local) | 13:00 | 11:30, 12:30, 13:00, 14:00 | Avg R from continued holding |
| Cooldown after 2 losses (days) | 3 | 0, 1, 3, 5 | Drawdown duration with/without |
| With-trend filter on/off | Off | On (HTF EMA), Off | Hit-rate delta with filter |
| Max trades per day | 1 | 1, 2 (allow re-entry on first failure) | Net edge of re-entry rule |

---

## 18. Footprint Absorption Reversal

**Concept (1 line):** At a key price level, when large aggressive order flow hits the book and is absorbed by passive liquidity without producing proportional price movement, the absorbed side is exhausted and the passive side gains control — a reversal entry is taken with the passive side.

**Edge sources:** pillar-04-volume-orderflow-liquidity (delta divergence and bid/ask absorption are direct measurement tools — this strategy cannot be executed without footprint or order-flow data); pillar-09-statistical-quant-thinking (signal is a conditional probability that must be verified empirically per instrument)

**Why it has edge (2-3 sentences):** Standard price charts hide the imbalance between buying and selling volume at each price level; footprint charts expose it. When large sell-delta accumulates at a key level but price fails to advance, the absorbing side is winning, and the path of least resistance is the opposite direction. The key-level requirement ensures absorption occurs at a meaningful reference point, not randomly mid-range.

**Brutal infrastructure note:** This strategy requires real-time consolidated tape and Level-2 / DOM data with footprint visualization (Sierra Chart, Bookmap, Jigsaw, ATAS, or equivalent). Costs run $100-300/month for data, plus exchange fees. CRITICAL: footprint absorption patterns are notoriously prone to **hindsight bias** — patterns visible on a replay look obvious; in live tape they are ambiguous and require sub-5-second decision time. Backtesting absorption setups on tick-replay overstates real-world edge unless the tester (a) freezes the screen at the decision moment with no forward information, or (b) uses an audited live-paper-trading record for at least 100 setups before claiming edge. This strategy is NOT suitable for traders without footprint/DOM tooling and demonstrated live tape-reading skill. It does NOT require co-location or sub-millisecond execution, but it does require professional-grade data and ~1-3 second discretionary decision speed.

**Sample-size warning:** Absorption setups at key levels occur 2-5 times per week per liquid instrument under strict criteria. You will need 6-12 months of live samples (50-200 trades) before backtest-vs-live divergence can be evaluated statistically. Do not deploy real size before this period.

### Entry model
- **Instrument & timeframe:** Liquid futures with reliable footprint data: ES, NQ, NIFTY futures, crude, XAUUSD futures. 3m or 5m footprint for signal; 15m for key-level context.
- **Setup conditions:** (1) Price at defined key level: prior session high/low, prior POC, daily VWAP, or historically respected round number. (2) At least 2 consecutive footprint bars with cumulative delta strongly in one direction but price not advancing beyond the key level. (3) Large stacked passive volume visible on the absorbing side. (4) No open news catalyst within 15 minutes.
- **Trigger:** Footprint bar closes with delta flipping back toward the passive direction AND price closes back below the key level (short) or above (long). Alternatively: clear imbalance flip on next bar.
- **Invalidation:** Price breaks cleanly through key level with expanding delta in the break direction — absorption has failed.

### Position management
- **Initial stop:** Beyond key level by 1 ATR(14) on 5m chart. Add 1 tick for entry slippage when computing risk.
- **Risk per trade:** 0.25–0.5%.
- **Scaling:** No scaling in.
- **Cooldown after loss:** After any absorption loss, skip the same key level for the rest of the session — the level has been broken or is being repeatedly tested by a determined seller/buyer.

### Exit model
- **Targets:** T1 = nearest 15m S/R in reversal direction, or 1× ATR from entry. T2 = prior swing low/high on 15m.
- **Trailing / stop adjustment:** After T1, trail using 3-bar high/low on 5m. Do not move to break-even before T1.
- **Time stop:** Exit remaining position if T2 not hit within 45 minutes.

### Confluence pairings
- Slot 6–10: Absorption at VWAP touch or prior VA high/low is the highest-probability variant.
- Slot 1–5: ICT order block or FVG aligning with absorption level confirms structural significance.

### Fine-tune parameters

| Parameter | Default | Search range | What to measure |
|-----------|---------|--------------|-----------------|
| Footprint bar interval | 5m | 1m, 3m, 5m, 10m, volume-bar | Signal-to-noise by interval |
| Min consecutive absorbing bars | 2 | 1, 2, 3, 4 | False-signal rate |
| Delta-flip magnitude threshold | qualitative | top 25%, top 10%, top 5% of session delta | Quantitative trigger frequency |
| Key-level type | prior H/L, POC, VWAP | each type separately | Edge per level-type |
| Distance buffer from level (ticks) | qualitative | 0, 1, 2, 3 ticks beyond | Stop-out vs entry quality |
| Stop ATR multiple | 1.0× | 0.7×, 1.0×, 1.5×, 2.0× | MAE vs stop-out tradeoff |
| Target 1 (× ATR) | 1.0× | 0.5×, 1.0×, 1.5× | Reward capture rate |
| Time stop (min) | 45 | 20, 30, 45, 60 | Avg R from time-out exits |
| News blackout window (min) | 15 | 5, 15, 30, 60 | Catastrophic loss rate by window |
| Cooldown: skip same level for session | Yes | Yes / No | Repeat-test loss frequency |

---

## 19. Intraday Z-Score Mean Reversion on Liquid Pairs

**Concept (1 line):** Two instruments with a historically stable spread relationship — when the intraday z-score of their spread exceeds 2 standard deviations, fade the divergence and trade mean reversion back toward the spread's intraday mean.

**Edge sources:** pillar-09-statistical-quant-thinking (cointegration is the statistical prerequisite — and is unstable intraday); pillar-04-volume-orderflow-liquidity (both legs must be liquid enough for simultaneous execution without material slippage)

**Why it has edge (2-3 sentences):** Pairs like EURUSD/GBPUSD share the USD leg and most of their macro driver set, creating mean-reverting spread dynamics that can be exploited intraday. The z-score is a normalized, dimensionless entry signal comparable across spread levels and sessions. The edge degrades or disappears in regimes where the cointegration relationship breaks down, so regime testing is non-optional.

**Statistical assumption (REQUIRED — read carefully):**

1. **In-sample p < 0.05 cointegration does NOT imply forward cointegration.** Engle-Granger and Johansen tests are diagnostic, not predictive. A pair that tests cointegrated on the trailing 5 days can fail to cointegrate tomorrow without warning. Treat the test as a *necessary but insufficient* filter.

2. **Cointegration is unstable intraday.** Half-life of intraday cointegration in FX major-major pairs is empirically 5-20 trading days. In equity-index pairs (NIFTY/BANKNIFTY) it is often shorter and breaks around quarterly index rebalances, dividend events, or sector-specific news.

3. **Regime kill switch (NEW, mandatory):** Compute rolling 5-day correlation of 5m returns between the two instruments. If correlation drops below 0.60 (FX pairs) or 0.70 (Indian index pair), halt the strategy and re-run cointegration test on the new window. Do not re-deploy until correlation re-establishes for at least 2 sessions and Engle-Granger p < 0.05 on new window. The kill switch is non-discretionary.

4. **Asymmetric event days are automatic skip:** BOE-only, ECB-only, RBI-only, sector-specific catalysts.

### Entry model
- **Instrument & timeframe:** EURUSD vs. GBPUSD or NIFTY 50 vs. BANKNIFTY futures. 5m bars.
- **Setup conditions:** (1) Rolling cointegration confirmed: Engle-Granger on prior 5 days of 5m data, p < 0.05. (2) Hedge ratio (beta) from OLS, updated daily. (3) Spread = A − beta × B. (4) Rolling z = (spread − SMA(60)) / std(60). (5) |z| crosses 2.0. (6) Skip first 30 minutes of session. (7) NEW: rolling 5d correlation ≥ 0.60 (FX) / 0.70 (Indian) — kill switch active.
- **Trigger:** z crosses 2.0: sell overperforming leg, buy underperforming leg as spread order. If non-simultaneous, leg into more liquid first, second within 2 seconds.
- **Invalidation:** z extends beyond 3.5 — exit both legs immediately.

### Position management
- **Initial stop:** z = 3.5 on either side. Size each leg so a 2.0→3.5 move costs ≤ 0.25–0.5% of account on net spread.
- **Risk per trade:** 0.25–0.5% on combined spread. Apply standing Part D cost assumption per leg — pairs trades pay round-turn on both legs, which can consume 30-50% of theoretical edge if not modeled.
- **Scaling:** No scaling.
- **Cooldown after loss:** After any 3.5-z stop-out, halt for the rest of the session AND re-run cointegration test before next session.

### Exit model
- **Targets:** T1 = z → 1.0 (close 50%). T2 = z → 0 (close remaining).
- **Trailing / stop adjustment:** After T1, move spread stop to z = 2.5.
- **Time stop:** If z has not reverted to ≤ 1.0 within 90 minutes, exit at market.

### Confluence pairings
- Slot 11–15: Hard avoid 30 min before/after asymmetric central-bank events.
- Slot 6–10: Both legs should be near respective VWAPs at divergence; if one leg is at a structural extreme, divergence may be structurally driven.

### Fine-tune parameters

| Parameter | Default | Search range | What to measure |
|-----------|---------|--------------|-----------------|
| Cointegration test window (days) | 5 | 3, 5, 10, 20 | Stability of beta and p-value |
| z-score lookback (bars) | 60 (5h) | 30, 60, 120, 240 | Half-life match to lookback |
| Entry z-threshold | 2.0 | 1.5, 2.0, 2.5, 3.0 | Frequency vs win rate |
| Exit z-threshold (T1) | 1.0 | 0.5, 1.0, 1.5 | Avg R per trade |
| Stop z-threshold | 3.5 | 3.0, 3.5, 4.0, 4.5 | Tail-loss frequency |
| Correlation kill-switch threshold | 0.60 / 0.70 | 0.50, 0.60, 0.70, 0.80 | Halted-period avoided-loss vs missed-trade |
| Hedge-ratio refresh frequency | daily | intraday, daily, weekly | Beta drift impact |
| Time stop (min) | 90 | 45, 60, 90, 120 | Avg R from time-out exits |
| Session blackout (open minutes) | 30 | 15, 30, 45, 60 | False-signal rate at open |
| Cooldown after stop-out | full session | next bar, next hour, full session | Repeat-failure rate |

---

## 20. Gold-DXY Divergence Intraday

**Concept (1 line):** XAUUSD and DXY maintain a historically inverse relationship; when the two diverge in the same direction for more than 30 consecutive minutes on an intraday basis, one instrument is mispriced relative to the other and a catch-up move is likely.

**Edge sources:** pillar-05-macro-intermarket-news (inverse relationship is macro-structural); pillar-09-statistical-quant-thinking (correlation must be validated on rolling intraday basis — relationship varies by regime, session, macro context)

**Why it has edge (2-3 sentences):** The Gold-DXY inverse relationship is driven by real dollar purchasing power. Sustained intraday divergence is typically temporary and caused by one instrument reacting faster to a catalyst; catch-up usually occurs within 30–90 minutes. Edge is in identifying divergence, waiting for the 30-minute minimum, and trading the lagging instrument in the expected catch-up direction.

**Statistical assumption — regime fragility (read this twice):**

The Gold-DXY inverse correlation is empirically REGIME-DEPENDENT and has broken down repeatedly in modern history:

- **2008-2009 (GFC):** correlation went positive intermittently as both DXY and gold rallied on safe-haven flows.
- **2011-2012:** ECB crisis broke correlation as gold tracked European credit risk independent of DXY.
- **2022 (rate shock):** Fed hiking cycle drove DXY higher AND gold higher simultaneously for extended periods — central banks (PBoC, RBI, CBRT) were net buyers of gold even as real yields rose, severing the textbook relationship for ~9 months.
- **2023-2024 (ETF flow shocks):** GLD redemptions and Asian central bank accumulation drove gold independently of DXY for multi-week stretches.

**Mandatory correlation gate (NEW):**

1. Rolling 10-day intraday correlation between XAUUSD 5m returns and DXY 5m returns must be ≤ -0.30. If correlation is above -0.30, strategy is PAUSED.
2. Additionally, compute rolling 30-day correlation. If 30-day correlation is above -0.20, this is a regime-shift warning — pause for one full week and reassess. Do not chase a re-correlating relationship; wait for stable two-week confirmation.
3. Auto-pause on: FOMC weeks, NFP day, major CPI prints, geopolitical risk-off events (VIX > 25 intraday).

### Entry model
- **Instrument & timeframe:** XAUUSD (spot or futures). DXY signal-only. 5m chart for divergence; 15m for context.
- **Setup conditions:** (1) Rolling 10-day intraday correlation ≤ -0.30 AND 30-day correlation ≤ -0.20. (2) Divergence window: both rising or both falling for at least 6 consecutive 5m bars (30 minutes), same direction on ≥ 4 of 6 bars. (3) Magnitude check: one instrument has clearly moved more — that one is "right," the lagger is the trade. (4) Session: London open through NY open only.
- **Trigger:** After 30-minute divergence confirmed, enter the lagging instrument in the direction implied by the leader. Entry on next 5m bar open.
- **Invalidation:** Divergence resolves in the wrong direction; or any major scheduled macro release within the 30-90 minute hold window.

### Position management
- **Initial stop:** 1.5× ATR(14) on 5m XAUUSD from entry.
- **Risk per trade:** 0.25% maximum (lower than other Part D strategies — correlation can break without warning).
- **Scaling:** None.
- **Cooldown after loss:** After any loss, re-compute 10-day correlation immediately. If correlation has weakened toward -0.20, pause for 48 hours.

### Exit model
- **Targets:** T1 = DXY-implied fair value of gold using rolling 10-day beta. T2 = T1 + 0.5× ATR if momentum extends.
- **Trailing / stop adjustment:** After T1 hit, move stop to break-even. Exit immediately if DXY reverses — the thesis depended on DXY direction.
- **Time stop:** Max hold 90 minutes.

### Confluence pairings
- Slot 11–15: USD-related releases (FOMC, NFP, CPI) create the cleanest divergence catch-ups — but ONLY trade these post-event, never through them.
- Slot 1–5: Gold catch-up aligned with HTF ICT order block or liquidity draw = highest-confidence variant.

### Fine-tune parameters

| Parameter | Default | Search range | What to measure |
|-----------|---------|--------------|-----------------|
| Rolling correlation window (days) | 10 | 5, 10, 20, 30 | Stability of correlation estimate |
| Correlation gate threshold | -0.30 | -0.20, -0.30, -0.40, -0.50 | Trade frequency vs hit rate |
| Long-window correlation check (days) | 30 | 20, 30, 60 | Regime-shift detection lead time |
| Min divergence window (bars) | 6 (30m) | 4, 6, 9, 12 | False-divergence rate |
| Required same-direction bars in window | 4 of 6 | 3, 4, 5, all | Signal purity vs frequency |
| Stop ATR multiple | 1.5× | 1.0×, 1.5×, 2.0×, 2.5× | MAE vs stop-out |
| Beta refresh frequency | daily | intraday, daily, weekly | Fair-value-target accuracy |
| Time stop (min) | 90 | 45, 60, 90, 120 | Avg R from time-out exits |
| Session window | London-NY open | London only, NY only, full overlap | Edge by session |
| Cooldown after loss (hrs) | 48 | 0, 24, 48, 72 | Repeat-failure rate during regime shift |

---

## Closing audit notes

- **Multiple testing across Part D:** treat the 5 strategies as a family. Adjust deployment thresholds accordingly. Do not run all five concurrently at full risk allocation in the same instrument family.
- **Transaction costs:** apply the Part D standing cost assumption to every backtest expectancy figure. Pair trades (strategy 19) pay double.
- **Infrastructure honesty:** strategy 18 (footprint absorption) requires professional data and live tape skill — do not attempt with retail charting. Strategies 16, 17, 19, 20 are executable on retail platforms (TradingView, NinjaTrader, MT5) with broker-quality data — none require co-location or sub-millisecond infrastructure.
- **Stationarity reminder:** every parameter table above implies a stable distribution. Re-fit quarterly. Walk-forward with windows large enough to span the parameter degrees of freedom × 30 trades minimum.
- **Cooldown after loss is not optional comfort — it is regime-shift insurance.** Most strategy blow-ups occur during a regime change that the trader rationalizes as "bad luck" rather than re-validating the underlying statistical assumption.
