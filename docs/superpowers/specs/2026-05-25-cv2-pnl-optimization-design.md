# CV2 PnL Optimization — Prune & Amplify

**Date:** 2026-05-25
**Target:** `strategies/backtest_strategies/kronos_combined_v2.py` (Kronos Combined Suite v2, live).
**Goal:** Maximize total book PnL while holding drawdown ~ current, growth from edge not leverage.
**Approach:** A — Prune & Amplify (data-driven composition surgery + amplify proven winners).

---

## 1. Baseline (measured)

Live-shape parity replay (`backtest/backtest_combined_v2.py --days 540`), which returned
**126 trading days** (candle history starts ~2025-11-26). Per-leg attribution, $ @ 0.01 lot/leg:

**Whole book: 3,337 trades · 26.5/day · net +6,698 · PF 1.26 · maxDD −1,621 · 68% win-days**

| Leg | Side | Trades | Net | PF | Win% | Read |
|---|---|---:|---:|---:|---:|---|
| SE1 failed-break-up fade | SELL | 365 | +1,309 | 1.53 | 53 | workhorse |
| E2 Bollinger-lower fade | BUY | 592 | +1,219 | 1.26 | 52 | volume engine |
| S02 stoch revert | BUY | 81 | +909 | 2.34 | 68 | best quality, rare |
| S07 CRT fade | SELL | 467 | +874 | 1.26 | 56 | solid |
| E1 failed-break fade | BUY | 529 | +812 | 1.18 | 50 | thin PF |
| SE2 Bollinger-upper fade | SELL | 268 | +655 | 1.34 | 51 | solid |
| S05 threebar pull | BUY | 68 | +651 | 2.43 | 71 | best quality, rare |
| S06 session sweep | SELL | 211 | +244 | 1.17 | 45 | weak |
| S14 M5 EMA stretch | BUY | 412 | +63 | 1.02 | 55 | churn / ~breakeven |
| E3 stoch-extreme fade | BUY | 344 | −36 | 0.99 | 45 | net loser |

**Where PnL lives:** short fades (SE1+SE2 = +1,964), the BB-lower long fade (E2 +1,219),
and the two rare high-PF MR legs (S02/S05, PF 2.3–2.4 but only 81/68 trades).
**Drags:** E3 (loser) + S14 (breakeven churn) = 756 trades for +27 net.

### Two fidelity caveats found
1. **TNX gate failed open the entire backtest** — `shared.macro_gate` parquet cache has
   17 days of TNX history (needs 21; yfinance not installed to refresh). So the MR legs
   (S02/S05/S06/S07/S14) are effectively **un-TNX-gated** in this baseline.
2. **6-month window.** Short sample; full-redesign overfitting risk is real. Per operator
   decision, we optimize on the 6mo directly (no formal IS/OOS), with one cheap last-40-day
   sanity glance on the final config.

---

## 2. Acceptance criteria

A change is **kept** only if, re-measured on the full 126-day replay:
- **Primary:** book net PnL strictly increases vs the running best.
- **DD constraint:** book maxDD no worse than **−1,780** (≈ baseline −1,621 + 10%).
- **PF constraint:** book PF ≥ **1.26** (no regression vs baseline; goal is to trend toward 1.5).
- **Per-leg floor:** any leg whose gate we relax / target we widen must keep **per-leg PF ≥ 1.3**.
  Never add negative-edge volume.

> The "PF ≥ 1.5" mentioned during scoping is a *goal*, not a current floor — the live book
> is PF 1.26 today. "Keep DD ~current" is the binding constraint.

---

## 3. Parity decisions (so live behaves like the optimized backtest)

- **TNX gate → bypass for CV2's MR legs.** Add a module-level toggle each MR leg reads
  (default preserves current behavior; CV2 sets it OFF). Rationale: it is unmeasurable
  without TNX data, it is already off in the backtest, and it throttles the best legs.
  The standalone Kronos S02/05/06/07/14 Strategy rows were deleted at the CV2 cutover, so
  **CV2 is the only runtime consumer of these leg modules** — changing their gating does
  not affect any other live strategy.
- **Event-window (±2h), liquidity-zone, D1/H4 bias gates → kept.** These use candle/event
  data and are active+measurable in the backtest, so they are tuned empirically (not bypassed).

---

## 4. Code architecture (minimal, reversible)

1. **Per-leg enable in CV2** — replace the all-or-nothing `ENABLE_FADES` flag with a
   `DISABLED_LEGS: set[str]` checked in `get_signal`, so E3 (or S14) can be dropped
   individually without disabling its siblings.
2. **Overridable knobs** — tuning params are already module-level constants
   (`_STOCH_THR`, `_ZONE_ATR` in S02; liquidity gate in S05; `_FADE_*_ATR`, `_SE_*_ATR`,
   `_BB_K` in CV2; `LONG_GATE_N` / `SHORT_GATE_N`). No structural refactor; the sweep
   driver assigns them between runs.
3. **TNX bypass toggle** — `_USE_TNX_GATE` (default `True`) added to each MR leg module;
   each leg's `tnx_gate_open` call is guarded by it. CV2 sets all to `False` at import.
4. **New sweep driver** `backtest/optimize_combined_v2.py`:
   - Fetches 1m/5m/15m/1d candles **once** and builds the as-of bias map once.
   - Defines a `replay(config) -> metrics` that sets module knobs, clears
     `cv2._last_fire_bucket`, runs the existing bar loop, returns
     `{net, pf, maxdd, tpd, trades, per_leg}`.
   - Runs a ranked list of candidate configs and prints a comparison table; writes a
     results CSV/JSON to `backtest/results/`.
   - Reuses `backtest_combined_v2.simulate_exit` / `build_asof_bias` (extract/import,
     don't duplicate).

---

## 5. Tuning sequence (each step measured against §2; keep best-so-far)

1. **Prune.** Drop E3. Test dropping S14. Expected: DD↓, PF↑, net ≈ flat or up.
2. **Amplify S02 / S05** (biggest lever). Sweep:
   - S02: stoch `_STOCH_THR` 15 → {20, 25}; zone `_ZONE_ATR` 2.0 → {3.0, off}; D1-bias gate on/off.
   - S05: liquidity gate on/off (its rarity is mostly the intrinsic 3-bar pattern — limited headroom; measure).
   - Keep relaxations that raise net with leg PF ≥ 1.3.
3. **Widen workhorse targets.** Sweep SE1/SE2 target `_SE_TGT_ATR` 2.5 → {3.0, 3.5};
   E2 fade target `_FADE_TGT_ATR` 2.5 → 3.0. Bank what lifts net without breaking the DD bound.
4. **(Optional) bias-gate.** Test relaxing `SHORT_GATE_N` / the `bias30 == -1` short condition.
   Regime-sensitive (the 6mo sample was a gold sell-off) — accept only if DD holds.

After each phase, lock in the winning subset before starting the next (greedy, attribution-led).

---

## 6. Deliverable

- Winning config baked into `kronos_combined_v2.py` **module defaults** (and the per-leg
  TNX bypass + `DISABLED_LEGS`).
- Final parity re-run (`backtest_combined_v2.py`) confirming new net / PF / maxDD vs baseline.
- One cheap **last-40-day** sanity glance on the final config; flag (do not silently ship)
  if it fails to beat baseline there.
- A short before/after report appended to this doc.
- **DB deploy stays the operator's manual step** (`python -m db.deploy_combined_v2 --commit`)
  — auto-mode blocks the agent from running deploy scripts. Compose redeploy of
  `kronos_combined_v2` + `position_manager` is also operator-run.

---

## 7. Out of scope

- archD / SH legs stay OFF (abandoned/dropped for cause) unless a phase stalls.
- No new instruments (XAG_USD multi-instrument is a separate effort).
- No live deploy / DB writes / git push by the agent.
- No formal IS/OOS split (operator decision); only the single last-40-day glance.

---

## 8. Before / after report

_(filled in at the end of implementation)_
