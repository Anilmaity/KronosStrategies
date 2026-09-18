# Kronos research context pack — read this in full before anything else (2026-09-18)

You are a senior quant/ICT researcher joining a project that has months of prior work. This
page tells you what the project is, what has already been established or refuted (so you do
not redo it), where every tool and dataset is, how to query the knowledge base, and how the
project judges evidence. Your mandate (in your task prompt) is open: you form your own
hypotheses. Your discipline is not open: every hypothesis is pre-registered before its
numbers exist, judged against a control, and reported whether it wins or loses.

## 1. The project in one paragraph
Kronos is a live XAUUSD algorithmic trading platform built on ICT/SMC concepts. Three repos
(`KronosStrategies` Python engine + backtester, `Kronos_Backend` Django/GraphQL, `kronos_frontend`
Next.js) share one Postgres. The engine's strategies (`strategies/backtest_strategies/sNN_*.py`,
`concept_strategies/cNN_*.py`; contract: `NAME`, `CONFIG`, `get_signal(w1m, w5m, w15m, now_utc)
-> Signal|None`) run as Docker services on a Lightsail box, place orders through MetaAPI on
a **$10k MT5 demo** (Winprofx-Demo, no live money), under a **strategy manager** with a $150/day
kill-switch, $120 soft brake and concurrency cap. Entry gates in evaluation order are
documented in the vault's `70 Code Map/Entry Gates Reference.md` — live places only ~40–45 %
of generated signals (`entry_drift`, `sl_too_tight`, brakes, caps); see
`20 Strategies/Backtest Fidelity - Live vs Sim.md` for why live ≠ sim (selection, not slippage).

## 2. What is trading right now (2026-09-18) and what we know about each
| strategy | concept | 24-mo screen (TEST PF @0.45 / 0.80, mid-M1) | live-trigger PF @0.80 (quote-S5) | notes |
|---|---|---|---|---|
| **S93 FVG scalp** (`s93_fvg_scalp`) | FVG continuation, NY 13–14 UTC only | 1.34 / 1.24 | 1.29 (sample) | hours narrowed 09-01 (`lab/REPORT_s93.md`); shipped `_TP_R` 1.5 is its own optimum |
| **S94 sweep reversal** | liquidity sweep reversal | 0.87 / 0.80 at shipped config → **fails** | — | runs **long-only, `_SD_MULT` 2.5** since 09-18 (`REPORT_s94_*_2026-09-18.md`): shorts lose everywhere; long edge partly beta |
| **S99 MSS+FVG** | sweep → MSS → FVG | 0.93 / 0.84 → **fails** | — | live since Jul; July drawdown diagnosed (`40 Incidents/July 2026 Drawdown.md`); no Stage-2 run yet |
| **S100 M3 combo** | M3 FVG+OB+RSI | 1.07 / 0.96 → **fails stress** | — | spec `20 Strategies/M3 Scalper Spec v3.md`; disable-OB refuted (`REPORT_followup_2026-09-03.md`); no Stage-2 yet |
| **c03 FVG fill** (`c03_fvg_fill`) | 5m FVG fill in impulse leg, killzones, H1 EMA-slope bias | **1.57 / 1.46** | **1.25** (TRAIN 1.13 / TEST 1.35) | deployed 09-18 05:00 UTC; median stop 3.5 pt; zero nominal-entry gap |
| **s14 OB mitigation + bias** (`s14_ob_mit_bias`) | 5m OB mitigation gated by 15m EMA21 | **1.67 / 1.40** | 1.11 at floor 1.5 → **1.23 with `MIN_SL_DIST_PTS=3.0`** (deployed 09-18 11:11) | ~3.3 trades/day now; sub-2-pt stops were net negative under live triggers |
| Neymar Telegram Copy / VIP | copy-trader of a Telegram channel | n/a | n/a | not a strategy; ignore unless your topic is the book |
Retired/rejected with reasons: `20 Strategies/Retired and Rejected Strategies.md` (S95 session breakout retired on live record though it passes the screen; ARM_SL_FLOOR A/B failed 3/3; kronos_combined_v2 decommissioned; s97 disqualified as a bar-range artefact).

## 3. What has already been established — do not re-derive, build on it
- **24-month ICT screen** (`lab/REPORT_xau2y_2026-09-18.md`, protocol `lab/PROTOCOL_xau2y_2026-09-18.md`): 15 modules × 2 costs, Stage-2 grid of 86 arms. Survivors c03, s14, s95, s93. Equal-risk book of the four: +1,196 R / 531 days, 23/25 months positive, daily-R correlations ≤ 0.2.
- **Quote-level exits on every c03/s14 trade** (`lab/REPORT_s5exit_2026-09-18.md`): the whole live-trigger haircut is stop-on-bid/ask geometry; s14's [1.5, 2) stops net negative; fixed by the 3.0 floor (`lab/REPORT_s14_minstop_2026-09-18.md`, pre-registered grid, TRAIN-justified, edge of grid noted).
- **Six S5 studies, all negative on the tradeable arms, all with matched controls** (`lab/research_s5/REPORT_S5_PROGRAMME_2026-09-18.md` + each folder's REPORT.md): mechanical sweep-and-reclaim, FVG displacement quality, Silver Bullet/macros, intraday CRT, Power-of-Three/Judas. What they *did* establish: a gold "sweep" is a half-spread 5–10 s poke followed by **continuation**; the Judas extreme is a within-day base rate (the opposite extreme forms in **Asia** 60–65 % of days); ICT windows concentrate **range not direction**, the live window being **09:50–11:10 NY**; displacement quality has TEST AUC 0.500; the C2-wick artefact generalises. Their "next hypotheses" sections are your best-leads list.
- **Execution, measured** (`lab/research_s5/execution/REPORT.md`, `results/cost_model.csv`): spread 1.2–2.3 bp of price (0.58–0.66 pts median 07–19 UTC now); gap-through slippage per stop-out 0.29–0.60 by hour, **doubled 2025→2026**; trigger geometry a flat 0.275 pts/trade for everyone incl. random entries; s97/s96/s95 book nominal entries the market never offered (c03/s14 do not). **Cost convention from now: mid-M1 harness 0.75 base / 1.00 stress; quote-S5 resolution 0.45 / 0.70.** (0.45 on mid-M1 is retired.)
- **Optimization campaign 2026-09** (`20 Strategies/Optimization Campaign 2026-09.md`, `lab/CAMPAIGN.md`, `REPORT_s93/s94/s99/s100.md`): the roster sits within a few points of break-even; friction is first-order; S93 hours change shipped; global stop floor, Asia thin-liquidity, higher TP multiples, S94 `_MIN_RR`/`_STOP_BUF`/`_CONFIRM_N` all refuted; break-even-at-1R rejected; S100 disable-OB inverted by shared `_pending` state (filtered-CSV attribution can flip a sign — never judge a change by filtering a saved trade list; re-run the strategy).
- **Session timing on gold** (`50 Research/Session Timing on Gold.md`): time-of-day buys opportunity, not accuracy; the NY-AM window returned −0.057R vs the rest of the day on 7,835 events.
- **C2 wick claim refuted** (`50 Research/C2 Wick Claim Refuted.md`) and **Backtest Methodology Traps** (`60 Concepts/Backtest Methodology Traps.md`): geometric confounds, exit-resolution flips, `label="left"`, the control doing the work, units bugs, power before interpretation, harness properties read as market properties, silent market-structure changes. Read it; every study so far has been bitten by at least one.
- **The TTrades method** (`50 Research/TTrades Method Spec.md`, ~1,930-line spec in the RD repo, plus a 1,536-file concept corpus indexed in `kb`): the channel's own ordered method — HTF frame → bias/draw on liquidity → fractal model C1–C4 → POI gate + CISD → risk = protected swing, targets = liquidity/imbalances. Its governing rule: **no entry without a higher-timeframe reason**. This has NOT been tested as an overlay on the roster.
- **Manager sim backtest** (`20 Strategies/Manager Sim Backtest.md`): gating is a drawdown reducer, not a return edge. **July drawdown** (`40 Incidents/July 2026 Drawdown.md`): −$413 book, fully diagnosed.

## 4. Data
All under `KronosStrategies/strategies/` (run python from there: `../.venv/bin/python`).
- **S5** (QA'd, `lab/QA_S5_2026-09-18.md`): `backtest/results/bars_cache/s5/XAU_USD/<YYYY-MM>.parquet`
  — `time` (UTC), `o h l c` (mid), `bid_c ask_c`, `volume`; 8,682,738 bars 2024-08-15 → 2026-09-18.
  `from lab.tools.qa_s5_cache import load_s5` (556 MB; load once; vectorise; loop over events only).
  Untradeable window 2025-12-25 23:00–23:15 UTC.
- **M1/M5/M15/H1/H4/D** (QA'd): `backtest/results/bars_cache_2y/is_XAU_USD_<tf>.parquet`
  (`time, open, high, low, close, volume`), 2024-08-15 → 2026-09-17, UTC days. Select in the harness
  with `LAB_BARS_CACHE=backtest/results/bars_cache_2y`.
- **Deep history M1** 2010→2026-07 (16.5 yr, trust 2016+; calendar change Oct 2015): see
  `50 Research/XAUUSD Data Inventory.md` for the path under `ClaudeTradingRD/m3_scalper/`.
- **Live record (read-only)**: prod Postgres via `source ../../Kronos_Backend/scripts/prod-db-env.sh`
  (exports DB_HOST/PORT/NAME/USER/PASSWORD; psycopg2 in `../../Kronos_Backend/.venv`; use
  `PGSSLMODE=require`; open the connection **readonly**). Tables: `apis_position` (rows per trade;
  `realized_profit_loss` in price×lots, ×100 = USD; `symbol` XAU_USD = engine, XAUUSD = copy-trader),
  `apis_order` (ENTRY/TARGET/STOPLOSS rows, `broker_order_id`), `apis_strategysignal` (every generated
  signal with `status` PLACED/REJECTED and `rejection_reason` — the selection record), `broker_deals`
  (MetaAPI deal history, USD `profit`, `position_id`, `raw` json with `comment` tag), `apis_strategy`/
  `apis_userstrategy`/`apis_managedstrategy`. Units and timestamp conventions: `70 Code Map/Units and
  Conventions.md`. **Never write to this database.**

## 5. Tools (reuse; do not edit shared code — copy into your folder if you must change behaviour)
- `lab/harness.py`: `load_bars(tfs, cache)`, `replay(module_name, bars, start, end, cfg) -> rows`,
  `summarize(...)`. Drives the REAL `get_signal()` per M1 bar with the real gate rules
  (`shared/gate_rules.py`), the module's CONFIG session hours, SL-before-TP on M1, exits on mid.
  `Cfg` fields: `cost_pts, min_sl_dist_pts, news_blackout, max_concurrent, cooldown_s, block_hours,
  sides, regime ('above_sma20'|'below_sma20'), regime_sma, be_at_r, env (per-strategy env
  overrides), patch (module attrs, incl. dotted "pkg.module:ATTR" delegates)`.
- `lab/sweep.py`: `Arm(label, strategy, cfg, start, end, split)`, `run_campaign(name, arms, workers)`;
  CLI `python -m lab.sweep <campaign.py> --workers N` → `lab/results/<name>/<arm>.json + .trades.parquet`.
  Campaign files: see `lab/campaigns/*.py` for the pattern (module docstring = the pre-registration).
- `lab/tools/campaign_score.py`: bars 1–5 (n, PF both costs, TRAIN, monthly, regime) → CSV.
- `lab/s5exit.py`: `resolve(trade, s5, t5, mode, cost, max_hold_min, start_offset_s=60)`; CLI
  `python -m lab.s5exit --trades <parquet|csv> --cost c --max-hold m --split 2025-12-01 --out f`
  → mid_M1 / mid_S5 / quote_S5 per trade. Any tradeable claim is judged on **quote_S5**.
- `lab/tools/fetch_oanda_tail.py`, `fetch_oanda_s5.py`, `build_bars_cache_2y.py`: data builders (not needed).
- **Knowledge base**: `cd /Users/anil/Projects/Kronos/kb && .venv/bin/python ask.py "question"`
  (`-n 12` more hits, `-s vault|lab|research|ttrades-concept|research-meta|docs`, `--full`).
  13,183 chunks over the vault, all lab reports, the RD research and the TTrades ICT corpus.
  Use it before designing anything: "has X been tested", "what does the corpus say about Y".
- **Skills** (synced from the Windows profile 2026-09-18): `~/.claude/skills/<name>/SKILL.md` (+ reference
  files in each folder). Trading ones: `ict-smc-strategy-design` (OB retest, FVG fill, sweep reversal,
  breaker, OTE — the project's own quantification rules), `crt-strategy-design`, `quant-strategy-design`,
  `event-driven-strategy-design`, `macro-crossasset-strategy-design`, `news-sentiment-strategy-design`,
  `backtest-expert`, `xau-challenge-doctrine`, `trading-knowledge` (+ `trading-knowledge-map`), and
  `pillar-01..10` (structure, auction/volume profile, candlesticks, order flow/liquidity, macro, sentiment,
  psychology, risk/position sizing, statistical thinking, market history). Read the relevant ones before
  designing; they encode how this project quantifies each concept.
- Vault (Obsidian, plain markdown): `/Users/anil/Projects/KronosVault/` — `Home.md` is the map;
  sections 10 Architecture, 20 Strategies, 30 Operations, 40 Incidents, 50 Research,
  60 Concepts (ICT-SMC Glossary, Backtest Methodology Traps), 70 Code Map (32 notes written from
  source: Entry Gates Reference, Units and Conventions, Strategy Module Contract, ICT Engine,
  Shared TA Primitives, Regime Engine, Gating Policies, Environment Variables...).
- Prior S5 study code (reusable patterns): `lab/research_s5/{sweeps,fvg_displacement,killzones,crt,
  execution,po3_judas}/` — level builders, event finders, quote-fill resolvers, permutation nulls,
  matched random controls. Read the one closest to your topic before writing your own.

## 6. How evidence is judged here (non-negotiable)
1. **Pre-register**: `PROTOCOL.md` in your folder before the first number — the question, the exact
   rule, the grid, the split, the costs, the control, the bars, the number of comparisons. Append
   deviations with timestamps; never rewrite history. One protocol per hypothesis; you may run several
   hypotheses sequentially (protocol → run → report → next).
2. **TRAIN < 2025-12-01 ≤ TEST** (2024-09-01 → 2026-09-17). Choose on TRAIN; read TEST once.
3. **Costs**: mid-M1 harness 0.75 / 1.00; quote-S5 0.45 / 0.70 (declare which model you use where).
   Fills at the next S5 bar on the quote (long ask / short bid), exits on the quote, stop before target.
4. **Bars** (`lab/PROTOCOL_xau2y_2026-09-18.md`): TEST n ≥ 40; TEST PF > 1 at both costs; TRAIN PF > 0.9;
   ≥ 55 % TEST months positive and no month > 50 % of TEST net; regime independence. For a change to
   an existing strategy add Stage-2 bars 6 (plateau) and 7 (beat the incumbent at stress cost on TEST
   PF AND points) and TRAIN-justify the pick.
5. **Control**: the same rule with the concept removed, randomised in time, or geometry-matched random
   entries with the same count/hours. Credit the concept only with what the control cannot explain.
6. **Re-run, don't filter**: a change to a strategy is tested by re-running `get_signal` under the
   change (harness `env`/`patch`/gates), never by filtering its saved trade list (the S100 `_pending`
   lesson).
7. **Report** every number from script output; negative results in full; what it does and does not
   establish; concrete next hypotheses.

## 7. Scope and safety
- Work only inside your folder `lab/research_s5/r2_<topic>/` (create it). New strategy variants go
  there as modules (import the original and override, or copy the file); run them through the harness
  by adding your folder to `sys.path` or via `Cfg.patch`/`env`. Do not edit `lab/harness.py`,
  `lab/s5exit.py`, `shared/`, any live strategy module, `compose.yml`, or anything on the box.
- Prod DB read-only. No deploys. No secrets in files or output. ≤ 3 worker processes.
- Budget: iterate for as long as the questions deserve (2–3 hours of compute is fine); the coordinator
  integrates and commits. End with a summary: hypotheses tested (pre-registered / passed / failed),
  headline numbers, what you recommend, file paths.
