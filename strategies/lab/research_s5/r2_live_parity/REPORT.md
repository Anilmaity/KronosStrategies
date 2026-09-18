# r2_live_parity — the live book against the sim, 2026-07-01 → 2026-09-18 (report 2026-09-18)

Pre-registration: `PROTOCOL.md` (two recorded additions, both after the first run, both listed
there). Scripts `00_extract.py … 07_table.py`; every number below is copied from
`results/0N_*.txt` / `results/*.csv`. Live record: prod Postgres read-only snapshot taken
2026-09-18 12:00 UTC (1,445 signals, 867 positions, 1,780 broker deals, 633 manager actions).
Harness: `lab/harness.py` on `bars_cache_2y` (to 09-17) with the live configuration of D6;
every harness trade re-resolved on the S5 quote; every REJECTED live signal resolved from its
own levels at the price the market actually offered.

---

## One page for the operator

**What the live book did (broker truth, 07-01 → 09-18).** The four engine legs placed 515
trades for **+123.8 pts, PF 1.09, mean R −0.016, +$689**. Per leg: S100 +103.8 pts / PF 1.17
(n 268), S94 +34.1 / 1.13 (n 85), S99 +4.7 / 1.02 (n 102), S93 −18.8 / 0.90 (n 60). **None of
the four has a live mean R distinguishable from zero** (bootstrap 90 % CIs all straddle 0;
`06_book`). The whole account over the same window is **+$169**: engine +$1,006, manual mobile
scalps +$179, **copy slot −$1,016** (Position-view: engine +$650, copy −$1,034). The copy slot
(free −$827, PF 0.6; VIP −$394, PF 0.3) is the only part of the book with a clearly negative
sign, it is accelerating (−$743 in September to date), and since 08-18 it has supplied
**half of every kill-switch trip** (copy −$116 / −$123 / −$149 of the three most recent trips).

**Sim vs live is no longer a mystery — it is two errors of ~0.8 pt/trade that cancel.** Signal
generation is exact (0–5 unexplained harness trades per leg inside live-active windows). The
harness books every trade at the nominal level and charges 0.45; live fills at the market ~0.8 s
after a signal that is itself ~65 s after the bar close, and the `entry_drift` gate rejects the
adverse half of that noise. Result: **placed trades fill 0.35–0.45 pt better than the nominal
level** (all four legs, CIs exclude 0), and **drift-rejected signals would have filled 1.3–1.9 pt
worse**. The plain harness therefore undervalues what live placed (execution term −372 pts on 455
matched pairs, outcome agreement 94–100 %) and overvalues what live refused (+202 pts nominal vs
**−126 pts at the offered price**). With the drift gate and a market fill modelled
(`02c_drift_model`), harness PF on live-active windows moves from 1.14 / 0.82 / 1.10 / 0.89 to
**1.25 / 0.91 / 1.24 / 1.11** (S93 / S94 / S99 / S100) against live 0.90 / 1.13 / 1.02 / 1.17 —
inside the live sampling noise. **Adopt the drift-gate + market-fill model as the lab's parity
comparator; retire "nominal level + flat cost" for anything that is compared with live.**

**Every entry gate is net protective and should stay.** The 763 rejected signals of the four legs,
resolved at the offered price with live slippage: **−489 pts, PF 0.75, −$4,490** at live sizing,
versus +124 pts / +$689 for what was placed. By gate (pooled, pts with slip / USD): entry_drift
−126 / −$1,281 (239 signals), sl_too_tight −118 / −$1,179 (188), open_position_cap −92 / −$876
(78), soft_daily_brake −96 / −$530 (120), metaapi_rejection −73 / −$534 (63, luck), news −34 /
−$303 (20), duplicate −8 / −$67 (15); the one exception is **no_add_to_loser +58 / +$280 (40)** —
small, keep. Loosening the drift budget to 1.0 pt would have admitted 64 more trades for +4 pts
(PF 1.02) — nothing.

**Execution is better than the model, not worse.** Live stop-out slippage in 07–16 UTC is
**+0.26 pt [90 % CI 0.20, 0.33] vs the execution study's 0.52–0.62** (the S5 proxy was declared
an upper bound; it is ~2×). TP fills are 0.07–0.23 pt *better* than the level. Broker fills sit
0.08–0.24 pt inside the OANDA sided quote. Median signal→fill latency 0.83–0.89 s. One structural
cost was found and is already gone: for signal stops under the broker's **3.0-pt stops floor**
(`shared/metaapi_client._DEFAULT_MIN_STOP_DISTANCE`) the broker backstop sits at 3.0 and the
strategy stop lives only in the monitor's active close; **49 of 350 stop-outs filled on the wider
backstop at +0.95 pt extra each (−$479), 44 of them in July, 5 in August, 0 since** — the monitor
now holds tight stops at +0.06 pt mean slip.

**The rails.** Seven real kill-switch trips (07-10, 08-07, 08-12, 08-18, 08-25 at $150; 08-28,
09-09 at $250) plus the known false 07-09 trip that exit-day attribution does not reproduce. The
engine trades the harness would have taken after each trip: **−134 pts / −$919 over 7 days (WR
10–28 %)** — the switch saved money on 6 of 7 days (cost $220 on 08-28). The soft brake refused
120 signals worth **−96 pts / −$530** (PF 0.74). Of the 57 trading days, 19 closed ≤ −$120, 11 ≤
−$150, 2 ≤ −$250; the harness's 6-leg TEST book at $38/R would close ≤ −$150 on 30 % of days and
≤ −$250 on 20 % — **the kill-switch at this sizing is a throughput limiter that trips one day in
five, not a ruin rail**; on the evidence it has not cost edge. **Concurrency does not bind**: the
full 09-18 roster holds ≥ 3 positions at once for 1.0 % of open-market minutes; a book cap of 3
refuses 22 of 1,491 entries worth −$79, a cap of 5 refuses none. The per-strategy cap of 1 refused
72 S100 signals worth −$879 — keep it.

**What the book should look like (evidence-weighted, not edge-weighted).**
1. **Copy slot: cut it to one channel at most and to the same $38 risk as the strategies, or arm
   it OFF.** It is −$1,221 on the window with PF 0.3–0.6, it takes 91 % of its calls twice, and it
   is now the main source of kill-switch days. This is the only change here with a measured
   four-figure effect. (Operator decision; the number is unambiguous.)
2. **Keep every gate, the $250 kill-switch, the $120 brake, max_concurrent 5 and per-strategy cap
   1 exactly as they are.** Nothing in the record says a rail is costing edge.
3. **Sizing: equal risk $38/R on c03, s14, S93, S94-long; half risk ($19/R) on S99 and S100 until
   c03 and s14 each have 100 live trades.** TRAIN-fitted edge weights put 0 on all four legacy
   legs and 2.8× / 3.2× on c03 / s14 (TEST book net R 1,667 vs 169 equal, max DD 32 R vs 51) — but
   c03/s14 were *selected* on that TEST half and have 3 live trades between them, so full
   edge-weighting is small-sample overbetting; the leg-drop test says the cheapest improvement is
   less S99/S100 (TEST book net R 169 → 314 without S99, → 431 without S100), and live S100 is 79
   trades and −99 pts below its 09-03 high-water mark (rolling-30 R −0.39). S93 is 48 trades and
   −74 pts below its 07-13 high (one 52-pt-stop trade is −51 of that).
4. **Watch rule (pre-declared in D11): a leg is cut when its live mean-R 90 % CI upper bound is
   below 0.** None is there yet; S100 is the one to watch — re-read at 100 trades of the new
   roster.
5. **Lab process: the parity comparator is now `02c` (drift gate + market fill + quote exits +
   0.26 SL slip).** c03/s14's "expect 1.25 / 1.23" from `REPORT_s5exit` are nominal-level numbers;
   under the live fill mechanism they should read *higher* on placed trades and the gate will
   refuse ~40 % of their signals. Their parity read should be done with `02c`, not the plain
   harness.

---

## 1. Pre-registered definitions (PROTOCOL.md D1–D12, verified)

- Timestamps: every relevant column is `timestamptz`; read with session TZ = UTC they are
  correct instants (signal_at 09:55:05Z ↔ broker deal 09:55:06Z). The vault's "created_at is
  −5:30" is a Django `USE_TZ=False` read artefact; it does not apply here. Harness `entry_time` is
  the bar OPEN; live `signal_at` is bar-open + 62–77 s (p10–p90; median 65 s) — the 5-s poll +
  20-s OANDA TTL. Match window [−180 s, +60 s] with the side key; matched levels are identical
  (|harness entry − live signal entry| median 0.00, p90 0.00, all four legs).
- Live trade: closed `XAU_USD` position with a broker ticket (531; 522 with broker deals, 9 on the
  `realized/lots` fallback — 8 ORB before the deals archive started 07-06 and 1 S100). Where both
  exist the fallback agrees with broker truth to 0.03 pt median (the reconciler works).
- Points = broker exit − broker entry (signed); R = pts / nominal stop. Costs: harness mid-M1
  0.75 / 1.00, quote-S5 0.45 / 0.70; live points carry the real spread and slippage, so the
  like-for-like comparator is quote-S5 @ 0.45 (and, better, `02c`).
- Config drift reproduced: S93 `_HOURS` (7,8,9,12,13,14) to 09-02 then (13,14); S94 both sides
  SD 2.0; S100 ER gate off; `MIN_SL_DIST_PTS` 1.5; news 12:25–12:45. Sensitivity: S93 July with
  `S93_SOFT_VETO=off` (the pre-opt15 module) gives 176 trades / −29.9 pts vs 122 / +18.4 with it.
- Live-active windows from the manager's START/PAUSE rows: S100 was not deployed before 07-23,
  S93/S94/S99 not before 07-06/07; kill-switch days are paused after the trip. Harness trades
  outside those windows are "pre-deployment / paused", not fidelity gaps.

## 2. (a) Per strategy: live vs harness on the same dates and bars (`07_table.txt`, `02c`)

Live-active windows only (harness n = trades the harness generated while the live runner was
active):

| leg | harness n | mid@0.75 pts / PF | mid@1.00 | quote@0.45 | quote@0.70 | **sim-with-drift-gate** n / pts / PF | **live** n / pts / PF / mean R / USD |
|---|---|---|---|---|---|---|---|
| S93 | 113 | +17.1 / 1.05 | −11.1 / 0.97 | +42.2 / 1.14 | +14.0 / 1.04 | 65 / +40.9 / **1.25** | 60 / −18.8 / **0.90** / −0.032 / −$122 |
| S94 | 147 | −105.6 / 0.82 | −142.4 / 0.77 | −103.4 / 0.82 | −140.2 / 0.76 | 84 / −24.2 / **0.91** | 85 / +34.1 / **1.13** / −0.162 / +$148 |
| S99 | 202 | +36.6 / 1.07 | −13.9 / 0.97 | +51.4 / 1.10 | +0.9 / 1.00 | 116 / +66.9 / **1.24** | 102 / +4.7 / **1.02** / +0.026 / +$186 |
| S100 | 436 | −167.8 / 0.87 | −276.8 / 0.79 | −136.3 / 0.89 | −245.3 / 0.81 | 247 / +62.0 / **1.11** | 268 / +103.8 / **1.17** / +0.018 / +$477 |

Bootstrap 90 % CIs on pts/trade (`02c`): S93 sim-gate [−0.76, +2.10] vs live [−2.38, +1.58]; S94
[−1.78, +1.30] vs [−1.11, +1.97]; S99 [−0.52, +1.66] vs [−1.30, +1.28]; S100 [−0.41, +0.94] vs
[−0.25, +1.05]. Every sim-gate/live pair overlaps; the live samples cannot separate PF 0.9 from
1.25 at these n. On the trades both took (matched PLACED pairs, sim-gate model) the per-pair
difference is −0.28 / −0.22 / −0.01 / −0.33 pt (S93 / S94 / S99 / S100): the residual sim
pessimism after the gate is the +0.26 SL slip assumption (live 0.09 excluding backstop rows) and
the broker's 0.08–0.24 fill advantage over the OANDA quote.

**Gap decomposition** (quote@0.45 plain harness − live, live-active windows, four-leg totals):
gap **−270 pts** = drift-rejected **+202** (nominal value; −126 at the offered price) +
metaapi-rejected −94 + soft-brake −76 + cap −38 + no_add_to_loser +17 + duplicate +2 + sim-only +5
− live-only +84 (16 S93 trades the harness never produced, −88 pts, incl. the 07-14 52-pt-stop
trade at −51) + **execution −372** on 455 matched pairs (−0.64 to −0.93 per pair; outcome
agreement S93 100 %, S94 100 %, S99 96 %, S100 94 %). The identity closes to 0.1 pt per leg
(`02_parity.txt`).

Per-leg gate shares at the offered price (`04_gates.txt`, pts with slip / USD): S100 — drift
−115 / −$1,117 (117), sl_too_tight −68 / −$685 (83), cap −92 / −$879 (72), brake +16 / +$149 (56),
metaapi −21 / −$214 (24), no_add +24 / +$200 (8); all rejected −260 / −$2,582, PF 0.71. S94 — drift
−49 / −$412 (42), sl_too_tight −23 / −$234 (57), brake −51 / −$416 (14), metaapi −53 / −$229 (9);
all −155 / −$1,384, PF 0.64. S99 — drift +11 / +$180 (47), sl_too_tight −25 / −$245 (47), brake −45
/ −$196 (31), no_add +31 / +$32 (17), metaapi +32 / +$133 (15), news −41 / −$127 (8); all −24 /
−$148, PF 0.93. S93 — drift +27 / +$67 (33), brake −16 / −$67 (19), metaapi −31 / −$225 (15),
no_add +9 / +$101 (12), news −19 / −$135 (6); all −49 / −$376, PF 0.83. Drift-rejected signals win
*more* often than placed ones (SL 55 % / TP 41 % vs 68 % / 30 %) and still lose — the +1.75 pt paid
at the offered price is what the gate refuses, not the setup.

## 3. (b) Execution (`03_execution.txt`)

Entry (n 514 with broker deals): fill vs nominal level, adverse +: **S100 −0.365 [−0.45, −0.28],
S93 −0.447 [−0.74, −0.17], S94 −0.375 [−0.69, −0.09], S99 −0.349 [−0.52, −0.18]**; 55–62 % of
fills favourable; accepted p5 −2.26, p50 −0.16, p95 +0.72. Decomposed: the market at signal time
is already 0.47–0.62 pt *past* the level in the trade's favour (`nominal_vs_mid`), and the broker
fills 0.08–0.24 pt inside the OANDA sided quote (`fill_vs_quote`; OANDA spread at fill 0.54–0.60).
Rejected `entry_drift` signals carried +1.31 / +1.63 / +1.83 / +1.45 pt adverse at the gate
(S100 / S93 / S94 / S99; medians 0.99–1.47; budget 0.5). By hour the favourable fill is largest at
13–14 UTC (−0.73 / −0.75) and near zero at 07 (−0.02). Latency signal → fill: median 0.83–0.89 s,
p90 1.7–2.5 s.

Stop-outs (n 350): slip beyond the signal stop **mean +0.21, median +0.04, p90 +0.67, 7.1 % > 1
pt**; excluding the 49 broker-backstop rows **+0.09 / +0.02 / +0.22**. By exit hour vs the
study's TEST-era model: live means 0.05–0.58 (n 9–33) against 0.31–0.88 (S5 overshoot) and
0.37–0.82 (jump model) — live is below the model in 17 of 19 hours; 07–16 pooled **+0.264 [0.203,
0.330] vs 0.517 / 0.618**. By stop bucket live vs model: < 2 pt 0.64 vs 0.37 (the backstop rows),
2–3 0.19 vs 0.42, 3–5 0.09 vs 0.52, 5–10 0.14 vs 0.72, 10+ 0.04 vs 1.56. Targets (n 157): filled
0.07–0.23 pt *better* than the level. All-in live cost per trade vs the nominal-level model (SL and
TP exits, n 507): **−0.263 [−0.354, −0.175] — live paid less than nothing** against a model that
charges 0.45–0.75.

Backstop (b5): 46 % of stop-outs had a signal stop < 3.0 pt; 30 % of those (49) filled on the
broker's 3.0-pt floor instead of the monitor's active close, at +0.95 pt extra each (total
+46.7 pts, −$479 at their lots; S99 16, S94 16, S100 9, S93 8); **44 in July, 5 in August, 0
since**. The 113 tight stops the monitor did close: +0.06 mean, +0.17 p90.

## 4. (c) Risk rails (`05_rails.txt`)

Kill-switch trips re-detected on the manager-view running total with the threshold history
(200 → 150 → 250): 07-10 −188 (engine −188, copy 0), 08-07 −160 (−160 / 0), 08-12 −154 (−135 /
−19), 08-18 −169 (−53 / −116), 08-25 −178 (−178 / 0), 08-28 −257 (−134 / −123), 09-09 −265 (−116 /
−149); 7 re-detected vs 8 ManagerAction rows (07-09 was the documented false trip). Engine
harness trades after the trip on those days: S93 12 / −36 pts / −$223, S94 10 / −41.5 / −$218, S99
17 / −25.8 / −$339, S100 29 / −30.8 / −$139 → **−919 USD saved** (by day: 07-09 −8, 07-10 −195,
08-07 −32, 08-12 −230, 08-18 −309, 08-25 −139, **08-28 +220**, 09-09 −224; the copy slot's
after-trip calls are not modelled). Days: 57 manager-view trading days, median −$37, best +$566,
worst −$369; ≤ −120: 19, ≤ −150: 11, ≤ −250: 2. Soft brake: 120 rejections on 16 days, counterfactual
−96 pts / −$530, PF 0.74, WR 35 % (S94 −$416, S99 −$196, S93 −$67, S100 +$149).

Concurrency: live `open_position_cap` 82 rejections, counterfactual −68 pts / −$647 (S100 72 /
−$879; c03 4 / +$229; S93 2; S99 4). Prospective 09-18 roster (S93 13–14 h, S94 long SD 2.5, S99,
S100, c03, s14 @3.0) over 07-01 → 09-17 in the harness: 1,491 trades; simultaneous open positions
0: 68.4 %, 1: 26.0 %, 2: 4.7 %, 3: 0.9 %, 4: 0.1 %, 5: 0.0 % of open-market minutes; net
same-direction exposure ≥ 2 for 3.7 %. Book cap 3 refuses 22 (1.5 %, −$79); cap 5 refuses 0.

## 5. (d) Book construction (`06_book.txt`)

Live legs (D11): S100 n 268, mean R +0.018 [−0.138, +0.177], PF 1.17, WR 27 %, SQN 0.19, HWM
09-03, 79 trades / −99 pts since, rolling-30 R −0.394. S99 n 102, +0.026 [−0.210, +0.260], PF 1.02,
SQN 0.18, HWM 07-09, 91 trades / −18 pts since. S94 n 85, −0.162 [−0.563, +0.238], PF 1.13, SQN
−0.66, HWM 09-16. S93 n 60, −0.032 [−0.308, +0.249], PF 0.90, SQN −0.19, HWM 07-13, 48 trades /
−74 pts since. Verdict by the pre-declared rule: **all four "indistinguishable from 0"** (S93, S94
indicative at n < 100); ORB / S95 / c03 / s14 below the n = 40 floor. Monthly: S100 Jul +3.8, Aug
+123.5, Sep −23.5 pts; S94 Jul −14.3 (R −23.0), Aug +52.5, Sep −4.1; S99 −22.3 / +9.1 / +17.8; S93
−28.4 / +11.2 / −1.5. Copy: free −$827 (180 legs, PF 0.6), VIP −$394 (148, PF 0.3); Jul +67, Aug
−358, Sep −743.

Correlations: live daily R (53 days) between legs −0.15 … +0.25; copy vs legs −0.07 … +0.15.
Harness 24-month daily R: TRAIN −0.15 … +0.35 (S93–S99 0.35), TEST −0.11 … +0.24. Diversifiable.

24-month legs (mid-M1 @0.80 lists, the nearest to the 0.75 convention): TRAIN mean R S93 −0.079,
S94-long −0.073, S99 −0.282, S100 −0.231, c03 +0.098, s14 +0.204; TEST +0.043, +0.017, −0.151,
−0.108, +0.388, +0.315. Edge weights from TRAIN (clip ≥ 0, cap 2×, same total risk): S93 0, S94 0,
S99 0, S100 0, **c03 2.78, s14 3.22**. Equal vs edge: TRAIN −490 R / DD 492 vs +931 / 87; **TEST
+169 R / DD 51 / worst day −18.2 vs +1,667 / 32 / −26.7**; live window +10 / 51 vs +436 / 32. Leg
drop on TEST (equal): without S93 160, S94 163, **S99 314, S100 431**, c03 −156, s14 −69 (full
169). Live four-leg book on the window: −9.5 R, max DD 42 R, worst day −16.7 R (= −$635 at $38);
harness c03+s14 alone on the same days: +146 R, DD 11, worst −8.6. At $38/R the 6-leg equal TEST
book: mean day +$31, sd $346, p5 −$495, worst −$690; ≤ −150 on 29.7 % of days, ≤ −250 on 19.6 %;
$25/R: 22.0 % / 8.6 %, annualised +$5,085, max DD −$1,263; $50/R: 35.9 % / 24.4 %, +$10,170,
−$2,526.

## 6. What this establishes, and what it does not

- Establishes: live signal generation equals the harness; the live/sim gap is the fill model
  (nominal level + flat cost) interacting with the drift gate, ~0.8 pt/trade each way; every gate
  is net protective at the offered price; execution is cheaper than the S5 model by about half;
  the backstop cost was a July artefact; the kill-switch and brake saved money on the record;
  the cap does not bind for the 09-18 roster; no live leg has an edge distinguishable from zero
  at 60–268 trades; the copy slot is the book's loss.
- Does not establish: that S100 or S99 are negative live (the CI rule is not met); that c03/s14
  will hold their TEST numbers live (3 trades; and their TEST half is the screen's selection
  half, so the edge-weighted TEST book is not an out-of-sample read); the cascade effect of a
  rejection freeing the slot (02c ignores it); anything about the copy slot's counterfactual;
  the kill-switch saving beyond 7 days / 68 trades (the after-trip WR 10–28 % is suggestive of
  loss-day persistence, not proof).
- Comparisons: no parameter search. The one post-hoc number (drift budget 0.75 → 25 trades,
  +43 pts, PF 1.64) is labelled as such and not recommended.

## 7. Next hypotheses (each needs its own pre-registration)

1. **Harness fill model**: fill at the S5 sided quote at bar-open + 66 s with the live drift
   budget, as `02c`, inside the harness loop (so cascades are modelled), and re-run the 24-month
   screen for c03 / s14 / S93 / S94-long. Prediction from this record: placed-trade PF rises
   ~0.1, trade count falls ~40 %, points roughly unchanged.
2. **Loss-day persistence**: on days that reach −$150 by 13 UTC, is the rest of the day's
   expectancy negative? 7 days here; the 24-month harness book has ~60 such days at $38/R.
   If it holds, the kill-switch is an edge, and the threshold should be set from the daily sd
   ($346 at $38/R), not from a round number.
3. **Copy-slot counterfactual** from `tg_signals` (levels are logged): what the channel is worth
   at $38 vs $100 risk, single vs double-armed, before the operator decides between VIP-only
   and OFF.
4. **S100 regime**: August +123 / September −23 pts on 235 trades; test whether the live
   drift-gate edge (favourable fill) is regime-dependent (5-s jump size) using the execution
   study's monthly jump series.

## Files

`PROTOCOL.md`; `00_extract.py`, `00b_extract_actions.py`, `common.py`, `01_harness_replay.py`,
`02_parity.py`, `02b_simonly.py`, `02c_drift_model.py`, `03_execution.py`, `04_gates.py`,
`05_rails.py`, `06_book.py`, `07_table.py`; `results/00_extract.txt … 07_table.txt`,
`results/live_*.parquet`, `deals.parquet`, `sim_*.parquet`, `parity_*.csv`,
`gates_counterfactual.csv`, `rails_*.csv`, `book_*.csv`, `exec_*.csv`, `02_parity_summary.csv`,
`02c_drift_model_summary.csv`, `07_table_a.csv`, `07_table_decomp.csv`.
